"""Known-good and known-bad cases for ``specguard quote-check``.

The CLI must admit exactly what the gate admits. Every verdict case below is
checked against :func:`specguard.gate.verify_quote` directly, so a CLI that
drifted from the gate fails here. All PDFs are the fictional fixtures from
:mod:`tests.fixtures_pdf`.
"""

from __future__ import annotations

import io
import json
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Any

import pytest

from specguard.cli import main
from specguard.gate import verify_quote
from tests.fixtures_pdf import SOFT_HYPHEN, write_image_only_pdf, write_pdf

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]

EXACT_QUOTE = "Receptacles shall be specification grade, rated 20 amperes at 125 volts."


def run(capsys: pytest.CaptureFixture[str], *argv: str) -> tuple[int, dict[str, Any]]:
    """Run the CLI in JSON mode and return its exit code and parsed output."""
    code = main(["quote-check", *argv])
    captured = capsys.readouterr()
    return code, json.loads(captured.out)


def write_batch(path: Path, records: list[object], encoding: str = "utf-8") -> Path:
    """Write one JSON document per line; strings are written verbatim."""
    lines = [record if isinstance(record, str) else json.dumps(record) for record in records]
    path.write_text("\n".join(lines) + "\n", encoding=encoding)
    return path


# --- single mode: verdicts --------------------------------------------------


def test_a_quote_on_its_cited_page_passes(capsys, spec_pdf: Path) -> None:
    code, output = run(capsys, "--pdf", str(spec_pdf), "--page", "1", "--quote", EXACT_QUOTE)

    assert code == 0
    assert output["schema"] == "specguard.quote-check/v1"
    assert output["ok"] is True
    assert output["summary"] == {"total": 1, "passed": 1, "failed": 0, "errors": 0}
    result = output["results"][0]
    assert result["status"] == "pass"
    assert result["verified"] is True
    assert result["reason"] is None
    assert result["page_count"] == 4
    assert result["normalized_quote"] == EXACT_QUOTE.casefold()
    assert result["match"]["found"] is True
    assert result["match"]["offset"] == len("section 26 27 26 - wiring devices ")
    assert result["normalized_quote"] in result["match"]["context"]


def test_one_changed_digit_fails_with_the_gate_reason(capsys, spec_pdf: Path) -> None:
    quote = EXACT_QUOTE.replace("20 amperes", "30 amperes")
    code, output = run(capsys, "--pdf", str(spec_pdf), "--page", "1", "--quote", quote)

    assert code == 1
    assert output["ok"] is False
    result = output["results"][0]
    assert result["status"] == "fail"
    assert result["verified"] is False
    assert result["reason"] == "quote_not_found_on_cited_page"
    assert result["match"]["found"] is False
    assert result["match"]["offset"] is None
    # The retry hint points at where the quote diverged from the page.
    prefix = "receptacles shall be specification grade, rated "
    assert result["match"]["longest_prefix_found"] == prefix


def test_a_real_quote_cited_to_the_wrong_page_fails(capsys, spec_pdf: Path) -> None:
    code, output = run(capsys, "--pdf", str(spec_pdf), "--page", "2", "--quote", EXACT_QUOTE)

    assert code == 1
    assert output["results"][0]["reason"] == "quote_not_found_on_cited_page"


def test_a_page_outside_the_document_fails(capsys, spec_pdf: Path) -> None:
    code, output = run(capsys, "--pdf", str(spec_pdf), "--page", "5", "--quote", EXACT_QUOTE)

    assert code == 1
    result = output["results"][0]
    assert result["status"] == "fail"
    assert result["reason"] == "page_out_of_range"
    assert result["page_count"] == 4
    assert result["match"] is None
    assert "1 to 4" in result["detail"]


def test_page_zero_is_out_of_range_not_a_crash(capsys, spec_pdf: Path) -> None:
    code, output = run(capsys, "--pdf", str(spec_pdf), "--page", "0", "--quote", EXACT_QUOTE)

    assert code == 1
    assert output["results"][0]["reason"] == "page_out_of_range"


def test_a_quote_that_cuts_a_number_fails_and_says_why(capsys, spec_pdf: Path) -> None:
    """``0 amperes`` sits inside ``20 amperes``; the token-boundary rule rejects it."""
    code, output = run(capsys, "--pdf", str(spec_pdf), "--page", "1", "--quote", "0 amperes")

    assert code == 1
    result = output["results"][0]
    assert result["reason"] == "quote_not_found_on_cited_page"
    assert result["match"]["substring_occurrences"] == 1
    assert "inside a word or number" in result["detail"]


