"""Public, read-only findings views and the guarded PDF audit submission route."""

from __future__ import annotations

import asyncio
import os
import secrets
import tempfile
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from starlette.datastructures import FormData

from specguard.models import AuditRunSummary
from specguard.web.repository import FirestoreRunRepository, RunRepository
from specguard.web.runtime import AuditRunner, GoogleAuditRunner
from specguard.web.storage import CloudStorage, ObjectStorage, StoredObject

MAX_UPLOAD_BYTES = 5 * 1024 * 1024
MAX_IN_FLIGHT_AUDITS = 2
EXPECTED_UPLOAD_FIELDS = frozenset({"spec_pdf", "cut_sheet_pdf"})
TEMPLATES_DIRECTORY = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIRECTORY))


@dataclass(frozen=True)
class WebSettings:
    """Non-secret configuration supplied to the Cloud Run service."""

    project_id: str
    bucket_name: str
    demo_passphrase: str

    @classmethod
    def from_environment(cls) -> WebSettings:
        """Read the explicit runtime configuration from the environment."""
        return cls(
            project_id=os.environ.get("SPECGUARD_PROJECT", "specguard-hack"),
            bucket_name=os.environ.get("SPECGUARD_RUNS_BUCKET", ""),
            demo_passphrase=os.environ.get("SPECGUARD_DEMO_PASSPHRASE", ""),
        )


@dataclass
class WebServices:
    """Concrete dependencies for one app, replaceable by isolated route tests."""

    settings: WebSettings
    repository: RunRepository
    storage: ObjectStorage
    audit_runner: AuditRunner
    audit_slots: asyncio.Semaphore

    @classmethod
    def production(cls) -> WebServices:
        """Build lazy Google Cloud dependencies for the deployed service."""
        settings = WebSettings.from_environment()
        return cls(
            settings=settings,
            repository=FirestoreRunRepository(project_id=settings.project_id),
            storage=CloudStorage(bucket_name=settings.bucket_name, project_id=settings.project_id),
            audit_runner=GoogleAuditRunner(project_id=settings.project_id),
            audit_slots=asyncio.Semaphore(MAX_IN_FLIGHT_AUDITS),
        )


class UploadValidationError(ValueError):
    """A user-visible upload validation failure."""


class AuditFailedError(Exception):
    """An audit that stopped before it produced a completed run record."""

    def __init__(self, run_id: str | None) -> None:
        super().__init__("The audit could not complete.")
        self.run_id = run_id


def create_app(services: WebServices | None = None) -> FastAPI:
    """Create the FastAPI service with an optional isolated dependency set."""
    app = FastAPI(title="SpecGuard")
    app.state.services = services or WebServices.production()

    @app.get("/")
    def findings_page(request: Request) -> Response:
        """Show the upload form and the newest audit runs."""
        return _render_index(request, app.state.services)

    @app.post("/audit")
    async def audit(
        request: Request,
        spec_pdf: Annotated[UploadFile, File(...)],
        cut_sheet_pdf: Annotated[UploadFile, File(...)],
        demo_passphrase: Annotated[str, Form(...)],
    ) -> Response:
        """Validate two PDFs, audit them, and store the durable run artifacts."""
        app_services: WebServices = app.state.services
        if not app_services.settings.bucket_name:
            raise HTTPException(status_code=503, detail="The runs bucket is not configured.")
        expected_passphrase = app_services.settings.demo_passphrase
        if not expected_passphrase or not secrets.compare_digest(
            demo_passphrase.encode("utf-8"), expected_passphrase.encode("utf-8")
        ):
            return _render_index(
                request,
                app_services,
                error="The demo passphrase is required.",
                status_code=403,
            )

        async with app_services.audit_slots:
            try:
                _reject_unexpected_file_parts(await request.form())
                spec_bytes = await _read_pdf_upload(spec_pdf, "The specification")
                cut_sheet_bytes = await _read_pdf_upload(cut_sheet_pdf, "The cut sheet")
            except UploadValidationError as error:
                return _render_index(request, app_services, error=str(error), status_code=400)

            run_id = uuid.uuid4().hex
            try:
                run = await _run_audit(
                    app_services,
                    run_id=run_id,
                    spec_bytes=spec_bytes,
                    cut_sheet_bytes=cut_sheet_bytes,
                )
            except AuditFailedError as failure:
                return _render_index(
                    request,
                    app_services,
                    error="The audit did not complete.",
                    failed_run_id=failure.run_id,
                    status_code=500,
                )
        return RedirectResponse(url=f"/runs/{run['run_id']}", status_code=303)

    @app.get("/runs/{run_id}")
    def run_detail(request: Request, run_id: str) -> Response:
        """Show all persisted records for one audit run to a human reviewer."""
        app_services: WebServices = app.state.services
        run = app_services.repository.get_run(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="Audit run not found.")
        findings = app_services.repository.get_findings(run_id)
        rejections = app_services.repository.get_rejections(run_id)
        integrity_records = app_services.repository.get_integrity_records(run_id)
        return templates.TemplateResponse(
            request=request,
            name="run_detail.html",
            context={
                "run": run,
                "findings": findings,
                "rejections": rejections,
                "integrity_records": integrity_records,
            },
        )

    @app.get("/runs/{run_id}/rfi.pdf")
    def rfi_pdf(run_id: str) -> Response:
        """Serve the durable RFI bytes for a run that generated one."""
        app_services: WebServices = app.state.services
        run = app_services.repository.get_run(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="Audit run not found.")
        rfi = run.get("rfi")
        if not rfi:
            raise HTTPException(status_code=404, detail="This run has no RFI draft.")
        try:
            content = app_services.storage.download_bytes(str(rfi["object_name"]))
        except Exception:
            raise HTTPException(status_code=404, detail="The RFI draft is unavailable.") from None
        return Response(
            content=content,
            media_type="application/pdf",
            headers={"Content-Disposition": f'inline; filename="rfi-{run_id}.pdf"'},
        )

    return app


