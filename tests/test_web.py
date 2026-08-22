"""Network-free route tests for the public findings page."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from specguard import gate
from specguard.models import AuditRunSummary, DocumentRole, QuarantinedDocument, RunQuarantine
from specguard.web.app import (
    GATE_CHECKS_PER_IP_HOUR,
    MAX_UPLOAD_BYTES,
    QUOTE_CONTEXT_CACHE_SIZE,
    SAMPLE_RUNS_PER_UTC_DAY,
    SECURITY_HEADERS,
    WebServices,
    WebSettings,
    create_app,
)
from specguard.web.storage import StoredObject

RUN_ID = "web-run-1234"
SPEC_BYTES = b"%PDF-1.7\nfictional specification\n"
CUT_SHEET_BYTES = b"%PDF-1.7\nfictional cut sheet\n"
RFI_BYTES = b"%PDF-1.7\nfictional rfi\n"
EXTRA_BYTES = b"%PDF-1.7\nfictional extra part\n"


class FakeRunRepository:
    """Small in-memory run repository for route tests."""

    def __init__(self, *, failing_create_calls: frozenset[int] = frozenset()) -> None:
        self.runs: dict[str, dict[str, Any]] = {}
        self.findings: dict[str, list[dict[str, Any]]] = {}
        self.rejections: dict[str, list[dict[str, Any]]] = {}
        self.integrity_records: dict[str, list[dict[str, Any]]] = {}
        self.failing_create_calls = failing_create_calls
        self.create_calls = 0
        self.sample_runs_by_day: dict[str, int] = {}
        self.sample_runs_by_hour_ip: dict[tuple[str, str], int] = {}
        self.gate_checks_by_hour_ip: dict[tuple[str, str], int] = {}
        self.submission_tokens: dict[str, str] = {}

    def create_run(self, run: Mapping[str, Any]) -> None:
        self.create_calls += 1
        if self.create_calls in self.failing_create_calls:
            raise RuntimeError("firestore write failed")
        self.runs[str(run["run_id"])] = dict(run)

    def create_upload_run(self, run: Mapping[str, Any], submission_token: str) -> str | None:
        existing_run_id = self.submission_tokens.get(submission_token)
        if existing_run_id is not None:
            return existing_run_id
        self.create_run(run)
        self.submission_tokens[submission_token] = str(run["run_id"])
        return None

    def get_submission_run_id(self, submission_token: str) -> str | None:
        return self.submission_tokens.get(submission_token)

    def reserve_sample_run(
        self,
        *,
        day: str,
        hour: str,
        client_ip: str,
        hourly_limit: int,
        daily_limit: int,
    ) -> bool:
        daily_current = self.sample_runs_by_day.get(day, 0)
        bucket = (hour, client_ip)
        hourly_current = self.sample_runs_by_hour_ip.get(bucket, 0)
        if daily_current >= daily_limit or hourly_current >= hourly_limit:
            return False
        self.sample_runs_by_day[day] = daily_current + 1
        self.sample_runs_by_hour_ip[bucket] = hourly_current + 1
        return True

    def reserve_gate_check(self, *, hour: str, client_ip: str, hourly_limit: int) -> bool:
        bucket = (hour, client_ip)
        current = self.gate_checks_by_hour_ip.get(bucket, 0)
        if current >= hourly_limit:
            return False
        self.gate_checks_by_hour_ip[bucket] = current + 1
        return True

    def list_runs(self, limit: int = 20) -> list[dict[str, Any]]:
        return list(reversed(list(self.runs.values())))[:limit]

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        return self.runs.get(run_id)

    def get_findings(self, run_id: str) -> list[dict[str, Any]]:
        return self.findings.get(run_id, [])

    def get_rejections(self, run_id: str) -> list[dict[str, Any]]:
        return self.rejections.get(run_id, [])

    def get_integrity_records(self, run_id: str) -> list[dict[str, Any]]:
        return self.integrity_records.get(run_id, [])


class FakeObjectStorage:
    """In-memory durable objects with the same hashes as the production adapter."""

    def __init__(self, *, fail_upload_at: int | None = None) -> None:
        self.objects: dict[str, bytes] = {}
        self.fail_upload_at = fail_upload_at
        self.upload_count = 0
        self.download_count = 0

    def upload_bytes(self, object_name: str, data: bytes, content_type: str) -> StoredObject:
        self.upload_count += 1
        if self.upload_count == self.fail_upload_at:
            raise RuntimeError("storage write failed")
        self.objects[object_name] = data
        return StoredObject(
            object_name=object_name,
            sha256=hashlib.sha256(data).hexdigest(),
            content_type=content_type,
        )

    def download_bytes(self, object_name: str) -> bytes:
        self.download_count += 1
        return self.objects[object_name]

    def delete_object(self, object_name: str) -> None:
        self.objects.pop(object_name, None)


class FakeAuditRunner:
    """Audit runner that writes an RFI without network or model access."""

    def __init__(
        self, *, summary: AuditRunSummary | None = None, failure: Exception | None = None
    ) -> None:
        self.summary = summary
        self.failure = failure
        self.calls: list[tuple[bytes, bytes, str]] = []

    async def run_audit(
        self,
        *,
        spec_path: Path,
        cut_sheet_path: Path,
        run_id: str,
        output_directory: Path,
    ) -> AuditRunSummary:
        self.calls.append((spec_path.read_bytes(), cut_sheet_path.read_bytes(), run_id))
        if self.failure is not None:
            raise self.failure
        if self.summary is not None:
            return self.summary.model_copy(update={"run_id": run_id})
        output_directory.mkdir(parents=True, exist_ok=True)
        rfi_path = output_directory / "rfi.pdf"
        rfi_path.write_bytes(RFI_BYTES)
        return AuditRunSummary(
            run_id=run_id,
            claims_made=1,
            rejected=1,
            retried=1,
            findings_persisted=1,
            rfi_path=str(rfi_path),
        )


def _client(
    *,
    summary: AuditRunSummary | None = None,
    failure: Exception | None = None,
    fail_upload_at: int | None = None,
    failing_create_calls: frozenset[int] = frozenset(),
) -> tuple[TestClient, FakeRunRepository, FakeObjectStorage, FakeAuditRunner]:
    repository = FakeRunRepository(failing_create_calls=failing_create_calls)
    storage = FakeObjectStorage(fail_upload_at=fail_upload_at)
    runner = FakeAuditRunner(summary=summary, failure=failure)
    app = create_app(
        WebServices(
            settings=WebSettings(
                project_id="test-project",
                bucket_name="test-runs",
                demo_passphrase="test-passphrase",
            ),
            repository=repository,
            storage=storage,
            audit_runner=runner,
            audit_slots=asyncio.Semaphore(2),
        )
    )
    return TestClient(app), repository, storage, runner


def _files(
    *, spec_type: str = "application/pdf", cut_sheet_type: str = "application/pdf"
) -> dict[str, tuple[str, bytes, str]]:
    return {
        "spec_pdf": ("specification.pdf", SPEC_BYTES, spec_type),
        "cut_sheet_pdf": ("cut-sheet.pdf", CUT_SHEET_BYTES, cut_sheet_type),
    }


def test_findings_page_shows_the_upload_form_and_no_runs() -> None:
    client, _, _, _ = _client()

    response = client.get("/")

    assert response.status_code == 200
    assert "Specification PDF" in response.text
    assert "Cut-sheet PDF" in response.text
    assert "audit-submit" in response.text
    assert "Run a sample audit" in response.text
    assert "Caldra (compliant)" in response.text
    assert "Veylan 208V" in response.text
    assert "Torven 70 deg C" in response.text
    assert "Veylan altered (integrity screen)" in response.text
    assert "No audit runs are stored yet." in response.text


@pytest.mark.parametrize(
    "case_id",
    ["caldra", "veylan-208v", "torven-70c", "veylan-altered"],
)
def test_sample_audit_routes_run_committed_fixtures_without_a_passphrase(case_id: str) -> None:
    client, repository, storage, runner = _client()

    response = client.post(f"/sample/{case_id}", follow_redirects=False)

    assert response.status_code == 303
    run_id = response.headers["location"].removeprefix("/runs/")
    assert repository.runs[run_id]["source"] == "sample"
    assert runner.calls[0][0].startswith(b"%PDF-")
    assert runner.calls[0][1].startswith(b"%PDF-")
    assert f"{run_id}/specification.pdf" in storage.objects
    assert f"{run_id}/submitted-document.pdf" in storage.objects


def test_sample_audit_per_ip_limit_returns_a_plain_429_page() -> None:
    client, repository, _, runner = _client()

    for _ in range(6):
        response = client.post("/sample/caldra", follow_redirects=False)
        assert response.status_code == 303

    response = client.post("/sample/caldra", follow_redirects=False)

    assert response.status_code == 429
    assert "The sample audit limit is reached. Try again later." in response.text
    assert len(runner.calls) == 6
    assert sum(repository.sample_runs_by_day.values()) == 6


def test_sample_audit_per_ip_limit_reads_the_address_cloud_run_appended() -> None:
    client, _, _, runner = _client()
    spoofed = {"x-forwarded-for": "203.0.113.9, 198.51.100.4"}

    for _ in range(6):
        response = client.post("/sample/caldra", headers=spoofed, follow_redirects=False)
        assert response.status_code == 303

    blocked = client.post("/sample/caldra", headers=spoofed, follow_redirects=False)
    other_client = client.post(
        "/sample/caldra",
        headers={"x-forwarded-for": "203.0.113.9, 198.51.100.5"},
        follow_redirects=False,
    )

    assert blocked.status_code == 429
    assert other_client.status_code == 303
    assert len(runner.calls) == 7


def test_sample_audit_per_ip_limit_ignores_a_caller_supplied_prefix() -> None:
    client, _, _, runner = _client()

    for index in range(6):
        response = client.post(
            "/sample/caldra",
            headers={"x-forwarded-for": f"10.0.0.{index}, 198.51.100.4"},
            follow_redirects=False,
        )
        assert response.status_code == 303

    response = client.post(
        "/sample/caldra",
        headers={"x-forwarded-for": "10.0.0.99, 198.51.100.4"},
        follow_redirects=False,
    )

    assert response.status_code == 429
    assert len(runner.calls) == 6


def test_sample_audit_global_limit_returns_a_plain_429_page() -> None:
    client, repository, _, runner = _client()
    today = datetime.now(UTC).date().isoformat()
    repository.sample_runs_by_day[today] = SAMPLE_RUNS_PER_UTC_DAY

    response = client.post("/sample/caldra", follow_redirects=False)

    assert response.status_code == 429
    assert "The sample audit limit is reached. Try again later." in response.text
    assert runner.calls == []


def test_findings_page_shows_running_states_that_block_repeat_submits() -> None:
    client, _, _, _ = _client()

    response = client.get("/")
    script = client.get("/static/index.js")

    assert response.status_code == 200
    assert script.status_code == 200
    assert "Audit running. Do not submit again." in response.text
    assert "A repeated submit returns the first run." in response.text
    assert "#audit-submit:disabled" in response.text
    assert "auditSubmit.disabled = true" in script.text
    assert "if (submitting) { event.preventDefault(); return; }" in script.text
    assert "Sample audit running. Do not submit again." in response.text
    assert "All sample buttons are disabled until this run opens." in response.text
    assert ".sample-grid button:disabled" in response.text
    assert "for (const button of sampleButtons) button.disabled = true;" in script.text
    assert "if (sampleSubmitting) { event.preventDefault(); return; }" in script.text


def test_findings_page_confirms_each_chosen_file_before_the_run_starts() -> None:
    client, _, _, _ = _client()

    response = client.get("/")
    script = client.get("/static/index.js")

    assert response.status_code == 200
    assert script.status_code == 200
    assert "No file chosen" in response.text
    assert "Choose PDF" in response.text
    assert "Replace PDF" in script.text
    assert ".drop-zone[data-filled]" in response.text
    assert "This file is larger than 5 MB." in script.text


def test_findings_page_gives_the_submit_button_press_feedback() -> None:
    client, _, _, _ = _client()

    response = client.get("/")

    assert response.status_code == 200
    assert "#audit-submit:active:not(:disabled) { transform: scale(0.97); }" in response.text


def test_findings_page_respects_reduced_motion_and_gates_hover() -> None:
    client, _, _, _ = _client()

    response = client.get("/")

    assert response.status_code == 200
    assert "@media (prefers-reduced-motion: reduce)" in response.text
    assert "@media (hover: hover) and (pointer: fine)" in response.text
    assert "transition: all" not in response.text
    assert "scale(0)" not in response.text


def test_recent_runs_shorten_the_run_identifier_and_keep_it_reachable() -> None:
    client, repository, _, _ = _client()
    repository.runs[RUN_ID] = {
        "run_id": RUN_ID,
        "created_at": datetime(2026, 8, 21, tzinfo=UTC),
        "status": "COMPLETED",
        "source": "sample",
        "summary": {"claims_made": 1, "rejected": 0, "retried": 0, "findings_persisted": 1},
        "documents": {},
        "rfi": None,
    }

    response = client.get("/")

    assert response.status_code == 200
    assert f'href="/runs/{RUN_ID}" title="{RUN_ID}"' in response.text
    assert f"<code>{RUN_ID[:8]}</code>" in response.text
    assert '<time datetime="2026-08-21T00:00:00+00:00">' in response.text
    assert "SAMPLE" in response.text


def test_findings_page_promises_no_completion_time_or_progress_value() -> None:
    client, _, _, _ = _client()

    response = client.get("/")

    assert response.status_code == 200
    assert "The server reports no progress, so this page shows no bar and no percentage." in (
        response.text
    )
    assert 'role="progressbar"' not in response.text
    assert "<progress" not in response.text


def test_audit_rejects_a_missing_or_wrong_passphrase() -> None:
    client, _, _, runner = _client()

    response = client.post(
        "/audit",
        data={"demo_passphrase": "wrong", "submission_token": "test-token"},
        files=_files(),
    )

    assert response.status_code == 403
    assert "The demo passphrase is required." in response.text
    assert runner.calls == []


def test_audit_rejects_a_non_ascii_wrong_passphrase() -> None:
    client, _, _, runner = _client()

    response = client.post(
        "/audit", data={"demo_passphrase": "é", "submission_token": "test-token"}, files=_files()
    )

    assert response.status_code == 403
    assert "The demo passphrase is required." in response.text
    assert runner.calls == []


def test_audit_rejects_an_unauthorized_oversized_upload_before_audit() -> None:
    client, _, _, runner = _client()
    oversized_pdf = b"%PDF-1.7\n" + b"x" * MAX_UPLOAD_BYTES
    files = {
        "spec_pdf": ("specification.pdf", oversized_pdf, "application/pdf"),
        "cut_sheet_pdf": ("cut-sheet.pdf", CUT_SHEET_BYTES, "application/pdf"),
    }

    response = client.post(
        "/audit", data={"demo_passphrase": "wrong", "submission_token": "test-token"}, files=files
    )

    assert response.status_code == 403
    assert runner.calls == []


def test_audit_rejects_a_non_pdf_upload() -> None:
    client, _, _, runner = _client()

    response = client.post(
        "/audit",
        data={"demo_passphrase": "test-passphrase", "submission_token": "test-token"},
        files=_files(spec_type="text/plain"),
    )

    assert response.status_code == 400
    assert "application/pdf content type" in response.text
    assert runner.calls == []


def test_audit_rejects_an_oversized_upload() -> None:
    client, _, _, runner = _client()
    oversized_pdf = b"%PDF-1.7\n" + b"x" * MAX_UPLOAD_BYTES
    files = {
        "spec_pdf": ("specification.pdf", oversized_pdf, "application/pdf"),
        "cut_sheet_pdf": ("cut-sheet.pdf", CUT_SHEET_BYTES, "application/pdf"),
    }

    response = client.post(
        "/audit",
        data={"demo_passphrase": "test-passphrase", "submission_token": "test-token"},
        files=files,
    )

    assert response.status_code == 400
    assert "must not exceed 5 MB" in response.text
    assert runner.calls == []


def test_audit_stores_run_scoped_objects_and_redirects_to_the_run() -> None:
    client, repository, storage, runner = _client()

    response = client.post(
        "/audit",
        data={"demo_passphrase": "test-passphrase", "submission_token": "test-token"},
        files=_files(),
        follow_redirects=False,
    )

    assert response.status_code == 303
    run_id = response.headers["location"].removeprefix("/runs/")
    run = repository.runs[run_id]
    assert runner.calls == [(SPEC_BYTES, CUT_SHEET_BYTES, run_id)]
    assert storage.objects[f"{run_id}/specification.pdf"] == SPEC_BYTES
    assert storage.objects[f"{run_id}/submitted-document.pdf"] == CUT_SHEET_BYTES
    assert storage.objects[f"{run_id}/rfi.pdf"] == RFI_BYTES
    assert run["documents"]["specification"]["sha256"] == hashlib.sha256(SPEC_BYTES).hexdigest()
    assert (
        run["documents"]["submitted_document"]["sha256"]
        == hashlib.sha256(CUT_SHEET_BYTES).hexdigest()
    )
    assert run["rfi"]["sha256"] == hashlib.sha256(RFI_BYTES).hexdigest()
    assert "rfi_path" not in str(run)
    assert run["source"] == "upload"


def test_audit_cleans_up_objects_when_uploads_cannot_be_recorded() -> None:
    client, repository, storage, runner = _client(fail_upload_at=2)

    response = client.post(
        "/audit",
        data={"demo_passphrase": "test-passphrase", "submission_token": "test-token"},
        files=_files(),
    )

    assert response.status_code == 500
    assert repository.runs == {}
    assert storage.objects == {}
    assert runner.calls == []
    assert "text/html" in response.headers["content-type"]
    assert "The audit did not complete." in response.text
    assert "Open run" not in response.text


def test_audit_records_a_failed_run_after_durable_inputs_are_stored() -> None:
    client, repository, storage, runner = _client(failure=RuntimeError("model failed"))

    response = client.post(
        "/audit",
        data={"demo_passphrase": "test-passphrase", "submission_token": "test-token"},
        files=_files(),
    )

    assert response.status_code == 500
    assert len(runner.calls) == 1
    run_id, run = next(iter(repository.runs.items()))
    assert run["status"] == "FAILED"
    assert run["summary"]["failure"] == "audit_failed"
    assert storage.objects[f"{run_id}/specification.pdf"] == SPEC_BYTES
    assert storage.objects[f"{run_id}/submitted-document.pdf"] == CUT_SHEET_BYTES
    assert "text/html" in response.headers["content-type"]
    assert "The audit did not complete." in response.text
    assert f'href="/runs/{run_id}"' in response.text
    detail = client.get(f"/runs/{run_id}")
    assert detail.status_code == 200
    assert "The audit did not complete." in detail.text


def test_audit_rejects_a_submission_carrying_an_unexpected_file() -> None:
    client, repository, storage, runner = _client()
    files = [
        ("spec_pdf", ("specification.pdf", SPEC_BYTES, "application/pdf")),
        ("cut_sheet_pdf", ("cut-sheet.pdf", CUT_SHEET_BYTES, "application/pdf")),
        ("extra_pdf", ("extra.pdf", EXTRA_BYTES, "application/pdf")),
    ]

    response = client.post(
        "/audit",
        data={"demo_passphrase": "test-passphrase", "submission_token": "test-token"},
        files=files,
    )

    assert response.status_code == 400
    assert "unexpected file" in response.text
    assert runner.calls == []
    assert repository.runs == {}
    assert storage.objects == {}


def test_audit_rejects_a_submission_carrying_two_files_for_one_document() -> None:
    client, repository, storage, runner = _client()
    files = [
        ("spec_pdf", ("specification.pdf", SPEC_BYTES, "application/pdf")),
        ("spec_pdf", ("second.pdf", EXTRA_BYTES, "application/pdf")),
        ("cut_sheet_pdf", ("cut-sheet.pdf", CUT_SHEET_BYTES, "application/pdf")),
    ]

    response = client.post(
        "/audit",
        data={"demo_passphrase": "test-passphrase", "submission_token": "test-token"},
        files=files,
    )

    assert response.status_code == 400
    assert "more than one file for the same document" in response.text
    assert runner.calls == []
    assert repository.runs == {}
    assert storage.objects == {}


def test_audit_deletes_uploaded_objects_when_the_run_record_cannot_be_written() -> None:
    client, repository, storage, runner = _client(failing_create_calls=frozenset({1}))

    response = client.post(
        "/audit",
        data={"demo_passphrase": "test-passphrase", "submission_token": "test-token"},
        files=_files(),
    )

    assert response.status_code == 500
    assert repository.runs == {}
    assert storage.objects == {}
    assert runner.calls == []
    assert "The audit did not complete." in response.text
    assert "Open run" not in response.text


def test_audit_keeps_recorded_objects_when_the_failed_write_cannot_land() -> None:
    client, repository, storage, runner = _client(
        failure=RuntimeError("model failed"), failing_create_calls=frozenset({2, 3})
    )

    response = client.post(
        "/audit",
        data={"demo_passphrase": "test-passphrase", "submission_token": "test-token"},
        files=_files(),
    )

    assert response.status_code == 500
    assert len(runner.calls) == 1
    run_id, run = next(iter(repository.runs.items()))
    assert run["status"] == "RUNNING"
    assert repository.create_calls == 3
    assert storage.objects[f"{run_id}/specification.pdf"] == SPEC_BYTES
    assert storage.objects[f"{run_id}/submitted-document.pdf"] == CUT_SHEET_BYTES
    assert f'href="/runs/{run_id}"' in response.text


def test_run_view_tells_a_reviewer_that_a_running_run_has_not_finished() -> None:
    client, repository, _, _ = _client()
    repository.runs[RUN_ID] = {
        "run_id": RUN_ID,
        "created_at": datetime.now(UTC),
        "status": "RUNNING",
        "summary": {"claims_made": 0, "rejected": 0, "retried": 0, "findings_persisted": 0},
        "documents": {},
        "rfi": None,
    }

    response = client.get(f"/runs/{RUN_ID}")

    assert response.status_code == 200
    assert "This run has not finished." in response.text
    assert "The server reports no progress while the model step runs." in response.text


def test_deploy_script_limits_cloud_run_request_concurrency() -> None:
    """Pin both halves of the aggregate concurrency number the README states.

    Request concurrency alone does not bound the service. Two concurrent
    requests per instance across two instances is four concurrent audits, so
    the instance cap is part of the documented claim, not a cost setting.
    """
    script = (Path(__file__).parents[1] / "deploy-specguard.ps1").read_text(encoding="utf-8")

    assert '"--concurrency"\n    "2"' in script
    assert '"--max-instances"\n    "1"' in script
    assert "SPECGUARD_GEMMA_ENDPOINT=disabled" in script


def test_deploy_image_bundles_sample_fixture_pdfs() -> None:
    dockerfile = (Path(__file__).parents[1] / "Dockerfile").read_text(encoding="utf-8")

    assert "COPY fixtures/*.pdf ./fixtures/" in dockerfile


def test_run_view_renders_findings_rejections_and_hidden_integrity_text() -> None:
    client, repository, storage, _ = _client()
    repository.runs[RUN_ID] = {
        "run_id": RUN_ID,
        "created_at": datetime(2026, 8, 21, tzinfo=UTC),
        "status": "QUARANTINED",
        "source": "sample",
        "summary": {
            "claims_made": 1,
            "rejected": 1,
            "retried": 1,
            "findings_persisted": 1,
            "quarantine": {"reason": "text_layer_integrity_screen"},
        },
        "documents": {
            "specification": {"object_name": f"{RUN_ID}/specification.pdf", "sha256": "ab" * 32},
            "submitted_document": {
                "object_name": f"{RUN_ID}/submitted-document.pdf",
                "sha256": "cd" * 32,
            },
        },
        "rfi": {"object_name": f"{RUN_ID}/rfi.pdf", "sha256": "ef" * 32},
    }
    repository.findings[RUN_ID] = [
        {
            "claim_text": "The submitted voltage conflicts with the requirement.",
            "spec_quote": {"page_number": 3, "text": "Provide 480V."},
            "cut_sheet_quote": {"page_number": 1, "text": "Nominal system: 208V."},
            "severity": "unclassified",
        }
    ]
    repository.rejections[RUN_ID] = [
        {"claim_text": "Unsupported claim", "reason": "quote_not_found_on_cited_page"}
    ]
    repository.integrity_records[RUN_ID] = [
        {
            "document_role": "submitted_document",
            "flagged_pages": [1],
            "document_sha256": "cd" * 32,
            "hidden_spans": [
                {
                    "page_number": 1,
                    "text": "Human-only hidden span.",
                    "font": "Helvetica",
                    "size": 9,
                }
            ],
        }
    ]
    storage.objects[f"{RUN_ID}/rfi.pdf"] = RFI_BYTES

    response = client.get(f"/runs/{RUN_ID}")

    assert response.status_code == 200
    assert "VERIFIED" in response.text
    assert "REJECTED" in response.text
    assert "QUARANTINED" in response.text
    assert "The submitted voltage conflicts with the requirement." in response.text
    assert "quote_not_found_on_cited_page" in response.text
    assert "Human-only hidden span." in response.text
    assert "Open the RFI draft PDF" in response.text
    assert "The text-layer integrity screen stopped this run." in response.text
    assert "text_layer_integrity_screen" in response.text
    assert "Specification page 3" in response.text
    assert "Submitted page 1" in response.text
    assert "SAMPLE" in response.text


def test_rfi_route_serves_durable_pdf_bytes() -> None:
    client, repository, storage, _ = _client()
    repository.runs[RUN_ID] = {
        "run_id": RUN_ID,
        "rfi": {"object_name": f"{RUN_ID}/rfi.pdf", "sha256": "ef" * 32},
    }
    storage.objects[f"{RUN_ID}/rfi.pdf"] = RFI_BYTES

    response = client.get(f"/runs/{RUN_ID}/rfi.pdf")

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert response.headers["content-disposition"] == f'inline; filename="rfi-{RUN_ID}.pdf"'
    assert response.content == RFI_BYTES


def test_quarantined_run_stores_no_rfi() -> None:
    quarantine = RunQuarantine(
        reason="text_layer_integrity_screen",
        documents=[
            QuarantinedDocument(
                document_role=DocumentRole.SUBMITTED_DOCUMENT,
                document_sha256="ab" * 32,
                page_count=1,
                flagged_pages=[1],
                hidden_span_count=1,
            )
        ],
    )
    summary = AuditRunSummary(
        run_id="unused",
        claims_made=0,
        rejected=0,
        retried=0,
        findings_persisted=0,
        rfi_path=None,
        quarantine=quarantine,
    )
    client, repository, storage, _ = _client(summary=summary)

    response = client.post(
        "/audit",
        data={"demo_passphrase": "test-passphrase", "submission_token": "test-token"},
        files=_files(),
        follow_redirects=False,
    )

    run_id = response.headers["location"].removeprefix("/runs/")
    assert response.status_code == 303
    assert repository.runs[run_id]["status"] == "QUARANTINED"
    assert repository.runs[run_id]["rfi"] is None
    assert f"{run_id}/rfi.pdf" not in storage.objects


def test_run_view_renders_classified_severity_badge_and_model_id() -> None:
    client, repository, _, _ = _client()
    repository.runs[RUN_ID] = {
        "run_id": RUN_ID,
        "created_at": datetime(2026, 8, 21, tzinfo=UTC),
        "status": "COMPLETED",
        "summary": {
            "claims_made": 1,
            "rejected": 0,
            "retried": 0,
            "findings_persisted": 1,
            "quarantine": None,
        },
        "documents": {
            "specification": {"object_name": f"{RUN_ID}/specification.pdf", "sha256": "ab" * 32},
            "submitted_document": {
                "object_name": f"{RUN_ID}/submitted-document.pdf",
                "sha256": "cd" * 32,
            },
        },
        "rfi": {"object_name": f"{RUN_ID}/rfi.pdf", "sha256": "ef" * 32},
    }
    repository.findings[RUN_ID] = [
        {
            "claim_text": "The submitted voltage conflicts with the requirement.",
            "spec_quote": {"page_number": 3, "text": "Provide 480V."},
            "cut_sheet_quote": {"page_number": 1, "text": "Nominal system: 208V."},
            "severity": "high",
            "severity_model_id": "gemma-3-27b-it",
        }
    ]

    response = client.get(f"/runs/{RUN_ID}")

    assert response.status_code == 200
    assert "severity-high" in response.text
    assert "HIGH" in response.text
    assert "gemma-3-27b-it" in response.text
    assert (
        "Severity is an advisory Gemma annotation on already-verified findings. "
        "It is not part of verification."
    ) in response.text


def test_upload_submission_token_replay_returns_the_first_run() -> None:
    client, repository, _, runner = _client()
    data = {"demo_passphrase": "test-passphrase", "submission_token": "replay-token"}

    first = client.post("/audit", data=data, files=_files(), follow_redirects=False)
    second = client.post("/audit", data=data, files=_files(), follow_redirects=False)

    assert first.status_code == 303
    assert second.status_code == 303
    assert second.headers["location"] == first.headers["location"]
    assert len(repository.runs) == 1
    assert len(runner.calls) == 1


def test_sample_limit_survives_a_cold_start() -> None:
    client, repository, _, first_runner = _client()
    headers = {"x-forwarded-for": "203.0.113.9, 198.51.100.4"}

    for _ in range(4):
        assert (
            client.post("/sample/caldra", headers=headers, follow_redirects=False).status_code
            == 303
        )

    second_runner = FakeAuditRunner()
    second_client = TestClient(
        create_app(
            WebServices(
                settings=WebSettings(
                    project_id="test-project",
                    bucket_name="test-runs",
                    demo_passphrase="test-passphrase",
                ),
                repository=repository,
                storage=FakeObjectStorage(),
                audit_runner=second_runner,
                audit_slots=asyncio.Semaphore(2),
            )
        )
    )
    for _ in range(2):
        assert (
            second_client.post(
                "/sample/caldra", headers=headers, follow_redirects=False
            ).status_code
            == 303
        )

    blocked = second_client.post("/sample/caldra", headers=headers, follow_redirects=False)

    assert blocked.status_code == 429
    assert len(first_runner.calls) == 4
    assert len(second_runner.calls) == 2
    assert sum(repository.sample_runs_by_day.values()) == 6


def test_stalled_run_is_a_read_side_status_on_list_and_detail() -> None:
    client, repository, _, _ = _client()
    repository.runs[RUN_ID] = {
        "run_id": RUN_ID,
        "created_at": datetime.now(UTC) - timedelta(minutes=11),
        "status": "RUNNING",
        "summary": {"claims_made": 0, "rejected": 0, "retried": 0, "findings_persisted": 0},
        "documents": {},
        "rfi": None,
    }

    listing = client.get("/")
    detail = client.get(f"/runs/{RUN_ID}")

    assert "STALLED" in listing.text
    assert "No completion for over ten minutes." in listing.text
    assert "This run is stalled." in detail.text
    assert "Firestore still stores its state as RUNNING." in detail.text
    assert repository.runs[RUN_ID]["status"] == "RUNNING"


def test_completed_zero_finding_run_says_why_it_has_no_rfi() -> None:
    client, repository, _, _ = _client()
    repository.runs[RUN_ID] = {
        "run_id": RUN_ID,
        "created_at": datetime.now(UTC),
        "status": "COMPLETED",
        "summary": {"claims_made": 0, "rejected": 0, "retried": 0, "findings_persisted": 0},
        "documents": {},
        "rfi": None,
    }

    assert "No RFI — no discrepancies found." in client.get("/").text
    assert "No RFI — no discrepancies found." in client.get(f"/runs/{RUN_ID}").text


def test_completed_rejected_claims_do_not_claim_no_discrepancies() -> None:
    client, repository, _, _ = _client()
    repository.runs[RUN_ID] = {
        "run_id": RUN_ID,
        "created_at": datetime.now(UTC),
        "status": "COMPLETED",
        "summary": {"claims_made": 1, "rejected": 1, "retried": 1, "findings_persisted": 0},
        "documents": {},
        "rfi": None,
    }
    repository.rejections[RUN_ID] = [
        {"claim_text": "Unsupported claim", "reason": "quote_not_found_on_cited_page"}
    ]

    listing = client.get("/")
    detail = client.get(f"/runs/{RUN_ID}")

    assert "No RFI — no discrepancies found." not in listing.text
    assert "No verified findings" in listing.text
    assert "No RFI — no discrepancies found." not in detail.text
    assert "No RFI — no findings were verified." in detail.text
    assert "quote_not_found_on_cited_page" in detail.text


def test_run_view_renders_fallback_severity_reason_and_audit_usage() -> None:
    client, repository, _, _ = _client()
    fallback_reason = "severity endpoint not deployed outside demo windows"
    repository.runs[RUN_ID] = {
        "run_id": RUN_ID,
        "created_at": datetime.now(UTC),
        "status": "COMPLETED",
        "summary": {
            "claims_made": 1,
            "rejected": 0,
            "retried": 0,
            "findings_persisted": 1,
            "audit_model_usage": {"prompt_tokens": 101, "output_tokens": 17, "total_tokens": 118},
        },
        "documents": {},
        "rfi": None,
    }
    repository.findings[RUN_ID] = [
        {
            "claim_text": "The submitted voltage conflicts with the requirement.",
            "spec_quote": {"page_number": 3, "text": "Provide 480V."},
            "cut_sheet_quote": {"page_number": 1, "text": "Nominal system: 208V."},
            "severity": "unclassified",
            "severity_status": "fallback",
            "severity_reason": fallback_reason,
        }
    ]

    response = client.get(f"/runs/{RUN_ID}")

    assert response.status_code == 200
    assert fallback_reason in response.text
    assert "Prompt tokens" in response.text
    assert ">101<" in response.text
    assert ">17<" in response.text
    assert ">118<" in response.text


def test_run_view_renders_recorded_usage_unavailability_reason() -> None:
    client, repository, _, _ = _client()
    unavailable_reason = "The ADK audit call path did not expose token usage metadata."
    repository.runs[RUN_ID] = {
        "run_id": RUN_ID,
        "created_at": datetime.now(UTC),
        "status": "COMPLETED",
        "summary": {
            "claims_made": 0,
            "rejected": 0,
            "retried": 0,
            "findings_persisted": 0,
            "audit_model_usage": {"unavailable_reason": unavailable_reason},
        },
        "documents": {},
        "rfi": None,
    }

    response = client.get(f"/runs/{RUN_ID}")

    assert response.status_code == 200
    assert unavailable_reason in response.text


# --- Phase 6e: production basics, gate playground, context, JSON export ---

FIXTURE_DIRECTORY = Path(__file__).resolve().parents[1] / "fixtures"
SPECIFICATION_FIXTURE = FIXTURE_DIRECTORY / "asterquay_learning_workshop_specification.pdf"
CUT_SHEET_FIXTURE = FIXTURE_DIRECTORY / "torven_70c_termination_switchboard.pdf"
SPEC_QUOTE = "Conductor terminations shall be rated 90 deg C minimum."
SPEC_QUOTE_PAGE = 5
CUT_SHEET_QUOTE = "Field conductor termination rating: 158 deg F."
CUT_SHEET_QUOTE_PAGE = 2
FIXTURE_RUN_ID = "fixture-run-6e"


class ExplodingRepository:
    """A repository that refuses every call, to prove a route reads nothing."""

    def __getattr__(self, name: str) -> Any:
        def explode(*args: Any, **kwargs: Any) -> Any:
            raise AssertionError(f"the route must not call repository.{name}")

        return explode


class ExplodingStorage:
    """Object storage that refuses every call, to prove a route reads nothing."""

    def __getattr__(self, name: str) -> Any:
        def explode(*args: Any, **kwargs: Any) -> Any:
            raise AssertionError(f"the route must not call storage.{name}")

        return explode


def _fixture_run_client() -> tuple[TestClient, FakeRunRepository, FakeObjectStorage]:
    """Build a client holding one completed run over the committed fixtures."""
    client, repository, storage, _ = _client()
    spec_bytes = SPECIFICATION_FIXTURE.read_bytes()
    cut_sheet_bytes = CUT_SHEET_FIXTURE.read_bytes()
    spec_hash = hashlib.sha256(spec_bytes).hexdigest()
    cut_sheet_hash = hashlib.sha256(cut_sheet_bytes).hexdigest()
    storage.objects[f"{FIXTURE_RUN_ID}/specification.pdf"] = spec_bytes
    storage.objects[f"{FIXTURE_RUN_ID}/submitted-document.pdf"] = cut_sheet_bytes
    storage.objects[f"{FIXTURE_RUN_ID}/rfi.pdf"] = RFI_BYTES
    created_at = datetime(2026, 8, 22, 12, 0, tzinfo=UTC)
    repository.runs[FIXTURE_RUN_ID] = {
        "run_id": FIXTURE_RUN_ID,
        "created_at": created_at,
        "source": "sample",
        "status": "COMPLETED",
        "summary": {
            "claims_made": 2,
            "rejected": 1,
            "retried": 1,
            "findings_persisted": 1,
            "quarantine": None,
            "audit_model_usage": {
                "prompt_tokens": 4527,
                "output_tokens": 15,
                "total_tokens": 5652,
                "unavailable_reason": None,
            },
        },
        "documents": {
            "specification": {
                "object_name": f"{FIXTURE_RUN_ID}/specification.pdf",
                "sha256": spec_hash,
                "content_type": "application/pdf",
            },
            "submitted_document": {
                "object_name": f"{FIXTURE_RUN_ID}/submitted-document.pdf",
                "sha256": cut_sheet_hash,
                "content_type": "application/pdf",
            },
        },
        "rfi": {
            "object_name": f"{FIXTURE_RUN_ID}/rfi.pdf",
            "sha256": hashlib.sha256(RFI_BYTES).hexdigest(),
            "content_type": "application/pdf",
        },
    }
    repository.findings[FIXTURE_RUN_ID] = [
        {
            "finding_id": "finding-6e-1",
            "run_id": FIXTURE_RUN_ID,
            "submittal_id": FIXTURE_RUN_ID,
            "claim_text": "The submitted termination rating is below the specified minimum.",
            "spec_quote": {
                "text": SPEC_QUOTE,
                "page_number": SPEC_QUOTE_PAGE,
                "document_sha256": spec_hash,
            },
            "cut_sheet_quote": {
                "text": CUT_SHEET_QUOTE,
                "page_number": CUT_SHEET_QUOTE_PAGE,
                "document_sha256": cut_sheet_hash,
            },
            "severity": "high",
            "severity_model_id": "google-gemma3-gemma-3-1b-it",
            "severity_status": "classified",
            "severity_reason": None,
            "verification_status": "verified",
            "spec_locator": f"Page {SPEC_QUOTE_PAGE}",
            "cut_sheet_locator": f"Page {CUT_SHEET_QUOTE_PAGE}",
            "created_at": created_at,
        }
    ]
    repository.rejections[FIXTURE_RUN_ID] = [
        {
            "run_id": FIXTURE_RUN_ID,
            "claim_text": "An invented conflict the gate refused.",
            "reason": json.dumps(
                [
                    {
                        "normalized_quote": "invented gamma quote.",
                        "page_count": 7,
                        "rejection_reason": "quote_not_found_on_cited_page",
                    }
                ],
                sort_keys=True,
            ),
            "timestamp": created_at,
        },
        {
            "run_id": FIXTURE_RUN_ID,
            "claim_text": "Initial model output",
            "reason": "model_output_invalid",
            "timestamp": created_at,
        },
    ]
    repository.integrity_records[FIXTURE_RUN_ID] = [
        {
            "integrity_finding_id": "integrity-6e-1",
            "run_id": FIXTURE_RUN_ID,
            "screen_id": "text_layer_render_mode_v1",
            "document_role": "submitted_document",
            "document_sha256": cut_sheet_hash,
            "page_count": 2,
            "flagged_pages": [1],
            "hidden_spans": [
                {
                    "page_number": 1,
                    "text": "Nominal system: 209V, 3-phase, 4-wire.",
                    "font": "NotoSans",
                    "size": 9.0,
                    "char_flags": 8,
                    "bbox": [10.0, 20.0, 30.0, 40.0],
                }
            ],
            "created_at": created_at,
        }
    ]
    return client, repository, storage


# --- 5. Production basics ------------------------------------------------


@pytest.mark.parametrize("path", ["/healthz", "/health"])
def test_healthz_returns_200_without_reading_any_dependency(path: str) -> None:
    app = create_app(
        WebServices(
            settings=WebSettings(project_id="p", bucket_name="b", demo_passphrase="x"),
            repository=ExplodingRepository(),
            storage=ExplodingStorage(),
            audit_runner=FakeAuditRunner(),
            audit_slots=asyncio.Semaphore(2),
        )
    )

    response = TestClient(app).get(path)

    assert response.status_code == 200
    assert response.text == "ok"


@pytest.mark.parametrize(
    "path", ["/", "/gate", "/healthz", "/health", "/static/index.js", "/runs/does-not-exist"]
)
def test_every_response_carries_the_security_headers(path: str) -> None:
    client, _, _, _ = _client()

    response = client.get(path)

    for name, value in SECURITY_HEADERS.items():
        assert response.headers[name] == value


def test_the_content_security_policy_allows_no_inline_script() -> None:
    client, _, _, _ = _client()

    policy = client.get("/").headers["Content-Security-Policy"]

    assert "script-src 'self'" in policy
    assert "'unsafe-inline'" not in policy.split("script-src")[1].split(";")[0]
    assert "'unsafe-eval'" not in policy
    assert "default-src 'none'" in policy
    assert "frame-ancestors 'none'" in policy


@pytest.mark.parametrize("path", ["/", "/gate"])
def test_no_page_carries_an_inline_script_body(path: str) -> None:
    """Every script element loads a same-origin file and carries no code."""
    client, _, _, _ = _client()

    body = client.get(path).text

    for opening_tag, contents in re.findall(r"(<script\b[^>]*>)(.*?)</script>", body, re.DOTALL):
        assert "src=" in opening_tag
        assert contents.strip() == ""


def test_the_static_script_is_served_and_holds_the_landing_page_behaviour() -> None:
    client, _, _, _ = _client()

    response = client.get("/static/index.js")

    assert response.status_code == 200
    assert "auditForm.addEventListener('submit'" in response.text
    assert '<script src="/static/index.js" defer></script>' in client.get("/").text


def test_the_root_page_answers_a_head_probe_with_the_security_headers() -> None:
    client, _, _, _ = _client()

    response = client.head("/")

    assert response.status_code == 200
    assert response.content == b""
    for name, value in SECURITY_HEADERS.items():
        assert response.headers[name] == value


def test_a_generated_500_still_carries_the_security_headers() -> None:
    """The response most likely to leak detail is not the one without headers."""
    app = create_app(
        WebServices(
            settings=WebSettings(project_id="p", bucket_name="b", demo_passphrase="x"),
            repository=ExplodingRepository(),
            storage=ExplodingStorage(),
            audit_runner=FakeAuditRunner(),
            audit_slots=asyncio.Semaphore(2),
        )
    )

    response = TestClient(app, raise_server_exceptions=False).get("/")

    assert response.status_code == 500
    for name, value in SECURITY_HEADERS.items():
        assert response.headers[name] == value


def test_the_security_headers_name_the_four_required_controls() -> None:
    assert SECURITY_HEADERS["X-Content-Type-Options"] == "nosniff"
    assert SECURITY_HEADERS["X-Frame-Options"] == "DENY"
    assert SECURITY_HEADERS["Referrer-Policy"] == "no-referrer"
    assert "Content-Security-Policy" in SECURITY_HEADERS


# --- 1. Gate playground --------------------------------------------------


def test_gate_playground_verifies_the_prefilled_example() -> None:
    client, repository, _, _ = _client()

    response = client.get("/gate")

    assert response.status_code == 200
    assert "VERIFIED" in response.text
    assert SPEC_QUOTE in response.text
    assert "the same function the runtime calls" in response.text
    assert "specguard.gate.verify_quote" in response.text
    assert repository.gate_checks_by_hour_ip == {}


def test_gate_playground_reports_the_page_count_and_no_rejection_reason() -> None:
    client, _, _, _ = _client()

    body = client.get("/gate").text

    assert "Page count" in body
    assert ">7<" in body
    assert "A verified quote carries no rejection reason." in body


def test_gate_playground_rejects_one_changed_digit() -> None:
    client, _, _, _ = _client()

    response = client.get(
        "/gate",
        params={
            "fixture": "specification",
            "page": str(SPEC_QUOTE_PAGE),
            "quote": "Conductor terminations shall be rated 80 deg C minimum.",
        },
    )

    assert response.status_code == 200
    assert "REJECTED" in response.text
    assert "quote_not_found_on_cited_page" in response.text


def test_gate_playground_rejects_a_real_quote_cited_to_the_wrong_page() -> None:
    client, _, _, _ = _client()

    response = client.get(
        "/gate", params={"fixture": "specification", "page": "4", "quote": SPEC_QUOTE}
    )

    assert "REJECTED" in response.text
    assert "quote_not_found_on_cited_page" in response.text


def test_gate_playground_reports_a_page_outside_the_document() -> None:
    client, _, _, _ = _client()

    response = client.get(
        "/gate", params={"fixture": "specification", "page": "99", "quote": SPEC_QUOTE}
    )

    assert "REJECTED" in response.text
    assert "page_out_of_range" in response.text


def test_gate_playground_shows_the_normalized_quote_the_gate_compared() -> None:
    client, _, _, _ = _client()

    response = client.get(
        "/gate",
        params={"fixture": "specification", "page": "5", "quote": "  CONDUCTOR   TERMINATIONS  "},
    )

    assert gate.normalize("  CONDUCTOR   TERMINATIONS  ") in response.text
    assert "conductor terminations" in response.text


def test_the_gate_verdict_is_read_before_the_form_that_produced_it() -> None:
    """The answer comes first; a verdict below the form can sit off screen."""
    client, _, _, _ = _client()

    body = client.get("/gate").text

    assert body.index('id="gate-result"') < body.index('id="gate-form"')


def test_the_gate_verdict_card_is_coloured_by_its_own_outcome() -> None:
    """A judge should read the outcome without reading the words."""
    client, _, _, _ = _client()

    verified = client.get("/gate").text
    rejected = client.get(
        "/gate",
        params={"fixture": "specification", "page": "4", "quote": SPEC_QUOTE},
    ).text

    assert "card verdict verdict-ok" in verified
    assert "card verdict verdict-bad" in rejected
    assert ".verdict-ok { border-left-color: var(--ok-line); }" in verified
    assert ".verdict-bad { border-left-color: var(--bad-line); }" in rejected


def test_gate_playground_lists_both_one_click_near_misses() -> None:
    client, _, _, _ = _client()

    body = client.get("/gate").text

    assert "One digit changed: 90 becomes 80" in body
    assert "Right quote, wrong page: cited to page 4" in body
    assert "page=4" in body


def test_gate_playground_refuses_a_fixture_it_does_not_hold() -> None:
    client, _, _, _ = _client()

    response = client.get("/gate", params={"fixture": "some-other-file", "page": "1", "quote": "x"})

    assert response.status_code == 400
    assert "Choose one of the committed fixtures." in response.text


@pytest.mark.parametrize("page", ["0", "-3", "two", ""])
def test_gate_playground_refuses_a_page_that_is_not_a_positive_number(page: str) -> None:
    client, _, _, _ = _client()

    response = client.get(
        "/gate", params={"fixture": "specification", "page": page, "quote": SPEC_QUOTE}
    )

    assert response.status_code == 400
    assert "whole number of at least 1" in response.text


def test_gate_playground_refuses_an_empty_quote() -> None:
    client, _, _, _ = _client()

    response = client.get("/gate", params={"fixture": "specification", "page": "5", "quote": "  "})

    assert response.status_code == 400
    assert "Enter a quote to check." in response.text


def test_gate_playground_never_shows_a_filesystem_path() -> None:
    client, _, _, _ = _client()

    body = client.get(
        "/gate", params={"fixture": "specification", "page": "5", "quote": SPEC_QUOTE}
    ).text

    assert str(SPECIFICATION_FIXTURE) not in body
    assert str(SPECIFICATION_FIXTURE.parent) not in body
    assert "pdf_path" not in body


def test_gate_playground_limits_checks_per_address_per_hour() -> None:
    client, repository, _, _ = _client()
    params = {"fixture": "specification", "page": "5", "quote": SPEC_QUOTE}

    for _ in range(GATE_CHECKS_PER_IP_HOUR):
        assert client.get("/gate", params=params).status_code == 200

    blocked = client.get("/gate", params=params)

    assert blocked.status_code == 429
    assert "The gate playground limit is reached. Try again later." in blocked.text
    assert sum(repository.gate_checks_by_hour_ip.values()) == GATE_CHECKS_PER_IP_HOUR


def test_gate_playground_writes_no_run_and_calls_no_model() -> None:
    client, repository, storage, runner = _client()

    client.get("/gate", params={"fixture": "caldra", "page": "1", "quote": "Caldra"})

    assert repository.runs == {}
    assert storage.objects == {}
    assert runner.calls == []


# --- 6. Page explainer ---------------------------------------------------


def test_every_button_a_judge_presses_answers_the_press() -> None:
    """A pressable control with no active state reads as unresponsive."""
    client, _, _, _ = _client()

    body = client.get("/").text

    assert "#audit-submit:active:not(:disabled) { transform: scale(0.97); }" in body
    assert ".sample-grid button:active:not(:disabled) { transform: scale(0.97); }" in body
    assert ".sample-grid button:active:not(:disabled) { transform: none; }" in body


def test_the_findings_page_explains_the_three_steps_and_links_to_the_gate() -> None:
    client, _, _, _ = _client()

    body = client.get("/").text

    assert "Screen" in body
    assert "Audit" in body
    assert "Gate" in body
    assert "Gemini 3.7 Flash via Vertex AI" in body
    assert "verbatim quote" in body
    assert "RFI draft" in body
    assert "Uncited claims are blocked from the ledger." in body
    assert 'href="/gate"' in body
    assert "The runtime enforces the gate." in body


# --- 2. Quote in context -------------------------------------------------


def test_the_run_page_shows_each_verified_quote_inside_its_cited_page() -> None:
    client, _, _ = _fixture_run_client()

    body = client.get(f"/runs/{FIXTURE_RUN_ID}").text

    spec_window = " ".join(gate.normalize(SPEC_QUOTE).split())
    cut_sheet_window = " ".join(gate.normalize(CUT_SHEET_QUOTE).split())
    assert f"<mark>{spec_window}</mark>" in body
    assert f"<mark>{cut_sheet_window}</mark>" in body
    assert "Specification page 5, as the gate read it" in body
    assert "Submitted page 2, as the gate read it" in body


def test_the_window_beside_a_quote_holds_neighbouring_page_text() -> None:
    client, _, _ = _fixture_run_client()

    body = client.get(f"/runs/{FIXTURE_RUN_ID}").text

    assert "2.1 terminations" in body
    assert "use listed terminals" in body


def test_the_run_page_context_is_deterministic() -> None:
    client, _, _ = _fixture_run_client()

    first = client.get(f"/runs/{FIXTURE_RUN_ID}").text
    second = client.get(f"/runs/{FIXTURE_RUN_ID}").text

    assert first == second


def test_a_rejected_claim_shows_its_gate_reason_where_a_window_would_be() -> None:
    client, _, _ = _fixture_run_client()

    body = client.get(f"/runs/{FIXTURE_RUN_ID}").text

    assert "quote_not_found_on_cited_page" in body
    assert "invented gamma quote." in body
    assert "Normalized quote, 7-page document" in body
    assert "model_output_invalid" in body
    assert "no per-quote gate feedback" in body


def test_an_unreadable_source_document_says_so_instead_of_guessing() -> None:
    client, _, storage = _fixture_run_client()
    storage.objects.pop(f"{FIXTURE_RUN_ID}/specification.pdf")

    body = client.get(f"/runs/{FIXTURE_RUN_ID}").text

    assert "The stored source document could not be read" in body
    assert "<mark>" not in body


def test_the_run_page_context_never_prints_a_filesystem_path() -> None:
    client, _, _ = _fixture_run_client()

    body = client.get(f"/runs/{FIXTURE_RUN_ID}").text

    assert str(SPECIFICATION_FIXTURE) not in body
    assert "specguard-context-" not in body


# --- 4. JSON export ------------------------------------------------------


def test_the_json_export_carries_every_persisted_record_of_a_run() -> None:
    client, _, _ = _fixture_run_client()

    response = client.get(f"/runs/{FIXTURE_RUN_ID}/export.json")
    payload = response.json()

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    assert payload["schema"] == "specguard.run.export.v1"
    assert payload["run"]["run_id"] == FIXTURE_RUN_ID
    assert payload["run"]["status"] == "COMPLETED"
    assert payload["run"]["created_at"] == "2026-08-22T12:00:00+00:00"
    assert payload["summary"]["findings_persisted"] == 1
    assert payload["summary"]["rejected"] == 1
    assert payload["summary"]["audit_model_usage"]["total_tokens"] == 5652
    assert {document["role"] for document in payload["documents"]} == {
        "specification",
        "submitted_document",
    }
    assert all(len(document["sha256"]) == 64 for document in payload["documents"])
    assert payload["rfi"]["object_name"] == f"{FIXTURE_RUN_ID}/rfi.pdf"


def test_the_exported_finding_carries_both_anchors_and_its_severity_record() -> None:
    client, _, _ = _fixture_run_client()

    finding = client.get(f"/runs/{FIXTURE_RUN_ID}/export.json").json()["findings"][0]

    assert finding["finding_id"] == "finding-6e-1"
    assert finding["verification_status"] == "verified"
    assert finding["spec_quote"]["text"] == SPEC_QUOTE
    assert finding["spec_quote"]["page_number"] == SPEC_QUOTE_PAGE
    assert len(finding["spec_quote"]["document_sha256"]) == 64
    assert finding["cut_sheet_quote"]["page_number"] == CUT_SHEET_QUOTE_PAGE
    assert finding["severity"] == {
        "label": "high",
        "status": "classified",
        "model_id": "google-gemma3-gemma-3-1b-it",
        "reason": None,
    }
    assert finding["created_at"] == "2026-08-22T12:00:00+00:00"


def test_the_export_carries_rejections_with_their_parsed_gate_feedback() -> None:
    client, _, _ = _fixture_run_client()

    rejections = client.get(f"/runs/{FIXTURE_RUN_ID}/export.json").json()["rejections"]

    assert len(rejections) == 2
    assert rejections[0]["details"][0]["rejection_reason"] == "quote_not_found_on_cited_page"
    assert rejections[0]["details"][0]["page_count"] == 7
    assert rejections[0]["timestamp"] == "2026-08-22T12:00:00+00:00"
    assert rejections[1]["reason"] == "model_output_invalid"
    assert rejections[1]["details"] == []


def test_the_export_carries_integrity_records_with_their_document_hashes() -> None:
    client, _, _ = _fixture_run_client()

    records = client.get(f"/runs/{FIXTURE_RUN_ID}/export.json").json()["integrity_records"]

    assert records[0]["screen_id"] == "text_layer_render_mode_v1"
    assert records[0]["flagged_pages"] == [1]
    assert len(records[0]["document_sha256"]) == 64
    assert records[0]["hidden_spans"][0]["text"] == "Nominal system: 209V, 3-phase, 4-wire."


def test_the_export_shows_no_hidden_span_field_the_run_page_withholds() -> None:
    """The export mirrors the run page's span columns and adds nothing."""
    client, _, _ = _fixture_run_client()

    span = client.get(f"/runs/{FIXTURE_RUN_ID}/export.json").json()["integrity_records"][0][
        "hidden_spans"
    ][0]

    assert set(span) == {"page_number", "text", "font", "size"}


