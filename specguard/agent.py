"""One ADK agent and the bounded audit loop around its four tools."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Protocol

from google.adk.agents import LlmAgent
from google.adk.models.google_llm import Gemini
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from specguard import gate
from specguard.models import (
    AuditClaim,
    AuditClaimBatch,
    AuditRunSummary,
    CitedQuote,
    Finding,
    PersistedFinding,
)
from specguard.tools import AuditTools

MODEL_ID = "gemini-3.7-flash"
MODEL_LOCATION = "global"
APP_NAME = "specguard"
PROMPT_PATH = Path(__file__).parent / "prompts" / "audit_claims_v1.txt"


class ClaimGenerator(Protocol):
    """The model surface used by the deterministic audit loop."""

    async def generate_claims(self, message: str) -> AuditClaimBatch:
        """Return structured discrepancy claims for one model turn."""


def load_audit_prompt() -> str:
    """Load the versioned, fixture-neutral audit instruction."""
    return PROMPT_PATH.read_text(encoding="utf-8")


def create_adk_agent(
    tools: AuditTools,
    *,
    project_id: str,
    model_id: str = MODEL_ID,
    location: str = MODEL_LOCATION,
) -> LlmAgent:
    """Build the single ADK agent with exactly the four required tools."""
    model = Gemini(
        model=model_id,
        client_kwargs={
            "vertexai": True,
            "project": project_id,
            "location": location,
        },
    )
    return LlmAgent(
        name="specguard_auditor",
        description="Audits one submitted technical document against one specification.",
        model=model,
        instruction=load_audit_prompt(),
        tools=[
            tools.extract_pdf_text,
            tools.verify_quote,
            tools.persist_finding,
            tools.draft_rfi,
        ],
        output_schema=AuditClaimBatch,
        generate_content_config=types.GenerateContentConfig(temperature=0),
    )


class AdkClaimGenerator:
    """Run structured-output turns through ADK and the Google GenAI SDK."""

    def __init__(self, agent: LlmAgent, *, run_id: str) -> None:
        self._session_service = InMemorySessionService()
        self._runner = Runner(
            app_name=APP_NAME,
            agent=agent,
            session_service=self._session_service,
        )
        self._run_id = run_id
        self._user_id = "local-auditor"
        self._session_created = False

    async def generate_claims(self, message: str) -> AuditClaimBatch:
        if not self._session_created:
            await self._session_service.create_session(
                app_name=APP_NAME,
                user_id=self._user_id,
                session_id=self._run_id,
            )
            self._session_created = True

        content = types.Content(role="user", parts=[types.Part.from_text(text=message)])
        final_text: str | None = None
        async for event in self._runner.run_async(
            user_id=self._user_id,
            session_id=self._run_id,
            new_message=content,
        ):
            if not event.is_final_response() or not event.content:
                continue
            text_parts = [part.text for part in event.content.parts or [] if part.text]
            if text_parts:
                final_text = "".join(text_parts)

        if final_text is None:
            raise RuntimeError("the ADK agent returned no final structured response")
        return AuditClaimBatch.model_validate_json(final_text)


class AuditRuntime:
    """Apply one verification retry per claim, then persist only through the gate."""

    def __init__(
        self,
        *,
        claim_generator: ClaimGenerator,
        tools: AuditTools,
        spec_path: str | Path,
        cut_sheet_path: str | Path,
        run_id: str,
    ) -> None:
        self._claim_generator = claim_generator
        self._tools = tools
        self._spec_path = Path(spec_path).resolve(strict=True)
        self._cut_sheet_path = Path(cut_sheet_path).resolve(strict=True)
        self._run_id = run_id

    async def run(self) -> AuditRunSummary:
        initial_message = self._build_document_message()
        initial_batch = await self._claim_generator.generate_claims(initial_message)
        persisted_findings: list[PersistedFinding] = []
        retried = 0
        rejected = 0

        for claim in initial_batch.claims:
            current_claim = claim
            verification_results = self._verify_claim(current_claim)
            failed = [result for result in verification_results if not result["verified"]]

            if failed:
                retried += 1
                retry_batch = await self._claim_generator.generate_claims(
                    self._build_retry_message(current_claim, failed)
                )
                if len(retry_batch.claims) != 1:
                    rejected += 1
                    self._tools.record_rejection(
                        current_claim.claim_description,
                        f"retry_returned_{len(retry_batch.claims)}_claims",
                    )
                    continue
                current_claim = retry_batch.claims[0]
                verification_results = self._verify_claim(current_claim)
                failed = [result for result in verification_results if not result["verified"]]
                if failed:
                    rejected += 1
                    self._tools.record_rejection(
                        current_claim.claim_description,
                        _final_rejection_reason(failed),
                    )
                    continue

            finding = self._claim_to_finding(current_claim)
            persistence_result = self._tools.persist_finding(finding)
            if not persistence_result.get("persisted"):
                rejected += 1
                self._tools.record_rejection(
                    current_claim.claim_description,
                    str(persistence_result.get("reason", "persistence_refused_finding")),
                )
                continue
            persisted_findings.append(
                PersistedFinding.model_validate(persistence_result["finding"])
            )

        rfi_result = self._tools.draft_rfi(persisted_findings)
        return AuditRunSummary(
            run_id=self._run_id,
            claims_made=len(initial_batch.claims),
            verified=len(persisted_findings),
            rejected=rejected,
            retried=retried,
            findings_persisted=len(persisted_findings),
            rfi_path=rfi_result["rfi_path"],
        )

    def _build_document_message(self) -> str:
        specification = self._extract_document(self._spec_path, "SPECIFICATION")
        submitted = self._extract_document(self._cut_sheet_path, "SUBMITTED DOCUMENT")
        return (
            "Audit the two extracted documents below. "
            "The labels and page markers are context, not source text.\n\n"
            f"{specification}\n\n{submitted}"
        )

    def _extract_document(self, path: Path, label: str) -> str:
        page_count = gate.build_document_record(path).page_count
        pages: list[str] = []
        for page_number in range(1, page_count + 1):
            result = self._tools.extract_pdf_text(str(path), page_number)
            if not result["ok"]:
                raise RuntimeError(result["error_message"])
            pages.append(f"--- {label} PAGE {page_number} ---\n{result['text']}")
        return "\n".join(pages)

    def _verify_claim(self, claim: AuditClaim) -> list[dict[str, object]]:
        return [
            self._tools.verify_quote(claim.spec_quote, claim.spec_page, str(self._spec_path)),
            self._tools.verify_quote(
                claim.cut_sheet_quote,
                claim.cut_sheet_page,
                str(self._cut_sheet_path),
            ),
        ]

    def _claim_to_finding(self, claim: AuditClaim) -> Finding:
        return Finding(
            submittal_id=self._run_id,
            spec_locator=f"Page {claim.spec_page}",
            cut_sheet_locator=f"Page {claim.cut_sheet_page}",
            claim_text=claim.claim_description,
            quotes=[
                CitedQuote(
                    text=claim.spec_quote,
                    page_number=claim.spec_page,
                    document_path=str(self._spec_path),
                ),
                CitedQuote(
                    text=claim.cut_sheet_quote,
                    page_number=claim.cut_sheet_page,
                    document_path=str(self._cut_sheet_path),
                ),
            ],
        )

    @staticmethod
    def _build_retry_message(claim: AuditClaim, failed_results: list[dict[str, object]]) -> str:
        rejection_feedback = [
            {
                "rejection_reason": result["rejection_reason"],
                "normalized_quote": result["normalized_quote"],
                "page_count": result["page_count"],
            }
            for result in failed_results
        ]
        return (
            "The runtime rejected one or more quoted text anchors from this claim. "
            "Correct this claim once using the documents already in this conversation. "
            "Return exactly one corrected claim if the direct conflict remains supported. "
            "Return an empty claims list if it does not. Do not add a different claim.\n\n"
            f"Original claim:\n{claim.model_dump_json()}\n\n"
            f"Gate rejection feedback:\n{json.dumps(rejection_feedback, ensure_ascii=False)}"
        )


def _final_rejection_reason(failed_results: list[dict[str, object]]) -> str:
    details = [
        {
            "rejection_reason": result["rejection_reason"],
            "normalized_quote": result["normalized_quote"],
            "page_count": result["page_count"],
        }
        for result in failed_results
    ]
    return json.dumps(details, ensure_ascii=False, sort_keys=True)
