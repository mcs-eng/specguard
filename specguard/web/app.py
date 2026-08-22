"""Public, read-only findings views and the guarded PDF audit submission route."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import secrets
import tempfile
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated, Any

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.datastructures import FormData

from specguard import context, gate
from specguard.models import AuditRunSummary
from specguard.tools import QUOTES_VERIFIED_MEANING
from specguard.web.repository import (
    FirestoreRunRepository,
    RunRepository,
    SubmissionTokenRefused,
)
from specguard.web.runtime import AuditRunner, GoogleAuditRunner
from specguard.web.storage import CloudStorage, ObjectStorage, StoredObject

#: The stored ``verification_status`` a findings record must carry to be part
#: of the ledger. The run page and the JSON export read only these records.
LEDGER_VERIFICATION_STATUS = "verified"

MAX_UPLOAD_BYTES = 5 * 1024 * 1024
MAX_IN_FLIGHT_AUDITS = 2
EXPECTED_UPLOAD_FIELDS = frozenset({"spec_pdf", "cut_sheet_pdf"})
SAMPLE_RUNS_PER_IP_HOUR = 6
SAMPLE_RUNS_PER_UTC_DAY = 60
GATE_CHECKS_PER_IP_HOUR = 60

#: How many upload submission tokens one address may have minted in a UTC hour.
#: Minting on render is what makes a token one-time and expiring, and it also
#: means an unauthenticated GET writes a Firestore document. This cap is far
#: above any human's reload count and bounds that write surface.
TOKEN_MINTS_PER_IP_HOUR = 30

#: How many recent runs the landing page reads before it keeps the sample ones.
#: The filter runs here rather than in the query, because an equality filter
#: plus an ordering needs a Firestore composite index. Reading five pages'
#: worth and keeping the samples costs one query and needs no index. If more
#: than this many upload runs are newer than every sample run, the list is
#: short; it is never wrong.
LANDING_PAGE_RUN_SCAN = 100
LANDING_PAGE_RUNS = 20
QUOTE_CONTEXT_CACHE_SIZE = 32
RUN_STALLED_AFTER = timedelta(minutes=10)

#: How long a minted upload submission token stays claimable. A page left open
#: overnight submits against a token this service no longer honours, and the
#: reader is told to reload rather than being handed a run they did not intend.
SUBMISSION_TOKEN_LIFETIME = timedelta(hours=1)
FIXTURES_DIRECTORY = Path(__file__).parents[2] / "fixtures"
TEMPLATES_DIRECTORY = Path(__file__).parent / "templates"
STATIC_DIRECTORY = Path(__file__).parent / "static"
templates = Jinja2Templates(directory=str(TEMPLATES_DIRECTORY))

#: Response headers set on every response the service returns.
#:
#: ``script-src 'self'`` allows no inline script, so the landing-page behaviour
#: lives in ``/static/index.js``. ``style-src`` still allows inline style,
#: because the page ships its stylesheet inside the document; no style rule can
#: execute code.
SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'none'; "
        "script-src 'self'; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; "
        "font-src 'self'; "
        "form-action 'self'; "
        "base-uri 'none'; "
        "frame-ancestors 'none'"
    ),
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "X-Frame-Options": "DENY",
    # One year, subdomains included. Cloud Run serves this service over HTTPS
    # only and redirects plain HTTP, so a browser that has seen one response
    # never sends the next request in the clear.
    "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
}


@dataclass(frozen=True)
class SampleAuditCase:
    """One committed fixture pair available to judges without a passphrase."""

    label: str
    cut_sheet_filename: str


SAMPLE_AUDIT_CASES = {
    "caldra": SampleAuditCase("Caldra (compliant)", "caldra_meridian_480v_switchboard.pdf"),
    "veylan-208v": SampleAuditCase("Veylan 208V", "veylan_arcworks_208v_switchboard.pdf"),
    "torven-70c": SampleAuditCase("Torven 70 deg C", "torven_70c_termination_switchboard.pdf"),
    "veylan-altered": SampleAuditCase(
        "Veylan altered (integrity screen)", "veylan_arcworks_208v_altered.pdf"
    ),
}
SAMPLE_SPECIFICATION_FILENAME = "asterquay_learning_workshop_specification.pdf"


@dataclass(frozen=True)
class GateFixture:
    """One committed fixture the gate playground may read."""

    label: str
    filename: str


GATE_FIXTURES = {
    "specification": GateFixture(
        "Specification - Asterquay Learning Workshop", SAMPLE_SPECIFICATION_FILENAME
    ),
    "caldra": GateFixture(
        "Cut sheet - Caldra Meridian 480V", "caldra_meridian_480v_switchboard.pdf"
    ),
    "veylan-208v": GateFixture(
        "Cut sheet - Veylan Arcworks 208V", "veylan_arcworks_208v_switchboard.pdf"
    ),
    "torven-70c": GateFixture(
        "Cut sheet - Torven 70 deg C termination", "torven_70c_termination_switchboard.pdf"
    ),
    "veylan-altered": GateFixture(
        "Cut sheet - Veylan altered copy", "veylan_arcworks_208v_altered.pdf"
    ),
}
GATE_DEFAULT_FIXTURE = "specification"
GATE_DEFAULT_PAGE = 5
GATE_DEFAULT_QUOTE = "Conductor terminations shall be rated 90 deg C minimum."

#: Shown when the committed fixtures could not be read at import.
GATE_FIXTURES_UNAVAILABLE = "The gate fixtures are unavailable on this instance."

#: Two one-click inputs that the gate refuses, so a reader sees a rejection
#: without composing one. Each changes exactly one thing about the passing
#: example above.
GATE_NEAR_MISSES = (
    {
        "label": "One digit changed: 90 becomes 80",
        "note": "The same sentence, one character different. The gate does no fuzzy matching.",
        "fixture": GATE_DEFAULT_FIXTURE,
        "page": GATE_DEFAULT_PAGE,
        "quote": "Conductor terminations shall be rated 80 deg C minimum.",
    },
    {
        "label": "Right quote, wrong page: cited to page 4",
        "note": "The sentence is real and is on page 5. The gate reads the cited page only.",
        "fixture": GATE_DEFAULT_FIXTURE,
        "page": 4,
        "quote": GATE_DEFAULT_QUOTE,
    },
)


def _default_gate_result() -> dict[str, Any] | None:
    """Verify the prefilled example once, at import, from a committed fixture.

    ``GET /gate`` is a public route with no counter, so it must not open a PDF
    per request. The example is fixed, the fixture is committed, and the gate
    is deterministic, so the verdict is the same on every request and is
    computed here rather than there. A fixture this instance cannot read
    yields ``None``, and the page says so instead of failing to start.
    """
    selected = GATE_FIXTURES[GATE_DEFAULT_FIXTURE]
    try:
        result = gate.verify_quote(
            GATE_DEFAULT_QUOTE, GATE_DEFAULT_PAGE, FIXTURES_DIRECTORY / selected.filename
        )
    except Exception:
        return None
    return _gate_result_view(result, selected)


@dataclass(frozen=True)
class WebSettings:
    """Non-secret configuration supplied to the Cloud Run service."""

    project_id: str
    bucket_name: str
    demo_passphrase: str
    #: Whether ``X-Forwarded-For`` may name the client for the rate limits.
    #: True only behind a proxy that appends the real peer address, which
    #: Cloud Run does. False everywhere else, because there the header is
    #: whatever the caller typed.
    trust_forwarded_for: bool = False

    @classmethod
    def from_environment(cls) -> WebSettings:
        """Read the explicit runtime configuration from the environment."""
        return cls(
            project_id=os.environ.get("SPECGUARD_PROJECT", "specguard-hack"),
            bucket_name=os.environ.get("SPECGUARD_RUNS_BUCKET", ""),
            demo_passphrase=os.environ.get("SPECGUARD_DEMO_PASSPHRASE", ""),
            trust_forwarded_for=os.environ.get("SPECGUARD_TRUST_FORWARDED_FOR") == "1",
        )


@dataclass
class WebServices:
    """Concrete dependencies for one app, replaceable by isolated route tests."""

    settings: WebSettings
    repository: RunRepository
    storage: ObjectStorage
    audit_runner: AuditRunner
    audit_slots: asyncio.Semaphore
    #: Page windows already built, newest last, keyed by run and anchor set.
    #: One entry per run, bounded, and local to this app so a test app never
    #: reads another app's entries.
    quote_contexts: OrderedDict[tuple[str, str], list[dict[str, Any]]] = field(
        default_factory=OrderedDict
    )

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


#: Shown for a submission token this service never minted, or one that expired.
#: It names the fix, not the mechanism, because a reader who left a tab open
#: overnight has done nothing wrong.
STALE_SUBMISSION_TOKEN_ERROR = (
    "This upload form is no longer valid. Reload the page and submit again. "
    "A submission form is accepted for one hour after the page is served."
)


class UploadReplay(Exception):
    """A repeated upload submission token that already owns a run."""

    def __init__(self, run_id: str) -> None:
        super().__init__("The upload submission token was already used.")
        self.run_id = run_id


def create_app(services: WebServices | None = None) -> FastAPI:
    """Create the FastAPI service with an optional isolated dependency set."""
    app = FastAPI(title="SpecGuard")
    app.state.services = services or WebServices.production()
    app.mount("/static", StaticFiles(directory=str(STATIC_DIRECTORY)), name="static")

    @app.middleware("http")
    async def set_security_headers(request: Request, call_next: Any) -> Response:
        """Apply the same security headers to every response the router returns."""
        response = await call_next(request)
        for name, value in SECURITY_HEADERS.items():
            response.headers.setdefault(name, value)
        return response

    @app.exception_handler(Exception)
    async def unhandled_error(request: Request, error: Exception) -> Response:
        """Return a 500 that still carries the security headers.

        Starlette builds its stack with ``ServerErrorMiddleware`` outside every
        middleware an application adds, so a 500 that middleware generates
        never passes back through the header layer above. Without this handler
        the one response most likely to leak a stack trace would be the one
        response with no ``Content-Security-Policy`` and no ``nosniff``.
        ``ServerErrorMiddleware`` re-raises after sending this response, so the
        error still reaches the logs.
        """
        return JSONResponse(
            {"detail": "Internal Server Error"},
            status_code=500,
            headers=dict(SECURITY_HEADERS),
        )

    @app.get("/healthz")
    @app.get("/health")
    def healthz() -> Response:
        """Report that this instance serves requests, without reading a dependency.

        This route touches no Firestore collection, no storage bucket, and no
        model endpoint. It answers one question only: did this process start
        and can it serve. A check that called a dependency would report that
        dependency's health under this route's name.

        Two paths serve the same handler. On Cloud Run the Google Front End
        answers ``/healthz`` itself with its own 404 and never forwards the
        request, so ``/health`` is the path that reaches this process on the
        deployed service. ``/healthz`` stays registered because it is the
        conventional name and it is reachable everywhere else this app runs.
        """
        return Response("ok", media_type="text/plain; charset=utf-8")

    @app.api_route("/", methods=["GET", "HEAD"])
    def findings_page(request: Request) -> Response:
        """Show the upload form and the newest audit runs.

        HEAD is answered as well as GET. ``curl -I`` is the ordinary way to
        probe a service, and a read-only page that refuses it reports 405 to
        anyone checking whether the service is up.
        """
        return _render_index(request, app.state.services)

    @app.get("/gate")
    def gate_playground(request: Request) -> Response:
        """Show the gate playground and the verdict for its prefilled example.

        This route reads no query string, opens no PDF, and touches no
        Firestore counter. The default verdict was computed once, at import,
        from a committed fixture, so serving this page costs one template
        render. A check on a reader's own values is a POST.
        """
        return _render_gate(
            request,
            fixture_id=GATE_DEFAULT_FIXTURE,
            page_text=str(GATE_DEFAULT_PAGE),
            quote_text=GATE_DEFAULT_QUOTE,
            result=GATE_DEFAULT_RESULT,
            error=None if GATE_DEFAULT_RESULT is not None else GATE_FIXTURES_UNAVAILABLE,
        )

    @app.post("/gate")
    def gate_check(
        request: Request,
        fixture: Annotated[str, Form()] = "",
        page: Annotated[str, Form()] = "",
        quote: Annotated[str, Form()] = "",
    ) -> Response:
        """Run ``specguard.gate.verify_quote`` against one committed fixture.

        This route calls the same function the runtime calls at write time. It
        makes no model call, writes no record, and needs no passphrase. Each
        check reserves one durable per-address slot.

        A submitted request is answered on its own values only. Filling a blank
        field from the default example would answer a question the reader did
        not ask, and would report a verdict for the wrong input.
        """
        app_services: WebServices = app.state.services
        now = datetime.now(UTC)
        if not app_services.repository.reserve_gate_check(
            hour=now.strftime("%Y-%m-%dT%H:00Z"),
            client_ip=_client_ip(request, app_services.settings.trust_forwarded_for),
            hourly_limit=GATE_CHECKS_PER_IP_HOUR,
        ):
            return _gate_limit_response(request)

        fixture_id = fixture
        page_text = page
        quote_text = quote

        error = _gate_input_error(fixture_id, page_text, quote_text)
        if error is not None:
            return _render_gate(
                request,
                fixture_id=fixture_id,
                page_text=page_text,
                quote_text=quote_text,
                error=error,
                status_code=400,
            )

        selected = GATE_FIXTURES[fixture_id]
        try:
            result = gate.verify_quote(
                quote_text, int(page_text), FIXTURES_DIRECTORY / selected.filename
            )
        except OSError:
            raise HTTPException(
                status_code=503, detail="The gate fixtures are unavailable."
            ) from None
        return _render_gate(
            request,
            fixture_id=fixture_id,
            page_text=page_text,
            quote_text=quote_text,
            result=_gate_result_view(result, selected),
        )

    @app.post("/audit")
    async def audit(
        request: Request,
        spec_pdf: Annotated[UploadFile, File(...)],
        cut_sheet_pdf: Annotated[UploadFile, File(...)],
        demo_passphrase: Annotated[str, Form(...)],
        submission_token: Annotated[str, Form(...)],
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
            token_record = (
                app_services.repository.get_submission_token(submission_token)
                if submission_token
                else None
            )
            if token_record is None:
                return _render_index(
                    request,
                    app_services,
                    error=STALE_SUBMISSION_TOKEN_ERROR,
                    status_code=400,
                )
            existing_run_id = token_record.get("run_id")
            if existing_run_id:
                return RedirectResponse(url=f"/runs/{existing_run_id}", status_code=303)
            if _token_has_expired(token_record, datetime.now(UTC)):
                return _render_index(
                    request,
                    app_services,
                    error=STALE_SUBMISSION_TOKEN_ERROR,
                    status_code=400,
                )

            run_id = uuid.uuid4().hex
            try:
                run = await _run_audit(
                    app_services,
                    run_id=run_id,
                    spec_bytes=spec_bytes,
                    cut_sheet_bytes=cut_sheet_bytes,
                    source="upload",
                    submission_token=submission_token,
                )
            except UploadReplay as replay:
                return RedirectResponse(url=f"/runs/{replay.run_id}", status_code=303)
            except SubmissionTokenRefused:
                return _render_index(
                    request,
                    app_services,
                    error=STALE_SUBMISSION_TOKEN_ERROR,
                    status_code=400,
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

    @app.post("/sample/{case_id}")
    async def sample_audit(request: Request, case_id: str) -> Response:
        """Run one committed sample fixture pair without the upload passphrase."""
        app_services: WebServices = app.state.services
        if not app_services.settings.bucket_name:
            raise HTTPException(status_code=503, detail="The runs bucket is not configured.")
        case = SAMPLE_AUDIT_CASES.get(case_id)
        if case is None:
            raise HTTPException(status_code=404, detail="Sample audit not found.")
        try:
            spec_bytes, cut_sheet_bytes = _sample_audit_bytes(case)
        except OSError:
            raise HTTPException(
                status_code=503, detail="The sample audit fixtures are unavailable."
            ) from None

        async with app_services.audit_slots:
            now = datetime.now(UTC)
            if not app_services.repository.reserve_sample_run(
                day=now.date().isoformat(),
                hour=now.strftime("%Y-%m-%dT%H:00Z"),
                client_ip=_client_ip(request, app_services.settings.trust_forwarded_for),
                hourly_limit=SAMPLE_RUNS_PER_IP_HOUR,
                daily_limit=SAMPLE_RUNS_PER_UTC_DAY,
            ):
                return _sample_limit_response(request)
            run_id = uuid.uuid4().hex
            try:
                run = await _run_audit(
                    app_services,
                    run_id=run_id,
                    spec_bytes=spec_bytes,
                    cut_sheet_bytes=cut_sheet_bytes,
                    source="sample",
                )
            except AuditFailedError as failure:
                return _render_index(
                    request,
                    app_services,
                    error="The sample audit did not complete.",
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
        run = _display_run(run)
        findings = _ledger_findings(app_services.repository.get_findings(run_id))
        rejections = app_services.repository.get_rejections(run_id)
        integrity_records = app_services.repository.get_integrity_records(run_id)
        return templates.TemplateResponse(
            request=request,
            name="run_detail.html",
            context={
                "run": run,
                "findings": _findings_with_context(app_services, run, findings),
                "rejections": [_rejection_view(rejection) for rejection in rejections],
                "integrity_records": integrity_records,
                "quotes_verified_meaning": QUOTES_VERIFIED_MEANING,
            },
        )

    @app.get("/runs/{run_id}/export.json")
    def export_run(run_id: str) -> Response:
        """Serve one run's persisted records as JSON, by an explicit field allowlist.

        Every field in the response is named in :func:`_export_payload`. The
        export is therefore built by addition, not by redaction: an ephemeral
        request path, an upload passphrase, or any other value that is not on
        the list cannot reach this response by being forgotten.
        """
        app_services: WebServices = app.state.services
        run = app_services.repository.get_run(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="Audit run not found.")
        payload = _export_payload(
            _display_run(run),
            findings=_ledger_findings(app_services.repository.get_findings(run_id)),
            rejections=app_services.repository.get_rejections(run_id),
            integrity_records=app_services.repository.get_integrity_records(run_id),
        )
        return Response(
            json.dumps(payload, indent=2, ensure_ascii=False),
            media_type="application/json",
            headers={"Content-Disposition": f'inline; filename="specguard-run-{run_id}.json"'},
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


def _sample_audit_bytes(case: SampleAuditCase) -> tuple[bytes, bytes]:
    """Read the committed specification and selected cut-sheet fixture from the image."""
    return (
        (FIXTURES_DIRECTORY / SAMPLE_SPECIFICATION_FILENAME).read_bytes(),
        (FIXTURES_DIRECTORY / case.cut_sheet_filename).read_bytes(),
    )


def _client_ip(request: Request, trust_forwarded_for: bool) -> str:
    """Return the client address used for the per-client rate limits.

    ``X-Forwarded-For`` is read only when the deployment says a proxy appends
    the real peer address to it. Cloud Run does, so ``deploy-specguard.ps1``
    sets ``SPECGUARD_TRUST_FORWARDED_FOR=1``. Reading the last entry rather
    than the first means a caller behind that proxy cannot claim a fresh limit
    bucket by sending its own header.

    With no such proxy the header is only what the caller typed, so honouring
    it would let anyone reset every limit by varying one header. A local or
    directly reachable deployment therefore ignores the header entirely and
    uses the ASGI peer address, which is the real client there.
    """
    if trust_forwarded_for:
        appended = request.headers.get("x-forwarded-for", "").rsplit(",", 1)[-1].strip()
        if appended:
            return appended
    return request.client.host if request.client is not None else "unknown"


def _sample_limit_response(request: Request) -> Response:
    """Return the rate-limit page for exhausted sample audit budgets.

    A refusal page that renders unstyled reads as a broken service rather than
    as a budget limit, so this one uses the same shell as every other page and
    points the reader at the gate playground, which needs no model call.
    """
    return _render_limit(
        request,
        heading="Too many sample audits",
        detail="The sample audit limit is reached. Try again later.",
        offer_gate=True,
    )


async def _run_audit(
    services: WebServices,
    *,
    run_id: str,
    spec_bytes: bytes,
    cut_sheet_bytes: bytes,
    source: str,
    submission_token: str | None = None,
) -> dict[str, Any]:
    """Store an auditable run record for one upload or committed sample pair."""
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
            source=source,
        )
        if submission_token is None:
            services.repository.create_run(pending_run)
        else:
            try:
                existing_run_id = services.repository.create_upload_run(
                    pending_run, submission_token, now=datetime.now(UTC)
                )
            except SubmissionTokenRefused:
                _delete_unrecorded_objects(services.storage, uploaded_names)
                raise
            if existing_run_id is not None:
                _delete_unrecorded_objects(services.storage, uploaded_names)
                raise UploadReplay(existing_run_id)
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
            source=source,
        )
        services.repository.create_run(run)
        return run
    except (UploadReplay, SubmissionTokenRefused):
        raise
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
    *, run_id: str, specification: StoredObject, submitted_document: StoredObject, source: str
) -> dict[str, Any]:
    """Build a run record before the audit can create durable derived data."""
    return {
        "run_id": run_id,
        "created_at": datetime.now(UTC),
        "source": source,
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
    source: str,
) -> dict[str, Any]:
    """Build the public Firestore run record without any ephemeral file paths."""
    return {
        "run_id": run_id,
        "created_at": created_at,
        "source": source,
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
            "audit_model_usage": (
                summary.audit_model_usage.model_dump(mode="json")
                if summary.audit_model_usage is not None
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
        "audit_model_usage": None,
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


def _display_run(run: dict[str, Any], now: datetime | None = None) -> dict[str, Any]:
    """Add read-side status fields without changing the Firestore run record."""
    displayed = dict(run)
    status = str(displayed.get("status", ""))
    created_at = displayed.get("created_at")
    reference_time = now or datetime.now(UTC)
    is_stalled = (
        status == "RUNNING"
        and isinstance(created_at, datetime)
        and created_at <= reference_time - RUN_STALLED_AFTER
    )
    displayed["display_status"] = "STALLED" if is_stalled else status
    displayed["is_stalled"] = is_stalled
    return displayed


CONTEXT_UNAVAILABLE_KEY = "context_unavailable"
CONTEXT_UNAVAILABLE = (
    "The stored source document could not be read, so no page context is shown here."
)


def _gate_input_error(fixture_id: str, page_text: str, quote_text: str) -> str | None:
    """Return the user-visible reason the playground cannot run this input."""
    if fixture_id not in GATE_FIXTURES:
        return "Choose one of the committed fixtures."
    if not page_text.strip().isdigit() or int(page_text) < 1:
        return "The page number must be a whole number of at least 1."
    if not quote_text.strip():
        return "Enter a quote to check."
    return None


def _gate_result_view(result: Any, selected: GateFixture) -> dict[str, Any]:
    """Render one gate verdict without disclosing the fixture's local path."""
    return {
        "verified": result.verified,
        "fixture_label": selected.label,
        "fixture_filename": selected.filename,
        "page_number": result.page_number,
        "page_count": result.page_count,
        "normalized_quote": result.normalized_quote,
        "rejection_reason": (
            result.rejection_reason.value if result.rejection_reason is not None else None
        ),
    }


