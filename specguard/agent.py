"""One ADK agent and the bounded audit loop around its five tools."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Protocol

from google.adk.agents import LlmAgent
from google.adk.models.google_llm import Gemini
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types
from pydantic import ValidationError

from specguard import gate, integrity
from specguard.models import (
    AgentMode,
    AuditClaim,
    AuditClaimBatch,
    AuditModelUsage,
    AuditRunSummary,
    CitedQuote,
    DocumentRole,
    Finding,
    ModelToolCall,
    PersistedFinding,
    QuarantinedDocument,
    RunQuarantine,
    Severity,
)
from specguard.tools import AuditTools

MODEL_ID = "gemini-3.7-flash"
MODEL_LOCATION = "global"
APP_NAME = "specguard"
MODEL_OUTPUT_INVALID_REASON = "model_output_invalid"

#: The environment variable that selects the agent mode for a deployment.
AGENT_MODE_ENVIRONMENT_VARIABLE = "SPECGUARD_AGENT_MODE"

_PROMPT_DIRECTORY = Path(__file__).parent / "prompts"

#: One versioned instruction file per agent mode.
PROMPT_PATHS: dict[AgentMode, Path] = {
    AgentMode.FULL_TEXT: _PROMPT_DIRECTORY / "audit_claims_v1.txt",
    AgentMode.NAVIGATE: _PROMPT_DIRECTORY / "audit_claims_navigate_v1.txt",
}

#: The default-mode instruction file, under the name it has always had.
PROMPT_PATH = PROMPT_PATHS[AgentMode.FULL_TEXT]

#: How much of each specification page the navigate-mode index carries. The
#: index exists to tell the model which page to open, not to answer for it.
PAGE_INDEX_CHARACTERS = 160


class ClaimGenerator(Protocol):
    """The model surface used by the deterministic audit loop."""

    async def generate_claims(self, message: str) -> AuditClaimBatch:
        """Return structured discrepancy claims for one model turn."""


def resolve_agent_mode(value: str | None = None) -> AgentMode:
    """Resolve the configured agent mode, defaulting to ``full_text``.

    An unset or empty setting is ``full_text``. An unrecognized setting raises
    instead of falling back, because a mode that quietly reverted would make
    every number measured under it describe the other mode.
    """
    configured = value if value is not None else os.environ.get(AGENT_MODE_ENVIRONMENT_VARIABLE)
    if not configured:
        return AgentMode.FULL_TEXT
    try:
        return AgentMode(configured)
    except ValueError:
        raise ValueError(
            f"{AGENT_MODE_ENVIRONMENT_VARIABLE} must be one of "
            f"{', '.join(mode.value for mode in AgentMode)}, not {configured!r}"
        ) from None


def load_audit_prompt(agent_mode: AgentMode = AgentMode.FULL_TEXT) -> str:
    """Load the versioned, fixture-neutral audit instruction for one mode."""
    return PROMPT_PATHS[agent_mode].read_text(encoding="utf-8")


def registered_tools(tools: AuditTools, agent_mode: AgentMode) -> list[Any]:
    """Return the tool list one mode registers to the model.

    ``full_text`` registers all five tools, unchanged. ``navigate`` registers
    only the read-only pair on
    :class:`specguard.tools.ModelFacingAuditTools`. A tool-using model that can
    reach ``persist_finding`` or ``draft_rfi`` can write before the runtime has
    verified anything, and no runtime path wants a model-initiated write or
    RFI. The agent reads; the runtime writes.
    """
    if agent_mode is AgentMode.NAVIGATE:
        model_facing = tools.model_facing_tools()
        return [model_facing.extract_pdf_text, model_facing.verify_quote]
    return [
        tools.check_text_integrity,
        tools.extract_pdf_text,
        tools.verify_quote,
        tools.persist_finding,
        tools.draft_rfi,
    ]


def create_adk_agent(
    tools: AuditTools,
    *,
    project_id: str,
    model_id: str = MODEL_ID,
    location: str = MODEL_LOCATION,
    agent_mode: AgentMode = AgentMode.FULL_TEXT,
) -> LlmAgent:
    """Build the single ADK agent with the tools and prompt its mode selects."""
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
        instruction=load_audit_prompt(agent_mode),
        tools=registered_tools(tools, agent_mode),
        output_schema=AuditClaimBatch,
        generate_content_config=types.GenerateContentConfig(temperature=0),
    )


class AdkClaimGenerator:
    """Run structured-output turns through ADK and the Google GenAI SDK.

    Besides the turn itself, this class keeps the receipt for the agentic
    claim: every function call ADK reports the model initiating, in the order
    it made them, with a bounded reading of the answer each one got. The
    recording state below is declared at class level and rebound rather than
    mutated, so it is immutable, unshared between instances, and readable on an
    instance that was built without ``__init__``.
    """

    #: Model-initiated calls recorded so far, oldest first.
    _model_tool_calls: tuple[ModelToolCall, ...] = ()

    #: The ADK function-call identifier for each entry above, by position.
    #: A model that supplies no identifier leaves ``None`` here.
    _recorded_call_ids: tuple[str | None, ...] = ()

    #: Zero-based index of the runtime turn currently running.
    _turn_index: int = 0

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
        self._prompt_tokens: int | None = 0
        self._output_tokens: int | None = 0
        self._total_tokens: int | None = 0
        self._usage_seen = False

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
        try:
            async for event in self._runner.run_async(
                user_id=self._user_id,
                session_id=self._run_id,
                new_message=content,
            ):
                self._record_usage(getattr(event, "usage_metadata", None))
                self._record_tool_calls(event)
                self._record_tool_responses(event)
                if not event.is_final_response() or not event.content:
                    continue
                text_parts = [part.text for part in event.content.parts or [] if part.text]
                if text_parts:
                    final_text = "".join(text_parts)
        finally:
            # The turn advances even when it failed, so a call recorded on a
            # later turn is never filed under an earlier one.
            self._turn_index = self._turn_index + 1

        if final_text is None:
            raise RuntimeError("the ADK agent returned no final structured response")
        return AuditClaimBatch.model_validate_json(final_text)

    def model_tool_calls(self) -> list[ModelToolCall]:
        """Return every model-initiated call recorded so far, in call order."""
        return list(self._model_tool_calls)

    def _record_tool_calls(self, event: Any) -> None:
        """Record each function call this event reports the model initiating.

        Only the tool name and the two bounded locating arguments are kept. The
        quote a model sent to the checking tool is not recorded, because the
        receipt exists to show which pages were opened, not to hold a second
        copy of the text.
        """
        get_function_calls = getattr(event, "get_function_calls", None)
        if not callable(get_function_calls):
            return
        for function_call in get_function_calls():
            arguments = getattr(function_call, "args", None) or {}
            recorded = ModelToolCall(
                turn_index=self._turn_index,
                tool_name=getattr(function_call, "name", None) or "unnamed_tool",
                document_role=_recorded_document_role(arguments.get("document_role")),
                page_number=_recorded_page_number(arguments.get("page_number")),
            )
            self._model_tool_calls = (*self._model_tool_calls, recorded)
            self._recorded_call_ids = (
                *self._recorded_call_ids,
                getattr(function_call, "id", None),
            )

    def _record_tool_responses(self, event: Any) -> None:
        """Attach each tool answer in this event to the call it answers."""
        get_function_responses = getattr(event, "get_function_responses", None)
        if not callable(get_function_responses):
            return
        for function_response in get_function_responses():
            self._attach_tool_response(function_response)

    def _attach_tool_response(self, function_response: Any) -> None:
        """Record the bounded outcome fields of one tool answer."""
        slot = self._slot_for_response(function_response)
        if slot is None:
            return
        response = getattr(function_response, "response", None)
        if not isinstance(response, dict):
            return
        verified = response.get("verified")
        error_code = response.get("error_code")
        if verified is None and error_code is None:
            return
        calls = list(self._model_tool_calls)
        calls[slot] = calls[slot].model_copy(
            update={
                "response_verified": verified if isinstance(verified, bool) else None,
                "response_error_code": None if error_code is None else str(error_code),
            }
        )
        self._model_tool_calls = tuple(calls)

    def _slot_for_response(self, function_response: Any) -> int | None:
        """Find the recorded call one answer belongs to.

        ADK pairs an answer to its call by identifier. A model that supplies no
        identifier falls back to the most recent recorded call of the same name
        that carries no answer yet.
        """
        call_id = getattr(function_response, "id", None)
        name = getattr(function_response, "name", None)
        if call_id is not None:
            return next(
                (
                    slot
                    for slot, recorded_id in enumerate(self._recorded_call_ids)
                    if recorded_id == call_id
                ),
                None,
            )
        return next(
            (
                slot
                for slot in reversed(range(len(self._model_tool_calls)))
                if self._model_tool_calls[slot].tool_name == name
                and self._model_tool_calls[slot].response_verified is None
                and self._model_tool_calls[slot].response_error_code is None
            ),
            None,
        )

    def audit_model_usage(self) -> AuditModelUsage:
        """Return exact ADK usage counts, or record that this path exposed none."""
        if not self._usage_seen or all(
            count is None
            for count in (self._prompt_tokens, self._output_tokens, self._total_tokens)
        ):
            return AuditModelUsage(
                unavailable_reason="The ADK audit call path did not expose token usage metadata."
            )
        return AuditModelUsage(
            prompt_tokens=self._prompt_tokens,
            output_tokens=self._output_tokens,
            total_tokens=self._total_tokens,
        )

    def _record_usage(self, usage_metadata: Any) -> None:
        """Accumulate SDK-provided counts without estimating missing values."""
        if usage_metadata is None:
            return
        self._usage_seen = True
        self._prompt_tokens = _add_usage_count(
            self._prompt_tokens, getattr(usage_metadata, "prompt_token_count", None)
        )
        self._output_tokens = _add_usage_count(
            self._output_tokens, getattr(usage_metadata, "candidates_token_count", None)
        )
        self._total_tokens = _add_usage_count(
            self._total_tokens, getattr(usage_metadata, "total_token_count", None)
        )


def _add_usage_count(current: int | None, reported: Any) -> int | None:
    """Add one SDK count while preserving an unavailable field as ``None``."""
    if current is None or reported is None:
        return None
    return current + int(reported)


def _recorded_document_role(value: Any) -> DocumentRole | None:
    """Read a bound role out of a model argument, or record none."""
    try:
        return DocumentRole(value)
    except ValueError:
        return None


def _recorded_page_number(value: Any) -> int | None:
    """Record the page a model asked for, exactly as it supplied it."""
    if isinstance(value, bool) or not isinstance(value, int | float | str):
        return None
    try:
        return int(value)
    except ValueError:
        return None


class AuditRuntime:
    """Screen both documents, then apply one verification retry per claim.

    The runtime disposes; the agent proposes. Two deterministic controls sit
    around the model and neither is optional for any caller of :meth:`run`:

    1. The text-layer integrity screen runs first, on both bound documents,
       before any extracted text is assembled into a model message. A flagged
       document quarantines the run: no model call is made, the runtime
       attempts one deterministic integrity record per flagged document, and
       the summary discloses the quarantine together with any refusal to write
       that record. :meth:`run` takes no argument that can skip this screen.
       The runtime and its tools must be bound to the same two documents, and
       the runtime refuses to send text from a document that changed after the
       screen read it.
    2. The verification gate runs on every quoted anchor, and again at write
       time inside the persistence tool.

    ``agent_mode`` changes only what the model is shown and which tools it may
    call. Both controls above run identically in either mode, and in
    ``navigate`` mode the runtime still owns every write: the model is
    registered no tool that can write.
    """

    def __init__(
        self,
        *,
        claim_generator: ClaimGenerator,
        tools: AuditTools,
        spec_path: str | Path,
        cut_sheet_path: str | Path,
        run_id: str,
        severity_classifier: Any = None,
        project_id: str | None = None,
        agent_mode: AgentMode = AgentMode.FULL_TEXT,
    ) -> None:
        self._claim_generator = claim_generator
        self._tools = tools
        self._spec_path = Path(spec_path).resolve(strict=True)
        self._cut_sheet_path = Path(cut_sheet_path).resolve(strict=True)
        self._run_id = run_id
        self._severity_classifier = severity_classifier
        self._project_id = project_id
        self._agent_mode = agent_mode
        if self._spec_path != tools.spec_path or self._cut_sheet_path != tools.cut_sheet_path:
            raise ValueError("the runtime and its tools must be bound to the same two documents")
        self._screened_hashes: dict[Path, str] = {}

    async def run(self) -> AuditRunSummary:
        quarantine = self._screen_documents()
        if quarantine is not None:
            return AuditRunSummary(
                run_id=self._run_id,
                claims_made=0,
                rejected=0,
                retried=0,
                findings_persisted=0,
                rfi_path=None,
                quarantine=quarantine,
                audit_model_usage=AuditModelUsage(
                    unavailable_reason=(
                        "No audit model call was made because the integrity screen "
                        "quarantined this run."
                    )
                ),
                agent_mode=self._agent_mode,
            )

        initial_message = self._build_model_message()
        try:
            initial_batch = await self._claim_generator.generate_claims(initial_message)
        except (ValidationError, RuntimeError):
            self._tools.record_rejection("Initial model output", MODEL_OUTPUT_INVALID_REASON)
            return AuditRunSummary(
                run_id=self._run_id,
                claims_made=0,
                rejected=1,
                retried=0,
                findings_persisted=0,
                rfi_path=None,
                audit_model_usage=self._audit_model_usage(),
                agent_mode=self._agent_mode,
                model_tool_calls=self._recorded_model_tool_calls(),
            )

        persisted_findings: list[PersistedFinding] = []
        severity_results: list[Any] = []
        retried = 0
        rejected = 0

        for claim in initial_batch.claims:
            current_claim = claim
            verification_results = self._verify_claim(current_claim)
            failed = [result for result in verification_results if not result["verified"]]

            if failed:
                retried += 1
                try:
                    retry_batch = await self._claim_generator.generate_claims(
                        self._build_retry_message(current_claim, failed)
                    )
                except (ValidationError, RuntimeError):
                    rejected += 1
                    self._tools.record_rejection(
                        current_claim.claim_description,
                        MODEL_OUTPUT_INVALID_REASON,
                    )
                    continue
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
            persisted = PersistedFinding.model_validate(persistence_result["finding"])
            persisted, sev_result = self._annotate_severity(persisted)
            persisted_findings.append(persisted)
            severity_results.append(sev_result)

        severity_status: str | None = None
        severity_reason: str | None = None
        if persisted_findings:
            if all(r.status == "classified" for r in severity_results):
                severity_status = "classified"
            else:
                severity_status = "fallback"
                reasons = [r.reason for r in severity_results if getattr(r, "reason", None)]
                severity_reason = reasons[0] if reasons else "Classification fallback"

        return AuditRunSummary(
            run_id=self._run_id,
            claims_made=len(initial_batch.claims),
            rejected=rejected,
            retried=retried,
            findings_persisted=len(persisted_findings),
            rfi_path=(
                self._draft_rfi_and_resolve_path(persisted_findings) if persisted_findings else None
            ),
            severity_status=severity_status,
            severity_reason=severity_reason,
            audit_model_usage=self._audit_model_usage(),
            agent_mode=self._agent_mode,
            model_tool_calls=self._recorded_model_tool_calls(),
        )

    def _audit_model_usage(self) -> AuditModelUsage:
        """Return the generator's exact usage record or disclose its absence."""
        reported_usage = getattr(self._claim_generator, "audit_model_usage", None)
        if callable(reported_usage):
            usage = reported_usage()
            if isinstance(usage, AuditModelUsage):
                return usage
        return AuditModelUsage(
            unavailable_reason="The audit claim generator did not expose token usage metadata."
        )

    def _recorded_model_tool_calls(self) -> list[ModelToolCall]:
        """Return the generator's recorded model-initiated calls.

        A generator that records none reports an empty list, which is the true
        answer for ``full_text`` mode: the model is sent every page and calls
        nothing.
        """
        recorded = getattr(self._claim_generator, "model_tool_calls", None)
        if not callable(recorded):
            return []
        calls = recorded()
        if not isinstance(calls, list):
            return []
        return [call for call in calls if isinstance(call, ModelToolCall)]

    def _draft_rfi_and_resolve_path(self, findings: list[PersistedFinding]) -> str:
        """Draft the RFI, then resolve its path off the model-facing channel.

        ``draft_rfi`` returns an opaque identifier because it is model-callable.
        The deterministic runtime exchanges that identifier for the ephemeral
        path through ``AuditTools.rfi_path_for``, which is not a registered
        tool. An identifier the bound tool set cannot resolve is a runtime
        defect, not a model outcome, so it raises rather than reporting a run
        that quietly lost its artifact.
        """
        rfi_result = self._tools.draft_rfi(findings)
        rfi_id = str(rfi_result["rfi_id"])
        rfi_path = self._tools.rfi_path_for(rfi_id)
        if rfi_path is None:
            raise RuntimeError("the bound tool set did not issue the returned RFI identifier")
        return str(rfi_path)

    def _annotate_severity(self, finding: PersistedFinding) -> tuple[PersistedFinding, Any]:
        """Annotate a persisted finding with Gemma severity classification.

        Severity is an annotation on a verified finding. It runs ONLY on
        findings that already passed the gate and were persisted to the ledger.
        It never touches verification status or rejection reason. If Gemma
        fails, UNCLASSIFIED is retained and the audit still completes.
        """
        try:
            from specguard.severity import SeverityResult, classify_severity

            result = classify_severity(
                finding,
                classifier=self._severity_classifier,
                project_id=self._project_id,
            )
            self._tools.update_finding_severity(
                finding.finding_id,
                result.severity,
                model_id=result.model_id,
                endpoint_label=getattr(result, "endpoint_label", None),
                status=result.status,
                reason=result.reason,
            )
            updated = finding.model_copy(
                update={
                    "severity": result.severity,
                    "severity_model_id": result.model_id,
                    "severity_endpoint_label": getattr(result, "endpoint_label", None),
                    "severity_status": result.status,
                    "severity_reason": result.reason,
                }
            )
            return updated, result
        except Exception as error:
            from specguard.severity import SeverityResult

            fallback_res = SeverityResult(
                severity=Severity.UNCLASSIFIED,
                model_id=None,
                status="fallback",
                reason=f"Runtime error: {error}",
            )
            return finding, fallback_res

    def _screen_documents(self) -> RunQuarantine | None:
        """Screen both bound documents before any text can reach the model.

        Return ``None`` when both documents are clean. Otherwise persist one
        deterministic integrity record per flagged document and return the
        disclosure. Either document flagging stops the whole run, because the
        model message carries the text of both.
        """
        quarantined: list[QuarantinedDocument] = []
        self._screened_hashes = {}
        for role, path in (
            (DocumentRole.SPECIFICATION, self._spec_path),
            (DocumentRole.SUBMITTED_DOCUMENT, self._cut_sheet_path),
        ):
            report = integrity.check_text_layer(path)
            self._screened_hashes[path] = report.sha256
            if report.clean:
                continue
            result = self._tools.persist_integrity_finding(role.value)
            record = result.get("integrity_finding") if result.get("persisted") else None
            # A record whose evidence describes different bytes than the screen
            # read is not this document's record, so its identifier is not
            # attached and the mismatch is disclosed instead.
            matched = record is not None and record["document_sha256"] == report.sha256
            quarantined.append(
                QuarantinedDocument(
                    document_role=role,
                    document_sha256=report.sha256,
                    page_count=report.page_count,
                    flagged_pages=report.flagged_pages,
                    detectors=report.detectors,
                    hidden_span_count=len(report.hidden_spans),
                    integrity_finding_id=(str(record["integrity_finding_id"]) if matched else None),
                    persistence_reason=(
                        None
                        if matched
                        else str(
                            result.get("reason", "persisted_record_describes_other_bytes")
                            if record is None
                            else "persisted_record_describes_other_bytes"
                        )
                    ),
                )
            )

        if not quarantined:
            return None
        return RunQuarantine(reason=integrity.QUARANTINE_REASON, documents=quarantined)

    def _assert_documents_still_match_the_screen(self) -> None:
        """Refuse to send text that the integrity screen did not read.

        The screen and the extraction step read the file separately. Re-reading
        the hashes after extraction closes the plain case where a screened
        document is replaced before its text is assembled for the model. A
        writer that replaces a document and restores it inside this window is
        outside the guarantee, as README already records for the gate.
        """
        for path, screened_hash in self._screened_hashes.items():
            if gate.build_document_record(path).sha256 != screened_hash:
                raise RuntimeError("a source document changed after the integrity screen read it")

    def _build_model_message(self) -> str:
        """Build the initial model message this run's mode calls for."""
        if self._agent_mode is AgentMode.NAVIGATE:
            return self._build_navigate_message()
        return self._build_document_message()

    def _build_navigate_message(self) -> str:
        """Send the submitted document in full and the specification as an index.

        The submitted document is the thing under review, so the model is given
        all of it. The specification is the thing to be navigated, so the model
        is given only enough of each page to decide which pages to open, and it
        opens them through the read-only extraction tool.
        """
        index = self._build_specification_page_index()
        submitted = self._extract_document(
            self._cut_sheet_path, "SUBMITTED DOCUMENT", DocumentRole.SUBMITTED_DOCUMENT
        )
        self._assert_documents_still_match_the_screen()
        return (
            "Audit the submitted document below against the governing specification. "
            "The specification text is not in this message: read its pages with the "
            "page-reading tool. The labels and page markers are context, not source "
            f"text.\n\n{index}\n\n{submitted}"
        )

    def _build_specification_page_index(self) -> str:
        """Build the deterministic one-line-per-page index of the specification.

        Each line is the start of one page after the gate's own normalizer has
        run on it, so the index cannot disagree with the gate about what the
        page says. These reads belong to the runtime and are not charged to the
        model's page budget: choosing to build an index is not a model
        decision.
        """
        page_count = gate.build_document_record(self._spec_path).page_count
        lines = [
            f"SPECIFICATION PAGE INDEX ({page_count} pages). Each line is the start of "
            "one page, normalized. It is a navigation aid and never a source of quotes."
        ]
        for page_number in range(1, page_count + 1):
            result = self._tools.extract_pdf_text(DocumentRole.SPECIFICATION.value, page_number)
            if not result["ok"]:
                raise RuntimeError(
                    f"SPECIFICATION page {page_number} could not be extracted: "
                    f"{result.get('error_code')}"
                )
            opening = gate.normalize(result["text"] or "")[:PAGE_INDEX_CHARACTERS]
            lines.append(f"page {page_number}: {opening}")
        return "\n".join(lines)

    def _build_document_message(self) -> str:
        specification = self._extract_document(
            self._spec_path, "SPECIFICATION", DocumentRole.SPECIFICATION
        )
        submitted = self._extract_document(
            self._cut_sheet_path, "SUBMITTED DOCUMENT", DocumentRole.SUBMITTED_DOCUMENT
        )
        self._assert_documents_still_match_the_screen()
        return (
            "Audit the two extracted documents below. "
            "The labels and page markers are context, not source text.\n\n"
            f"{specification}\n\n{submitted}"
        )

    def _extract_document(self, path: Path, label: str, role: DocumentRole) -> str:
        page_count = gate.build_document_record(path).page_count
        pages: list[str] = []
        for page_number in range(1, page_count + 1):
            result = self._tools.extract_pdf_text(role.value, page_number)
            if not result["ok"]:
                raise RuntimeError(
                    f"{label} page {page_number} could not be extracted: {result.get('error_code')}"
                )
            pages.append(f"--- {label} PAGE {page_number} ---\n{result['text']}")
        return "\n".join(pages)

    def _verify_claim(self, claim: AuditClaim) -> list[dict[str, object]]:
        return [
            self._tools.verify_quote(
                claim.spec_quote, claim.spec_page, DocumentRole.SPECIFICATION.value
            ),
            self._tools.verify_quote(
                claim.cut_sheet_quote,
                claim.cut_sheet_page,
                DocumentRole.SUBMITTED_DOCUMENT.value,
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
