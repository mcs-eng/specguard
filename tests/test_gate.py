"""Known-good and known-bad cases for the verification gate.

The gate drives nothing until every case here passes. Each known-good case
names the one normalization step it exercises. Each known-bad case names the
rejection reason it demands.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from specguard.gate import build_document_record, extract_page_text, normalize, verify_quote
from specguard.models import RejectionReason
from tests.fixtures_pdf import (
    FI_LIGATURE,
    NO_BREAK_SPACE,
    SOFT_HYPHEN,
    write_image_only_pdf,
    write_pdf,
    write_pdf_with_hidden_text,
)

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


@pytest.mark.parametrize(
    ("cited_page", "quote"),
    [
        (1, "SECTION 26 27 26 - WIRING DEVICES"),
        (2, "Panelboard MDP-2 shall be rated 208 volts"),
        (3, "Grounding conductors shall be sized in accordance with"),
        (4, "The Contractor shall submit certified test reports"),
    ],
)
def test_every_page_has_a_verifying_quote(spec_pdf: Path, cited_page: int, quote: str) -> None:
    """Each page must carry at least one positive case.

    Codex found that without a positive on pages 3 and 4, the mutation
    ``document[min(page_number - 1, 1)]`` — read page 2 for every later
    citation — passed the entire suite.
    """
    assert verify_quote(quote, cited_page, spec_pdf).verified is True


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
    result = verify_quote("Panelboard MDP2", 2, spec_pdf)
    assert result.verified is False
    assert result.rejection_reason is RejectionReason.QUOTE_NOT_FOUND_ON_CITED_PAGE
    assert verify_quote("Panelboard MDP-2", 2, spec_pdf).verified is True


def test_match_may_not_start_inside_a_number(spec_pdf: Path) -> None:
    """The page says 15 kV. A claim quoting 5 kV must not ride on that 5."""
    assert verify_quote("15 kV with shielded conductors", 2, spec_pdf).verified is True
    result = verify_quote("5 kV with shielded conductors", 2, spec_pdf)
    assert result.verified is False
    assert result.rejection_reason is RejectionReason.QUOTE_NOT_FOUND_ON_CITED_PAGE


def test_match_may_not_start_inside_a_word(spec_pdf: Path) -> None:
    """The page says Panelboard. A claim quoting board must not ride on it."""
    assert verify_quote("board MDP-2 shall be rated", 2, spec_pdf).verified is False


def test_match_may_not_end_inside_a_word(spec_pdf: Path) -> None:
    """The page says Receptacles. A claim quoting Receptacle must not match."""
    assert verify_quote("Receptacle", 1, spec_pdf).verified is False
    assert verify_quote("Receptacles", 1, spec_pdf).verified is True


def test_boundary_rule_allows_punctuation_edges(spec_pdf: Path) -> None:
    """A quote whose edge is punctuation needs no alphanumeric boundary."""
    assert verify_quote(", rated 20 amperes", 1, spec_pdf).verified is True


@pytest.mark.parametrize(
    ("page", "quote"),
    [
        ("breaker rated 0.5 A minimum", "5 A minimum"),
        ("pressure of -5 kPa at inlet", "5 kPa at inlet"),
        ("tolerance of ±5% allowed", "5% allowed"),
        ("feeder 12,500 kcmil", "500 kcmil"),
        ("model AHU-1 unit", "1 unit"),
    ],
)
def test_match_may_not_start_against_a_number_binding_character(
    page: str, quote: str, tmp_path: Path
) -> None:
    """A digit edge may not sit against a decimal point, a sign, or a separator.

    Codex found this: the earlier rule required only an alphanumeric boundary,
    so ``0.5 A`` satisfied a claim quoting ``5 A`` because ``.`` is not
    alphanumeric.
    """
    pdf = write_pdf(tmp_path / "numbers.pdf", [[page]])
    result = verify_quote(quote, 1, pdf)
    assert result.verified is False, quote
    assert result.rejection_reason is RejectionReason.QUOTE_NOT_FOUND_ON_CITED_PAGE


def test_whole_numbers_still_verify(tmp_path: Path) -> None:
    """The numeric boundary rule must not reject an honest whole-number quote."""
    pdf = write_pdf(tmp_path / "numbers.pdf", [["breaker rated 0.5 A minimum"]])
    assert verify_quote("0.5 A minimum", 1, pdf).verified is True


def test_soft_hyphen_never_joins_digits(tmp_path: Path) -> None:
    """Codex case: NEMA 1 broken by a soft hyphen must not become NEMA 12.

    Type 1 and Type 12 enclosures are materially different equipment.
    """
    pdf = write_pdf(tmp_path / "shy.pdf", [["enclosure NEMA 1" + SOFT_HYPHEN, "2 required here"]])
    assert verify_quote("NEMA 12 required", 1, pdf).verified is False
    assert verify_quote("NEMA 1 2 required here", 1, pdf).verified is True


def test_soft_hyphen_never_erases_a_space_next_to_a_digit(tmp_path: Path) -> None:
    """A quote may not use an invisible character to delete its own space."""
    pdf = write_pdf(tmp_path / "shy.pdf", [["unit AHU1 serves the lab"]])
    assert verify_quote("AHU" + SOFT_HYPHEN + " 1 serves", 1, pdf).verified is False


def test_boundary_rule_checks_every_occurrence() -> None:
    """A bad first occurrence must not hide a good later one."""
    from specguard.gate import contains_on_boundaries

    assert contains_on_boundaries("x208 and 208 volts", "208") is True
    assert contains_on_boundaries("x208 and y208", "208") is False


def test_extract_page_text_reads_the_cited_page(spec_pdf: Path) -> None:
    assert "Receptacles" in extract_page_text(spec_pdf, 1)
    assert "Receptacles" not in extract_page_text(spec_pdf, 2)


@pytest.mark.parametrize("cited_page", [0, 5, -1])
def test_extract_page_text_raises_for_a_bad_page(spec_pdf: Path, cited_page: int) -> None:
    with pytest.raises(IndexError):
        extract_page_text(spec_pdf, cited_page)


def test_fixture_font_round_trips_the_special_characters(spec_pdf: Path) -> None:
    """Guard the fixture premise, not only the final match.

    If PyMuPDF ever stops carrying U+00AD or U+FB01 through extraction, the
    soft-hyphen and ligature cases would silently stop testing anything. This
    asserts the raw extracted characters.
    """
    raw = extract_page_text(spec_pdf, 2)
    assert SOFT_HYPHEN in raw
    assert FI_LIGATURE in raw


# --- normalization and provenance -------------------------------------------


def test_normalize_is_idempotent() -> None:
    raw = "  The ﬁxture is trans­\n formed   NOW\t"
    once = normalize(raw)
    assert once == normalize(once)
    assert once == "the fixture is transformed now"


def test_normalize_keeps_word_boundaries() -> None:
    assert normalize("one\ntwo") == "one two"


def test_normalize_flattens_superscripts_known_limitation() -> None:
    """NFKC folds a superscript into a plain digit. This widens the match.

    A page reading "10 squared amperes" normalizes to "102 amperes", so a claim
    that quotes "102 amperes" verifies against it. The contract mandates NFKC,
    so this behavior is pinned here rather than worked around. README records it
    as a limitation.
    """
    assert normalize("rated 10² amperes") == "rated 102 amperes"
    assert normalize("35 mm² copper") == "35 mm2 copper"


def test_normalize_erases_case_sensitive_units_known_limitation() -> None:
    """Casefolding makes mW and MW identical. That is a millionfold difference.

    The contract requires case-insensitive matching, so this is a known cost of
    the contract rather than a defect. README records it.
    """
    assert normalize("rated 15 mW") == normalize("rated 15 MW")


def test_normalize_splices_across_layout_known_limitation() -> None:
    """Whitespace collapse discards layout, so separated text becomes adjacent."""
    assert normalize("left column\n\n\nright column") == "left column right column"


def test_invisible_text_layer_verifies_known_limitation(tmp_path: Path) -> None:
    """The gate reads the text layer, not the visible page.

    The page shows 208 volts. An invisible layer, the same render mode an OCR
    layer uses, says 209 volts. The gate verifies the 209 quote against text no
    reader can see. README records this as a limitation. It is the strongest
    reason the gate must not be described as proving a claim true.
    """
    pdf = write_pdf_with_hidden_text(
        tmp_path / "hidden.pdf",
        visible="Panelboard rated 208 volts",
        hidden="Panelboard rated 209 volts",
    )
    assert verify_quote("Panelboard rated 208 volts", 1, pdf).verified is True
    assert verify_quote("Panelboard rated 209 volts", 1, pdf).verified is True


def test_image_only_page_verifies_nothing(tmp_path: Path) -> None:
    """A page with no text layer carries no evidence, so every quote rejects."""
    pdf = write_image_only_pdf(tmp_path / "scan.pdf")
    assert extract_page_text(pdf, 1).strip() == ""
    result = verify_quote("Panelboard rated 208 volts", 1, pdf)
    assert result.verified is False
    assert result.rejection_reason is RejectionReason.QUOTE_NOT_FOUND_ON_CITED_PAGE


def test_normalize_uses_casefold_not_lower() -> None:
    """Pin casefold specifically. Codex found lower() would pass the old suite.

    ``lower()`` leaves the German sharp s alone; ``casefold()`` maps it to ss.
    """
    assert normalize("STRASSE") == normalize("Straße")
    assert "ß".lower() != "ß".casefold()


def test_normalize_does_not_fold_unlike_dashes() -> None:
    """NFKC leaves a non-breaking hyphen distinct from a plain hyphen."""
    assert normalize("MDP‑2") != normalize("MDP-2")


def test_document_record_digests_the_actual_bytes(spec_pdf: Path) -> None:
    """A constant digest would pass a length-and-stability check. This will not."""
    record = build_document_record(spec_pdf)
    assert record.page_count == 4
    assert record.sha256 == hashlib.sha256(spec_pdf.read_bytes()).hexdigest()
    assert record.sha256 == build_document_record(spec_pdf).sha256


def test_document_record_digest_differs_for_different_bytes(tmp_path: Path) -> None:
    one = write_pdf(tmp_path / "one.pdf", [["Alpha content."]])
    two = write_pdf(tmp_path / "two.pdf", [["Beta content."]])
    assert build_document_record(one).sha256 != build_document_record(two).sha256


def test_result_reports_the_document_it_read(spec_pdf: Path) -> None:
    result = verify_quote(EXACT_QUOTE, 1, spec_pdf)
    assert result.pdf_path == str(spec_pdf)
    assert result.page_number == 1
    assert result.page_count == 4
