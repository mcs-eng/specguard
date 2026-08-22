"""Network-free tests for Gemma severity classification."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pymupdf
import pytest
from requests.exceptions import Timeout

import specguard.severity as severity_module
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
        fail_message: str = "HTTP 429: RESOURCE_EXHAUSTED - prepayment credits depleted",
    ) -> None:
        self.severity = severity
        self.model_id = model_id
        self.should_fail = should_fail
        self.fail_message = fail_message
        self.calls: list[tuple[str, str, str]] = []

    def classify(
        self,
        claim_text: str,
        spec_quote: str,
        cut_sheet_quote: str,
    ) -> SeverityResult:
        self.calls.append((claim_text, spec_quote, cut_sheet_quote))
        if self.should_fail:
            return SeverityResult(
                severity=Severity.UNCLASSIFIED,
                model_id=None,
                status="fallback",
                reason=self.fail_message,
            )
        return SeverityResult(
            severity=self.severity,
            model_id=self.model_id,
            rationale="Test rationale for severity assignment.",
            status="classified",
            reason=None,
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


def _rfi_text(rfi_path: Path | str | None) -> str:
    """Read a generated RFI as one whitespace-normalized string.

    The RFI now lays its findings out in a table, so a cell wraps its text
    across several extracted lines. Collapsing whitespace lets a test assert on
    the sentence a reader sees rather than on the column width.
    """
    assert rfi_path is not None
    with pymupdf.open(rfi_path) as document:
        return " ".join(" ".join(page.get_text().split()) for page in document)


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
    fake = FakeSeverityClassifier(severity=Severity.HIGH, model_id="gemma-4-31b-it")
    finding = PersistedFinding(
        finding_id="find-1",
        run_id="run-1",
        claim_text="Parameter mismatch.",
        spec_quote=PersistedQuote(text="Req A", page_number=1, document_sha256="aa" * 32),
        cut_sheet_quote=PersistedQuote(text="Sub B", page_number=1, document_sha256="bb" * 32),
    )

    result = classify_severity(finding, classifier=fake)

    assert result.severity is Severity.HIGH
    assert result.model_id == "gemma-4-31b-it"
    assert result.status == "classified"
    assert result.reason is None
    assert len(fake.calls) == 1
    assert fake.calls[0] == ("Parameter mismatch.", "Req A", "Sub B")


def test_unclassified_retained_on_classifier_failure() -> None:
    """If the classifier throws an exception, UNCLASSIFIED is retained and fallback recorded."""
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
    assert result.status == "fallback"
    assert "RESOURCE_EXHAUSTED" in (result.reason or "")


def test_unclassified_retained_when_no_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """If no API key is available in env or secret manager, classify returns UNCLASSIFIED."""
    monkeypatch.delenv("SPECGUARD_GEMMA_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setattr(severity_module, "_fetch_secret_from_manager", lambda *args: None)
    classifier = GemmaSeverityClassifier(api_key=None)

    result = classifier.classify("Claim", "Quote 1", "Quote 2")

    assert result.severity is Severity.UNCLASSIFIED
    assert result.model_id is None
    assert result.status == "fallback"


def test_disabled_endpoint_records_the_demo_window_fallback_without_a_network_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The disabled sentinel must stop before it constructs an endpoint client."""
    monkeypatch.setenv("SPECGUARD_GEMMA_ENDPOINT", "disabled")

    def unexpected_endpoint_client(*args: object, **kwargs: object) -> object:
        raise AssertionError("the disabled sentinel must not make a network call")

    monkeypatch.setattr(
        severity_module, "VertexEndpointSeverityClassifier", unexpected_endpoint_client
    )
    finding = PersistedFinding(
        finding_id="find-1",
        run_id="run-1",
        claim_text="Parameter mismatch.",
        spec_quote=PersistedQuote(text="Req A", page_number=1, document_sha256="aa" * 32),
        cut_sheet_quote=PersistedQuote(text="Sub B", page_number=1, document_sha256="bb" * 32),
    )

    result = classify_severity(finding)

    assert result.severity is Severity.UNCLASSIFIED
    assert result.status == "fallback"
    assert result.reason == "severity endpoint not deployed outside demo windows"


