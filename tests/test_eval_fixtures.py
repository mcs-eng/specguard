"""Tests for the fixture evaluation harness.

Every test here is network-free. The harness's own scoring, rendering, ship
gate, and revision reporting are exercised against constructed results; the
real model path is never called.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from scripts.eval_fixtures import (
    DEFAULT_RECEIPTS_PATH,
    LANE_MESSY,
    LANE_ORIGINAL,
    LANES,
    RATE_LIMIT_BACKOFF_SECONDS,
    README_TABLE_END,
    README_TABLE_START,
    RECEIPT_RUN,
    RECEIPT_TOOL_CALL,
    RECEIPTS_FILE_NAME,
    UNRECORDED_REVISION,
    CasePair,
    CaseResult,
    EvalCase,
    EvidencePair,
    LaneResult,
    PairResult,
    ReceiptLog,
    RunOutcome,
    ShipGateVerdict,
    _parser,
    aggregate,
    build_run_outcome,
    compare_to_previous,
    current_code_revision,
    eval_summary_line,
    evaluate_ship_gate,
    format_code_revision,
    group_cases_into_pairs,
    is_rate_limited,
    load_decoy_pairs,
    load_eval_cases,
    load_evidence_pairs,
    overall_catch_rate,
    read_previous_readme_section,
    receipt_records,
    render_case_table,
    render_eval_markdown,
    render_pair_table,
    render_readme_section,
    run_pair_with_backoff,
    selected_lanes,
    selected_modes,
    uncommitted_source_paths,
    write_readme_section,
)
from specguard.gate import normalize
from specguard.models import (
    AgentMode,
    AuditModelUsage,
    AuditRunSummary,
    DocumentRole,
    ModelToolCall,
    QuarantinedDocument,
    RunQuarantine,
)

MANIFEST_PATH = Path(__file__).parents[1] / "fixtures" / "MANIFEST.md"
MANIFEST_TEXT = MANIFEST_PATH.read_text(encoding="utf-8")

D01 = EvidencePair(
    id="D-01",
    spec_page=3,
    spec_quote="Provide a 480V, 3-phase distribution switchboard for service distribution.",
    cut_sheet_page=1,
    cut_sheet_quote="Nominal system: 208V, 3-phase, 4-wire.",
)
D02 = EvidencePair(
    id="D-02",
    spec_page=5,
    spec_quote="Terminations shall be rated 90 deg C minimum.",
    cut_sheet_page=1,
    cut_sheet_quote="Termination rating: 158 deg F.",
)
DECOY = EvidencePair(
    id="N-01",
    spec_page=17,
    spec_quote="The cabinet finish shall be graphite gray.",
    cut_sheet_page=9,
    cut_sheet_quote="graphite-grey baked coating",
)

FINDING_CASE = EvalCase(
    id="E-02",
    spec_pdf="spec.pdf",
    cut_sheet_pdf="veylan.pdf",
    expected_outcome="finding",
    evidence=D01,
)
SECOND_FINDING_CASE = EvalCase(
    id="E-03",
    spec_pdf="spec.pdf",
    cut_sheet_pdf="veylan.pdf",
    expected_outcome="finding",
    evidence=D02,
)
COMPLIANT_CASE = EvalCase(
    id="E-01",
    spec_pdf="spec.pdf",
    cut_sheet_pdf="caldra.pdf",
    expected_outcome="no_finding",
    evidence=None,
)
DECOY_CASE = EvalCase(
    id="E-18",
    spec_pdf="spec.pdf",
    cut_sheet_pdf="veylan.pdf",
    expected_outcome="no_finding",
    evidence=None,
    decoy=DECOY,
)
QUARANTINE_CASE = EvalCase(
    id="E-04",
    spec_pdf="spec.pdf",
    cut_sheet_pdf="altered.pdf",
    expected_outcome="quarantine",
    evidence=None,
)


def _pair(*cases: EvalCase) -> CasePair:
    return CasePair(
        spec_pdf=cases[0].spec_pdf, cut_sheet_pdf=cases[0].cut_sheet_pdf, cases=tuple(cases)
    )


def _summary(**overrides: object) -> AuditRunSummary:
    values: dict[str, object] = {
        "run_id": "run-1",
        "claims_made": 1,
        "rejected": 0,
        "retried": 0,
        "findings_persisted": 1,
        "rfi_path": "artifacts/rfi.pdf",
    }
    values.update(overrides)
    return AuditRunSummary(**values)  # type: ignore[arg-type]


def _finding(evidence: EvidencePair, severity: str = "high", **extra: object) -> dict[str, object]:
    return {
        "spec_quote": {"text": evidence.spec_quote, "page_number": evidence.spec_page},
        "cut_sheet_quote": {
            "text": evidence.cut_sheet_quote,
            "page_number": evidence.cut_sheet_page,
        },
        "severity": severity,
        **extra,
    }


def _outcome(**overrides: object) -> RunOutcome:
    values: dict[str, object] = {
        "run_id": "run-1",
        "quarantined": False,
        "model_turns": 1,
        "claims_made": 1,
        "rejected": 0,
        "retried": 0,
        "findings_persisted": 1,
        "caught_case_ids": frozenset(),
        "decoy_hit_case_ids": (),
        "unattributed_false_positives": 0,
        "severities": ("high",),
    }
    values.update(overrides)
    if "case_severities" not in values:
        values["case_severities"] = {
            case_id: tuple(values["severities"])  # type: ignore[arg-type]
            for case_id in values["caught_case_ids"]  # type: ignore[union-attr]
        }
    return RunOutcome(**values)  # type: ignore[arg-type]


def _lane(
    *,
    lane: str = LANE_ORIGINAL,
    mode: AgentMode = AgentMode.NAVIGATE,
    pairs: list[tuple[CasePair, list[RunOutcome]]],
) -> LaneResult:
    return aggregate(
        lane, mode, [pair for pair, _ in pairs], {pair.key: runs for pair, runs in pairs}
    )


def _one_case_lane(
    *, mode: AgentMode = AgentMode.NAVIGATE, catches: int = 1, iterations: int = 1
) -> LaneResult:
    runs = [
        _outcome(caught_case_ids=frozenset({FINDING_CASE.id} if index < catches else set()))
        for index in range(iterations)
    ]
    return _lane(mode=mode, pairs=[(_pair(FINDING_CASE), runs)])


# --- 1. Reading the manifest, lane by lane --------------------------------


def test_the_original_lane_declares_four_bound_audit_cases() -> None:
    """The harness reads its expectations from the manifest, not from itself."""
    cases = load_eval_cases(MANIFEST_TEXT, LANES[LANE_ORIGINAL])

    assert [case.id for case in cases] == ["E-01", "E-02", "E-03", "E-04"]
    assert [case.expected_outcome for case in cases] == [
        "no_finding",
        "finding",
        "finding",
        "quarantine",
    ]
    assert all(case.spec_path.is_file() and case.cut_sheet_path.is_file() for case in cases)


def test_the_messy_lane_declares_nine_cases_over_one_document_pair() -> None:
    cases = load_eval_cases(MANIFEST_TEXT, LANES[LANE_MESSY])

    assert [case.id for case in cases] == [f"E-{number}" for number in range(11, 20)]
    assert sum(1 for case in cases if case.expected_outcome == "finding") == 7
    assert sum(1 for case in cases if case.expected_outcome == "no_finding") == 2
    assert {case.pair_key for case in cases} == {
        ("nimbrin_thermal_annex_specification.pdf", "zarqelune_vantrel_package.pdf")
    }


def test_the_messy_lane_binds_each_decoy_case_to_its_decoy_pair() -> None:
    cases = {case.id: case for case in load_eval_cases(MANIFEST_TEXT, LANES[LANE_MESSY])}

    assert cases["E-18"].decoy is not None
    assert cases["E-18"].decoy.id == "N-01"
    assert cases["E-19"].decoy is not None
    assert cases["E-19"].decoy.id == "N-02"
    assert cases["E-11"].decoy is None


def test_each_lane_reads_its_own_evidence_block() -> None:
    original = load_evidence_pairs(MANIFEST_TEXT, LANES[LANE_ORIGINAL])
    messy = load_evidence_pairs(MANIFEST_TEXT, LANES[LANE_MESSY])

    assert set(original) == {"D-01", "D-02"}
    assert set(messy) == {f"M-0{number}" for number in range(1, 8)}
    assert messy["M-01"].spec_page == 17


def test_only_the_messy_lane_records_decoy_pairs() -> None:
    assert load_decoy_pairs(MANIFEST_TEXT, LANES[LANE_ORIGINAL]) == {}
    assert set(load_decoy_pairs(MANIFEST_TEXT, LANES[LANE_MESSY])) == {"N-01", "N-02"}


def test_a_finding_case_without_evidence_is_refused() -> None:
    text = MANIFEST_TEXT.replace('"evidence_id": "D-01"', '"evidence_id": null')

    with pytest.raises(ValueError, match="expects a finding but names no evidence pair"):
        load_eval_cases(text, LANES[LANE_ORIGINAL])


def test_a_case_naming_unknown_evidence_is_refused() -> None:
    text = MANIFEST_TEXT.replace('"evidence_id": "D-01"', '"evidence_id": "D-99"')

    with pytest.raises(ValueError, match="names unknown evidence D-99"):
        load_eval_cases(text, LANES[LANE_ORIGINAL])


def test_a_case_naming_an_unknown_decoy_is_refused() -> None:
    text = MANIFEST_TEXT.replace('"decoy_id": "N-01"', '"decoy_id": "N-99"')

    with pytest.raises(ValueError, match="names unknown decoy N-99"):
        load_eval_cases(text, LANES[LANE_MESSY])


def test_a_missing_manifest_block_is_named_in_the_error() -> None:
    with pytest.raises(ValueError, match="must contain the decoy-evidence-messy block"):
        load_decoy_pairs("no blocks here", LANES[LANE_MESSY])


# --- 2. Grouping cases into the audits that measure them ------------------


def test_the_original_lane_groups_into_four_single_case_audits() -> None:
    pairs = group_cases_into_pairs(load_eval_cases(MANIFEST_TEXT, LANES[LANE_ORIGINAL]))

    assert len(pairs) == 4
    assert all(len(pair.cases) == 1 for pair in pairs)


def test_the_messy_lane_groups_nine_cases_into_one_audit() -> None:
    """Nine cases on one pair are five audits at five iterations, not forty-five."""
    pairs = group_cases_into_pairs(load_eval_cases(MANIFEST_TEXT, LANES[LANE_MESSY]))

    assert len(pairs) == 1
    assert len(pairs[0].cases) == 9


def test_grouping_keeps_the_manifest_order_of_first_appearance() -> None:
    pairs = group_cases_into_pairs(
        [COMPLIANT_CASE, FINDING_CASE, SECOND_FINDING_CASE, QUARANTINE_CASE]
    )

    assert [pair.cut_sheet_pdf for pair in pairs] == ["caldra.pdf", "veylan.pdf", "altered.pdf"]
    assert [case.id for case in pairs[1].cases] == ["E-02", "E-03"]


# --- 3. Scoring one audit against every case of its pair ------------------


def test_an_exact_match_counts_as_a_catch_for_its_own_case() -> None:
    pair = _pair(FINDING_CASE, SECOND_FINDING_CASE)

    outcome = build_run_outcome(pair, _summary(), [_finding(D01)], model_turns=1)

    assert outcome.caught_case_ids == frozenset({"E-02"})
    assert outcome.unattributed_false_positives == 0


def test_one_audit_can_catch_several_cases_of_the_same_pair() -> None:
    pair = _pair(FINDING_CASE, SECOND_FINDING_CASE)

    outcome = build_run_outcome(
        pair, _summary(findings_persisted=2), [_finding(D01), _finding(D02)], model_turns=1
    )

    assert outcome.caught_case_ids == frozenset({"E-02", "E-03"})
    assert outcome.unattributed_false_positives == 0


def test_a_near_miss_is_not_a_catch_and_is_an_unattributed_false_positive() -> None:
    near_miss = _finding(D01)
    near_miss["cut_sheet_quote"] = {"text": "Nominal system: 208V, 3-phase.", "page_number": 1}

    outcome = build_run_outcome(_pair(FINDING_CASE), _summary(), [near_miss], model_turns=1)

    assert outcome.caught_case_ids == frozenset()
    assert outcome.unattributed_false_positives == 1
    assert outcome.decoy_hit_case_ids == ()


def test_a_decoy_match_is_counted_as_a_decoy_and_never_as_an_invention() -> None:
    pair = _pair(FINDING_CASE, DECOY_CASE)

    outcome = build_run_outcome(pair, _summary(), [_finding(DECOY)], model_turns=1)

    assert outcome.decoy_hit_case_ids == ("E-18",)
    assert outcome.unattributed_false_positives == 0
    assert outcome.caught_case_ids == frozenset()


def test_a_contiguous_tail_of_the_planted_quote_carries_the_same_evidence() -> None:
    """Evidence equality, not string equality: a shorter span of the same passage."""
    variant = _finding(D01)
    variant["cut_sheet_quote"] = {"text": "208V, 3-phase, 4-wire.", "page_number": 1}

    outcome = build_run_outcome(_pair(FINDING_CASE), _summary(), [variant], model_turns=1)

    assert outcome.caught_case_ids == frozenset({"E-02"})
    assert outcome.unattributed_false_positives == 0


def test_a_longer_span_that_contains_the_planted_quote_is_a_catch() -> None:
    """Containment runs in either direction: a table row label joined to its value."""
    variant = _finding(D01)
    variant["cut_sheet_quote"] = {
        "text": "Rating summary Nominal system: 208V, 3-phase, 4-wire. Main lugs only.",
        "page_number": 1,
    }

    outcome = build_run_outcome(_pair(FINDING_CASE), _summary(), [variant], model_turns=1)

    assert outcome.caught_case_ids == frozenset({"E-02"})
    assert outcome.unattributed_false_positives == 0


def test_the_planted_text_on_the_wrong_page_is_not_a_catch() -> None:
    """The cited page is half of the evidence. The same words elsewhere are not it."""
    wrong_page = _finding(D01)
    wrong_page["cut_sheet_quote"] = {"text": D01.cut_sheet_quote, "page_number": 2}

    outcome = build_run_outcome(_pair(FINDING_CASE), _summary(), [wrong_page], model_turns=1)

    assert outcome.caught_case_ids == frozenset()
    assert outcome.unattributed_false_positives == 1


def test_a_quote_that_only_overlaps_the_planted_span_is_not_a_catch() -> None:
    """Overlap is not containment. Neither string contains the other."""
    overlap = _finding(D01)
    overlap["cut_sheet_quote"] = {
        "text": "3-phase, 4-wire. Main lugs only.",
        "page_number": 1,
    }

    outcome = build_run_outcome(_pair(FINDING_CASE), _summary(), [overlap], model_turns=1)

    assert outcome.caught_case_ids == frozenset()
    assert outcome.unattributed_false_positives == 1


def test_an_empty_quote_matches_nothing() -> None:
    """The empty string is contained by everything under a naive rule. Not here."""
    empty = _finding(D01)
    empty["cut_sheet_quote"] = {"text": "", "page_number": 1}

    outcome = build_run_outcome(_pair(FINDING_CASE), _summary(), [empty], model_turns=1)

    assert outcome.caught_case_ids == frozenset()
    assert outcome.unattributed_false_positives == 1


def test_a_quote_riding_inside_a_larger_number_is_not_a_catch() -> None:
    """Raw containment succeeds here. Only the gate's token boundaries refuse it.

    A matcher using plain ``in`` would score this as a catch, so this test is
    what separates the shipped rule from that weaker one.
    """
    rating = EvidencePair(
        id="D-90",
        spec_page=1,
        spec_quote="Rated 5 A continuous.",
        cut_sheet_page=1,
        cut_sheet_quote="5 A",
    )
    case = EvalCase(
        id="E-90",
        spec_pdf="spec.pdf",
        cut_sheet_pdf="veylan.pdf",
        expected_outcome="finding",
        evidence=rating,
    )
    riding = _finding(rating)
    riding["cut_sheet_quote"] = {"text": "0.5 A", "page_number": 1}
    assert "5 a" in normalize("0.5 A")

    outcome = build_run_outcome(_pair(case), _summary(), [riding], model_turns=1)

    assert outcome.caught_case_ids == frozenset()
    assert outcome.unattributed_false_positives == 1


def test_the_gates_own_normalization_decides_the_match() -> None:
    """Case, whitespace runs, and a soft hyphen are the gate's business, not the scorer's."""
    variant = _finding(D01)
    variant["cut_sheet_quote"] = {
        "text": "NOMINAL   SYSTEM:\n208V, 3-PHASE, 4-WI\u00adRE.",
        "page_number": 1,
    }

    outcome = build_run_outcome(_pair(FINDING_CASE), _summary(), [variant], model_turns=1)

    assert outcome.caught_case_ids == frozenset({"E-02"})
    assert outcome.unattributed_false_positives == 0


@pytest.mark.parametrize(
    "quote",
    [
        {"text": "Nominal system: 208V, 3-phase, 4-wire.", "page_number": None},
        {"text": "Nominal system: 208V, 3-phase, 4-wire.", "page_number": "1"},
        {"text": "Nominal system: 208V, 3-phase, 4-wire.", "page_number": 1.9},
        {"text": None, "page_number": 1},
        {"text": 208, "page_number": 1},
        "Nominal system: 208V, 3-phase, 4-wire.",
    ],
    ids=["page-none", "page-string", "page-float", "text-none", "text-int", "quote-not-a-mapping"],
)
def test_a_malformed_quote_record_is_never_a_catch(quote: object) -> None:
    """A malformed record is not evidence, and coercing one could invent a catch."""
    malformed = _finding(D01)
    malformed["cut_sheet_quote"] = quote

    outcome = build_run_outcome(_pair(FINDING_CASE), _summary(), [malformed], model_turns=1)

    assert outcome.caught_case_ids == frozenset()
    assert outcome.unattributed_false_positives == 1


def test_a_quote_wide_enough_to_carry_two_pairs_is_attributed_to_neither() -> None:
    """Several planted pairs share pages, so a page-wide quote names no discrepancy."""
    wide = {
        "spec_quote": {
            "text": (
                "Provide a 480V, 3-phase distribution switchboard for service distribution. "
                "Terminations shall be rated 90 deg C minimum."
            ),
            "page_number": 3,
        },
        "cut_sheet_quote": {
            "text": "Nominal system: 208V, 3-phase, 4-wire. Termination rating: 158 deg F.",
            "page_number": 1,
        },
        "severity": "high",
    }
    same_page = EvidencePair(
        id="D-02b",
        spec_page=3,
        spec_quote="Terminations shall be rated 90 deg C minimum.",
        cut_sheet_page=1,
        cut_sheet_quote="Termination rating: 158 deg F.",
    )
    second = EvalCase(
        id="E-03",
        spec_pdf="spec.pdf",
        cut_sheet_pdf="veylan.pdf",
        expected_outcome="finding",
        evidence=same_page,
    )

    outcome = build_run_outcome(_pair(FINDING_CASE, second), _summary(), [wide], model_turns=1)

    assert outcome.caught_case_ids == frozenset()
    assert outcome.decoy_hit_case_ids == ()
    assert outcome.unattributed_false_positives == 1


def test_a_quote_carrying_a_planted_pair_and_a_decoy_credits_neither() -> None:
    """A finding that is both a catch and a decoy hit is evidence for no case."""
    both = {
        "spec_quote": {
            "text": (
                "Provide a 480V, 3-phase distribution switchboard for service distribution. "
                "The cabinet finish shall be graphite gray."
            ),
            "page_number": 3,
        },
        "cut_sheet_quote": {
            "text": "Nominal system: 208V, 3-phase, 4-wire. Finish: graphite-grey baked coating.",
            "page_number": 1,
        },
        "severity": "high",
    }
    overlapping_decoy = EvidencePair(
        id="N-01b",
        spec_page=3,
        spec_quote="The cabinet finish shall be graphite gray.",
        cut_sheet_page=1,
        cut_sheet_quote="graphite-grey baked coating",
    )
    decoy_case = EvalCase(
        id="E-18",
        spec_pdf="spec.pdf",
        cut_sheet_pdf="veylan.pdf",
        expected_outcome="no_finding",
        evidence=None,
        decoy=overlapping_decoy,
    )

    outcome = build_run_outcome(_pair(FINDING_CASE, decoy_case), _summary(), [both], model_turns=1)

    assert outcome.caught_case_ids == frozenset()
    assert outcome.decoy_hit_case_ids == ()
    assert outcome.unattributed_false_positives == 1


def test_a_span_variant_of_a_decoy_is_still_counted_as_a_decoy() -> None:
    """The same rule governs both sides of the ledger, so a variant decoy is no invention."""
    pair = _pair(FINDING_CASE, DECOY_CASE)
    variant = _finding(DECOY)
    variant["cut_sheet_quote"] = {"text": "baked coating", "page_number": 9}

    outcome = build_run_outcome(pair, _summary(), [variant], model_turns=1)

    assert outcome.decoy_hit_case_ids == ("E-18",)
    assert outcome.unattributed_false_positives == 0
    assert outcome.caught_case_ids == frozenset()


def test_every_persisted_finding_on_a_compliant_case_is_a_false_positive() -> None:
    outcome = build_run_outcome(
        _pair(COMPLIANT_CASE), _summary(), [_finding(D01), _finding(D02)], model_turns=1
    )

    assert outcome.caught_case_ids == frozenset()
    assert outcome.unattributed_false_positives == 2


def test_a_quarantined_run_reports_no_model_turn_and_no_finding() -> None:
    quarantine = RunQuarantine(
        reason="hidden_text_layer",
        documents=[
            QuarantinedDocument(
                document_role=DocumentRole.SUBMITTED_DOCUMENT,
                document_sha256="a" * 64,
                page_count=2,
                flagged_pages=[1],
                detectors=["invisible_render_mode"],
                hidden_span_count=2,
            )
        ],
    )
    summary = _summary(claims_made=0, findings_persisted=0, rfi_path=None, quarantine=quarantine)

    outcome = build_run_outcome(_pair(QUARANTINE_CASE), summary, [], model_turns=0)

    assert outcome.quarantined is True
    assert outcome.model_turns == 0
    assert outcome.findings_persisted == 0
    assert outcome.model_output_invalid is False


def test_the_outcome_carries_the_token_tool_and_self_check_receipts() -> None:
    summary = _summary(
        audit_model_usage=AuditModelUsage(prompt_tokens=1234, output_tokens=56, total_tokens=1290),
        self_check_rejections=2,
        self_check_rejected_quote_returned=True,
    )

    outcome = build_run_outcome(_pair(FINDING_CASE), summary, [_finding(D01)], model_turns=1)

    assert outcome.prompt_tokens == 1234
    assert outcome.self_check_rejections == 2
    assert outcome.self_check_rejected_quote_returned is True


def test_a_run_with_no_usable_model_turn_is_flagged() -> None:
    summary = _summary(claims_made=0, rejected=1, findings_persisted=0, rfi_path=None)

    outcome = build_run_outcome(_pair(COMPLIANT_CASE), summary, [], model_turns=1)

    assert outcome.model_output_invalid is True


# --- 4. Rates, and the promise never to round one up ----------------------


def test_catch_rate_is_the_measured_fraction_not_a_rounded_claim() -> None:
    lane = _one_case_lane(catches=3, iterations=4)

    assert lane.cases[0].catch_rate == 0.75
    assert "75%" in render_case_table(lane.cases)


def test_a_case_with_nothing_planted_reports_no_catch_rate() -> None:
    lane = _lane(pairs=[(_pair(COMPLIANT_CASE), [_outcome(findings_persisted=0, severities=())])])

    assert lane.cases[0].catch_rate is None
    assert "| n/a |" in render_case_table(lane.cases)


def test_the_overall_catch_rate_weights_every_run_equally() -> None:
    first = CaseResult(
        case=FINDING_CASE,
        runs=[_outcome(caught_case_ids=frozenset({"E-02"})) for _ in range(3)],
    )
    second = CaseResult(case=SECOND_FINDING_CASE, runs=[_outcome() for _ in range(1)])

    assert overall_catch_rate([first, second]) == 0.75


def test_a_short_catch_rate_falls_back_to_a_decimal_rather_than_rounding_up() -> None:
    """199 of 200 rounds to 100 at whole percent, so it is published as 99.5."""
    table = render_case_table(_one_case_lane(catches=199, iterations=200).cases)

    assert "| 99.5% |" in table
    assert "| 100% |" not in table


def test_a_rate_that_still_rounds_to_complete_publishes_as_under_one_hundred() -> None:
    """9999 of 10000 rounds to 100 even at one decimal, so it publishes as `<100%`."""
    table = render_case_table(_one_case_lane(catches=9999, iterations=10000).cases)

    assert "| <100% |" in table
    assert "| 100% |" not in table


def test_a_rare_catch_never_publishes_as_zero_percent() -> None:
    """The same guard runs at the bottom: 1 of 10000 is not none."""
    table = render_case_table(_one_case_lane(catches=1, iterations=10000).cases)

    catch_rate_cell = table.splitlines()[-1].split("|")[4].strip()

    assert catch_rate_cell == ">0%"


def test_an_exact_rate_still_publishes_as_one_hundred_percent() -> None:
    lane = _one_case_lane(catches=5, iterations=5)

    assert "100%" in render_case_table(lane.cases)


def test_a_mean_with_nothing_to_average_reads_as_not_applicable() -> None:
    lane = _lane(pairs=[(_pair(FINDING_CASE), [_outcome(prompt_tokens=None)])])

    assert lane.pairs[0].mean_prompt_tokens is None
    assert "| n/a |" in render_pair_table(lane.pairs)


def test_the_mean_prompt_token_count_skips_runs_that_reported_none() -> None:
    lane = _lane(
        pairs=[
            (
                _pair(FINDING_CASE),
                [
                    _outcome(prompt_tokens=100),
                    _outcome(prompt_tokens=None),
                    _outcome(prompt_tokens=200),
                ],
            )
        ]
    )

    assert lane.pairs[0].mean_prompt_tokens == 150


# --- 5. Whether a case matched what the manifest declared -----------------


def test_a_finding_case_passes_only_when_every_run_caught_it() -> None:
    caught = _one_case_lane(catches=2, iterations=2)
    missed = _one_case_lane(catches=1, iterations=2)

    assert caught.cases[0].meets_expectation is True
    assert missed.cases[0].meets_expectation is False


def test_a_decoy_case_fails_when_a_run_reproduced_its_decoy() -> None:
    lane = _lane(pairs=[(_pair(DECOY_CASE), [_outcome(decoy_hit_case_ids=("E-18",))])])

    assert lane.cases[0].decoy_false_positives == 1
    assert lane.cases[0].meets_expectation is False


def test_a_decoy_case_fails_when_a_run_invented_a_finding() -> None:
    lane = _lane(pairs=[(_pair(DECOY_CASE), [_outcome(unattributed_false_positives=1)])])

    assert lane.cases[0].meets_expectation is False


def test_a_compliant_case_passes_only_with_no_finding_of_any_kind() -> None:
    lane = _lane(pairs=[(_pair(COMPLIANT_CASE), [_outcome(findings_persisted=0, severities=())])])

    assert lane.cases[0].meets_expectation is True


def test_a_run_with_no_usable_model_turn_fails_the_compliant_case() -> None:
    """Persisting nothing because the model broke is not the declared outcome."""
    lane = _lane(
        pairs=[
            (
                _pair(COMPLIANT_CASE),
                [_outcome(findings_persisted=0, severities=(), model_output_invalid=True)],
            )
        ]
    )

    assert lane.cases[0].meets_expectation is False


def test_a_compliant_case_that_never_reached_the_model_fails() -> None:
    lane = _lane(
        pairs=[
            (_pair(COMPLIANT_CASE), [_outcome(findings_persisted=0, severities=(), model_turns=0)])
        ]
    )

    assert lane.cases[0].meets_expectation is False


def test_the_quarantine_case_only_passes_with_zero_model_turns() -> None:
    clean = _lane(
        pairs=[
            (
                _pair(QUARANTINE_CASE),
                [_outcome(quarantined=True, model_turns=0, findings_persisted=0, severities=())],
            )
        ]
    )
    leaked = _lane(
        pairs=[
            (
                _pair(QUARANTINE_CASE),
                [_outcome(quarantined=True, model_turns=1, findings_persisted=0, severities=())],
            )
        ]
    )

    assert clean.cases[0].meets_expectation is True
    assert leaked.cases[0].meets_expectation is False


def test_a_quarantined_run_that_persisted_a_finding_fails() -> None:
    lane = _lane(
        pairs=[
            (
                _pair(QUARANTINE_CASE),
                [_outcome(quarantined=True, model_turns=0, findings_persisted=1)],
            )
        ]
    )

    assert lane.cases[0].meets_expectation is False


def test_aggregate_keeps_manifest_order_and_survives_a_pair_with_no_runs() -> None:
    pairs = [_pair(FINDING_CASE), _pair(COMPLIANT_CASE)]
    lane = aggregate(LANE_ORIGINAL, AgentMode.NAVIGATE, pairs, {pairs[0].key: [_outcome()]})

    assert [result.case.id for result in lane.cases] == ["E-02", "E-01"]
    assert lane.cases[1].iterations == 0
    assert lane.cases[1].catch_rate is None
    assert lane.cases[1].meets_expectation is False


def test_severity_distribution_counts_only_findings_attributed_to_the_case() -> None:
    """A shared pair's runs must not repeat their findings once per case."""
    lane = _lane(
        pairs=[
            (
                _pair(FINDING_CASE),
                [
                    _outcome(
                        caught_case_ids=frozenset({FINDING_CASE.id}),
                        severities=("high", "low"),
                        case_severities={FINDING_CASE.id: ("high",)},
                        unattributed_false_positives=1,
                    ),
                    _outcome(
                        caught_case_ids=frozenset({FINDING_CASE.id}),
                        severities=("high",),
                        case_severities={FINDING_CASE.id: ("high",)},
                    ),
                ],
            )
        ]
    )

    assert lane.cases[0].severity_distribution == {"high": 2}


