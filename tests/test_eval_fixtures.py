"""Aggregation and publication logic of the measured eval harness, driven by fakes.

No test here touches Vertex, Firestore, or the network. The real run is a
receipted script execution recorded in EVAL.md and HANDOFF.md.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts.eval_fixtures import (
    README_TABLE_END,
    README_TABLE_START,
    CaseResult,
    EvalCase,
    EvidencePair,
    RunOutcome,
    aggregate,
    build_run_outcome,
    compare_to_previous,
    current_code_revision,
    format_code_revision,
    load_eval_cases,
    load_evidence_pairs,
    overall_catch_rate,
    read_previous_readme_section,
    render_eval_markdown,
    render_readme_section,
    render_results_table,
    write_readme_section,
)
from specguard.models import AuditRunSummary, DocumentRole, QuarantinedDocument, RunQuarantine

MANIFEST_PATH = Path(__file__).parents[1] / "fixtures" / "MANIFEST.md"

D01 = EvidencePair(
    id="D-01",
    spec_page=3,
    spec_quote="Provide a 480V, 3-phase distribution switchboard for service distribution.",
    cut_sheet_page=1,
    cut_sheet_quote="Nominal system: 208V, 3-phase, 4-wire.",
)
FINDING_CASE = EvalCase(
    id="E-02",
    spec_pdf="spec.pdf",
    cut_sheet_pdf="veylan.pdf",
    expected_outcome="finding",
    evidence=D01,
)
COMPLIANT_CASE = EvalCase(
    id="E-01",
    spec_pdf="spec.pdf",
    cut_sheet_pdf="caldra.pdf",
    expected_outcome="no_finding",
    evidence=None,
)
QUARANTINE_CASE = EvalCase(
    id="E-04",
    spec_pdf="spec.pdf",
    cut_sheet_pdf="altered.pdf",
    expected_outcome="quarantine",
    evidence=None,
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


def _expected_finding(severity: str = "high") -> dict[str, object]:
    return {
        "spec_quote": {"text": D01.spec_quote, "page_number": D01.spec_page},
        "cut_sheet_quote": {"text": D01.cut_sheet_quote, "page_number": D01.cut_sheet_page},
        "severity": severity,
    }


def _outcome(case: EvalCase, **overrides: object) -> RunOutcome:
    values: dict[str, object] = {
        "run_id": "run-1",
        "quarantined": False,
        "model_calls": 1,
        "claims_made": 1,
        "rejected": 0,
        "retried": 0,
        "findings_persisted": 1,
        "caught_expected_pair": case.expected_outcome == "finding",
        "false_positives": 0,
        "severities": ("high",),
    }
    values.update(overrides)
    return RunOutcome(**values)  # type: ignore[arg-type]


def test_the_committed_manifest_declares_four_bound_audit_cases() -> None:
    """The harness reads its expectations from the manifest, not from itself."""
    cases = load_eval_cases(MANIFEST_PATH.read_text(encoding="utf-8"))

    assert [case.id for case in cases] == ["E-01", "E-02", "E-03", "E-04"]
    assert [case.expected_outcome for case in cases] == [
        "no_finding",
        "finding",
        "finding",
        "quarantine",
    ]
    veylan = next(case for case in cases if case.id == "E-02")
    assert veylan.evidence is not None
    assert veylan.evidence.id == "D-01"
    assert veylan.evidence.spec_page == 3
    assert veylan.cut_sheet_path.is_file()
    assert veylan.spec_path.is_file()


def test_manifest_evidence_pairs_are_read_from_the_existing_block() -> None:
    """The quote a run must reproduce is recorded once and read twice."""
    pairs = load_evidence_pairs(MANIFEST_PATH.read_text(encoding="utf-8"))

    assert sorted(pairs) == ["D-01", "D-02"]
    assert pairs["D-02"].cut_sheet_quote == "Field conductor termination rating: 158 deg F."


def test_a_finding_case_without_evidence_is_refused() -> None:
    """A case cannot claim a catch rate with no pair to catch."""
    manifest = (
        "<!-- fixture-evidence\n[]\n-->\n"
        '<!-- eval-cases\n[{"id": "E-09", "spec_pdf": "a.pdf", "cut_sheet_pdf": "b.pdf", '
        '"expected_outcome": "finding", "evidence_id": null}]\n-->'
    )
    with pytest.raises(ValueError, match="names no evidence pair"):
        load_eval_cases(manifest)


def test_an_exact_evidence_match_counts_as_a_catch() -> None:
    """Both quotes and both page numbers must equal the manifest pair."""
    outcome = build_run_outcome(FINDING_CASE, _summary(), [_expected_finding()], model_calls=1)

    assert outcome.caught_expected_pair is True
    assert outcome.false_positives == 0
    assert outcome.severities == ("high",)


def test_a_near_miss_is_not_a_catch_and_is_a_false_positive() -> None:
    """A right quote on the wrong page must not be scored as a catch."""
    near_miss = _expected_finding()
    near_miss["spec_quote"] = {"text": D01.spec_quote, "page_number": D01.spec_page + 1}

    outcome = build_run_outcome(FINDING_CASE, _summary(), [near_miss], model_calls=1)

    assert outcome.caught_expected_pair is False
    assert outcome.false_positives == 1


def test_every_persisted_finding_on_the_compliant_case_is_a_false_positive() -> None:
    """The compliant cut sheet has nothing planted, so any finding is a miss."""
    outcome = build_run_outcome(
        COMPLIANT_CASE, _summary(), [_expected_finding(), _expected_finding()], model_calls=1
    )

    assert outcome.caught_expected_pair is False
    assert outcome.false_positives == 2


def test_a_quarantined_run_reports_no_model_call_and_no_finding() -> None:
    """The altered fixture must stop before the model and persist nothing."""
    quarantine = RunQuarantine(
        reason="text_layer_integrity_screen",
        documents=[
            QuarantinedDocument(
                document_role=DocumentRole.SUBMITTED_DOCUMENT,
                document_sha256="ab" * 32,
                page_count=2,
                flagged_pages=[1],
                hidden_span_count=2,
            )
        ],
    )
    summary = _summary(claims_made=0, findings_persisted=0, rfi_path=None, quarantine=quarantine)

    outcome = build_run_outcome(QUARANTINE_CASE, summary, [], model_calls=0)

    assert outcome.quarantined is True
    assert outcome.model_calls == 0
    assert outcome.findings_persisted == 0
    assert outcome.false_positives == 0


def test_catch_rate_is_the_measured_fraction_not_a_rounded_claim() -> None:
    """Three catches in five runs publishes as 60 percent."""
    runs = [_outcome(FINDING_CASE, caught_expected_pair=index < 3) for index in range(5)]
    result = CaseResult(case=FINDING_CASE, runs=runs)

    assert result.catches == 3
    assert result.catch_rate == pytest.approx(0.6)
    assert result.meets_expectation is False
    assert "60%" in render_results_table([result])


def test_a_case_with_nothing_planted_reports_no_catch_rate() -> None:
    """An unmeasured case must not inflate the published average."""
    compliant = CaseResult(
        case=COMPLIANT_CASE,
        runs=[
            _outcome(
                COMPLIANT_CASE, caught_expected_pair=False, findings_persisted=0, severities=()
            )
            for _ in range(5)
        ],
    )
    caught = CaseResult(case=FINDING_CASE, runs=[_outcome(FINDING_CASE) for _ in range(5)])

    assert compliant.catch_rate is None
    assert "n/a" in render_results_table([compliant])
    assert overall_catch_rate([compliant, caught]) == pytest.approx(1.0)


def test_the_overall_catch_rate_weights_every_run_equally() -> None:
    """Two cases, one perfect and one at 40 percent, average to 70 percent."""
    perfect = CaseResult(case=FINDING_CASE, runs=[_outcome(FINDING_CASE) for _ in range(5)])
    partial_case = EvalCase(
        id="E-03",
        spec_pdf="spec.pdf",
        cut_sheet_pdf="torven.pdf",
        expected_outcome="finding",
        evidence=D01,
    )
    partial = CaseResult(
        case=partial_case,
        runs=[_outcome(partial_case, caught_expected_pair=index < 2) for index in range(5)],
    )

    assert overall_catch_rate([perfect, partial]) == pytest.approx(0.7)


def test_the_quarantine_case_only_passes_with_zero_model_calls() -> None:
    """A quarantine that still called the model has not met the expectation."""
    clean = CaseResult(
        case=QUARANTINE_CASE,
        runs=[
            _outcome(
                QUARANTINE_CASE,
                quarantined=True,
                model_calls=0,
                findings_persisted=0,
                caught_expected_pair=False,
                severities=(),
            )
            for _ in range(5)
        ],
    )
    leaked = CaseResult(
        case=QUARANTINE_CASE,
        runs=[
            _outcome(
                QUARANTINE_CASE,
                quarantined=True,
                model_calls=1,
                findings_persisted=0,
                caught_expected_pair=False,
                severities=(),
            )
            for _ in range(5)
        ],
    )

    assert clean.quarantine_rate == pytest.approx(1.0)
    assert clean.meets_expectation is True
    assert leaked.meets_expectation is False


def test_severity_distribution_counts_labels_across_every_run() -> None:
    """The distribution is per case, over every persisted finding of that case."""
    runs = [
        _outcome(FINDING_CASE, severities=("high",)),
        _outcome(FINDING_CASE, severities=("high", "medium")),
        _outcome(FINDING_CASE, severities=("unclassified",)),
    ]
    result = CaseResult(case=FINDING_CASE, runs=runs)

    assert result.severity_distribution == {"high": 2, "medium": 1, "unclassified": 1}
    assert "high 2, medium 1, unclassified 1" in render_results_table([result])


def test_aggregate_keeps_manifest_order_and_survives_a_case_with_no_runs() -> None:
    """The published table lists cases in the order the manifest declares them."""
    results = aggregate(
        [COMPLIANT_CASE, FINDING_CASE, QUARANTINE_CASE],
        {"E-02": [_outcome(FINDING_CASE)]},
    )

    assert [result.case.id for result in results] == ["E-01", "E-02", "E-04"]
    assert results[0].iterations == 0
    assert results[0].catch_rate is None
    assert results[0].meets_expectation is False


def test_eval_markdown_publishes_a_short_catch_rate_as_measured() -> None:
    """A miss is named in the document, not smoothed away."""
    partial = CaseResult(
        case=FINDING_CASE,
        runs=[_outcome(FINDING_CASE, caught_expected_pair=index < 2) for index in range(5)],
    )

    document = render_eval_markdown(
        [partial],
        iterations=5,
        run_date="2026-08-21",
        model_id="gemini-3.7-flash",
        severity_model_id="`gemma-3-1b-it` on a Vertex AI endpoint",
        vertex_spend="not visible in the run output",
    )

    assert "- Date: 2026-08-21" in document
    assert "- Iterations per case: 5" in document
    assert "`gemini-3.7-flash`" in document
    assert "gemma-3-1b-it" in document
    assert "not visible in the run output" in document
    assert "**40%**" in document
    assert "## Cases that did not match the manifest" in document
    assert "2 of 5 runs caught the expected pair" in document
    assert "published as measured" in document


def test_eval_markdown_states_plainly_when_every_case_matched() -> None:
    """A clean sheet says so once, without decorating the result."""
    document = render_eval_markdown(
        [CaseResult(case=FINDING_CASE, runs=[_outcome(FINDING_CASE) for _ in range(5)])],
        iterations=5,
        run_date="2026-08-21",
        model_id="gemini-3.7-flash",
        severity_model_id="no endpoint configured for this run",
        vertex_spend="not visible in the run output",
    )

    assert "**100%**" in document
    assert "None. Every case matched its declared expected outcome in every run." in document
    assert "not a compliance determination" in document


def test_the_readme_section_says_so_when_the_catch_rate_is_short() -> None:
    """The README must not describe the runtime as catching everything."""
    partial = CaseResult(
        case=FINDING_CASE,
        runs=[_outcome(FINDING_CASE, caught_expected_pair=index < 2) for index in range(5)],
    )

    section = render_readme_section([partial], iterations=5, run_date="2026-08-21")

    assert section.startswith(README_TABLE_START)
    assert section.endswith(README_TABLE_END)
    assert "40%" in section
    assert "does not catch every planted discrepancy on every run" in section
    assert "EVAL.md" in section


def test_write_readme_section_replaces_only_the_marked_block() -> None:
    """Publication is idempotent and never disturbs the rest of the README."""
    readme = f"before\n\n{README_TABLE_START}\nold\n{README_TABLE_END}\n\nafter\n"
    section = render_readme_section(
        [CaseResult(case=FINDING_CASE, runs=[_outcome(FINDING_CASE)])],
        iterations=1,
        run_date="2026-08-21",
    )

    written = write_readme_section(readme, section)
    rewritten = write_readme_section(written, section)

    assert written.startswith("before\n\n")
    assert written.endswith("\n\nafter\n")
    assert "old" not in written
    assert rewritten == written


def test_write_readme_section_refuses_a_readme_with_no_markers() -> None:
    """Silently appending a table to the wrong place is worse than failing."""
    with pytest.raises(ValueError, match="eval-table markers"):
        write_readme_section("no markers here", "section")


def test_the_committed_readme_carries_the_publication_markers() -> None:
    """The harness can publish into the committed README without editing it first."""
    readme = (Path(__file__).parents[1] / "README.md").read_text(encoding="utf-8")

    assert readme.count(README_TABLE_START) == 1
    assert readme.count(README_TABLE_END) == 1


def test_eval_markdown_says_when_the_retry_loop_never_fired() -> None:
    """A clean table must not read as evidence that the retry loop works."""
    result = CaseResult(case=FINDING_CASE, runs=[_outcome(FINDING_CASE) for _ in range(5)])

    document = render_eval_markdown(
        [result],
        iterations=5,
        run_date="2026-08-21",
        model_id="gemini-3.7-flash",
        severity_model_id="an endpoint",
        vertex_spend="not visible in the run output",
    )

    assert "## What this run did not exercise" in document
    assert "rejection-and-retry loop did not fire" in document
    assert "not evidence that the loop works" in document
    assert "covered by the test suite" in document


def test_eval_markdown_reports_a_retry_loop_that_did_fire() -> None:
    """When the gate rejected a claim, the document says so instead."""
    result = CaseResult(
        case=FINDING_CASE,
        runs=[_outcome(FINDING_CASE, rejected=1, retried=2) for _ in range(2)],
    )

    document = render_eval_markdown(
        [result],
        iterations=2,
        run_date="2026-08-21",
        model_id="gemini-3.7-flash",
        severity_model_id="an endpoint",
        vertex_spend="not visible in the run output",
    )

    assert "rejected 2 claims and the runtime retried 4" in document
    assert "did fire in this run" in document


def test_eval_markdown_names_every_severity_fallback() -> None:
    """An unclassified finding is a recorded fallback, not a missing number."""
    result = CaseResult(
        case=FINDING_CASE,
        runs=[
            _outcome(FINDING_CASE, severities=("high",)),
            _outcome(FINDING_CASE, severities=("unclassified",)),
        ],
    )

    document = render_eval_markdown(
        [result],
        iterations=2,
        run_date="2026-08-21",
        model_id="gemini-3.7-flash",
        severity_model_id="an endpoint",
        vertex_spend="not visible in the run output",
    )

    assert "1 of 2 persisted findings carry `unclassified` severity" in document
    assert "never blocks an audit" in document


def test_eval_markdown_omits_the_fallback_note_when_every_finding_was_classified() -> None:
    """No fallback, no note: the document reports only what happened."""
    result = CaseResult(case=FINDING_CASE, runs=[_outcome(FINDING_CASE) for _ in range(3)])

    document = render_eval_markdown(
        [result],
        iterations=3,
        run_date="2026-08-21",
        model_id="gemini-3.7-flash",
        severity_model_id="an endpoint",
        vertex_spend="not visible in the run output",
    )

    assert "unclassified` severity" not in document


def test_a_short_catch_rate_never_publishes_as_one_hundred_percent() -> None:
    """199 catches in 200 runs must not render as the number reserved for exact."""
    runs = [_outcome(FINDING_CASE, caught_expected_pair=index != 0) for index in range(200)]
    result = CaseResult(case=FINDING_CASE, runs=runs)

    assert result.catch_rate == pytest.approx(0.995)
    assert "100%" not in render_results_table([result]).split("|")[4]
    assert "99.5%" in render_results_table([result])


def test_a_rate_that_still_rounds_to_complete_publishes_as_under_one_hundred() -> None:
    """Even one decimal can read as complete, so the renderer falls back again."""
    runs = [_outcome(FINDING_CASE, caught_expected_pair=index != 0) for index in range(5000)]
    result = CaseResult(case=FINDING_CASE, runs=runs)

    assert "<100%" in render_results_table([result])


def test_an_exact_rate_still_publishes_as_one_hundred_percent() -> None:
    """The guard must not spoil a genuinely complete result."""
    result = CaseResult(case=FINDING_CASE, runs=[_outcome(FINDING_CASE) for _ in range(5)])

    assert "100%" in render_results_table([result])
    assert "<100%" not in render_results_table([result])


def test_a_run_with_no_usable_model_turn_fails_the_compliant_case() -> None:
    """Persisting nothing because the model broke is not a clean pass."""
    broken = build_run_outcome(
        COMPLIANT_CASE,
        _summary(claims_made=0, rejected=1, findings_persisted=0),
        [],
        model_calls=1,
    )
    result = CaseResult(case=COMPLIANT_CASE, runs=[broken])

    assert broken.model_output_invalid is True
    assert result.unusable_runs == 1
    assert result.meets_expectation is False


def test_a_compliant_case_that_never_reached_the_model_fails() -> None:
    """A no_finding pass requires that the model actually read the documents."""
    never_ran = _outcome(
        COMPLIANT_CASE,
        caught_expected_pair=False,
        findings_persisted=0,
        severities=(),
        model_calls=0,
    )
    result = CaseResult(case=COMPLIANT_CASE, runs=[never_ran])

    assert result.meets_expectation is False


def test_a_quarantined_run_that_persisted_a_finding_fails() -> None:
    """A quarantine that still wrote to the ledger is not a clean quarantine."""
    inconsistent = _outcome(
        QUARANTINE_CASE,
        quarantined=True,
        model_calls=0,
        findings_persisted=1,
        caught_expected_pair=False,
        false_positives=1,
        severities=("high",),
    )
    result = CaseResult(case=QUARANTINE_CASE, runs=[inconsistent])

    assert result.quarantine_rate == pytest.approx(1.0)
    assert result.meets_expectation is False


def test_the_header_publishes_the_severity_model_the_runs_recorded() -> None:
    """The operator's description is labelled as theirs, not as a measurement."""
    measured = _outcome(FINDING_CASE, severity_model_ids=("google-gemma3-gemma-3-1b-it",))
    result = CaseResult(case=FINDING_CASE, runs=[measured])

    document = render_eval_markdown(
        [result],
        iterations=1,
        run_date="2026-08-21",
        model_id="gemini-3.7-flash",
        severity_model_id="an operator-supplied string",
        vertex_spend="not visible in the run output",
    )

    assert (
        "Severity model, measured from the persisted findings: "
        "`google-gemma3-gemma-3-1b-it`" in document
    )
    assert "Severity endpoint, as the operator named it: an operator-supplied string" in document


