"""Unit tests for the five deterministic SpecGuard tools."""

from __future__ import annotations

import hashlib
import inspect
from datetime import UTC, datetime
from pathlib import Path

import pymupdf
import pytest

from specguard import gate as gate_module
from specguard import integrity
from specguard.agent import create_adk_agent, load_audit_prompt
from specguard.gate import verify_quote
from specguard.integrity import check_text_layer
from specguard.models import (
    CitedQuote,
    Finding,
    PersistedFinding,
    PersistedQuote,
    VerificationStatus,
)
from specguard.tools import (
    DOCUMENTS_COLLECTION,
    FINDINGS_COLLECTION,
    INTEGRITY_FINDINGS_COLLECTION,
    REJECTIONS_COLLECTION,
    AuditTools,
)
from tests.fake_firestore import FakeFirestoreClient
from tests.fixtures_pdf import write_pdf

FIXED_TIME = datetime(2026, 8, 20, 12, 0, tzinfo=UTC)


def _source_pdfs(tmp_path: Path) -> tuple[Path, Path]:
    spec = write_pdf(
        tmp_path / "spec.pdf",
        [["Project: Fictional Workshop | Owner: Fictional Public Authority", "Requirement alpha."]],
    )
    cut_sheet = write_pdf(tmp_path / "cut.pdf", [["Submitted characteristic beta."]])
    return spec, cut_sheet


def _tools(
    tmp_path: Path,
    client: FakeFirestoreClient,
    spec: Path,
    cut_sheet: Path,
) -> AuditTools:
    return AuditTools(
        firestore_client=client,
        spec_path=spec,
        cut_sheet_path=cut_sheet,
        run_id="run-1234abcd",
        output_directory=tmp_path / "artifacts",
        now=lambda: FIXED_TIME,
    )


def _finding(
    spec: Path,
    cut_sheet: Path,
    *,
    spec_quote: str = "Requirement alpha.",
    cut_sheet_quote: str = "Submitted characteristic beta.",
    status: VerificationStatus = VerificationStatus.PENDING,
) -> Finding:
    return Finding(
        submittal_id="run-1234abcd",
        spec_locator="Page 1",
        cut_sheet_locator="Page 1",
        claim_text="The submitted characteristic conflicts with the requirement.",
        quotes=[
            CitedQuote(text=spec_quote, page_number=1, document_path=str(spec)),
            CitedQuote(text=cut_sheet_quote, page_number=1, document_path=str(cut_sheet)),
        ],
        verification_status=status,
    )


def test_extract_pdf_text_returns_page_text(tmp_path: Path) -> None:
    client = FakeFirestoreClient()
    spec, cut_sheet = _source_pdfs(tmp_path)
    result = _tools(tmp_path, client, spec, cut_sheet).extract_pdf_text(str(spec), 1)
    assert result["ok"] is True
    assert "Requirement alpha." in result["text"]
    assert result["error_code"] is None


def test_extract_pdf_text_catches_bad_page(tmp_path: Path) -> None:
    client = FakeFirestoreClient()
    spec, cut_sheet = _source_pdfs(tmp_path)
    result = _tools(tmp_path, client, spec, cut_sheet).extract_pdf_text(str(spec), 2)
    assert result == {
        "ok": False,
        "pdf_path": str(spec),
        "page_number": 2,
        "text": None,
        "error_code": "page_out_of_range",
        "error_message": "page 2 is outside a 1-page document",
    }


def test_verify_quote_tool_returns_the_gate_result_unchanged(tmp_path: Path) -> None:
    client = FakeFirestoreClient()
    spec, cut_sheet = _source_pdfs(tmp_path)
    tools = _tools(tmp_path, client, spec, cut_sheet)
    expected = verify_quote("Requirement alpha.", 1, spec).model_dump(mode="json")
    assert tools.verify_quote("Requirement alpha.", 1, str(spec)) == expected


def test_agent_owns_exactly_the_five_required_tools(tmp_path: Path) -> None:
    client = FakeFirestoreClient()
    spec, cut_sheet = _source_pdfs(tmp_path)
    agent = create_adk_agent(_tools(tmp_path, client, spec, cut_sheet), project_id="test-project")
    assert [tool.__name__ for tool in agent.tools] == [
        "check_text_integrity",
        "extract_pdf_text",
        "verify_quote",
        "persist_finding",
        "draft_rfi",
    ]