def test_the_export_excludes_paths_passphrases_and_tokens() -> None:
    client, repository, _ = _fixture_run_client()
    repository.submission_tokens["secret-token-value"] = FIXTURE_RUN_ID

    payload = client.get(f"/runs/{FIXTURE_RUN_ID}/export.json").json()
    payload.pop("exclusions")
    body = json.dumps(payload)

    assert "document_path" not in body
    assert "pdf_path" not in body
    assert "rfi_path" not in body
    assert "passphrase" not in body.lower()
    assert "secret-token-value" not in body
    assert "submission_token" not in body
    assert str(SPECIFICATION_FIXTURE) not in body
    assert "C:\\" not in body
    assert "/tmp" not in body
    assert "specguard-context-" not in body


def test_the_export_states_its_own_exclusions() -> None:
    client, _, _ = _fixture_run_client()

    exclusions = client.get(f"/runs/{FIXTURE_RUN_ID}/export.json").json()["exclusions"]

    assert any("filesystem path" in line for line in exclusions)
    assert any("passphrase" in line for line in exclusions)
    assert any("hidden-span" in line for line in exclusions)


def test_the_export_of_a_quarantined_run_carries_its_disclosure() -> None:
    client, repository, _ = _fixture_run_client()
    repository.runs[FIXTURE_RUN_ID]["status"] = "QUARANTINED"
    repository.runs[FIXTURE_RUN_ID]["summary"]["quarantine"] = {
        "reason": "text_layer_integrity_screen",
        "documents": [
            {
                "document_role": "submitted_document",
                "document_sha256": "ab" * 32,
                "page_count": 2,
                "flagged_pages": [1],
                "hidden_span_count": 2,
                "integrity_finding_id": "integrity-6e-1",
                "persistence_reason": None,
            }
        ],
    }

    payload = client.get(f"/runs/{FIXTURE_RUN_ID}/export.json").json()

    assert payload["run"]["status"] == "QUARANTINED"
    assert payload["summary"]["quarantine"]["reason"] == "text_layer_integrity_screen"
    assert payload["summary"]["quarantine"]["documents"][0]["hidden_span_count"] == 2


