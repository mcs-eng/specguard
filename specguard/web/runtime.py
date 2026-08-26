"""Production audit runner for the web service."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from google.cloud import firestore

from specguard.agent import AdkClaimGenerator, AuditRuntime, create_adk_agent
from specguard.models import AgentMode, AuditRunSummary
from specguard.tools import AuditTools


class AuditRunner(Protocol):
    """The audit operation used after validated PDFs reach durable storage."""

    async def run_audit(
        self,
        *,
        spec_path: Path,
        cut_sheet_path: Path,
        run_id: str,
        submittal_number: str,
        output_directory: Path,
    ) -> AuditRunSummary:
        """Run one audit over the two local request copies.

        ``submittal_number`` is the number the caller already assigned to this
        run and stored on its record. The RFI is numbered from it, so no part
        of the runtime derives a second identifier of its own.
        """


class GoogleAuditRunner:
    """Construct the existing deterministic runtime for one web request."""

    def __init__(self, *, project_id: str, agent_mode: AgentMode = AgentMode.FULL_TEXT) -> None:
        self._project_id = project_id
        self._agent_mode = agent_mode

    async def run_audit(
        self,
        *,
        spec_path: Path,
        cut_sheet_path: Path,
        run_id: str,
        submittal_number: str,
        output_directory: Path,
    ) -> AuditRunSummary:
        """Run the existing runtime with Cloud Run application credentials."""
        firestore_client = firestore.Client(project=self._project_id)
        try:
            tools = AuditTools(
                firestore_client=firestore_client,
                spec_path=spec_path,
                cut_sheet_path=cut_sheet_path,
                run_id=run_id,
                submittal_number=submittal_number,
                output_directory=output_directory,
            )
            agent = create_adk_agent(
                tools, project_id=self._project_id, agent_mode=self._agent_mode
            )
            claim_generator = AdkClaimGenerator(agent, run_id=run_id)
            runtime = AuditRuntime(
                claim_generator=claim_generator,
                tools=tools,
                spec_path=spec_path,
                cut_sheet_path=cut_sheet_path,
                run_id=run_id,
                project_id=self._project_id,
                agent_mode=self._agent_mode,
            )
            return await runtime.run()
        finally:
            firestore_client.close()
