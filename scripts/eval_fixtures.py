"""Measure the real SpecGuard runtime against the committed fixture manifest.

The harness runs the deployed model path N times over every audit case the
fixture manifest declares, then writes EVAL.md and refreshes the eval table in
README.md. It publishes what it measured. A catch rate below 100 percent is
written as measured, never rounded up and never dropped.

Definitions used in every number below:

- A run **catches** a planted discrepancy when it persists a finding that
  carries the same evidence as the pair the manifest records for that case:
  each side cites the page the manifest records, and the persisted quote and
  the manifest quote contain one another in either direction once both are
  normalized by the verification gate's own rule. A longer or shorter span of
  the same passage on the cited page is the same evidence. A quote that only
  overlaps the planted passage is not, and the planted words on another page
  are not. The same rule decides a decoy match, so a span variant of a decoy
  still counts as a decoy hit rather than as an invention.
- A **decoy false positive** is a persisted finding that reproduces one of the
  compliant near-match pairs the manifest records as decoys. It is counted
  separately from an invented finding because the two errors are different: one
  reads a wording difference as a conflict, the other matches nothing planted.
- An **unattributed false positive** is a persisted finding that matches neither
  a planted pair nor a decoy pair. It belongs to the audit, not to any one case,
  and is reported in the per-run table.
- The **quarantine rate** is the fraction of runs the integrity screen stopped
  before any model call.
- **Rejections** and **retries** are the runtime counters, summed over the runs
  of a document pair. A rejection is a claim the verification gate refused.
- A **model turn** is one call to the claim generator. A **model tool call** is
  one function call the model itself initiated inside a turn.

One audit runs per document pair per iteration, and every case declared against
that pair is scored from that one run. A pair carrying seven planted
discrepancies is audited once per iteration, not seven times, because one audit
is what a reviewer would actually run.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import time
import uuid
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from specguard.agent import MODEL_ID
from specguard.gate import contains_on_boundaries, normalize
from specguard.models import AgentMode, AuditRunSummary

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_DIRECTORY = REPOSITORY_ROOT / "fixtures"
MANIFEST_PATH = FIXTURE_DIRECTORY / "MANIFEST.md"
EVAL_PATH = REPOSITORY_ROOT / "EVAL.md"
README_PATH = REPOSITORY_ROOT / "README.md"
DEFAULT_PROJECT = "specguard-hack"
DEFAULT_ITERATIONS = 5
DEFAULT_OUTPUT_DIRECTORY = REPOSITORY_ROOT / "artifacts" / "eval"
README_TABLE_START = "<!-- eval-table-start -->"
README_TABLE_END = "<!-- eval-table-end -->"
MEASURED_ON_PATTERN = re.compile(r"Measured on (\d{4}-\d{2}-\d{2}) by")
TABLE_ROW_PATTERN = re.compile(r"^\| `(E-\d+)` \|(.*)\|\s*$", re.MULTILINE)
UNRECORDED_REVISION = "not recorded for this run"

#: The two files this harness rewrites. Edits to them cannot change what the
#: runtime did, and they are always dirty on a second run, so they are excluded
#: from the working-tree check that names the measured code.
HARNESS_OUTPUTS = frozenset({"EVAL.md", "README.md"})

#: How long to wait before retrying one audit the model provider rate-limited,
#: and how many times. A rate limit is an infrastructure condition, not a model
#: outcome: the retried audit is a fresh audit, measured exactly like any other,
#: and every wait is printed so the log shows what happened. After the last
#: wait the harness stops rather than looping.
RATE_LIMIT_BACKOFF_SECONDS = (90, 300)

OUTCOME_FINDING = "finding"
OUTCOME_NO_FINDING = "no_finding"
OUTCOME_QUARANTINE = "quarantine"
NOT_APPLICABLE = "n/a"

LANE_ORIGINAL = "original"
LANE_MESSY = "messy"
LANE_CHOICES = (LANE_ORIGINAL, LANE_MESSY, "all")
MODE_CHOICES = (AgentMode.FULL_TEXT.value, AgentMode.NAVIGATE.value, "both")


@dataclass(frozen=True)
class Lane:
    """One evaluation lane and the manifest blocks that declare it."""

    name: str
    evidence_block: str
    cases_block: str
    decoy_block: str | None


LANES: dict[str, Lane] = {
    LANE_ORIGINAL: Lane(
        name=LANE_ORIGINAL,
        evidence_block="fixture-evidence",
        cases_block="eval-cases",
        decoy_block=None,
    ),
    LANE_MESSY: Lane(
        name=LANE_MESSY,
        evidence_block="fixture-evidence-messy",
        cases_block="eval-cases-messy",
        decoy_block="decoy-evidence-messy",
    ),
}


@dataclass(frozen=True)
class EvidencePair:
    """One planted discrepancy or one compliant decoy, as the manifest records it."""

    id: str
    spec_page: int
    spec_quote: str
    cut_sheet_page: int
    cut_sheet_quote: str


@dataclass(frozen=True)
class EvalCase:
    """One audit case: a submitted document, its expected outcome, its evidence."""

    id: str
    spec_pdf: str
    cut_sheet_pdf: str
    expected_outcome: str
    evidence: EvidencePair | None
    decoy: EvidencePair | None = None

    @property
    def spec_path(self) -> Path:
        return FIXTURE_DIRECTORY / self.spec_pdf

    @property
    def cut_sheet_path(self) -> Path:
        return FIXTURE_DIRECTORY / self.cut_sheet_pdf

    @property
    def pair_key(self) -> tuple[str, str]:
        return (self.spec_pdf, self.cut_sheet_pdf)


@dataclass(frozen=True)
class CasePair:
    """Every case declared against one specification and one submitted document."""

    spec_pdf: str
    cut_sheet_pdf: str
    cases: tuple[EvalCase, ...]

    @property
    def spec_path(self) -> Path:
        return FIXTURE_DIRECTORY / self.spec_pdf

    @property
    def cut_sheet_path(self) -> Path:
        return FIXTURE_DIRECTORY / self.cut_sheet_pdf

    @property
    def key(self) -> tuple[str, str]:
        return (self.spec_pdf, self.cut_sheet_pdf)


@dataclass(frozen=True)
class RunOutcome:
    """What one real audit of one document pair produced."""

    run_id: str
    quarantined: bool
    model_turns: int
    claims_made: int
    rejected: int
    retried: int
    findings_persisted: int
    caught_case_ids: frozenset[str]
    decoy_hit_case_ids: tuple[str, ...]
    unattributed_false_positives: int
    severities: tuple[str, ...]
    case_severities: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    severity_status: str | None = None
    model_output_invalid: bool = False
    severity_model_ids: tuple[str, ...] = ()
    prompt_tokens: int | None = None
    model_tool_calls: int = 0
    self_check_rejections: int = 0
    self_check_rejected_quote_returned: bool = False


@dataclass
class CaseResult:
    """The aggregate of every run of the pair this case is declared against.

    Every case of one pair shares that pair's runs. Only the per-case numbers
    below are attributed to this case: a catch belongs to the planted pair it
    reproduces, and a decoy false positive belongs to the decoy it reproduces.
    Anything a run produced that no case claims is reported once, on the pair,
    so that summing a column of this table can never double count.
    """

    case: EvalCase
    runs: list[RunOutcome] = field(default_factory=list)

    @property
    def iterations(self) -> int:
        return len(self.runs)

    @property
    def catches(self) -> int:
        return sum(1 for run in self.runs if self.case.id in run.caught_case_ids)

    @property
    def catch_rate(self) -> float | None:
        """The fraction of runs that reproduced the expected evidence pair.

        Return ``None`` where no pair is expected, because a case with nothing
        planted has no catch rate. Reporting 100 percent there would inflate the
        published average with a measurement that was never taken.
        """
        if self.case.expected_outcome != OUTCOME_FINDING or not self.runs:
            return None
        return self.catches / self.iterations

    @property
    def decoy_false_positives(self) -> int:
        """Persisted findings that reproduced this case's compliant decoy pair."""
        return sum(run.decoy_hit_case_ids.count(self.case.id) for run in self.runs)

    @property
    def unattributed_false_positives(self) -> int:
        """The pair's inventions. Shared by every case of the pair, never summed."""
        return sum(run.unattributed_false_positives for run in self.runs)

    @property
    def quarantines(self) -> int:
        return sum(1 for run in self.runs if run.quarantined)

    @property
    def quarantine_rate(self) -> float | None:
        if not self.runs:
            return None
        return self.quarantines / self.iterations

    @property
    def model_turns(self) -> int:
        return sum(run.model_turns for run in self.runs)

    @property
    def severity_distribution(self) -> dict[str, int]:
        """Severity labels of the findings attributed to this case only.

        Every case of a shared pair holds the same runs, so counting each
        run's whole severity tuple would repeat the pair's findings once per
        declared case. Only findings that matched this case's planted or
        decoy pair count here; pair-level totals stay on the run.
        """
        counter: Counter[str] = Counter()
        for run in self.runs:
            counter.update(run.case_severities.get(self.case.id, ()))
        return dict(sorted(counter.items()))

    @property
    def unusable_runs(self) -> int:
        """Runs whose initial model turn produced nothing the runtime could use."""
        return sum(1 for run in self.runs if run.model_output_invalid)

    @property
    def severity_model_ids(self) -> list[str]:
        """Every distinct severity model identifier these runs actually recorded."""
        return sorted({model for run in self.runs for model in run.severity_model_ids})

    @property
    def meets_expectation(self) -> bool:
        """Whether every run matched what the manifest declared for this case.

        A run that never produced a usable model turn fails every expectation,
        including ``no_finding``. Persisting nothing because the model broke is
        not the same result as persisting nothing because the submitted document
        complies, and only the second one is what the manifest declares.
        """
        if not self.runs or self.unusable_runs:
            return False
        if self.case.expected_outcome == OUTCOME_FINDING:
            return self.catches == self.iterations
        if self.case.expected_outcome == OUTCOME_NO_FINDING:
            return (
                self.decoy_false_positives == 0
                and self.unattributed_false_positives == 0
                and self.quarantines == 0
                and all(run.model_turns >= 1 for run in self.runs)
            )
        return (
            self.quarantines == self.iterations
            and self.model_turns == 0
            and all(run.findings_persisted == 0 for run in self.runs)
        )


