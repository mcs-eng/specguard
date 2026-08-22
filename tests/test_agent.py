"""Network-free tests for the bounded ADK audit orchestration."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

from google.genai import types

from specguard.agent import AdkClaimGenerator, AuditRuntime
from specguard.models import AuditClaim, AuditClaimBatch
from specguard.tools import FINDINGS_COLLECTION, REJECTIONS_COLLECTION, AuditTools
from tests.fake_firestore import FakeFirestoreClient
from tests.fixtures_pdf import write_pdf


class FakeClaimGenerator:
    def __init__(self, *responses: object) -> None:
        self._responses = list(responses)
        self.messages: list[str] = []

    async def generate_claims(self, message: str) -> AuditClaimBatch:
        self.messages.append(message)
        if not self._responses:
            raise AssertionError("the runtime exceeded the configured model-turn cap")
        response = self._responses.pop(0)
        if isinstance(response, RuntimeError):
            raise response
        return AuditClaimBatch.model_validate(response)


def _claim(
    *,
    spec_quote: str = "The required characteristic is alpha.",
    cut_sheet_quote: str = "The submitted characteristic is beta.",
    description: str = "The submitted characteristic conflicts with the requirement.",
) -> AuditClaim:
    return AuditClaim(
        claim_description=description,
        spec_quote=spec_quote,
        spec_page=1,
        cut_sheet_quote=cut_sheet_quote,
        cut_sheet_page=1,
    )


def _runtime(
    tmp_path: Path,
    generator: FakeClaimGenerator,
) -> tuple[AuditRuntime, FakeFirestoreClient, Path, Path]:
    spec = write_pdf(
        tmp_path / "governing.pdf",
        [["The required characteristic is alpha."], ["Second specification page."]],
    )
    cut_sheet = write_pdf(
        tmp_path / "submitted.pdf",
        [["The submitted characteristic is beta."], ["Second submitted page."]],
    )
    client = FakeFirestoreClient()
    tools = AuditTools(
        firestore_client=client,
        spec_path=spec,
        cut_sheet_path=cut_sheet,
        run_id="audit-run-1",
        output_directory=tmp_path / "artifacts",
    )
    runtime = AuditRuntime(
        claim_generator=generator,
        tools=tools,
        spec_path=spec,
        cut_sheet_path=cut_sheet,
        run_id="audit-run-1",
    )
    return runtime, client, spec, cut_sheet


def test_runtime_uses_all_four_tools_and_persists_a_verified_claim(tmp_path: Path) -> None:
    generator = FakeClaimGenerator(AuditClaimBatch(claims=[_claim()]))
    runtime, client, spec, cut_sheet = _runtime(tmp_path, generator)

    summary = asyncio.run(runtime.run())

    assert summary.claims_made == 1
    assert summary.verified == 1
    assert summary.rejected == 0
    assert summary.retried == 0
    assert summary.findings_persisted == 1
    assert len(client.data[FINDINGS_COLLECTION]) == 1
    assert Path(summary.rfi_path).is_file()
    assert len(generator.messages) == 1
    message = generator.messages[0]
    assert "--- SPECIFICATION PAGE 1 ---" in message
    assert "--- SPECIFICATION PAGE 2 ---" in message
    assert "--- SUBMITTED DOCUMENT PAGE 1 ---" in message
    assert "--- SUBMITTED DOCUMENT PAGE 2 ---" in message
    assert spec.name not in message
    assert cut_sheet.name not in message
    assert str(spec) not in message
    assert str(cut_sheet) not in message


def test_rejected_claim_gets_exactly_one_retry_then_rejection(tmp_path: Path) -> None:
    invalid_initial = _claim(cut_sheet_quote="Invented quote one.")
    invalid_retry = _claim(cut_sheet_quote="Invented quote two.")
    generator = FakeClaimGenerator(
        AuditClaimBatch(claims=[invalid_initial]),
        AuditClaimBatch(claims=[invalid_retry]),
    )
    runtime, client, _, _ = _runtime(tmp_path, generator)

    summary = asyncio.run(runtime.run())

    assert len(generator.messages) == 2
    assert summary.claims_made == 1
    assert summary.retried == 1
    assert summary.verified == 0
    assert summary.rejected == 1
    assert summary.findings_persisted == 0
    assert FINDINGS_COLLECTION not in client.data
    assert len(client.data[REJECTIONS_COLLECTION]) == 1
    retry_message = generator.messages[1]
    assert '"rejection_reason": "quote_not_found_on_cited_page"' in retry_message
    assert '"normalized_quote": "invented quote one."' in retry_message
    assert '"page_count": 2' in retry_message


def test_one_retry_can_correct_the_quote_and_persist(tmp_path: Path) -> None:
    generator = FakeClaimGenerator(
        AuditClaimBatch(claims=[_claim(cut_sheet_quote="Invented quote.")]),
        AuditClaimBatch(claims=[_claim()]),
    )
    runtime, client, _, _ = _runtime(tmp_path, generator)

    summary = asyncio.run(runtime.run())

    assert len(generator.messages) == 2
    assert summary.retried == 1
    assert summary.verified == 1
    assert summary.rejected == 0
    assert summary.findings_persisted == 1
    assert len(client.data[FINDINGS_COLLECTION]) == 1
    assert REJECTIONS_COLLECTION not in client.data


def test_retry_must_return_exactly_one_corrected_claim(tmp_path: Path) -> None:
    generator = FakeClaimGenerator(
        AuditClaimBatch(claims=[_claim(cut_sheet_quote="Invented quote.")]),
        AuditClaimBatch(claims=[]),
    )
    runtime, client, _, _ = _runtime(tmp_path, generator)

    summary = asyncio.run(runtime.run())

    assert len(generator.messages) == 2
    assert summary.retried == 1
    assert summary.rejected == 1
    rejection = next(iter(client.data[REJECTIONS_COLLECTION].values()))
    assert rejection["reason"] == "retry_returned_0_claims"


def test_empty_model_output_persists_no_findings_and_drafts_no_rfi(tmp_path: Path) -> None:
    generator = FakeClaimGenerator(AuditClaimBatch(claims=[]))
    runtime, client, _, _ = _runtime(tmp_path, generator)

    summary = asyncio.run(runtime.run())

    assert summary.claims_made == 0
    assert summary.verified == 0
    assert summary.rejected == 0
    assert summary.retried == 0
    assert summary.findings_persisted == 0
    assert summary.rfi_path is None
    assert client.data == {}


def test_invalid_initial_model_output_records_rejection_and_drafts_no_rfi(
    tmp_path: Path,
) -> None:
    generator = FakeClaimGenerator(
        {"claims": [{"claim_description": "Incomplete structured output."}]}
    )
    runtime, client, _, _ = _runtime(tmp_path, generator)

    summary = asyncio.run(runtime.run())

    assert len(generator.messages) == 1
    assert summary.claims_made == 0
    assert summary.rejected == 1
    assert summary.retried == 0
    assert summary.findings_persisted == 0
    assert FINDINGS_COLLECTION not in client.data
    rejection = next(iter(client.data[REJECTIONS_COLLECTION].values()))
    assert rejection["claim_text"] == "Initial model output"
    assert rejection["reason"] == "model_output_invalid"
    assert summary.rfi_path is None


def test_invalid_retry_model_output_rejects_that_claim_and_continues(tmp_path: Path) -> None:
    invalid_claim = _claim(
        cut_sheet_quote="Invented quote.",
        description="The first claim has an invalid quote.",
    )
    valid_claim = _claim(description="The second claim has valid source quotes.")
    generator = FakeClaimGenerator(
        AuditClaimBatch(claims=[invalid_claim, valid_claim]),
        {"claims": [{"claim_description": "Incomplete retry output."}]},
    )
    runtime, client, _, _ = _runtime(tmp_path, generator)

    summary = asyncio.run(runtime.run())

    assert len(generator.messages) == 2
    assert summary.claims_made == 2
    assert summary.rejected == 1
    assert summary.retried == 1
    assert summary.findings_persisted == 1
    assert len(client.data[FINDINGS_COLLECTION]) == 1
    rejection = next(iter(client.data[REJECTIONS_COLLECTION].values()))
    assert rejection["claim_text"] == invalid_claim.claim_description
    assert rejection["reason"] == "model_output_invalid"


def test_runtime_records_usage_unavailability_without_an_adk_generator(tmp_path: Path) -> None:
    generator = FakeClaimGenerator(AuditClaimBatch(claims=[]))
    runtime, _, _, _ = _runtime(tmp_path, generator)

    summary = asyncio.run(runtime.run())

    assert summary.audit_model_usage is not None
    assert (
        summary.audit_model_usage.unavailable_reason
        == "The audit claim generator did not expose token usage metadata."
    )


def test_adk_claim_generator_records_sdk_usage_metadata() -> None:
    class FakeRunner:
        async def run_async(self, **_: object):
            yield SimpleNamespace(
                usage_metadata=SimpleNamespace(
                    prompt_token_count=101,
                    candidates_token_count=17,
                    total_token_count=118,
                ),
                content=types.Content(
                    role="model", parts=[types.Part.from_text(text='{"claims": []}')]
                ),
                is_final_response=lambda: True,
            )

    generator = object.__new__(AdkClaimGenerator)
    generator._runner = FakeRunner()
    generator._run_id = "audit-run-1"
    generator._user_id = "local-auditor"
    generator._session_created = True
    generator._prompt_tokens = 0
    generator._output_tokens = 0
    generator._total_tokens = 0
    generator._usage_seen = False

    batch = asyncio.run(generator.generate_claims("Audit this document."))
    usage = generator.audit_model_usage()

    assert batch.claims == []
    assert usage.prompt_tokens == 101
    assert usage.output_tokens == 17
    assert usage.total_tokens == 118
    assert usage.unavailable_reason is None
