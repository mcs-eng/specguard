"""Process-level regressions for unusual inputs at the CLI's PDF boundary."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pymupdf
import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
MARKER_QUOTE = "Synthetic marker quote."


def _write_unusual_pdfs(directory: Path) -> list[tuple[str, Path, str]]:
    """Build four synthetic inputs and the diagnostic expected for each one."""
    valid = directory / "source.pdf"
    with pymupdf.open() as document:
        page = document.new_page()
        page.insert_text((72, 72), MARKER_QUOTE)
        document.save(valid)

    encrypted = directory / "encrypted.pdf"
    with pymupdf.open(valid) as document:
        document.save(
            encrypted,
            encryption=pymupdf.PDF_ENCRYPT_AES_256,
            owner_pw="synthetic-owner-password",
            user_pw="synthetic-user-password",
        )

    empty = directory / "empty.pdf"
    empty.write_bytes(b"")

    truncated = directory / "truncated.pdf"
    valid_bytes = valid.read_bytes()
    eof_marker = valid_bytes.rfind(b"%%EOF")
    assert eof_marker != -1
    truncated.write_bytes(valid_bytes[:eof_marker])
    with pymupdf.open(truncated) as recovered:
        assert recovered.is_repaired is False
        assert MARKER_QUOTE in recovered[0].get_text()

    renamed = directory / "renamed.pdf"
    renamed.write_text(MARKER_QUOTE, encoding="utf-8")

    return [
        ("encrypted", encrypted, "is encrypted or password-protected"),
        ("empty", empty, "is empty; expected a PDF"),
        ("truncated", truncated, "is damaged or incomplete; PDF repair would be required"),
        ("renamed", renamed, "is not a PDF"),
    ]


def _run_cli(*arguments: str) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "specguard.cli",
            "quote-check",
            *arguments,
        ],
        cwd=REPOSITORY_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )


@pytest.fixture
def unusual_pdf_batch(tmp_path: Path) -> tuple[Path, list[tuple[str, Path, str]]]:
    cases = _write_unusual_pdfs(tmp_path)
    batch = tmp_path / "unusual-pdfs.jsonl"
    batch.write_text(
        "".join(
            json.dumps({"id": name, "pdf": str(path), "page": 1, "quote": MARKER_QUOTE})
            + "\n"
            for name, path, _ in cases
        ),
        encoding="utf-8",
    )
    return batch, cases


def test_unusual_pdfs_are_structured_errors_without_a_traceback(
    unusual_pdf_batch: tuple[Path, list[tuple[str, Path, str]]],
) -> None:
    batch, cases = unusual_pdf_batch

    process = _run_cli("--batch", str(batch), "--format", "json")

    assert process.returncode == 1
    assert process.stderr == ""
    assert "Traceback" not in process.stdout
    output = json.loads(process.stdout)
    assert output["summary"] == {"total": 4, "passed": 0, "failed": 0, "errors": 4}
    assert [result["id"] for result in output["results"]] == [name for name, _, _ in cases]
    for result, (_, path, expected_detail) in zip(output["results"], cases, strict=True):
        assert result["status"] == "error"
        assert result["verified"] is False
        assert result["reason"] == "pdf_unreadable"
        assert result["pdf"] == str(path)
        assert result["detail"] == f"{path} {expected_detail}"
        assert "\n" not in result["detail"]


@pytest.mark.parametrize("case_index", range(4), ids=("encrypted", "empty", "truncated", "renamed"))
def test_each_unusual_pdf_is_a_one_line_text_error(
    unusual_pdf_batch: tuple[Path, list[tuple[str, Path, str]]],
    case_index: int,
) -> None:
    _, cases = unusual_pdf_batch
    name, path, expected_detail = cases[case_index]

    process = _run_cli(
        "--pdf",
        str(path),
        "--page",
        "1",
        "--quote",
        MARKER_QUOTE,
        "--id",
        name,
        "--format",
        "text",
    )

    assert process.returncode == 1
    assert process.stderr == ""
    assert "Traceback" not in process.stdout
    lines = process.stdout.splitlines()
    assert lines == [
        f"ERROR [{name}]  page 1  pdf_unreadable: {path} {expected_detail}",
        "0 passed, 0 failed, 1 errors (1 total)",
    ]