def test_prompt_is_generic_and_contains_no_fixture_hints() -> None:
    prompt = load_audit_prompt().casefold()
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
    ]
    assert all(term not in prompt for term in forbidden)
    assert "verbatim quote" in prompt
    assert "one-based page" in prompt


def test_persist_finding_reverifies_and_writes_documents_by_hash(tmp_path: Path) -> None:
    client = FakeFirestoreClient()
    spec, cut_sheet = _source_pdfs(tmp_path)
    result = _tools(tmp_path, client, spec, cut_sheet).persist_finding(
        _finding(spec, cut_sheet, status=VerificationStatus.VERIFIED)
    )

    assert result["persisted"] is True
    assert len(client.data[DOCUMENTS_COLLECTION]) == 2
    assert len(client.data[FINDINGS_COLLECTION]) == 1
    stored = next(iter(client.data[FINDINGS_COLLECTION].values()))
    spec_hash = hashlib.sha256(spec.read_bytes()).hexdigest()
    cut_hash = hashlib.sha256(cut_sheet.read_bytes()).hexdigest()
    assert stored["spec_quote"]["document_sha256"] == spec_hash
    assert stored["cut_sheet_quote"]["document_sha256"] == cut_hash
    assert "document_path" not in stored["spec_quote"]
    assert "document_path" not in stored["cut_sheet_quote"]
    assert client.data[DOCUMENTS_COLLECTION][spec_hash]["path"] == str(spec.resolve())
    assert client.data[DOCUMENTS_COLLECTION][cut_hash]["path"] == str(cut_sheet.resolve())
    assert stored["created_at"] == FIXED_TIME
    assert client.batches[0].committed is True


def test_persist_finding_refuses_unverified_quote_without_any_write(tmp_path: Path) -> None:
    client = FakeFirestoreClient()
    spec, cut_sheet = _source_pdfs(tmp_path)
    result = _tools(tmp_path, client, spec, cut_sheet).persist_finding(
        _finding(spec, cut_sheet, cut_sheet_quote="Invented submitted quote.")
    )

    assert result["persisted"] is False
    assert result["reason"] == "one_or_more_quotes_rejected_by_gate"
    assert result["verification_results"][0]["verified"] is True
    assert result["verification_results"][1]["verified"] is False
    assert client.data == {}
    assert client.batches == []


def test_persist_finding_ignores_a_hand_set_verified_status(tmp_path: Path) -> None:
    client = FakeFirestoreClient()
    spec, cut_sheet = _source_pdfs(tmp_path)
    result = _tools(tmp_path, client, spec, cut_sheet).persist_finding(
        _finding(
            spec,
            cut_sheet,
            cut_sheet_quote="Invented submitted quote.",
            status=VerificationStatus.VERIFIED,
        )
    )
    assert result["persisted"] is False
    assert FINDINGS_COLLECTION not in client.data