def _reject_unexpected_file_parts(form: FormData) -> None:
    """Refuse a submission that carries any file part beyond the two audited ones.

    FastAPI binds one scalar upload per declared field, so a duplicate or an
    unexpected file part would be parsed and spooled without ever meeting the
    content-type and size checks. Refusing the whole request keeps every file
    the service accepts under those checks.
    """
    seen: set[str] = set()
    for field_name, value in form.multi_items():
        if isinstance(value, str):
            continue
        if field_name not in EXPECTED_UPLOAD_FIELDS:
            raise UploadValidationError(
                "The submission carried an unexpected file. Send one specification "
                "PDF and one cut-sheet PDF."
            )
        if field_name in seen:
            raise UploadValidationError(
                "The submission carried more than one file for the same document."
            )
        seen.add(field_name)


async def _read_pdf_upload(upload: UploadFile, label: str) -> bytes:
    """Read one small PDF only after enforcing its type and size limits."""
    if upload.content_type != "application/pdf":
        raise UploadValidationError(f"{label} must use the application/pdf content type.")
    data = await upload.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        raise UploadValidationError(f"{label} must not exceed 5 MB.")
    if not data.startswith(b"%PDF-"):
        raise UploadValidationError(f"{label} is not a PDF file.")
    return data


async def _run_audit(
    services: WebServices,
    *,
    run_id: str,
    spec_bytes: bytes,
    cut_sheet_bytes: bytes,
) -> dict[str, Any]:
    """Store an auditable run record for every successfully uploaded document pair."""
    specification_name = f"{run_id}/specification.pdf"
    submitted_document_name = f"{run_id}/submitted-document.pdf"
    uploaded_names: list[str] = []
    pending_run: dict[str, Any] | None = None
    run_recorded = False
    rfi_object_name: str | None = None
    rfi: StoredObject | None = None
    try:
        uploaded_names.append(specification_name)
        specification = services.storage.upload_bytes(
            specification_name, spec_bytes, "application/pdf"
        )
        uploaded_names.append(submitted_document_name)
        submitted_document = services.storage.upload_bytes(
            submitted_document_name, cut_sheet_bytes, "application/pdf"
        )
        pending_run = _pending_run_record(
            run_id=run_id,
            specification=specification,
            submitted_document=submitted_document,
        )
        services.repository.create_run(pending_run)
        run_recorded = True

        with tempfile.TemporaryDirectory(prefix="specguard-") as directory:
            root = Path(directory)
            spec_path = root / "specification.pdf"
            cut_sheet_path = root / "submitted-document.pdf"
            spec_path.write_bytes(spec_bytes)
            cut_sheet_path.write_bytes(cut_sheet_bytes)
            summary = await services.audit_runner.run_audit(
                spec_path=spec_path,
                cut_sheet_path=cut_sheet_path,
                run_id=run_id,
                output_directory=root / "artifacts",
            )
            if summary.rfi_path is not None:
                rfi_object_name = f"{run_id}/rfi.pdf"
                rfi = _store_rfi(services.storage, run_id, summary)

        run = _run_record(
            run_id=run_id,
            created_at=pending_run["created_at"],
            summary=summary,
            specification=specification,
            submitted_document=submitted_document,
            rfi=rfi,
        )
        services.repository.create_run(run)
        return run
    except Exception:
        if rfi_object_name is not None:
            _delete_unrecorded_objects(services.storage, [rfi_object_name])
        if not run_recorded or pending_run is None:
            _delete_unrecorded_objects(services.storage, uploaded_names)
            raise AuditFailedError(None) from None
        _record_failed_run(services.repository, pending_run)
        raise AuditFailedError(run_id) from None