def test_a_caught_case_with_no_attribution_shows_no_findings() -> None:
    lane = _lane(
        pairs=[(_pair(FINDING_CASE), [_outcome(severities=("high",), case_severities={})])]
    )

    assert lane.cases[0].severity_distribution == {}


# --- 6. The ship gate, against known-good and known-bad results -----------


def _gate_lane(
    lane: str,
    mode: AgentMode,
    *,
    e01_false_positives: int = 0,
    e02_catches: int = 5,
    e03_catches: int = 5,
    e04_quarantines: int = 5,
    e04_turns: int = 0,
    messy_catches: int = 5,
    messy_decoys: int = 0,
    iterations: int = 5,
) -> LaneResult:
    """Build one lane result with exactly the numbers the gate reads."""
    if lane == LANE_ORIGINAL:
        return _lane(
            lane=lane,
            mode=mode,
            pairs=[
                (
                    _pair(COMPLIANT_CASE),
                    [
                        _outcome(
                            unattributed_false_positives=(e01_false_positives if index == 0 else 0),
                            findings_persisted=0,
                            severities=(),
                        )
                        for index in range(iterations)
                    ],
                ),
                (
                    _pair(FINDING_CASE),
                    [
                        _outcome(
                            caught_case_ids=frozenset({"E-02"} if index < e02_catches else set())
                        )
                        for index in range(iterations)
                    ],
                ),
                (
                    _pair(
                        EvalCase(
                            id="E-03",
                            spec_pdf="spec.pdf",
                            cut_sheet_pdf="torven.pdf",
                            expected_outcome="finding",
                            evidence=D02,
                        )
                    ),
                    [
                        _outcome(
                            caught_case_ids=frozenset({"E-03"} if index < e03_catches else set())
                        )
                        for index in range(iterations)
                    ],
                ),
                (
                    _pair(QUARANTINE_CASE),
                    [
                        _outcome(
                            quarantined=index < e04_quarantines,
                            model_turns=e04_turns,
                            findings_persisted=0,
                            severities=(),
                        )
                        for index in range(iterations)
                    ],
                ),
            ],
        )
    messy_case = EvalCase(
        id="E-11",
        spec_pdf="nimbrin.pdf",
        cut_sheet_pdf="zarqelune.pdf",
        expected_outcome="finding",
        evidence=D01,
    )
    messy_decoy_case = EvalCase(
        id="E-18",
        spec_pdf="nimbrin.pdf",
        cut_sheet_pdf="zarqelune.pdf",
        expected_outcome="no_finding",
        evidence=None,
        decoy=DECOY,
    )
    return _lane(
        lane=lane,
        mode=mode,
        pairs=[
            (
                _pair(messy_case, messy_decoy_case),
                [
                    _outcome(
                        caught_case_ids=frozenset({"E-11"} if index < messy_catches else set()),
                        decoy_hit_case_ids=("E-18",) if index < messy_decoys else (),
                    )
                    for index in range(iterations)
                ],
            )
        ],
    )


