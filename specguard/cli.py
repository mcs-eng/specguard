"""Command-line quote check: is this quote on this page of this PDF?

``specguard quote-check`` runs the verification gate in :mod:`specguard.gate`
on a local PDF. The verdict for every record comes from
:func:`specguard.gate.verify_quote` and nothing else, so the CLI admits exactly
what the gate admits: the normalized quote must sit on token boundaries on the
cited page. The extra fields in each result (match offset, surrounding text,
longest matching prefix) are diagnostics for a human or an agent deciding how
to retry. They never change a verdict.

This module imports only the gate, the models, and the standard library. It
needs none of the hosted demo's dependencies (ADK, Gemini, Firestore, Cloud
Storage, FastAPI), which are the ``demo`` extra.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pymupdf

from specguard.gate import find_on_boundaries, normalize, verify_quote
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


def check_quote(
    pdf: str,
    page: int,
    quote: str,
    *,
    record_id: object = None,
    line: int | None = None,
    pdf_root: Path | None = None,
) -> dict[str, Any]:
    """Run the gate on one quote and return a result record.

    The verdict is :func:`specguard.gate.verify_quote`, which reads the file
    itself. The CLI reads the file's bytes once before the gate and once after
    it. The diagnostics in ``match`` come from that first snapshot, and the
    record carries its SHA-256. If the two reads differ, the file changed while
    it was checked and the record is an error, not a verdict. A writer that
    replaces the file and restores it between the reads is outside this check,
    exactly as it is outside the gate's own guarantee.
    """
    path = Path(pdf)
    if pdf_root is not None and not path.is_absolute():
        path = pdf_root / path
    shown_path = str(path)
    common = {"record_id": record_id, "line": line, "pdf": shown_path, "page": page, "quote": quote}

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
        snapshot = path.read_bytes()
        verdict = verify_quote(quote, page, path)
        unchanged = path.read_bytes() == snapshot
    except Exception as exc:  # any open or read failure is reported, not raised
        return _result(
            status="error",
            reason=ERROR_PDF_UNREADABLE,
            detail=f"could not read {shown_path}: {type(exc).__name__}: {exc}",
            normalized_quote=normalized_quote,
            **common,
        )

    common["pdf_sha256"] = hashlib.sha256(snapshot).hexdigest()
    if not unchanged:
        return _result(
            status="error",
            reason=ERROR_PDF_CHANGED,
            detail=f"{shown_path} changed while it was being checked; re-run once it is stable",
            normalized_quote=normalized_quote,
            **common,
        )

    if verdict.rejection_reason is RejectionReason.PAGE_OUT_OF_RANGE:
        return _result(
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

    try:
        filetype = path.suffix.lstrip(".") or "pdf"
        with pymupdf.open(stream=snapshot, filetype=filetype) as document:
            normalized_page = normalize(document[page - 1].get_text())
    except Exception as exc:  # the gate opened these bytes, so this is not expected
        return _result(
            status="error",
            reason=ERROR_PDF_UNREADABLE,
            detail=f"could not re-open the snapshot of {shown_path}: {type(exc).__name__}",
            page_count=verdict.page_count,
            normalized_quote=verdict.normalized_quote,
            **common,
        )

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
        return _result(
            status="pass",
            reason=None,
            detail=f"found on page {page} at offset {offset} of the normalized page text",
            page_count=verdict.page_count,
            normalized_quote=needle,
            match=match,
            **common,
        )

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
    return _result(
        status="fail",
        reason=verdict.rejection_reason.value,
        detail=detail,
        page_count=verdict.page_count,
        normalized_quote=needle,
        match=match,
        **common,
    )


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


def _read_batch(source: str) -> str:
    """Read the batch input from a path, or from standard input for ``-``."""
    if source == "-":
        return _decode_batch(sys.stdin.buffer.read())
    path = Path(source)
    try:
        return _decode_batch(path.read_bytes())
    except OSError as exc:
        raise _UsageError(f"cannot read batch file {source!r}: {exc.strerror or exc}") from exc


def _check_batch_line(text: str, line: int, pdf_root: Path | None) -> dict[str, Any]:
    """Validate one batch line and run the gate on it, or describe why not."""
    try:
        record = json.loads(text)
    except json.JSONDecodeError as exc:
        return _result(
            record_id=None,
            line=line,
            status="error",
            reason=ERROR_INVALID_JSON,
            detail=f"line {line} is not valid JSON: {exc.msg} at column {exc.colno}",
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
                f"line {line} must be a JSON object with pdf, page, and quote; "
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
            detail=f"line {line}: " + "; ".join(problems),
            pdf=pdf if isinstance(pdf, str) else None,
            page=page if isinstance(page, int) and not isinstance(page, bool) else None,
            quote=quote if isinstance(quote, str) else None,
        )
    return check_quote(pdf, page, quote, record_id=record_id, line=line, pdf_root=pdf_root)


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
        name = f"[{result['id']}]" if result["id"] is not None else ""
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


def build_parser() -> argparse.ArgumentParser:
    """Return the ``specguard`` argument parser."""
    parser = argparse.ArgumentParser(
        prog="specguard",
        description="SpecGuard local tools. The hosted audit demo is not needed for these.",
    )
    commands = parser.add_subparsers(dest="command", metavar="COMMAND")
    commands.required = True
    quote_check = commands.add_parser(
        "quote-check",
        help="verify that quotes appear on their cited PDF pages",
        description=QUOTE_CHECK_DESCRIPTION,
        epilog=QUOTE_CHECK_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
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
        results = [
            _check_batch_line(raw, number, args.pdf_root) for number, raw in _batch_lines(text)
        ]
        if not results:
            raise _UsageError("the batch input holds no records; nothing was verified")
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
    try:
        document = _run_quote_check(args)
    except _UsageError as exc:
        print(f"specguard {args.command}: error: {exc}", file=sys.stderr)
        print(f"run 'specguard {args.command} --help' for the input format", file=sys.stderr)
        return EXIT_USAGE

    if args.format == "text":
        output = _render_text(document)
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(errors="backslashreplace")
    else:
        output = json.dumps(document, indent=2)
    print(output)
    return EXIT_ALL_PASSED if document["ok"] else EXIT_SOME_FAILED


if __name__ == "__main__":
    sys.exit(main())
