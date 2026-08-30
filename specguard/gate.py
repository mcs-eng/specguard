"""Deterministic verification gate for cited quotes.

VERIFICATION CONTRACT

1. Extraction. The gate extracts the text of the cited page with PyMuPDF
   (pinned version). It reads that page and no other page.
2. Normalization. The gate normalizes the quote and the page text with the same
   steps, in this order:
   a. Unicode NFKC normalization.
   b. Rejoin a word broken by a soft hyphen: where a soft hyphen (U+00AD) sits
      between two letters, remove it together with any whitespace that follows
      it. A soft hyphen next to a digit never joins, so ``1<U+00AD>2`` stays
      two tokens and does not become ``12``.
   c. Remove every remaining soft hyphen, leaving the surrounding whitespace
      alone.
   d. Casefold.
   e. Collapse every run of whitespace to a single space.
   f. Strip leading and trailing whitespace.
3. Match. The claim verifies only if the normalized quote is a contiguous
   substring of the normalized text of the cited page, and the substring sits
   on token boundaries: the match may not begin or end in the middle of a word
   or a number. A digit at the edge of the quote may not sit against a
   character that binds to a number either, so a claim quoting ``5 A`` cannot
   ride on the page text ``0.5 A``. There is no fuzzy matching, no edit
   distance, and no cross-page search.
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

#: A soft hyphen joins a word across a line break only between two letters.
#: ``[^\W\d_]`` is "letter": word character, but not a digit and not underscore.
_SOFT_HYPHEN_WORD_JOIN = re.compile(r"(?<=[^\W\d_])" + SOFT_HYPHEN + r"\s*(?=[^\W\d_])")
_WHITESPACE_RUN = re.compile(r"\s+")

#: Characters that bind to a digit and so must not sit against a quote edge
#: that is itself a digit. Without these, the page text ``0.5 A`` would satisfy
#: a claim quoting ``5 A``, and ``-5 kPa`` would satisfy ``5 kPa``.
_NUMBER_BINDING = frozenset(".,-+/±⁄−")


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
    text = _SOFT_HYPHEN_WORD_JOIN.sub("", text)
    text = text.replace(SOFT_HYPHEN, "")
    text = text.casefold()
    text = _WHITESPACE_RUN.sub(" ", text)
    return text.strip()


def _cuts_a_token(edge: str, neighbour: str) -> bool:
    """True if ``neighbour`` continues the token that ``edge`` belongs to.

    An alphanumeric edge may not sit against another alphanumeric character.
    A digit edge additionally may not sit against a character that binds to a
    number, so a quote of ``5 A`` cannot ride on the page text ``0.5 A``.
    """
    if not edge.isalnum():
        return False
    if neighbour.isalnum():
        return True
    return edge.isdigit() and neighbour in _NUMBER_BINDING


def _sits_on_token_boundaries(haystack: str, needle: str, start: int) -> bool:
    """True if the match at ``start`` does not cut a word or a number in half."""
    end = start + len(needle)
    if start > 0 and _cuts_a_token(needle[0], haystack[start - 1]):
        return False
    if end < len(haystack) and _cuts_a_token(needle[-1], haystack[end]):
        return False
    return True


def contains_on_boundaries(haystack: str, needle: str) -> bool:
    """True if ``needle`` occurs in ``haystack`` on token boundaries.

    Both arguments must already be normalized. Every occurrence is checked, not
    only the first: an occurrence that cuts a number in half does not hide a
    later occurrence that does not.
    """
    if not needle:
        return False
    start = haystack.find(needle)
    while start != -1:
        if _sits_on_token_boundaries(haystack, needle, start):
            return True
        start = haystack.find(needle, start + 1)
    return False


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

    found = contains_on_boundaries(normalized_page, normalized_quote)
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