def test_the_header_says_so_when_no_severity_model_was_recorded() -> None:
    """No recorded model id means the header must not imply one ran."""
    result = CaseResult(case=FINDING_CASE, runs=[_outcome(FINDING_CASE, severity_model_ids=())])

    document = render_eval_markdown(
        [result],
        iterations=1,
        run_date="2026-08-21",
        model_id="gemini-3.7-flash",
        severity_model_id="an operator-supplied string",
        vertex_spend="not visible in the run output",
    )

    assert "none recorded on any persisted finding" in document


def test_eval_markdown_lists_every_run_identifier_as_a_receipt() -> None:
    """The document must let a reader check the table against Firestore."""
    result = CaseResult(
        case=FINDING_CASE,
        runs=[_outcome(FINDING_CASE, run_id="run-aaa"), _outcome(FINDING_CASE, run_id="run-bbb")],
    )

    document = render_eval_markdown(
        [result],
        iterations=2,
        run_date="2026-08-21",
        model_id="gemini-3.7-flash",
        severity_model_id="an endpoint",
        vertex_spend="not visible in the run output",
    )

    assert "## Run identifiers" in document
    assert "`run-aaa`, `run-bbb`" in document


def test_eval_markdown_names_unusable_runs() -> None:
    """A broken run is reported, not silently absorbed into the counters."""
    broken = build_run_outcome(
        FINDING_CASE, _summary(claims_made=0, rejected=1, findings_persisted=0), [], model_calls=1
    )
    result = CaseResult(case=FINDING_CASE, runs=[broken])

    document = render_eval_markdown(
        [result],
        iterations=1,
        run_date="2026-08-21",
        model_id="gemini-3.7-flash",
        severity_model_id="an endpoint",
        vertex_spend="not visible in the run output",
    )

    assert "1 runs produced no usable model turn" in document
    assert "## Cases that did not match the manifest" in document