@dataclass
class PairResult:
    """Everything one document pair's runs produced that no single case owns."""

    pair: CasePair
    runs: list[RunOutcome] = field(default_factory=list)

    @property
    def iterations(self) -> int:
        return len(self.runs)

    @property
    def unattributed_false_positives(self) -> int:
        return sum(run.unattributed_false_positives for run in self.runs)

    @property
    def decoy_false_positives(self) -> int:
        return sum(len(run.decoy_hit_case_ids) for run in self.runs)

    @property
    def rejections(self) -> int:
        return sum(run.rejected for run in self.runs)

    @property
    def retries(self) -> int:
        return sum(run.retried for run in self.runs)

    @property
    def quarantines(self) -> int:
        return sum(1 for run in self.runs if run.quarantined)

    @property
    def quarantine_rate(self) -> float | None:
        if not self.runs:
            return None
        return self.quarantines / self.iterations

    @property
    def model_turns(self) -> int:
        return sum(run.model_turns for run in self.runs)

    @property
    def model_tool_calls(self) -> int:
        return sum(run.model_tool_calls for run in self.runs)

    @property
    def mean_prompt_tokens(self) -> float | None:
        """Mean prompt tokens per run, over the runs that reported a count."""
        counts = [run.prompt_tokens for run in self.runs if run.prompt_tokens is not None]
        return sum(counts) / len(counts) if counts else None

    @property
    def mean_model_tool_calls(self) -> float | None:
        return self.model_tool_calls / self.iterations if self.runs else None

    @property
    def self_check_rejections(self) -> int:
        return sum(run.self_check_rejections for run in self.runs)

    @property
    def runs_that_kept_a_rejected_quote(self) -> int:
        return sum(1 for run in self.runs if run.self_check_rejected_quote_returned)


@dataclass
class LaneResult:
    """One lane measured in one agent mode."""

    lane: str
    mode: AgentMode
    cases: list[CaseResult] = field(default_factory=list)
    pairs: list[PairResult] = field(default_factory=list)

    @property
    def total_runs(self) -> int:
        return sum(pair.iterations for pair in self.pairs)

    @property
    def catch_rate(self) -> float | None:
        return overall_catch_rate(self.cases)

    @property
    def decoy_false_positives(self) -> int:
        return sum(pair.decoy_false_positives for pair in self.pairs)

    @property
    def unattributed_false_positives(self) -> int:
        return sum(pair.unattributed_false_positives for pair in self.pairs)

    @property
    def false_positives(self) -> int:
        """Every persisted finding that reproduced no planted pair."""
        return self.decoy_false_positives + self.unattributed_false_positives

    @property
    def mean_prompt_tokens(self) -> float | None:
        counts = [
            run.prompt_tokens
            for pair in self.pairs
            for run in pair.runs
            if run.prompt_tokens is not None
        ]
        return sum(counts) / len(counts) if counts else None

    @property
    def model_tool_calls(self) -> int:
        return sum(pair.model_tool_calls for pair in self.pairs)

    @property
    def self_check_rejections(self) -> int:
        return sum(pair.self_check_rejections for pair in self.pairs)

    @property
    def runs_that_kept_a_rejected_quote(self) -> int:
        return sum(pair.runs_that_kept_a_rejected_quote for pair in self.pairs)

    def case(self, case_id: str) -> CaseResult | None:
        return next((result for result in self.cases if result.case.id == case_id), None)


def _manifest_text() -> str:
    return MANIFEST_PATH.read_text(encoding="utf-8")


def _machine_block(manifest_text: str, block_name: str) -> list[dict[str, Any]]:
    """Read one named machine-readable JSON block out of the manifest."""
    pattern = re.compile(rf"<!-- {re.escape(block_name)}\s*(\[.*?\])\s*-->", re.DOTALL)
    match = pattern.search(manifest_text)
    if match is None:
        raise ValueError(f"MANIFEST.md must contain the {block_name} block")
    return json.loads(match.group(1))


def _evidence_pairs(entries: Sequence[dict[str, Any]]) -> dict[str, EvidencePair]:
    return {
        str(entry["id"]): EvidencePair(
            id=str(entry["id"]),
            spec_page=int(entry["spec_page"]),
            spec_quote=str(entry["spec_quote"]),
            cut_sheet_page=int(entry["cut_sheet_page"]),
            cut_sheet_quote=str(entry["cut_sheet_quote"]),
        )
        for entry in entries
    }


def load_evidence_pairs(manifest_text: str, lane: Lane | None = None) -> dict[str, EvidencePair]:
    """Read the planted-discrepancy evidence one lane records."""
    selected = LANES[LANE_ORIGINAL] if lane is None else lane
    return _evidence_pairs(_machine_block(manifest_text, selected.evidence_block))


def load_decoy_pairs(manifest_text: str, lane: Lane) -> dict[str, EvidencePair]:
    """Read the compliant near-match pairs one lane records, if it records any."""
    if lane.decoy_block is None:
        return {}
    return _evidence_pairs(_machine_block(manifest_text, lane.decoy_block))


