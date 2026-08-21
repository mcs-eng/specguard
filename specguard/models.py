"""Data schema for SpecGuard findings and document provenance.

These models carry the data that the verification gate in :mod:`specguard.gate`
accepts or rejects. They hold no matching logic of their own.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator


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

    Phase 1 never computes a severity, so a finding built by Phase 1 code keeps
    the ``UNCLASSIFIED`` default. The other members exist so a later phase can
    set a real value. Nothing in the gate reads this field.
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

    The schema requires a finding to carry at least one cited quote, and it
    keeps ``verification_status`` and ``rejection_reason`` consistent with each
    other. It does not and cannot prove that the gate was ever run: a caller
    that sets ``VERIFIED`` by hand gets a ``VERIFIED`` finding. Binding the
    status to a real :func:`specguard.gate.verify_quote` result is the job of
    the persistence path in a later phase.
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

    @model_validator(mode="after")
    def _status_and_reason_agree(self) -> Finding:
        """A rejection needs a reason, and nothing else may carry one."""
        rejected = self.verification_status is VerificationStatus.REJECTED
        if rejected and self.rejection_reason is None:
            raise ValueError("a rejected finding must carry a rejection_reason")
        if not rejected and self.rejection_reason is not None:
            raise ValueError("only a rejected finding may carry a rejection_reason")
        return self


class AuditClaim(BaseModel):
    """One model-generated discrepancy claim with paired source evidence."""

    model_config = ConfigDict(frozen=True)

    claim_description: str = Field(
        min_length=1, description="Plain-language description of the direct discrepancy."
    )
    spec_quote: str = Field(
        min_length=1, description="Verbatim quote from the specification document."
    )
    spec_page: int = Field(ge=1, description="One-based specification page number.")
    cut_sheet_quote: str = Field(
        min_length=1, description="Verbatim quote from the cut-sheet document."
    )
    cut_sheet_page: int = Field(ge=1, description="One-based cut-sheet page number.")


class AuditClaimBatch(BaseModel):
    """Structured output returned by the model for one audit turn."""

    model_config = ConfigDict(frozen=True)

    claims: list[AuditClaim] = Field(
        default_factory=list,
        description="Direct discrepancies supported by one quote from each document.",
    )


class PersistedQuote(BaseModel):
    """A stored quote that references its source document by SHA-256."""

    model_config = ConfigDict(frozen=True)

    text: str = Field(min_length=1)
    page_number: int = Field(ge=1)
    document_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class PersistedFinding(BaseModel):
    """A finding returned after the guarded Firestore write succeeds."""

    model_config = ConfigDict(frozen=True)

    finding_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    claim_text: str = Field(min_length=1)
    spec_quote: PersistedQuote
    cut_sheet_quote: PersistedQuote
    severity: Severity = Severity.UNCLASSIFIED


class PdfTextResult(BaseModel):
    """Structured result from the PDF extraction tool."""

    model_config = ConfigDict(frozen=True)

    ok: bool
    pdf_path: str
    page_number: int
    text: str | None = None
    error_code: str | None = None
    error_message: str | None = None


class AuditRunSummary(BaseModel):
    """Stable counters and output path for one complete audit run."""

    model_config = ConfigDict(frozen=True)

    run_id: str = Field(min_length=1)
    claims_made: int = Field(ge=0)
    rejected: int = Field(ge=0)
    retried: int = Field(ge=0)
    findings_persisted: int = Field(ge=0)
    rfi_path: str = Field(min_length=1)

    @property
    def verified(self) -> int:
        """Return the former counter name for read-only compatibility."""
        return self.findings_persisted