def test_the_export_of_an_unknown_run_is_a_404() -> None:
    client, _, _, _ = _client()

    assert client.get("/runs/no-such-run/export.json").status_code == 404


def test_the_run_page_links_to_its_json_export() -> None:
    client, _, _ = _fixture_run_client()

    body = client.get(f"/runs/{FIXTURE_RUN_ID}").text

    assert f'href="/runs/{FIXTURE_RUN_ID}/export.json"' in body


def test_both_run_artifacts_are_offered_as_peer_actions() -> None:
    """Opening the RFI and exporting the run are the same kind of act."""
    client, _, _ = _fixture_run_client()

    body = client.get(f"/runs/{FIXTURE_RUN_ID}").text

    assert f'<a class="action primary" href="/runs/{FIXTURE_RUN_ID}/rfi.pdf">' in body
    assert f'<a class="action" href="/runs/{FIXTURE_RUN_ID}/export.json">' in body
    assert ".action:active { transform: scale(0.97); }" in body
    assert ".action:active { transform: none; }" in body


def test_a_run_with_no_rfi_still_offers_its_export_and_says_why() -> None:
    """The export exists for every run; only the RFI depends on findings."""
    client, repository, _ = _fixture_run_client()
    repository.runs[FIXTURE_RUN_ID]["rfi"] = None
    repository.runs[FIXTURE_RUN_ID]["summary"]["findings_persisted"] = 0
    repository.runs[FIXTURE_RUN_ID]["summary"]["rejected"] = 0
    repository.findings[FIXTURE_RUN_ID] = []

    body = client.get(f"/runs/{FIXTURE_RUN_ID}").text

    assert f'<a class="action" href="/runs/{FIXTURE_RUN_ID}/export.json">' in body
    assert "action primary" not in body
    assert "No RFI — no discrepancies found." in body


