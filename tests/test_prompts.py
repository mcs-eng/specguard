"""Scan every model-facing instruction against a fixture-name denylist.

A prompt that names a fixture, a fictional product, or a planted value stops
measuring the runtime and starts measuring the prompt. The denylist below is
derived from ``fixtures/MANIFEST.md`` and the committed fixture directory
rather than typed by hand, so a fixture added later brings its own names with
it. The derivation is itself tested here against known-good and known-bad
inputs, because a scanner that matches nothing passes every file.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from specguard.agent import PROMPT_PATHS, registered_tools
from specguard.models import AgentMode
from specguard.tools import AuditTools
from tests.fake_firestore import FakeFirestoreClient
from tests.fixtures_pdf import write_pdf

FIXTURES_DIRECTORY = Path(__file__).resolve().parent.parent / "fixtures"
MANIFEST_PATH = FIXTURES_DIRECTORY / "MANIFEST.md"
PROMPTS_DIRECTORY = Path(__file__).resolve().parent.parent / "specguard" / "prompts"

#: Ordinary English words that appear inside a fixture file name or a fictional
#: entity name, and that a defensive audit instruction may legitimately use.
#: Each one names a category, not a fixture: "specification" is the document
#: under audit in every project, and "package" is what a submittal arrives as.
#: The fictional words beside them — Nimbrin, Zarqelune, Vantrel, Asterquay,
#: Veylan, Arcworks, Torven, Caldra, Meridian, Lumaquay, Orvessa — are not
#: here, and no prompt may use them.
ORDINARY_WORDS = frozenset(
    {
        "altered",
        "annex",
        "assemblies",
        "civic",
        "energy",
        "industrial",
        "learning",
        "office",
        "package",
        "specification",
        "switchboard",
        "termination",
        "thermal",
        "workshop",
    }
)

#: Words that follow a bare number inside a planted value and make the pair a
#: measurement rather than an ordinary phrase.
_UNIT_WORDS = frozenset({"a", "v", "ka", "mm", "deg", "vdc", "positions"})

_COLLISION_TERM = re.compile(r"^\| `\"([^\"]+)\"` \|", re.MULTILINE)
_MACHINE_BLOCK = re.compile(r"<!--\s*[a-z-]+\s*(\[.*?\])\s*-->", re.DOTALL)
_PROSE_QUOTE = re.compile(r"^- \*\*(?:Spec|Cut-sheet) quote:\*\* `([^`]+)`", re.MULTILINE)
_TOKEN_EDGE = re.compile(r"^[^0-9a-z]+|[^0-9a-z]+$")


def _collapse(text: str) -> str:
    """Casefold text and collapse every run of whitespace to one space."""
    return " ".join(text.casefold().split())


def _tokens(text: str) -> list[str]:
    """Split text into casefolded tokens, keeping internal hyphens."""
    return [
        stripped
        for stripped in (_TOKEN_EDGE.sub("", token) for token in _collapse(text).split())
        if stripped
    ]


def _is_identifier(token: str) -> bool:
    """True for a token that mixes digits and letters, such as a model number.

    A designator like a product suffix or a listing mark is never an ordinary
    prompt word, so it is denied at any length.
    """
    return any(character.isdigit() for character in token) and any(
        character.isalpha() for character in token
    )


def _distinctive(token: str) -> bool:
    """True for a token that names a fixture rather than a category."""
    if token in ORDINARY_WORDS:
        return False
    return _is_identifier(token) or len(token) >= 5


def _name_terms(manifest_text: str) -> set[str]:
    """Derive the fictional names from the file names and the collision table."""
    terms: set[str] = set()
    for pdf_path in sorted(FIXTURES_DIRECTORY.glob("*.pdf")):
        terms.add(pdf_path.name.casefold())
        terms.add(pdf_path.stem.casefold())
        terms.update(token for token in pdf_path.stem.casefold().split("_") if _distinctive(token))
    for phrase in _COLLISION_TERM.findall(manifest_text):
        terms.add(_collapse(phrase))
        terms.update(token for token in _tokens(phrase) if _distinctive(token))
    return terms


def _planted_quotes(manifest_text: str) -> set[str]:
    """Collect every planted quote the manifest states, in either form.

    The machine-readable blocks carry the pairs a run is scored against. The
    prose entries carry those and the compliant near-match decoys as well, and
    a decoy value is as much a planted value as a mismatch is.
    """
    quotes: set[str] = set(_PROSE_QUOTE.findall(manifest_text))
    for block in _MACHINE_BLOCK.findall(manifest_text):
        for entry in json.loads(block):
            if not isinstance(entry, dict):
                continue
            quotes.update(
                value
                for key in ("spec_quote", "cut_sheet_quote")
                if isinstance(value := entry.get(key), str)
            )
    return quotes


def _planted_value_terms(manifest_text: str) -> set[str]:
    """Derive the denied terms from every planted quote.

    Three shapes are kept. A token that mixes digits and letters is an
    identifier or a rating, never an ordinary prompt word. A bare number
    followed by a unit is a measurement, and only that pair is kept, so a
    prompt numbering its own steps does not trip on the digit alone. The whole
    quote is kept as well, which is what catches a purely verbal value.

    What this deliberately does not do is deny the ordinary words inside a
    quote. Denying "provide", "rating", or "minimum" would make an honest audit
    instruction unwritable, so a prompt that paraphrased a verbal planted value
    without reproducing it would pass this scan. The names, the designators,
    and every numeric value are covered.
    """
    terms: set[str] = set()
    for quote in _planted_quotes(manifest_text):
        terms.add(_collapse(quote))
        terms.update(_quote_terms(_tokens(quote)))
    return terms


def _quote_terms(tokens: list[str]) -> set[str]:
    """Return the identifiers and measurements one planted quote contains."""
    terms: set[str] = set()
    for position, token in enumerate(tokens):
        if _is_identifier(token):
            terms.add(token)
        if not token.isdigit():
            continue
        unit = tokens[position + 1] if position + 1 < len(tokens) else None
        if unit not in _UNIT_WORDS or unit is None:
            continue
        terms.add(f"{token} {unit}")
        # A degree value names its scale in the next token, and the scale is
        # what makes the value a requirement rather than a bare number.
        if unit == "deg" and position + 2 < len(tokens):
            terms.add(f"{token} deg {tokens[position + 2]}")
    return terms


def fixture_denylist() -> set[str]:
    """Return every term no model-facing instruction may contain."""
    manifest_text = MANIFEST_PATH.read_text(encoding="utf-8")
    return _name_terms(manifest_text) | _planted_value_terms(manifest_text)


def denied_terms_in(text: str, denylist: set[str]) -> list[str]:
    """Return every denied term the text contains, on token boundaries."""
    collapsed = _collapse(text)
    return sorted(
        term
        for term in denylist
        if re.search(rf"(?<![0-9a-z]){re.escape(term)}(?![0-9a-z])", collapsed)
    )


# --- 1. The derivation, against known-good and known-bad input -----------


def test_the_denylist_names_every_fictional_entity_and_planted_value() -> None:
    denylist = fixture_denylist()

    assert {"nimbrin", "zarqelune", "vantrel", "asterquay", "veylan", "torven"} <= denylist
    assert {"arcworks", "caldra", "meridian", "orvessa"} <= denylist
    assert {"l-36", "l-18", "ql-4", "qf-2", "qp-7", "e-4", "e-2"} <= denylist
    assert {"50 ka", "42 ka", "1000 mm", "760 mm", "24 vdc"} <= denylist
    assert {"80 deg c", "140 deg f", "90 deg c", "158 deg f"} <= denylist
    assert {"208v", "480v"} <= denylist
    assert "nimbrin_thermal_annex_specification.pdf" in denylist
    assert "zarqelune vantrel l-36" in denylist
    assert "the cabinet finish shall be graphite gray." in denylist


def test_the_denylist_covers_the_compliant_decoy_values_as_well() -> None:
    """A decoy value is a planted value, and the prose states both of them."""
    denylist = fixture_denylist()

    assert "24 vdc" in denylist
    assert "graphite-grey baked coating" in denylist
    assert "24-volt direct-current" in denylist


def test_the_scan_records_what_it_does_not_catch() -> None:
    """A verbal value survives paraphrase, and the docstring says so.

    This test exists so the limit is a recorded property rather than an
    assumption. Reproducing the planted quote is caught; naming the same idea
    in other words is not, because denying the ordinary words inside a quote
    would make an honest instruction unwritable.
    """
    denylist = fixture_denylist()

    assert denied_terms_in("The cabinet finish shall be graphite gray.", denylist) == [
        "the cabinet finish shall be graphite gray."
    ]
    assert denied_terms_in("Report a finish that does not match.", denylist) == []


def test_the_denylist_admits_the_ordinary_words_a_prompt_needs() -> None:
    """A scanner that denied these would make an honest prompt unwritable."""
    denylist = fixture_denylist()

    assert denylist.isdisjoint(ORDINARY_WORDS)
    assert (
        denied_terms_in("Audit the submitted document against the specification.", denylist) == []
    )
    assert denied_terms_in("Work in this order. 1. Read the index. 2. Open a page.", denylist) == []


@pytest.mark.parametrize(
    ("leaked", "expected"),
    [
        ("Compare the Zarqelune Vantrel L-36 against the requirement.", "vantrel"),
        ("Read nimbrin_thermal_annex_specification.pdf page 17.", "nimbrin"),
        ("The fault-duty rating must be 50 kA symmetrical.", "50 ka"),
        ("The assembly needs the QL-4 listing.", "ql-4"),
        ("Check the E-4 enclosure classification.", "e-4"),
        ("The system is 208V, 3-phase.", "208v"),
        ("Terminals shall be rated 80 deg C minimum.", "80 deg c"),
    ],
)
def test_the_scan_catches_a_prompt_that_names_a_fixture(leaked: str, expected: str) -> None:
    assert expected in denied_terms_in(leaked, fixture_denylist())


# --- 2. The committed model-facing text ----------------------------------


def test_the_scan_reads_every_committed_prompt_file() -> None:
    """A scan that silently read nothing would pass every file below.

    The scan covers the whole prompt directory, not only the two instruction
    files an agent mode selects. The severity instruction is model-facing text
    as well, and it carries the same prohibition.
    """
    scanned = sorted(path.name for path in PROMPTS_DIRECTORY.glob("*.txt"))

    assert set(scanned) >= {path.name for path in PROMPT_PATHS.values()}
    assert scanned == [
        "audit_claims_navigate_v1.txt",
        "audit_claims_v1.txt",
        "classify_severity_v1.txt",
    ]
    assert all(PROMPTS_DIRECTORY.joinpath(name).read_text(encoding="utf-8") for name in scanned)


def test_no_committed_prompt_names_a_fixture_or_a_planted_value() -> None:
    denylist = fixture_denylist()

    leaks = {
        path.name: denied_terms_in(path.read_text(encoding="utf-8"), denylist)
        for path in sorted(PROMPTS_DIRECTORY.glob("*.txt"))
    }

    assert leaks == {name: [] for name in leaks}


def test_no_registered_tool_description_names_a_fixture(tmp_path: Path) -> None:
    """A tool docstring is model-facing text and carries the same prohibition."""
    spec = write_pdf(tmp_path / "spec.pdf", [["Requirement alpha."]])
    cut_sheet = write_pdf(tmp_path / "cut.pdf", [["Characteristic beta."]])
    tools = AuditTools(
        firestore_client=FakeFirestoreClient(),
        spec_path=spec,
        cut_sheet_path=cut_sheet,
        run_id="run-1234abcd",
        output_directory=tmp_path / "artifacts",
    )
    denylist = fixture_denylist()

    leaks = {
        f"{mode.value}:{tool.__name__}": denied_terms_in(tool.__doc__ or "", denylist)
        for mode in AgentMode
        for tool in registered_tools(tools, mode)
    }

    assert leaks == {name: [] for name in leaks}
    assert len(leaks) == 7
