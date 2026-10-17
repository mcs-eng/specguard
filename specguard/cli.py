"""Command-line quote check: is this quote on this page of this PDF?

``specguard quote-check`` runs the verification gate in :mod:`specguard.gate`
on a local PDF. The CLI and :func:`specguard.gate.verify_quote` share the same
gate implementation, so the CLI admits exactly what the gate admits: the
normalized quote must sit on token boundaries on the cited page. The extra
fields in each result (match offset, surrounding text, longest matching prefix)
are diagnostics for a human or an agent deciding how to retry. They never
change a verdict.

This module imports only the gate, the models, and the standard library. It
needs none of the hosted demo's dependencies (ADK, Gemini, Firestore, Cloud
Storage, FastAPI), which are the ``demo`` extra.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import mmap
import sys
import tempfile
from collections.abc import Sequence
from pathlib import Path
from typing import Any, BinaryIO, Never

import pymupdf

from specguard.gate import (
    VerificationResult,
    _verify_quote_in_document,
    find_on_boundaries,
    normalize,
)
from specguard.models import RejectionReason

OUTPUT_SCHEMA = "specguard.quote-check/v1"

EXIT_ALL_PASSED = 0
EXIT_SOME_FAILED = 1
EXIT_USAGE = 2

#: Characters of normalized page text shown on each side of a match.
CONTEXT_CHARS = 40

#: Below this many normalized characters a page's text layer is likely a title
#: block or a seal alone, and a miss says more about the file than the quote.
#: It only changes the wording of a failure's detail, never a verdict.
SPARSE_TEXT_CHARS = 500

#: Bound Python heap use while copying or comparing arbitrarily large PDFs.
FILE_CHUNK_BYTES = 1024 * 1024

#: Record-level error reasons. A record with one of these was never judged by
#: the gate; it still counts against the exit code.
ERROR_INVALID_JSON = "invalid_json"
ERROR_INVALID_RECORD = "invalid_record"
ERROR_EMPTY_QUOTE = "empty_quote"
ERROR_PDF_NOT_FOUND = "pdf_not_found"
ERROR_PDF_UNREADABLE = "pdf_unreadable"
ERROR_PDF_CHANGED = "pdf_changed_during_check"

QUOTE_CHECK_DESCRIPTION = """\
Check that each quote appears verbatim on its cited page of a PDF.

The verdict is the SpecGuard gate: NFKC, soft-hyphen join, casefold, and
whitespace collapse are applied to both the quote and the page text, and the
quote must then be a contiguous substring of the cited page's text layer that
does not start or end inside a word or number. No fuzzy matching, no edit
distance, no search of other pages. A miss is a failure, always.

Single mode:  --pdf FILE --page N --quote TEXT [--id ID]
Batch mode:   --batch FILE   (JSON Lines; use - to read standard input)
"""

QUOTE_CHECK_EPILOG = """\
batch input (one JSON object per line; blank lines are skipped):
  {"id": "q1", "pdf": "A002 First Floor Plan.pdf", "page": 1, "quote": "FIRST FLOOR PLAN"}
  pdf    string, required. Relative paths resolve against --pdf-root, else the
         current directory.
  page   JSON integer, required, one-based.
  quote  string, required, non-empty after normalization.
  id     string or integer, optional; echoed back so results can be joined.
  The file may be UTF-8 (with or without BOM) or UTF-16 with a BOM.

output (--format json, the default): one JSON document on stdout
  {"schema": "specguard.quote-check/v1", "ok": bool,
   "summary": {"total", "passed", "failed", "errors"},
   "results": [{"id", "line", "status", "verified", "reason", "detail",
                "pdf", "pdf_sha256", "page", "page_count", "quote",
                "normalized_quote", "match"}]}
  pdf_sha256  SHA-256 of the bytes the gate read; null when the file was
              never read. A file that changes while it is checked is an error.
  status  "pass"  the gate verified the quote on the cited page
          "fail"  the gate rejected it; reason is page_out_of_range or
                  quote_not_found_on_cited_page
          "error" the record was never judged; reason is invalid_json,
                  invalid_record, empty_quote, pdf_not_found, pdf_unreadable,
                  or pdf_changed_during_check
  match   diagnostics, never part of the verdict: found, offset and context in
          the normalized page text, substring_occurrences (ignoring token
          boundaries), longest_prefix_found, page_text_chars.

exit codes:
  0  every record passed
  1  at least one record failed or errored
  2  usage error (bad arguments, unreadable or empty batch file)