def load_eval_cases(manifest_text: str | None = None, lane: Lane | None = None) -> list[EvalCase]:
    """Read every declared audit case of one lane and bind its evidence."""
    text = _manifest_text() if manifest_text is None else manifest_text
    selected = LANES[LANE_ORIGINAL] if lane is None else lane
    evidence = load_evidence_pairs(text, selected)
    decoys = load_decoy_pairs(text, selected)
    cases: list[EvalCase] = []
    for entry in _machine_block(text, selected.cases_block):
        evidence_id = entry.get("evidence_id")
        decoy_id = entry.get("decoy_id")
        expected = str(entry["expected_outcome"])
        if expected == OUTCOME_FINDING and evidence_id is None:
            raise ValueError(f"case {entry['id']} expects a finding but names no evidence pair")
        if evidence_id is not None and str(evidence_id) not in evidence:
            raise ValueError(f"case {entry['id']} names unknown evidence {evidence_id}")
        if decoy_id is not None and str(decoy_id) not in decoys:
            raise ValueError(f"case {entry['id']} names unknown decoy {decoy_id}")
        cases.append(
            EvalCase(
                id=str(entry["id"]),
                spec_pdf=str(entry["spec_pdf"]),
                cut_sheet_pdf=str(entry["cut_sheet_pdf"]),
                expected_outcome=expected,
                evidence=evidence[str(evidence_id)] if evidence_id is not None else None,
                decoy=decoys[str(decoy_id)] if decoy_id is not None else None,
            )
        )
    return cases


def group_cases_into_pairs(cases: Sequence[EvalCase]) -> list[CasePair]:
    """Group cases by the two documents one audit would read, in manifest order."""
    ordered: dict[tuple[str, str], list[EvalCase]] = {}
    for case in cases:
        ordered.setdefault(case.pair_key, []).append(case)
    return [
        CasePair(spec_pdf=key[0], cut_sheet_pdf=key[1], cases=tuple(grouped))
        for key, grouped in ordered.items()
    ]


def _matches_quote(quote: Mapping[str, Any], page: int, manifest_quote: str) -> bool:
    """True when one persisted quote carries the manifest evidence on its page.

    This is evidence equality, not string equality. The cited page must be the
    page the manifest records, and the two quotes must contain one another in
    either direction after ``specguard.gate.normalize``, on the token
    boundaries the verification gate itself uses. A shorter span of the planted
    passage is the same evidence; a span that merely overlaps it is not, and
    neither is the planted text on a page the manifest does not name. An empty
    quote contains nothing and is contained by nothing, so it matches nothing.
    """
    if int(quote.get("page_number", -1)) != page:
        return False
    persisted = normalize(str(quote.get("text", "")))
    planted = normalize(manifest_quote)
    return contains_on_boundaries(persisted, planted) or contains_on_boundaries(planted, persisted)


def _matches_pair(finding: Mapping[str, Any], evidence: EvidencePair) -> bool:
    """True when a persisted finding carries the manifest pair on both sides."""
    return _matches_quote(
        finding.get("spec_quote") or {}, evidence.spec_page, evidence.spec_quote
    ) and _matches_quote(
        finding.get("cut_sheet_quote") or {}, evidence.cut_sheet_page, evidence.cut_sheet_quote
    )


def build_run_outcome(
    pair: CasePair,
    summary: AuditRunSummary,
    findings: Sequence[dict[str, Any]],
    *,
    model_turns: int,
) -> RunOutcome:
    """Score one completed audit against every case declared for its pair.

    Each persisted finding is classified once. It is a catch for the case whose
    planted pair it reproduces, a decoy false positive for the case whose decoy
    pair it reproduces, or an unattributed false positive when it reproduces
    neither.
    """
    caught: set[str] = set()
    decoy_hits: list[str] = []
    unattributed = 0
    case_severities: dict[str, list[str]] = {}
    for finding in findings:
        matched_case = next(
            (
                case
                for case in pair.cases
                if case.evidence is not None and _matches_pair(finding, case.evidence)
            ),
            None,
        )
        if matched_case is not None:
            caught.add(matched_case.id)
            case_severities.setdefault(matched_case.id, []).append(
                str(finding.get("severity", "unclassified"))
            )
            continue
        decoy_case = next(
            (
                case
                for case in pair.cases
                if case.decoy is not None and _matches_pair(finding, case.decoy)
            ),
            None,
        )
        if decoy_case is not None:
            decoy_hits.append(decoy_case.id)
            case_severities.setdefault(decoy_case.id, []).append(
                str(finding.get("severity", "unclassified"))
            )
            continue
        unattributed += 1

    severities = tuple(str(finding.get("severity", "unclassified")) for finding in findings)
    severity_model_ids = tuple(
        sorted(
            {
                str(finding["severity_model_id"])
                for finding in findings
                if finding.get("severity_model_id")
            }
        )
    )
    # The runtime reports an unusable initial model turn as one rejection with
    # no claims. A run that never produced a claim is not evidence that the
    # case behaved correctly, so it is flagged rather than counted as a pass.
    model_output_invalid = (
        not summary.quarantined and summary.claims_made == 0 and summary.rejected > 0
    )
    usage = summary.audit_model_usage
    return RunOutcome(
        run_id=summary.run_id,
        quarantined=summary.quarantined,
        model_turns=model_turns,
        claims_made=summary.claims_made,
        rejected=summary.rejected,
        retried=summary.retried,
        findings_persisted=summary.findings_persisted,
        caught_case_ids=frozenset(caught),
        decoy_hit_case_ids=tuple(decoy_hits),
        unattributed_false_positives=unattributed,
        severities=severities,
        case_severities={case_id: tuple(values) for case_id, values in case_severities.items()},
        severity_status=summary.severity_status,
        model_output_invalid=model_output_invalid,
        severity_model_ids=severity_model_ids,
        prompt_tokens=usage.prompt_tokens if usage is not None else None,
        model_tool_calls=len(summary.model_tool_calls),
        self_check_rejections=summary.self_check_rejections,
        self_check_rejected_quote_returned=summary.self_check_rejected_quote_returned,
    )


def aggregate(
    lane: str,
    mode: AgentMode,
    pairs: Sequence[CasePair],
    outcomes: Mapping[tuple[str, str], list[RunOutcome]],
) -> LaneResult:
    """Group every run outcome under its pair and under every case of that pair."""
    pair_results = [PairResult(pair=pair, runs=list(outcomes.get(pair.key, []))) for pair in pairs]
    case_results = [
        CaseResult(case=case, runs=list(outcomes.get(pair.key, [])))
        for pair in pairs
        for case in pair.cases
    ]
    return LaneResult(lane=lane, mode=mode, cases=case_results, pairs=pair_results)


def _percent(value: float | None) -> str:
    """Render a rate without ever rounding a short result up to 100 percent.

    Whole-percent rounding turns 199 catches in 200 runs into ``100%``, which
    is the one number this document promises never to publish unless it is
    exact. A rate that is not exactly 1 therefore falls back to one decimal,
    and to ``<100%`` when even that reads as complete. The same guard runs at
    the bottom of the range so a rare false positive never publishes as zero.
    """
    if value is None:
        return NOT_APPLICABLE
    if value == 1:
        return "100%"
    if value == 0:
        return "0%"
    rendered = value * 100
    text = f"{rendered:.0f}%"
    if text in {"100%", "0%"}:
        text = f"{rendered:.1f}%"
    if text == "100.0%":
        return "<100%"
    if text == "0.0%":
        return ">0%"
    return text


def _mean(value: float | None) -> str:
    """Render a per-run mean, or say that nothing reported one."""
    return NOT_APPLICABLE if value is None else f"{value:.1f}"