def test_an_image_only_page_fails_and_says_it_has_no_text(capsys, tmp_path: Path) -> None:
    pdf = write_image_only_pdf(tmp_path / "scan.pdf")
    code, output = run(capsys, "--pdf", str(pdf), "--page", "1", "--quote", "anything")

    assert code == 1
    result = output["results"][0]
    assert result["reason"] == "quote_not_found_on_cited_page"
    assert result["match"]["page_text_chars"] == 0
    assert "no extractable text" in result["detail"]


def test_a_miss_on_a_sparse_text_layer_says_to_check_visually(capsys, tmp_path: Path) -> None:
    """A title-block-only page earns a hint; a page with real text does not."""
    sparse = write_pdf(tmp_path / "sparse.pdf", [["Sheet S-3. Seal block only."]])
    dense_lines = [f"General note {n}: framing members shall be as scheduled." for n in range(12)]
    dense = write_pdf(tmp_path / "dense.pdf", [dense_lines])

    _, sparse_output = run(capsys, "--pdf", str(sparse), "--page", "1", "--quote", "W8x31 beam")
    _, dense_output = run(capsys, "--pdf", str(dense), "--page", "1", "--quote", "W8x31 beam")

    assert "check the page visually" in sparse_output["results"][0]["detail"]
    assert dense_output["results"][0]["match"]["page_text_chars"] >= 500
    assert "check the page visually" not in dense_output["results"][0]["detail"]


@pytest.mark.parametrize(
    ("quote", "page"),
    [
        (EXACT_QUOTE, 1),
        (EXACT_QUOTE.upper(), 1),
        ("smooth thermoplastic in a color selected by the Architect.", 1),
        ("Northgate Civic Annex transformer room ambient of 40 degrees Celsius.", 2),
        ("The ﬁxture schedule lists a 30 ampere branch", 2),
        ("sized in accordance with Table 4 of this Section", 3),
        ("Panelboard MDP-2 shall be rated 208 volts", 1),
        ("0 amperes", 1),
        ("trans" + SOFT_HYPHEN + "former", 2),
        ("", 1),
    ],
)
def test_the_cli_verdict_is_the_gate_verdict(capsys, spec_pdf: Path, quote: str, page: int) -> None:
    """Known-good and known-bad gate cases give the same verdict through the CLI."""
    main(["quote-check", "--pdf", str(spec_pdf), "--page", str(page), "--quote", quote])
    result = json.loads(capsys.readouterr().out)["results"][0]

    assert result["verified"] is verify_quote(quote, page, spec_pdf).verified


def test_non_ascii_quotes_round_trip_as_ascii_json(capsys, spec_pdf: Path) -> None:
    quote = "The ﬁxture schedule lists a 30 ampere branch"
    main(["quote-check", "--pdf", str(spec_pdf), "--page", "2", "--quote", quote])
    raw = capsys.readouterr().out

    assert raw.isascii()
    result = json.loads(raw)["results"][0]
    assert result["quote"] == quote
    assert result["status"] == "pass"


# --- single mode: errors and usage ------------------------------------------


def test_a_missing_pdf_is_an_error_record(capsys, tmp_path: Path) -> None:
    missing = tmp_path / "absent.pdf"
    code, output = run(capsys, "--pdf", str(missing), "--page", "1", "--quote", "x")

    assert code == 1
    result = output["results"][0]
    assert result["status"] == "error"
    assert result["reason"] == "pdf_not_found"
    assert str(missing) in result["detail"]
    assert output["summary"]["errors"] == 1


def test_a_file_that_is_not_a_pdf_is_an_error_record(capsys, tmp_path: Path) -> None:
    fake = tmp_path / "fake.pdf"
    fake.write_bytes(b"this is not a PDF")
    code, output = run(capsys, "--pdf", str(fake), "--page", "1", "--quote", "x")

    assert code == 1
    assert output["results"][0]["reason"] == "pdf_unreadable"


def test_a_whitespace_only_quote_is_an_error_record(capsys, spec_pdf: Path) -> None:
    code, output = run(capsys, "--pdf", str(spec_pdf), "--page", "1", "--quote", " \t ")

    assert code == 1
    assert output["results"][0]["reason"] == "empty_quote"


def test_the_id_is_echoed(capsys, spec_pdf: Path) -> None:
    _, output = run(
        capsys, "--pdf", str(spec_pdf), "--page", "1", "--quote", EXACT_QUOTE, "--id", "Q-7"
    )

    assert output["results"][0]["id"] == "Q-7"


def test_single_mode_names_its_missing_arguments(capsys, spec_pdf: Path) -> None:
    code = main(["quote-check", "--pdf", str(spec_pdf), "--page", "1"])
    captured = capsys.readouterr()

    assert code == 2
    assert captured.out == ""
    assert "missing --quote" in captured.err


