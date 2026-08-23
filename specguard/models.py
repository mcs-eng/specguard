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


class DocumentRole(StrEnum):
    """Which of the two documents bound to one audit run is meant.

    The role, not a path, is what a caller passes to the integrity persistence
    tool. A caller therefore cannot point that tool at a document the run is
    not bound to, and cannot supply the evidence it records.
    """

    SPECIFICATION = "specification"
    SUBMITTED_DOCUMENT = "submitted_document"


class AgentMode(StrEnum):
    """How the runtime presents the two bound documents to the model.

    ``FULL_TEXT``
        The runtime extracts every page of both documents and sends all of that
        text in one message. The model makes no tool call of its own.
    ``NAVIGATE``
        The runtime sends the submitted document in full and a deterministic
        page index of the specification. The model reads the specification
        pages it wants through the read-only extraction tool.

    The mode changes what the model is shown and which tools it may call. It
    changes nothing about the verification gate, which runs on every claim in
    both modes.
    """

    FULL_TEXT = "full_text"
    NAVIGATE = "navigate"


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
        default=Severity.UNCLASSIFIED, description="Severity rating; annotated after verification."
    )
    severity_model_id: str | None = Field(
        default=None, description="Provenance model ID that classified the severity."
    )
    severity_status: str | None = Field(
        default=None, description="Outcome of severity classification: classified or fallback."
    )
    severity_reason: str | None = Field(
        default=None, description="Machine-readable fallback reason or HTTP error if fallback."
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
    #: The model identifier the severity endpoint reported for this call.
    severity_model_id: str | None = None
    #: The configured label of the endpoint that was called. It records what
    #: this deployment was pointed at, never what served the request.
    severity_endpoint_label: str | None = None
    severity_status: str | None = None
    severity_reason: str | None = None


class PdfTextResult(BaseModel):
    """Structured result from extraction of one bound document page."""

    model_config = ConfigDict(frozen=True)

    ok: bool
    document_role: DocumentRole
    page_number: int
    text: str | None = None
    error_code: str | None = None
    #: The exception class name, never its message. A PyMuPDF or OS error
    #: message names the file it failed on, and this result is returned to a
    #: model that must never learn a path outside its two bound documents.
    error_type: str | None = None


class QuarantinedDocument(BaseModel):
    """One document the text-layer integrity screen kept away from the model.

    Every value here is copied from the deterministic screen report. No model
    output reaches this record.
    """

    model_config = ConfigDict(frozen=True)

    document_role: DocumentRole = Field(description="Which bound document was screened.")
    document_sha256: str = Field(
        pattern=r"^[0-9a-f]{64}$",
        description="Lowercase hex SHA-256 of the screened byte stream. Chain of custody only.",
    )
    page_count: int = Field(ge=1, description="Number of pages the screen read.")
    flagged_pages: list[int] = Field(
        min_length=1, description="One-based pages that carry text the screen flagged."
    )
    detectors: list[str] = Field(
        default_factory=list,
        description="Names of the screen rules that flagged this document, in rule order.",
    )
    hidden_span_count: int = Field(ge=1, description="Number of flags the screen raised.")
    integrity_finding_id: str | None = Field(
        default=None,
        description="Identifier of the persisted integrity record, when it was written.",
    )
    persistence_reason: str | None = Field(
        default=None, description="Machine-readable reason the integrity record was not written."
    )


class RunQuarantine(BaseModel):
    """The disclosure that a run stopped before any text reached the model."""

    model_config = ConfigDict(frozen=True)

    reason: str = Field(min_length=1, description="Machine-readable reason for the quarantine.")
    documents: list[QuarantinedDocument] = Field(
        min_length=1, description="Every document the screen flagged, in bound order."
    )


class AuditModelUsage(BaseModel):
    """Exact Gemini token counts exposed by ADK for one audit run.

    A missing SDK value remains unavailable. The runtime never derives a token
    count from prompt text, output text, or model pricing.
    """

    model_config = ConfigDict(frozen=True)

    prompt_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    total_tokens: int | None = Field(default=None, ge=0)
    unavailable_reason: str | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def _has_counts_or_reason(self) -> AuditModelUsage:
        """Require either SDK counts or an explicit unavailability disclosure."""
        has_counts = any(
            count is not None
            for count in (self.prompt_tokens, self.output_tokens, self.total_tokens)
        )
        if not has_counts and self.unavailable_reason is None:
            raise ValueError("audit model usage needs SDK counts or an unavailability reason")
        if has_counts and self.unavailable_reason is not None:
            raise ValueError(
                "audit model usage with SDK counts cannot carry an unavailability reason"
            )
        return self


class ModelToolCall(BaseModel):
    """One function call a model turn initiated, as ADK reported it.

    This record is a receipt, not a transcript. It carries the tool name, the
    two bounded arguments that say what the model asked for, and a bounded
    reading of the tool's answer. It never carries the quote the model sent or
    the page text the tool returned, so the receipt cannot grow into a second
    copy of the documents.

    ``page_number`` is recorded exactly as the model supplied it, with no
    range constraint. A model that asks for page zero produces a receipt that
    says so; the tool still refuses the read.
    """

    model_config = ConfigDict(frozen=True)

    turn_index: int = Field(
        ge=0, description="Zero-based index of the runtime model turn this call belongs to."
    )
    tool_name: str = Field(min_length=1, description="Tool name as ADK reported the call.")
    document_role: DocumentRole | None = Field(
        default=None, description="Bound document role the model named, when it named one."
    )
    page_number: int | None = Field(
        default=None, description="Page number the model asked for, exactly as supplied."
    )
    response_verified: bool | None = Field(
        default=None, description="The ``verified`` field of the tool answer, when it had one."
    )
    response_error_code: str | None = Field(
        default=None, description="The ``error_code`` field of the tool answer, when it had one."
    )
    quote_sha256: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
        description=(
            "SHA-256 of the normalized quote a quote-checking call carried. It "
            "identifies which quote was checked without recording its text."
        ),
    )