examples:
  specguard quote-check --pdf spec.pdf --page 3 --quote "rated 20 amperes"
  specguard quote-check --batch quotes.jsonl --pdf-root D:\\drawings
  Get-Content quotes.jsonl | specguard quote-check --batch -     (ASCII quotes only
      on Windows PowerShell 5.1, which re-encodes pipes; prefer a file path)
"""


def _one_line(text: str) -> str:
    """Escape control characters so a diagnostic cannot forge another line."""
    return "".join(
        character if character.isprintable() else character.encode("unicode_escape").decode("ascii")
        for character in text
    )


def _unreadable_pdf_detail(path: Path, failure: Exception) -> str:
    """Format every PDF read failure with the resolved native path."""
    return (
        f"could not read {_one_line(str(path))}: "
        f"{type(failure).__name__}: {_one_line(str(failure))}"
    )


class _ArgumentParser(argparse.ArgumentParser):
    """Argument parser that reports invalid invocations on one clear line."""

    def error(self, message: str) -> Never:
        self.exit(EXIT_USAGE, f"{self.prog}: error: {_one_line(message)}\n")


class _UsageError(Exception):
    """A problem with the invocation itself, reported with exit code 2."""


def _json_type_name(value: object) -> str:
    """Name a decoded JSON value's type in JSON's own terms."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int | float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    return "object"


def _result(
    *,
    record_id: object,
    line: int | None,
    status: str,
    reason: str | None,
    detail: str,
    pdf: str | None,
    page: int | None,
    quote: str | None,
    page_count: int | None = None,
    normalized_quote: str | None = None,
    match: dict[str, Any] | None = None,
    pdf_sha256: str | None = None,
) -> dict[str, Any]:
    """Build one result record with every key present, in a fixed order."""
    return {
        "id": record_id,
        "line": line,
        "status": status,
        "verified": status == "pass",
        "reason": reason,
        "detail": detail,
        "pdf": pdf,
        "pdf_sha256": pdf_sha256,
        "page": page,
        "page_count": page_count,
        "quote": quote,
        "normalized_quote": normalized_quote,
        "match": match,
    }


def _longest_prefix_found(haystack: str, needle: str) -> str:
    """Return the longest prefix of ``needle`` that occurs anywhere in ``haystack``.

    Substring presence is monotonic in prefix length, so a binary search finds
    it. This is a retry hint only: it ignores token boundaries and never feeds
    the verdict.
    """
    low, high = 0, len(needle)
    while low < high:
        middle = (low + high + 1) // 2
        if needle[:middle] in haystack:
            low = middle
        else:
            high = middle - 1
    return needle[:low]


class _NotPdfError(Exception):
    """A readable input that PyMuPDF identifies as something other than PDF."""


def _snapshot_file(path: Path) -> tuple[BinaryIO, str]:
    """Copy a file to temporary storage and return the copy plus its digest."""
    digest = hashlib.sha256()
    snapshot = tempfile.TemporaryFile()
    try:
        with path.open("rb") as source:
            while chunk := source.read(FILE_CHUNK_BYTES):
                digest.update(chunk)
                snapshot.write(chunk)
        snapshot.seek(0)
    except Exception:
        snapshot.close()
        raise
    return snapshot, digest.hexdigest()


def _matches_snapshot(path: Path, snapshot: BinaryIO) -> bool:
    """Compare current file bytes with a temporary snapshot using bounded memory."""
    snapshot.seek(0)
    with path.open("rb") as current:
        while True:
            expected = snapshot.read(FILE_CHUNK_BYTES)
            actual = current.read(FILE_CHUNK_BYTES)
            if expected != actual:
                return False
            if not expected:
                return True


