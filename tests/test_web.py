"""Network-free route tests for the public findings page."""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from specguard.models import AuditRunSummary, DocumentRole, QuarantinedDocument, RunQuarantine
from specguard.web.app import (
    MAX_UPLOAD_BYTES,
    SAMPLE_RUNS_PER_UTC_DAY,
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

    assert response.status_code == 200
    assert "Audit running. Do not submit again." in response.text
    assert "A repeated submit returns the first run." in response.text
    assert "#audit-submit:disabled" in response.text
    assert "auditSubmit.disabled = true" in response.text
    assert "if (submitting) { event.preventDefault(); return; }" in response.text
    assert "Sample audit running. Do not submit again." in response.text
    assert "All sample buttons are disabled until this run opens." in response.text
    assert ".sample-grid button:disabled" in response.text
    assert "for (const button of sampleButtons) button.disabled = true;" in response.text
    assert "if (sampleSubmitting) { event.preventDefault(); return; }" in response.text


def test_findings_page_confirms_each_chosen_file_before_the_run_starts() -> None:
    client, _, _, _ = _client()

    response = client.get("/")

    assert response.status_code == 200
    assert "No file chosen" in response.text
    assert "Choose PDF" in response.text
    assert "Replace PDF" in response.text
    assert ".drop-zone[data-filled]" in response.text
    assert "This file is larger than 5 MB." in response.text


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