def _gate_limit_response(request: Request) -> Response:
    """Return the rate-limit page for exhausted gate-playground budgets."""
    return _render_limit(
        request,
        heading="Too many gate checks",
        detail="The gate playground limit is reached. Try again later.",
        offer_gate=False,
    )


def _render_limit(request: Request, *, heading: str, detail: str, offer_gate: bool) -> Response:
    """Render one refused request as a page, not as a bare browser default."""
    return templates.TemplateResponse(
        request=request,
        name="limit.html",
        context={"heading": heading, "detail": detail, "offer_gate": offer_gate},
        status_code=429,
    )


def _render_gate(
    request: Request,
    *,
    fixture_id: str,
    page_text: str,
    quote_text: str,
    result: dict[str, Any] | None = None,
    error: str | None = None,
    status_code: int = 200,
) -> Response:
    """Render the gate playground with its form state and one verdict."""
    return templates.TemplateResponse(
        request=request,
        name="gate.html",
        context={
            "fixtures": GATE_FIXTURES,
            "selected_fixture": fixture_id,
            "page_text": page_text,
            "quote_text": quote_text,
            "near_misses": GATE_NEAR_MISSES,
            "default_fixture": GATE_DEFAULT_FIXTURE,
            "default_page": GATE_DEFAULT_PAGE,
            "default_quote": GATE_DEFAULT_QUOTE,
            "result": result,
            "error": error,
        },
        status_code=status_code,
    )


