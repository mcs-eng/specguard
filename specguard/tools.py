"""The five deterministic tools owned by the SpecGuard ADK agent."""

from __future__ import annotations

import secrets
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
        self._drafted_rfis: dict[str, Path] = {}

    @property
    def spec_path(self) -> Path:
        """The resolved specification path this tool set is bound to."""
        return self._spec_path

    @property
    def cut_sheet_path(self) -> Path:
        """The resolved submitted-document path this tool set is bound to."""
        return self._cut_sheet_path

    def rfi_path_for(self, rfi_id: str) -> Path | None:
        """Resolve one drafted RFI's filesystem path for the deterministic runtime.

        This is the second channel for the RFI path, and it is not one of the
        five registered agent tools, so no model turn can reach it. The model
        receives the opaque identifier from :meth:`draft_rfi` and nothing else;
        the runtime exchanges that identifier for the ephemeral path here.
        Return ``None`` for an identifier this tool set did not issue.
        """
        return self._drafted_rfis.get(rfi_id)

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
        """Generate one human-review RFI draft PDF and return an opaque handle.

        This tool is model-callable, so it returns no filesystem path. The
        ephemeral output path is an operational detail of the machine running
        the audit, and disclosing it to a model hands back a writable location
        outside the two bound documents. The result carries an opaque
        identifier instead; the runtime exchanges it through
        :meth:`rfi_path_for`, which is not a registered tool.
        """
        if not findings:
            raise ValueError("an RFI requires at least one persisted finding")
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
        screens = self._screen_bound_documents()
        self._output_directory.mkdir(parents=True, exist_ok=True)
        output_path = self._output_directory / f"rfi-{self._run_id}.pdf"
        rfi_number = f"SG-{self._run_id[:8].upper()}"
        issued = self._now().strftime("%Y-%m-%d")

        document = pymupdf.open()
        writer = _RfiWriter(document, rfi_number)
        writer.heading("REQUEST FOR INFORMATION - DRAFT", size=16)
        writer.field_block(
            [
                ("RFI number", rfi_number),
                ("Project", project),
                ("Owner", owner),
                ("Submittal ID", self._run_id),
                ("Run ID", self._run_id),
                ("Date issued", issued),
                ("Findings in this draft", str(len(findings))),
            ]
        )
        writer.paragraph(
            "This runtime uses the audit run identifier as the submittal identifier, "
            "so those two fields carry the same value."
        )
        writer.space(8)
        writer.line("DRAFT - HUMAN REVIEW REQUIRED", bold=True)
        writer.paragraph(
            "This draft presents quoted text anchors for review. "
            "It does not establish that any finding is accurate."
        )
        writer.space(10)

        writer.heading("FINDINGS", size=12)
        writer.paragraph(
            "Every quote below was located on its cited page by the verification gate, "
            "once when the finding was written and once again before this page rendered."
        )
        writer.space(4)
        writer.table(
            ["Claim", "Specification quote", "Submitted quote", "Severity"],
            [_finding_row(finding) for finding in findings],
            [144.0, 130.0, 130.0, 100.0],
        )
        writer.space(4)
        writer.paragraph(
            "Severity is an advisory annotation applied after verification. "
            "It is not a compliance determination and it changes no verification status."
        )
        writer.space(10)

        writer.heading("TEXT-LAYER INTEGRITY SCREEN", size=12)
        writer.paragraph(
            "Both documents were screened for text that the PDF render mode keeps off "
            "the visible page. A flagged document stops its run before any model call, "
            "so a document listed here as flagged could not have produced this draft."
        )
        writer.space(4)
        writer.table(
            ["Document", "Screen", "Pages read", "Result"],
            screens,
            [150.0, 120.0, 70.0, 164.0],
        )
        writer.space(10)

        writer.heading("CHAIN-OF-CUSTODY METADATA", size=12)
        writer.paragraph(f"Specification document SHA-256: {spec_document.sha256}")
        writer.paragraph(f"Submitted document SHA-256: {cut_sheet_document.sha256}")
        writer.paragraph(
            "These hashes identify the source byte streams used for this run. "
            "They are chain-of-custody metadata only. They do not prove accuracy, "
            "and no part of the verification gate reads them."
        )
        writer.space(14)

        writer.reserve(130)
        writer.heading("REVIEW", size=12)
        writer.paragraph(
            "This draft is not issued until a human reviewer signs it. SpecGuard signs nothing."
        )
        writer.space(6)
        writer.signature_line("Reviewed by (print)", "Date")
        writer.signature_line("Signature", "Date")
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
        rfi_id = secrets.token_hex(16)
        self._drafted_rfis[rfi_id] = output_path.resolve()
        return {
            "rfi_id": rfi_id,
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

    def _screen_bound_documents(self) -> list[list[str]]:
        """Re-run the text-layer screen over both bound documents for the RFI.

        The screen result printed in the RFI is read from the files here, not
        copied from a caller. A reader of the draft therefore sees what the
        screen reports about the same bytes the chain-of-custody block names.
        """
        rows: list[list[str]] = []
        for role, path in (
            (DocumentRole.SPECIFICATION, self._spec_path),
            (DocumentRole.SUBMITTED_DOCUMENT, self._cut_sheet_path),
        ):
            report = integrity.check_text_layer(path)
            result = (
                "Clean: no span is hidden by render mode."
                if report.clean
                else f"FLAGGED on pages {report.flagged_pages}: "
                f"{len(report.hidden_spans)} hidden spans."
            )
            rows.append(
                [
                    role.value.replace("_", " "),
                    integrity.SCREEN_ID,
                    str(report.page_count),
                    result,
                ]
            )
        return rows


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

    def reserve(self, points: float) -> None:
        """Start a new page unless ``points`` of vertical room remain.

        A signature block split across a page break reads as two half-signed
        pages. Reserving the whole block keeps it together.
        """
        self._ensure(points)

    def field_block(self, fields: list[tuple[str, str]]) -> None:
        """Write a boxed header block, one ``Label: value`` line per field.

        Each label and its value share one text run. A split run would place
        them on separate extracted lines, so a reader of the extracted text
        would no longer see which value belongs to which label.
        """
        fontsize = 9.5
        line_height = fontsize + 4
        lines = [
            wrapped
            for label, value in fields
            for wrapped in self._wrap(
                f"{label}: {value}", fontname="helv", fontsize=fontsize, max_width=484.0
            )
        ]
        height = len(lines) * line_height + 14
        self._ensure(height + 6)
        top = self._y
        self._page.draw_rect(
            pymupdf.Rect(54, top, 558, top + height),
            color=(0.72, 0.76, 0.80),
            fill=(0.95, 0.96, 0.98),
            width=0.6,
        )
        y = top + 7 + fontsize
        for line in lines:
            self._page.insert_text((64, y), line, fontname="helv", fontsize=fontsize)
            y += line_height
        # ``_y`` is the baseline of the next line, so clearing the box border
        # needs the box height plus one line of ascent.
        self._y = top + height + 6 + fontsize

    def signature_line(self, left_label: str, right_label: str) -> None:
        """Draw two ruled signature fields side by side."""
        self._ensure(34)
        baseline = self._y + 16
        self._page.draw_line((54, baseline), (360, baseline), color=(0.4, 0.4, 0.4), width=0.6)
        self._page.draw_line((390, baseline), (558, baseline), color=(0.4, 0.4, 0.4), width=0.6)
        self._page.insert_text((54, baseline + 11), left_label, fontname="helv", fontsize=8)
        self._page.insert_text((390, baseline + 11), right_label, fontname="helv", fontsize=8)
        self._y = baseline + 26

    def table(self, headers: list[str], rows: list[list[str]], widths: list[float]) -> None:
        """Write a bordered table, repeating the header row after a page break."""
        fontsize = 8.0
        padding = 4.0
        self._write_table_header(headers, widths, fontsize, padding)
        for row in rows:
            cells = [
                self._wrap(
                    str(value), fontname="helv", fontsize=fontsize, max_width=width - 2 * padding
                )
                for value, width in zip(row, widths, strict=True)
            ]
            height = max(len(lines) for lines in cells) * (fontsize + 2.6) + 2 * padding
            if self._y + height > 730:
                self._new_page()
                self._write_table_header(headers, widths, fontsize, padding)
            self._write_table_row(cells, widths, fontsize, padding, height)
        # Leave the table's bottom border clear of the next baseline.
        self._y += fontsize + 4

    def _write_table_header(
        self, headers: list[str], widths: list[float], fontsize: float, padding: float
    ) -> None:
        height = fontsize + 2 * padding + 2
        self._ensure(height + 20)
        top = self._y
        self._page.draw_rect(
            pymupdf.Rect(54, top, 54 + sum(widths), top + height),
            color=(0.65, 0.65, 0.65),
            fill=(0.92, 0.94, 0.96),
            width=0.5,
        )
        x = 54.0
        for header, width in zip(headers, widths, strict=True):
            self._page.insert_text(
                (x + padding, top + padding + fontsize),
                header,
                fontname="hebo",
                fontsize=fontsize,
            )
            x += width
        self._y = top + height

    def _write_table_row(
        self,
        cells: list[list[str]],
        widths: list[float],
        fontsize: float,
        padding: float,
        height: float,
    ) -> None:
        top = self._y
        self._page.draw_rect(
            pymupdf.Rect(54, top, 54 + sum(widths), top + height),
            color=(0.78, 0.78, 0.78),
            width=0.5,
        )
        x = 54.0
        for lines, width in zip(cells, widths, strict=True):
            y = top + padding + fontsize
            for line in lines:
                self._page.insert_text((x + padding, y), line, fontname="helv", fontsize=fontsize)
                y += fontsize + 2.6
            x += width
        self._y = top + height

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

    def _write_wrapped(
        self, text: str, *, fontname: str, fontsize: float, left: float = 54.0
    ) -> None:
        lines = self._wrap(text, fontname=fontname, fontsize=fontsize, max_width=558.0 - left)
        line_height = fontsize + 3
        for line in lines:
            self._ensure(line_height)
            self._page.insert_text((left, self._y), line, fontname=fontname, fontsize=fontsize)
            self._y += line_height

    @staticmethod
    def _wrap(text: str, *, fontname: str, fontsize: float, max_width: float) -> list[str]:
        """Break text into lines that fit ``max_width`` at this font and size."""
        lines: list[str] = []
        current = ""
        pieces = [
            piece
            for word in text.split()
            for piece in _RfiWriter._split_long_word(
                word, fontname=fontname, fontsize=fontsize, max_width=max_width
            )
        ]
        for word in pieces:
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
        return lines

    @staticmethod
    def _split_long_word(
        word: str, *, fontname: str, fontsize: float, max_width: float
    ) -> list[str]:
        """Split one word that cannot fit a line, so it never runs past a border.

        A model identifier or a hash is one long token. Word wrapping alone
        leaves it hanging outside its table cell, so a token wider than the
        cell is broken by character instead.
        """
        if pymupdf.get_text_length(word, fontname=fontname, fontsize=fontsize) <= max_width:
            return [word]
        pieces: list[str] = []
        current = ""
        for character in word:
            candidate = current + character
            if (
                pymupdf.get_text_length(candidate, fontname=fontname, fontsize=fontsize) > max_width
                and current
            ):
                pieces.append(current)
                current = character
            else:
                current = candidate
        if current:
            pieces.append(current)
        return pieces


def _finding_row(finding: PersistedFinding) -> list[str]:
    """Render one persisted finding as four table cells."""
    severity_label = (
        finding.severity.value.upper()
        if isinstance(finding.severity, Severity)
        else str(finding.severity).upper()
    )
    severity_cell = [severity_label]
    if finding.severity_model_id:
        severity_cell.append(f"model: {finding.severity_model_id}")
    elif finding.severity_status:
        severity_cell.append(f"status: {finding.severity_status}")
    if finding.severity_reason:
        severity_cell.append(f"reason: {finding.severity_reason}")
    return [
        finding.claim_text,
        f'Page {finding.spec_quote.page_number}: "{finding.spec_quote.text}"',
        f'Page {finding.cut_sheet_quote.page_number}: "{finding.cut_sheet_quote.text}"',
        " - ".join(severity_cell),
    ]


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
