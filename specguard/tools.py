"""The five deterministic tools owned by the SpecGuard ADK agent."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pymupdf

from specguard import gate, integrity
from specguard.integrity import PersistedIntegrityFinding
from specguard.models import (
    CitedQuote,
    DocumentRole,
    Finding,
    PdfTextResult,
    PersistedFinding,
    PersistedQuote,
    Severity,
    VerificationStatus,
)

DOCUMENTS_COLLECTION = "documents"
FINDINGS_COLLECTION = "findings"
REJECTIONS_COLLECTION = "rejections"
INTEGRITY_FINDINGS_COLLECTION = "integrity_findings"


def _canonical_path(path: str | Path) -> Path:
    return Path(path).resolve(strict=True)


def _json_data(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    return value


class AuditTools:
    """Bind the five agent tools to one audit run and its two source PDFs."""

    def __init__(
        self,
        *,
        firestore_client: Any,
        spec_path: str | Path,
        cut_sheet_path: str | Path,
        run_id: str,
        output_directory: str | Path,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._firestore = firestore_client
        self._spec_path = _canonical_path(spec_path)
        self._cut_sheet_path = _canonical_path(cut_sheet_path)
        self._run_id = run_id
        self._output_directory = Path(output_directory)
        self._now = now or (lambda: datetime.now(UTC))

    @property
    def spec_path(self) -> Path:
        """The resolved specification path this tool set is bound to."""
        return self._spec_path

    @property
    def cut_sheet_path(self) -> Path:
        """The resolved submitted-document path this tool set is bound to."""
        return self._cut_sheet_path

    def check_text_integrity(self, document_role: str) -> dict[str, Any]:
        """Screen one bound document's text layer and return the flag summary.

        This tool is model-callable, so two limits apply to it and to nothing
        else in :mod:`specguard.integrity`. It accepts a bound role rather than
        a path, so it cannot be pointed at a file this run is not bound to. It
        returns the flag summary only — never the hidden span text — because
        returning that text to a model would reopen the disclosure the screen
        exists to close. The full evidence goes to the deterministic integrity
        record, which no model reads.
        """
        try:
            role = DocumentRole(document_role)
        except ValueError:
            return {"ok": False, "error_code": "unknown_document_role"}

        path = self._spec_path if role is DocumentRole.SPECIFICATION else self._cut_sheet_path
        try:
            report = integrity.check_text_layer(path)
        except (FileNotFoundError, OSError, RuntimeError) as error:
            return {
                "ok": False,
                "error_code": "document_unreadable",
                "error_message": str(error),
            }
        return {
            "ok": True,
            "screen_id": integrity.SCREEN_ID,
            "document_role": role.value,
            "document_sha256": report.sha256,
            "page_count": report.page_count,
            "clean": report.clean,
            "flagged_pages": report.flagged_pages,
            "hidden_span_count": len(report.hidden_spans),
        }

    def extract_pdf_text(self, document_role: str, page_number: int) -> dict[str, Any]:
        """Extract one page from a bound document and return errors as data.

        The model names a role, never a path. It therefore cannot read another
        file through this agent tool.
        """
        role, path = self._path_for_role(document_role)
        if role is None or path is None:
            return {"ok": False, "error_code": "unknown_document_role"}
        try:
            text = gate.extract_page_text(path, page_number)
        except IndexError as error:
            return PdfTextResult(
                ok=False,
                document_role=role,
                page_number=page_number,
                error_code="page_out_of_range",
                error_message=str(error),
            ).model_dump(mode="json")
        return PdfTextResult(
            ok=True,
            document_role=role,
            page_number=page_number,
            text=text,
        ).model_dump(mode="json")

    def verify_quote(self, quote: str, page_number: int, document_role: str) -> dict[str, Any]:
        """Verify a quote on a page of a bound document.

        The model names a role, never a path. The gate verdict is returned
        unchanged except that ``pdf_path`` is removed, so the ephemeral request
        path of the bound document never reaches the model. The gate itself is
        untouched, and every other verdict field is passed through.
        """
        _, path = self._path_for_role(document_role)
        if path is None:
            return {"verified": False, "error_code": "unknown_document_role"}
        result = gate.verify_quote(quote, page_number, path).model_dump(mode="json")
        result.pop("pdf_path", None)
        return result

    def persist_finding(self, finding: Finding) -> dict[str, Any]:
        """Re-verify both bound source quotes before one atomic Firestore write."""
        bound_quotes = self._bind_source_quotes(finding)
        if isinstance(bound_quotes, str):
            return {"persisted": False, "reason": bound_quotes, "verification_results": []}

        spec_quote, cut_sheet_quote = bound_quotes
        spec_document_before = gate.build_document_record(self._spec_path)
        cut_sheet_document_before = gate.build_document_record(self._cut_sheet_path)
        verification_results = [
            gate.verify_quote(spec_quote.text, spec_quote.page_number, self._spec_path),
            gate.verify_quote(
                cut_sheet_quote.text, cut_sheet_quote.page_number, self._cut_sheet_path
            ),
        ]
        rejected = [result for result in verification_results if not result.verified]
        if rejected:
            return {
                "persisted": False,
                "reason": "one_or_more_quotes_rejected_by_gate",
                "verification_results": [_json_data(result) for result in verification_results],
            }

        spec_document = gate.build_document_record(self._spec_path)
        cut_sheet_document = gate.build_document_record(self._cut_sheet_path)
        if (
            spec_document.sha256 != spec_document_before.sha256
            or cut_sheet_document.sha256 != cut_sheet_document_before.sha256
        ):
            return {
                "persisted": False,
                "reason": "source_document_changed_during_verification",
                "verification_results": [_json_data(result) for result in verification_results],
            }
        finding_ref = self._firestore.collection(FINDINGS_COLLECTION).document()
        persisted = PersistedFinding(
            finding_id=finding_ref.id,
            run_id=self._run_id,
            claim_text=finding.claim_text,
            spec_quote=PersistedQuote(
                text=spec_quote.text,
                page_number=spec_quote.page_number,
                document_sha256=spec_document.sha256,
            ),
            cut_sheet_quote=PersistedQuote(
                text=cut_sheet_quote.text,
                page_number=cut_sheet_quote.page_number,
                document_sha256=cut_sheet_document.sha256,
            ),
            severity=Severity.UNCLASSIFIED,
        )
        finding_data = {
            **persisted.model_dump(mode="json"),
            "submittal_id": finding.submittal_id,
            "spec_locator": finding.spec_locator,
            "cut_sheet_locator": finding.cut_sheet_locator,
            "verification_status": VerificationStatus.VERIFIED.value,
            "created_at": self._now(),
        }

        batch = self._firestore.batch()
        batch.set(
            self._firestore.collection(DOCUMENTS_COLLECTION).document(spec_document.sha256),
            spec_document.model_dump(mode="json"),
            merge=True,
        )
        batch.set(
            self._firestore.collection(DOCUMENTS_COLLECTION).document(cut_sheet_document.sha256),
            cut_sheet_document.model_dump(mode="json"),
            merge=True,
        )
        batch.set(finding_ref, finding_data)
        batch.commit()
        return {
            "persisted": True,
            "finding": persisted.model_dump(mode="json"),
            "verification_results": [_json_data(result) for result in verification_results],
        }

    def update_finding_severity(
        self,
        finding_id: str,
        severity: Severity,
        *,
        model_id: str | None = None,
        status: str | None = None,
        reason: str | None = None,
    ) -> dict[str, Any]:
        """Update only severity, provenance, and classification status on a persisted finding.

        This method updates severity, severity_model_id, severity_status, and
        severity_reason only. It cannot modify verification_status, rejection_reason,
        quotes, or claims.
        """
        finding_ref = self._firestore.collection(FINDINGS_COLLECTION).document(finding_id)
        update_data: dict[str, Any] = {
            "severity": (
                severity.value if isinstance(severity, Severity) else str(severity).lower()
            ),
            "severity_model_id": model_id,
            "severity_status": status,
            "severity_reason": reason,
        }
        if hasattr(finding_ref, "update"):
            finding_ref.update(update_data)
        else:
            finding_ref.set(update_data, merge=True)
        return {
            "updated": True,
            "finding_id": finding_id,
            "severity": update_data["severity"],
            "severity_model_id": model_id,
            "severity_status": status,
            "severity_reason": reason,
        }

    def persist_integrity_finding(self, document_role: str) -> dict[str, Any]:
        """Re-screen one bound document and write its integrity record.

        The caller names a role and nothing else. Every stored value — the
        hidden span text, the page numbers, the SHA-256 — is read from the file
        again here, so no caller and no model can author or edit this record. A
        document that screens clean is refused, and no write happens.
        """
        try:
            role = DocumentRole(document_role)
        except ValueError:
            return {"persisted": False, "reason": "unknown_document_role"}

        path = self._spec_path if role is DocumentRole.SPECIFICATION else self._cut_sheet_path
        document_before = gate.build_document_record(path)
        report = integrity.check_text_layer(path)
        document_after = gate.build_document_record(path)
        if (
            document_before.sha256 != document_after.sha256
            or report.sha256 != document_before.sha256
        ):
            return {"persisted": False, "reason": "source_document_changed_during_screening"}
        if report.clean:
            return {"persisted": False, "reason": "document_carries_no_hidden_span"}

        integrity_ref = self._firestore.collection(INTEGRITY_FINDINGS_COLLECTION).document()
        persisted = PersistedIntegrityFinding(
            integrity_finding_id=integrity_ref.id,
            run_id=self._run_id,
            screen_id=integrity.SCREEN_ID,
            document_role=role,
            document_sha256=report.sha256,
            page_count=report.page_count,
            flagged_pages=report.flagged_pages,
            hidden_spans=report.hidden_spans,
        )
        integrity_data = {
            **persisted.model_dump(mode="json"),
            "created_at": self._now(),
        }

        batch = self._firestore.batch()
        batch.set(
            self._firestore.collection(DOCUMENTS_COLLECTION).document(document_after.sha256),
            document_after.model_dump(mode="json"),
            merge=True,
        )
        batch.set(integrity_ref, integrity_data)
        batch.commit()
        return {
            "persisted": True,
            "integrity_finding": persisted.model_dump(mode="json"),
        }

    def draft_rfi(self, findings: list[PersistedFinding]) -> dict[str, Any]:
        """Generate one human-review RFI draft PDF for this audit run."""
        spec_document = gate.build_document_record(self._spec_path)
        cut_sheet_document = gate.build_document_record(self._cut_sheet_path)
        if any(
            finding.spec_quote.document_sha256 != spec_document.sha256
            or finding.cut_sheet_quote.document_sha256 != cut_sheet_document.sha256
            for finding in findings
        ):
            raise ValueError("finding document hashes do not match the bound source PDFs")
        for finding in findings:
            verification_results = [
                gate.verify_quote(
                    finding.spec_quote.text,
                    finding.spec_quote.page_number,
                    self._spec_path,
                ),
                gate.verify_quote(
                    finding.cut_sheet_quote.text,
                    finding.cut_sheet_quote.page_number,
                    self._cut_sheet_path,
                ),
            ]
            if any(not result.verified for result in verification_results):
                raise ValueError("finding contains a quote rejected by the verification gate")
        project, owner = self._read_project_header()
        self._output_directory.mkdir(parents=True, exist_ok=True)
        output_path = self._output_directory / f"rfi-{self._run_id}.pdf"
        rfi_number = f"SG-{self._run_id[:8].upper()}"

        document = pymupdf.open()
        writer = _RfiWriter(document, rfi_number)
        writer.heading("REQUEST FOR INFORMATION - DRAFT", size=16)
        writer.line(f"RFI number: {rfi_number}", bold=True)
        writer.line(f"Project: {project}")
        writer.line(f"Owner: {owner}")
        writer.space(8)
        writer.line("DRAFT - HUMAN REVIEW REQUIRED", bold=True)
        writer.paragraph(
            "This draft presents quoted text anchors for review. "
            "It does not establish that any finding is accurate."
        )
        writer.space(8)

        if not findings:
            writer.heading("FINDINGS", size=12)
            writer.paragraph(
                "No findings were persisted for this run. "
                "This statement is not a compliance determination."
            )
        else:
            for index, finding in enumerate(findings, start=1):
                writer.heading(f"FINDING {index}", size=12)
                writer.paragraph(finding.claim_text)
                severity_val = (
                    finding.severity.value.upper()
                    if isinstance(finding.severity, Severity)
                    else str(finding.severity).upper()
                )
                if finding.severity_model_id:
                    writer.line(
                        f"Severity: {severity_val} (model: {finding.severity_model_id})", bold=True
                    )
                else:
                    writer.line(f"Severity: {severity_val}", bold=True)
                writer.line(
                    f"Specification quote - page {finding.spec_quote.page_number}", bold=True
                )
                writer.paragraph(f'"{finding.spec_quote.text}"')
                writer.line(
                    f"Submitted document quote - page {finding.cut_sheet_quote.page_number}",
                    bold=True,
                )
                writer.paragraph(f'"{finding.cut_sheet_quote.text}"')
                writer.space(8)

        writer.heading("CHAIN-OF-CUSTODY METADATA", size=12)
        writer.paragraph(f"Specification document SHA-256: {spec_document.sha256}")
        writer.paragraph(f"Submitted document SHA-256: {cut_sheet_document.sha256}")
        writer.paragraph(
            "These hashes identify the source byte streams used for this run. "
            "They do not prove accuracy."
        )
        writer.finish()
        document.set_metadata(
            {
                "title": f"SpecGuard RFI Draft {rfi_number}",
                "author": "SpecGuard",
                "subject": "Draft for human review",
                "keywords": "draft, human review, chain of custody",
            }
        )
        document.save(str(output_path), garbage=4, deflate=True)
        document.close()
        return {
            "rfi_path": str(output_path.resolve()),
            "rfi_number": rfi_number,
            "finding_count": len(findings),
        }

    def record_rejection(self, claim_text: str, reason: str) -> str:
        """Write a final rejected claim outside the findings ledger."""
        rejection_ref = self._firestore.collection(REJECTIONS_COLLECTION).document()
        rejection_ref.set(
            {
                "run_id": self._run_id,
                "claim_text": claim_text,
                "reason": reason,
                "timestamp": self._now(),
            }
        )
        return rejection_ref.id

    def _path_for_role(self, document_role: str) -> tuple[DocumentRole | None, Path | None]:
        """Return one bound source path for a valid document role."""
        try:
            role = DocumentRole(document_role)
        except ValueError:
            return None, None
        return role, self._spec_path if role is DocumentRole.SPECIFICATION else self._cut_sheet_path

    def _bind_source_quotes(self, finding: Finding) -> tuple[CitedQuote, CitedQuote] | str:
        if len(finding.quotes) != 2:
            return "finding_must_have_exactly_two_quotes"

        spec_quotes: list[CitedQuote] = []
        cut_sheet_quotes: list[CitedQuote] = []
        for quote in finding.quotes:
            try:
                quote_path = _canonical_path(quote.document_path)
            except (FileNotFoundError, OSError):
                return "cited_document_does_not_exist"
            if quote_path == self._spec_path:
                spec_quotes.append(quote)
            elif quote_path == self._cut_sheet_path:
                cut_sheet_quotes.append(quote)
            else:
                return "cited_document_is_not_bound_to_this_audit"

        if len(spec_quotes) != 1 or len(cut_sheet_quotes) != 1:
            return "finding_must_cite_each_bound_document_once"
        return spec_quotes[0], cut_sheet_quotes[0]

    def _read_project_header(self) -> tuple[str, str]:
        text = gate.extract_page_text(self._spec_path, 1)
        project = _value_after_label(text, "Project:") or "Not identified in source text"
        owner = _value_after_label(text, "Owner:") or "Not identified in source text"
        return project, owner


class _RfiWriter:
    """Small text writer with deterministic wrapping and page breaks."""

    def __init__(self, document: pymupdf.Document, rfi_number: str) -> None:
        self._document = document
        self._rfi_number = rfi_number
        self._page: pymupdf.Page
        self._y: float
        self._new_page()

    def heading(self, text: str, *, size: float) -> None:
        self._ensure(size + 12)
        self._page.insert_text((54, self._y), text, fontname="hebo", fontsize=size)
        self._y += size + 8

    def line(self, text: str, *, bold: bool = False) -> None:
        self._write_wrapped(text, fontname="hebo" if bold else "helv", fontsize=9.5)

    def paragraph(self, text: str) -> None:
        self._write_wrapped(text, fontname="helv", fontsize=9.5)
        self._y += 3

    def space(self, points: float) -> None:
        self._ensure(points)
        self._y += points

    def finish(self) -> None:
        for page_number, page in enumerate(self._document, start=1):
            page.draw_line((54, 748), (558, 748), color=(0.65, 0.65, 0.65), width=0.5)
            page.insert_text(
                (54, 765),
                f"SpecGuard RFI draft {self._rfi_number}",
                fontname="helv",
                fontsize=8,
                color=(0.35, 0.35, 0.35),
            )
            page.insert_text(
                (520, 765),
                f"Page {page_number}",
                fontname="helv",
                fontsize=8,
                color=(0.35, 0.35, 0.35),
            )

    def _new_page(self) -> None:
        self._page = self._document.new_page(width=612, height=792)
        self._y = 54

    def _ensure(self, height: float) -> None:
        if self._y + height > 730:
            self._new_page()

    def _write_wrapped(self, text: str, *, fontname: str, fontsize: float) -> None:
        max_width = 504.0
        words = text.split()
        lines: list[str] = []
        current = ""
        for word in words:
            candidate = word if not current else f"{current} {word}"
            if (
                pymupdf.get_text_length(candidate, fontname=fontname, fontsize=fontsize)
                <= max_width
            ):
                current = candidate
            else:
                if current:
                    lines.append(current)
                current = word
        if current or not lines:
            lines.append(current)

        line_height = fontsize + 3
        for line in lines:
            self._ensure(line_height)
            self._page.insert_text((54, self._y), line, fontname=fontname, fontsize=fontsize)
            self._y += line_height


def _value_after_label(text: str, label: str) -> str | None:
    for line in text.splitlines():
        if label not in line:
            continue
        value = line.split(label, maxsplit=1)[1].strip()
        if "|" in value:
            value = value.split("|", maxsplit=1)[0].strip()
        if value:
            return value
    return None