def _gate_results(**navigate_overrides: object) -> dict[tuple[str, str], LaneResult]:
    """A known-good result set, with the named navigate numbers overridden."""
    original_overrides = {
        key: value for key, value in navigate_overrides.items() if key.startswith("e0")
    }
    messy_overrides = {
        key: value for key, value in navigate_overrides.items() if key.startswith("messy")
    }
    return {
        (LANE_ORIGINAL, AgentMode.FULL_TEXT.value): _gate_lane(LANE_ORIGINAL, AgentMode.FULL_TEXT),
        (LANE_ORIGINAL, AgentMode.NAVIGATE.value): _gate_lane(
            LANE_ORIGINAL,
            AgentMode.NAVIGATE,
            **original_overrides,  # type: ignore[arg-type]
        ),
        (LANE_MESSY, AgentMode.FULL_TEXT.value): _gate_lane(LANE_MESSY, AgentMode.FULL_TEXT),
        (LANE_MESSY, AgentMode.NAVIGATE.value): _gate_lane(
            LANE_MESSY,
            AgentMode.NAVIGATE,
            **messy_overrides,  # type: ignore[arg-type]
        ),
    }


def test_the_ship_gate_passes_a_known_good_result_set() -> None:
    verdict = evaluate_ship_gate(_gate_results())

    assert verdict.evaluable is True
    assert verdict.navigate_ships is True
    assert verdict.shipping_mode is AgentMode.NAVIGATE
    assert all(line.passed for line in verdict.lines)
    assert "navigate SHIPS as default" in verdict.render()