class AuditRunSummary(BaseModel):
    """Stable counters and output path for one complete audit run.

    A quarantined run makes no model call, persists no claim finding, and
    drafts no RFI, so its counters are zero and ``rfi_path`` is ``None``. The
    ``quarantine`` field carries the disclosure instead.
    """

    model_config = ConfigDict(frozen=True)

    run_id: str = Field(min_length=1)
    claims_made: int = Field(ge=0)
    rejected: int = Field(ge=0)
    retried: int = Field(ge=0)
    findings_persisted: int = Field(ge=0)
    rfi_path: str | None = Field(default=None, min_length=1)
    quarantine: RunQuarantine | None = Field(
        default=None, description="Set when the integrity screen stopped the run."
    )
    severity_status: str | None = Field(
        default=None, description="Outcome of severity classification across findings."
    )
    severity_reason: str | None = Field(
        default=None, description="Fallback reason if severity classification fell back."
    )
    audit_model_usage: AuditModelUsage | None = Field(
        default=None,
        description="Exact Gemini usage metadata, or its recorded unavailability reason.",
    )
    agent_mode: AgentMode = Field(
        default=AgentMode.FULL_TEXT,
        description="How the runtime presented the documents to the model for this run.",
    )
    model_tool_calls: list[ModelToolCall] = Field(
        default_factory=list,
        description="Every model-initiated tool call this run recorded, in the order made.",
    )
    self_check_rejections: int = Field(
        default=0,
        ge=0,
        description="Model-initiated quote checks that answered that the quote was not found.",
    )
    self_check_rejected_quote_returned: bool = Field(
        default=False,
        description=(
            "Whether the model still returned a quote its own check had rejected. "
            "Together with the count above this is the honest measure of whether "
            "the self-check changed anything; the runtime gate never trusted it."
        ),
    )

    @property
    def verified(self) -> int:
        """Return the former counter name for read-only compatibility."""
        return self.findings_persisted

    @property
    def quarantined(self) -> bool:
        """True when the integrity screen stopped this run before the model."""
        return self.quarantine is not None
