"""Acceptance tests for the Phase 7c messy fictional fixture set."""

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
MESSY_SPECIFICATION = "nimbrin_thermal_annex_specification.pdf"
MESSY_PACKAGE = "zarqelune_vantrel_package.pdf"
MESSY_PDF_NAMES = (MESSY_SPECIFICATION, MESSY_PACKAGE)
EVIDENCE_PATTERN = re.compile(
    r"<!-- fixture-evidence-messy\s*(\[.*?\])\s*-->", re.DOTALL
)
EVAL_CASES_PATTERN = re.compile(r"<!-- eval-cases-messy\s*(\[.*?\])\s*-->", re.DOTALL)


def _manifest_text() -> str:
    return MANIFEST_PATH.read_text(encoding="utf-8")


def _json_block(pattern: re.Pattern[str]) -> list[dict[str, object]]:
    match = pattern.search(_manifest_text())
    assert match is not None
    value = json.loads(match.group(1))
    assert isinstance(value, list)
    return value


def _evidence_pairs() -> list[dict[str, object]]:
    return _json_block(EVIDENCE_PATTERN)


def _eval_cases() -> list[dict[str, object]]:
    return _json_block(EVAL_CASES_PATTERN)


def _extracted_text_by_page(pdf_path: Path) -> list[str]:
    with pymupdf.open(pdf_path) as document:
        return [page.get_text() for page in document]


@pytest.mark.parametrize("pair", _evidence_pairs(), ids=lambda pair: str(pair["id"]))
def test_every_messy_manifest_evidence_pair_verifies(pair: dict[str, object]) -> None:
    """Every planted quote pair verifies through the unchanged page-local gate."""
    spec_result = verify_quote(
        str(pair["spec_quote"]),
        int(pair["spec_page"]),
        FIXTURE_DIRECTORY / str(pair["spec_pdf"]),
    )
    cut_sheet_result = verify_quote(
        str(pair["cut_sheet_quote"]),
        int(pair["cut_sheet_page"]),
        FIXTURE_DIRECTORY / str(pair["cut_sheet_pdf"]),
    )

    assert spec_result.verified is True, pair["id"]
    assert cut_sheet_result.verified is True, pair["id"]
    assert spec_result.rejection_reason is None
    assert cut_sheet_result.rejection_reason is None


def test_messy_manifest_has_seven_findings_and_two_decoys() -> None:
    pairs = _evidence_pairs()
    cases = _eval_cases()

    assert len(pairs) == 7
    assert [pair["id"] for pair in pairs] == [f"M-{index:02d}" for index in range(1, 8)]
    assert len(cases) == 9
    assert sum(case["expected_outcome"] == "finding" for case in cases) == 7
    assert [case["id"] for case in cases[-2:]] == ["E-18", "E-19"]
    assert all(case["expected_outcome"] == "no_finding" for case in cases[-2:])
    assert all(case["evidence_id"] is None for case in cases[-2:])


def test_messy_right_quote_on_wrong_page_rejects() -> None:
    pair = _evidence_pairs()[0]
    pdf_path = FIXTURE_DIRECTORY / str(pair["spec_pdf"])
    correct_page = int(pair["spec_page"])

    assert verify_quote(str(pair["spec_quote"]), correct_page, pdf_path).verified is True
    result = verify_quote(str(pair["spec_quote"]), correct_page + 1, pdf_path)

    assert result.verified is False
    assert result.rejection_reason is RejectionReason.QUOTE_NOT_FOUND_ON_CITED_PAGE


def test_messy_document_shape_has_noise_tables_columns_and_running_pages() -> None:
    spec_pages = _extracted_text_by_page(FIXTURE_DIRECTORY / MESSY_SPECIFICATION)
    package_pages = _extracted_text_by_page(FIXTURE_DIRECTORY / MESSY_PACKAGE)

    assert len(spec_pages) == 32
    assert len(package_pages) == 10
    assert "TABLE OF CONTENTS" in spec_pages[0]
    assert "TWO-COLUMN SPECIFICATION LAYOUT" in spec_pages[12]
    assert "See Section 26 24 16, Paragraph 2.2" in "\n".join(spec_pages)
    assert "boilerplate" in "\n".join(spec_pages).casefold()
    assert "Page 13 of 32" in spec_pages[12]
    assert "Page 10 of 10" in package_pages[9]
    assert "CERTIFICATIONS AND DECLARATIONS" in package_pages[9]
    assert "DIMENSIONS AND SERVICE CLEARANCES" in package_pages[7]
    assert "42 kA symmetrical at 480 V" in package_pages[5]


def test_messy_manifest_sha256_values_match_the_committed_pdfs() -> None:
    manifest = _manifest_text()
    for pdf_name in MESSY_PDF_NAMES:
        digest = hashlib.sha256((FIXTURE_DIRECTORY / pdf_name).read_bytes()).hexdigest().upper()
        assert f"`{digest}`" in manifest, pdf_name


def test_messy_rebuild_is_byte_identical(tmp_path: Path) -> None:
    rebuilt = {path.name: path for path in build_fixtures(tmp_path)}

    for pdf_name in MESSY_PDF_NAMES:
        assert rebuilt[pdf_name].read_bytes() == (FIXTURE_DIRECTORY / pdf_name).read_bytes()


def test_collision_receipt_names_only_the_final_fictional_manufacturer() -> None:
    manifest = _manifest_text()
    assert '"Zarqelune Industrial Assemblies"' in manifest
    assert "No exact-name equipment-brand result was returned" in manifest