@pytest.mark.parametrize(
    ("override", "failing_line"),
    [
        ({"e02_catches": 4}, "E-02 catch rate is 100%"),
        ({"e03_catches": 0}, "E-03 catch rate is 100%"),
        ({"e01_false_positives": 1}, "E-01 false positives are 0"),
        ({"e04_quarantines": 4}, "E-04 quarantine rate is 100% with 0 model turns"),
        ({"e04_turns": 1}, "E-04 quarantine rate is 100% with 0 model turns"),
        ({"messy_catches": 2}, "messy-lane catch rate is at least full_text's"),
        ({"messy_decoys": 1}, "messy-lane decoy false positives are at most full_text's"),
    ],
)
def test_the_ship_gate_fails_each_known_bad_result_set(
    override: dict[str, object], failing_line: str
) -> None:
    verdict = evaluate_ship_gate(_gate_results(**override))

    assert verdict.navigate_ships is False
    assert verdict.shipping_mode is AgentMode.FULL_TEXT
    failed = [line.name for line in verdict.lines if not line.passed]
    assert failing_line in failed
    assert "navigate does NOT ship" in verdict.render()


def test_a_catch_rate_regression_against_full_text_fails_the_gate() -> None:
    results = _gate_results()
    results[(LANE_ORIGINAL, AgentMode.NAVIGATE.value)] = _gate_lane(
        LANE_ORIGINAL, AgentMode.NAVIGATE, e02_catches=3
    )

    verdict = evaluate_ship_gate(results)

    failed = [line.name for line in verdict.lines if not line.passed]
    assert "no original-lane catch-rate regression against full_text" in failed


