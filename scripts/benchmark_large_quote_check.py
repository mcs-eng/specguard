"""Generate scaled synthetic PDFs and benchmark the complete quote-check CLI path.

Generated PDFs and JSONL inputs live in a temporary directory and are deleted
after the run. Generation is outside every timed interval.
"""

from __future__ import annotations

import argparse
import cProfile
import gc
import hashlib
import io
import json
import os
import pstats
import secrets
import shutil
import statistics
import tempfile
import time
import tracemalloc
from collections.abc import Iterator
from contextlib import contextmanager, redirect_stdout
from pathlib import Path
from typing import Any

import pymupdf

from specguard.cli import main
from specguard.gate import contains_on_boundaries, normalize

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
FIXTURES = REPOSITORY_ROOT / "fixtures"
DEFAULT_SCALES = (10, 100)


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return parsed


@contextmanager
def _temporary_directory() -> Iterator[Path]:
    """Create inherited-permission scratch space, including on recent Windows Python."""
    root = Path(tempfile.gettempdir())
    mode = 0o777 if os.name == "nt" else 0o700
    for _ in range(100):
        path = root / f"specguard-large-input-{secrets.token_hex(8)}"
        try:
            path.mkdir(mode=mode)
        except FileExistsError:
            continue
        break
    else:
        raise RuntimeError("could not allocate benchmark scratch directory")
    try:
        yield path
    finally:
        shutil.rmtree(path)


def _largest_fixture() -> Path:
    fixtures = list(FIXTURES.glob("*.pdf"))
    if not fixtures:
        raise RuntimeError("fixtures holds no PDFs")
    return max(fixtures, key=lambda path: path.stat().st_size)


def _page_quotes(source: Path) -> list[str]:
    quotes: list[str] = []
    with pymupdf.open(str(source)) as document:
        for page_number, page in enumerate(document, start=1):
            normalized_page = normalize(page.get_text())
            words = normalized_page.split()
            if not words:
                raise RuntimeError(f"fixture page {page_number} has no extractable text")
            quote = " ".join(words[: min(12, len(words))])
            if not contains_on_boundaries(normalized_page, quote):
                raise RuntimeError(
                    f"fixture page {page_number} did not yield a boundary-safe quote"
                )
            quotes.append(quote)
    return quotes


def _write_scaled_pdf(source: Path, destination: Path, scale: int) -> int:
    with pymupdf.open(str(source)) as source_document, pymupdf.open() as output:
        for _ in range(scale):
            output.insert_pdf(source_document)
        page_count = output.page_count
        output.save(str(destination), garbage=0, deflate=False, no_new_id=True)
    return page_count


def _write_batch(
    path: Path,
    pdf_name: str,
    quotes: list[str],
    scale: int,
    record_limit: int | None,
) -> int:
    records = []
    for copy_number in range(scale):
        for page_offset, quote in enumerate(quotes):
            page_number = copy_number * len(quotes) + page_offset + 1
            records.append(
                json.dumps(
                    {
                        "id": f"page-{page_number:05d}",
                        "pdf": pdf_name,
                        "page": page_number,
                        "quote": quote,
                    },
                    separators=(",", ":"),
                )
            )
            if record_limit is not None and len(records) == record_limit:
                path.write_text("\n".join(records) + "\n", encoding="utf-8")
                return len(records)
    path.write_text("\n".join(records) + "\n", encoding="utf-8")
    return len(records)


def _invoke(batch: Path, pdf_root: Path, *, collect: bool = True) -> tuple[float, str]:
    output = io.StringIO()
    if collect:
        gc.collect()
    started = time.perf_counter_ns()
    with redirect_stdout(output):
        exit_code = main(
            [
                "quote-check",
                "--batch",
                str(batch),
                "--pdf-root",
                str(pdf_root),
            ]
        )
    elapsed_seconds = (time.perf_counter_ns() - started) / 1_000_000_000
    if exit_code != 0:
        raise RuntimeError(f"quote-check returned {exit_code}")
    return elapsed_seconds, output.getvalue()


