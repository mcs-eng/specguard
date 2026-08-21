"""Bypass attempts against the guarded persistence path.

Every test tries to reach the findings collection without a passing
write-time gate result. The invariant holds only if every attempt is
blocked and every legitimate write demonstrably re-runs the gate.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from specguard import gate as gate_module
from specguard.models import CitedQuote, RejectionReason, VerificationStatus
from specguard.tools import FINDINGS_COLLECTION
from tests.adversarial.harness import CUT_LINE, Harness
from tests.fixtures_pdf import write_pdf


def test_persist_finding_runs_the_gate_at_write_time(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Even a caller-set VERIFIED finding must trigger two fresh gate calls."""
    harness = Harness(tmp_path)
    calls: list[tuple[str, int, Path]] = []
    original_verify = gate_module.verify_quote

    def counting_verify(
        quote: str, page_number: int, pdf_path: str | Path
    ) -> gate_module.VerificationResult:
        calls.append((quote, page_number, Path(pdf_path).resolve()))
        return original_verify(quote, page_number, pdf_path)

    monkeypatch.setattr(gate_module, "verify_quote", counting_verify)
    result = harness.tools.persist_finding(harness.finding(status=VerificationStatus.VERIFIED))

    assert result["persisted"] is True
    assert len(calls) == 2
    assert {call[2] for call in calls} == {
        harness.spec.resolve(),
        harness.cut_sheet.resolve(),
    }


def test_hand_verified_finding_with_invented_quote_writes_nothing(tmp_path: Path) -> None:
    """A fabricated quote under a hand-set VERIFIED status must be blocked."""
    harness = Harness(tmp_path)
    result = harness.tools.persist_finding(
        harness.finding(
            cut_quote="This sentence is absent from the submitted document.",
            status=VerificationStatus.VERIFIED,
        )
    )
    assert result["persisted"] is False
    assert result["reason"] == "one_or_more_quotes_rejected_by_gate"
    assert harness.client.data == {}
    assert harness.client.batches == []


def test_a_passing_tool_check_carries_no_authority_at_write_time(tmp_path: Path) -> None:
    """A stale earlier verification must not carry a later write.

    The quote verifies through the public tool, then the document is
    replaced. Persistence must re-verify against the current bytes and
    refuse.
    """
    harness = Harness(tmp_path)
    first_check = harness.tools.verify_quote(CUT_LINE, 1, str(harness.cut_sheet))
    assert first_check["verified"] is True

    write_pdf(
        harness.cut_sheet,
        [["The submitted document was replaced after the check."], ["Second page."]],
    )
    result = harness.tools.persist_finding(harness.finding(status=VerificationStatus.VERIFIED))

    assert result["persisted"] is False
    assert result["reason"] == "one_or_more_quotes_rejected_by_gate"
    assert harness.client.data == {}
    assert harness.client.batches == []


def test_caller_status_has_no_authority_in_either_direction(tmp_path: Path) -> None:
    """The gate result, not the caller-set status, decides the stored status.

    A finding hand-marked REJECTED whose quotes verify is persisted as
    VERIFIED. This documents that ``persist_finding`` ignores the field
    entirely; the write-time gate is the only authority.
    """
    harness = Harness(tmp_path)
    result = harness.tools.persist_finding(
        harness.finding(
            status=VerificationStatus.REJECTED,
            reason=RejectionReason.QUOTE_NOT_FOUND_ON_CITED_PAGE,
        )
    )
    assert result["persisted"] is True
    stored = next(iter(harness.client.data[FINDINGS_COLLECTION].values()))
    assert stored["verification_status"] == "verified"


def test_a_single_quote_finding_is_refused(tmp_path: Path) -> None:
    harness = Harness(tmp_path)
    finding = harness.finding(
        quotes=[
            CitedQuote(text="Requirement alpha", page_number=1, document_path=str(harness.spec))
        ]
    )
    result = harness.tools.persist_finding(finding)
    assert result["persisted"] is False
    assert result["reason"] == "finding_must_have_exactly_two_quotes"
    assert harness.client.data == {}


def test_three_quotes_are_refused(tmp_path: Path) -> None:
    harness = Harness(tmp_path)
    base = harness.finding()
    finding = base.model_copy(update={"quotes": [*base.quotes, base.quotes[0]]})
    result = harness.tools.persist_finding(finding)
    assert result["persisted"] is False
    assert result["reason"] == "finding_must_have_exactly_two_quotes"
    assert harness.client.data == {}


def test_citing_the_spec_twice_is_refused(tmp_path: Path) -> None:
    """Two verifying spec quotes must not stand in for cut-sheet evidence."""
    harness = Harness(tmp_path)
    finding = harness.finding(
        quotes=[
            CitedQuote(text="Requirement alpha", page_number=1, document_path=str(harness.spec)),
            CitedQuote(
                text="Second governing page.", page_number=2, document_path=str(harness.spec)
            ),
        ]
    )
    result = harness.tools.persist_finding(finding)
    assert result["persisted"] is False
    assert result["reason"] == "finding_must_cite_each_bound_document_once"
    assert harness.client.data == {}


def test_a_whitespace_quote_cannot_persist(tmp_path: Path) -> None:
    """A one-space quote passes the schema but must die at the gate."""
    harness = Harness(tmp_path)
    result = harness.tools.persist_finding(
        harness.finding(cut_quote=" ", status=VerificationStatus.VERIFIED)
    )
    assert result["persisted"] is False
    assert result["reason"] == "one_or_more_quotes_rejected_by_gate"
    assert harness.client.data == {}


def test_persisted_finding_document_carries_no_local_paths(tmp_path: Path) -> None:
    """The stored finding references documents by hash, never by path."""
    harness = Harness(tmp_path)
    result = harness.tools.persist_finding(harness.finding())
    assert result["persisted"] is True

    stored = next(iter(harness.client.data[FINDINGS_COLLECTION].values()))
    dump = json.dumps(stored, default=str)
    assert str(harness.spec) not in dump
    assert str(harness.cut_sheet) not in dump
    assert harness.spec.name not in dump
    assert harness.cut_sheet.name not in dump