def _severity_cell(distribution: dict[str, int]) -> str:
    if not distribution:
        return "no findings"
    return ", ".join(f"{name} {count}" for name, count in distribution.items())


def render_case_table(results: Sequence[CaseResult]) -> str:
    """Render the per-case table: what each declared case measured."""
    header = (
        "| Case | Submitted document | Expected outcome | Catch rate "
        "| Decoy false positives | Quarantine rate | Severity distribution |\n"
        "| --- | --- | --- | ---: | ---: | ---: | --- |"
    )
    rows = [
        "| "
        + " | ".join(
            [
                f"`{result.case.id}`",
                f"`{result.case.cut_sheet_pdf}`",
                f"`{result.case.expected_outcome}`",
                _percent(result.catch_rate),
                str(result.decoy_false_positives),
                _percent(result.quarantine_rate),
                _severity_cell(result.severity_distribution),
            ]
        )
        + " |"
        for result in results
    ]
    return "\n".join([header, *rows])


def render_pair_table(results: Sequence[PairResult]) -> str:
    """Render the per-run table: what belongs to an audit rather than to a case."""
    header = (
        "| Specification | Submitted document | Runs | Unattributed false positives "
        "| Rejections | Retries | Model turns | Mean prompt tokens "
        "| Model tool calls per run | Self-check rejections "
        "| Runs that returned a rejected quote |\n"
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |"
    )
    rows = [
        "| "
        + " | ".join(
            [
                f"`{result.pair.spec_pdf}`",
                f"`{result.pair.cut_sheet_pdf}`",
                str(result.iterations),
                str(result.unattributed_false_positives),
                str(result.rejections),
                str(result.retries),
                str(result.model_turns),
                _mean(result.mean_prompt_tokens),
                _mean(result.mean_model_tool_calls),
                str(result.self_check_rejections),
                str(result.runs_that_kept_a_rejected_quote),
            ]
        )
        + " |"
        for result in results
    ]
    return "\n".join([header, *rows])


def _measured_severity(results: Sequence[CaseResult]) -> str:
    """Name the severity models the persisted findings actually recorded.

    The operator supplies a description of the endpoint on the command line.
    That string is not a measurement, so the header prints this alongside it:
    the distinct ``severity_model_id`` values read back from Firestore.
    """
    models = sorted({model for result in results for model in result.severity_model_ids})
    if not models:
        return "none recorded on any persisted finding"
    return ", ".join(f"`{model}`" for model in models)


def _run_identifier_lines(results: Sequence[PairResult]) -> list[str]:
    """List every run identifier so the published numbers can be checked."""
    lines: list[str] = []
    for result in results:
        if not result.runs:
            continue
        identifiers = ", ".join(f"`{run.run_id}`" for run in result.runs)
        lines.append(f"- `{result.pair.cut_sheet_pdf}`: {identifiers}")
    return lines


def _unexercised_notes(lane_result: LaneResult) -> list[str]:
    """State what these numbers do not cover, so the table is not over-read.

    A table of clean results invites a reader to assume every control was
    exercised. Three things are commonly assumed and are not always true: that
    the rejection-and-retry loop ran, that every finding carries a model
    severity, and that the model's own quote self-check did anything. All three
    are derived from the measured runs and written here.
    """
    notes: list[str] = []
    rejections = sum(pair.rejections for pair in lane_result.pairs)
    retries = sum(pair.retries for pair in lane_result.pairs)
    if rejections == 0 and retries == 0:
        notes.append(
            "- The verification gate rejected nothing and the runtime retried nothing "
            "in this lane. The model cited every quote correctly on the first turn, so "
            "the rejection-and-retry loop did not fire. These numbers are therefore "
            "not evidence that the loop works. The loop is covered by the test suite, "
            "which drives rejections deterministically."
        )
    else:
        notes.append(
            f"- The verification gate rejected {rejections} claims and the runtime "
            f"retried {retries}, so the rejection-and-retry loop did fire in this lane."
        )

    unusable = sum(result.unusable_runs for result in lane_result.cases)
    if unusable:
        notes.append(
            f"- {unusable} case-runs produced no usable model turn and were recorded as "
            "unusable. A case containing such a run does not match the manifest, "
            "whatever its other counters say."
        )

    if lane_result.mode is not AgentMode.NAVIGATE:
        notes.append(
            "- This lane ran in `full_text` mode, which sends every page of both "
            "documents up front, so the model needs no tool call to read them. The "
            "mode still registers all five tools, and the tool-call column records "
            "the calls the model chose to make, including the structured-output "
            "call ADK adds for this model."
        )
    rejected = lane_result.self_check_rejections
    kept = lane_result.runs_that_kept_a_rejected_quote
    if rejected == 0:
        notes.append(
            "- The model's own quote self-check rejected nothing in this lane. The "
            "self-check therefore changed no answer here, and these numbers are not "
            "evidence that it would. The runtime gate ran on every claim regardless."
        )
    else:
        notes.append(
            f"- The model's own quote self-check rejected {rejected} quotes, and "
            f"{kept} runs still returned a quote their own check had rejected. The "
            "runtime never trusted that check: its gate ran on every claim, and "
            "again before any write."
        )

    pair_severities = [
        severity
        for pair_result in lane_result.pairs
        for run in pair_result.runs
        for severity in run.severities
    ]
    unclassified = sum(1 for severity in pair_severities if severity == "unclassified")
    classified = len(pair_severities) - unclassified
    if unclassified:
        notes.append(
            f"- {unclassified} of {unclassified + classified} persisted findings carry "
            "`unclassified` severity. Severity classification fell back for those "
            "findings and the reason is recorded on each one. A fallback never blocks "
            "an audit and never changes verification status."
        )
    return notes


def overall_catch_rate(results: Sequence[CaseResult]) -> float | None:
    """The catch rate across every run of every case that plants a discrepancy."""
    measured = [result for result in results if result.catch_rate is not None]
    if not measured:
        return None
    catches = sum(result.catches for result in measured)
    runs = sum(result.iterations for result in measured)
    return catches / runs if runs else None


# --- The ship gate -------------------------------------------------------


@dataclass(frozen=True)
class ShipGateLine:
    """One fixed condition of the ship gate, and whether this run met it."""

    name: str
    passed: bool
    detail: str


@dataclass(frozen=True)
class ShipGateVerdict:
    """Whether navigate mode may become the default, and why."""

    lines: tuple[ShipGateLine, ...]
    evaluable: bool
    reason: str = ""

    @property
    def navigate_ships(self) -> bool:
        """True only when every fixed condition was measured and met."""
        return self.evaluable and bool(self.lines) and all(line.passed for line in self.lines)

    @property
    def shipping_mode(self) -> AgentMode:
        return AgentMode.NAVIGATE if self.navigate_ships else AgentMode.FULL_TEXT

    def render(self) -> str:
        """Render the verdict exactly as the gate computed it."""
        if not self.evaluable:
            return f"SHIP GATE not evaluated: {self.reason}"
        rows = [
            f"  [{'PASS' if line.passed else 'FAIL'}] {line.name}: {line.detail}"
            for line in self.lines
        ]
        verdict = "navigate SHIPS as default" if self.navigate_ships else "navigate does NOT ship"
        return "\n".join([f"SHIP GATE {verdict}", *rows])


def _rate_at_least(navigate: float | None, full_text: float | None) -> bool:
    """True when the navigate rate is no worse than the full-text rate.

    Two unmeasured rates are equal, so they do not fail. One measured rate
    against one unmeasured rate is not a comparison, so it does not pass.
    """
    if navigate is None and full_text is None:
        return True
    if navigate is None or full_text is None:
        return False
    return navigate >= full_text


