"""Runtime-level invariants: retry cap, feedback hygiene, and collection separation."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pymupdf

from specguard.agent import AuditRuntime
from specguard.models import AuditClaim, AuditClaimBatch
from specguard.tools import FINDINGS_COLLECTION, REJECTIONS_COLLECTION
from tests.adversarial.harness import CUT_LINE, CUT_SENTINEL, RUN_ID, SPEC_LINE, Harness


class ScriptedGenerator:
    """Returns pre-scripted batches and refuses turns beyond the script."""

    def __init__(self, *batches: AuditClaimBatch) -> None:
        self.batches = list(batches)
        self.messages: list[str] = []

    async def generate_claims(self, message: str) -> AuditClaimBatch:
        self.messages.append(message)
        if not self.batches:
            raise AssertionError("the runtime requested more model turns than the script allows")
        return self.batches.pop(0)

    @property
    def remaining(self) -> int:
        return len(self.batches)


def _claim(
    *,
    cut_quote: str = CUT_LINE,
    description: str = "The submitted characteristic conflicts with the requirement.",
) -> AuditClaim:
    return AuditClaim(
        claim_description=description,
        spec_quote=SPEC_LINE,
        spec_page=1,
        cut_sheet_quote=cut_quote,
        cut_sheet_page=1,
    )


def _runtime(harness: Harness, generator: ScriptedGenerator) -> AuditRuntime:
    return AuditRuntime(
        claim_generator=generator,
        tools=harness.tools,
        spec_path=harness.spec,
        cut_sheet_path=harness.cut_sheet,
        run_id=RUN_ID,
    )


def test_no_second_retry_even_when_more_model_answers_exist(tmp_path: Path) -> None:
    """The one-retry cap must bind even if the model still has a valid answer."""
    harness = Harness(tmp_path)
    generator = ScriptedGenerator(
        AuditClaimBatch(claims=[_claim(cut_quote="Invented quote one.")]),
        AuditClaimBatch(claims=[_claim(cut_quote="Invented quote two.")]),
        AuditClaimBatch(claims=[_claim()]),
    )
    summary = asyncio.run(_runtime(harness, generator).run())

    assert len(generator.messages) == 2
    assert generator.remaining == 1
    assert summary.retried == 1
    assert summary.rejected == 1
    assert summary.findings_persisted == 0
    assert FINDINGS_COLLECTION not in harness.client.data
    assert len(harness.client.data[REJECTIONS_COLLECTION]) == 1


def test_a_retried_claim_is_verified_again_before_persisting(tmp_path: Path) -> None:
    """The corrected claim must pass the gate itself; the retry grants nothing."""
    harness = Harness(tmp_path)
    generator = ScriptedGenerator(
        AuditClaimBatch(claims=[_claim(cut_quote="Invented quote one.")]),
        AuditClaimBatch(claims=[_claim(cut_quote="Invented quote two.")]),
    )
    summary = asyncio.run(_runtime(harness, generator).run())

    assert summary.retried == 1
    assert summary.findings_persisted == 0
    assert FINDINGS_COLLECTION not in harness.client.data


def test_retry_feedback_contains_only_gate_result_fields(tmp_path: Path) -> None:
    """The retry message carries gate feedback and the claim, nothing else.

    The feedback objects must expose exactly rejection_reason,
    normalized_quote, and page_count. No file name, no path, and no page
    text that the claim itself did not quote may leak into the retry turn.
    """
    harness = Harness(tmp_path)
    generator = ScriptedGenerator(
        AuditClaimBatch(claims=[_claim(cut_quote="Invented gamma quote.")]),
        AuditClaimBatch(claims=[]),
    )
    asyncio.run(_runtime(harness, generator).run())

    assert len(generator.messages) == 2
    retry_message = generator.messages[1]
    feedback = json.loads(retry_message.split("Gate rejection feedback:\n", 1)[1])
    assert feedback
    assert all(
        set(item) == {"rejection_reason", "normalized_quote", "page_count"} for item in feedback
    )
    assert "pdf_path" not in retry_message
    assert harness.spec.name not in retry_message
    assert harness.cut_sheet.name not in retry_message
    assert str(harness.spec) not in retry_message
    assert str(harness.cut_sheet) not in retry_message
    assert CUT_SENTINEL not in retry_message


def test_mixed_batch_keeps_findings_and_rejections_disjoint(tmp_path: Path) -> None:
    """One good and one bad claim: no double-counting, no cross-writes.

    The good claim persists through one atomic batch. The bad claim, after
    its failed retry, lands only in the rejections collection. The RFI
    renders the persisted claim only.
    """
    harness = Harness(tmp_path)
    good = _claim()
    bad = _claim(
        cut_quote="Invented gamma quote.",
        description="This claim never earns a verified quote.",
    )
    generator = ScriptedGenerator(
        AuditClaimBatch(claims=[good, bad]),
        AuditClaimBatch(
            claims=[_claim(cut_quote="Invented delta quote.", description=bad.claim_description)]
        ),
    )
    summary = asyncio.run(_runtime(harness, generator).run())

    assert summary.claims_made == 2
    assert summary.verified == 1
    assert summary.rejected == 1
    assert summary.retried == 1
    assert summary.findings_persisted == 1

    findings = harness.client.data[FINDINGS_COLLECTION]
    rejections = harness.client.data[REJECTIONS_COLLECTION]
    assert len(findings) == 1
    assert len(rejections) == 1
    finding_claims = {stored["claim_text"] for stored in findings.values()}
    rejection_claims = {stored["claim_text"] for stored in rejections.values()}
    assert finding_claims.isdisjoint(rejection_claims)

    assert len(harness.client.batches) == 1
    assert harness.client.batches[0].committed is True

    with pymupdf.open(summary.rfi_path) as document:
        rfi_text = "\n".join(page.get_text() for page in document)
    assert CUT_LINE in rfi_text
    assert "Invented gamma quote." not in rfi_text
    assert "Invented delta quote." not in rfi_text
    assert bad.claim_description not in rfi_text
