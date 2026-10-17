"""Process-level regressions for unusual inputs at the CLI's PDF boundary."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pymupdf
import pytest

from specguard.cli import check_quote
from specguard.gate import extract_page_text, verify_quote
from specguard.pdf import PdfLoadError

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
MARKER_QUOTE = "Synthetic marker quote."


def _write_unusual_pdfs(directory: Path) -> list[tuple[str, Path, str]]:
    """Build five synthetic inputs and the diagnostic expected for each one."""
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

    owner_only_encrypted = directory / "owner-only-encrypted.pdf"
    with pymupdf.open(valid) as document:
        document.save(
            owner_only_encrypted,
            encryption=pymupdf.PDF_ENCRYPT_AES_256,
            owner_pw="synthetic-owner-password",
            user_pw="",
        )
    with pymupdf.open(owner_only_encrypted) as document:
        assert document.needs_pass == 0
        assert document.is_encrypted is False
        assert document.metadata["encryption"] is not None

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
        (
            "owner-only-encrypted",
            owner_only_encrypted,
            "is encrypted or password-protected",
        ),
        ("empty", empty, "is empty; expected a PDF"),
        ("truncated", truncated, "is damaged or incomplete; PDF repair would be required"),
        ("renamed", renamed, "is not a PDF"),
    ]


def _write_late_repair_pdf(path: Path) -> Path:
    """Corrupt the content-stream xref offset so extraction triggers repair."""
    with pymupdf.open() as document:
        page = document.new_page()
        page.insert_text((72, 72), MARKER_QUOTE)
        document.save(path)
    with pymupdf.open(path) as document:
        content_xref = document[0].get_contents()[0]

    damaged = bytearray(path.read_bytes())
    xref_header = re.search(rb"xref\n0 \d+\n", damaged)
    assert xref_header is not None
    entry_start = xref_header.end() + content_xref * 20
    assert re.fullmatch(rb"\d{10} 00000 n \n", damaged[entry_start : entry_start + 20])
    damaged[entry_start : entry_start + 10] = b"0000000001"
    path.write_bytes(damaged)
    return path


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
        timeout=60,
        check=False,
    )


@pytest.fixture
def unusual_pdf_batch(tmp_path: Path) -> tuple[Path, list[tuple[str, Path, str]]]:
    cases = _write_unusual_pdfs(tmp_path)
    batch = tmp_path / "unusual-pdfs.jsonl"
    batch.write_text(
        "".join(
            json.dumps({"id": name, "pdf": str(path), "page": 1, "quote": MARKER_QUOTE}) + "\n"
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
    assert output["summary"] == {"total": 5, "passed": 0, "failed": 0, "errors": 5}
    assert [result["id"] for result in output["results"]] == [name for name, _, _ in cases]
    for result, (_, path, expected_detail) in zip(output["results"], cases, strict=True):
        assert result["status"] == "error"
        assert result["verified"] is False
        assert result["reason"] == "pdf_unreadable"
        assert result["pdf"] == str(path)
        assert result["detail"] == f"{path} {expected_detail}"
        assert "\n" not in result["detail"]


@pytest.mark.parametrize(
    "case_index",
    range(5),
    ids=("encrypted", "owner-only-encrypted", "empty", "truncated", "renamed"),
)
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


def test_extraction_triggered_repair_is_rejected_before_text_is_trusted(tmp_path: Path) -> None:
    damaged = _write_late_repair_pdf(tmp_path / "late-repair.pdf")
    with pymupdf.open(damaged) as recovered:
        assert recovered.is_repaired is False
        assert MARKER_QUOTE in recovered[0].get_text()
        assert recovered.is_repaired is True

    error = "is damaged or incomplete; PDF repair would be required"
    with pytest.raises(PdfLoadError, match=error):
        verify_quote(MARKER_QUOTE, 1, damaged)
    with pytest.raises(PdfLoadError, match=error):
        extract_page_text(damaged, 1)

    result = check_quote(str(damaged), 1, MARKER_QUOTE)
    assert result["status"] == "error"
    assert result["verified"] is False
    assert result["reason"] == "pdf_unreadable"
    assert result["detail"] == f"{damaged} {error}"