# --- Phase 6e: the code revision and the change-from-previous statement ---


def _one_case_results() -> list[CaseResult]:
    return aggregate([FINDING_CASE], {"E-02": [_outcome(FINDING_CASE)]})


def _readme_with(section: str) -> str:
    return f"# SpecGuard\n\n{section}\n\nAfter the block.\n"


def test_the_eval_header_names_the_code_revision_the_numbers_describe() -> None:
    document = render_eval_markdown(
        _one_case_results(),
        iterations=1,
        run_date="2026-08-22",
        model_id="gemini-3.7-flash",
        severity_model_id="none",
        vertex_spend="unavailable",
        code_revision="abc1234",
    )

    assert "- Code revision these numbers describe: `abc1234`" in document


def test_the_readme_block_names_the_code_revision() -> None:
    section = render_readme_section(
        _one_case_results(), iterations=1, run_date="2026-08-22", code_revision="abc1234"
    )

    assert "on code revision `abc1234`" in section


def test_an_unreadable_revision_is_recorded_as_unrecorded() -> None:
    document = render_eval_markdown(
        _one_case_results(),
        iterations=1,
        run_date="2026-08-22",
        model_id="gemini-3.7-flash",
        severity_model_id="none",
        vertex_spend="unavailable",
    )

    assert "- Code revision these numbers describe: `not recorded for this run`" in document