def test_a_false_positive_regression_against_full_text_fails_the_gate() -> None:
    results = _gate_results(e01_false_positives=1)

    verdict = evaluate_ship_gate(results)

    failed = [line.name for line in verdict.lines if not line.passed]
    assert "no original-lane false-positive regression against full_text" in failed


def test_the_gate_is_not_evaluable_without_both_modes_and_both_lanes() -> None:
    partial = {
        key: value
        for key, value in _gate_results().items()
        if key != (LANE_MESSY, AgentMode.NAVIGATE.value)
    }

    verdict = evaluate_ship_gate(partial)

    assert verdict.evaluable is False
    assert verdict.navigate_ships is False
    assert verdict.shipping_mode is AgentMode.FULL_TEXT
    assert "missing messy/navigate" in verdict.render()


def test_an_empty_verdict_never_ships() -> None:
    """An unevaluated gate is not a pass, however few conditions it holds."""
    assert ShipGateVerdict(lines=(), evaluable=True).navigate_ships is False


# --- 7. What the published documents say ----------------------------------


def _two_section_results() -> dict[tuple[str, str], LaneResult]:
    return {
        (LANE_ORIGINAL, AgentMode.FULL_TEXT.value): _one_case_lane(
            mode=AgentMode.FULL_TEXT, catches=1, iterations=1
        ),
        (LANE_ORIGINAL, AgentMode.NAVIGATE.value): _one_case_lane(
            mode=AgentMode.NAVIGATE, catches=1, iterations=1
        ),
    }


def _document(results: dict[tuple[str, str], LaneResult] | None = None, **overrides: object) -> str:
    values: dict[str, object] = {
        "iterations": 1,
        "run_date": "2026-08-23",
        "model_id": "gemini-3.7-flash",
        "severity_model_id": "none",
        "vertex_spend": "unavailable",
        "verdict": evaluate_ship_gate({}),
        "code_revision": "abc1234",
    }
    values.update(overrides)
    return render_eval_markdown(
        _two_section_results() if results is None else results,
        **values,  # type: ignore[arg-type]
    )


def test_the_eval_document_carries_one_section_per_mode_and_lane() -> None:
    document = _document()

    assert "## Results — `full_text` mode, original four-case lane" in document
    assert "## Results — `navigate` mode, original four-case lane" in document
    assert document.index("`full_text` mode") < document.index("`navigate` mode")


def test_the_eval_document_carries_the_gate_verdict_verbatim() -> None:
    verdict = evaluate_ship_gate(_gate_results())

    document = _document(verdict=verdict)

    assert verdict.render() in document
    assert "No condition was relaxed" in document


@pytest.mark.parametrize(
    ("iterations", "revision"),
    [(10, "31ef186"), (3, "deadbee"), (1, UNRECORDED_REVISION)],
)
def test_the_eval_document_says_what_it_supersedes_and_names_the_measurement(
    iterations: int, revision: str
) -> None:
    """A regeneration replaces this file in full, so it must say so itself.

    The values vary so a renderer hardcoded to the published run cannot pass.
    """
    document = _document(iterations=iterations, code_revision=revision)

    assert "## What these numbers supersede" in document
    assert "written in full by `scripts/eval_fixtures.py` on every run" in document
    assert (
        f"measured at {iterations} audits per document pair on code revision `{revision}`"
        in document
    )
    assert "superseded by this one, not corrected by it" in document


def test_the_eval_document_names_the_corrected_self_check_counting_rule() -> None:
    """The earlier edition counted digests. The reader must be told which rule ran."""
    document = _document()

    assert "corrected counting rule" in document
    assert "counted distinct quote digests" in document


@pytest.mark.parametrize(("iterations", "inflated"), [(10, 90), (5, 45), (1, 9)])
def test_the_method_section_states_the_audit_arithmetic_of_the_run_it_describes(
    iterations: int, inflated: int
) -> None:
    """The nine-case pair claim is arithmetic, so it must follow the iteration count."""
    document = _document(iterations=iterations)

    assert f"so {iterations} iterations are {iterations} audits, not {inflated}" in document
    assert "not forty-five" not in document


def test_the_eval_document_publishes_a_short_catch_rate_as_measured() -> None:
    results = {(LANE_ORIGINAL, AgentMode.NAVIGATE.value): _one_case_lane(catches=3, iterations=4)}

    document = _document(results)

    assert "75%" in document
    assert "did not match the manifest" in document
    assert "`E-02` in `navigate` mode" in document


def test_the_eval_document_states_plainly_when_every_case_matched() -> None:
    document = _document()

    assert "None. Every case matched its declared expected outcome in every run." in document


def test_the_eval_document_says_when_the_retry_loop_never_fired() -> None:
    document = _document()

    assert "the rejection-and-retry loop did not fire" in document


def test_the_eval_document_reports_a_retry_loop_that_did_fire() -> None:
    results = {
        (LANE_ORIGINAL, AgentMode.NAVIGATE.value): _lane(
            pairs=[
                (
                    _pair(FINDING_CASE),
                    [_outcome(caught_case_ids=frozenset({"E-02"}), rejected=2, retried=1)],
                )
            ]
        )
    }

    document = _document(results)

    assert "rejected 2 claims and the runtime retried 1" in document