def test_gemma_severity_classifier_parses_valid_structured_response() -> None:
    """GemmaSeverityClassifier correctly validates structured JSON from Gemma."""
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.text = (
        '{"severity": "medium", "rationale": "Operating rating difference requiring review."}'
    )
    mock_client.models.generate_content.return_value = mock_response

    classifier = GemmaSeverityClassifier(
        model_id="gemma-4-31b-it",
        client=mock_client,
    )
    result = classifier.classify("Discrepancy", "Spec quote", "Cut sheet quote")

    assert result.severity is Severity.MEDIUM
    assert result.model_id == "gemma-4-31b-it"
    assert result.status == "classified"
    assert "requiring review" in result.rationale


def test_severity_update_cannot_alter_verification_status(tmp_path: Path) -> None:
    """Pin: update_finding_severity modifies only severity metadata; status is untouched."""
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

    # Update severity with classification outcome
    update_res = tools.update_finding_severity(
        finding_id,
        Severity.HIGH,
        model_id="gemma-4-31b-it",
        status="classified",
        reason=None,
    )
    assert update_res["updated"] is True

    stored_after = client.data[FINDINGS_COLLECTION][finding_id]
    assert stored_after["severity"] == "high"
    assert stored_after["severity_model_id"] == "gemma-4-31b-it"
    assert stored_after["severity_status"] == "classified"
    assert stored_after["severity_reason"] is None
    # Verification status and rejection reason remain exactly untouched
    assert stored_after["verification_status"] == "verified"
    assert stored_after.get("rejection_reason") is None
    assert stored_after["claim_text"] == stored_before["claim_text"]
    assert stored_after["spec_quote"] == stored_before["spec_quote"]
    assert stored_after["cut_sheet_quote"] == stored_before["cut_sheet_quote"]


def _tools_with_one_verified_finding(tmp_path: Path, run_id: str) -> tuple[Any, Any, str]:
    """Persist one real verified finding and return the tools, the client, and its ID."""
    client = FakeFirestoreClient()
    spec = write_pdf(tmp_path / "spec.pdf", [["Req A"]])
    cut = write_pdf(tmp_path / "cut.pdf", [["Sub B"]])
    tools = AuditTools(
        firestore_client=client,
        spec_path=spec,
        cut_sheet_path=cut,
        run_id=run_id,
        output_directory=tmp_path / "artifacts",
    )
    persisted = tools.persist_finding(
        Finding(
            submittal_id=run_id,
            spec_locator="Page 1",
            cut_sheet_locator="Page 1",
            claim_text="Parameter mismatch.",
            quotes=[
                CitedQuote(text="Req A", page_number=1, document_path=str(spec)),
                CitedQuote(text="Sub B", page_number=1, document_path=str(cut)),
            ],
            verification_status=VerificationStatus.VERIFIED,
        )
    )
    assert persisted["persisted"] is True
    return tools, client, str(persisted["finding"]["finding_id"])


def test_severity_update_refuses_a_finding_that_is_not_in_the_ledger(tmp_path: Path) -> None:
    """An annotation call cannot create a severity-only document in the ledger.

    Before this check the method fell back to ``set(..., merge=True)``, which
    creates the document when it does not exist. A wrong or invented finding ID
    therefore wrote a findings record carrying a severity and nothing else: no
    quote, no claim, and no verification status. The run page then had a row in
    the findings collection that no verified write path produced.
    """
    tools, client, _ = _tools_with_one_verified_finding(tmp_path, "run-absent-1")
    before = dict(client.data[FINDINGS_COLLECTION])

    result = tools.update_finding_severity(
        "finding-that-was-never-written",
        Severity.HIGH,
        model_id="served-model",
        status="classified",
    )

    assert result == {"updated": False, "reason": "finding_not_in_ledger"}
    assert client.data[FINDINGS_COLLECTION] == before


