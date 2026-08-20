"""Deterministic verification gate for cited quotes.

VERIFICATION CONTRACT

1. Extraction. The gate extracts the text of the cited page with PyMuPDF
   (pinned version). It reads that page and no other page.
2. Normalization. The gate normalizes the quote and the page text with the same
   steps, in this order:
   a. Unicode NFKC normalization.
   b. Remove each soft hyphen (U+00AD) together with any whitespace that
      immediately follows it.
   c. Casefold.
   d. Collapse every run of whitespace to a single space.
   e. Strip leading and trailing whitespace.
3. Match. The claim verifies only if the normalized quote is a contiguous
   substring of the normalized text of the cited page. There is no fuzzy
   matching, no edit distance, and no cross-page search.
4. A miss is a rejection, always. Every rejection carries a machine-readable
   reason: ``page_out_of_range`` or ``quote_not_found_on_cited_page``.

Uncited claims are blocked from the ledger.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from pathlib import Path

import pymupdf
from pydantic import BaseModel, ConfigDict, Field

from specguard.models import DocumentRecord, RejectionReason

SOFT_HYPHEN = "\u00ad"

_SOFT_HYPHEN_RUN = re.compile(SOFT_HYPHEN + r"\s*")
_WHITESPACE_RUN = re.compile(r"\s+")


class VerificationResult(BaseModel):
    """Outcome of one call to :func:`verify_quote`."""

    model_config = ConfigDict(frozen=True)

    verified: bool = Field(description="True only if the gate found the quote on the cited page.")
    pdf_path: str = Field(description="Path of the document the gate read.")
    page_number: int = Field(description="One-based page number the gate was asked to read.")
    page_count: int = Field(ge=0, description="Number of pages in the document.")
    rejection_reason: RejectionReason | None = Field(
        default=None, description="Machine-readable reason for a rejection; None when verified."
    )
    normalized_quote: str = Field(
        default="", description="The quote after normalization, for agent retry feedback."
    )


def normalize(text: str) -> str:
    """Normalize text by the contract steps in :mod:`specguard.gate`.

    The same function normalizes both the quote and the page text. Any change
    here changes the contract.
    """
    text = unicodedata.normalize("NFKC", text)
    text = _SOFT_HYPHEN_RUN.sub("", text)
    text = text.casefold()
    text = _WHITESPACE_RUN.sub(" ", text)
    return text.strip()


def extract_page_text(pdf_path: str | Path, page_number: int) -> str:
    """Return the raw text of a one-based page number.

    Raises ``IndexError`` if the page number is outside the document.
    """
    with pymupdf.open(str(pdf_path)) as document:
        if page_number < 1 or page_number > document.page_count:
            raise IndexError(f"page {page_number} is outside a {document.page_count}-page document")
        return document[page_number - 1].get_text()


def verify_quote(quote: str, page_number: int, pdf_path: str | Path) -> VerificationResult:
    """Verify that ``quote`` appears on page ``page_number`` of ``pdf_path``.

    The gate follows the verification contract in the module docstring. It
    reads the cited page only. A miss is a rejection, always.
    """
    path_text = str(pdf_path)
    with pymupdf.open(path_text) as document:
        page_count = document.page_count
        if page_number < 1 or page_number > page_count:
            return VerificationResult(
                verified=False,
                pdf_path=path_text,
                page_number=page_number,
                page_count=page_count,
                rejection_reason=RejectionReason.PAGE_OUT_OF_RANGE,
                normalized_quote=normalize(quote),
            )
        page_text = document[page_number - 1].get_text()

    normalized_quote = normalize(quote)
    normalized_page = normalize(page_text)

    found = bool(normalized_quote) and normalized_quote in normalized_page
    return VerificationResult(
        verified=found,
        pdf_path=path_text,
        page_number=page_number,
        page_count=page_count,
        rejection_reason=None if found else RejectionReason.QUOTE_NOT_FOUND_ON_CITED_PAGE,
        normalized_quote=normalized_quote,
    )


def build_document_record(pdf_path: str | Path) -> DocumentRecord:
    """Record chain-of-custody metadata for a document.

    The SHA-256 records which byte stream was read. It is provenance metadata
    only and no part of the verification gate reads it.
    """
    path = Path(pdf_path)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    with pymupdf.open(str(path)) as document:
        page_count = document.page_count
    return DocumentRecord(path=str(path), sha256=digest, page_count=page_count)
