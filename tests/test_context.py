"""Tests for the bounded page window shown beside a verified quote.

The window is a read-side view. These tests pin two things: it reports the
occurrence the gate accepts and no other, and it never widens a miss into a
near match.
"""

from __future__ import annotations

from pathlib import Path

from specguard import context, gate
from tests.fixtures_pdf import write_pdf

FIXTURE_DIRECTORY = Path(__file__).resolve().parents[1] / "fixtures"
SPECIFICATION = FIXTURE_DIRECTORY / "asterquay_learning_workshop_specification.pdf"
SPEC_QUOTE = "Conductor terminations shall be rated 90 deg C minimum."
SPEC_PAGE = 5


def test_window_reports_the_quote_the_gate_verified() -> None:
    page_text = gate.extract_page_text(SPECIFICATION, SPEC_PAGE)

    window = context.quote_window(page_text, SPEC_QUOTE)

    assert window.found is True
    assert window.match == gate.normalize(SPEC_QUOTE)
    assert gate.verify_quote(SPEC_QUOTE, SPEC_PAGE, SPECIFICATION).verified is True


def test_window_text_reassembles_into_the_normalized_page() -> None:
    """The three parts are a contiguous slice of the normalized page text."""
    page_text = gate.extract_page_text(SPECIFICATION, SPEC_PAGE)

    window = context.quote_window(page_text, SPEC_QUOTE)

    assert window.before + window.match + window.after in gate.normalize(page_text)


def test_window_is_bounded_by_the_radius_on_each_side() -> None:
    page_text = gate.extract_page_text(SPECIFICATION, SPEC_PAGE)

    window = context.quote_window(page_text, SPEC_QUOTE, radius=20)

    assert len(window.before) == 20
    assert len(window.after) == 20
    assert window.truncated_before is True
    assert window.truncated_after is True


def test_a_quote_at_the_page_start_is_not_reported_as_truncated() -> None:
    window = context.quote_window("Alpha beta gamma.", "Alpha beta gamma.")

    assert window.found is True
    assert window.before == ""
    assert window.after == ""
    assert window.truncated_before is False
    assert window.truncated_after is False


def test_a_quote_that_is_not_on_the_page_yields_no_window() -> None:
    page_text = gate.extract_page_text(SPECIFICATION, SPEC_PAGE)
    changed_digit = "Conductor terminations shall be rated 80 deg C minimum."

    window = context.quote_window(page_text, changed_digit)

    assert window.found is False
    assert window.match == ""
    assert gate.verify_quote(changed_digit, SPEC_PAGE, SPECIFICATION).verified is False


def test_a_quote_from_another_page_yields_no_window() -> None:
    """The view reads one page, exactly as the gate reads one page."""
    other_page_text = gate.extract_page_text(SPECIFICATION, SPEC_PAGE - 1)

    window = context.quote_window(other_page_text, SPEC_QUOTE)

    assert window.found is False
    assert gate.verify_quote(SPEC_QUOTE, SPEC_PAGE - 1, SPECIFICATION).verified is False


def test_an_empty_quote_yields_no_window() -> None:
    assert context.quote_window("Alpha beta gamma.", "").found is False
    assert context.quote_window("Alpha beta gamma.", "   ").found is False


def test_the_window_uses_the_gate_token_boundary_rule() -> None:
    """A digit riding on a longer number is not a match here either."""
    page = "The rating is 0.5 A on this line."

    assert context.quote_window(page, "5 A").found is False
    assert gate.contains_on_boundaries(gate.normalize(page), gate.normalize("5 A")) is False


def test_the_window_skips_an_occurrence_that_cuts_a_token() -> None:
    """A boundary-cutting hit does not hide a later, legitimate occurrence."""
    page = "Circuit 1200 amperes, then 200 amperes."

    window = context.quote_window(page, "200 amperes")

    assert window.found is True
    assert window.before.endswith("then ")
    assert "1200" in window.before


def test_the_window_shows_the_normalized_text_the_gate_compared(tmp_path: Path) -> None:
    """A soft hyphen resolved by the gate is resolved in the window as well."""
    pdf_path = write_pdf(
        tmp_path / "hyphen.pdf",
        [["Terminations shall be rated for the trans­", "former room ambient."]],
    )
    page_text = gate.extract_page_text(pdf_path, 1)

    window = context.quote_window(page_text, "transformer room ambient.")

    assert window.found is True
    assert "­" not in window.before + window.match + window.after
    assert window.match == "transformer room ambient."


def test_find_boundary_occurrence_returns_the_index_the_gate_accepts() -> None:
    page = gate.normalize("Circuit 1200 amperes, then 200 amperes.")
    needle = gate.normalize("200 amperes")

    index = context.find_boundary_occurrence(page, needle)

    assert index is not None
    assert page[index : index + len(needle)] == needle
    assert page[index - 1] == " "


def test_find_boundary_occurrence_returns_none_for_a_miss() -> None:
    assert context.find_boundary_occurrence(gate.normalize("0.5 A"), gate.normalize("5 A")) is None
    assert context.find_boundary_occurrence("alpha", "") is None