def test_severity_update_refuses_a_finding_belonging_to_another_run(tmp_path: Path) -> None:
    """One run's tools cannot annotate another run's finding."""
    tools, client, finding_id = _tools_with_one_verified_finding(tmp_path, "run-owner-1")
    other = AuditTools(
        firestore_client=client,
        spec_path=tmp_path / "spec.pdf",
        cut_sheet_path=tmp_path / "cut.pdf",
        run_id="run-other-1",
        output_directory=tmp_path / "artifacts",
    )

    result = other.update_finding_severity(finding_id, Severity.HIGH, status="classified")

    assert result == {"updated": False, "reason": "finding_not_in_ledger"}
    assert client.data[FINDINGS_COLLECTION][finding_id]["severity"] == "unclassified"


def test_severity_update_refuses_a_record_that_is_not_verified(tmp_path: Path) -> None:
    """A findings document without a verified status is refused, not annotated."""
    tools, client, finding_id = _tools_with_one_verified_finding(tmp_path, "run-unverified-1")
    client.data[FINDINGS_COLLECTION][finding_id]["verification_status"] = "rejected"

    result = tools.update_finding_severity(finding_id, Severity.HIGH, status="classified")

    assert result == {"updated": False, "reason": "finding_not_in_ledger"}
    assert client.data[FINDINGS_COLLECTION][finding_id]["severity"] == "unclassified"


def test_severity_update_records_the_endpoint_label_separately(tmp_path: Path) -> None:
    """A configured endpoint label is stored under its own field, not as a model ID."""
    tools, client, finding_id = _tools_with_one_verified_finding(tmp_path, "run-label-1")

    result = tools.update_finding_severity(
        finding_id,
        Severity.HIGH,
        model_id=None,
        endpoint_label="google-gemma3-gemma-3-1b-it",
        status="classified",
    )

    assert result["updated"] is True
    stored = client.data[FINDINGS_COLLECTION][finding_id]
    assert stored["severity_model_id"] is None
    assert stored["severity_endpoint_label"] == "google-gemma3-gemma-3-1b-it"


def test_runtime_annotates_persisted_finding_with_severity(tmp_path: Path) -> None:
    """When a claim is verified and persisted, AuditRuntime classifies severity via fake."""
    fake_classifier = FakeSeverityClassifier(severity=Severity.HIGH, model_id="gemma-4-31b-it")
    generator = FakeClaimGenerator(AuditClaimBatch(claims=[_claim()]))
    runtime, client, _, _ = _runtime(tmp_path, generator, classifier=fake_classifier)

    summary = asyncio.run(runtime.run())

    assert summary.findings_persisted == 1
    assert summary.severity_status == "classified"
    assert summary.severity_reason is None
    assert len(fake_classifier.calls) == 1

    stored_finding = next(iter(client.data[FINDINGS_COLLECTION].values()))
    assert stored_finding["severity"] == "high"
    assert stored_finding["severity_model_id"] == "gemma-4-31b-it"
    assert stored_finding["severity_status"] == "classified"
    assert stored_finding["verification_status"] == "verified"

    # Verify RFI carries classified severity and model
    assert "HIGH - model: gemma-4-31b-it" in _rfi_text(summary.rfi_path)