def test_the_eval_document_says_a_navigate_self_check_changed_nothing() -> None:
    document = _document({(LANE_ORIGINAL, AgentMode.NAVIGATE.value): _one_case_lane()})

    assert "quote self-check rejected nothing in this lane" in document


def test_the_eval_document_reports_a_self_check_the_model_ignored() -> None:
    results = {
        (LANE_ORIGINAL, AgentMode.NAVIGATE.value): _lane(
            pairs=[
                (
                    _pair(FINDING_CASE),
                    [
                        _outcome(
                            caught_case_ids=frozenset({"E-02"}),
                            self_check_rejections=3,
                            self_check_rejected_quote_returned=True,
                        )
                    ],
                )
            ]
        )
    }

    document = _document(results)

    assert "self-check rejected 3 quotes, and 1 runs still returned" in document
    assert "The runtime never trusted that check" in document


def test_the_eval_document_says_full_text_registers_tools_and_records_calls() -> None:
    document = _document(
        {(LANE_ORIGINAL, AgentMode.FULL_TEXT.value): _one_case_lane(mode=AgentMode.FULL_TEXT)}
    )

    assert "The mode still registers all five tools" in document
    assert "the calls the model chose to make" in document
    assert "zero by construction" not in document
    assert "self-check rejected nothing in this lane" in document


def test_the_eval_document_names_every_severity_fallback() -> None:
    results = {
        (LANE_ORIGINAL, AgentMode.NAVIGATE.value): _lane(
            pairs=[
                (
                    _pair(FINDING_CASE),
                    [
                        _outcome(
                            caught_case_ids=frozenset({"E-02"}),
                            severities=("unclassified",),
                        )
                    ],
                )
            ]
        )
    }

    document = _document(results)

    assert "1 of 1 persisted findings carry `unclassified` severity" in document


def test_the_eval_document_omits_the_fallback_note_when_all_were_classified() -> None:
    document = _document()

    assert "unclassified` severity" not in document


def test_the_eval_document_lists_every_run_identifier_as_a_receipt() -> None:
    results = {
        (LANE_ORIGINAL, AgentMode.NAVIGATE.value): _lane(
            pairs=[
                (
                    _pair(FINDING_CASE),
                    [
                        _outcome(run_id="run-a", caught_case_ids=frozenset({"E-02"})),
                        _outcome(run_id="run-b", caught_case_ids=frozenset({"E-02"})),
                    ],
                )
            ]
        )
    }

    document = _document(results)

    assert "`run-a`, `run-b`" in document


def test_the_eval_document_names_unusable_runs() -> None:
    results = {
        (LANE_ORIGINAL, AgentMode.NAVIGATE.value): _lane(
            pairs=[(_pair(FINDING_CASE), [_outcome(model_output_invalid=True)])]
        )
    }

    document = _document(results)

    assert "1 case-runs produced no usable model turn" in document


def test_the_eval_header_names_the_code_revision_the_numbers_describe() -> None:
    assert "- Code revision these numbers describe: `abc1234`" in _document()


def test_an_unreadable_revision_is_recorded_as_unrecorded() -> None:
    document = _document(code_revision="not recorded for this run")

    assert "- Code revision these numbers describe: `not recorded for this run`" in document


def test_the_header_publishes_the_severity_model_the_runs_recorded() -> None:
    results = {
        (LANE_ORIGINAL, AgentMode.NAVIGATE.value): _lane(
            pairs=[
                (
                    _pair(FINDING_CASE),
                    [
                        _outcome(
                            caught_case_ids=frozenset({"E-02"}),
                            severity_model_ids=("gemma-3-12b-it",),
                        )
                    ],
                )
            ]
        )
    }

    document = _document(results)

    assert "measured from the persisted findings: `gemma-3-12b-it`" in document


def test_the_header_says_so_when_no_severity_model_was_recorded() -> None:
    assert "none recorded on any persisted finding" in _document()


# --- 8. The README block --------------------------------------------------


def _readme_section(
    results: dict[tuple[str, str], LaneResult] | None = None, **overrides: object
) -> str:
    values: dict[str, object] = {
        "iterations": 1,
        "run_date": "2026-08-23",
        "verdict": evaluate_ship_gate({}),
        "code_revision": "abc1234",
    }
    values.update(overrides)
    return render_readme_section(
        _two_section_results() if results is None else results,
        **values,  # type: ignore[arg-type]
    )


def test_the_gate_is_unevaluable_when_a_messy_lane_has_no_usable_run() -> None:
    """Two lanes of broken runs must never compare as equal measurements."""
    results = _gate_results()
    for mode in (AgentMode.FULL_TEXT, AgentMode.NAVIGATE):
        key = (LANE_MESSY, mode.value)
        pair = results[key].pairs[0].pair
        dead_runs = [
            _outcome(
                model_output_invalid=True,
                claims_made=0,
                rejected=1,
                findings_persisted=0,
                severities=(),
            )
            for _ in range(5)
        ]
        results[key] = _lane(lane=LANE_MESSY, mode=mode, pairs=[(pair, dead_runs)])

    verdict = evaluate_ship_gate(results)

    assert verdict.evaluable is False
    assert verdict.navigate_ships is False
    assert "no usable run" in verdict.reason


def test_the_readme_block_refuses_a_run_that_never_measured_the_shipping_mode() -> None:
    """A navigate-only run must not rewrite README with a full-text claim."""
    results = {(LANE_MESSY, AgentMode.NAVIGATE.value): _one_case_lane()}

    with pytest.raises(ValueError, match="measured no case in full_text mode"):
        _readme_section(results)


def test_the_readme_publishes_the_mode_that_ships() -> None:
    section = _readme_section(verdict=evaluate_ship_gate(_gate_results()))

    assert "in `navigate` mode" in section
    assert "met every condition of the ship gate and is the deployed default" in section


def test_the_readme_says_navigate_did_not_ship_when_the_gate_failed() -> None:
    section = _readme_section(verdict=evaluate_ship_gate(_gate_results(e02_catches=1)))

    assert "in `full_text` mode" in section
    assert "did not meet the ship gate" in section


def test_the_readme_says_the_gate_was_not_evaluated_when_it_could_not_be() -> None:
    section = _readme_section()

    assert "The ship gate was not evaluated for this run" in section
    assert "in `full_text` mode" in section


def test_the_readme_section_says_so_when_the_catch_rate_is_short() -> None:
    results = {
        (LANE_ORIGINAL, AgentMode.FULL_TEXT.value): _one_case_lane(
            mode=AgentMode.FULL_TEXT, catches=3, iterations=4
        )
    }

    section = _readme_section(results)

    assert "75%" in section
    assert "SpecGuard does not catch every planted discrepancy on every run." in section


def test_the_readme_block_names_the_code_revision() -> None:
    assert "on code revision `abc1234`" in _readme_section()


def test_write_readme_section_replaces_only_the_marked_block() -> None:
    readme = "\n".join(["before", README_TABLE_START, "old", README_TABLE_END, "after", ""])
    replacement = "\n".join([README_TABLE_START, "new", README_TABLE_END])

    updated = write_readme_section(readme, replacement)

    assert updated == "\n".join(
        ["before", README_TABLE_START, "new", README_TABLE_END, "after", ""]
    )


def test_write_readme_section_refuses_a_readme_with_no_markers() -> None:
    with pytest.raises(ValueError, match="must contain the eval-table markers"):
        write_readme_section("no markers here", "section")


def test_the_committed_readme_carries_the_publication_markers() -> None:
    readme = (Path(__file__).parents[1] / "README.md").read_text(encoding="utf-8")

    assert readme.count(README_TABLE_START) == 1
    assert readme.count(README_TABLE_END) == 1


# --- 9. Comparing this run to the published one ---------------------------


def test_the_previous_published_table_is_read_back_from_the_readme() -> None:
    section = _readme_section(run_date="2026-08-21")

    previous = read_previous_readme_section(section)

    assert previous is not None
    assert previous.run_date == "2026-08-21"
    assert "E-02" in previous.rows


def test_an_unchanged_table_says_no_number_moved() -> None:
    previous = read_previous_readme_section(_readme_section(run_date="2026-08-21"))
    table = render_case_table(_one_case_lane().cases)

    assert "No number in this table moved from the 2026-08-21 run." in compare_to_previous(
        table, previous
    )


def test_a_changed_number_is_named_plainly() -> None:
    previous = read_previous_readme_section(_readme_section(run_date="2026-08-21"))
    table = render_case_table(_one_case_lane(catches=0, iterations=1).cases)

    statement = compare_to_previous(table, previous)

    assert "Numbers moved from the 2026-08-21 run." in statement
    assert "`E-02`" in statement