# --- Phase 6e review: the run page reads each run's documents once --------


def test_a_second_read_of_one_run_downloads_nothing_again() -> None:
    """Run identifiers are public, so a reload must not repeat the PDF work."""
    client, _, storage = _fixture_run_client()

    first = client.get(f"/runs/{FIXTURE_RUN_ID}")
    after_first = storage.download_count
    second = client.get(f"/runs/{FIXTURE_RUN_ID}")

    assert after_first == 2
    assert storage.download_count == 2
    assert first.text == second.text
    assert "<mark>" in second.text


def test_a_changed_anchor_set_is_read_again_rather_than_served_stale() -> None:
    """A run still writing findings must never reuse a smaller window set."""
    client, repository, storage = _fixture_run_client()
    client.get(f"/runs/{FIXTURE_RUN_ID}")
    after_first = storage.download_count
    second_finding = dict(repository.findings[FIXTURE_RUN_ID][0])
    second_finding["finding_id"] = "finding-6e-2"
    second_finding["spec_quote"] = {
        **second_finding["spec_quote"],
        "text": "Use listed terminals that accept the conductor size installed at each location.",
    }
    repository.findings[FIXTURE_RUN_ID] = [
        repository.findings[FIXTURE_RUN_ID][0],
        second_finding,
    ]

    body = client.get(f"/runs/{FIXTURE_RUN_ID}").text

    assert storage.download_count == after_first + 2
    assert body.count("<mark>") == 4