def test_runtime_records_fallback_outcome_on_failure(
    tmp_path: Path,
) -> None:
    """Pin: if classification fails, fallback is explicitly recorded on finding and summary."""
    failing_classifier = FakeSeverityClassifier(
        should_fail=True,
        fail_message="HTTP 429: RESOURCE_EXHAUSTED - prepayment credits depleted",
    )
    generator = FakeClaimGenerator(AuditClaimBatch(claims=[_claim()]))
    runtime, client, _, _ = _runtime(tmp_path, generator, classifier=failing_classifier)

    summary = asyncio.run(runtime.run())

    assert summary.findings_persisted == 1
    assert summary.severity_status == "fallback"
    assert "HTTP 429" in (summary.severity_reason or "")

    stored_finding = next(iter(client.data[FINDINGS_COLLECTION].values()))
    assert stored_finding["severity"] == "unclassified"
    assert stored_finding.get("severity_model_id") is None
    assert stored_finding["severity_status"] == "fallback"
    assert "HTTP 429" in (stored_finding.get("severity_reason") or "")
    assert stored_finding["verification_status"] == "verified"

    text = _rfi_text(summary.rfi_path)
    assert "UNCLASSIFIED - status: fallback" in text
    assert "reason: HTTP 429" in text


def test_vertex_endpoint_classifier_parses_single_token() -> None:
    """VertexEndpointSeverityClassifier correctly parses single token HIGH/MEDIUM/LOW."""
    mock_session = MagicMock()
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "predictions": ["Prompt:\n...\nOutput:\nHIGH"],
        "deployedModelId": "1787417126",
        "model": "projects/p/locations/us-central1/models/served-gemma",
    }
    mock_session.post.return_value = mock_response

    from specguard.severity import VertexEndpointSeverityClassifier

    classifier = VertexEndpointSeverityClassifier(
        endpoint_resource_name="mg-endpoint-123",
        endpoint_dns="custom.vertexai.goog",
        model_id="gemma-2-2b-it",
        session=mock_session,
    )
    result = classifier.classify("Discrepancy", "Spec quote", "Cut quote")

    assert result.severity is Severity.HIGH
    assert result.model_id == "1787417126"
    assert result.endpoint_label == "gemma-2-2b-it"
    assert result.status == "classified"
    assert result.reason is None


def test_vertex_endpoint_classifier_parses_json_severity() -> None:
    """VertexEndpointSeverityClassifier correctly parses JSON snippet output."""
    mock_session = MagicMock()
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "predictions": ['Output:\n```json\n{"severity": "medium"}\n```']
    }
    mock_session.post.return_value = mock_response

    from specguard.severity import VertexEndpointSeverityClassifier

    classifier = VertexEndpointSeverityClassifier(
        endpoint_resource_name="mg-endpoint-123",
        endpoint_dns="custom.vertexai.goog",
        model_id="gemma-2-2b-it",
        session=mock_session,
    )
    result = classifier.classify("Discrepancy", "Spec quote", "Cut quote")

    assert result.severity is Severity.MEDIUM
    assert result.model_id is None
    assert result.endpoint_label == "gemma-2-2b-it"
    assert result.status == "classified"


def test_vertex_endpoint_records_the_served_model_the_endpoint_named() -> None:
    """Provenance is what the endpoint reported, not what this deployment configured.

    ``SPECGUARD_GEMMA_MODEL`` is an operator-supplied string. Nothing validates
    it against the endpoint, so recording it as ``severity_model_id`` published
    an observation nobody made. The display name the response carries is a real
    observation, and it wins.
    """
    mock_session = MagicMock()
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "predictions": ["Output:\nHIGH"],
        "modelDisplayName": "google-gemma3-gemma-3-1b-it",
        "deployedModelId": "1787417126",
    }
    mock_session.post.return_value = mock_response

    from specguard.severity import VertexEndpointSeverityClassifier

    classifier = VertexEndpointSeverityClassifier(
        endpoint_resource_name="mg-endpoint-123",
        endpoint_dns="custom.vertexai.goog",
        model_id="a-label-nobody-checked",
        session=mock_session,
    )
    result = classifier.classify("Discrepancy", "Spec quote", "Cut quote")

    assert result.model_id == "google-gemma3-gemma-3-1b-it"
    assert result.endpoint_label == "a-label-nobody-checked"


