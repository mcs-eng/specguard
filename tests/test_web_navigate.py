"""The agent mode and the tool-call receipt on the persisted run surface.

These tests reuse the in-process fakes in :mod:`tests.test_web`, so no test
here reaches Firestore, Cloud Storage, or a model.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime

import pytest

from specguard.models import AgentMode, AuditRunSummary, DocumentRole, ModelToolCall
from specguard.web.app import WebSettings
from tests.test_web import RUN_ID, _client

NAVIGATE_CALLS = [
    ModelToolCall(
        turn_index=0,
        tool_name="extract_pdf_text",
        document_role=DocumentRole.SPECIFICATION,
        page_number=17,
    ),
    ModelToolCall(
        turn_index=0,
        tool_name="verify_quote",
        document_role=DocumentRole.SPECIFICATION,
        page_number=17,
        response_verified=False,
    ),
    ModelToolCall(
        turn_index=0,
        tool_name="extract_pdf_text",
        document_role=DocumentRole.SPECIFICATION,
        page_number=41,
        response_error_code="page_budget_exhausted",
    ),
]


def _navigate_summary() -> AuditRunSummary:
    return AuditRunSummary(
        run_id=RUN_ID,
        claims_made=1,
        rejected=0,
        retried=0,
        findings_persisted=0,
        agent_mode=AgentMode.NAVIGATE,
        model_tool_calls=NAVIGATE_CALLS,
    )


def _stored_run(summary: AuditRunSummary) -> dict[str, object]:
    """Run one sample audit through the fake runner and read the stored record."""
    client, repository, _, _ = _client(summary=summary)
    response = client.post("/sample/caldra", follow_redirects=False)
    run_id = response.headers["location"].removeprefix("/runs/")
    return repository.runs[run_id]


# --- 1. What the run record stores ---------------------------------------


def test_a_navigate_run_persists_its_mode_and_every_recorded_tool_call() -> None:
    stored = _stored_run(_navigate_summary())

    summary = stored["summary"]
    assert summary["agent_mode"] == "navigate"
    assert [call["tool_name"] for call in summary["model_tool_calls"]] == [
        "extract_pdf_text",
        "verify_quote",
        "extract_pdf_text",
    ]
    assert summary["model_tool_calls"][0]["page_number"] == 17
    assert summary["model_tool_calls"][1]["response_verified"] is False
    assert summary["model_tool_calls"][2]["response_error_code"] == "page_budget_exhausted"


def test_a_full_text_run_persists_the_default_mode_and_an_empty_receipt() -> None:
    stored = _stored_run(
        AuditRunSummary(run_id=RUN_ID, claims_made=0, rejected=0, retried=0, findings_persisted=0)
    )

    assert stored["summary"]["agent_mode"] == "full_text"
    assert stored["summary"]["model_tool_calls"] == []


# --- 2. What the run page shows ------------------------------------------


def test_the_run_page_lists_the_pages_the_model_read() -> None:
    client, repository, _, _ = _client()
    repository.runs[RUN_ID] = {
        "run_id": RUN_ID,
        "created_at": datetime.now(UTC),
        "status": "COMPLETED",
        "summary": {
            "claims_made": 1,
            "rejected": 0,
            "retried": 0,
            "findings_persisted": 0,
            "agent_mode": "navigate",
            "model_tool_calls": [call.model_dump(mode="json") for call in NAVIGATE_CALLS],
        },
        "documents": {},
        "rfi": None,
    }

    response = client.get(f"/runs/{RUN_ID}")

    assert response.status_code == 200
    assert "Pages the model read" in response.text
    assert "extract_pdf_text" in response.text
    assert "verify_quote" in response.text
    assert ">17<" in response.text
    assert "quote not found" in response.text
    assert "page_budget_exhausted" in response.text
    assert "The model reads; the runtime writes." in response.text


def test_the_run_page_says_a_full_text_run_recorded_no_tool_call() -> None:
    client, repository, _, _ = _client()
    repository.runs[RUN_ID] = {
        "run_id": RUN_ID,
        "created_at": datetime.now(UTC),
        "status": "COMPLETED",
        "summary": {
            "claims_made": 0,
            "rejected": 0,
            "retried": 0,
            "findings_persisted": 0,
            "agent_mode": "full_text",
            "model_tool_calls": [],
        },
        "documents": {},
        "rfi": None,
    }

    response = client.get(f"/runs/{RUN_ID}")

    assert response.status_code == 200
    assert "This run recorded no model-initiated tool call." in response.text


def test_an_older_run_without_the_receipt_still_renders() -> None:
    """A run stored before this phase has no such field and must not 500."""
    client, repository, _, _ = _client()
    repository.runs[RUN_ID] = {
        "run_id": RUN_ID,
        "created_at": datetime.now(UTC),
        "status": "COMPLETED",
        "summary": {"claims_made": 0, "rejected": 0, "retried": 0, "findings_persisted": 0},
        "documents": {},
        "rfi": None,
    }

    response = client.get(f"/runs/{RUN_ID}")

    assert response.status_code == 200
    assert "This run recorded no model-initiated tool call." in response.text
    assert "Agent mode:" not in response.text


# --- 3. What the JSON export carries -------------------------------------


def test_the_json_export_carries_the_mode_and_the_tool_call_receipt() -> None:
    client, repository, _, _ = _client()
    repository.runs[RUN_ID] = {
        "run_id": RUN_ID,
        "created_at": datetime.now(UTC),
        "status": "COMPLETED",
        "summary": {
            "claims_made": 1,
            "rejected": 0,
            "retried": 0,
            "findings_persisted": 0,
            "agent_mode": "navigate",
            "model_tool_calls": [call.model_dump(mode="json") for call in NAVIGATE_CALLS],
        },
        "documents": {},
        "rfi": None,
    }

    payload = client.get(f"/runs/{RUN_ID}/export.json").json()

    assert payload["summary"]["agent_mode"] == "navigate"
    assert payload["summary"]["model_tool_calls"] == [
        {
            "turn_index": 0,
            "tool_name": "extract_pdf_text",
            "document_role": "specification",
            "page_number": 17,
            "response_verified": None,
            "response_error_code": None,
        },
        {
            "turn_index": 0,
            "tool_name": "verify_quote",
            "document_role": "specification",
            "page_number": 17,
            "response_verified": False,
            "response_error_code": None,
        },
        {
            "turn_index": 0,
            "tool_name": "extract_pdf_text",
            "document_role": "specification",
            "page_number": 41,
            "response_verified": None,
            "response_error_code": "page_budget_exhausted",
        },
    ]


def test_the_json_export_of_an_older_run_reports_an_empty_receipt() -> None:
    client, repository, _, _ = _client()
    repository.runs[RUN_ID] = {
        "run_id": RUN_ID,
        "created_at": datetime.now(UTC),
        "status": "COMPLETED",
        "summary": {"claims_made": 0, "rejected": 0, "retried": 0, "findings_persisted": 0},
        "documents": {},
        "rfi": None,
    }

    payload = client.get(f"/runs/{RUN_ID}/export.json").json()

    assert payload["summary"]["agent_mode"] is None
    assert payload["summary"]["model_tool_calls"] == []


# --- 4. What the deployment reads ----------------------------------------


@pytest.mark.parametrize(
    ("configured", "expected"),
    [
        (None, AgentMode.FULL_TEXT),
        ("full_text", AgentMode.FULL_TEXT),
        ("navigate", AgentMode.NAVIGATE),
    ],
)
def test_the_web_settings_read_the_agent_mode_from_the_environment(
    monkeypatch: pytest.MonkeyPatch, configured: str | None, expected: AgentMode
) -> None:
    if configured is None:
        monkeypatch.delenv("SPECGUARD_AGENT_MODE", raising=False)
    else:
        monkeypatch.setenv("SPECGUARD_AGENT_MODE", configured)

    assert WebSettings.from_environment().agent_mode is expected


def test_an_unknown_configured_mode_stops_the_settings_from_loading(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SPECGUARD_AGENT_MODE", "navigation")

    with pytest.raises(ValueError, match="full_text, navigate"):
        WebSettings.from_environment()

    assert os.environ["SPECGUARD_AGENT_MODE"] == "navigation"
