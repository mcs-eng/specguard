"""The web runner wires a request into the existing audit runtime safely."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from specguard.models import AgentMode
from specguard.web import runtime as web_runtime


class FakeFirestoreClient:
    def __init__(self, *, project: str) -> None:
        self.project = project
        self.closed = False

    def close(self) -> None:
        self.closed = True


def _patch_runner_dependencies(monkeypatch: pytest.MonkeyPatch, client: FakeFirestoreClient):
    monkeypatch.setattr(web_runtime.firestore, "Client", lambda *, project: client)
    captured: dict[str, object] = {}

    class FakeTools:
        def __init__(self, **kwargs: object) -> None:
            captured["tools_kwargs"] = kwargs

    class FakeGenerator:
        def __init__(self, agent: object, *, run_id: str) -> None:
            captured["generator"] = (agent, run_id)

    agent = object()
    monkeypatch.setattr(web_runtime, "AuditTools", FakeTools)
    monkeypatch.setattr(web_runtime, "create_adk_agent", lambda tools, **kwargs: agent)
    monkeypatch.setattr(web_runtime, "AdkClaimGenerator", FakeGenerator)
    return captured


def test_google_runner_passes_the_assigned_identity_and_closes_firestore(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    client = FakeFirestoreClient(project="demo-project")
    captured = _patch_runner_dependencies(monkeypatch, client)
    expected = object()

    class FakeRuntime:
        def __init__(self, **kwargs: object) -> None:
            captured["runtime_kwargs"] = kwargs

        async def run(self) -> object:
            return expected

    monkeypatch.setattr(web_runtime, "AuditRuntime", FakeRuntime)
    runner = web_runtime.GoogleAuditRunner(project_id="demo-project", agent_mode=AgentMode.NAVIGATE)

    result = asyncio.run(
        runner.run_audit(
            spec_path=tmp_path / "spec.pdf",
            cut_sheet_path=tmp_path / "submittal.pdf",
            run_id="run-1",
            submittal_number="S-042",
            output_directory=tmp_path,
        )
    )

    assert result is expected
    assert client.closed is True
    assert captured["tools_kwargs"]["submittal_number"] == "S-042"
    assert captured["runtime_kwargs"]["agent_mode"] is AgentMode.NAVIGATE
    assert captured["generator"][1] == "run-1"


def test_google_runner_closes_firestore_when_the_audit_raises(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    client = FakeFirestoreClient(project="demo-project")
    _patch_runner_dependencies(monkeypatch, client)

    class FailingRuntime:
        def __init__(self, **kwargs: object) -> None:
            pass

        async def run(self) -> object:
            raise RuntimeError("audit failed")

    monkeypatch.setattr(web_runtime, "AuditRuntime", FailingRuntime)
    runner = web_runtime.GoogleAuditRunner(project_id="demo-project")

    with pytest.raises(RuntimeError, match="audit failed"):
        asyncio.run(
            runner.run_audit(
                spec_path=tmp_path / "spec.pdf",
                cut_sheet_path=tmp_path / "submittal.pdf",
                run_id="run-1",
                submittal_number="S-001",
                output_directory=tmp_path,
            )
        )

    assert client.closed is True