def test_vertex_endpoint_records_no_model_id_when_the_response_names_none() -> None:
    """With nothing observed, the model field stays empty and the label carries it."""
    mock_session = MagicMock()
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"predictions": ["Output:\nLOW"]}
    mock_session.post.return_value = mock_response

    from specguard.severity import VertexEndpointSeverityClassifier

    classifier = VertexEndpointSeverityClassifier(
        endpoint_resource_name="mg-endpoint-123",
        endpoint_dns="custom.vertexai.goog",
        model_id="google-gemma3-gemma-3-1b-it",
        session=mock_session,
    )
    result = classifier.classify("Discrepancy", "Spec quote", "Cut quote")

    assert result.severity is Severity.LOW
    assert result.status == "classified"
    assert result.model_id is None
    assert result.endpoint_label == "google-gemma3-gemma-3-1b-it"


def test_the_two_severity_backends_read_separate_model_variables() -> None:
    """One variable named two things, so either value was wrong for one backend.

    ``SPECGUARD_GEMMA_MODEL`` names the Vertex endpoint's deployed model. The
    generativelanguage API does not share that naming scheme, and it 404s on a
    name it does not serve, so it reads its own variable.
    """
    import importlib

    import specguard.severity as module

    reloaded = importlib.reload(module)
    assert reloaded.GEMMA_MODEL_ID == "gemma-4-31b-it"
    assert reloaded.VERTEX_ENDPOINT_LABEL == "google-gemma3-gemma-3-1b-it"

    source = Path(reloaded.__file__).read_text(encoding="utf-8")
    assert 'os.environ.get("SPECGUARD_GEMMA_API_MODEL", "gemma-4-31b-it")' in source
    assert 'os.environ.get("SPECGUARD_GEMMA_MODEL", "google-gemma3-gemma-3-1b-it")' in source


def test_vertex_endpoint_classifier_handles_invalid_output() -> None:
    """VertexEndpointSeverityClassifier falls back gracefully when output cannot be parsed."""
    mock_session = MagicMock()
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"predictions": ["Output:\nI cannot determine the rating."]}
    mock_session.post.return_value = mock_response

    from specguard.severity import VertexEndpointSeverityClassifier

    classifier = VertexEndpointSeverityClassifier(
        endpoint_resource_name="mg-endpoint-123",
        endpoint_dns="custom.vertexai.goog",
        session=mock_session,
    )
    result = classifier.classify("Discrepancy", "Spec quote", "Cut quote")

    assert result.severity is Severity.UNCLASSIFIED
    assert result.status == "fallback"
    assert "No valid severity token" in (result.reason or "")


def test_vertex_endpoint_classifier_handles_http_error() -> None:
    """VertexEndpointSeverityClassifier falls back when HTTP error occurs."""
    mock_session = MagicMock()
    mock_response = MagicMock()
    mock_response.status_code = 503
    mock_response.text = "Service Unavailable"
    mock_session.post.return_value = mock_response

    from specguard.severity import VertexEndpointSeverityClassifier

    classifier = VertexEndpointSeverityClassifier(
        endpoint_resource_name="mg-endpoint-123",
        endpoint_dns="custom.vertexai.goog",
        session=mock_session,
    )
    result = classifier.classify("Discrepancy", "Spec quote", "Cut quote")

    assert result.severity is Severity.UNCLASSIFIED
    assert result.status == "fallback"
    assert "HTTP 503" in (result.reason or "")
    assert mock_session.post.call_count == 2


def test_vertex_endpoint_classifier_retries_one_5xx_then_succeeds() -> None:
    first = MagicMock(status_code=500, text="Internal Server Error")
    second = MagicMock(status_code=200)
    second.json.return_value = {"predictions": ["Output:\nHIGH"]}
    mock_session = MagicMock()
    mock_session.post.side_effect = [first, second]

    from specguard.severity import VertexEndpointSeverityClassifier

    result = VertexEndpointSeverityClassifier(
        endpoint_resource_name="mg-endpoint-123",
        endpoint_dns="custom.vertexai.goog",
        session=mock_session,
    ).classify("Discrepancy", "Spec quote", "Cut quote")

    assert result.severity is Severity.HIGH
    assert result.status == "classified"
    assert mock_session.post.call_count == 2


