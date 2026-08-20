"""Known-good and known-bad cases for the verification gate.

The gate drives nothing until every case here passes. Each known-good case
names the one normalization step it exercises. Each known-bad case names the
rejection reason it demands.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from specguard.gate import build_document_record, normalize, verify_quote
from specguard.models import RejectionReason
from tests.fixtures_pdf import FI_LIGATURE, NO_BREAK_SPACE

# --- known-good: these MUST verify ------------------------------------------

EXACT_QUOTE = "Receptacles shall be specification grade, rated 20 amperes at 125 volts."


def test_exact_quote_verifies(spec_pdf: Path) -> None:
    result = verify_quote(EXACT_QUOTE, 1, spec_pdf)
    assert result.verified is True
    assert result.rejection_reason is None


def test_case_only_difference_verifies(spec_pdf: Path) -> None:
    result = verify_quote(EXACT_QUOTE.upper(), 1, spec_pdf)
    assert result.verified is True


def test_case_only_difference_lowercase_verifies(spec_pdf: Path) -> None:
    result = verify_quote(EXACT_QUOTE.lower(), 1, spec_pdf)
    assert result.verified is True


def test_whitespace_difference_verifies(spec_pdf: Path) -> None:
    """Runs of spaces and tabs in the quote collapse to one space."""
    quote = "Receptacles   shall\tbe  specification grade"
    assert verify_quote(quote, 1, spec_pdf).verified is True


def test_line_break_difference_verifies(spec_pdf: Path) -> None:
    """The quote spans a line break on the cited page."""
    quote = "smooth thermoplastic in a color selected by the Architect."
    assert verify_quote(quote, 1, spec_pdf).verified is True


def test_line_break_inside_the_quote_verifies(spec_pdf: Path) -> None:
    """The quote itself carries a line break the page does not."""
    quote = "specification grade,\nrated 20 amperes"
    assert verify_quote(quote, 1, spec_pdf).verified is True


def test_soft_hyphen_line_break_verifies(spec_pdf: Path) -> None:
    """The page splits the word transformer across a soft-hyphen line break."""
    quote = "Northgate Civic Annex transformer room ambient of 40 degrees Celsius."
    result = verify_quote(quote, 2, spec_pdf)
    assert result.verified is True, result.rejection_reason


def test_nfkc_ligature_verifies(spec_pdf: Path) -> None:
    """The page carries an fi ligature; the quote carries the two plain letters."""
    assert verify_quote("The fixture schedule lists", 2, spec_pdf).verified is True


def test_nfkc_no_break_space_verifies(spec_pdf: Path) -> None:
    """The page carries a no-break space; the quote carries a plain space."""
    assert verify_quote("a 30 ampere branch circuit", 2, spec_pdf).verified is True


def test_nfkc_characters_in_the_quote_verify(spec_pdf: Path) -> None:
    """The quote carries the ligature and the no-break space; the match holds."""
    quote = "The " + FI_LIGATURE + "xture schedule lists a 30" + NO_BREAK_SPACE + "ampere"
    assert verify_quote(quote, 2, spec_pdf).verified is True


def test_quote_spanning_a_line_break_on_page_two_verifies(spec_pdf: Path) -> None:
    assert verify_quote("a 30 ampere branch circuit for the unit heater.", 2, spec_pdf).verified


# --- known-bad: these MUST reject -------------------------------------------


def test_absent_quote_rejects(spec_pdf: Path) -> None:
    result = verify_quote("Conduit shall be galvanized rigid steel throughout.", 1, spec_pdf)
    assert result.verified is False
    assert result.rejection_reason is RejectionReason.QUOTE_NOT_FOUND_ON_CITED_PAGE


def test_quote_on_a_different_page_rejects(spec_pdf: Path) -> None:
    """The quote is real, but it lives on page 1 and the claim cites page 2."""
    quote = "Device plates shall be smooth thermoplastic"
    assert verify_quote(quote, 1, spec_pdf).verified is True
    result = verify_quote(quote, 2, spec_pdf)
    assert result.verified is False
    assert result.rejection_reason is RejectionReason.QUOTE_NOT_FOUND_ON_CITED_PAGE


def test_quote_spanning_two_pages_rejects(spec_pdf: Path) -> None:
    """Half the quote is the tail of page 3 and half is the head of page 4."""
    quote = "sized in accordance with Table 4 of this Section"
    for cited_page in (3, 4):
        result = verify_quote(quote, cited_page, spec_pdf)
        assert result.verified is False, cited_page
        assert result.rejection_reason is RejectionReason.QUOTE_NOT_FOUND_ON_CITED_PAGE


def test_one_digit_changed_rejects(spec_pdf: Path) -> None:
    """The page says 208 volts; the claim quotes 209 volts."""
    assert verify_quote("rated 208 volts, three phase", 2, spec_pdf).verified is True
    result = verify_quote("rated 209 volts, three phase", 2, spec_pdf)
    assert result.verified is False
    assert result.rejection_reason is RejectionReason.QUOTE_NOT_FOUND_ON_CITED_PAGE


def test_unit_changed_rejects(spec_pdf: Path) -> None:
    """The page says 15 kV; the claim quotes 15 V."""
    assert verify_quote("rated 15 kV with shielded conductors", 2, spec_pdf).verified is True
    result = verify_quote("rated 15 V with shielded conductors", 2, spec_pdf)
    assert result.verified is False
    assert result.rejection_reason is RejectionReason.QUOTE_NOT_FOUND_ON_CITED_PAGE


@pytest.mark.parametrize("cited_page", [5, 9, 1000])
def test_page_number_past_the_end_rejects(spec_pdf: Path, cited_page: int) -> None:
    result = verify_quote(EXACT_QUOTE, cited_page, spec_pdf)
    assert result.verified is False
    assert result.rejection_reason is RejectionReason.PAGE_OUT_OF_RANGE
    assert result.page_count == 4


@pytest.mark.parametrize("cited_page", [0, -1])
def test_page_number_below_one_rejects(spec_pdf: Path, cited_page: int) -> None:
    result = verify_quote(EXACT_QUOTE, cited_page, spec_pdf)
    assert result.verified is False
    assert result.rejection_reason is RejectionReason.PAGE_OUT_OF_RANGE


@pytest.mark.parametrize("quote", ["", "   ", "\n\t"])
def test_empty_quote_rejects(spec_pdf: Path, quote: str) -> None:
    """An empty quote is not evidence, even though it is a substring of anything."""
    result = verify_quote(quote, 1, spec_pdf)
    assert result.verified is False
    assert result.rejection_reason is RejectionReason.QUOTE_NOT_FOUND_ON_CITED_PAGE


def test_visible_hyphen_is_not_a_soft_hyphen(spec_pdf: Path) -> None:
    """The contract strips U+00AD only. A printed hyphen stays in the text."""
    assert verify_quote("Panelboard MDP2", 2, spec_pdf).verified is False
    assert verify_quote("Panelboard MDP-2", 2, spec_pdf).verified is True


# --- normalization and provenance -------------------------------------------


def test_normalize_is_idempotent() -> None:
    raw = "  The ﬁxture is trans­\n formed   NOW\t"
    once = normalize(raw)
    assert once == normalize(once)
    assert once == "the fixture is transformed now"


def test_normalize_keeps_word_boundaries() -> None:
    assert normalize("one\ntwo") == "one two"


def test_document_record_is_chain_of_custody_only(spec_pdf: Path) -> None:
    record = build_document_record(spec_pdf)
    assert record.page_count == 4
    assert len(record.sha256) == 64
    assert record.sha256 == build_document_record(spec_pdf).sha256


def test_result_reports_the_document_it_read(spec_pdf: Path) -> None:
    result = verify_quote(EXACT_QUOTE, 1, spec_pdf)
    assert result.pdf_path == str(spec_pdf)
    assert result.page_number == 1
    assert result.page_count == 4