def _store_rfi(
    storage: ObjectStorage, run_id: str, summary: AuditRunSummary
) -> StoredObject | None:
    """Copy an RFI from ephemeral compute to durable object storage when present."""
    if summary.rfi_path is None:
        return None
    rfi_path = Path(summary.rfi_path)
    return storage.upload_bytes(f"{run_id}/rfi.pdf", rfi_path.read_bytes(), "application/pdf")


def _pending_run_record(
    *, run_id: str, specification: StoredObject, submitted_document: StoredObject
) -> dict[str, Any]:
    """Build a run record before the audit can create durable derived data."""
    return {
        "run_id": run_id,
        "created_at": datetime.now(UTC),
        "status": "RUNNING",
        "summary": _empty_summary(),
        "documents": {
            "specification": _stored_object_record(specification),
            "submitted_document": _stored_object_record(submitted_document),
        },
        "rfi": None,
    }


def _run_record(
    *,
    run_id: str,
    created_at: datetime,
    summary: AuditRunSummary,
    specification: StoredObject,
    submitted_document: StoredObject,
    rfi: StoredObject | None,
) -> dict[str, Any]:
    """Build the public Firestore run record without any ephemeral file paths."""
    return {
        "run_id": run_id,
        "created_at": created_at,
        "status": "QUARANTINED" if summary.quarantined else "COMPLETED",
        "summary": {
            "claims_made": summary.claims_made,
            "rejected": summary.rejected,
            "retried": summary.retried,
            "findings_persisted": summary.findings_persisted,
            "quarantine": (
                summary.quarantine.model_dump(mode="json")
                if summary.quarantine is not None
                else None
            ),
        },
        "documents": {
            "specification": _stored_object_record(specification),
            "submitted_document": _stored_object_record(submitted_document),
        },
        "rfi": _stored_object_record(rfi) if rfi is not None else None,
    }


def _empty_summary() -> dict[str, Any]:
    """Build the zero counters used before an audit produces a summary."""
    return {
        "claims_made": 0,
        "rejected": 0,
        "retried": 0,
        "findings_persisted": 0,
        "quarantine": None,
    }


def _delete_unrecorded_objects(storage: ObjectStorage, object_names: list[str]) -> None:
    """Best-effort remove objects that no run document can reference."""
    for object_name in object_names:
        try:
            storage.delete_object(object_name)
        except Exception:
            pass


def _record_failed_run(repository: RunRepository, pending_run: dict[str, Any]) -> bool:
    """Mark an already recorded input pair as failed, without exposing internal errors.

    The caller reaches this only after the RUNNING record was written, so the
    stored objects are already referenced by a run document. One retry narrows
    the window in which a failed audit keeps reporting RUNNING. If both writes
    fail the objects stay referenced by that RUNNING record rather than being
    deleted out from under it.
    """
    failed_run = dict(pending_run)
    failed_run["status"] = "FAILED"
    failed_run["summary"] = {**_empty_summary(), "failure": "audit_failed"}
    for _ in range(2):
        try:
            repository.create_run(failed_run)
        except Exception:
            continue
        return True
    return False


def _stored_object_record(stored: StoredObject) -> dict[str, str]:
    """Serialize one storage object for the run document."""
    return {
        "object_name": stored.object_name,
        "sha256": stored.sha256,
        "content_type": stored.content_type,
    }


def _render_index(
    request: Request,
    services: WebServices,
    *,
    error: str | None = None,
    failed_run_id: str | None = None,
    status_code: int = 200,
) -> Response:
    """Render the shared landing page with a controlled user-facing error."""
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "runs": services.repository.list_runs(),
            "error": error,
            "failed_run_id": failed_run_id,
        },
        status_code=status_code,
    )


app = create_app()