def test_an_unreadable_document_is_not_cached_as_a_failure() -> None:
    """A transient read failure must not outlast its cause."""
    client, _, storage = _fixture_run_client()
    specification = storage.objects.pop(f"{FIXTURE_RUN_ID}/specification.pdf")

    failed = client.get(f"/runs/{FIXTURE_RUN_ID}").text
    storage.objects[f"{FIXTURE_RUN_ID}/specification.pdf"] = specification
    recovered = client.get(f"/runs/{FIXTURE_RUN_ID}").text

    assert "The stored source document could not be read" in failed
    assert "The stored source document could not be read" not in recovered
    assert "<mark>" in recovered


def test_the_window_cache_is_bounded_and_evicts_the_oldest_run() -> None:
    client, repository, storage = _fixture_run_client()
    services: WebServices = client.app.state.services  # type: ignore[attr-defined]
    template = repository.runs[FIXTURE_RUN_ID]
    findings = repository.findings[FIXTURE_RUN_ID]
    for index in range(QUOTE_CONTEXT_CACHE_SIZE + 1):
        run_id = f"{FIXTURE_RUN_ID}-{index}"
        repository.runs[run_id] = {**template, "run_id": run_id}
        repository.findings[run_id] = findings
        assert client.get(f"/runs/{run_id}").status_code == 200

    assert len(services.quote_contexts) == QUOTE_CONTEXT_CACHE_SIZE
    assert all(key[0] != f"{FIXTURE_RUN_ID}-0" for key in services.quote_contexts)


def test_two_apps_never_share_a_cached_window_set() -> None:
    """The cache lives on one app's services, not on the module."""
    first, _, _ = _fixture_run_client()
    second, _, second_storage = _fixture_run_client()

    first.get(f"/runs/{FIXTURE_RUN_ID}")
    second.get(f"/runs/{FIXTURE_RUN_ID}")

    assert second_storage.download_count == 2
