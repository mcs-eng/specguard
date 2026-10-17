"""One validated loading boundary for PDFs used by the verification gate."""

from __future__ import annotations

from pathlib import Path

import pymupdf

_PDF_HEADER_WINDOW = 1024
_PDF_TRAILER_WINDOW = 1024
_PDF_WHITESPACE = b"\x00\x09\x0a\x0c\x0d\x20"


class PdfLoadError(RuntimeError):
    """A PDF input the gate cannot safely use for a verdict."""


def _source_prefix(source: str | Path | bytes) -> bytes:
    if isinstance(source, bytes):
        return source[:_PDF_HEADER_WINDOW]
    with Path(source).open("rb") as handle:
        return handle.read(_PDF_HEADER_WINDOW)


def _unopenable_detail(source: str | Path | bytes) -> str:
    prefix = _source_prefix(source)
    if not prefix:
        return "is empty; expected a PDF"
    if b"%PDF-" not in prefix:
        return "is not a PDF"
    return "is damaged or incomplete; PDF repair would be required"


def _source_tail(source: str | Path | bytes) -> bytes:
    if isinstance(source, bytes):
        return source[-_PDF_TRAILER_WINDOW:]
    with Path(source).open("rb") as handle:
        handle.seek(0, 2)
        handle.seek(max(0, handle.tell() - _PDF_TRAILER_WINDOW))
        return handle.read()


def _has_complete_trailer(source: str | Path | bytes) -> bool:
    return _source_tail(source).rstrip(_PDF_WHITESPACE).endswith(b"%%EOF")


def open_pdf(source: str | Path | bytes) -> pymupdf.Document:
    """Open one intact, unencrypted PDF or raise a stable one-line error.

    MuPDF can silently repair damaged files and then expose their recovered
    text. Recovered text is not authoritative enough for a quote verdict, so a
    repaired document is rejected at the same boundary as an unreadable one.
    """
    if isinstance(source, bytes) and not source:
        raise PdfLoadError("is empty; expected a PDF")
    try:
        document = (
            pymupdf.open(stream=source, filetype="pdf")
            if isinstance(source, bytes)
            else pymupdf.open(str(source))
        )
    except pymupdf.EmptyFileError as error:
        raise PdfLoadError("is empty; expected a PDF") from error
    except pymupdf.FileDataError as error:
        raise PdfLoadError(_unopenable_detail(source)) from error

    try:
        if not document.is_pdf:
            raise PdfLoadError("is not a PDF")
        if document.needs_pass or document.is_encrypted:
            raise PdfLoadError("is encrypted or password-protected")
        if document.is_repaired or not _has_complete_trailer(source):
            raise PdfLoadError("is damaged or incomplete; PDF repair would be required")
    except BaseException:
        document.close()
        raise
    return document