class _PdfSession:
    """One stable PDF parse shared by consecutive checks in a batch."""

    def __init__(self, path: Path, shown_path: str) -> None:
        self.path = path
        self.shown_path = shown_path
        self._mapping: mmap.mmap | None = None
        self._stream: memoryview | None = None
        self._snapshot, self.sha256 = _snapshot_file(path)
        try:
            self._snapshot.seek(0, 2)
            snapshot_size = self._snapshot.tell()
            self._snapshot.seek(0)
            if snapshot_size == 0:
                # Preserve PyMuPDF's stream-specific public error for empty files.
                self.document = pymupdf.open(stream=b"", filetype="pdf")
            else:
                self._mapping = mmap.mmap(
                    self._snapshot.fileno(), length=0, access=mmap.ACCESS_READ
                )
                self._stream = memoryview(self._mapping)
                self.document = pymupdf.open(stream=self._stream, filetype="pdf")
        except Exception:
            self._release_mapping()
            self._snapshot.close()
            raise
        if not self.document.is_pdf:
            self.document.close()
            self.document.stream = None
            self._release_mapping()
            self._snapshot.close()
            raise _NotPdfError
        self._pending: list[dict[str, Any]] = []
        self._closed = False

    def _release_mapping(self) -> None:
        """Release the zero-copy view after MuPDF has released the document."""
        if self._stream is not None:
            self._stream.release()
            self._stream = None
        if self._mapping is not None:
            self._mapping.close()
            self._mapping = None

    def verify(self, quote: str, page: int) -> tuple[VerificationResult, str | None]:
        """Run the canonical gate against this session's open document."""
        return _verify_quote_in_document(quote, page, self.path, self.document)

    def remember(self, result: dict[str, Any]) -> None:
        """Retain a result reference until the closing digest confirms stability."""
        self._pending.append(result)

    def _replace_pending(self, *, reason: str, detail: str, include_initial_digest: bool) -> None:
        for result in self._pending:
            replacement = _result(
                record_id=result["id"],
                line=result["line"],
                status="error",
                reason=reason,
                detail=detail,
                pdf=result["pdf"],
                page=result["page"],
                quote=result["quote"],
                normalized_quote=result["normalized_quote"],
                pdf_sha256=self.sha256 if include_initial_digest else None,
            )
            result.clear()
            result.update(replacement)

    def finish(self) -> None:
        """Close the parser and reject provisional results if the bytes changed."""
        if self._closed:
            return
        self._closed = True
        failure: Exception | None = None
        unchanged = False
        try:
            self.document.close()
        except Exception as exc:
            failure = exc
        else:
            try:
                self.document.stream = None
                self._release_mapping()
            except Exception as exc:
                failure = exc
        if failure is None:
            try:
                unchanged = _matches_snapshot(self.path, self._snapshot)
            except Exception as exc:
                failure = exc
        try:
            self._snapshot.close()
        except Exception as exc:
            if failure is None:
                failure = exc
        if failure is not None:
            self._replace_pending(
                reason=ERROR_PDF_UNREADABLE,
                detail=_unreadable_pdf_detail(self.path, failure),
                include_initial_digest=False,
            )
            return
        if not unchanged:
            self._replace_pending(
                reason=ERROR_PDF_CHANGED,
                detail=(
                    f"{self.shown_path} changed while it was being checked; "
                    "re-run once it is stable"
                ),
                include_initial_digest=True,
            )


class _PdfSessionCache:
    """Reuse one consecutive PDF safely without retaining an unbounded document set."""

    def __init__(self) -> None:
        self._current: _PdfSession | None = None

    def __enter__(self) -> _PdfSessionCache:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def get(self, path: Path, shown_path: str) -> _PdfSession:
        if self._current is not None and self._current.path != path:
            self._current.finish()
            self._current = None
        if self._current is None:
            self._current = _PdfSession(path, shown_path)
        return self._current

    def close(self) -> None:
        if self._current is not None:
            self._current.finish()
            self._current = None


