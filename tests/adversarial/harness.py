"""Shared builders for the adversarial invariant suite."""

from __future__ import annotations

from pathlib import Path

from specguard.models import (
    CitedQuote,
    Finding,
    RejectionReason,
    VerificationStatus,
)
from specguard.tools import AuditTools
from tests.fake_firestore import FakeFirestoreClient
from tests.fixtures_pdf import write_pdf

SPEC_LINE = "Requirement alpha governs this audit."
CUT_LINE = "Submitted characteristic beta is offered."
CUT_SENTINEL = "SENTINEL-UNQUOTED-SECOND-PAGE-LINE."
RUN_ID = "adversarial-run-1"


class Harness:
    """One bound audit: two generated PDFs, a fake Firestore, and the tools."""

    def __init__(self, tmp_path: Path) -> None:
        self.spec = write_pdf(
            tmp_path / "governing.pdf",
            [
                ["Project: Fictional Workshop | Owner: Fictional Authority", SPEC_LINE],
                ["Second governing page."],
            ],
        )
        self.cut_sheet = write_pdf(
            tmp_path / "submitted.pdf",
            [[CUT_LINE], [CUT_SENTINEL]],
        )
        self.client = FakeFirestoreClient()
        self.tools = AuditTools(
            firestore_client=self.client,
            spec_path=self.spec,
            cut_sheet_path=self.cut_sheet,
            run_id=RUN_ID,
            output_directory=tmp_path / "artifacts",
        )

    def finding(
        self,
        *,
        spec_quote: str = SPEC_LINE,
        cut_quote: str = CUT_LINE,
        status: VerificationStatus = VerificationStatus.PENDING,
        reason: RejectionReason | None = None,
        quotes: list[CitedQuote] | None = None,
    ) -> Finding:
        if quotes is None:
            quotes = [
                CitedQuote(text=spec_quote, page_number=1, document_path=str(self.spec)),
                CitedQuote(text=cut_quote, page_number=1, document_path=str(self.cut_sheet)),
            ]
        extra: dict[str, RejectionReason] = {}
        if reason is not None:
            extra["rejection_reason"] = reason
        return Finding(
            submittal_id=RUN_ID,
            spec_locator="Page 1",
            cut_sheet_locator="Page 1",
            claim_text="The submitted characteristic conflicts with the requirement.",
            quotes=quotes,
            verification_status=status,
            **extra,
        )