def test_current_code_revision_reads_this_repository() -> None:
    revision = current_code_revision()

    assert revision
    assert revision.split()[0] not in {"", "None"}


def test_a_clean_tree_is_named_by_its_commit_alone() -> None:
    assert format_code_revision("abc1234", "") == "abc1234"
    assert format_code_revision("abc1234", "\n") == "abc1234"


def test_uncommitted_changes_are_named_and_never_borrow_the_commit() -> None:
    """A dirty tree measured under a commit's name would misattribute the run."""
    named = format_code_revision("abc1234", " M specguard/agent.py\n")

    assert named == "abc1234 plus uncommitted changes"


def test_an_unreadable_tree_state_is_written_as_unknown() -> None:
    assert format_code_revision("abc1234", None) == "abc1234, working tree state unknown"


def test_an_unreadable_commit_is_written_as_unrecorded() -> None:
    assert format_code_revision(None, "") == "not recorded for this run"
    assert format_code_revision("", "") == "not recorded for this run"


def test_the_previous_published_table_is_read_back_from_the_readme() -> None:
    section = render_readme_section(
        _one_case_results(), iterations=1, run_date="2026-08-21", code_revision="old1234"
    )

    previous = read_previous_readme_section(_readme_with(section))

    assert previous is not None
    assert previous.run_date == "2026-08-21"
    assert "E-02" in previous.rows