def _output_digest(raw_output: str) -> str:
    document = json.loads(raw_output)
    for result in document["results"]:
        result["pdf"] = "<generated-pdf>"
    canonical = json.dumps(document, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _measure_case(
    *,
    batch: Path,
    pdf: Path,
    pdf_root: Path,
    page_count: int,
    records: int,
    warmup: int,
    repeat: int,
) -> dict[str, Any]:
    for _ in range(warmup):
        _invoke(batch, pdf_root)

    timings = []
    output_digests = set()
    for _ in range(repeat):
        elapsed, output = _invoke(batch, pdf_root)
        timings.append(elapsed)
        output_digests.add(_output_digest(output))
    if len(output_digests) != 1:
        raise RuntimeError("quote-check output changed between identical timed runs")

    gc.collect()
    tracemalloc.start()
    _, memory_output = _invoke(batch, pdf_root, collect=False)
    _, peak_bytes = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    if _output_digest(memory_output) not in output_digests:
        raise RuntimeError("memory measurement changed quote-check output")

    return {
        "pdf": pdf.name,
        "pdf_bytes": pdf.stat().st_size,
        "pdf_pages": page_count,
        "input": batch.name,
        "input_bytes": batch.stat().st_size,
        "records": records,
        "warmup_runs": warmup,
        "timed_runs": repeat,
        "median_seconds": statistics.median(timings),
        "minimum_seconds": min(timings),
        "maximum_seconds": max(timings),
        "peak_traced_python_bytes": peak_bytes,
        "output_sha256": output_digests.pop(),
    }


def _profile(batch: Path, pdf_root: Path) -> str:
    gc.collect()
    profiler = cProfile.Profile()
    profiler.enable()
    _invoke(batch, pdf_root, collect=False)
    profiler.disable()
    output = io.StringIO()
    pstats.Stats(profiler, stream=output).strip_dirs().sort_stats("cumtime").print_stats(20)
    return output.getvalue()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--scales",
        nargs="+",
        type=_positive_int,
        default=list(DEFAULT_SCALES),
        help="fixture copy counts to generate (default: 10 100)",
    )
    parser.add_argument("--warmup", type=_positive_int, default=1)
    parser.add_argument("--repeat", type=_positive_int, default=3)
    parser.add_argument(
        "--record-limit",
        type=_positive_int,
        help="check only the first N generated records (default: one per generated page)",
    )
    parser.add_argument(
        "--profile",
        action="store_true",
        help="include cProfile's top 20 cumulative entries for the largest scale",
    )
    return parser


def main_benchmark(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    fixture = _largest_fixture()
    quotes = _page_quotes(fixture)
    result: dict[str, Any] = {
        "fixture": fixture.name,
        "fixture_bytes": fixture.stat().st_size,
        "fixture_pages": len(quotes),
        "cases": [],
    }

    with _temporary_directory() as work:
        generated: dict[int, tuple[Path, Path]] = {}
        for scale in args.scales:
            pdf = work / f"synthetic-{scale}x.pdf"
            batch = work / f"synthetic-{scale}x.jsonl"
            page_count = _write_scaled_pdf(fixture, pdf, scale)
            records = _write_batch(batch, pdf.name, quotes, scale, args.record_limit)
            result["cases"].append(
                {
                    "scale": scale,
                    **_measure_case(
                        batch=batch,
                        pdf=pdf,
                        pdf_root=work,
                        page_count=page_count,
                        records=records,
                        warmup=args.warmup,
                        repeat=args.repeat,
                    ),
                }
            )
            generated[scale] = (batch, pdf)

        if args.profile:
            largest_scale = max(args.scales)
            batch, _ = generated[largest_scale]
            result["profile_scale"] = largest_scale
            result["profile_top_20_cumulative"] = _profile(batch, work)

    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main_benchmark())
