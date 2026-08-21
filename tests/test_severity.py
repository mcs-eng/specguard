"""Network-free tests for Gemma severity classification."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pymupdf
import pytest

from specguard.agent import AuditRuntime
from specguard.models import (
    AuditClaim,
    AuditClaimBatch,
    CitedQuote,
    Finding,
    PersistedFinding,
    PersistedQuote,
    Severity,
    VerificationStatus,
)
from specguard.severity import (
    GEMMA_MODEL_ID,
    GemmaSeverityClassifier,
    SeverityResult,
    classify_severity,
    load_severity_prompt,
)
from specguard.tools import FINDINGS_COLLECTION, AuditTools
from tests.fake_firestore import FakeFirestoreClient
from tests.fixtures_pdf import write_pdf


class FakeSeverityClassifier:
    """Test fake for Gemma severity classification."""

    def __init__(
        self,
        severity: Severity = Severity.HIGH,
        model_id: str = GEMMA_MODEL_ID,
        *,
        should_fail: bool = False,
    ) -> None:
        self.severity = severity
        self.model_id = model_id
        self.should_fail = should_fail
        self.calls: list[tuple[str, str, str]] = []

    def classify(
        self,
        claim_text: str,
        spec_quote: str,
        cut_sheet_quote: str,
    ) -> SeverityResult:
        self.calls.append((claim_text, spec_quote, cut_sheet_quote))
        if self.should_fail:
            raise RuntimeError("Simulated Gemma API network failure")
        return SeverityResult(
            severity=self.severity,
            model_id=self.model_id,
            rationale="Test rationale for severity assignment.",
        )


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
    classifier: Any = None,
) -> tuple[AuditRuntime, FakeFirestoreClient, Path, Path]:
    spec = write_pdf(
        tmp_path / "governing.pdf",
        [
            [
                "Project: Fictional Workshop | Owner: Fictional Authority",
                "The required characteristic is alpha.",
            ]
        ],
    )
    cut_sheet = write_pdf(
        tmp_path / "submitted.pdf",
        [["The submitted characteristic is beta."]],
    )
    client = FakeFirestoreClient()
    tools = AuditTools(
        firestore_client=client,
        spec_path=spec,
        cut_sheet_path=cut_sheet,
        run_id="audit-run-severity-1",
        output_directory=tmp_path / "artifacts",
    )
    runtime = AuditRuntime(
        claim_generator=generator,
        tools=tools,
        spec_path=spec,
        cut_sheet_path=cut_sheet,
        run_id="audit-run-severity-1",
        severity_classifier=classifier,
    )
    return runtime, client, spec, cut_sheet


def test_severity_prompt_is_generic_and_contains_no_fixture_vocabulary() -> None:
    """Prompt hygiene test: the severity prompt must not contain any fixture terms."""
    prompt = load_severity_prompt().casefold()
    forbidden = [
        "voltage",
        "temperature",
        "units",
        "switchboard",
        "caldra",
        "veylan",
        "torven",
        "asterquay",
        "planted discrepancy",
        "manifest.md",
        "208v",
        "480v",
        "158 deg f",
        "70 deg c",
        "90 deg c",
        "fahrenheit",
        "celsius",
    ]
    assert all(term not in prompt for term in forbidden)
    assert "discrepancy" in prompt
    assert "high" in prompt
    assert "medium" in prompt
    assert "low" in prompt


def test_classifier_wiring_with_a_fake() -> None:
    """Classifying with a fake classifier returns assigned severity and model ID."""
    fake = FakeSeverityClassifier(severity=Severity.HIGH, model_id="gemma-3-27b-it")
    finding = PersistedFinding(
        finding_id="find-1",
        run_id="run-1",
        claim_text="Parameter mismatch.",
        spec_quote=PersistedQuote(text="Req A", page_number=1, document_sha256="aa" * 32),
        cut_sheet_quote=PersistedQuote(text="Sub B", page_number=1, document_sha256="bb" * 32),
    )

    result = classify_severity(finding, classifier=fake)

    assert result.severity is Severity.HIGH
    assert result.model_id == "gemma-3-27b-it"
    assert len(fake.calls) == 1
    assert fake.calls[0] == ("Parameter mismatch.", "Req A", "Sub B")


def test_unclassified_retained_on_classifier_failure() -> None:
    """If the classifier throws an exception, UNCLASSIFIED is retained and no error is raised."""
    fake = FakeSeverityClassifier(should_fail=True)
    finding = PersistedFinding(
        finding_id="find-1",
        run_id="run-1",
        claim_text="Parameter mismatch.",
        spec_quote=PersistedQuote(text="Req A", page_number=1, document_sha256="aa" * 32),
        cut_sheet_quote=PersistedQuote(text="Sub B", page_number=1, document_sha256="bb" * 32),
    )

    result = classify_severity(finding, classifier=fake)

    assert result.severity is Severity.UNCLASSIFIED
    assert result.model_id is None


def test_unclassified_retained_when_no_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """If no API key is available in env or secret manager, classify returns UNCLASSIFIED."""
    monkeypatch.delenv("SPECGUARD_GEMMA_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    classifier = GemmaSeverityClassifier(api_key=None)

    result = classifier.classify("Claim", "Quote 1", "Quote 2")

    assert result.severity is Severity.UNCLASSIFIED
    assert result.model_id is None


def test_gemma_severity_classifier_parses_valid_structured_response() -> None:
    """GemmaSeverityClassifier correctly validates structured JSON from Gemma."""
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.text = (
        '{"severity": "medium", "rationale": "Operating rating difference requiring review."}'
    )
    mock_client.models.generate_content.return_value = mock_response

    classifier = GemmaSeverityClassifier(
        model_id="gemma-3-27b-it",
        client=mock_client,
    )
    result = classifier.classify("Discrepancy", "Spec quote", "Cut sheet quote")

    assert result.severity is Severity.MEDIUM
    assert result.model_id == "gemma-3-27b-it"
    assert "requiring review" in result.rationale


def test_severity_update_cannot_alter_verification_status(tmp_path: Path) -> None:
    """Pin: update_finding_severity modifies only severity and model_id; status is untouched."""
    client = FakeFirestoreClient()
    spec = write_pdf(tmp_path / "spec.pdf", [["Req A"]])
    cut = write_pdf(tmp_path / "cut.pdf", [["Sub B"]])
    tools = AuditTools(
        firestore_client=client,
        spec_path=spec,
        cut_sheet_path=cut,
        run_id="run-pin-1",
        output_directory=tmp_path / "artifacts",
    )

    initial_finding = Finding(
        submittal_id="run-pin-1",
        spec_locator="Page 1",
        cut_sheet_locator="Page 1",
        claim_text="Parameter mismatch.",
        quotes=[
            CitedQuote(text="Req A", page_number=1, document_path=str(spec)),
            CitedQuote(text="Sub B", page_number=1, document_path=str(cut)),
        ],
        verification_status=VerificationStatus.VERIFIED,
    )
    persist_res = tools.persist_finding(initial_finding)
    assert persist_res["persisted"] is True
    finding_id = persist_res["finding"]["finding_id"]

    stored_before = dict(client.data[FINDINGS_COLLECTION][finding_id])
    assert stored_before["verification_status"] == "verified"
    assert stored_before.get("rejection_reason") is None
    assert stored_before["severity"] == "unclassified"
    assert stored_before.get("severity_model_id") is None

    # Update severity
    update_res = tools.update_finding_severity(
        finding_id,
        Severity.HIGH,
        model_id="gemma-3-27b-it",
    )
    assert update_res["updated"] is True

    stored_after = client.data[FINDINGS_COLLECTION][finding_id]
    assert stored_after["severity"] == "high"
    assert stored_after["severity_model_id"] == "gemma-3-27b-it"
    # Verification status and rejection reason remain exactly untouched
    assert stored_after["verification_status"] == "verified"
    assert stored_after.get("rejection_reason") is None
    assert stored_after["claim_text"] == stored_before["claim_text"]
    assert stored_after["spec_quote"] == stored_before["spec_quote"]
    assert stored_after["cut_sheet_quote"] == stored_before["cut_sheet_quote"]


def test_runtime_annotates_persisted_finding_with_severity(tmp_path: Path) -> None:
    """When a claim is verified and persisted, AuditRuntime classifies severity via Gemma fake."""
    fake_classifier = FakeSeverityClassifier(severity=Severity.HIGH, model_id="gemma-3-27b-it")
    generator = FakeClaimGenerator(AuditClaimBatch(claims=[_claim()]))
    runtime, client, _, _ = _runtime(tmp_path, generator, classifier=fake_classifier)

    summary = asyncio.run(runtime.run())

    assert summary.findings_persisted == 1
    assert len(fake_classifier.calls) == 1

    stored_finding = next(iter(client.data[FINDINGS_COLLECTION].values()))
    assert stored_finding["severity"] == "high"
    assert stored_finding["severity_model_id"] == "gemma-3-27b-it"
    assert stored_finding["verification_status"] == "verified"

    # Verify RFI carries classified severity and model
    with pymupdf.open(summary.rfi_path) as document:
        text = "\n".join(page.get_text() for page in document)
    assert "Severity: HIGH (model: gemma-3-27b-it)" in text


def test_runtime_keeps_unclassified_on_severity_failure_and_audit_still_completes(
    tmp_path: Path,
) -> None:
    """If severity classification fails, finding remains UNCLASSIFIED and audit finishes."""
    failing_classifier = FakeSeverityClassifier(should_fail=True)
    generator = FakeClaimGenerator(AuditClaimBatch(claims=[_claim()]))
    runtime, client, _, _ = _runtime(tmp_path, generator, classifier=failing_classifier)

    summary = asyncio.run(runtime.run())

    assert summary.findings_persisted == 1
    stored_finding = next(iter(client.data[FINDINGS_COLLECTION].values()))
    assert stored_finding["severity"] == "unclassified"
    assert stored_finding.get("severity_model_id") is None
    assert stored_finding["verification_status"] == "verified"

    with pymupdf.open(summary.rfi_path) as document:
        text = "\n".join(page.get_text() for page in document)
    assert "Severity: UNCLASSIFIED" in text