def test_an_unchanged_table_says_no_number_moved() -> None:
    results = _one_case_results()
    section = render_readme_section(
        results, iterations=1, run_date="2026-08-21", code_revision="old1234"
    )
    previous = read_previous_readme_section(_readme_with(section))

    assert "No number in this table moved from the 2026-08-21 run." in compare_to_previous(
        results, previous
    )


def test_a_changed_number_is_named_plainly() -> None:
    before = aggregate([FINDING_CASE], {"E-02": [_outcome(FINDING_CASE)]})
    section = render_readme_section(
        before, iterations=1, run_date="2026-08-21", code_revision="old1234"
    )
    previous = read_previous_readme_section(_readme_with(section))
    after = aggregate(
        [FINDING_CASE],
        {"E-02": [_outcome(FINDING_CASE), _outcome(FINDING_CASE, caught_expected_pair=False)]},
    )

    statement = compare_to_previous(after, previous)

    assert "Numbers moved from the 2026-08-21 run." in statement
    assert "`E-02`" in statement
    assert "superseded, not corrected" in statement


def test_the_readme_block_carries_the_comparison_statement() -> None:
    results = _one_case_results()
    section = render_readme_section(
        results, iterations=1, run_date="2026-08-21", code_revision="old1234"
    )
    previous = read_previous_readme_section(_readme_with(section))

    republished = render_readme_section(
        results,
        iterations=1,
        run_date="2026-08-22",
        code_revision="new1234",
        previous=previous,
    )

    assert "No number in this table moved from the 2026-08-21 run." in republished
    assert republished.startswith(README_TABLE_START)
    assert republished.endswith(README_TABLE_END)


def test_the_eval_document_carries_the_comparison_statement() -> None:
    results = _one_case_results()
    section = render_readme_section(
        results, iterations=1, run_date="2026-08-21", code_revision="old1234"
    )
    previous = read_previous_readme_section(_readme_with(section))

    document = render_eval_markdown(
        results,
        iterations=1,
        run_date="2026-08-22",
        model_id="gemini-3.7-flash",
        severity_model_id="none",
        vertex_spend="unavailable",
        code_revision="new1234",
        previous=previous,
    )

    assert "## Change from the previous published run" in document
    assert "No number in this table moved from the 2026-08-21 run." in document


def test_a_readme_with_no_earlier_block_says_there_is_nothing_to_compare() -> None:
    assert read_previous_readme_section("# SpecGuard\n\nNo markers here.\n") is None
    assert "No earlier measured table" in compare_to_previous(_one_case_results(), None)