def evaluate_ship_gate(results: Mapping[tuple[str, str], LaneResult]) -> ShipGateVerdict:
    """Decide, from measured results alone, whether navigate becomes the default.

    The conditions were fixed in the phase work order before any run. This
    function reads results and reports; it never re-runs, re-weights, or relaxes
    a condition. A missing measurement makes the gate unevaluable, which is not
    a pass.
    """
    required = [
        (LANE_ORIGINAL, AgentMode.FULL_TEXT.value),
        (LANE_ORIGINAL, AgentMode.NAVIGATE.value),
        (LANE_MESSY, AgentMode.FULL_TEXT.value),
        (LANE_MESSY, AgentMode.NAVIGATE.value),
    ]
    missing = [f"{lane}/{mode}" for lane, mode in required if (lane, mode) not in results]
    if missing:
        return ShipGateVerdict(
            lines=(),
            evaluable=False,
            reason=(
                "the gate needs both lanes in both modes from one session; "
                f"missing {', '.join(missing)}"
            ),
        )

    unusable_lanes: list[str] = []
    for lane, mode in required:
        result = results[(lane, mode)]
        usable_runs = sum(
            1
            for pair_result in result.pairs
            for run in pair_result.runs
            if not run.model_output_invalid
        )
        if not result.cases or usable_runs == 0:
            unusable_lanes.append(f"{lane}/{mode} measured no usable run")
        elif lane == LANE_MESSY and result.catch_rate is None:
            unusable_lanes.append(f"{lane}/{mode} has no measurable catch rate")
    if unusable_lanes:
        return ShipGateVerdict(
            lines=(),
            evaluable=False,
            reason=("the gate compares measurements, never absences; " + "; ".join(unusable_lanes)),
        )

    original_navigate = results[(LANE_ORIGINAL, AgentMode.NAVIGATE.value)]
    original_full_text = results[(LANE_ORIGINAL, AgentMode.FULL_TEXT.value)]
    messy_navigate = results[(LANE_MESSY, AgentMode.NAVIGATE.value)]
    messy_full_text = results[(LANE_MESSY, AgentMode.FULL_TEXT.value)]

    lines: list[ShipGateLine] = []

    for case_id in ("E-02", "E-03"):
        case = original_navigate.case(case_id)
        rate = case.catch_rate if case is not None else None
        lines.append(
            ShipGateLine(
                name=f"{case_id} catch rate is 100%",
                passed=rate == 1,
                detail=f"measured {_percent(rate)}",
            )
        )

    e01 = original_navigate.case("E-01")
    e01_false_positives = (
        e01.unattributed_false_positives + e01.decoy_false_positives if e01 is not None else -1
    )
    lines.append(
        ShipGateLine(
            name="E-01 false positives are 0",
            passed=e01_false_positives == 0,
            detail=f"measured {e01_false_positives}",
        )
    )

    e04 = original_navigate.case("E-04")
    e04_rate = e04.quarantine_rate if e04 is not None else None
    e04_turns = e04.model_turns if e04 is not None else -1
    lines.append(
        ShipGateLine(
            name="E-04 quarantine rate is 100% with 0 model turns",
            passed=e04_rate == 1 and e04_turns == 0,
            detail=f"measured {_percent(e04_rate)} with {e04_turns} model turns",
        )
    )

    regressions = [
        result.case.id
        for result in original_navigate.cases
        if not _rate_at_least(
            result.catch_rate,
            getattr(original_full_text.case(result.case.id), "catch_rate", None),
        )
    ]
    lines.append(
        ShipGateLine(
            name="no original-lane catch-rate regression against full_text",
            passed=not regressions,
            detail=(
                "every case held or improved"
                if not regressions
                else f"regressed on {', '.join(regressions)}"
            ),
        )
    )

    lines.append(
        ShipGateLine(
            name="no original-lane false-positive regression against full_text",
            passed=original_navigate.false_positives <= original_full_text.false_positives,
            detail=(
                f"navigate {original_navigate.false_positives}, "
                f"full_text {original_full_text.false_positives}"
            ),
        )
    )

    lines.append(
        ShipGateLine(
            name="messy-lane catch rate is at least full_text's",
            passed=_rate_at_least(messy_navigate.catch_rate, messy_full_text.catch_rate),
            detail=(
                f"navigate {_percent(messy_navigate.catch_rate)}, "
                f"full_text {_percent(messy_full_text.catch_rate)}"
            ),
        )
    )

    lines.append(
        ShipGateLine(
            name="messy-lane decoy false positives are at most full_text's",
            passed=messy_navigate.decoy_false_positives <= messy_full_text.decoy_false_positives,
            detail=(
                f"navigate {messy_navigate.decoy_false_positives}, "
                f"full_text {messy_full_text.decoy_false_positives}"
            ),
        )
    )

    return ShipGateVerdict(lines=tuple(lines), evaluable=True)


# --- Rendering -----------------------------------------------------------


#: Publication order for the lanes, so a section never moves because a lane
#: name sorts differently than a reader expects.
LANE_ORDER = (LANE_ORIGINAL, LANE_MESSY)


def _section_order(key: tuple[str, str]) -> tuple[str, int]:
    """Order sections by mode, then by the published lane order."""
    lane, mode = key
    return (mode, LANE_ORDER.index(lane) if lane in LANE_ORDER else len(LANE_ORDER))


def _lane_label(lane: str) -> str:
    return "original four-case lane" if lane == LANE_ORIGINAL else "messy package lane"


def render_lane_section(lane_result: LaneResult) -> str:
    """Render one mode-and-lane results section."""
    lines = [
        f"## Results — `{lane_result.mode.value}` mode, {_lane_label(lane_result.lane)}",
        "",
        f"{lane_result.total_runs} audits over {len(lane_result.pairs)} document pair(s), "
        f"scoring {len(lane_result.cases)} declared case(s).",
        "",
        render_case_table(lane_result.cases),
        "",
        render_pair_table(lane_result.pairs),
        "",
    ]
    lines.extend(_unexercised_notes(lane_result))
    lines.extend(["", "Run identifiers behind this section:", ""])
    lines.extend(_run_identifier_lines(lane_result.pairs))
    return "\n".join(lines)


