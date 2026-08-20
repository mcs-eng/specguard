"""Data schema for SpecGuard findings and document provenance.

These models carry the data that the verification gate in :mod:`specguard.gate`
accepts or rejects. They hold no matching logic of their own.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class VerificationStatus(StrEnum):
    """Where a finding stands with respect to the verification gate."""

    PENDING = "pending"
    VERIFIED = "verified"
    REJECTED = "rejected"


class RejectionReason(StrEnum):
    """Machine-readable reason that the gate rejected a quote.

    ``PAGE_OUT_OF_RANGE``
        The cited page number does not exist in the document.
    ``QUOTE_NOT_FOUND_ON_CITED_PAGE``
        The normalized quote is not a contiguous substring of the normalized
        text of the cited page.
    """

    PAGE_OUT_OF_RANGE = "page_out_of_range"
    QUOTE_NOT_FOUND_ON_CITED_PAGE = "quote_not_found_on_cited_page"


class Severity(StrEnum):
    """Placeholder severity scale.

    Phase 1 assigns ``UNCLASSIFIED`` to every finding. A later phase sets a
    real value; nothing in the gate reads this field.
    """

    UNCLASSIFIED = "unclassified"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class CitedQuote(BaseModel):
    """One verbatim quote and the one-based page it is cited from."""

    model_config = ConfigDict(frozen=True)

    text: str = Field(min_length=1, description="Verbatim quote as it appears in the source.")
    page_number: int = Field(ge=1, description="One-based page number the quote is cited from.")
    document_path: str = Field(min_length=1, description="Path of the document that is cited.")


class DocumentRecord(BaseModel):
    """Chain-of-custody metadata for one source document.

    ``sha256`` records which byte stream was read, so a later reader can show
    that two runs used the same file. It is provenance metadata only. It says
    nothing about whether a quote is real, and no part of the verification gate
    reads it.
    """

    model_config = ConfigDict(frozen=True)

    path: str = Field(min_length=1, description="Path of the document on disk.")
    sha256: str = Field(
        pattern=r"^[0-9a-f]{64}$",
        description="Lowercase hex SHA-256 of the file bytes. Chain-of-custody metadata only.",
    )
    page_count: int = Field(ge=0, description="Number of pages in the document.")


class Finding(BaseModel):
    """One discrepancy claim raised against a submittal.

    A finding reaches ``VerificationStatus.VERIFIED`` only after every quote in
    ``quotes`` passes the gate. Uncited claims are blocked from the ledger.
    """

    model_config = ConfigDict(frozen=True)

    submittal_id: str = Field(min_length=1, description="Identifier of the submittal under audit.")
    spec_locator: str = Field(
        min_length=1, description="Human-readable locator in the specification, such as a section."
    )
    cut_sheet_locator: str = Field(
        min_length=1, description="Human-readable locator in the cut sheet, such as a table name."
    )
    claim_text: str = Field(min_length=1, description="The discrepancy claim in plain language.")
    quotes: list[CitedQuote] = Field(
        min_length=1, description="Verbatim quotes with page numbers that support the claim."
    )
    severity: Severity = Field(
        default=Severity.UNCLASSIFIED, description="Placeholder severity; set in a later phase."
    )
    verification_status: VerificationStatus = Field(
        default=VerificationStatus.PENDING, description="Gate outcome for this finding."
    )
    rejection_reason: RejectionReason | None = Field(
        default=None, description="Set when the gate rejects the finding; otherwise None."
    )