def test_a_table_with_different_columns_is_not_compared_cell_by_cell() -> None:
    """A changed column set is a changed table, not a changed measurement."""
    previous = read_previous_readme_section(
        "\n".join(
            [
                README_TABLE_START,
                "Measured on 2026-08-21 by x",
                "| `E-02` | a | b |",
                README_TABLE_END,
            ]
        )
    )
    table = render_case_table(_one_case_lane().cases)

    statement = compare_to_previous(table, previous)

    assert "carried a different set of columns" in statement


def test_a_readme_with_no_earlier_block_says_there_is_nothing_to_compare() -> None:
    assert "No earlier measured table was published" in compare_to_previous("", None)


def test_the_readme_block_carries_the_comparison_statement() -> None:
    previous = read_previous_readme_section(_readme_section(run_date="2026-08-21"))

    section = _readme_section(previous=previous)

    assert "No number in this table moved from the 2026-08-21 run." in section


# --- 10. Flags and the machine-readable summary line ----------------------


def test_the_mode_flag_expands_to_the_modes_to_measure() -> None:
    assert selected_modes("full_text") == [AgentMode.FULL_TEXT]
    assert selected_modes("navigate") == [AgentMode.NAVIGATE]
    assert selected_modes("both") == [AgentMode.FULL_TEXT, AgentMode.NAVIGATE]


def test_the_lane_flag_expands_to_the_lanes_to_measure() -> None:
    assert [lane.name for lane in selected_lanes("original")] == [LANE_ORIGINAL]
    assert [lane.name for lane in selected_lanes("messy")] == [LANE_MESSY]
    assert [lane.name for lane in selected_lanes("all")] == [LANE_ORIGINAL, LANE_MESSY]


def test_the_summary_line_names_its_mode_lane_and_every_published_number() -> None:
    lane = _one_case_lane(catches=3, iterations=4)

    line = eval_summary_line(lane, run_date="2026-08-23", code_revision="abc1234")

    assert "mode=navigate" in line
    assert "lane=original" in line
    assert "catch_rate=75%" in line
    assert "runs=4" in line
    assert "cases_matching_manifest=0/1" in line


def test_the_pair_table_never_double_counts_a_shared_run() -> None:
    """Nine cases share one audit, so run-level numbers appear once, on the pair."""
    pair = _pair(FINDING_CASE, SECOND_FINDING_CASE, DECOY_CASE)
    lane = _lane(pairs=[(pair, [_outcome(unattributed_false_positives=2, rejected=1)])])

    table = render_pair_table(lane.pairs)

    assert len(lane.cases) == 3
    assert len(lane.pairs) == 1
    assert table.count("veylan.pdf") == 1
    assert lane.unattributed_false_positives == 2


def test_the_case_table_reports_only_what_each_case_owns() -> None:
    """A run's inventions belong to the audit, so no case row may claim them.

    Three cases share one audit that invented two findings. Putting those two
    in the case table would publish six inventions where the run produced two,
    which is the whole reason the run-level numbers live in their own table.
    """
    pair = _pair(FINDING_CASE, SECOND_FINDING_CASE, DECOY_CASE)
    lane = _lane(
        pairs=[
            (
                pair,
                [_outcome(unattributed_false_positives=2, decoy_hit_case_ids=("E-18",))],
            )
        ]
    )

    decoy_column = [
        int(row.split("|")[5].strip()) for row in render_case_table(lane.cases).splitlines()[2:]
    ]

    assert decoy_column == [0, 0, 1]
    assert sum(decoy_column) == lane.decoy_false_positives == 1
    assert lane.unattributed_false_positives == 2
    assert "| 2 |" not in "\n".join(render_case_table(lane.cases).splitlines()[2:])


def test_a_pair_result_reports_its_own_run_count() -> None:
    pair = PairResult(pair=_pair(FINDING_CASE), runs=[_outcome(), _outcome()])

    assert pair.iterations == 2
    assert pair.mean_model_tool_calls == 0.0


def test_current_code_revision_reads_this_repository() -> None:
    revision = current_code_revision()

    assert revision
    assert revision.split()[0] not in {"", "None"}


def test_a_clean_tree_is_named_by_its_commit_alone() -> None:
    assert format_code_revision("abc1234", "") == "abc1234"
    assert format_code_revision("abc1234", "\n") == "abc1234"


def test_uncommitted_changes_are_named_and_never_borrow_the_commit() -> None:
    """A dirty tree measured under a commit's name would misattribute the run."""
    named = format_code_revision("abc1234", " M specguard/agent.py\n M specguard/gate.py\n")

    assert named == "abc1234 plus uncommitted changes to specguard/agent.py, specguard/gate.py"


def test_the_harness_own_output_files_do_not_make_the_tree_dirty() -> None:
    """This script rewrites EVAL.md and README.md, so they are dirty every run."""
    status = " M EVAL.md\n M README.md\n"

    assert uncommitted_source_paths(status) == []
    assert format_code_revision("abc1234", status) == "abc1234"


def test_a_source_edit_beside_the_harness_output_still_counts() -> None:
    status = " M EVAL.md\n M specguard/tools.py\n?? scripts/new_probe.py\n"

    assert uncommitted_source_paths(status) == ["specguard/tools.py", "scripts/new_probe.py"]
    assert "specguard/tools.py" in format_code_revision("abc1234", status)


def test_a_renamed_source_file_is_named_by_its_new_path() -> None:
    status = "R  specguard/old.py -> specguard/new.py\n"

    assert uncommitted_source_paths(status) == ["specguard/new.py"]


def test_the_revision_git_reports_is_stripped_before_it_is_published() -> None:
    """git rev-parse ends its answer with a newline; the published field must not."""
    assert format_code_revision("abc1234\n", "") == "abc1234"
    assert format_code_revision("  abc1234  ", None) == "abc1234, working tree state unknown"


def test_an_unreadable_tree_state_is_written_as_unknown() -> None:
    assert format_code_revision("abc1234", None) == "abc1234, working tree state unknown"


def test_an_unreadable_commit_is_written_as_unrecorded() -> None:
    assert format_code_revision(None, "") == "not recorded for this run"
    assert format_code_revision("", "") == "not recorded for this run"


# --- 11. Rate-limit pacing, which changes no measured number --------------


def test_a_rate_limit_is_recognised_and_other_failures_are_not() -> None:
    assert is_rate_limited(RuntimeError("429 RESOURCE_EXHAUSTED")) is True
    assert is_rate_limited(RuntimeError("Resource exhausted, try later")) is False
    assert is_rate_limited(RuntimeError("RESOURCE_EXHAUSTED")) is True
    assert is_rate_limited(ValueError("the model returned an invalid quote")) is False
    assert is_rate_limited(FileNotFoundError("fixtures/missing.pdf")) is False


def test_a_rate_limited_audit_is_retried_after_a_recorded_wait() -> None:
    waits: list[float] = []
    attempts: list[int] = []

    async def sleep(seconds: float) -> None:
        waits.append(seconds)

    async def run_pair(pair: CasePair, **_: object) -> RunOutcome:
        attempts.append(1)
        if len(attempts) == 1:
            raise RuntimeError("429 RESOURCE_EXHAUSTED")
        return _outcome(run_id="run-after-wait")

    outcome = asyncio.run(
        run_pair_with_backoff(
            _pair(FINDING_CASE),
            project_id="test-project",
            output_directory=Path("artifacts"),
            agent_mode=AgentMode.NAVIGATE,
            sleep=sleep,
            run_pair=run_pair,
        )
    )

    assert outcome.run_id == "run-after-wait"
    assert waits == [RATE_LIMIT_BACKOFF_SECONDS[0]]
    assert len(attempts) == 2


def test_the_backoff_stops_rather_than_looping_forever() -> None:
    waits: list[float] = []
    attempts: list[int] = []

    async def sleep(seconds: float) -> None:
        waits.append(seconds)

    async def run_pair(pair: CasePair, **_: object) -> RunOutcome:
        attempts.append(1)
        raise RuntimeError("429 RESOURCE_EXHAUSTED")

    with pytest.raises(RuntimeError, match="429"):
        asyncio.run(
            run_pair_with_backoff(
                _pair(FINDING_CASE),
                project_id="test-project",
                output_directory=Path("artifacts"),
                agent_mode=AgentMode.NAVIGATE,
                sleep=sleep,
                run_pair=run_pair,
            )
        )

    assert waits == list(RATE_LIMIT_BACKOFF_SECONDS)
    assert len(attempts) == len(RATE_LIMIT_BACKOFF_SECONDS) + 1