def check_quote(
    pdf: str,
    page: int,
    quote: str,
    *,
    record_id: object = None,
    line: int | None = None,
    pdf_root: Path | None = None,
    _sessions: _PdfSessionCache | None = None,
) -> dict[str, Any]:
    """Run the gate on one quote and return a result record.

    The verdict and diagnostics share the gate's canonical implementation and
    one open PDF. The CLI copies and hashes the bytes with bounded memory before
    checking, then compares the file byte-for-byte with that snapshot. A change
    makes every result from the open session an error. Batch mode reuses the
    session only for consecutive records naming the same path, which bounds
    parser state while avoiding record-by-record reparsing.
    """
    if _sessions is None:
        with _PdfSessionCache() as sessions:
            return check_quote(
                pdf,
                page,
                quote,
                record_id=record_id,
                line=line,
                pdf_root=pdf_root,
                _sessions=sessions,
            )

    path = Path(pdf)
    if pdf_root is not None and not path.is_absolute():
        path = pdf_root / path
    raw_path = path.as_posix()
    shown_path = _one_line(raw_path)
    common = {"record_id": record_id, "line": line, "pdf": raw_path, "page": page, "quote": quote}

    normalized_quote = normalize(quote)
    if not normalized_quote:
        return _result(
            status="error",
            reason=ERROR_EMPTY_QUOTE,
            detail="the quote is empty after normalization; there is nothing to verify",
            normalized_quote=normalized_quote,
            **common,
        )
    try:
        if not path.exists():
            return _result(
                status="error",
                reason=ERROR_PDF_NOT_FOUND,
                detail=f"no file at {shown_path}",
                normalized_quote=normalized_quote,
                **common,
            )
        if not path.is_file():
            return _result(
                status="error",
                reason=ERROR_PDF_NOT_FOUND,
                detail=f"{shown_path} is not a file",
                normalized_quote=normalized_quote,
                **common,
            )
        session = _sessions.get(path, shown_path)
        verdict, normalized_page = session.verify(quote, page)
    except _NotPdfError:
        return _result(
            status="error",
            reason=ERROR_PDF_UNREADABLE,
            detail=f"{shown_path} is not a PDF",
            normalized_quote=normalized_quote,
            **common,
        )
    except Exception as exc:  # any open or read failure is reported, not raised
        return _result(
            status="error",
            reason=ERROR_PDF_UNREADABLE,
            detail=_unreadable_pdf_detail(path, exc),
            normalized_quote=normalized_quote,
            **common,
        )

    common["pdf_sha256"] = session.sha256

    if verdict.rejection_reason is RejectionReason.PAGE_OUT_OF_RANGE:
        result = _result(
            status="fail",
            reason=verdict.rejection_reason.value,
            detail=(
                f"page {page} is outside this {verdict.page_count}-page document "
                f"(pages are numbered 1 to {verdict.page_count})"
            ),
            page_count=verdict.page_count,
            normalized_quote=verdict.normalized_quote,
            **common,
        )
        session.remember(result)
        return result

    assert normalized_page is not None
    needle = verdict.normalized_quote
    offset = find_on_boundaries(normalized_page, needle)
    if (offset != -1) is not verdict.verified:
        return _result(
            status="error",
            reason=ERROR_PDF_CHANGED,
            detail=f"the gate's read of {shown_path} and the CLI's snapshot disagree",
            page_count=verdict.page_count,
            normalized_quote=needle,
            **common,
        )

    match: dict[str, Any] = {
        "found": verdict.verified,
        "offset": None,
        "context": None,
        "substring_occurrences": normalized_page.count(needle),
        "longest_prefix_found": None,
        "page_text_chars": len(normalized_page),
    }
    if verdict.verified:
        match["offset"] = offset
        start = max(0, offset - CONTEXT_CHARS)
        match["context"] = normalized_page[start : offset + len(needle) + CONTEXT_CHARS]
        result = _result(
            status="pass",
            reason=None,
            detail=f"found on page {page} at offset {offset} of the normalized page text",
            page_count=verdict.page_count,
            normalized_quote=needle,
            match=match,
            **common,
        )
        session.remember(result)
        return result

    if not normalized_page:
        detail = (
            f"page {page} has no extractable text (an image-only page or no text layer); "
            "the gate cannot verify any quote on it"
        )
    elif match["substring_occurrences"]:
        detail = (
            f"the normalized quote occurs {match['substring_occurrences']} time(s) on page "
            f"{page}, but every occurrence starts or ends inside a word or number"
        )
    else:
        prefix = _longest_prefix_found(normalized_page, needle)
        match["longest_prefix_found"] = prefix
        detail = (
            f"the normalized quote is not on page {page}; its longest prefix found there is "
            f"{len(prefix)} of {len(needle)} characters"
        )
        if len(normalized_page) < SPARSE_TEXT_CHARS:
            detail += (
                f"; the page's text layer holds only {len(normalized_page)} characters, so its "
                "content may be drawn as outlines or an image: check the page visually"
            )
    assert verdict.rejection_reason is not None
    result = _result(
        status="fail",
        reason=verdict.rejection_reason.value,
        detail=detail,
        page_count=verdict.page_count,
        normalized_quote=needle,
        match=match,
        **common,
    )
    session.remember(result)
    return result


def _decode_batch(raw: bytes) -> str:
    """Decode a batch file: UTF-16 when it carries a BOM, else UTF-8 (BOM optional)."""
    try:
        if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
            return raw.decode("utf-16")
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise _UsageError(f"batch input is not valid UTF-8 or BOM-marked UTF-16 ({exc})") from exc


def _batch_lines(text: str) -> list[tuple[int, str]]:
    """Split JSON Lines at physical line breaks only, skipping blank lines.

    ``str.splitlines`` would also split at U+2028, U+2029, and U+0085, which a
    JSON string may carry literally, and so cut one valid record in two.
    """
    lines = text.split("\n")
    return [(number, raw) for number, raw in enumerate(lines, start=1) if raw.strip(" \t\r")]


