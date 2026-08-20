"""Acceptance tests for the committed fictional SpecGuard demo fixtures."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import pymupdf
import pytest

from fixtures.build_fixtures import build_fixtures
from specguard.gate import verify_quote
from specguard.models import RejectionReason

FIXTURE_DIRECTORY = Path(__file__).parents[1] / "fixtures"
MANIFEST_PATH = FIXTURE_DIRECTORY / "MANIFEST.md"
EVIDENCE_PATTERN = re.compile(r"<!-- fixture-evidence\s*(\[.*?\])\s*-->", re.DOTALL)
PDF_NAMES = (
    "asterquay_learning_workshop_specification.pdf",
    "caldra_meridian_480v_switchboard.pdf",
    "veylan_arcworks_208v_switchboard.pdf",
    "torven_70c_termination_switchboard.pdf",
)
EXPECTED_PAGE_COUNTS = {
    "asterquay_learning_workshop_specification.pdf": 7,
    "caldra_meridian_480v_switchboard.pdf": 2,
    "veylan_arcworks_208v_switchboard.pdf": 2,
    "torven_70c_termination_switchboard.pdf": 2,
}


def _manifest_text() -> str:
    return MANIFEST_PATH.read_text(encoding="utf-8")


def _evidence_pairs() -> list[dict[str, object]]:
    match = EVIDENCE_PATTERN.search(_manifest_text())
    assert match is not None, "MANIFEST.md must contain the fixture-evidence block"
    evidence = json.loads(match.group(1))
    assert isinstance(evidence, list)
    return evidence


def _extracted_text_by_page(pdf_path: Path) -> list[str]:
    with pymupdf.open(pdf_path) as document:
        return [page.get_text() for page in document]


@pytest.mark.parametrize("pair", _evidence_pairs(), ids=lambda pair: str(pair["id"]))
def test_every_manifest_evidence_pair_verifies(pair: dict[str, object]) -> None:
    """Every spec and cut-sheet quote in the manifest verifies at its cited page."""
    spec_result = verify_quote(
        str(pair["spec_quote"]), int(pair["spec_page"]), FIXTURE_DIRECTORY / str(pair["spec_pdf"])
    )
    cut_sheet_result = verify_quote(
        str(pair["cut_sheet_quote"]),
        int(pair["cut_sheet_page"]),
        FIXTURE_DIRECTORY / str(pair["cut_sheet_pdf"]),
    )

    assert spec_result.verified is True, pair["id"]
    assert spec_result.rejection_reason is None
    assert cut_sheet_result.verified is True, pair["id"]
    assert cut_sheet_result.rejection_reason is None


def test_right_quote_on_the_wrong_page_rejects() -> None:
    """A real Section A quote must not verify on the next specification page."""
    pair = _evidence_pairs()[0]
    correct_page = int(pair["spec_page"])
    pdf_path = FIXTURE_DIRECTORY / str(pair["spec_pdf"])

    assert verify_quote(str(pair["spec_quote"]), correct_page, pdf_path).verified is True
    result = verify_quote(str(pair["spec_quote"]), correct_page + 1, pdf_path)

    assert result.verified is False
    assert result.rejection_reason is RejectionReason.QUOTE_NOT_FOUND_ON_CITED_PAGE


def test_manifest_sha256_values_match_the_committed_pdfs() -> None:
    """The manifest hashes name exactly the PDF byte streams committed with it."""
    manifest = _manifest_text()
    for pdf_name in PDF_NAMES:
        digest = hashlib.sha256((FIXTURE_DIRECTORY / pdf_name).read_bytes()).hexdigest().upper()
        assert f"`{digest}`" in manifest, pdf_name


def test_all_fixture_pdfs_are_text_based() -> None:
    """The demo fixtures must carry extractable text rather than images of text."""
    for pdf_name in PDF_NAMES:
        pages = _extracted_text_by_page(FIXTURE_DIRECTORY / pdf_name)
        assert pages, pdf_name
        assert all(page.strip() for page in pages), pdf_name


def test_fixture_page_counts_and_specification_sections() -> None:
    """The specification has three sections and every cut sheet has two pages."""
    for pdf_name, expected_pages in EXPECTED_PAGE_COUNTS.items():
        assert len(_extracted_text_by_page(FIXTURE_DIRECTORY / pdf_name)) == expected_pages

    specification = "\n".join(
        _extracted_text_by_page(FIXTURE_DIRECTORY / "asterquay_learning_workshop_specification.pdf")
    )
    assert "SECTION 26 24 13" in specification
    assert "SECTION 26 05 19" in specification
    assert "SECTION 01 33 00" in specification


def test_compliant_cut_sheet_matches_both_required_values() -> None:
    """The control cut sheet provides the specified voltage and termination rating."""
    compliant_pdf = FIXTURE_DIRECTORY / "caldra_meridian_480v_switchboard.pdf"
    assert verify_quote("Nominal system: 480V, 3-phase, 4-wire.", 1, compliant_pdf).verified is True
    assert (
        verify_quote("Field conductor terminations: 90 deg C minimum.", 2, compliant_pdf).verified
        is True
    )


def test_fixture_rebuild_has_identical_extracted_text(tmp_path: Path) -> None:
    """The generator must reproduce every committed fixture's page text."""
    rebuilt_paths = build_fixtures(tmp_path)
    assert tuple(path.name for path in rebuilt_paths) == PDF_NAMES

    for rebuilt_path in rebuilt_paths:
        committed_path = FIXTURE_DIRECTORY / rebuilt_path.name
        assert _extracted_text_by_page(rebuilt_path) == _extracted_text_by_page(committed_path)