def test_a_failure_that_is_not_a_rate_limit_is_raised_at_once() -> None:
    """A harness that swallowed this would publish a table with a silent hole."""
    waits: list[float] = []
    attempts: list[int] = []

    async def sleep(seconds: float) -> None:
        waits.append(seconds)

    async def run_pair(pair: CasePair, **_: object) -> RunOutcome:
        attempts.append(1)
        raise FileNotFoundError("fixtures/missing.pdf")

    with pytest.raises(FileNotFoundError):
        asyncio.run(
            run_pair_with_backoff(
                _pair(FINDING_CASE),
                project_id="test-project",
                output_directory=Path("artifacts"),
                agent_mode=AgentMode.NAVIGATE,
                sleep=sleep,
                run_pair=run_pair,
            )
        )

    assert waits == []
    assert len(attempts) == 1


# --- 12. The committed per-run tool-call receipts -------------------------


def _call(**overrides: object) -> ModelToolCall:
    values: dict[str, object] = {
        "turn_index": 0,
        "tool_name": "extract_pdf_text",
        "document_role": DocumentRole.SPECIFICATION,
        "page_number": 17,
    }
    values.update(overrides)
    return ModelToolCall(**values)  # type: ignore[arg-type]


def _outcome_with_calls(*calls: ModelToolCall, **overrides: object) -> RunOutcome:
    """Score one fake summary carrying these recorded calls, as the harness does."""
    summary = _summary(model_tool_calls=list(calls), **overrides)
    return build_run_outcome(_pair(FINDING_CASE), summary, [_finding(D01)], model_turns=1)


def _written_lines(path: Path) -> list[dict[str, object]]:
    """Read the receipts file back as the JSONL it claims to be."""
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _write(log: ReceiptLog, outcome: RunOutcome, *, iteration: int = 1) -> None:
    log.record_run(
        outcome,
        mode=AgentMode.NAVIGATE,
        lane=LANE_ORIGINAL,
        pair=_pair(FINDING_CASE),
        iteration=iteration,
    )


def test_the_receipts_flag_defaults_to_the_repository_root_file() -> None:
    """The receipts are committed provenance, so their home is not a temp path."""
    args = _parser().parse_args([])

    assert args.receipts_path == DEFAULT_RECEIPTS_PATH
    assert DEFAULT_RECEIPTS_PATH.name == RECEIPTS_FILE_NAME
    assert _parser().parse_args(["--receipts-path", "elsewhere.jsonl"]).receipts_path == Path(
        "elsewhere.jsonl"
    )


def test_every_recorded_tool_call_becomes_one_receipt_line(tmp_path: Path) -> None:
    outcome = _outcome_with_calls(
        _call(page_number=17),
        _call(turn_index=1, tool_name="verify_quote", page_number=9, response_verified=False),
    )
    log = ReceiptLog(tmp_path / RECEIPTS_FILE_NAME)
    log.start_campaign()

    _write(log, outcome, iteration=3)

    calls = [line for line in _written_lines(log.path) if line["record"] == RECEIPT_TOOL_CALL]
    assert [line["call_index"] for line in calls] == [0, 1]
    assert [line["turn_index"] for line in calls] == [0, 1]
    assert [line["tool_name"] for line in calls] == ["extract_pdf_text", "verify_quote"]
    assert [line["page_number"] for line in calls] == [17, 9]
    assert calls[1]["response_verified"] is False
    assert all(line["mode"] == "navigate" for line in calls)
    assert all(line["lane"] == LANE_ORIGINAL for line in calls)
    assert all(line["pair"] == "spec.pdf::veylan.pdf" for line in calls)
    assert all(line["iteration"] == 3 for line in calls)
    assert all(line["run_id"] == "run-1" for line in calls)


def test_the_receipt_reuses_the_runtime_bounding_and_adds_no_quote_text(tmp_path: Path) -> None:
    """The receipt says which quote was checked, never what the quote said."""
    digest = "a" * 64
    outcome = _outcome_with_calls(_call(tool_name="verify_quote", quote_sha256=digest))
    log = ReceiptLog(tmp_path / RECEIPTS_FILE_NAME)
    log.start_campaign()

    _write(log, outcome)

    call = _written_lines(log.path)[0]
    assert call["quote_sha256"] == digest
    assert call["document_role"] == "specification"
    assert "quote" not in call
    assert "text" not in call


def test_the_per_tool_counts_sum_to_the_total_the_run_line_reports(tmp_path: Path) -> None:
    outcome = _outcome_with_calls(
        _call(),
        _call(turn_index=1),
        _call(turn_index=1, tool_name="verify_quote"),
        self_check_rejections=2,
        self_check_rejected_quote_returned=True,
    )
    log = ReceiptLog(tmp_path / RECEIPTS_FILE_NAME)
    log.start_campaign()

    _write(log, outcome)

    run_line = _written_lines(log.path)[-1]
    counts: dict[str, int] = run_line["tool_name_counts"]  # type: ignore[assignment]
    assert run_line["record"] == RECEIPT_RUN
    assert counts == {"extract_pdf_text": 2, "verify_quote": 1}
    assert sum(counts.values()) == run_line["model_tool_calls"] == 3
    assert run_line["self_check_rejections"] == 2
    assert run_line["self_check_rejected_quote_returned"] is True


def test_a_run_that_called_nothing_still_writes_its_summary_line(tmp_path: Path) -> None:
    """A run absent from the file and a run that called nothing must not read alike."""
    log = ReceiptLog(tmp_path / RECEIPTS_FILE_NAME)
    log.start_campaign()

    _write(log, _outcome_with_calls())

    lines = _written_lines(log.path)
    assert [line["record"] for line in lines] == [RECEIPT_RUN]
    assert lines[0]["model_tool_calls"] == 0
    assert lines[0]["tool_name_counts"] == {}


def test_each_run_appends_to_what_the_run_before_it_wrote(tmp_path: Path) -> None:
    log = ReceiptLog(tmp_path / RECEIPTS_FILE_NAME)
    log.start_campaign()

    _write(log, _outcome_with_calls(_call()), iteration=1)
    _write(log, _outcome_with_calls(), iteration=2)

    lines = _written_lines(log.path)
    assert [line["record"] for line in lines] == [
        RECEIPT_TOOL_CALL,
        RECEIPT_RUN,
        RECEIPT_RUN,
    ]
    assert [line["iteration"] for line in lines] == [1, 1, 2]


def test_a_new_campaign_truncates_the_file_rather_than_appending(tmp_path: Path) -> None:
    """One file describes one invocation, or the tables above it describe nothing."""
    log = ReceiptLog(tmp_path / RECEIPTS_FILE_NAME)
    log.start_campaign()
    _write(log, _outcome_with_calls(_call()))

    log.start_campaign()

    assert _written_lines(log.path) == []
    _write(log, _outcome_with_calls())
    assert len(_written_lines(log.path)) == 1


def test_the_run_identifier_on_every_line_is_the_one_the_runtime_assigned() -> None:
    outcome = _outcome_with_calls(_call(), run_id="run-from-the-runtime")

    records = receipt_records(
        outcome,
        mode=AgentMode.FULL_TEXT,
        lane=LANE_MESSY,
        pair=_pair(FINDING_CASE),
        iteration=5,
    )

    assert [record["run_id"] for record in records] == ["run-from-the-runtime"] * 2
    assert [record["mode"] for record in records] == ["full_text", "full_text"]
    assert [record["lane"] for record in records] == [LANE_MESSY, LANE_MESSY]


def test_the_eval_method_text_names_the_committed_receipts_file() -> None:
    assert f"committed to `{RECEIPTS_FILE_NAME}`" in _document()
    assert "one JSON line per call" in _document()
    assert "checked call by call" in _document()


def test_the_receipts_file_this_harness_writes_does_not_make_the_tree_dirty() -> None:
    """It is rewritten mid-campaign, so counting it would dirty every measurement."""
    status = f" M EVAL.md\n M README.md\n M {RECEIPTS_FILE_NAME}\n"

    assert uncommitted_source_paths(status) == []
    assert format_code_revision("abc1234", status) == "abc1234"