def render_eval_markdown(
    lane_results: Mapping[tuple[str, str], LaneResult],
    *,
    iterations: int,
    run_date: str,
    model_id: str,
    severity_model_id: str,
    vertex_spend: str,
    verdict: ShipGateVerdict,
    code_revision: str = UNRECORDED_REVISION,
) -> str:
    """Render the whole of EVAL.md, including what each number means."""
    ordered = sorted(lane_results.items(), key=lambda item: _section_order(item[0]))
    all_cases = [result for lane_result in lane_results.values() for result in lane_result.cases]
    total_runs = sum(lane_result.total_runs for lane_result in lane_results.values())
    lines = [
        "# SpecGuard measured evaluation",
        "",
        f"- Date: {run_date}",
        f"- Code revision these numbers describe: `{code_revision}`",
        f"- Iterations per document pair: {iterations}",
        f"- Audit model: `{model_id}` via Vertex AI",
        f"- Severity model, measured from the persisted findings: {_measured_severity(all_cases)}",
        f"- Severity endpoint, as the operator named it: {severity_model_id}",
        f"- Total Vertex spend: {vertex_spend}",
        f"- Sections measured: {len(lane_results)} (one per agent mode and lane)",
        f"- Total real audits: {total_runs}",
        "",
        "Every number below comes from one receipted execution of "
        "`scripts/eval_fixtures.py` against the deployed model path. "
        "The expected outcome of each case is read from the machine-readable blocks "
        "in `fixtures/MANIFEST.md`, not from this file.",
        "",
        "## Method",
        "",
        "One audit runs per document pair per iteration, and every case declared "
        "against that pair is scored from that one run. The messy package lane "
        f"declares nine cases against one document pair, so {iterations} iterations "
        f"are {iterations} audits, not {iterations * 9}. Auditing the same pair once "
        "per case would measure a workflow no reviewer performs and would multiply "
        "the spend by the number of planted discrepancies.",
        "",
        "The two agent modes differ only in what the model is shown and which tools "
        "it may call. `full_text` sends every page of both documents up front and "
        "needs no tool call, though all five tools stay registered. `navigate` "
        "sends the submitted document in full plus "
        "a deterministic page index of the specification, and registers three "
        "read-only tools. The verification gate runs on every claim in both modes, "
        "and the runtime owns every write in both modes.",
        "",
        "## What each column means",
        "",
        "- **Catch rate** is the fraction of runs that persisted a finding carrying "
        "the same evidence as the pair the manifest records for that case. Each side "
        "must cite the page the manifest records, and the persisted quote and the "
        "manifest quote must contain one another in either direction after the "
        "verification gate's own normalization, on the token boundaries that gate "
        "uses. A longer or shorter span of the same passage on the cited page is the "
        "same evidence. A quote that only overlaps the planted passage is not, and "
        "the planted words on another page are not. A case that plants nothing has "
        f"no catch rate and reads `{NOT_APPLICABLE}`, because reporting 100 percent "
        "for an unmeasured case would inflate the average.",
        "- **Decoy false positives** counts persisted findings that reproduced a "
        "compliant near-match pair the manifest records as a decoy, under the same "
        "evidence rule, so a shortened span of a decoy is counted here rather than "
        "as an invention. The wording "
        "differs between the two documents but the submission complies, so a finding "
        "here is a wording difference read as a conflict.",
        "- **Unattributed false positives** counts persisted findings that carried "
        "neither a planted pair nor a decoy pair under that rule. It belongs to the "
        "audit, not to any one case, which is why it appears only in the per-run table.",
        "- **Rejections** counts claims the verification gate refused. A rejection is "
        "the gate working, not a failure of the run.",
        "- **Retries** counts claims sent back to the model once after a gate rejection.",
        "- **Quarantine rate** is the fraction of runs the text-layer integrity screen "
        "stopped before any model call.",
        "- **Model turns** counts calls to the claim generator. The altered fixture "
        "must show zero.",
        "- **Mean prompt tokens** is the mean of the exact prompt-token counts ADK "
        "reported, over the runs that reported one. No count is estimated.",
        "- **Model tool calls per run** counts the function calls the model itself "
        "initiated, including the structured-output call ADK adds for this model.",
        "- **Self-check rejections** counts model-initiated quote checks that answered "
        "that the quote was not on the cited page.",
        "- **Runs that returned a rejected quote** counts runs where the model still "
        "returned a quote its own check had rejected. Together with the column beside "
        "it, this is the honest measure of whether the self-check changed anything.",
        "- **Severity distribution** counts the Gemma severity labels across every "
        "persisted finding of that case.",
        "",
        "## What these numbers supersede",
        "",
        "This file is written in full by `scripts/eval_fixtures.py` on every run. "
        "Nothing in it is carried forward by hand, so a section that stood in an "
        "earlier edition and is absent here was not preserved: it was replaced by "
        f"this measurement. These numbers were measured at {iterations} audits per "
        f"document pair on code revision `{code_revision}`. Any earlier published "
        "measurement taken at a different iteration count, a different code "
        "revision, or under a different scoring rule is superseded by this one, not "
        "corrected by it. The two records describe different runs and are not "
        "comparable cell by cell.",
        "",
        "The self-check columns count calls and their anchor match, which is the "
        "corrected counting rule. An earlier edition counted distinct quote digests, "
        "so a model that checked the same quote twice was counted once. Every "
        "self-check number in this edition was measured under the corrected rule.",
        "",
    ]

    for _, lane_result in ordered:
        lines.extend([render_lane_section(lane_result), ""])

    lines.extend(["## Ship gate", "", "```", verdict.render(), "```", ""])
    lines.append(
        "The conditions above were fixed in the phase work order before any run. A "
        "pure function evaluates them over these results, and the test suite "
        "exercises that same function against known-good and known-bad inputs. No "
        "condition was relaxed and no run was repeated to reach this verdict."
    )

    failures = [result for result in all_cases if not result.meets_expectation]
    lines.extend(["", "## Cases that did not match the manifest", ""])
    if not failures:
        lines.append("None. Every case matched its declared expected outcome in every run.")
    else:
        for result in failures:
            lines.append(
                f"- `{result.case.id}` (`{result.case.cut_sheet_pdf}`), expected "
                f"`{result.case.expected_outcome}`: {result.catches} of "
                f"{result.iterations} runs caught the expected pair, "
                f"{result.decoy_false_positives} decoy false positives, "
                f"{result.quarantines} quarantines, {result.model_turns} model turns."
            )
        lines.append("")
        lines.append(
            "These numbers are published as measured. The README states the same "
            "figures and does not describe the runtime as catching everything."
        )

    lines.extend(
        [
            "",
            "## Scope of these numbers",
            "",
            "This evaluation measures seven committed fictional fixtures, not a corpus "
            "of real submittals. It reports how the runtime behaved on documents built "
            "to carry known discrepancies. It is not evidence of accuracy on documents "
            "outside this set, and it is not a compliance determination.",
            "",
        ]
    )
    return "\n".join(lines)


@dataclass(frozen=True)
class PreviousRun:
    """The measured table the README carried before this run overwrote it."""

    run_date: str
    rows: dict[str, list[str]]


def read_previous_readme_section(readme_text: str) -> PreviousRun | None:
    """Read the README's current eval block so this run can be compared to it.

    A published number that moves is the one thing a reader must not have to
    diff by hand. Reading the block before it is replaced lets the new block
    state, in print, whether anything changed.
    """
    start = readme_text.find(README_TABLE_START)
    end = readme_text.find(README_TABLE_END)
    if start == -1 or end == -1 or end < start:
        return None
    block = readme_text[start:end]
    measured_on = MEASURED_ON_PATTERN.search(block)
    if measured_on is None:
        return None
    rows = {
        case_id: [cell.strip() for cell in cells.split("|")]
        for case_id, cells in TABLE_ROW_PATTERN.findall(block)
    }
    return PreviousRun(run_date=measured_on.group(1), rows=rows)


def compare_to_previous(case_table: str, previous: PreviousRun | None) -> str:
    """State plainly whether any published number moved since the last run."""
    if previous is None:
        return "No earlier measured table was published in this README to compare against."
    current = {
        case_id: [cell.strip() for cell in cells.split("|")]
        for case_id, cells in TABLE_ROW_PATTERN.findall(case_table)
    }
    shared = sorted(set(previous.rows) & set(current))
    if shared and any(len(previous.rows[case]) != len(current[case]) for case in shared):
        return (
            f"The table published on {previous.run_date} carried a different set of "
            "columns, so its cells cannot be compared with these one by one. The "
            "table above is the current measurement."
        )
    moved = [
        case_id
        for case_id in sorted(set(previous.rows) | set(current))
        if previous.rows.get(case_id) != current.get(case_id)
    ]
    if not moved:
        return (
            f"No number in this table moved from the {previous.run_date} run. "
            "Every case reports the same measurement it reported then."
        )
    return (
        f"Numbers moved from the {previous.run_date} run. These cases now measure "
        f"differently: {', '.join(f'`{case_id}`' for case_id in moved)}. "
        "The table above is the current measurement; the earlier figures are "
        "superseded, not corrected."
    )