def test_persist_finding_refuses_a_document_changed_during_verification(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = FakeFirestoreClient()
    spec, cut_sheet = _source_pdfs(tmp_path)
    original_verify = gate_module.verify_quote

    def verify_then_change(
        quote: str, page_number: int, pdf_path: str | Path
    ) -> gate_module.VerificationResult:
        result = original_verify(quote, page_number, pdf_path)
        if Path(pdf_path).resolve() == cut_sheet.resolve():
            write_pdf(cut_sheet, [["The submitted characteristic changed after verification."]])
        return result

    monkeypatch.setattr(gate_module, "verify_quote", verify_then_change)
    result = _tools(tmp_path, client, spec, cut_sheet).persist_finding(_finding(spec, cut_sheet))

    assert result["persisted"] is False
    assert result["reason"] == "source_document_changed_during_verification"
    assert client.data == {}
    assert client.batches == []


def test_persist_finding_refuses_an_unbound_document(tmp_path: Path) -> None:
    client = FakeFirestoreClient()
    spec, cut_sheet = _source_pdfs(tmp_path)
    other = write_pdf(tmp_path / "other.pdf", [["Requirement alpha."]])
    finding = _finding(spec, cut_sheet).model_copy(
        update={
            "quotes": [
                CitedQuote(text="Requirement alpha.", page_number=1, document_path=str(other)),
                CitedQuote(
                    text="Submitted characteristic beta.",
                    page_number=1,
                    document_path=str(cut_sheet),
                ),
            ]
        }
    )
    result = _tools(tmp_path, client, spec, cut_sheet).persist_finding(finding)
    assert result["persisted"] is False
    assert result["reason"] == "cited_document_is_not_bound_to_this_audit"
    assert client.data == {}


def test_rejection_writes_only_to_the_rejections_collection(tmp_path: Path) -> None:
    client = FakeFirestoreClient()
    spec, cut_sheet = _source_pdfs(tmp_path)
    rejection_id = _tools(tmp_path, client, spec, cut_sheet).record_rejection(
        "Unsupported claim", "quote_not_found_on_cited_page"
    )
    assert rejection_id == "rejections-1"
    assert FINDINGS_COLLECTION not in client.data
    stored = client.data[REJECTIONS_COLLECTION][rejection_id]
    assert stored == {
        "run_id": "run-1234abcd",
        "claim_text": "Unsupported claim",
        "reason": "quote_not_found_on_cited_page",
        "timestamp": FIXED_TIME,
    }


def test_draft_rfi_contains_required_evidence_and_hash_metadata(tmp_path: Path) -> None:
    client = FakeFirestoreClient()
    spec, cut_sheet = _source_pdfs(tmp_path)
    spec_hash = hashlib.sha256(spec.read_bytes()).hexdigest()
    cut_hash = hashlib.sha256(cut_sheet.read_bytes()).hexdigest()
    finding = PersistedFinding(
        finding_id="finding-1",
        run_id="run-1234abcd",
        claim_text="The submitted characteristic conflicts with the requirement.",
        spec_quote=PersistedQuote(
            text="Requirement alpha.", page_number=1, document_sha256=spec_hash
        ),
        cut_sheet_quote=PersistedQuote(
            text="Submitted characteristic beta.", page_number=1, document_sha256=cut_hash
        ),
    )

    result = _tools(tmp_path, client, spec, cut_sheet).draft_rfi([finding])
    with pymupdf.open(result["rfi_path"]) as document:
        text = "\n".join(page.get_text() for page in document)

    assert result["rfi_number"] == "SG-RUN-1234"
    assert "Project: Fictional Workshop" in text
    assert "Owner: Fictional Public Authority" in text
    assert "Severity: UNCLASSIFIED" in text
    assert "Specification quote - page 1" in text
    assert "Submitted document quote - page 1" in text
    assert "Requirement alpha." in text
    assert "Submitted characteristic beta." in text
    assert "CHAIN-OF-CUSTODY METADATA" in text
    assert spec_hash in text
    assert cut_hash in text
    assert "They do not prove accuracy." in text


def test_draft_rfi_refuses_hashes_that_do_not_match_the_bound_sources(tmp_path: Path) -> None:
    client = FakeFirestoreClient()
    spec, cut_sheet = _source_pdfs(tmp_path)
    finding = PersistedFinding(
        finding_id="finding-1",
        run_id="run-1234abcd",
        claim_text="The submitted characteristic conflicts with the requirement.",
        spec_quote=PersistedQuote(
            text="Requirement alpha.", page_number=1, document_sha256="ab" * 32
        ),
        cut_sheet_quote=PersistedQuote(
            text="Submitted characteristic beta.", page_number=1, document_sha256="cd" * 32
        ),
    )
    with pytest.raises(ValueError, match="do not match the bound source PDFs"):
        _tools(tmp_path, client, spec, cut_sheet).draft_rfi([finding])


def test_draft_rfi_with_no_findings_avoids_a_compliance_claim(tmp_path: Path) -> None:
    client = FakeFirestoreClient()
    spec, cut_sheet = _source_pdfs(tmp_path)
    result = _tools(tmp_path, client, spec, cut_sheet).draft_rfi([])
    with pymupdf.open(result["rfi_path"]) as document:
        text = "\n".join(page.get_text() for page in document)
    assert "No findings were persisted for this run." in text
    assert "not a compliance determination" in text


def test_check_text_integrity_tool_returns_the_screen_report(tmp_path: Path) -> None:
    """The tool hands back the deterministic screen report for one document."""
    client = FakeFirestoreClient()
    spec, cut_sheet = _source_pdfs(tmp_path)
    result = _tools(tmp_path, client, spec, cut_sheet).check_text_integrity(str(spec))

    assert result["ok"] is True
    assert result["screen_id"] == integrity.SCREEN_ID
    assert result["report"] == check_text_layer(spec).model_dump(mode="json")
    assert result["report"]["clean"] is True


def test_check_text_integrity_tool_reports_an_unreadable_document(tmp_path: Path) -> None:
    """A missing document returns a screening error rather than raising."""
    client = FakeFirestoreClient()
    spec, cut_sheet = _source_pdfs(tmp_path)
    result = _tools(tmp_path, client, spec, cut_sheet).check_text_integrity(
        str(tmp_path / "absent.pdf")
    )

    assert result["ok"] is False
    assert result["error_code"] == "document_unreadable"


def test_check_text_integrity_tool_flags_a_hidden_span(tmp_path: Path) -> None:
    """A document with an invisible span is reported with its span text."""
    client = FakeFirestoreClient()
    spec, _ = _source_pdfs(tmp_path)
    altered = write_pdf(
        tmp_path / "altered.pdf",
        [["Submitted characteristic beta."]],
        hidden={1: ["Submitted characteristic gamma."]},
    )
    result = _tools(tmp_path, client, spec, altered).check_text_integrity(str(altered))

    assert result["report"]["clean"] is False
    assert result["report"]["flagged_pages"] == [1]
    span = result["report"]["pages"][0]["hidden_spans"][0]
    assert span["text"] == "Submitted characteristic gamma."


def test_persist_integrity_finding_refuses_a_clean_document(tmp_path: Path) -> None:
    """A document with no hidden span produces no integrity record."""
    client = FakeFirestoreClient()
    spec, cut_sheet = _source_pdfs(tmp_path)
    result = _tools(tmp_path, client, spec, cut_sheet).persist_integrity_finding("specification")

    assert result == {"persisted": False, "reason": "document_carries_no_hidden_span"}
    assert client.data == {}
    assert client.batches == []


def test_persist_integrity_finding_refuses_an_unknown_role(tmp_path: Path) -> None:
    """The caller names a bound role or nothing is written."""
    client = FakeFirestoreClient()
    spec, cut_sheet = _source_pdfs(tmp_path)
    tools = _tools(tmp_path, client, spec, cut_sheet)

    assert tools.persist_integrity_finding(str(spec)) == {
        "persisted": False,
        "reason": "unknown_document_role",
    }
    assert tools.persist_integrity_finding("") == {
        "persisted": False,
        "reason": "unknown_document_role",
    }
    assert client.data == {}


def test_persist_integrity_finding_writes_span_evidence_and_the_document_hash(
    tmp_path: Path,
) -> None:
    """The stored record carries the spans, the pages, and the screened bytes."""
    client = FakeFirestoreClient()
    spec, _ = _source_pdfs(tmp_path)
    altered = write_pdf(
        tmp_path / "altered.pdf",
        [["Submitted characteristic beta."], ["Second submitted page."]],
        hidden={2: ["Submitted characteristic gamma."]},
    )
    tools = _tools(tmp_path, client, spec, altered)

    result = tools.persist_integrity_finding("submitted_document")

    assert result["persisted"] is True
    stored = client.data[INTEGRITY_FINDINGS_COLLECTION][
        result["integrity_finding"]["integrity_finding_id"]
    ]
    assert stored["document_sha256"] == check_text_layer(altered).sha256
    assert stored["flagged_pages"] == [2]
    assert stored["screen_id"] == integrity.SCREEN_ID
    assert stored["created_at"] == FIXED_TIME
    assert [span["text"] for span in stored["hidden_spans"]] == ["Submitted characteristic gamma."]
    assert client.data[DOCUMENTS_COLLECTION][stored["document_sha256"]]["page_count"] == 2


def test_persist_integrity_finding_reads_the_file_rather_than_the_caller(
    tmp_path: Path,
) -> None:
    """The record's evidence comes from the screen, not from any argument.

    ``persist_integrity_finding`` accepts one role name and nothing else, so
    there is no argument through which a model could author span text, a page
    number, or a hash.
    """
    parameters = list(inspect.signature(AuditTools.persist_integrity_finding).parameters)

    assert parameters == ["self", "document_role"]