def test_batch_and_single_mode_arguments_do_not_mix(capsys, spec_pdf: Path, tmp_path) -> None:
    batch = write_batch(tmp_path / "q.jsonl", [{"pdf": str(spec_pdf), "page": 1, "quote": "x"}])
    code = main(["quote-check", "--batch", str(batch), "--pdf", str(spec_pdf)])

    assert code == 2
    assert "cannot be combined with --pdf" in capsys.readouterr().err


def test_a_non_integer_page_is_a_usage_error(capsys, spec_pdf: Path) -> None:
    with pytest.raises(SystemExit) as raised:
        main(["quote-check", "--pdf", str(spec_pdf), "--page", "one", "--quote", "x"])

    assert raised.value.code == 2
    assert "invalid int value" in capsys.readouterr().err


def test_help_documents_the_formats_and_exit_codes(capsys) -> None:
    with pytest.raises(SystemExit) as raised:
        main(["quote-check", "--help"])
    text = capsys.readouterr().out

    assert raised.value.code == 0
    for needle in ("--batch", "--pdf-root", "exit codes", "page_out_of_range", "pdf_not_found"):
        assert needle in text


def test_text_format_prints_one_line_per_result_and_a_summary(capsys, spec_pdf: Path) -> None:
    code = main(
        ["quote-check", "--pdf", str(spec_pdf), "--page", "1", "--quote", "0 amperes"]
        + ["--format", "text"]
    )
    lines = capsys.readouterr().out.splitlines()

    assert code == 1
    assert lines[0].startswith("FAIL  page 1  quote_not_found_on_cited_page:")
    assert lines[-1] == "0 passed, 1 failed, 0 errors (1 total)"


# --- batch mode --------------------------------------------------------------


def test_a_mixed_batch_reports_every_line_in_order(capsys, spec_pdf: Path, tmp_path) -> None:
    batch = write_batch(
        tmp_path / "quotes.jsonl",
        [
            {"id": "good", "pdf": str(spec_pdf), "page": 1, "quote": EXACT_QUOTE},
            "",
            {"id": "wrong-page", "pdf": str(spec_pdf), "page": 3, "quote": EXACT_QUOTE},
            "{not json",
            {"id": "no-quote", "pdf": str(spec_pdf), "page": 1},
            {"id": "string-page", "pdf": str(spec_pdf), "page": "1", "quote": "x"},
            {"id": "bool-page", "pdf": str(spec_pdf), "page": True, "quote": "x"},
            ["pdf", 1, "quote"],
            {"id": 42, "pdf": str(spec_pdf), "page": 99, "quote": "x"},
        ],
    )
    code, output = run(capsys, "--batch", str(batch))

    assert code == 1
    results = output["results"]
    assert [result["line"] for result in results] == [1, 3, 4, 5, 6, 7, 8, 9]
    assert [(result["status"], result["reason"]) for result in results] == [
        ("pass", None),
        ("fail", "quote_not_found_on_cited_page"),
        ("error", "invalid_json"),
        ("error", "invalid_record"),
        ("error", "invalid_record"),
        ("error", "invalid_record"),
        ("error", "invalid_record"),
        ("fail", "page_out_of_range"),
    ]
    assert results[0]["id"] == "good"
    assert results[-1]["id"] == 42
    assert "missing required field(s): quote" in results[3]["detail"]
    assert "page must be a JSON integer" in results[4]["detail"]
    assert "got a JSON boolean" in results[5]["detail"]
    assert "got a JSON array" in results[6]["detail"]
    assert output["summary"] == {"total": 8, "passed": 1, "failed": 2, "errors": 5}


def test_an_all_passing_batch_exits_zero(capsys, spec_pdf: Path, tmp_path) -> None:
    batch = write_batch(
        tmp_path / "quotes.jsonl",
        [
            {"id": "a", "pdf": str(spec_pdf), "page": 1, "quote": EXACT_QUOTE},
            {"id": "b", "pdf": str(spec_pdf), "page": 4, "quote": "copper throughout"},
        ],
    )
    code, output = run(capsys, "--batch", str(batch))

    assert code == 0
    assert output["ok"] is True
    assert output["summary"]["passed"] == 2


def test_relative_pdf_paths_resolve_against_pdf_root(capsys, spec_pdf: Path, tmp_path) -> None:
    batch = write_batch(
        tmp_path / "quotes.jsonl", [{"pdf": spec_pdf.name, "page": 1, "quote": EXACT_QUOTE}]
    )
    code, output = run(capsys, "--batch", str(batch), "--pdf-root", str(spec_pdf.parent))

    assert code == 0
    assert output["results"][0]["pdf"] == str(spec_pdf)