def render_readme_section(
    lane_results: Mapping[tuple[str, str], LaneResult],
    *,
    iterations: int,
    run_date: str,
    verdict: ShipGateVerdict,
    code_revision: str = UNRECORDED_REVISION,
    previous: PreviousRun | None = None,
) -> str:
    """Render the README block between the eval-table markers.

    The README publishes the mode the deployed service runs. Both modes stay in
    EVAL.md, so a reader who wants the comparison has it here and the whole
    comparison there.
    """
    mode = verdict.shipping_mode
    shipping = [
        lane_results[(lane, mode.value)]
        for lane in (LANE_ORIGINAL, LANE_MESSY)
        if (lane, mode.value) in lane_results
    ]
    cases = [result for lane_result in shipping for result in lane_result.cases]
    if not cases:
        raise ValueError(
            f"this run measured no case in {mode.value} mode, so the README block "
            "would publish a measurement that did not happen"
        )
    pairs = [result for lane_result in shipping for result in lane_result.pairs]
    catch_rate = overall_catch_rate(cases)
    failures = [result for result in cases if not result.meets_expectation]
    honesty = (
        "Every case matched its declared expected outcome in every run."
        if not failures
        else (
            "Not every case matched its declared expected outcome. The tables above "
            "show the measured numbers, including the cases that missed. "
            "SpecGuard does not catch every planted discrepancy on every run."
        )
    )
    headline = (
        "not measured"
        if catch_rate is None
        else f"{_percent(catch_rate)} across every planted discrepancy"
    )
    if not verdict.evaluable:
        gate_sentence = (
            "The ship gate was not evaluated for this run, so the deployed default "
            "remains full-text mode. EVAL.md records why."
        )
    elif verdict.navigate_ships:
        gate_sentence = (
            "Navigation mode met every condition of the ship gate and is the deployed "
            "default. EVAL.md publishes both modes."
        )
    else:
        gate_sentence = (
            "Navigation mode was built and measured and did not meet the ship gate, so "
            "the deployed default remains full-text mode. EVAL.md publishes both modes "
            "and the gate verdict line by line."
        )
    case_table = render_case_table(cases)
    return "\n".join(
        [
            README_TABLE_START,
            "",
            f"Measured on {run_date} by `scripts/eval_fixtures.py`, {iterations} audits per "
            f"document pair against the deployed Vertex AI model path, on code revision "
            f"`{code_revision}`, in `{mode.value}` mode. "
            f"Catch rate: {headline}. "
            f"{honesty} {gate_sentence} "
            "Column definitions and the full record are in [EVAL.md](EVAL.md).",
            "",
            case_table,
            "",
            render_pair_table(pairs),
            "",
            compare_to_previous(case_table, previous),
            "",
            README_TABLE_END,
        ]
    )


def write_readme_section(readme_text: str, section: str) -> str:
    """Replace the marked README block, or append it when no block exists."""
    start = readme_text.find(README_TABLE_START)
    end = readme_text.find(README_TABLE_END)
    if start == -1 or end == -1:
        raise ValueError("README.md must contain the eval-table markers")
    return readme_text[:start] + section + readme_text[end + len(README_TABLE_END) :]


class _CountingClaimGenerator:
    """Wrap the real claim generator and count the model turns it makes."""

    def __init__(self, inner: Any) -> None:
        self._inner = inner
        self.calls = 0

    async def generate_claims(self, message: str) -> Any:
        self.calls += 1
        return await self._inner.generate_claims(message)

    def audit_model_usage(self) -> Any:
        return self._inner.audit_model_usage()

    def model_tool_calls(self) -> Any:
        return self._inner.model_tool_calls()


async def _run_one_pair(
    pair: CasePair,
    *,
    project_id: str,
    output_directory: Path,
    agent_mode: AgentMode,
) -> RunOutcome:
    """Execute one real audit and read its persisted records back from Firestore."""
    from google.cloud import firestore

    from specguard.agent import AdkClaimGenerator, AuditRuntime, create_adk_agent
    from specguard.tools import AuditTools
    from specguard.web.repository import FirestoreRunRepository

    run_id = uuid.uuid4().hex
    firestore_client = firestore.Client(project=project_id)
    try:
        tools = AuditTools(
            firestore_client=firestore_client,
            spec_path=pair.spec_path,
            cut_sheet_path=pair.cut_sheet_path,
            run_id=run_id,
            output_directory=output_directory,
        )
        agent = create_adk_agent(tools, project_id=project_id, agent_mode=agent_mode)
        counting = _CountingClaimGenerator(AdkClaimGenerator(agent, run_id=run_id))
        runtime = AuditRuntime(
            claim_generator=counting,
            tools=tools,
            spec_path=pair.spec_path,
            cut_sheet_path=pair.cut_sheet_path,
            run_id=run_id,
            project_id=project_id,
            agent_mode=agent_mode,
        )
        summary = await runtime.run()
    finally:
        firestore_client.close()

    repository = FirestoreRunRepository(project_id=project_id)
    findings = repository.get_findings(run_id)
    return build_run_outcome(pair, summary, findings, model_turns=counting.calls)


def is_rate_limited(error: BaseException) -> bool:
    """True for the provider's rate-limit refusal, which a wait can clear."""
    text = f"{type(error).__name__}: {error}"
    return "RESOURCE_EXHAUSTED" in text or "429" in text


async def run_pair_with_backoff(
    pair: CasePair,
    *,
    project_id: str,
    output_directory: Path,
    agent_mode: AgentMode,
    sleep: Any = None,
    run_pair: Any = None,
) -> RunOutcome:
    """Run one audit, waiting and retrying only when the provider rate-limits it.

    Nothing about the measurement changes here. A rate-limited audit produced no
    result to keep, so the retry measures the same case from the start; any
    other failure is raised, because a harness that swallowed it would publish a
    table with a silent hole in it.
    """
    waiter = asyncio.sleep if sleep is None else sleep
    execute = _run_one_pair if run_pair is None else run_pair
    for wait in (*RATE_LIMIT_BACKOFF_SECONDS, None):
        try:
            return await execute(
                pair,
                project_id=project_id,
                output_directory=output_directory,
                agent_mode=agent_mode,
            )
        except Exception as error:
            if wait is None or not is_rate_limited(error):
                raise
            print(
                f"  rate limited on {pair.cut_sheet_pdf}; waiting {wait}s before "
                "one more attempt at this audit",
                flush=True,
            )
            await waiter(wait)
    raise RuntimeError("the backoff loop must either return an outcome or raise")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the real SpecGuard runtime over every fixture case and publish EVAL.md."
    )
    parser.add_argument(
        "-n",
        "--iterations",
        type=int,
        default=DEFAULT_ITERATIONS,
        help=f"Audits per document pair (default {DEFAULT_ITERATIONS}).",
    )
    parser.add_argument("--project", default=DEFAULT_PROJECT, help="Google Cloud project ID.")
    parser.add_argument(
        "--mode",
        choices=MODE_CHOICES,
        default=AgentMode.FULL_TEXT.value,
        help="Agent mode to measure. `both` measures each mode in turn.",
    )
    parser.add_argument(
        "--lane",
        choices=LANE_CHOICES,
        default=LANE_ORIGINAL,
        help="Fixture lane to measure. `all` measures each lane in turn.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIRECTORY,
        help="Directory for the RFI drafts these runs generate.",
    )
    parser.add_argument(
        "--pause-seconds",
        type=float,
        default=0.0,
        help=(
            "Seconds to wait between audits. Pacing changes no measured number; "
            "it keeps a long run under the provider's per-minute rate limit."
        ),
    )
    parser.add_argument(
        "--vertex-spend",
        default="not visible in the run output",
        help="Total Vertex spend for this evaluation, if a receipt shows it.",
    )
    parser.add_argument(
        "--severity-model",
        default="",
        help="Severity model identifier to record in the EVAL.md header.",
    )
    parser.add_argument(
        "--code-revision",
        default="",
        help=(
            "Code revision these numbers describe. Defaults to the current "
            "git commit; recorded as unavailable when git cannot report one."
        ),
    )
    return parser