def _batch_source_label(source: str) -> str:
    """Return a human-readable name for a batch file or standard input."""
    return "standard input" if source == "-" else f"batch file {_one_line(source)}"


def _read_batch(source: str) -> str:
    """Read the batch input from a path, or from standard input for ``-``."""
    label = _batch_source_label(source)
    try:
        raw = sys.stdin.buffer.read() if source == "-" else Path(source).read_bytes()
    except (OSError, ValueError) as exc:
        message = exc.strerror if isinstance(exc, OSError) and exc.strerror else str(exc)
        raise _UsageError(f"cannot read {label}: {_one_line(message)}") from exc
    try:
        return _decode_batch(raw)
    except _UsageError as exc:
        raise _UsageError(f"{label}: {exc}") from exc


def _reject_json_constant(value: str) -> Never:
    """Reject Python's non-standard NaN and Infinity JSON extensions."""
    raise ValueError(f"{value} is not a valid JSON value")


def _check_batch_line(
    text: str,
    line: int,
    pdf_root: Path | None,
    source: str,
    sessions: _PdfSessionCache,
) -> dict[str, Any]:
    """Validate one batch line and run the gate on it, or describe why not."""
    location = f"{_batch_source_label(source)}, line {line}"
    try:
        record = json.loads(text, parse_constant=_reject_json_constant)
    except json.JSONDecodeError as exc:
        return _result(
            record_id=None,
            line=line,
            status="error",
            reason=ERROR_INVALID_JSON,
            detail=f"{location} is not valid JSON: {exc.msg} at column {exc.colno}",
            pdf=None,
            page=None,
            quote=None,
        )
    except (RecursionError, ValueError) as exc:
        message = _one_line(str(exc))
        return _result(
            record_id=None,
            line=line,
            status="error",
            reason=ERROR_INVALID_JSON,
            detail=f"{location} is not valid JSON: {message}",
            pdf=None,
            page=None,
            quote=None,
        )
    if not isinstance(record, dict):
        return _result(
            record_id=None,
            line=line,
            status="error",
            reason=ERROR_INVALID_RECORD,
            detail=(
                f"{location} must be a JSON object with pdf, page, and quote; "
                f"got a JSON {_json_type_name(record)}"
            ),
            pdf=None,
            page=None,
            quote=None,
        )

    record_id = record.get("id")
    pdf = record.get("pdf")
    page = record.get("page")
    quote = record.get("quote")
    problems: list[str] = []
    missing = [key for key in ("pdf", "page", "quote") if key not in record]
    if missing:
        problems.append("missing required field(s): " + ", ".join(missing))
    if record_id is not None and (
        isinstance(record_id, bool) or not isinstance(record_id, str | int)
    ):
        problems.append(
            f"id must be a string or an integer; got a JSON {_json_type_name(record_id)}"
        )
        record_id = None
    if "pdf" in record and (not isinstance(pdf, str) or not pdf.strip()):
        problems.append(f"pdf must be a non-empty string; got a JSON {_json_type_name(pdf)}")
    if "page" in record and (isinstance(page, bool) or not isinstance(page, int)):
        problems.append(
            f"page must be a JSON integer (one-based); got a JSON {_json_type_name(page)} "
            f"{json.dumps(page)}"
        )
    if "quote" in record and not isinstance(quote, str):
        problems.append(f"quote must be a string; got a JSON {_json_type_name(quote)}")
    if problems:
        return _result(
            record_id=record_id,
            line=line,
            status="error",
            reason=ERROR_INVALID_RECORD,
            detail=f"{location}: " + "; ".join(problems),
            pdf=pdf if isinstance(pdf, str) else None,
            page=page if isinstance(page, int) and not isinstance(page, bool) else None,
            quote=quote if isinstance(quote, str) else None,
        )
    return check_quote(
        pdf,
        page,
        quote,
        record_id=record_id,
        line=line,
        pdf_root=pdf_root,
        _sessions=sessions,
    )


def _envelope(results: list[dict[str, Any]]) -> dict[str, Any]:
    """Wrap results in the versioned output document with its summary."""
    passed = sum(1 for result in results if result["status"] == "pass")
    failed = sum(1 for result in results if result["status"] == "fail")
    errors = sum(1 for result in results if result["status"] == "error")
    return {
        "schema": OUTPUT_SCHEMA,
        "ok": bool(results) and passed == len(results),
        "summary": {"total": len(results), "passed": passed, "failed": failed, "errors": errors},
        "results": results,
    }


