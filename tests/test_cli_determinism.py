"""Cross-platform byte stability for ``specguard quote-check`` reports."""

from __future__ import annotations

import io
import sys
from pathlib import PurePosixPath, PureWindowsPath

import pytest

from specguard import cli


class _MissingPosixPath(PurePosixPath):
    """A synthetic missing path with POSIX display semantics."""

    def exists(self) -> bool:
        return False


class _MissingWindowsPath(PureWindowsPath):
    """A synthetic missing path with Windows display semantics."""

    def exists(self) -> bool:
        return False


EXPECTED_JSON = """\
{
  "schema": "specguard.quote-check/v1",
  "ok": false,
  "summary": {
    "total": 1,
    "passed": 0,
    "failed": 0,
    "errors": 1
  },
  "results": [
    {
      "id": null,
      "line": null,
      "status": "error",
      "verified": false,
      "reason": "pdf_not_found",
      "detail": "no file at nested/\\ufb01xture.pdf",
      "pdf": "nested/\\ufb01xture.pdf",
      "pdf_sha256": null,
      "page": 1,
      "page_count": null,
      "quote": "Synthetic quote.",
      "normalized_quote": "synthetic quote.",
      "match": null
    }
  ]
}
""".encode("ascii")

EXPECTED_TEXT = (
    "ERROR  page 1  pdf_not_found: no file at nested/ﬁxture.pdf\n"
    "0 passed, 0 failed, 1 errors (1 total)\n"
).encode()


def _report_bytes(
    monkeypatch: pytest.MonkeyPatch,
    path_type: type[PurePosixPath] | type[PureWindowsPath],
    *,
    encoding: str,
    newline: str,
    report_format: str,
) -> tuple[int, bytes]:
    """Run one synthetic missing-file report through a chosen host text stream."""
    raw = io.BytesIO()
    stdout = io.TextIOWrapper(raw, encoding=encoding, newline=newline)
    with monkeypatch.context() as patch:
        patch.setattr(cli, "Path", path_type)
        patch.setattr(sys, "stdout", stdout)
        exit_code = cli.main(
            [
                "quote-check",
                "--pdf",
                "nested/ﬁxture.pdf",
                "--page",
                "1",
                "--quote",
                "Synthetic quote.",
                "--format",
                report_format,
            ]
        )
        stdout.flush()
        report = raw.getvalue()
    stdout.detach()
    return exit_code, report


@pytest.mark.parametrize(
    ("report_format", "expected"),
    [("json", EXPECTED_JSON), ("text", EXPECTED_TEXT)],
)
def test_report_bytes_are_identical_on_windows_and_posix(
    monkeypatch: pytest.MonkeyPatch, report_format: str, expected: bytes
) -> None:
    """Host path, newline, and encoding conventions cannot change a report."""
    posix_code, posix_report = _report_bytes(
        monkeypatch,
        _MissingPosixPath,
        encoding="utf-8",
        newline="\n",
        report_format=report_format,
    )
    windows_code, windows_report = _report_bytes(
        monkeypatch,
        _MissingWindowsPath,
        encoding="cp1252",
        newline="\r\n",
        report_format=report_format,
    )

    assert posix_code == windows_code == cli.EXIT_SOME_FAILED
    assert posix_report == expected
    assert windows_report == expected
    assert b"\r" not in posix_report
    assert b"\r" not in windows_report