def selected_modes(value: str) -> list[AgentMode]:
    """Expand the ``--mode`` argument into the modes to measure, in order."""
    if value == "both":
        return [AgentMode.FULL_TEXT, AgentMode.NAVIGATE]
    return [AgentMode(value)]


def selected_lanes(value: str) -> list[Lane]:
    """Expand the ``--lane`` argument into the lanes to measure, in order."""
    if value == "all":
        return [LANES[LANE_ORIGINAL], LANES[LANE_MESSY]]
    return [LANES[value]]


def uncommitted_source_paths(status: str) -> list[str]:
    """Return the dirty paths that could have changed what the runtime did.

    ``EVAL.md`` and ``README.md`` are this harness's own output. They are dirty
    after every run, and editing them cannot change a measurement, so counting
    them would make the revision field read "uncommitted" forever and mean
    nothing.
    """
    paths: list[str] = []
    for line in status.splitlines():
        entry = line[3:].strip() if len(line) > 3 else ""
        path = entry.split(" -> ")[-1].strip('"')
        if path and path not in HARNESS_OUTPUTS:
            paths.append(path)
    return paths


def format_code_revision(revision: str | None, status: str | None) -> str:
    """Name the code state the numbers describe, including an uncommitted one.

    A commit identifier alone can misattribute a measurement: with uncommitted
    edits in the tree, ``git rev-parse HEAD`` still names the previous commit,
    and the published table would then claim that commit describes the runtime
    that ran. ``revision`` is the short commit or ``None`` when git could not
    report one; ``status`` is the porcelain status text, or ``None`` when it
    could not be read. Each unknown is written as unknown.
    """
    revision = (revision or "").strip()
    if not revision:
        return UNRECORDED_REVISION
    if status is None:
        return f"{revision}, working tree state unknown"
    dirty = uncommitted_source_paths(status)
    if dirty:
        return f"{revision} plus uncommitted changes to {', '.join(sorted(dirty))}"
    return revision


def current_code_revision() -> str:
    """Return the code state of this working tree, or an explicit non-answer.

    The published numbers describe one state of the code. Naming that state is
    part of the measurement, so a state that cannot be read is recorded as
    unread rather than left out.
    """
    return format_code_revision(_git("rev-parse", "--short", "HEAD"), _git("status", "--porcelain"))


def _git(*arguments: str) -> str | None:
    """Run one read-only git command, or return ``None`` when it cannot run."""
    import subprocess

    try:
        completed = subprocess.run(
            ["git", *arguments],
            cwd=REPOSITORY_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return None
    return completed.stdout if completed.returncode == 0 else None


def eval_summary_line(lane_result: LaneResult, *, run_date: str, code_revision: str) -> str:
    """One machine-readable summary line per mode and lane."""
    matching = sum(1 for result in lane_result.cases if result.meets_expectation)
    return (
        f"EVAL SUMMARY date={run_date} code_revision={code_revision} "
        f"mode={lane_result.mode.value} lane={lane_result.lane} "
        f"cases={len(lane_result.cases)} runs={lane_result.total_runs} "
        f"catch_rate={_percent(lane_result.catch_rate)} "
        f"decoy_false_positives={lane_result.decoy_false_positives} "
        f"unattributed_false_positives={lane_result.unattributed_false_positives} "
        f"rejections={sum(p.rejections for p in lane_result.pairs)} "
        f"retries={sum(p.retries for p in lane_result.pairs)} "
        f"model_turns={sum(p.model_turns for p in lane_result.pairs)} "
        f"mean_prompt_tokens={_mean(lane_result.mean_prompt_tokens)} "
        f"model_tool_calls={lane_result.model_tool_calls} "
        f"self_check_rejections={lane_result.self_check_rejections} "
        f"runs_returning_a_rejected_quote={lane_result.runs_that_kept_a_rejected_quote} "
        f"cases_matching_manifest={matching}/{len(lane_result.cases)}"
    )


def main(argv: Sequence[str] | None = None) -> int:
    import os

    args = _parser().parse_args(argv)
    if args.iterations < 1:
        print("iterations must be at least 1")
        return 2

    modes = selected_modes(args.mode)
    lanes = selected_lanes(args.lane)
    severity_model = args.severity_model or (
        f"`{os.environ['SPECGUARD_GEMMA_MODEL']}` on a Vertex AI endpoint"
        if os.environ.get("SPECGUARD_GEMMA_ENDPOINT") and os.environ.get("SPECGUARD_GEMMA_MODEL")
        else "no endpoint configured for this run"
    )

    manifest_text = _manifest_text()
    lane_results: dict[tuple[str, str], LaneResult] = {}
    audits_run = 0

    for mode in modes:
        for lane in lanes:
            cases = load_eval_cases(manifest_text, lane)
            pairs = group_cases_into_pairs(cases)
            outcomes: dict[tuple[str, str], list[RunOutcome]] = {pair.key: [] for pair in pairs}
            for iteration in range(1, args.iterations + 1):
                for pair in pairs:
                    if args.pause_seconds and audits_run:
                        time.sleep(args.pause_seconds)
                    outcome = asyncio.run(
                        run_pair_with_backoff(
                            pair,
                            project_id=args.project,
                            output_directory=args.output_dir,
                            agent_mode=mode,
                        )
                    )
                    audits_run += 1
                    outcomes[pair.key].append(outcome)
                    print(
                        f"{mode.value}/{lane.name} iteration {iteration}/{args.iterations} "
                        f"{pair.cut_sheet_pdf}: run {outcome.run_id} "
                        f"quarantined={outcome.quarantined} turns={outcome.model_turns} "
                        f"tool_calls={outcome.model_tool_calls} "
                        f"prompt_tokens={outcome.prompt_tokens} "
                        f"persisted={outcome.findings_persisted} "
                        f"caught={sorted(outcome.caught_case_ids)} "
                        f"decoys={list(outcome.decoy_hit_case_ids)} "
                        f"other_fp={outcome.unattributed_false_positives} "
                        f"rejected={outcome.rejected} retried={outcome.retried}",
                        flush=True,
                    )
            lane_results[(lane.name, mode.value)] = aggregate(lane.name, mode, pairs, outcomes)

    verdict = evaluate_ship_gate(lane_results)
    run_date = datetime.now(UTC).strftime("%Y-%m-%d")
    code_revision = args.code_revision or current_code_revision()
    readme_text = README_PATH.read_text(encoding="utf-8")
    previous = read_previous_readme_section(readme_text)

    EVAL_PATH.write_text(
        render_eval_markdown(
            lane_results,
            iterations=args.iterations,
            run_date=run_date,
            model_id=MODEL_ID,
            severity_model_id=severity_model,
            vertex_spend=args.vertex_spend,
            verdict=verdict,
            code_revision=code_revision,
        ),
        encoding="utf-8",
    )
    try:
        readme_section = render_readme_section(
            lane_results,
            iterations=args.iterations,
            run_date=run_date,
            verdict=verdict,
            code_revision=code_revision,
            previous=previous,
        )
    except ValueError as error:
        print(f"README not rewritten: {error}")
    else:
        README_PATH.write_text(write_readme_section(readme_text, readme_section), encoding="utf-8")

    for key in sorted(lane_results, key=_section_order):
        print(
            eval_summary_line(lane_results[key], run_date=run_date, code_revision=code_revision),
            flush=True,
        )
    print(verdict.render(), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
