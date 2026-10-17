"""Regression matrix for hostile files and invalid ``specguard`` arguments."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

from specguard.cli import main

EXACT_QUOTE = "Receptacles shall be specification grade, rated 20 amperes at 125 volts."


def _run_json(capsys: pytest.CaptureFixture[str], *argv: str) -> tuple[int, dict[str, Any]]:
    code = main(["quote-check", *argv])
    captured = capsys.readouterr()

    assert captured.err == ""
    return code, json.loads(captured.out)


def test_readable_non_pdf_with_a_matching_quote_is_rejected(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    source = tmp_path / "readable.md"
    source.write_text("# Synthetic manual\n\nSynthetic marker quote.\n", encoding="utf-8")

    code, document = _run_json(
        capsys,
        "--pdf",
        str(source),
        "--page",
        "1",
        "--quote",
        "Synthetic marker quote",
    )

    assert code == 1
    result = document["results"][0]
    assert result["status"] == "error"
    assert result["reason"] == "pdf_unreadable"
    assert result["pdf"] == source.as_posix()
    assert Path(result["pdf"]) == source
    assert result["detail"] == f"{source.as_posix()} is not a PDF"


@pytest.mark.parametrize(
    ("filename", "payload", "message"),
    [
        ("oversized-integer.jsonl", "9" * 5000, "Exceeds the limit"),
        ("deeply-nested.jsonl", "[" * 3000 + "]" * 3000, "maximum recursion depth"),
    ],
    ids=["oversized-integer", "deeply-nested"],
)
def test_json_decoder_limits_are_reported_as_record_errors(
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
    filename: str,
    payload: str,
    message: str,
) -> None:
    batch = tmp_path / filename
    batch.write_text(payload + "\n", encoding="utf-8")

    code, document = _run_json(capsys, "--batch", str(batch))

    assert code == 1
    assert document["summary"] == {"total": 1, "passed": 0, "failed": 0, "errors": 1}
    result = document["results"][0]
    assert result["status"] == "error"
    assert result["reason"] == "invalid_json"
    assert str(batch) in result["detail"]
    assert "line 1" in result["detail"]
    assert message in result["detail"]
    assert "\n" not in result["detail"]


@pytest.mark.parametrize("constant", ["NaN", "Infinity", "-Infinity"])
def test_nonstandard_json_constants_cannot_hide_in_an_otherwise_passing_record(
    capsys: pytest.CaptureFixture[str], tmp_path: Path, spec_pdf: Path, constant: str
) -> None:
    batch = tmp_path / "nonstandard.jsonl"
    valid_fields = json.dumps({"pdf": str(spec_pdf), "page": 1, "quote": EXACT_QUOTE})
    batch.write_text(valid_fields[:-1] + f', "extra": {constant}}}\n', encoding="utf-8")

    code, document = _run_json(capsys, "--batch", str(batch))

    assert code == 1
    result = document["results"][0]
    assert result["status"] == "error"
    assert result["reason"] == "invalid_json"
    assert str(batch) in result["detail"]
    assert "line 1" in result["detail"]
    assert constant in result["detail"]


class _UnreadableBuffer:
    def read(self) -> bytes:
        raise PermissionError("access denied")


class _UnreadableStdin:
    buffer = _UnreadableBuffer()


def test_unreadable_standard_input_is_one_usage_error(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sys, "stdin", _UnreadableStdin())

    code = main(["quote-check", "--batch", "-"])
    captured = capsys.readouterr()

    assert code == 2
    assert captured.out == ""
    assert (
        captured.err == "specguard quote-check: error: cannot read standard input: access denied\n"
    )


@pytest.mark.parametrize(
    "content",
    [
        b"\n  \n",
        b"\xff\xfe\x00",
    ],
    ids=["empty", "invalid-encoding"],
)
def test_batch_usage_errors_name_the_input_file(
    capsys: pytest.CaptureFixture[str], tmp_path: Path, content: bytes
) -> None:
    batch = tmp_path / "bad.jsonl"
    batch.write_bytes(content)

    code = main(["quote-check", "--batch", str(batch)])
    captured = capsys.readouterr()

    assert code == 2
    assert captured.out == ""
    assert captured.err.count("\n") == 1
    assert str(batch) in captured.err


def test_unreadable_batch_file_is_one_usage_error(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    batch = tmp_path / "denied.jsonl"
    batch.write_text("{}\n", encoding="utf-8")
    original = Path.read_bytes

    def deny_batch(path: Path) -> bytes:
        if path == batch:
            raise PermissionError("access denied")
        return original(path)

    monkeypatch.setattr(Path, "read_bytes", deny_batch)

    code = main(["quote-check", "--batch", str(batch)])
    captured = capsys.readouterr()

    assert code == 2
    assert captured.out == ""
    assert captured.err == (
        f"specguard quote-check: error: cannot read batch file {batch}: access denied\n"
    )


@pytest.mark.parametrize(
    "argv",
    [
        ["--he"],
        ["quote-check", "--he"],
        ["quote-check", "--bat", "quotes.jsonl"],
        ["quote-check", "--pdf", "spec.pdf", "--pag", "1", "--quote", "x"],
        ["quote-check", "--pdf", "spec.pdf", "--page", "1", "--quot", "x"],
        ["quote-check", "--forma", "text"],
    ],
)
def test_abbreviated_long_options_are_invalid(
    capsys: pytest.CaptureFixture[str], argv: list[str]
) -> None:
    with pytest.raises(SystemExit) as raised:
        main(argv)
    captured = capsys.readouterr()

    assert raised.value.code == 2
    assert captured.out == ""
    assert captured.err.count("\n") == 1
    assert "error: unrecognized arguments:" in captured.err


def test_control_characters_cannot_add_diagnostic_lines(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    with pytest.raises(SystemExit) as raised:
        main(["quote-check", "--bad\nflag"])
    invalid_flag = capsys.readouterr()

    assert raised.value.code == 2
    assert invalid_flag.out == ""
    assert invalid_flag.err.count("\n") == 1
    assert "--bad\\nflag" in invalid_flag.err

    missing_batch = tmp_path / "missing\nbatch.jsonl"
    code = main(["quote-check", "--batch", str(missing_batch)])
    batch_error = capsys.readouterr()

    assert code == 2
    assert batch_error.out == ""
    assert batch_error.err.count("\n") == 1
    assert "missing\\nbatch.jsonl" in batch_error.err

    missing_pdf = tmp_path / "missing\ninput.pdf"
    code = main(
        [
            "quote-check",
            "--pdf",
            str(missing_pdf),
            "--page",
            "1",
            "--quote",
            "x",
            "--format",
            "text",
        ]
    )
    pdf_error = capsys.readouterr()

    assert code == 1
    assert pdf_error.err == ""
    assert len(pdf_error.out.splitlines()) == 2
    assert "missing\\ninput.pdf" in pdf_error.out.splitlines()[0]

    code = main(
        [
            "quote-check",
            "--pdf",
            str(tmp_path / "missing-id.pdf"),
            "--page",
            "1",
            "--quote",
            "x",
            "--id",
            "first\nforged",
            "--format",
            "text",
        ]
    )
    id_error = capsys.readouterr()

    assert code == 1
    assert id_error.err == ""
    assert len(id_error.out.splitlines()) == 2
    assert "[first\\nforged]" in id_error.out.splitlines()[0]