def test_vertex_endpoint_classifier_retries_one_timeout_then_succeeds() -> None:
    second = MagicMock(status_code=200)
    second.json.return_value = {"predictions": ["Output:\nLOW"]}
    mock_session = MagicMock()
    mock_session.post.side_effect = [Timeout("deadline"), second]

    from specguard.severity import VertexEndpointSeverityClassifier

    result = VertexEndpointSeverityClassifier(
        endpoint_resource_name="mg-endpoint-123",
        endpoint_dns="custom.vertexai.goog",
        session=mock_session,
    ).classify("Discrepancy", "Spec quote", "Cut quote")

    assert result.severity is Severity.LOW
    assert result.status == "classified"
    assert mock_session.post.call_count == 2


def test_vertex_endpoint_classifier_stops_after_two_failed_attempts() -> None:
    first = MagicMock(status_code=503, text="Service Unavailable")
    second = MagicMock(status_code=503, text="Service Unavailable")
    mock_session = MagicMock()
    mock_session.post.side_effect = [first, second]

    from specguard.severity import VertexEndpointSeverityClassifier

    result = VertexEndpointSeverityClassifier(
        endpoint_resource_name="mg-endpoint-123",
        endpoint_dns="custom.vertexai.goog",
        session=mock_session,
    ).classify("Discrepancy", "Spec quote", "Cut quote")

    assert result.severity is Severity.UNCLASSIFIED
    assert result.status == "fallback"
    assert "HTTP 503" in (result.reason or "")
    assert mock_session.post.call_count == 2


def test_classify_severity_selects_vertex_endpoint_from_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """classify_severity selects Vertex endpoint when SPECGUARD_GEMMA_ENDPOINT is configured."""
    monkeypatch.setenv("SPECGUARD_GEMMA_ENDPOINT", "projects/p/locations/l/endpoints/e123")
    monkeypatch.setenv("SPECGUARD_GEMMA_ENDPOINT_DNS", "e123.vertexai.goog")

    mock_session = MagicMock()
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"predictions": ["Output:\nLOW"]}
    mock_session.post.return_value = mock_response

    finding = PersistedFinding(
        finding_id="find-1",
        run_id="run-1",
        claim_text="Parameter mismatch.",
        spec_quote=PersistedQuote(text="Req A", page_number=1, document_sha256="aa" * 32),
        cut_sheet_quote=PersistedQuote(text="Sub B", page_number=1, document_sha256="bb" * 32),
    )

    from specguard.severity import VertexEndpointSeverityClassifier

    # Patch _get_session on VertexEndpointSeverityClassifier
    monkeypatch.setattr(VertexEndpointSeverityClassifier, "_get_session", lambda self: mock_session)

    result = classify_severity(finding)
    assert result.severity is Severity.LOW
    assert result.status == "classified"


def test_vertex_endpoint_classifier_does_not_falsely_match_prompt_rubric() -> None:
    """Prompt echo containing 'HIGH' in rubric must not override a 'LOW' completion."""
    mock_session = MagicMock()
    mock_response = MagicMock()
    mock_response.status_code = 200
    # Simulate an echoed prompt containing HIGH/MEDIUM/LOW rubric followed by actual Output: LOW
    echoed_prediction = "Prompt:\nAssign severity: HIGH (critical), MEDIUM, or LOW.\nOutput:\nLOW"
    mock_response.json.return_value = {"predictions": [echoed_prediction]}
    mock_session.post.return_value = mock_response

    from specguard.severity import VertexEndpointSeverityClassifier

    classifier = VertexEndpointSeverityClassifier(
        endpoint_resource_name="mg-endpoint-123",
        endpoint_dns="custom.vertexai.goog",
        model_id="gemma-2-2b-it",
        session=mock_session,
    )
    result = classifier.classify("Discrepancy", "Spec quote", "Cut quote")

    assert result.severity is Severity.LOW
    assert result.status == "classified"
