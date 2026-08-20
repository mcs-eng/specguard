"""Schema tests for the finding and document models."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from specguard.models import (
    CitedQuote,
    DocumentRecord,
    Finding,
    RejectionReason,
    Severity,
    VerificationStatus,
)

QUOTE = CitedQuote(
    text="Panelboard MDP-2 shall be rated 208 volts",
    page_number=2,
    document_path="fictional_spec.pdf",
)


def finding(**overrides: object) -> Finding:
    fields: dict[str, object] = {
        "submittal_id": "SUB-0007",
        "spec_locator": "Section 26 27 26, paragraph 2.1",
        "cut_sheet_locator": "Ratings table, row 3",
        "claim_text": "The cut sheet rates the panelboard at a different voltage than the spec.",
        "quotes": [QUOTE],
    }
    fields.update(overrides)
    return Finding(**fields)  # type: ignore[arg-type]


def test_finding_defaults_are_pending_and_unclassified() -> None:
    result = finding()
    assert result.verification_status is VerificationStatus.PENDING
    assert result.severity is Severity.UNCLASSIFIED
    assert result.rejection_reason is None


def test_finding_carries_a_machine_readable_rejection_reason() -> None:
    result = finding(
        verification_status=VerificationStatus.REJECTED,
        rejection_reason=RejectionReason.QUOTE_NOT_FOUND_ON_CITED_PAGE,
    )
    assert result.rejection_reason.value == "quote_not_found_on_cited_page"


def test_finding_requires_at_least_one_quote() -> None:
    """Uncited claims are blocked from the ledger; the schema refuses them."""
    with pytest.raises(ValidationError):
        finding(quotes=[])


def test_cited_quote_page_numbers_are_one_based() -> None:
    with pytest.raises(ValidationError):
        CitedQuote(text="anything", page_number=0, document_path="fictional_spec.pdf")


def test_document_record_requires_a_hex_sha256() -> None:
    with pytest.raises(ValidationError):
        DocumentRecord(path="fictional_spec.pdf", sha256="not-a-digest", page_count=4)


def test_document_record_accepts_a_valid_digest() -> None:
    record = DocumentRecord(path="fictional_spec.pdf", sha256="ab" * 32, page_count=4)
    assert record.page_count == 4


def test_finding_round_trips_through_json() -> None:
    original = finding(verification_status=VerificationStatus.VERIFIED)
    assert Finding.model_validate_json(original.model_dump_json()) == original
