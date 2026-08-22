"""Gemma-based severity classification for verified SpecGuard findings.

Severity classification is an annotation on a verified finding. It runs ONLY
on findings that have already passed the verification gate and been persisted to
the ledger. It is never a path into the ledger and it never alters verification
status.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from google.genai import types
from pydantic import BaseModel, Field
from requests.exceptions import Timeout

from specguard.models import Finding, PersistedFinding, Severity

logger = logging.getLogger(__name__)

#: Default model for the generativelanguage backend, which addresses a model by
#: name in the request URL and returns 404 for a name it does not serve. The
#: 2026-08-21 ListModels probe recorded in HANDOFF.md returned
#: ``models/gemma-4-31b-it`` and ``models/gemma-4-26b-a4b-it``, and 404 for
#: ``models/gemma-3-27b-it``. This constant reads its own variable:
#: ``SPECGUARD_GEMMA_MODEL`` names the Vertex endpoint's deployed model, whose
#: naming scheme this API does not share, and one shared variable made either
#: value wrong for the other backend.
GEMMA_MODEL_ID = os.environ.get("SPECGUARD_GEMMA_API_MODEL", "gemma-4-31b-it")

#: Fallback label for the Vertex endpoint backend when the endpoint's own
#: response names no served model. It is a configured label, never an observed
#: model identifier, and it is recorded under that name.
VERTEX_ENDPOINT_LABEL = os.environ.get("SPECGUARD_GEMMA_MODEL", "google-gemma3-gemma-3-1b-it")

#: Fields a Vertex ``:predict`` response may use to name the model that served
#: it, most specific first.
_SERVED_MODEL_FIELDS = ("modelDisplayName", "deployedModelId", "model")


def _served_model_id(payload: Any) -> str | None:
    """Return the model identifier the endpoint reported, if it reported one."""
    if not isinstance(payload, dict):
        return None
    for field in _SERVED_MODEL_FIELDS:
        value = payload.get(field)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


PROMPT_PATH = Path(__file__).parent / "prompts" / "classify_severity_v1.txt"


class SeverityClassification(BaseModel):
    """Structured response schema from Gemma severity classifier."""

    severity: Severity = Field(description="Assigned severity level: low, medium, or high.")
    rationale: str = Field(
        default="",
        description="Brief technical rationale for the assigned severity level.",
    )


@dataclass(frozen=True)
class SeverityResult:
    """The outcome of a severity classification attempt."""

    severity: Severity
    #: The model identifier the serving endpoint reported. ``None`` when the
    #: endpoint reported none: a configured value is not an observation.
    model_id: str | None = None
    #: The configured label for the endpoint that answered. It names what this
    #: deployment was pointed at, not what served the request.
    endpoint_label: str | None = None
    rationale: str = ""
    status: str = "classified"
    reason: str | None = None

    def __eq__(self, other: object) -> bool:
        if isinstance(other, Severity):
            return self.severity == other
        if isinstance(other, SeverityResult):
            return (
                self.severity == other.severity
                and self.model_id == other.model_id
                and self.endpoint_label == other.endpoint_label
                and self.rationale == other.rationale
                and self.status == other.status
                and self.reason == other.reason
            )
        return super().__eq__(other)


class SeverityClassifier(Protocol):
    """Protocol for severity classifiers (used for dependency injection and tests)."""

    def classify(
        self,
        claim_text: str,
        spec_quote: str,
        cut_sheet_quote: str,
    ) -> SeverityResult:
        """Classify the severity of a verified finding."""


def load_severity_prompt() -> str:
    """Load the versioned, fixture-neutral severity classification instruction."""
    return PROMPT_PATH.read_text(encoding="utf-8")


def _extract_error_detail(error: Exception) -> str:
    """Extract a concise HTTP status or error description from an exception."""
    if hasattr(error, "code") and hasattr(error, "message"):
        return f"HTTP {error.code}: {str(error.message).strip()}"
    if hasattr(error, "status_code"):
        return f"HTTP {error.status_code}: {str(error).strip()}"
    msg = str(error).strip()
    if "429" in msg or "RESOURCE_EXHAUSTED" in msg:
        return "HTTP 429: RESOURCE_EXHAUSTED - prepayment credits depleted"
    if "404" in msg or "NOT_FOUND" in msg:
        return "HTTP 404: NOT_FOUND - model not found or not supported for generateContent"
    if "403" in msg or "PERMISSION_DENIED" in msg:
        return "HTTP 403: PERMISSION_DENIED - API key restriction or unauthorized"
    return f"{type(error).__name__}: {msg}"


def _fetch_secret_from_manager(
    project_id: str = "specguard-hack",
    secret_id: str = "specguard-gemma-key",
    timeout: float = 3.0,
) -> str | None:
    """Fetch the Gemma API key from Google Secret Manager if configured."""
    try:
        from google.cloud import secretmanager

        client = secretmanager.SecretManagerServiceClient()
        name = f"projects/{project_id}/secrets/{secret_id}/versions/latest"
        response = client.access_secret_version(request={"name": name}, timeout=timeout)
        return response.payload.data.decode("utf-8").strip()
    except Exception:
        return None


def _get_api_key(project_id: str | None = None) -> str | None:
    """Resolve the Gemini API key from environment variables or Secret Manager."""
    env_key = os.environ.get("SPECGUARD_GEMMA_KEY") or os.environ.get("GEMINI_API_KEY")
    if env_key and env_key.strip():
        return env_key.strip()
    resolved_project = project_id or os.environ.get("SPECGUARD_PROJECT", "specguard-hack")
    return _fetch_secret_from_manager(resolved_project)


class GemmaSeverityClassifier:
    """Gemma severity classifier calling generativelanguage.googleapis.com."""

    def __init__(
        self,
        *,
        model_id: str = GEMMA_MODEL_ID,
        api_key: str | None = None,
        client: Any = None,
        project_id: str | None = None,
        timeout_ms: int = 15000,
    ) -> None:
        self.model_id = model_id
        self._api_key = api_key
        self._client = client
        self._project_id = project_id or os.environ.get("SPECGUARD_PROJECT", "specguard-hack")
        self._timeout_ms = timeout_ms

    def _get_client(self) -> Any:
        if self._client is not None:
            return self._client
        key = self._api_key or _get_api_key(self._project_id)
        if not key:
            raise ValueError("No Gemini API key available for Gemma classification.")
        from google import genai

        self._client = genai.Client(api_key=key)
        return self._client

    def classify(
        self,
        claim_text: str,
        spec_quote: str,
        cut_sheet_quote: str,
    ) -> SeverityResult:
        """Call Gemma with structured output to classify severity."""
        try:
            client = self._get_client()
            prompt = load_severity_prompt()
            content = (
                f"{prompt}\n\n"
                f"Discrepancy Claim:\n{claim_text}\n\n"
                f'Specification Requirement Quote:\n"{spec_quote}"\n\n'
                f'Submitted Document Quote:\n"{cut_sheet_quote}"'
            )
            response = client.models.generate_content(
                model=self.model_id,
                contents=content,
                config=types.GenerateContentConfig(
                    temperature=0,
                    response_mime_type="application/json",
                    response_schema=SeverityClassification,
                    http_options=types.HttpOptions(timeout=self._timeout_ms),
                ),
            )
            text = response.text
            if not text:
                return SeverityResult(
                    severity=Severity.UNCLASSIFIED,
                    model_id=None,
                    status="fallback",
                    reason="Empty response text from Gemma",
                )

            data = json.loads(text)
            parsed = SeverityClassification.model_validate(data)
            return SeverityResult(
                severity=parsed.severity,
                model_id=self.model_id,
                rationale=parsed.rationale,
                status="classified",
                reason=None,
            )
        except Exception as error:
            error_detail = _extract_error_detail(error)
            logger.warning("Gemma severity classification failed: %s", error_detail)
            return SeverityResult(
                severity=Severity.UNCLASSIFIED,
                model_id=None,
                status="fallback",
                reason=error_detail,
            )


def _parse_severity_token(raw_output: Any, prompt: str | None = None) -> Severity:
    """Strictly parse a severity token (LOW, MEDIUM, HIGH) from endpoint completion only."""
    import re

    if isinstance(raw_output, dict):
        text = str(
            raw_output.get("generated_text")
            or raw_output.get("text")
            or raw_output.get("content")
            or raw_output
        )
    else:
        text = str(raw_output)

    if prompt and prompt in text:
        text = text.split(prompt, 1)[1]

    if "Output:" in text:
        text = text.split("Output:")[-1]

    text = text.strip()

    json_match = re.search(
        r'["\']severity["\']\s*:\s*["\']?(HIGH|MEDIUM|LOW)["\']?', text, re.IGNORECASE
    )
    if json_match:
        return Severity(json_match.group(1).lower())
    word_match = re.search(r"\b(HIGH|MEDIUM|LOW)\b", text, re.IGNORECASE)
    if word_match:
        return Severity(word_match.group(1).lower())
    preview = str(raw_output)[:100]
    raise ValueError(f"No valid severity token (LOW, MEDIUM, HIGH) found in: {preview}")


class VertexEndpointSeverityClassifier:
    """Gemma severity classifier calling a deployed Vertex AI Model Garden Endpoint.

    Authenticates using Application Default Credentials (ADC) / the runtime SA.
    Does not use an API key.
    """

    def __init__(
        self,
        *,
        endpoint_resource_name: str,
        endpoint_dns: str | None = None,
        model_id: str | None = None,
        project_id: str | None = None,
        location: str = "us-central1",
        timeout_seconds: float = 15.0,
        session: Any = None,
    ) -> None:
        self.endpoint_resource_name = endpoint_resource_name
        self.endpoint_dns = endpoint_dns or os.environ.get("SPECGUARD_GEMMA_ENDPOINT_DNS")
        #: What this deployment was pointed at. Nothing validates it against the
        #: endpoint, so it is a label, and it is recorded as one.
        self.endpoint_label = model_id or VERTEX_ENDPOINT_LABEL
        self.project_id = project_id or os.environ.get("SPECGUARD_PROJECT", "specguard-hack")
        self.location = location
        self.timeout_seconds = timeout_seconds
        self._session = session

    def _get_session(self) -> Any:
        if self._session is not None:
            return self._session
        import google.auth
        from google.auth.transport.requests import AuthorizedSession

        credentials, _ = google.auth.default(
            scopes=["https://www.googleapis.com/auth/cloud-platform"]
        )
        self._session = AuthorizedSession(credentials)
        return self._session

    def _get_predict_url(self) -> str:
        endpoint_name = self.endpoint_resource_name
        if not endpoint_name.startswith("projects/"):
            endpoint_name = (
                f"projects/{self.project_id}/locations/{self.location}/endpoints/{endpoint_name}"
            )
        if self.endpoint_dns:
            return f"https://{self.endpoint_dns}/v1/{endpoint_name}:predict"

        # Attempt to auto-discover dedicatedEndpointDns from endpoint resource
        try:
            session = self._get_session()
            desc_url = f"https://{self.location}-aiplatform.googleapis.com/v1/{endpoint_name}"
            headers = {"X-Goog-User-Project": self.project_id}
            desc_resp = session.get(desc_url, headers=headers, timeout=5.0)
            if desc_resp.status_code == 200:
                data = desc_resp.json()
                dedicated_dns = data.get("dedicatedEndpointDns")
                if dedicated_dns:
                    self.endpoint_dns = dedicated_dns
                    return f"https://{self.endpoint_dns}/v1/{endpoint_name}:predict"
        except Exception:
            pass

        return f"https://{self.location}-aiplatform.googleapis.com/v1/{endpoint_name}:predict"

    def classify(
        self,
        claim_text: str,
        spec_quote: str,
        cut_sheet_quote: str,
    ) -> SeverityResult:
        """Call Vertex AI endpoint with prompt to classify severity."""
        try:
            session = self._get_session()
            prompt = load_severity_prompt()
            user_msg = (
                f"{prompt}\n\n"
                f"Discrepancy Claim:\n{claim_text}\n\n"
                f"Specification Requirement Quote:\n{spec_quote}\n\n"
                f"Submitted Document Quote:\n{cut_sheet_quote}\n\n"
                "Respond with exactly one token: HIGH, MEDIUM, or LOW."
            )
            content = f"<start_of_turn>user\n{user_msg}<end_of_turn>\n<start_of_turn>model\n"
            body = {
                "instances": [{"prompt": content}],
                "parameters": {
                    "temperature": 0.0,
                    "max_tokens": 50,
                },
            }
            url = self._get_predict_url()
            headers = {"X-Goog-User-Project": self.project_id}
            response = None
            timeout_error: Timeout | None = None
            for attempt in range(2):
                try:
                    response = session.post(
                        url,
                        json=body,
                        headers=headers,
                        timeout=self.timeout_seconds,
                    )
                except Timeout as error:
                    timeout_error = error
                    if attempt == 0:
                        continue
                    break
                if response.status_code == 200:
                    break
                if 500 <= response.status_code <= 599 and attempt == 0:
                    continue
                break

            if response is None or response.status_code != 200:
                if response is None and timeout_error is not None:
                    error_msg = f"Request timed out after {self.timeout_seconds:g} seconds"
                else:
                    status_str = str(response.status_code) if response is not None else "ERR"
                    error_text = (
                        response.text[:200].strip() if response is not None else "No response"
                    )
                    error_msg = f"HTTP {status_str}: {error_text}"
                logger.warning("Vertex endpoint classification returned error: %s", error_msg)
                return SeverityResult(
                    severity=Severity.UNCLASSIFIED,
                    model_id=None,
                    endpoint_label=self.endpoint_label,
                    status="fallback",
                    reason=error_msg,
                )

            data = response.json()
            predictions = data.get("predictions", [])
            if not predictions:
                return SeverityResult(
                    severity=Severity.UNCLASSIFIED,
                    model_id=None,
                    endpoint_label=self.endpoint_label,
                    status="fallback",
                    reason="Empty predictions list from Vertex endpoint",
                )

            raw_prediction = predictions[0]
            severity = _parse_severity_token(raw_prediction, prompt=content)
            # The served identifier if the endpoint reported one, and nothing in
            # its place if it did not. The configured label goes to its own
            # field, because calling it a model ID publishes a value that
            # nothing observed.
            return SeverityResult(
                severity=severity,
                model_id=_served_model_id(data),
                endpoint_label=self.endpoint_label,
                rationale="Classified via Vertex AI Model Garden Endpoint",
                status="classified",
                reason=None,
            )
        except Exception as error:
            error_detail = _extract_error_detail(error)
            logger.warning("Vertex endpoint severity classification failed: %s", error_detail)
            return SeverityResult(
                severity=Severity.UNCLASSIFIED,
                model_id=None,
                endpoint_label=self.endpoint_label,
                status="fallback",
                reason=error_detail,
            )


def classify_severity(
    finding: Finding | PersistedFinding,
    *,
    classifier: SeverityClassifier | None = None,
    client: Any = None,
    model_id: str = GEMMA_MODEL_ID,
    api_key: str | None = None,
    project_id: str | None = None,
    endpoint_resource_name: str | None = None,
    endpoint_dns: str | None = None,
) -> SeverityResult:
    """Classify the severity of a verified finding using Gemma.

    Failure mode: if the Gemma call fails for any reason (network, API, schema,
    missing key, endpoint error), this function returns SeverityResult with UNCLASSIFIED
    and None model_id and records status="fallback" and reason.
    Classification failure must never block an audit.
    """
    try:
        if isinstance(finding, PersistedFinding):
            claim_text = finding.claim_text
            spec_quote = finding.spec_quote.text
            cut_sheet_quote = finding.cut_sheet_quote.text
        elif isinstance(finding, Finding):
            claim_text = finding.claim_text
            if len(finding.quotes) >= 2:
                spec_quote = finding.quotes[0].text
                cut_sheet_quote = finding.quotes[1].text
            elif finding.quotes:
                spec_quote = finding.quotes[0].text
                cut_sheet_quote = ""
            else:
                return SeverityResult(
                    severity=Severity.UNCLASSIFIED,
                    model_id=None,
                    status="fallback",
                    reason="Finding has no quotes",
                )
        else:
            return SeverityResult(
                severity=Severity.UNCLASSIFIED,
                model_id=None,
                status="fallback",
                reason="Unsupported finding type",
            )

        if classifier is not None:
            return classifier.classify(claim_text, spec_quote, cut_sheet_quote)

        endpoint = endpoint_resource_name or os.environ.get("SPECGUARD_GEMMA_ENDPOINT")
        if endpoint == "disabled":
            return SeverityResult(
                severity=Severity.UNCLASSIFIED,
                model_id=None,
                status="fallback",
                reason="severity endpoint not deployed outside demo windows",
            )
        if endpoint:
            endpoint_classifier = VertexEndpointSeverityClassifier(
                endpoint_resource_name=endpoint,
                endpoint_dns=endpoint_dns,
                project_id=project_id,
            )
            return endpoint_classifier.classify(claim_text, spec_quote, cut_sheet_quote)

        active_classifier = GemmaSeverityClassifier(
            model_id=model_id,
            client=client,
            api_key=api_key,
            project_id=project_id,
        )
        return active_classifier.classify(claim_text, spec_quote, cut_sheet_quote)
    except Exception as error:
        error_detail = _extract_error_detail(error)
        logger.warning("classify_severity encountered an unhandled error: %s", error_detail)
        return SeverityResult(
            severity=Severity.UNCLASSIFIED,
            model_id=None,
            status="fallback",
            reason=error_detail,
        )