def _landing_page_runs(services: WebServices) -> list[dict[str, Any]]:
    """Return the newest sample runs, read from a bounded window of recent runs."""
    sample_runs = [
        _display_run(run)
        for run in services.repository.list_runs(LANDING_PAGE_RUN_SCAN)
        if run.get("source") == "sample"
    ]
    return sample_runs[:LANDING_PAGE_RUNS]


def _mint_submission_token(request: Request, services: WebServices) -> str | None:
    """Mint and record one upload submission token, within a bounded budget.

    Returns ``None`` when this address has exhausted its hourly mint budget, or
    when the request is a HEAD probe. The page then renders without an upload
    form and says so; the samples and the gate playground still work, because
    neither needs a token.
    """
    if request.method == "HEAD":
        return None
    now = datetime.now(UTC)
    if not services.repository.reserve_token_mint(
        hour=now.strftime("%Y-%m-%dT%H:00Z"),
        client_ip=_client_ip(request, services.settings.trust_forwarded_for),
        hourly_limit=TOKEN_MINTS_PER_IP_HOUR,
    ):
        return None
    submission_token = secrets.token_urlsafe(32)
    services.repository.mint_submission_token(
        submission_token, expires_at=now + SUBMISSION_TOKEN_LIFETIME
    )
    return submission_token