@pytest.mark.parametrize("encoding", ["utf-8-sig", "utf-16"])
def test_bom_marked_batch_files_are_read(capsys, spec_pdf: Path, tmp_path, encoding) -> None:
    """Windows PowerShell writes a BOM; the batch reader accepts it."""
    quote = "The ﬁxture schedule lists a 30 ampere branch"
    batch = write_batch(
        tmp_path / "quotes.jsonl",
        [{"pdf": str(spec_pdf), "page": 2, "quote": quote}],
        encoding=encoding,
    )
    code, output = run(capsys, "--batch", str(batch))

    assert code == 0
    assert output["results"][0]["quote"] == quote


def test_batch_reads_standard_input(capsys, monkeypatch, spec_pdf: Path) -> None:
    line = json.dumps({"id": "stdin", "pdf": str(spec_pdf), "page": 1, "quote": EXACT_QUOTE})
    stdin = io.TextIOWrapper(io.BytesIO((line + "\n").encode("utf-8")))
    monkeypatch.setattr(sys, "stdin", stdin)
    code, output = run(capsys, "--batch", "-")

    assert code == 0
    assert output["results"][0]["id"] == "stdin"


def test_an_empty_batch_is_a_usage_error_not_a_pass(capsys, tmp_path: Path) -> None:
    batch = tmp_path / "empty.jsonl"
    batch.write_text("\n  \n", encoding="utf-8")
    code = main(["quote-check", "--batch", str(batch)])
    captured = capsys.readouterr()

    assert code == 2
    assert captured.out == ""
    assert "no records" in captured.err


def test_a_missing_batch_file_is_a_usage_error(capsys, tmp_path: Path) -> None:
    code = main(["quote-check", "--batch", str(tmp_path / "absent.jsonl")])

    assert code == 2
    assert "cannot read batch file" in capsys.readouterr().err


def test_a_batch_that_is_not_utf8_is_a_usage_error(capsys, tmp_path: Path) -> None:
    batch = tmp_path / "latin1.jsonl"
    batch.write_bytes('{"pdf": "a.pdf", "page": 1, "quote": "café"}\n'.encode("latin-1"))
    code = main(["quote-check", "--batch", str(batch)])

    assert code == 2
    assert "not UTF-8" in capsys.readouterr().err


def test_a_batch_spanning_pages_and_files(capsys, spec_pdf: Path, tmp_path: Path) -> None:
    other = write_pdf(tmp_path / "other.pdf", [["Sheet A-101 First Floor Plan."]])
    batch = write_batch(
        tmp_path / "quotes.jsonl",
        [
            {"id": 1, "pdf": str(spec_pdf), "page": 4, "quote": "certified test reports"},
            {"id": 2, "pdf": str(other), "page": 1, "quote": "A-101 first floor plan"},
            {"id": 3, "pdf": str(other), "page": 1, "quote": "A-10"},
        ],
    )
    code, output = run(capsys, "--batch", str(batch))

    assert code == 1
    assert [result["status"] for result in output["results"]] == ["pass", "pass", "fail"]


# --- the split from the hosted demo -----------------------------------------


def test_core_dependencies_exclude_the_hosted_demo() -> None:
    """Installing the gate and CLI pulls PyMuPDF and Pydantic, nothing cloud-side."""
    project = tomllib.loads((REPOSITORY_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    core = {
        requirement.split("=")[0].split(">")[0].split("<")[0].strip().lower()
        for requirement in project["project"]["dependencies"]
    }
    demo = " ".join(project["project"]["optional-dependencies"]["demo"])

    assert core == {"pymupdf", "pydantic"}
    for name in ("google-adk", "google-cloud-firestore", "fastapi", "uvicorn"):
        assert name in demo
    assert project["project"]["scripts"]["specguard"] == "specguard.cli:main"


def test_the_cli_runs_with_every_demo_dependency_blocked(spec_pdf: Path) -> None:
    """Import and run the CLI in a fresh interpreter that cannot import the demo stack."""
    script = f"""
import importlib.abc, sys

BLOCKED = ("google", "fastapi", "starlette", "uvicorn", "jinja2", "multipart")

class Block(importlib.abc.MetaPathFinder):
    def find_spec(self, name, path=None, target=None):
        if name.split(".")[0] in BLOCKED:
            raise ImportError(f"blocked demo dependency: {{name}}")
        return None

sys.meta_path.insert(0, Block())
from specguard.cli import main
code = main(["quote-check", "--pdf", {str(spec_pdf)!r}, "--page", "1",
             "--quote", {EXACT_QUOTE!r}, "--format", "text"])
leaked = sorted(m for m in sys.modules if m.split(".")[0] in BLOCKED)
print("LEAKED", leaked)
sys.exit(code)
"""
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert "PASS" in completed.stdout
    assert "LEAKED []" in completed.stdout