def _render_text(document: dict[str, Any]) -> str:
    """Render the output document as one line per result plus a summary line."""
    lines = []
    for result in document["results"]:
        label = result["status"].upper()
        where = f"line {result['line']}" if result["line"] is not None else ""
        name = f"[{_one_line(str(result['id']))}]" if result["id"] is not None else ""
        head = " ".join(part for part in (label, name, where) if part)
        page = f"page {result['page']}" if result["page"] is not None else "page ?"
        if result["status"] == "pass":
            lines.append(f"{head}  {page}  {result['quote']!r}")
        else:
            lines.append(f"{head}  {page}  {result['reason']}: {result['detail']}")
    summary = document["summary"]
    lines.append(
        f"{summary['passed']} passed, {summary['failed']} failed, {summary['errors']} errors "
        f"({summary['total']} total)"
    )
    return "\n".join(lines)


def _write_report(output: str) -> None:
    """Write one UTF-8, LF-terminated report on every supported platform."""
    reconfigure = getattr(sys.stdout, "reconfigure", None)
    if callable(reconfigure):
        reconfigure(encoding="utf-8", errors="backslashreplace", newline="\n")
    sys.stdout.write(output)
    sys.stdout.write("\n")


def build_parser() -> argparse.ArgumentParser:
    """Return the ``specguard`` argument parser."""
    parser = _ArgumentParser(
        prog="specguard",
        description="SpecGuard local tools. The hosted audit demo is not needed for these.",
        allow_abbrev=False,
    )
    commands = parser.add_subparsers(dest="command", metavar="COMMAND")
    quote_check = commands.add_parser(
        "quote-check",
        help="verify that quotes appear on their cited PDF pages",
        description=QUOTE_CHECK_DESCRIPTION,
        epilog=QUOTE_CHECK_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        allow_abbrev=False,
    )
    quote_check.add_argument("--pdf", help="PDF to read (single mode)")
    quote_check.add_argument("--page", type=int, help="one-based page number (single mode)")
    quote_check.add_argument("--quote", help="verbatim quote to find (single mode)")
    quote_check.add_argument("--id", dest="record_id", help="identifier echoed in the result")
    quote_check.add_argument(
        "--batch", metavar="FILE", help="JSON Lines file of {pdf, page, quote, id}; - for stdin"
    )
    quote_check.add_argument(
        "--pdf-root",
        type=Path,
        metavar="DIR",
        help="directory that relative pdf paths resolve against (default: current directory)",
    )
    quote_check.add_argument(
        "--format",
        choices=("json", "text"),
        default="json",
        help="json (default, machine-readable) or text (one line per result)",
    )
    return parser


def _run_quote_check(args: argparse.Namespace) -> dict[str, Any]:
    """Run single or batch mode and return the output document."""
    single = [name for name in ("pdf", "page", "quote") if getattr(args, name) is not None]
    if args.batch is not None:
        if single or args.record_id is not None:
            flags = ", ".join(f"--{name}" for name in single) or "--id"
            raise _UsageError(f"--batch cannot be combined with {flags}")
        text = _read_batch(args.batch)
        with _PdfSessionCache() as sessions:
            results = [
                _check_batch_line(raw, number, args.pdf_root, args.batch, sessions)
                for number, raw in _batch_lines(text)
            ]
        if not results:
            raise _UsageError(
                f"{_batch_source_label(args.batch)} holds no records; nothing was verified"
            )
        return _envelope(results)

    if len(single) != 3:
        missing = ", ".join(f"--{name}" for name in ("pdf", "page", "quote") if name not in single)
        raise _UsageError(
            f"single mode needs --pdf, --page, and --quote (missing {missing}), or use --batch FILE"
        )
    result = check_quote(
        args.pdf, args.page, args.quote, record_id=args.record_id, pdf_root=args.pdf_root
    )
    return _envelope([result])


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point for the ``specguard`` console script."""
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.error("the following arguments are required: COMMAND")
    try:
        document = _run_quote_check(args)
    except _UsageError as exc:
        print(f"specguard {args.command}: error: {_one_line(str(exc))}", file=sys.stderr)
        return EXIT_USAGE

    output = _render_text(document) if args.format == "text" else json.dumps(document, indent=2)
    _write_report(output)
    return EXIT_ALL_PASSED if document["ok"] else EXIT_SOME_FAILED


if __name__ == "__main__":
    sys.exit(main())