def _token_has_expired(record: dict[str, Any], now: datetime) -> bool:
    """Treat a token record with no readable expiry as expired, never as valid."""
    expires_at = record.get("expires_at")
    if not isinstance(expires_at, datetime):
        return True
    return expires_at <= now


def _ledger_findings(findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep only the findings records that carry a verified status.

    The findings collection is read by run identifier. Every record the audit
    path writes there carries ``verification_status == "verified"``, but the
    read side must not depend on that, because a record that lost the field, or
    that some other writer created, would otherwise be rendered under the same
    heading as a gate-verified finding. Each row then states its own stored
    status rather than inheriting the heading's.
    """
    return [
        finding
        for finding in findings
        if finding.get("verification_status") == LEDGER_VERIFICATION_STATUS
    ]


def _findings_with_context(
    services: WebServices, run: dict[str, Any], findings: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Attach one bounded window of cited-page text to each verified quote.

    The window comes from the gate's own extraction of the stored source
    document, normalized by the gate's own normalizer, so a reader sees the
    text the gate compared rather than a second rendering of it. A source
    document this service cannot read yields no window and says so.

    Building the windows downloads and reparses both stored PDFs, and run
    identifiers are public, so a reload would repeat that work for as long as
    anyone kept reloading. The result is cached per run. The cache key carries
    a digest of the anchors it was built from, so a run whose findings are
    still being written recomputes rather than serving a partial set, and no
    status test is needed. A failed read is never cached, because it can be
    transient and a stuck failure would outlast its cause.
    """
    views = [dict(finding) for finding in findings]
    if not views:
        return views
    key = (str(run.get("run_id", "")), _anchor_digest(views))
    contexts = services.quote_contexts.get(key)
    if contexts is None:
        contexts = _read_quote_contexts(services, run, views)
        if all(CONTEXT_UNAVAILABLE_KEY not in window_set for window_set in contexts):
            _remember_quote_contexts(services, key, contexts)
    else:
        services.quote_contexts.move_to_end(key)
    for view, window_set in zip(views, contexts, strict=True):
        view.update(window_set)
    return views


def _read_quote_contexts(
    services: WebServices, run: dict[str, Any], views: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Download both stored documents once and build every window from them."""
    documents = run.get("documents") or {}
    contexts: list[dict[str, Any]] = []
    try:
        with tempfile.TemporaryDirectory(prefix="specguard-context-") as directory:
            root = Path(directory)
            local_paths: dict[str, Path] = {}
            for role in ("specification", "submitted_document"):
                record = documents.get(role)
                if not record:
                    raise KeyError(role)
                local_path = root / f"{role}.pdf"
                local_path.write_bytes(services.storage.download_bytes(str(record["object_name"])))
                local_paths[role] = local_path
            page_cache: dict[tuple[str, int], str | None] = {}
            for view in views:
                contexts.append(
                    {
                        "spec_context": _quote_context(
                            local_paths["specification"],
                            view.get("spec_quote"),
                            page_cache,
                            "spec",
                        ),
                        "cut_sheet_context": _quote_context(
                            local_paths["submitted_document"],
                            view.get("cut_sheet_quote"),
                            page_cache,
                            "cut_sheet",
                        ),
                    }
                )
    except Exception:
        return [
            {
                "spec_context": None,
                "cut_sheet_context": None,
                CONTEXT_UNAVAILABLE_KEY: CONTEXT_UNAVAILABLE,
            }
            for _ in views
        ]
    return contexts


def _remember_quote_contexts(
    services: WebServices, key: tuple[str, str], contexts: list[dict[str, Any]]
) -> None:
    """Store one run's windows, evicting the least recently read run."""
    services.quote_contexts[key] = contexts
    services.quote_contexts.move_to_end(key)
    while len(services.quote_contexts) > QUOTE_CONTEXT_CACHE_SIZE:
        services.quote_contexts.popitem(last=False)


def _anchor_digest(views: list[dict[str, Any]]) -> str:
    """Digest exactly the anchors a window set is built from.

    Two reads of the same completed run produce the same digest and share one
    cache entry. A read taken while findings are still being written produces a
    different digest, so it never reuses a window set built from fewer anchors.
    """
    parts: list[str] = []
    for view in views:
        for role in ("spec_quote", "cut_sheet_quote"):
            quote = view.get(role)
            if isinstance(quote, dict):
                parts.append(
                    f"{quote.get('page_number')}\x1f{quote.get('document_sha256')}"
                    f"\x1f{quote.get('text')}"
                )
            else:
                parts.append("\x1f")
    return hashlib.sha256("\x1e".join(parts).encode("utf-8")).hexdigest()


def _quote_context(
    pdf_path: Path,
    quote_record: Any,
    page_cache: dict[tuple[str, int], str | None],
    role: str,
) -> dict[str, Any] | None:
    """Return one bounded page window for a persisted quote, or ``None``."""
    if not isinstance(quote_record, dict):
        return None
    page_number = int(quote_record["page_number"])
    key = (role, page_number)
    if key not in page_cache:
        try:
            page_cache[key] = gate.extract_page_text(pdf_path, page_number)
        except IndexError:
            page_cache[key] = None
    page_text = page_cache[key]
    if page_text is None:
        return None
    return context.quote_window(page_text, str(quote_record["text"])).model_dump(mode="json")


def _rejection_view(rejection: dict[str, Any]) -> dict[str, Any]:
    """Add the parsed gate feedback beside a stored rejection record."""
    view = dict(rejection)
    view["details"] = _rejection_details(str(rejection.get("reason", "")))
    return view


def _rejection_details(reason: str) -> list[dict[str, Any]]:
    """Parse the gate feedback the runtime recorded, or return an empty list.

    A rejection reason is either the JSON feedback the gate produced for one
    claim or a plain machine code such as ``model_output_invalid``. A plain
    code has no per-quote detail, and this function invents none.
    """
    try:
        parsed = json.loads(reason)
    except (TypeError, ValueError):
        return []
    if not isinstance(parsed, list):
        return []
    return [
        {
            "rejection_reason": str(entry.get("rejection_reason", "")),
            "normalized_quote": str(entry.get("normalized_quote", "")),
            "page_count": entry.get("page_count"),
        }
        for entry in parsed
        if isinstance(entry, dict)
    ]


def _isoformat(value: Any) -> str | None:
    """Render a stored timestamp for JSON, or ``None`` when there is none."""
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value) if value else None


def _export_payload(
    run: dict[str, Any],
    *,
    findings: list[dict[str, Any]],
    rejections: list[dict[str, Any]],
    integrity_records: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build the JSON export of one run from an explicit field allowlist."""
    summary = run.get("summary") or {}
    documents = run.get("documents") or {}
    rfi = run.get("rfi")
    return {
        "schema": "specguard.run.export.v1",
        "run": {
            "run_id": run.get("run_id"),
            "status": run.get("status"),
            "display_status": run.get("display_status"),
            "is_stalled": bool(run.get("is_stalled")),
            "source": run.get("source"),
            "created_at": _isoformat(run.get("created_at")),
        },
        "summary": {
            "claims_made": summary.get("claims_made"),
            "rejected": summary.get("rejected"),
            "retried": summary.get("retried"),
            "findings_persisted": summary.get("findings_persisted"),
            "failure": summary.get("failure"),
            "audit_model_usage": _export_usage(summary.get("audit_model_usage")),
            "quarantine": _export_quarantine(summary.get("quarantine")),
        },
        "documents": [
            {
                "role": role,
                "object_name": record.get("object_name"),
                "sha256": record.get("sha256"),
                "content_type": record.get("content_type"),
            }
            for role, record in documents.items()
            if isinstance(record, dict)
        ],
        "rfi": (
            {
                "object_name": rfi.get("object_name"),
                "sha256": rfi.get("sha256"),
                "content_type": rfi.get("content_type"),
            }
            if isinstance(rfi, dict)
            else None
        ),
        "findings": [_export_finding(finding) for finding in findings],
        "rejections": [
            {
                "claim_text": rejection.get("claim_text"),
                "reason": rejection.get("reason"),
                "details": _rejection_details(str(rejection.get("reason", ""))),
                "timestamp": _isoformat(rejection.get("timestamp")),
            }
            for rejection in rejections
        ],
        "integrity_records": [_export_integrity(record) for record in integrity_records],
        "exclusions": [
            "No filesystem path of any request copy, fixture, or generated file.",
            "No upload passphrase, submission token, or client address.",
            "No hidden-span data beyond the page number, text, font, and size the "
            "run page already shows to a human reviewer.",
        ],
    }


def _export_usage(usage: Any) -> dict[str, Any] | None:
    """Export the exact SDK token counts, or the recorded reason there are none."""
    if not isinstance(usage, dict):
        return None
    return {
        "prompt_tokens": usage.get("prompt_tokens"),
        "output_tokens": usage.get("output_tokens"),
        "total_tokens": usage.get("total_tokens"),
        "unavailable_reason": usage.get("unavailable_reason"),
    }


def _export_quarantine(quarantine: Any) -> dict[str, Any] | None:
    """Export the quarantine disclosure without any screened file path."""
    if not isinstance(quarantine, dict):
        return None
    documents = quarantine.get("documents")
    return {
        "reason": quarantine.get("reason"),
        "documents": [
            {
                "document_role": document.get("document_role"),
                "document_sha256": document.get("document_sha256"),
                "page_count": document.get("page_count"),
                "flagged_pages": document.get("flagged_pages"),
                "detectors": document.get("detectors"),
                "hidden_span_count": document.get("hidden_span_count"),
                "integrity_finding_id": document.get("integrity_finding_id"),
                "persistence_reason": document.get("persistence_reason"),
            }
            for document in (documents if isinstance(documents, list) else [])
            if isinstance(document, dict)
        ],
    }


def _export_finding(finding: dict[str, Any]) -> dict[str, Any]:
    """Export one persisted finding, its two anchors, and its severity record."""
    return {
        "finding_id": finding.get("finding_id"),
        "run_id": finding.get("run_id"),
        "submittal_id": finding.get("submittal_id"),
        "claim_text": finding.get("claim_text"),
        "verification_status": finding.get("verification_status"),
        "verification_meaning": QUOTES_VERIFIED_MEANING,
        "spec_locator": finding.get("spec_locator"),
        "cut_sheet_locator": finding.get("cut_sheet_locator"),
        "spec_quote": _export_quote(finding.get("spec_quote")),
        "cut_sheet_quote": _export_quote(finding.get("cut_sheet_quote")),
        "severity": {
            "label": finding.get("severity"),
            "status": finding.get("severity_status"),
            # model_id is what the endpoint reported. endpoint_label is what
            # this deployment was configured to call. They are separate fields
            # because a configured label is not an observation.
            "model_id": finding.get("severity_model_id"),
            "endpoint_label": finding.get("severity_endpoint_label"),
            "reason": finding.get("severity_reason"),
        },
        "created_at": _isoformat(finding.get("created_at")),
    }


def _export_quote(quote: Any) -> dict[str, Any] | None:
    """Export one verified anchor: its text, its page, and its document hash."""
    if not isinstance(quote, dict):
        return None
    return {
        "text": quote.get("text"),
        "page_number": quote.get("page_number"),
        "document_sha256": quote.get("document_sha256"),
    }


def _export_integrity(record: dict[str, Any]) -> dict[str, Any]:
    """Export one integrity record with the same span fields the run page shows."""
    spans = record.get("hidden_spans")
    return {
        "integrity_finding_id": record.get("integrity_finding_id"),
        "run_id": record.get("run_id"),
        "screen_id": record.get("screen_id"),
        "document_role": record.get("document_role"),
        "document_sha256": record.get("document_sha256"),
        "page_count": record.get("page_count"),
        "flagged_pages": record.get("flagged_pages"),
        "detectors": record.get("detectors"),
        "hidden_spans": [
            {
                "page_number": span.get("page_number"),
                "detector": span.get("detector"),
                "evidence": span.get("evidence"),
                "text": span.get("text"),
                "font": span.get("font"),
                "size": span.get("size"),
            }
            for span in (spans if isinstance(spans, list) else [])
            if isinstance(span, dict)
        ],
        "created_at": _isoformat(record.get("created_at")),
    }


def _render_index(
    request: Request,
    services: WebServices,
    *,
    error: str | None = None,
    failed_run_id: str | None = None,
    status_code: int = 200,
) -> Response:
    """Render the shared landing page with a controlled user-facing error.

    The recent-runs list carries sample runs only. An upload run is reachable
    by its own URL, which the uploader receives on the redirect, and by no
    other route: it is never listed, never linked, and never enumerated. A
    128-bit random run identifier is the whole access control, so listing one
    here would publish somebody else's submittal to every later visitor.
    """
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "runs": _landing_page_runs(services),
            "submission_token": _mint_submission_token(request, services),
            "error": error,
            "failed_run_id": failed_run_id,
        },
        status_code=status_code,
    )


#: The verdict ``GET /gate`` serves, verified once at import. See
#: :func:`_default_gate_result`.
GATE_DEFAULT_RESULT = _default_gate_result()

app = create_app()
