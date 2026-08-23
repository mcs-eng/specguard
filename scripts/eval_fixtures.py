"""Measure the real SpecGuard runtime against the committed fixture manifest.

The harness runs the deployed model path N times over every audit case the
fixture manifest declares, then writes EVAL.md and refreshes the eval table in
README.md. It publishes what it measured. A catch rate below 100 percent is
written as measured, never rounded up and never dropped.

Definitions used in every number below:

- A run **catches** a planted discrepancy when it persists a finding whose two
  quotes and two page numbers equal the evidence pair the manifest records for
  that case. A near miss is not a catch.
- A **false positive** is a persisted finding that is not the expected pair.
  Every persisted finding of a ``no_finding`` case is a false positive.
- The **quarantine rate** is the fraction of runs the integrity screen stopped
  before any model call.
- **Rejections** and **retries** are the runtime counters, summed over the runs
  of a case. A rejection is a claim the verification gate refused.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import uuid
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from specguard.agent import MODEL_ID
from specguard.models import AuditRunSummary

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
EVIDENCE_PATTERN = re.compile(r"<!-- fixture-evidence\s*(\[.*?\])\s*-->", re.DOTALL)
EVAL_CASES_PATTERN = re.compile(r"<!-- eval-cases\s*(\[.*?\])\s*-->", re.DOTALL)
MEASURED_ON_PATTERN = re.compile(r"Measured on (\d{4}-\d{2}-\d{2}) by")
TABLE_ROW_PATTERN = re.compile(r"^\| `(E-\d+)` \|(.*)\|\s*$", re.MULTILINE)
UNRECORDED_REVISION = "not recorded for this run"

#: The two files this harness rewrites. Edits to them cannot change what the
#: runtime did, and they are always dirty on a second run, so they are excluded
#: from the working-tree check that names the measured code.
HARNESS_OUTPUTS = frozenset({"EVAL.md", "README.md"})

OUTCOME_FINDING = "finding"
OUTCOME_NO_FINDING = "no_finding"
OUTCOME_QUARANTINE = "quarantine"
NOT_APPLICABLE = "n/a"


@dataclass(frozen=True)
class EvidencePair:
    """One planted discrepancy, as the manifest records it."""

    id: str
    spec_page: int
    spec_quote: str
    cut_sheet_page: int
    cut_sheet_quote: str


@dataclass(frozen=True)
class EvalCase:
    """One audit case: a cut sheet, its expected outcome, and its evidence."""

    id: str
    spec_pdf: str
    cut_sheet_pdf: str
    expected_outcome: str
    evidence: EvidencePair | None

    @property
    def spec_path(self) -> Path:
        return FIXTURE_DIRECTORY / self.spec_pdf

    @property
    def cut_sheet_path(self) -> Path:
        return FIXTURE_DIRECTORY / self.cut_sheet_pdf


@dataclass(frozen=True)
class RunOutcome:
    """What one real run of one case produced."""

    run_id: str
    quarantined: bool
    model_calls: int
    claims_made: int
    rejected: int
    retried: int
    findings_persisted: int
    caught_expected_pair: bool
    false_positives: int
    severities: tuple[str, ...]
    severity_status: str | None = None
    model_output_invalid: bool = False
    severity_model_ids: tuple[str, ...] = ()


@dataclass
class CaseResult:
    """The aggregate of every run of one case."""

    case: EvalCase
    runs: list[RunOutcome] = field(default_factory=list)

    @property
    def iterations(self) -> int:
        return len(self.runs)

    @property
    def catches(self) -> int:
        return sum(1 for run in self.runs if run.caught_expected_pair)

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
    def false_positives(self) -> int:
        return sum(run.false_positives for run in self.runs)

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
    def model_calls(self) -> int:
        return sum(run.model_calls for run in self.runs)

    @property
    def severity_distribution(self) -> dict[str, int]:
        counter: Counter[str] = Counter()
        for run in self.runs:
            counter.update(run.severities)
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
        """Whether every run of this case matched what the manifest declared.

        A run that never produced a usable model turn fails every expectation,
        including ``no_finding``. Persisting nothing because the model broke is
        not the same result as persisting nothing because the cut sheet
        complies, and only the second one is what the manifest declares.
        """
        if not self.runs or self.unusable_runs:
            return False
        if self.case.expected_outcome == OUTCOME_FINDING:
            return self.catches == self.iterations and self.false_positives == 0
        if self.case.expected_outcome == OUTCOME_NO_FINDING:
            return (
                self.false_positives == 0
                and self.quarantines == 0
                and all(run.findings_persisted == 0 for run in self.runs)
                and all(run.model_calls >= 1 for run in self.runs)
            )
        return (
            self.quarantines == self.iterations
            and self.model_calls == 0
            and self.false_positives == 0
            and all(run.findings_persisted == 0 for run in self.runs)
        )


def _manifest_text() -> str:
    return MANIFEST_PATH.read_text(encoding="utf-8")


def load_evidence_pairs(manifest_text: str) -> dict[str, EvidencePair]:
    """Read the planted-discrepancy evidence the manifest records."""
    match = EVIDENCE_PATTERN.search(manifest_text)
    if match is None:
        raise ValueError("MANIFEST.md must contain the fixture-evidence block")
    pairs: dict[str, EvidencePair] = {}
    for entry in json.loads(match.group(1)):
        pairs[str(entry["id"])] = EvidencePair(
            id=str(entry["id"]),
            spec_page=int(entry["spec_page"]),
            spec_quote=str(entry["spec_quote"]),
            cut_sheet_page=int(entry["cut_sheet_page"]),
            cut_sheet_quote=str(entry["cut_sheet_quote"]),
        )
    return pairs


def load_eval_cases(manifest_text: str | None = None) -> list[EvalCase]:
    """Read every declared audit case and bind it to its evidence pair."""
    text = _manifest_text() if manifest_text is None else manifest_text
    match = EVAL_CASES_PATTERN.search(text)
    if match is None:
        raise ValueError("MANIFEST.md must contain the eval-cases block")
    evidence = load_evidence_pairs(text)
    cases: list[EvalCase] = []
    for entry in json.loads(match.group(1)):
        evidence_id = entry.get("evidence_id")
        expected = str(entry["expected_outcome"])
        if expected == OUTCOME_FINDING and evidence_id is None:
            raise ValueError(f"case {entry['id']} expects a finding but names no evidence pair")
        if evidence_id is not None and str(evidence_id) not in evidence:
            raise ValueError(f"case {entry['id']} names unknown evidence {evidence_id}")
        cases.append(
            EvalCase(
                id=str(entry["id"]),
                spec_pdf=str(entry["spec_pdf"]),
                cut_sheet_pdf=str(entry["cut_sheet_pdf"]),
                expected_outcome=expected,
                evidence=evidence[str(evidence_id)] if evidence_id is not None else None,
            )
        )
    return cases


def _matches_expected_pair(finding: dict[str, Any], evidence: EvidencePair) -> bool:
    spec_quote = finding.get("spec_quote") or {}
    cut_sheet_quote = finding.get("cut_sheet_quote") or {}
    return (
        str(spec_quote.get("text", "")) == evidence.spec_quote
        and int(spec_quote.get("page_number", -1)) == evidence.spec_page
        and str(cut_sheet_quote.get("text", "")) == evidence.cut_sheet_quote
        and int(cut_sheet_quote.get("page_number", -1)) == evidence.cut_sheet_page
    )


def build_run_outcome(
    case: EvalCase,
    summary: AuditRunSummary,
    findings: Sequence[dict[str, Any]],
    *,
    model_calls: int,
) -> RunOutcome:
    """Score one completed run against the case the manifest declared."""
    caught = False
    false_positives = 0
    for finding in findings:
        if case.evidence is not None and _matches_expected_pair(finding, case.evidence):
            caught = True
        else:
            false_positives += 1
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
    return RunOutcome(
        run_id=summary.run_id,
        quarantined=summary.quarantined,
        model_calls=model_calls,
        claims_made=summary.claims_made,
        rejected=summary.rejected,
        retried=summary.retried,
        findings_persisted=summary.findings_persisted,
        caught_expected_pair=caught,
        false_positives=false_positives,
        severities=severities,
        severity_status=summary.severity_status,
        model_output_invalid=model_output_invalid,
        severity_model_ids=severity_model_ids,
    )


def aggregate(cases: Sequence[EvalCase], outcomes: dict[str, list[RunOutcome]]) -> list[CaseResult]:
    """Group every run outcome under its case, in manifest order."""
    return [CaseResult(case=case, runs=list(outcomes.get(case.id, []))) for case in cases]


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


def _severity_cell(distribution: dict[str, int]) -> str:
    if not distribution:
        return "no findings"
    return ", ".join(f"{name} {count}" for name, count in distribution.items())


def render_results_table(results: Sequence[CaseResult]) -> str:
    """Render the per-case measurement table published in EVAL.md and README.md."""
    header = (
        "| Case | Cut sheet | Expected outcome | Catch rate | False positives "
        "| Rejections | Retries | Quarantine rate | Model calls | Severity distribution |\n"
        "| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |"
    )
    rows = [_results_row(result) for result in results]
    return "\n".join([header, *rows])


def _results_row(result: CaseResult) -> str:
    """Render one measured case as a table row."""
    cells = [
        f"`{result.case.id}`",
        f"`{result.case.cut_sheet_pdf}`",
        f"`{result.case.expected_outcome}`",
        _percent(result.catch_rate),
        str(result.false_positives),
        str(result.rejections),
        str(result.retries),
        _percent(result.quarantine_rate),
        str(result.model_calls),
        _severity_cell(result.severity_distribution),
    ]
    return "| " + " | ".join(cells) + " |"


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


def _run_identifier_lines(results: Sequence[CaseResult]) -> list[str]:
    """List every run identifier so the published numbers can be checked."""
    lines: list[str] = []
    for result in results:
        if not result.runs:
            continue
        identifiers = ", ".join(f"`{run.run_id}`" for run in result.runs)
        lines.append(f"- `{result.case.id}` `{result.case.cut_sheet_pdf}`: {identifiers}")
    return lines


def _unexercised_notes(results: Sequence[CaseResult]) -> list[str]:
    """State what these numbers do not cover, so the table is not over-read.

    A table of clean results invites a reader to assume every control was
    exercised. Two things are commonly assumed and are not always true: that
    the rejection-and-retry loop ran, and that every finding carries a model
    severity. Both are derived from the measured runs and written here.
    """
    notes: list[str] = []
    rejections = sum(result.rejections for result in results)
    retries = sum(result.retries for result in results)
    if rejections == 0 and retries == 0:
        notes.append(
            "- The verification gate rejected nothing and the runtime retried nothing "
            "in this run. The model cited every quote correctly on the first turn, so "
            "the rejection-and-retry loop did not fire. These numbers are therefore "
            "not evidence that the loop works. The loop is covered by the test suite, "
            "which drives rejections deterministically."
        )
    else:
        notes.append(
            f"- The verification gate rejected {rejections} claims and the runtime "
            f"retried {retries}, so the rejection-and-retry loop did fire in this run."
        )

    unusable = sum(result.unusable_runs for result in results)
    if unusable:
        notes.append(
            f"- {unusable} runs produced no usable model turn and were recorded as "
            "unusable. A case containing such a run does not match the manifest, "
            "whatever its other counters say."
        )

    unclassified = sum(result.severity_distribution.get("unclassified", 0) for result in results)
    classified = sum(
        count
        for result in results
        for label, count in result.severity_distribution.items()
        if label != "unclassified"
    )
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


def render_eval_markdown(
    results: Sequence[CaseResult],
    *,
    iterations: int,
    run_date: str,
    model_id: str,
    severity_model_id: str,
    vertex_spend: str,
    code_revision: str = UNRECORDED_REVISION,
    previous: PreviousRun | None = None,
) -> str:
    """Render the whole of EVAL.md, including what each number means."""
    catch_rate = overall_catch_rate(results)
    failures = [result for result in results if not result.meets_expectation]
    lines = [
        "# SpecGuard measured evaluation",
        "",
        f"- Date: {run_date}",
        f"- Code revision these numbers describe: `{code_revision}`",
        f"- Iterations per case: {iterations}",
        f"- Audit model: `{model_id}` via Vertex AI",
        f"- Severity model, measured from the persisted findings: {_measured_severity(results)}",
        f"- Severity endpoint, as the operator named it: {severity_model_id}",
        f"- Total Vertex spend: {vertex_spend}",
        f"- Audit cases: {len(results)}, drawn from the five committed fixture PDFs",
        f"- Total real runs: {sum(result.iterations for result in results)}",
        "",
        "Every number below comes from one receipted execution of "
        "`scripts/eval_fixtures.py` against the deployed model path. "
        "The expected outcome of each case is read from the `eval-cases` block "
        "in `fixtures/MANIFEST.md`, not from this file.",
        "",
        "## Results",
        "",
        render_results_table(results),
        "",
        "## What each column means",
        "",
        "- **Catch rate** is the fraction of runs that persisted a finding whose two "
        "quotes and two page numbers equal the evidence pair the manifest records "
        "for that case. A near miss is not a catch. A case that plants nothing has "
        f"no catch rate and reads `{NOT_APPLICABLE}`, because reporting 100 percent "
        "for an unmeasured case would inflate the average.",
        "- **False positives** counts every persisted finding that is not the expected "
        "pair. For the compliant cut sheet, every persisted finding is a false positive.",
        "- **Rejections** counts claims the verification gate refused. A rejection is "
        "the gate working, not a failure of the run.",
        "- **Retries** counts claims sent back to the model once after a gate rejection.",
        "- **Quarantine rate** is the fraction of runs the text-layer integrity screen "
        "stopped before any model call.",
        "- **Model calls** counts real audit turns. The altered fixture must show zero.",
        "- **Severity distribution** counts the Gemma severity labels across every "
        "persisted finding of that case.",
        "",
        "## Headline numbers",
        "",
    ]

    if catch_rate is None:
        lines.append("- Catch rate across planted discrepancies: not measured.")
    else:
        lines.append(
            f"- Catch rate across every planted discrepancy: **{_percent(catch_rate)}** "
            f"({sum(r.catches for r in results if r.catch_rate is not None)} of "
            f"{sum(r.iterations for r in results if r.catch_rate is not None)} runs)."
        )
    compliant = [r for r in results if r.case.expected_outcome == OUTCOME_NO_FINDING]
    lines.append(
        "- False positives on the compliant cut sheet: "
        f"**{sum(r.false_positives for r in compliant)}** across "
        f"{sum(r.iterations for r in compliant)} runs."
    )
    quarantine_cases = [r for r in results if r.case.expected_outcome == OUTCOME_QUARANTINE]
    for result in quarantine_cases:
        lines.append(
            f"- Quarantine rate on `{result.case.cut_sheet_pdf}`: "
            f"**{_percent(result.quarantine_rate)}** with {result.model_calls} model calls."
        )

    lines.extend(["", "## What this run did not exercise", ""])
    lines.extend(_unexercised_notes(results))

    lines.extend(["", "## Change from the previous published run", ""])
    lines.append(compare_to_previous(results, previous))

    lines.extend(["", "## Cases that did not match the manifest", ""])
    if not failures:
        lines.append("None. Every case matched its declared expected outcome in every run.")
    else:
        for result in failures:
            lines.append(
                f"- `{result.case.id}` (`{result.case.cut_sheet_pdf}`), expected "
                f"`{result.case.expected_outcome}`: {result.catches} of "
                f"{result.iterations} runs caught the expected pair, "
                f"{result.false_positives} false positives, "
                f"{result.quarantines} quarantines, {result.model_calls} model calls."
            )
        lines.append("")
        lines.append(
            "These numbers are published as measured. The README states the same "
            "figures and does not describe the runtime as catching everything."
        )

    lines.extend(["", "## Run identifiers", ""])
    lines.append(
        "Every run below is a real Firestore run. These identifiers are the "
        "receipt behind the table: each one can be queried against the "
        "`findings`, `rejections`, and `integrity_findings` collections."
    )
    lines.append("")
    lines.extend(_run_identifier_lines(results))

    lines.extend(
        [
            "",
            "## Scope of these numbers",
            "",
            "This evaluation measures five committed fictional fixtures, not a corpus "
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


def compare_to_previous(results: Sequence[CaseResult], previous: PreviousRun | None) -> str:
    """State plainly whether any published number moved since the last run."""
    if previous is None:
        return "No earlier measured table was published in this README to compare against."
    current = {
        case_id: [cell.strip() for cell in cells.split("|")]
        for case_id, cells in TABLE_ROW_PATTERN.findall(render_results_table(results))
    }
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
    results: Sequence[CaseResult],
    *,
    iterations: int,
    run_date: str,
    code_revision: str = UNRECORDED_REVISION,
    previous: PreviousRun | None = None,
) -> str:
    """Render the README block between the eval-table markers."""
    catch_rate = overall_catch_rate(results)
    failures = [result for result in results if not result.meets_expectation]
    honesty = (
        "Every case matched its declared expected outcome in every run."
        if not failures
        else (
            "Not every case matched its declared expected outcome. The table above "
            "shows the measured numbers, including the cases that missed. "
            "SpecGuard does not catch every planted discrepancy on every run."
        )
    )
    headline = (
        "not measured"
        if catch_rate is None
        else f"{_percent(catch_rate)} across every planted discrepancy"
    )
    return "\n".join(
        [
            README_TABLE_START,
            "",
            f"Measured on {run_date} by `scripts/eval_fixtures.py`, {iterations} runs per case "
            f"against the deployed Vertex AI model path, on code revision `{code_revision}`. "
            f"Catch rate: {headline}. "
            f"{honesty} Column definitions and the full record are in [EVAL.md](EVAL.md).",
            "",
            render_results_table(results),
            "",
            compare_to_previous(results, previous),
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


async def _run_one_case(
    case: EvalCase,
    *,
    project_id: str,
    output_directory: Path,
    agent_mode: Any = None,
) -> RunOutcome:
    """Execute one real audit and read its persisted records back from Firestore."""
    from google.cloud import firestore

    from specguard.agent import (
        AdkClaimGenerator,
        AuditRuntime,
        create_adk_agent,
        resolve_agent_mode,
    )
    from specguard.tools import AuditTools
    from specguard.web.repository import FirestoreRunRepository

    mode = resolve_agent_mode() if agent_mode is None else agent_mode
    run_id = uuid.uuid4().hex
    firestore_client = firestore.Client(project=project_id)
    try:
        tools = AuditTools(
            firestore_client=firestore_client,
            spec_path=case.spec_path,
            cut_sheet_path=case.cut_sheet_path,
            run_id=run_id,
            output_directory=output_directory,
        )
        agent = create_adk_agent(tools, project_id=project_id, agent_mode=mode)
        counting = _CountingClaimGenerator(AdkClaimGenerator(agent, run_id=run_id))
        runtime = AuditRuntime(
            claim_generator=counting,
            tools=tools,
            spec_path=case.spec_path,
            cut_sheet_path=case.cut_sheet_path,
            run_id=run_id,
            project_id=project_id,
            agent_mode=mode,
        )
        summary = await runtime.run()
    finally:
        firestore_client.close()

    repository = FirestoreRunRepository(project_id=project_id)
    findings = repository.get_findings(run_id)
    return build_run_outcome(case, summary, findings, model_calls=counting.calls)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the real SpecGuard runtime over every fixture case and publish EVAL.md."
    )
    parser.add_argument(
        "-n",
        "--iterations",
        type=int,
        default=DEFAULT_ITERATIONS,
        help=f"Runs per case (default {DEFAULT_ITERATIONS}).",
    )
    parser.add_argument("--project", default=DEFAULT_PROJECT, help="Google Cloud project ID.")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIRECTORY,
        help="Directory for the RFI drafts these runs generate.",
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


def main(argv: Sequence[str] | None = None) -> int:
    import os

    args = _parser().parse_args(argv)
    if args.iterations < 1:
        print("iterations must be at least 1")
        return 2

    from specguard.agent import resolve_agent_mode

    agent_mode = resolve_agent_mode()
    cases = load_eval_cases()
    severity_model = args.severity_model or (
        f"`{os.environ['SPECGUARD_GEMMA_MODEL']}` on a Vertex AI endpoint"
        if os.environ.get("SPECGUARD_GEMMA_ENDPOINT") and os.environ.get("SPECGUARD_GEMMA_MODEL")
        else "no endpoint configured for this run"
    )
    outcomes: dict[str, list[RunOutcome]] = {case.id: [] for case in cases}

    for iteration in range(1, args.iterations + 1):
        for case in cases:
            outcome = asyncio.run(
                _run_one_case(
                    case,
                    project_id=args.project,
                    output_directory=args.output_dir,
                    agent_mode=agent_mode,
                )
            )
            outcomes[case.id].append(outcome)
            print(
                f"iteration {iteration}/{args.iterations} {case.id} "
                f"{case.cut_sheet_pdf}: run {outcome.run_id} "
                f"quarantined={outcome.quarantined} model_calls={outcome.model_calls} "
                f"persisted={outcome.findings_persisted} caught={outcome.caught_expected_pair} "
                f"rejected={outcome.rejected} retried={outcome.retried}",
                flush=True,
            )

    results = aggregate(cases, outcomes)
    run_date = datetime.now(UTC).strftime("%Y-%m-%d")
    code_revision = args.code_revision or current_code_revision()
    readme_text = README_PATH.read_text(encoding="utf-8")
    previous = read_previous_readme_section(readme_text)
    EVAL_PATH.write_text(
        render_eval_markdown(
            results,
            iterations=args.iterations,
            run_date=run_date,
            model_id=MODEL_ID,
            severity_model_id=severity_model,
            vertex_spend=args.vertex_spend,
            code_revision=code_revision,
            previous=previous,
        ),
        encoding="utf-8",
    )
    README_PATH.write_text(
        write_readme_section(
            readme_text,
            render_readme_section(
                results,
                iterations=args.iterations,
                run_date=run_date,
                code_revision=code_revision,
                previous=previous,
            ),
        ),
        encoding="utf-8",
    )

    catch_rate = overall_catch_rate(results)
    print(
        f"EVAL SUMMARY date={run_date} code_revision={code_revision} "
        f"agent_mode={agent_mode.value} "
        f"iterations={args.iterations} "
        f"cases={len(results)} runs={sum(r.iterations for r in results)} "
        f"catch_rate={_percent(catch_rate)} "
        f"false_positives={sum(r.false_positives for r in results)} "
        f"rejections={sum(r.rejections for r in results)} "
        f"retries={sum(r.retries for r in results)} "
        f"model_calls={sum(r.model_calls for r in results)} "
        f"cases_matching_manifest={sum(1 for r in results if r.meets_expectation)}/{len(results)}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
