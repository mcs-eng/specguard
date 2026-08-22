# SpecGuard Phase 1 — handoff

Date: 2026-08-20. Scope: verification gate, data schema, test suite. Pure local Python. No GCP call, no ADK, no network dependency at test time.

## Quality-gate receipts

All commands run in `C:\Users\mcspd\dev\specguard` on arya. Exit codes are unpiped.

| Command | Exit | Result |
| --- | --- | --- |
| `uv sync` | 0 | 15 packages installed |
| `uv run pytest -q` | 0 | `70 passed in 0.87s` |
| `uv run ruff check .` | 0 | `All checks passed!` |
| `uv run ruff format --check .` | 0 | `10 files already formatted` |
| `gh repo create mcs-eng/specguard --private --source=. --remote=origin --push` | 0 | `https://github.com/mcs-eng/specguard`, `* [new branch] HEAD -> main` (re-run after the history rewrite) |

Test count by stage: 36 at the first commit, 38 after the NFKC superscript limitation was pinned, 55 after review iteration 1, 68 after adversarial iteration 2, 70 after backing the text-layer limitations with tests. Commit SHAs quoted elsewhere in this file predate the history rewrite.

## GitHub repository

Mason authorized repo creation mid-session, superseding the work order's "no remote and no push" line.

- Remote: `https://github.com/mcs-eng/specguard`, **private**, owner `mcs-eng`, branch `main`.
- Private matches the settled plan: private during build, public 2026-08-30 before submission.
- No AI attribution in any commit message or in the repo description.

### Planning docs are untracked (Mason, 2026-08-20)

`SETUP.md` and `PLAN.md` are now in `.gitignore` and removed from the index by `git rm --cached`. Both still exist on disk; only the repo stopped carrying them. Mason's rule: a repo is for code, and planning documents in it distract agents that read the tree as context. `README.md` and this handoff stay tracked, because they describe the current contract and the receipts behind it rather than intent or schedule.

### History rewrite (Mason approved, 2026-08-20)

Untracking alone did not close the exposure. `git rm --cached` removes a file from the index, not from history, so the original commits still carried the whole of `SETUP.md`: a billing account ID, the GCP project number, the runtime service account email, the budget id, and a personal address. None of it is a credential and all of it is Mason's own, so it was fine while the repo stayed private. It would have gone public the moment the repo did.

Mason chose the rewrite path plus a fresh remote, so no superseded objects stay reachable on GitHub by direct SHA.

Steps taken, in order:

1. Full copy of the repo to the session scratchpad as a rollback point.
2. `uv tool install git-filter-repo` → exit 0 → `git-filter-repo==2.47.0`.
3. `git filter-repo --invert-paths --path SETUP.md --path PLAN.md --force` → exit 0 → `Parsed 8 commits`, `New history written`. Both files remain on disk, untouched; only the history lost them.
4. **A second pass was needed.** The first verification pass found the same values still in history, because this handoff had quoted them verbatim while documenting the blocker. Documenting a leak reproduced it. The literals were replaced with descriptions, and `git filter-repo --replace-text` scrubbed the remaining copies from every commit.
5. Remote deleted and recreated, then the rewritten history pushed.

Verification gates, all of which must hold before the 08-30 public flip:

- `git log --all --oneline -- SETUP.md PLAN.md` returns no commits.
- `git grep` for each sensitive literal across `git rev-list --all` returns no hits.
- Every commit and its date survives, so the repo still shows the real build progression through the contest window.

Redo this check at the 08-28 pre-publish sweep, because any new document could reintroduce a value the same way this handoff did.

Secret scanning could not be enabled. Receipt:

`gh api -X PATCH repos/mcs-eng/specguard -f 'security_and_analysis[secret_scanning][status]=enabled'` → exit 1 → `HTTP 422: Secret scanning is not available for this repository.`

Secret scanning on a private repo needs GitHub Advanced Security, which this account does not have; it becomes available when the repo goes public on 08-30. Until then the standing "secret scanning stays on" control is **unmet on this repo**. The mitigation is that no credential has been committed: the Gemma API key is deferred to Phase 3 and goes to Secret Manager, never the repo. Re-run the command above after the public flip and confirm exit 0.

`PYTHONIOENCODING=utf-8` was set for the pytest run only, so the terminal could print the ligature and soft-hyphen characters in failure output. It does not affect the result.

## Pinned dependency versions

Runtime (`[project.dependencies]`):

- `pymupdf==1.28.2` — exact pin, as required. Text extraction is the load-bearing behavior.
- `pydantic>=2.9,<3` — resolved to `pydantic 2.13.4`, locked in `uv.lock`.

Dev group (`[dependency-groups].dev`):

- `pytest>=9.0.3,<10` — resolved to `9.1.1`. Raised from `>=8.3,<9` after Dependabot flagged GHSA for "pytest has vulnerable tmpdir handling" (`< 9.0.3`, medium) on first push. Suite passes unchanged on pytest 9.
- `ruff>=0.6,<1` — resolved to `0.16.4`.
- `pymupdf-fonts==1.0.5` — **deviation from the work order, see below.**

`google-adk` is not installed and is not referenced anywhere. Python pin: 3.12.

## Contract-precision and environment decisions, all flagged

### 1. Soft hyphen rejoins words, never numbers

The work order states "strip soft hyphens (U+00AD)". Read literally, a page reading `trans\u00ad\nformer` normalizes to `trans former` — the line break survives as a space and the required known-good case ("quote split across a soft-hyphen line break") can never pass.

Implemented rule: where a soft hyphen sits **between two letters**, remove it together with any whitespace that follows it. Every other soft hyphen is removed on its own, leaving the surrounding whitespace intact. This is the only reading under which the mandated known-good case is provable.

**Correction.** An earlier version of this file claimed the rule "does not loosen the match." That was false, and the adversarial review was right to call it out. The first implementation removed a soft hyphen plus following whitespace unconditionally, which merged digits: `NEMA 1<U+00AD>\n2` became `NEMA 12`, and Type 1 and Type 12 enclosures are materially different equipment. It also let a quote delete its own space, so `AHU<U+00AD> 1` matched page text `AHU1`. Restricting the join to letters closes both. Tests: `test_soft_hyphen_never_joins_digits`, `test_soft_hyphen_never_erases_a_space_next_to_a_digit`.

What is true is the narrower claim: a *printed* hyphen (U+002D) is never removed, so a visible line-end hyphen still causes a rejection. `test_visible_hyphen_is_not_a_soft_hyphen` proves that.

Order of operations in `normalize()`: NFKC, soft-hyphen word join, remaining soft-hyphen removal, casefold, whitespace collapse, strip.

### 2. `pymupdf-fonts` added as a dev dependency

Extraction probe (recorded, run before the tests were written): with the PyMuPDF base14 fonts, `page.insert_text` writes U+00AD back out as a plain hyphen (U+002D) and drops U+FB01 to U+FFFD. Under base14 the mandated soft-hyphen and ligature known-good cases test nothing.

Probe results:

| Font | U+00AD survives | U+FB01 survives |
| --- | --- | --- |
| base14 `helv` | no | no |
| `C:/Windows/Fonts/arial.ttf` | yes | yes |
| `C:/Windows/Fonts/times.ttf` | yes | yes |
| `C:/Windows/Fonts/calibri.ttf` | no (emits U+2010) | yes |
| `notos` from `pymupdf-fonts` | yes | yes |

Chose `notos` over a Windows system font: pip-installable, portable to the Cloud Run build image, no hard-coded absolute path. It is a test-fixture dependency only. It is in the dev group and nothing in `specguard/` imports it.

Side effect worth knowing: with any embedded TrueType font, PyMuPDF extracts every space as U+00A0. NFKC folds that to a plain space, so the no-break-space path is exercised by every fixture page, not only the one line that inserts an explicit U+00A0.

### 3. pytest `--basetemp=.pytest-tmp`

The session sandbox denies writing to `C:\Users\mcspd\AppData\Local\Temp\pytest-of-mcspdg`, so `tmp_path_factory` failed with `PermissionError: [WinError 5]`. Fixture PDFs now build under a repo-local, gitignored `.pytest-tmp/`. Behavior is unchanged; only the fixture directory moved.

## What the suite proves

70 tests. Every case the work order names is present, and every known-bad case asserts the machine-readable rejection reason, not only the boolean.

Known-good (verify):

- exact quote — `test_exact_quote_verifies`
- case-only difference — `test_case_only_difference_verifies`, `..._lowercase_verifies`
- whitespace and line-break differences — `test_whitespace_difference_verifies`, `test_line_break_difference_verifies`, `test_line_break_inside_the_quote_verifies`
- soft-hyphen line break — `test_soft_hyphen_line_break_verifies`
- NFKC characters — `test_nfkc_ligature_verifies`, `test_nfkc_no_break_space_verifies`, `test_nfkc_characters_in_the_quote_verify`

Known-bad (reject):

- quote absent — `test_absent_quote_rejects`
- wrong page cited — `test_quote_on_a_different_page_rejects` (asserts the quote *does* verify on its real page first, so the test cannot pass by accident)
- quote spanning two pages — `test_quote_spanning_two_pages_rejects` (cited as page 3 and as page 4; both reject)
- one digit changed, 208 vs 209 — `test_one_digit_changed_rejects` (asserts 208 verifies first)
- unit changed, kV vs V — `test_unit_changed_rejects` (asserts kV verifies first)
- page out of range — `test_page_number_past_the_end_rejects` (5, 9, 1000) and `test_page_number_below_one_rejects` (0, -1)
- empty quote — `test_empty_quote_rejects` (the empty string is a substring of everything; the gate refuses it explicitly)

All fixture content is fictional: an invented project name, an invented section number, invented equipment values. Nothing from any real project, vendor, PDG, or PEL source.

## SHA-256 handling

`DocumentRecord.sha256` is described as chain-of-custody metadata in `specguard/models.py`, in `specguard/gate.py`, and in `README.md`. Each place states that no part of the gate reads it. No code path passes it to `verify_quote`. Codex confirmed this heading clean with no findings.

`test_document_record_digests_the_actual_bytes` asserts the digest equals `hashlib.sha256` of the file bytes, and `test_document_record_digest_differs_for_different_bytes` asserts two different files differ. The earlier version checked only length and repeatability, which a hardcoded constant would have passed.

## Codex review

Two Codex runs died before producing output (`task-mt1waekd-9s16ev`, `task-mt1w7owo-tc2iiz`): the job PID exited with no final message while the plugin status file still read `running`. Verified dead with `Get-Process -Id 25892` returning not-running. The `cancel` subcommand could not clear them — under Git Bash it mangles `/PID` into a path, and from PowerShell the companion reports "No job found" for an id its own `status` subcommand resolves. Cosmetic only; both processes were already gone.

The third run completed: `task-mt1x4djd-ha83zp`, exit 0.

### Iteration 1 — findings and responses

**Accepted and fixed.**

1. **False verification: the match could begin inside a number.** The gate used a plain `in` test, so the quote `"5 kV with shielded conductors"` verified against a page reading `"15 kV with shielded conductors"`. This is exactly the failure class the demo is built on, and it was the most serious finding.

   Fix: the match now requires alphanumeric boundaries — it may not begin or end in the middle of a word or a number. A boundary is required only between two alphanumeric characters, so a quote whose edge is punctuation still matches. `contains_on_boundaries` checks every occurrence, not only the first, so a bad first hit cannot hide a good later one. This **strengthens** the contract; it rejects strictly more than before, so no known-good case is at risk. The contract text in `gate.py` and `README.md` was updated together with the code.

   New tests: `test_match_may_not_start_inside_a_number`, `test_match_may_not_start_inside_a_word`, `test_match_may_not_end_inside_a_word`, `test_boundary_rule_allows_punctuation_edges`, `test_boundary_rule_checks_every_occurrence`.

2. **A rejected finding could carry no reason.** `verification_status` and `rejection_reason` were independent fields. Added a `model_validator`: a `REJECTED` finding must carry a reason, and nothing else may carry one. Tests: `test_rejected_finding_must_carry_a_reason`, `test_only_a_rejected_finding_may_carry_a_reason`.

3. **README overstated the guarantee.** It claimed "a quote which is not on the cited page never reaches the ledger" and "a hallucinated quote is rejected" without qualification. The "What the contract does not claim" section was rewritten to list every known path to a wrong verification, each pinned by a test.

4. **`test_visible_hyphen_is_not_a_soft_hyphen` asserted only `verified is False`.** It would have passed if the gate returned the wrong rejection reason. Now asserts the reason.

5. **The SHA-256 test would pass with a constant digest.** It checked only length and repeatability. Now asserts the digest equals `hashlib.sha256` of the actual file bytes, plus a second test that two different files produce different digests.

6. **`extract_page_text` had no test at all** despite a docstring promising one-based pages and an `IndexError`. Added `test_extract_page_text_reads_the_cited_page` and `test_extract_page_text_raises_for_a_bad_page`.

7. **The fixture premise was unasserted.** The tests checked only the final match, so if PyMuPDF ever stopped carrying U+00AD or U+FB01 through extraction, the soft-hyphen and ligature cases would silently stop testing anything. Added `test_fixture_font_round_trips_the_special_characters`, which asserts the raw extracted characters.

8. **`Severity` docstring claimed Phase 1 assigns `UNCLASSIFIED` to every finding**, which callers can override. Reworded to describe the default rather than a guarantee.

**Accepted as known limitations, documented rather than fixed.** Each is a cost of a contract clause the work order mandates. Changing any of them would mean weakening or contradicting the contract, which is Mason's call, not this session's.

9. **Casefolding erases case-sensitive units.** `15 mW` and `15 MW` normalize identically — a millionfold difference, and a real hazard in electrical submittals. The contract mandates case-insensitive matching and a known-good case requires it. Pinned by `test_normalize_erases_case_sensitive_units_known_limitation`; listed in README.

10. **NFKC flattens superscripts.** Found independently before the review and confirmed by it. `10²` normalizes to `102`. `mm²` is common in cut sheets. Pinned by `test_normalize_flattens_superscripts_known_limitation`.

11. **Whitespace collapse discards layout.** Text from two columns, two table cells, or a header and a body can become adjacent, so a quote can splice text that never appeared together. Pinned by `test_normalize_splices_across_layout_known_limitation`; also open question 6.

12. **The text layer is not the visible page.** Hidden text or an OCR layer over a scan verifies against text a reader cannot see. The old README line "It does not read scanned documents" was wrong in the direction that matters: a pure image scan carries no text, but an OCR-layer scan *is* read. README now says this precisely.

13. **The schema cannot prove the gate ran.** A caller can construct a `VERIFIED` finding without calling `verify_quote`. The validator in fix 2 closes the internal-consistency gap but not this one; only the persistence path can. Stated in the `Finding` docstring, in README, and pinned by `test_the_schema_does_not_prove_the_gate_ran`. Carried as open question 2.

**Carried to Phase 3, not fixed here.**

14. **`CitedQuote.document_path` and the `pdf_path` argument to `verify_quote` are independent.** A claim citing `A.pdf` could be checked against `B.pdf` and return verified. Codex marked this a hypothesis because the calling code does not exist yet; it is correct that nothing in Phase 1 binds them. Added as open question 7.

### Iteration 2 — adversarial design review

Mason requested an adversarial design review mid-session. It ran read-only against clean commit `65948a9`, so it saw the iteration-1 corrections. It was asked to challenge the approach rather than hunt defects. `task-mt1xluh7-hi5jc2`, exit 0.

`/codex:adversarial-review` could not be used directly: it is marked `disable-model-invocation: true`, and it resolves its target from a git diff, which a one-commit repo cannot supply. The same framing was sent through the task path with an explicit file list.

**Accepted and fixed — two more false-verification paths, both the same class as iteration 1.**

15. **The boundary rule was alphanumeric, not numeric.** A decimal point, a sign, and a thousands separator are all non-alphanumeric, so the iteration-1 fix still let a digit-leading quote ride on a longer number. Confirmed cases: page `0.5 A` satisfied a claim quoting `5 A`; page `-5 kPa` satisfied `5 kPa`; page `±5%` satisfied `5%`; page `12,500 kcmil` satisfied `500 kcmil`; page `AHU-1 unit` satisfied `1 unit`.

    Fix: a digit at the edge of the quote may not sit against any character in `.,-+/±⁄` either. `test_match_may_not_start_against_a_number_binding_character` covers all five, and `test_whole_numbers_still_verify` guards against over-rejection.

16. **The soft-hyphen rule merged digits.** See the correction above. `NEMA 1<U+00AD>\n2` became `NEMA 12`. Fixed by restricting the join to letters.

**Accepted and fixed — the suite was weaker evidence than it looked.**

17. **Two mutations survived the entire suite.** Codex identified both by static analysis and marked them unconfirmed because it did not run pytest. Both were confirmed by actually running them:

    | Mutation | Before | After |
    | --- | --- | --- |
    | `document[min(page_number - 1, 1)]` — read page 2 for every later citation | suite passed | 2 failures, exit 1 |
    | `text.casefold()` → `text.lower()` | suite passed | 1 failure, exit 1 |

    Cause of the first: every positive case lived on pages 1 and 2, and the only page 3 and page 4 tests expected rejection. Fixed by `test_every_page_has_a_verifying_quote`, which requires a verifying quote on all four pages. Cause of the second: no fixture distinguished `casefold` from `lower`. Fixed by `test_normalize_uses_casefold_not_lower`, which uses the German sharp s. Restoring the original file returns exit 0 on 68 tests.

**Recorded, not actioned — these are Phase 3 architecture, and several would change the mandated contract.**

18. **"VERIFIED" is the wrong word for what the gate proves.** Codex's strongest point. The gate establishes that the quoted characters occur on the cited page. It does not establish that the claim built on them is sound. Its recommendation is to name the result `text_anchor_found` and keep the finding `unreviewed` until a human or a structured comparator checks the claim. This is a naming and narrative decision that touches the demo script and the README headline, so it is Mason's call, not this session's. See open question 9.

19. **Match within one extracted block, not the whole page.** Whitespace collapse discards row, cell, and column boundaries, so a column-major extraction such as `AHU-1 / AHU-2 / 30 A / 60 A` can manufacture an association that never appeared visually. The fix is to match inside a single block or table cell and keep coordinates. This is a real contract change and is the most valuable single upgrade available for Phase 3.

20. **The schema models prose plus an undifferentiated bag of quotes.** Missing: typed requirement evidence versus typed submittal evidence (today one spec quote satisfies the schema with no evidence of what the product actually offers); product and model applicability, so a multi-model cut sheet cannot have the right value cited from the wrong row; per-quote verification results with the matched span; structured comparison fields (subject, property, operator, required value and unit, submitted value and unit); evidence of absence, which no positive substring can support; and `ambiguous` / `unextractable` / `needs_review` outcomes, because a binary verified/rejected turns extraction uncertainty into false certainty.

21. **`document_path` is mutable and unbound to `DocumentRecord.sha256`.** Same root as finding 14, stated more sharply: document identity should be the hash and revision, not a path string.

22. **The fixtures are PDFs that PyMuPDF both writes and reads.** They prove the gate implements its contract; they are weak evidence about real vendor PDFs. Codex recommends checking representative real submittals with `get_text("rawdict")` and comparing collision candidates against the rendered page. Phase 2 fixture work should keep this in view.

## Open questions for Phase 3

1. **Retry feedback shape.** `VerificationResult.normalized_quote` is the only field carrying the gate's view of the quote back to the agent. Phase 3 must decide whether the bounced-back rejection also shows the agent the normalized page text, or a near-miss window from it. Showing the page text makes the retry easier but risks the agent pattern-matching a quote out of the feedback rather than out of the document.
2. **Multi-quote findings.** `Finding.quotes` accepts a list, but nothing yet defines whether one failing quote rejects the whole finding or only that quote. The honest default is all-or-nothing; confirm before the ADK tool is written.
3. **Cut-sheet page locators.** The schema carries `spec_locator` and `cut_sheet_locator` as free text, and `CitedQuote.document_path` as the machine link. Phase 2 fixture design should confirm this is enough for the findings page to render a usable citation.
4. **Scanned documents.** The gate is proven against text-based PDFs only. If any demo document is ever a scan, the gate's guarantee does not hold and the README must say so. Keep the fixture set text-based.
5. **`extract_pdf_text` tool boundary.** `extract_page_text` currently raises `IndexError` for a bad page, while `verify_quote` returns a rejection. Phase 3 should decide which surface the ADK tool exposes so a bad page number never becomes an agent-visible exception.
6. **Multi-column extraction order.** The fixtures are single-column, so extraction order matches reading order. A two-column cut sheet can make PyMuPDF interleave text from both columns, which would break a legitimate quote and produce a false rejection. Phase 2 should either keep fixtures single-column or prove the gate against a two-column page before the demo depends on one.
7. **Document identity binding.** `verify_quote(quote, page_number, pdf_path)` takes the document path as an argument, while `CitedQuote` carries its own `document_path`. Nothing checks that they agree, so a claim citing one document could be verified against another. Phase 3 must bind them at the call site, or `verify_quote` should take the `CitedQuote` directly.
8. **Rename the passing outcome.** The adversarial review argues `VERIFIED` overstates what the gate proves and recommends `text_anchor_found`, with the finding staying `unreviewed` until the claim itself is checked. The counter-argument is that the demo narrative and the README headline are built on the current word. Decide before the video script is written, because changing it afterwards is expensive.
9. **Firestore write path.** Nothing persists yet. Decide whether the gate result is stored alongside the finding or recomputed on read, and whether a rejected finding is written at all or only counted.

## Phase 2 — demo fixtures

Date: 2026-08-20. Scope: four fictional, text-based PDFs; the reproducible PyMuPDF generator; a SHA-256 and citation manifest; and fixture acceptance tests. No Phase 1 code or tests changed.

### Deliverables

- `fixtures/build_fixtures.py` generates all committed PDFs with fixed content and metadata.
- `fixtures/asterquay_learning_workshop_specification.pdf` is a seven-page fictional specification with Sections 26 24 13, 26 05 19, and 01 33 00.
- `fixtures/caldra_meridian_480v_switchboard.pdf` is a compliant fictional 480V, 3-phase, 90 deg C cut sheet.
- `fixtures/veylan_arcworks_208v_switchboard.pdf` plants the 208V versus 480V system mismatch.
- `fixtures/torven_70c_termination_switchboard.pdf` plants the 158 deg F termination rating, which is 70 deg C, versus the 90 deg C requirement. Its cut-sheet rating is stated in Fahrenheit only.
- `fixtures/MANIFEST.md` contains each PDF purpose, committed SHA-256, planted discrepancy, and exact quote/page evidence pair.
- `tests/test_fixtures.py` parses the manifest evidence block, verifies every listed spec and cut-sheet quote through `verify_quote`, and proves that a correct quote on its wrong page rejects.

### Quality-gate receipts

All commands ran in `C:\Users\mcspd\dev\specguard` on arya. Every exit code below is from the unpiped command shown.

| Command | Exit | Result |
| --- | ---: | --- |
| `uv run python fixtures/build_fixtures.py` | 0 | Generated the four professional fictional PDFs. |
| `uv run pytest -q` | 0 | `78 passed in 1.71s`. |
| `uv run ruff check .` | 0 | `All checks passed!` |
| `uv run ruff format --check .` | 0 | `13 files already formatted`. |
| `git diff --check` | 0 | No whitespace errors. |
| `uv run python -c "from fixtures.build_fixtures import build_fixtures; from pathlib import Path; import hashlib; root=Path('fixtures'); before={path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in root.glob('*.pdf')}; build_fixtures(); after={path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in root.glob('*.pdf')}; print('byte-identical=' + str(before == after)); raise SystemExit(0 if before == after else 1)"` | 0 | `byte-identical=True`. |

### Manufacturer-name collision checks

The following exact-name web searches returned no results. This is an exact-name search result, not a claim that no similar name exists.

- `"Caldra Meridian Electric" manufacturer` — no links found.
- `"Veylan Arcworks" manufacturer` — no links found.
- `"Torven Switchgear Works" manufacturer` — no links found.

### Codex review

One authorized read-only Codex review ran after the local acceptance checks. It reported no concrete correctness or work-order failures. It confirmed that the four PDFs are reproducible and text-based, manifest hashes match, every manifest citation verifies through the unchanged gate, and the wrong-page case rejects. It made no edits. No correction iteration was needed.

### Open questions for Phase 3

1. The gate proves text anchors only. Phase 3 needs a structured comparison step that evaluates the 158 deg F to 70 deg C conversion and records its conclusion separately from the quote result.
2. The citation display needs a decision on how it will present paired specification and cut-sheet page locators beside one discrepancy.
3. The existing document-identity binding question remains: the Phase 3 persistence path must bind a cited document record to the exact PDF path and hash it verifies.

### Fixture content correction

The visual layout remains unchanged. This correction restores explicit fictional context, benign Section C boilerplate, and discrepancy-neutral language. It removes approval-like review status, external standards claims, certification implications, and language that disclosed the Veylan mismatch. `test_fixtures_disclose_fictional_status_without_approval_or_standard_claims` prevents those content defects from returning.

| Command | Exit | Result |
| --- | ---: | --- |
| `uv run pytest -q` | 0 | `79 passed in 1.70s`. |
| `uv run ruff check .` | 0 | `All checks passed!` |
| `uv run ruff format --check .` | 0 | `13 files already formatted`. |
| `git diff --check` | 0 | No whitespace errors. |
| `uv run python -c "from fixtures.build_fixtures import build_fixtures; from pathlib import Path; import hashlib; root=Path('fixtures'); before={path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in root.glob('*.pdf')}; build_fixtures(); after={path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in root.glob('*.pdf')}; print('byte-identical=' + str(before == after)); raise SystemExit(0 if before == after else 1)"` | 0 | `byte-identical=True`. |

## Phase 3 — ADK agent, guarded Firestore persistence, and RFI draft

Date: 2026-08-20. Scope: one Google ADK agent with exactly four tools, Gemini structured claim output, a one-retry verification loop, Firestore persistence, and RFI draft PDF generation. `google-adk==2.7.1` remained pinned. The model client used Vertex AI model `gemini-3.7-flash` at location `global`.

### Delivered capability

`run_audit.py --spec <pdf> --cutsheet <pdf>` now runs the complete audit against real Vertex AI and real Firestore. It prints claims made, verified, rejected, retried, findings persisted, and the RFI path. The model receives only extracted text under generic document labels and one-based page markers. The runtime does not read or pass `fixtures/MANIFEST.md`, source file names, or fixture-derived hints to the model.

The versioned prompt is `specguard/prompts/audit_claims_v1.txt`. A unit test rejects the named forbidden hint classes and fixture names.

### Persistence path for the adversarial reviewer

The main attack question is whether a finding can reach the `findings` collection without both quotes passing the unchanged gate.

1. `AuditRuntime` calls the public verification tool for both quotes at `specguard/agent.py:213-221`.
2. That first result is not trusted for persistence. `AuditTools.persist_finding` binds exactly one quote to each of the two source paths, then independently calls `specguard.gate.verify_quote` for both at `specguard/tools.py:81-95`.
3. Any gate rejection returns before a Firestore document reference or batch exists at `specguard/tools.py:96-102`.
4. The tool hashes both documents before and after verification. A byte change in that interval returns before any write at `specguard/tools.py:104-114`.
5. The first `findings` document reference is created only after both gate results pass and both hashes remain stable at `specguard/tools.py:115`.
6. The two `DocumentRecord` values and the finding enter one Firestore batch at `specguard/tools.py:141-153`. Each stored quote references its document by SHA-256. The finding quote objects do not store a local path.
7. A caller-set `verification_status=VERIFIED` has no authority. `test_persist_finding_ignores_a_hand_set_verified_status` proves the persistence tool re-runs the gate and writes nothing on rejection.
8. `test_persist_finding_refuses_unverified_quote_without_any_write`, `test_persist_finding_refuses_an_unbound_document`, and `test_persist_finding_refuses_a_document_changed_during_verification` cover the other direct bypass attempts.
9. Final rejected claims use `record_rejection`, which writes only `claim_text`, `reason`, `run_id`, and `timestamp` to the separate `rejections` collection.

The scope of this construction is the SpecGuard application path. A separate process with direct Firestore write credentials is outside the Python API guarantee and can write to Firestore independently.

### Quality-gate receipts

All commands ran in `C:\Users\mcspd\dev\specguard` on arya. Every exit code below is from the unpiped command shown.

| Command | Exit | Result |
| --- | ---: | --- |
| `uv run pytest -q` | 0 | `98 passed, 1 warning in 8.45s`. The warning is ADK's deprecation notice for `BaseAgentConfig`; no test failed. |
| `uv run ruff check .` | 0 | `All checks passed!` |
| `uv run ruff format --check .` | 0 | `19 files already formatted`. |
| `uv build` plus a wheel-content assertion for `specguard/prompts/audit_claims_v1.txt` | 0 | The source distribution and wheel built, and the versioned prompt was present in the wheel. |
| `git diff --check` | 0 | No whitespace errors. Git printed only the repository's existing LF-to-CRLF working-copy warnings. |

### Real run A — compliant cut sheet

Command:

`uv run python run_audit.py --spec fixtures/asterquay_learning_workshop_specification.pdf --cutsheet fixtures/caldra_meridian_480v_switchboard.pdf` → exit 0

Summary:

```text
RUN SUMMARY
run id: d238995ab2ed4ceb9767828cab0c5a07
claims made: 0
verified: 0
rejected: 0
retried: 0
findings persisted: 0
RFI path: C:\Users\mcspd\dev\specguard\artifacts\rfi-d238995ab2ed4ceb9767828cab0c5a07.pdf
```

Firestore verification for this run reported `findings=0 rejections=0` with exit 0. This is the required false-positive check.

### Real run B — first direct mismatch

Command:

`uv run python run_audit.py --spec fixtures/asterquay_learning_workshop_specification.pdf --cutsheet fixtures/veylan_arcworks_208v_switchboard.pdf` → exit 0

Summary:

```text
RUN SUMMARY
run id: e7a1a49e70614591bc6a97da6936d0dd
claims made: 1
verified: 1
rejected: 0
retried: 0
findings persisted: 1
RFI path: C:\Users\mcspd\dev\specguard\artifacts\rfi-e7a1a49e70614591bc6a97da6936d0dd.pdf
```

The generated RFI contains the specification quote `Provide a 480V, 3-phase distribution switchboard for service distribution.` on page 3 and the submitted quote `Nominal system: 208V, 3-phase, 4-wire.` on page 1. The PyMuPDF content assertion returned `missing=[]` with exit 0. Firestore verification reported `findings=1 rejections=0` with exit 0.

### Real run C — converted mismatch

Command:

`uv run python run_audit.py --spec fixtures/asterquay_learning_workshop_specification.pdf --cutsheet fixtures/torven_70c_termination_switchboard.pdf` → exit 0

Summary:

```text
RUN SUMMARY
run id: b4617edf4fc5403c9bf2fd5ca8004009
claims made: 1
verified: 1
rejected: 0
retried: 0
findings persisted: 1
RFI path: C:\Users\mcspd\dev\specguard\artifacts\rfi-b4617edf4fc5403c9bf2fd5ca8004009.pdf
```

The generated RFI contains the specification quote `Conductor terminations shall be rated 90 deg C minimum.` on page 5 and the submitted quote `Field conductor termination rating: 158 deg F.` on page 2. Its claim text states the conversion to `70 deg C`. The PyMuPDF content assertion returned `missing=[]` with exit 0. Firestore verification reported `findings=1 rejections=0` with exit 0.

A separate Firestore assertion checked both persisted runs. It reported `document_refs_exist=[True, True]` and `finding_quotes_have_no_paths=True` for each run, with exit 0.

### Retry and rejection observations

The three real runs produced zero gate rejections and zero retries. The bounded retry path is covered without network calls by `test_rejected_claim_gets_exactly_one_retry_then_rejection`, `test_one_retry_can_correct_the_quote_and_persist`, and `test_retry_must_return_exactly_one_corrected_claim`. The retry message carries `rejection_reason`, `normalized_quote`, and `page_count` from the gate result.

No Vertex cost value was visible in the three command outputs. Actual spend is therefore not available from these receipts.

### ADK pin and review result

ADK 2.7.1 supported the specified structured output, four function tools, Vertex global location, and bounded multi-turn session. It emitted an experimental-feature warning for JSON-schema function declarations, but no specified behavior proved impossible and no version deviation was made.

The one authorized Codex review invocation returned a trust-boundary warning instead of a review result. The invocation reported zero reviewer tokens and produced no findings or correction iteration. The planned Fable-tier adversarial review must therefore treat the persistence attack map above as unreviewed, not as independently confirmed.

## Phase 3 fixes

Date: 2026-08-21. Scope: close REVIEW-P3 findings F1, F2, F4, and F8 without changing the ledger invariant.

- `draft_rfi` now re-runs the verification gate for both quotes in every finding against the two bound source PDFs. It raises before rendering if either quote rejects. The F1 adversarial test now passes without an xfail marker.
- README now limits the guarantee to writes through the SpecGuard persistence tool. It also records concurrent source-file modification during a run as outside the guarantee.
- `AuditRuntime.run` now records `model_output_invalid` for malformed or absent model output. An invalid initial turn drafts a no-findings RFI and returns a summary. An invalid retry rejects that claim and continues with the remaining claims.
- The run summary now reports `findings persisted` instead of printing the duplicate `verified` label. `AuditRunSummary.verified` remains a read-only compatibility property backed by `findings_persisted`.

All commands ran in `C:\Users\mcspd\dev\specguard` on arya. Every exit code below is from the unpiped command shown.

| Command | Exit | Result |
| --- | ---: | --- |
| `uv run pytest -q` | 0 | `116 passed, 1 warning in 5.09s`; zero xfails. |
| `uv run ruff check .` | 0 | `All checks passed!` |
| `uv run python run_audit.py --spec fixtures/asterquay_learning_workshop_specification.pdf --cutsheet fixtures/veylan_arcworks_208v_switchboard.pdf` | 0 | Run `39e0c11f3af84c16a6a6d126817f5e20` made one claim, rejected none, persisted one finding, and generated `artifacts/rfi-39e0c11f3af84c16a6a6d126817f5e20.pdf`. |

## Phase 3.5 — document integrity screening

Date: 2026-08-21. Scope: a deterministic text-layer integrity screen, its enforcement inside the runtime before any text reaches the model, one altered demo fixture, the fifth agent tool, and the tests and disclosure that back them. The verification gate, `specguard/gate.py`, and the verification contract were not modified.

### Delivered capability

`specguard/integrity.py` screens a PDF for text that PDF render mode keeps off the visible page while leaving it in the text layer. It reads PyMuPDF span data only: no OCR, no image rendering, no pixel comparison. `check_text_layer(pdf_path)` returns one report per page holding the invisible spans, their page and font data, the raw MuPDF character flags the rule read, and that page's visible text. The report is deterministic for a given byte stream, through any path spelling, and carries the screened document's SHA-256 and page count. All three come from one immutable byte snapshot.

`AuditRuntime.run` calls the screen first, on both bound documents, before any extracted text is assembled into a model message. `run()` takes no argument that can skip it, so the screen is not optional for any caller. Either bound document flagging quarantines the whole run, because the model message carries the text of both.

A quarantined run makes no model call, persists no claim finding, and drafts no RFI. It attempts one deterministic integrity record per flagged document, into the separate `integrity_findings` collection, and the summary discloses any refusal to write that record instead of reporting an identifier. `AuditTools.persist_integrity_finding` accepts one role name and nothing else. It re-screens the bound file itself at write time, so no caller and no model can author or edit the stored evidence. A document that screens clean is refused and nothing is written.

`check_text_integrity` is registered as the fifth agent tool, ahead of `extract_pdf_text`. The runtime does not depend on the model calling it.

### Detection rule and its limit

MuPDF records the outcome of each text-showing operator's render mode on every character as `char_flags`. The screen reports a span whose characters are neither filled (`FZ_STEXT_FILLED`, 16) nor stroked (`FZ_STEXT_STROKED`, 32). That is render mode 3, the mode an OCR layer uses over a scan. `test_char_flag_bits_match_mupdf` asserts the two constants against `pymupdf.mupdf` so the rule cannot drift from the library silently.

Ground truth for the rule was established by rendering a probe page at 150 dpi and counting non-white pixels per render mode. Modes 0, 1, 2, 4, 5, and 6 painted ink. Modes 3 and 7 painted none. MuPDF reports `char_flags` 80 for modes 4, 5, 6, and 7 alike, so the screen cannot separate a clip-only span from a filled-and-clipped one and reports neither. That gap is stated in the module docstring, in README.md, and pinned by `test_clip_only_render_mode_is_a_known_limitation`, which fails loudly if a later PyMuPDF version starts separating the two.

### Fixture

`fixtures/veylan_arcworks_208v_altered.pdf` is generated by the same `_build_cut_sheet_pdf` call as the unaltered Veylan cut sheet, with two extra page-1 spans written in render mode 3. Both of its rendered pages are pixel-identical to the unaltered fixture at 150 dpi, asserted by `test_the_altered_fixture_is_visually_identical_to_the_original`. Only the two hidden lines differ in the text layer. The hidden wording lives in the P3.5 work order, in `fixtures/build_fixtures.py`, and in the `tests/test_integrity.py` assertion that the screen reports it exactly. It is deliberately absent from PLAN.md, README.md, and MANIFEST.md prose. `fixtures/MANIFEST.md` lists the fixture, its purpose, its SHA-256, and the fact that two hidden spans exist; it quotes the first and deliberately does not quote the second.

### Quality-gate receipts

All commands ran in `C:\Users\mcspd\dev\specguard` on arya. Every exit code below is from the unpiped command shown.

| Command | Exit | Result |
| --- | ---: | --- |
| `uv run pytest -q` | 0 | `153 passed, 1 warning in 9.13s`. The warning is ADK's `BaseAgentConfig` deprecation notice; no test failed. Zero xfails. |
| `uv run ruff check .` | 0 | `All checks passed!` |
| `uv run ruff format --check .` | 0 | `28 files already formatted`. |
| `uv run python -c "from fixtures.build_fixtures import build_fixtures; from pathlib import Path; import hashlib; root=Path('fixtures'); before={path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in root.glob('*.pdf')}; build_fixtures(); after={path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in root.glob('*.pdf')}; print('files=' + str(len(after))); print('byte-identical=' + str(before == after)); raise SystemExit(0 if before == after else 1)"` | 0 | `files=5`, `byte-identical=True`. The four earlier fixtures are unchanged and the new one rebuilds to the same bytes. |

Test count moved from 116 to 153. The additions are 19 in `tests/test_integrity.py`, 11 in `tests/test_quarantine.py`, and 7 in `tests/test_tools.py`. The adversarial suite in `tests/adversarial/` is unchanged and still green.

### Real run D — altered fixture, quarantined before any model call

Command:

`uv run python run_audit.py --spec fixtures/asterquay_learning_workshop_specification.pdf --cutsheet fixtures/veylan_arcworks_208v_altered.pdf` -> exit 0

Summary:

```text
RUN SUMMARY
run id: 582cf78c04614202823a14f46dfa3968
QUARANTINED: text_layer_integrity_screen
no model call was made for this run
  document: submitted_document
    SHA-256: 010fe8a7f5690f84ffa28619b345b3e15c9b900fd9a9837916ff09262f1ab332
    pages screened: 2
    flagged pages: [1]
    hidden spans: 2
    integrity record: BNcytx35iDIn7R9AmoFX
claims made: 0
rejected: 0
retried: 0
findings persisted: 0
RFI path: not generated
```

A Firestore query on `run_id == 582cf78c04614202823a14f46dfa3968` returned exit 0 and reported `integrity_findings = 1`, `findings = 0`, `rejections = 0`. The stored integrity record carried `screen_id=text_layer_render_mode_v1`, `document_role=submitted_document`, `document_sha256` matching the summary, `flagged_pages=[1]`, both hidden span texts, and no local path key.

Scope of the no-model-call claim: this receipt shows a run that made zero claims, persisted zero findings, generated no RFI, and produced no ADK function-declaration warning, where run E over the same specification produced all four. The claim that no model call is made is proven directly by `tests/test_quarantine.py::test_a_quarantined_document_produces_no_model_call`, which asserts the generator's call count is zero, and by `test_the_screen_runs_before_any_text_is_extracted_for_the_model`, which asserts the model message is never built. No packet capture was run against this command.

### Real run E — clean Veylan fixture, audit proceeds normally

Command:

`uv run python run_audit.py --spec fixtures/asterquay_learning_workshop_specification.pdf --cutsheet fixtures/veylan_arcworks_208v_switchboard.pdf` -> exit 0

Summary:

```text
RUN SUMMARY
run id: 5886a71d06264b128096b6c6b4342a9f
claims made: 1
rejected: 0
retried: 0
findings persisted: 1
RFI path: C:\Users\mcspd\dev\specguard\artifacts\rfi-5886a71d06264b128096b6c6b4342a9f.pdf
```

The screen flagged nothing and printed no quarantine block, so the run reached the model and completed the Phase 3 path unchanged. A Firestore query on `run_id == 5886a71d06264b128096b6c6b4342a9f` returned exit 0 and reported `integrity_findings = 0`, `findings = 1`, `rejections = 0`. The persisted finding cites `Provide a 480V, 3-phase distribution switchboard for service distribution.` at specification page 3 and `Nominal system: 208V, 3-phase, 4-wire.` at submitted page 1. The PyMuPDF content assertion on the generated RFI returned `missing=[]` and found no `209V`, with exit 0.

No Vertex cost value was visible in either command output. Actual spend is therefore not available from these receipts.

### Schema and interface changes

- `AuditRunSummary.rfi_path` is now `str | None`. A quarantined run drafts no RFI and reports `None`; `run_audit.py` prints `not generated`. Every non-quarantined path still sets a real path, asserted by the existing suite.
- `AuditRunSummary.quarantine` is a new optional `RunQuarantine`, and `AuditRunSummary.quarantined` is a read-only property. `AuditRunSummary.verified` is unchanged.
- `DocumentRole`, `QuarantinedDocument`, and `RunQuarantine` are new in `specguard/models.py`. `HiddenSpan`, `PageIntegrityReport`, `DocumentIntegrityReport`, and `PersistedIntegrityFinding` live in `specguard/integrity.py`, beside the screen that authors them.
- `tests/fixtures_pdf.py::write_pdf` gained an optional `hidden` argument. Existing callers are unchanged and write no hidden span.

### Stale docstring corrections

- `tests/adversarial/__init__.py` no longer says a control is pinned with a strict xfail. The marker was removed when finding F1 was fixed in commit 206378a; the suite now carries no expected failure.
- `tests/adversarial/test_write_surface.py::test_draft_rfi_refuses_a_fabricated_quote_even_with_correct_hashes` no longer ends with a sentence saying the tool renders the forgery. That sentence contradicted the test's own assertion after the F1 fix. It is not named in the work order; it was corrected because a docstring stating the opposite of its assertion is a defect a reviewer would raise.

### Codex review and correction iteration 1

One authorized read-only Codex review ran against commit `7dd0421`. Unlike the Phase 3 attempt, it returned a real result: 7 findings, 2 high, 3 medium, 2 low, with no file modified (`git status --short --branch`, `git diff --exit-code`, and `git diff --cached --exit-code` all exit 0 in its receipts). Its own attempt to run the quality gates failed on a `uv` cache permission error under its read-only sandbox; it reported `.venv\Scripts\ruff.exe check . --no-cache` exit 0 as a diagnostic substitute. The authoritative gate receipts are the ones in this section, run here.

Two of its findings were real defects in the Phase 3.5 code. Both are now fixed.

**HIGH — the model-callable screening tool disclosed hidden text.** `check_text_integrity` accepted an arbitrary filesystem path and returned the complete report, hidden span text included. A submitted document carrying visible prompt-injection text could name another local PDF, and the model could then read that PDF's hidden text through the tool. That reopens the exact disclosure the screen exists to close. The tool now takes a bound `document_role` and returns the flag summary only: screen identity, role, SHA-256, page count, clean flag, flagged pages, and hidden-span count. The span text goes only to the deterministic integrity record, which no model reads. `test_check_text_integrity_tool_never_returns_hidden_span_text` and `test_no_model_registered_tool_returns_hidden_span_text` pin this.

**HIGH — a document replaced after the screen could reach the model.** The screen and the extraction step read each file separately, so replacing a screened clean document with a hidden-text document in that window sent the replacement's text to the model. `AuditRuntime` now records each screened document's SHA-256 and re-reads both hashes after extraction, refusing to send text if either changed. `test_a_document_replaced_after_the_screen_never_reaches_the_model` pins it. An A-B-A writer that replaces a document and restores it inside that window remains outside the guarantee, which matches the scope README already records for the gate and REVIEW-P3 finding F3.

**MEDIUM — screen evidence was not bound to the reported hash.** `check_text_layer` hashed the file through `gate.build_document_record`, then reopened the path for span extraction. It now reads the bytes once and derives the SHA-256, the page count, and the span evidence from that single snapshot, via `pymupdf.open(stream=...)`. `test_the_report_evidence_and_hash_describe_one_byte_snapshot` replaces the file the instant after that read and asserts the report still describes the original bytes.

**MEDIUM — the runtime and its tools could be bound to different documents.** `AuditRuntime.__init__` now compares its two resolved paths against `AuditTools.spec_path` and `AuditTools.cut_sheet_path` and raises on a mismatch. The runtime also attaches an integrity record identifier only when that record's `document_sha256` matches the hash the screen read; otherwise it reports `persisted_record_describes_other_bytes`. Two new tests pin both.

**MEDIUM — documentation promised a record that the code may refuse to write.** README and this file said a flagged run writes one record per flagged document. The runtime permits refusal and discloses it. Both now say the runtime attempts the write and discloses any refusal.

**LOW — the determinism claim was path-sensitive.** A relative and an absolute path to one file produced unequal reports, because `pdf_path` kept the caller's spelling. `check_text_layer` now resolves the path. `test_the_same_bytes_screen_identically_through_any_path_spelling` pins it.

**LOW — test fidelity.** The rebuild test compared only hash, flagged pages, and span text; it now compares the complete serialized report with the path excluded. A sentinel assertion in `tests/test_quarantine.py` was vacuously true over an empty message list; it now also runs a clean pair, asserts a real non-empty model message exists, and checks the sentinel against both runs. The `tests/test_quarantine.py` module docstring now states that it covers the runtime's bound-document path and that the registered tool surface is covered in `tests/test_tools.py`.

Findings recorded and not actioned: none from this review. Every finding above was either fixed or, in the A-B-A case, an explicitly disclosed residue of a fix.

### Named follow-up, not done here

`extract_pdf_text` accepts a path argument and returns raw page text, so for an unscreened file it would return text hidden by render mode. It is a Phase 3 tool with its own tests and its own callers, and binding it to document roles is outside the Phase 3.5 work order. The quarantine does not rest on that tool refusing anything: it rests on the runtime stopping the run before any extraction happens. README discloses this rather than claiming it away. Binding `extract_pdf_text` and `verify_quote` to bound roles is the recommended follow-up before the repo goes public on 2026-08-30.

### Receipts after correction iteration 1

All commands ran in `C:\Users\mcspd\dev\specguard` on arya. Every exit code below is from the unpiped command shown.

| Command | Exit | Result |
| --- | ---: | --- |
| `uv run pytest -q` | 0 | `162 passed, 1 warning in 6.83s`. Zero xfails. |
| `uv run ruff check .` | 0 | `All checks passed!` |
| `uv run ruff format --check .` | 0 | `28 files already formatted`. |
| fixture rebuild hash compare, command as above | 0 | `files=5`, `byte-identical=True`. |

Test count moved from 153 to 162: 5 added in `tests/test_integrity.py`, 3 in `tests/test_quarantine.py`, and 1 net in `tests/test_tools.py`. No fixture PDF changed.

Both real runs were executed again against the corrected code, because runs D and E above predate these fixes and their receipts would otherwise describe code that is no longer committed.

`uv run python run_audit.py --spec fixtures/asterquay_learning_workshop_specification.pdf --cutsheet fixtures/veylan_arcworks_208v_altered.pdf` -> exit 0

```text
RUN SUMMARY
run id: 074fd5342e57430b9c24b7e66b4d95bd
QUARANTINED: text_layer_integrity_screen
no model call was made for this run
  document: submitted_document
    SHA-256: 010fe8a7f5690f84ffa28619b345b3e15c9b900fd9a9837916ff09262f1ab332
    pages screened: 2
    flagged pages: [1]
    hidden spans: 2
    integrity record: B9YeWhp6r6UPULiiEhPw
claims made: 0
rejected: 0
retried: 0
findings persisted: 0
RFI path: not generated
```

`uv run python run_audit.py --spec fixtures/asterquay_learning_workshop_specification.pdf --cutsheet fixtures/veylan_arcworks_208v_switchboard.pdf` -> exit 0

```text
RUN SUMMARY
run id: 996f2388477248fa87f9d6e984ecf4e7
claims made: 1
rejected: 0
retried: 0
findings persisted: 1
RFI path: C:\Users\mcspd\dev\specguard\artifacts\rfi-996f2388477248fa87f9d6e984ecf4e7.pdf
```

One Firestore query covering both runs returned exit 0. For `074fd5342e57430b9c24b7e66b4d95bd` it reported `integrity_findings = 1`, `findings = 0`, `rejections = 0`, with the record carrying the fixture SHA-256, `flagged_pages=[1]`, and both hidden span texts. For `996f2388477248fa87f9d6e984ecf4e7` it reported `integrity_findings = 0`, `findings = 1`, `rejections = 0`, citing the same two quotes and pages as run E. The RFI content assertion returned `missing=[]` and found no `209V`, exit 0.

These two runs, not runs D and E, are the receipts for the committed code.

## Phase 4 — findings page, durable uploads, and Cloud Run

Date: 2026-08-21. Scope: FastAPI findings page, role-bound model tools, Cloud Storage object persistence, and the Cloud Run deployment path.

### Delivered locally

- `extract_pdf_text` and `verify_quote` now accept `specification` or `submitted_document`, never a filesystem path. The runtime uses the same role-bound tool surface.
- `specguard/web/` adds the public read-only findings page, guarded PDF upload, run detail page, human-only integrity record view, and durable RFI route.
- Each upload and generated RFI is stored under `<run_id>/` in Cloud Storage. The run document records each object name, content type, and SHA-256. No ephemeral path enters the run document.
- The upload route requires a passphrase, accepts only `application/pdf` with a PDF signature, limits each file to 5 MB, and allows two in-flight audits per instance.

### Local quality-gate receipts

All commands ran in `C:\Users\mcspd\dev\specguard` on arya. Every exit code below is from the unpiped command shown.

| Command | Exit | Result |
| --- | ---: | --- |
| `uv lock` | 0 | Locked FastAPI, Cloud Storage, Jinja2, multipart parsing, and Uvicorn dependencies. |
| `uv sync` | 0 | Installed the new locked dependencies. |
| `uv run pytest -q tests/test_web.py` | 0 | `8 passed, 2 warnings in 1.93s`. |
| `uv run pytest -q` | 0 | `172 passed, 2 warnings in 7.48s`. |
| `uv run ruff check .` | 0 | `All checks passed!` |
| `uv run ruff format --check .` | 0 | `34 files already formatted`. |
| `git diff --check` | 0 | No whitespace errors; Git printed existing LF-to-CRLF working-copy warnings only. |

### Cloud setup receipts

The setup commands and their unpiped exit codes are recorded in local `SETUP.md`. They enabled Cloud Storage and Secret Manager, created the private uniform-access `specguard-hack-runs` bucket in us-central1, granted the runtime service account `roles/storage.objectAdmin` on that bucket, created `specguard-demo-passphrase` without a value, and granted the runtime service account `roles/secretmanager.secretAccessor` on that secret.

### Deployment hold

Deployment is intentionally stopped before the `gcloud run deploy` command. `specguard-demo-passphrase` has no enabled version, and Mason must create one in Google Cloud Console without sending its value through this session. The exact human step is in local `SETUP.md`. After Mason confirms that one enabled version exists, the remaining work is: deploy with the Secret Manager reference, record the live URL and cold-start time, curl public `GET /`, submit one real audit through the web UI, run the authorized Codex review, apply at most two corrections, re-run the quality gates, and commit.

## Phase 5 — submission-state UI pass

Date: 2026-08-21. Scope: the deployed page's idle, running, completed, quarantined, and failed states, and the human-facing failure path. No change to the verification gate, the role-bound tools, the passphrase guard, the upload limits, the two-audit application limit, or the Cloud Run concurrency setting.

The Phase 4 "Deployment hold" section above is superseded. The secret version exists and the service is deployed.

### Capability delivered

Mason submits one audit, sees at once that it started, reads that the model step has no progress report, and cannot start a duplicate run by clicking or by pressing Enter again.

### Behavior changes

- The submit control disables itself on submit and now has a real disabled appearance: flat grey fill, muted label, `not-allowed` cursor, no hover brightening. `#audit-submit:disabled` is the rule.
- The form-level submit handler sets a `submitting` flag and calls `event.preventDefault()` on every later submit event. This covers a repeated Enter press, not only a repeated click. The submit button carries no `name`, so disabling it does not remove a field from the POST body.
- The running panel is a bordered blue block with a pulsing dot, `role="status"` and `aria-live="polite"`. Its copy is: `Audit running. Do not submit again.` then `A second submit starts a duplicate run.` then a sentence stating that the model step takes time and that the server reports no progress, so the page shows no bar and no percentage. No completion time is promised. The pulse is disabled under `prefers-reduced-motion: reduce`.
- The field group receives `inert` and dims to 50 percent while a submit is in flight. `inert` blocks focus and pointer interaction only; it does not remove fields from the POST body, unlike `disabled`.
- A `pageshow` handler with `event.persisted` returns the form to its idle state, so a back-button restore from the browser cache does not leave a permanently dead button.
- A failed audit no longer returns FastAPI's JSON error body. `POST /audit` now renders the landing page with HTTP 500, a red alert reading `The audit did not complete.`, and, when a run record exists, a link to that FAILED run. `AuditFailedError` carries the run ID from `_run_audit` to the route for this. When the failure happened before any run record could be written, the page shows the alert with no run link, matching the existing cleanup path.
- The run detail page states each non-completed status in plain words: FAILED, RUNNING, and QUARANTINED each get their own notice. The quarantine notice names the reason code and states that no model call was made.
- Finding quotes now carry an explicit locator label above each quote, `Specification page N` and `Submitted page N`, instead of an inline `Page N`. Quote text, rejection reason codes, hidden-span text, SHA-256 values, and the RFI link are unchanged in content.
- The recent-runs table leads with the status column, right-aligns the two count columns with tabular figures, and reads `None` instead of an em dash when a run has no RFI.

### Controls confirmed unchanged

`specguard/gate.py`, `PLAN.md`, and every fixture PDF are untouched. `POST /audit` still requires the demo passphrase through `secrets.compare_digest`. Uploads are still `application/pdf` only, still capped at 5 MB, and still checked for the `%PDF-` signature. `MAX_IN_FLIGHT_AUDITS` is still 2. `deploy-specguard.ps1` still passes `--concurrency 2`, and `test_deploy_script_limits_cloud_run_request_concurrency` still pins it. Durable objects are still keyed by run ID with SHA-256 in Firestore. Failed runs are still recorded as FAILED, and unrecorded objects are still deleted. Public routes are still GET-only. No external analytics, tracking, or third-party asset was added; the pages use system fonts and inline CSS only.

### Test changes

`tests/test_web.py` moved from 8 to 16 tests. New or changed:

- `test_findings_page_shows_a_disabled_submit_state_and_a_stop_message` asserts the stop copy, the `#audit-submit:disabled` rule, the disable statement, and the repeat-submit guard.
- `test_findings_page_promises_no_completion_time_or_progress_value` asserts the no-progress sentence and asserts that no `role="progressbar"` and no `<progress>` element exists.
- `test_audit_cleans_up_objects_when_uploads_cannot_be_recorded` now also asserts an HTML response carrying the failure message and no FAILED-run link.
- `test_audit_records_a_failed_run_after_durable_inputs_are_stored` now also asserts an HTML response carrying the failure message and a link to the FAILED run.
- `test_run_view_tells_a_reviewer_that_a_running_run_has_not_finished` covers the RUNNING notice.
- `test_run_view_renders_findings_rejections_and_hidden_integrity_text` now also asserts the quarantine notice, its reason code, and both quote locators.
- `test_findings_page_shows_the_upload_form_and_no_runs` dropped the retired `Audit is running. Wait for the findings page.` string.

Every route test remains network-free. All fakes stay in-process.

### Local quality-gate receipts

All commands ran in `C:\Users\mcspd\dev\specguard` on arya. Every exit code below is from the unpiped command shown.

| Command | Exit | Result |
| --- | ---: | --- |
| `uv run pytest -q` | 0 | `180 passed, 2 warnings in 7.98s`. |
| `uv run ruff check .` | 0 | `All checks passed!` |
| `uv run ruff format --check .` | 0 | `34 files already formatted`. |
| `git diff --check` | 0 | No whitespace error. Git printed existing LF-to-CRLF working-copy warnings only. |

### Deployment receipts

| Command | Exit | Result |
| --- | ---: | --- |
| `.\deploy-specguard.ps1` | 0 | `Service [specguard] revision [specguard-00003-797] has been deployed and is serving 100 percent of traffic.` |
| `gcloud run services describe specguard --region us-central1 --project specguard-hack` | 0 | `containerConcurrency = 2`, `specguard-00003-797`, `percent = 100`. |
| `Invoke-WebRequest https://specguard-108657628939.us-central1.run.app/` | 0 | `StatusCode = 200`, 18303 bytes. The response contains `Audit running. Do not submit again.`, `A second submit starts a duplicate run.`, `#audit-submit:disabled`, and `The server reports no progress`. |

- Deployed revision: `specguard-00003-797`, 100 percent of traffic.
- Live URL: `https://specguard-108657628939.us-central1.run.app`.
- Internal run URL reported by describe: `https://specguard-ypkohkbwgq-uc.a.run.app`.

### Visual check limitation

The Chrome extension was not connected in this session, so no live browser screenshot was taken. The pages were rendered to static HTML through the in-process test client with fictional run data, one file per state, and reviewed as files. The running state and the disabled button are covered by the route tests listed above rather than by a browser screenshot.

### Phase 5 second pass — design-engineering polish

Mason asked for a further pass using Emil Kowalski's design-engineering skill. The animation gate from that skill's `find-animation-opportunities` companion was applied: every candidate had to name a frequency band, one purpose, a duration budget, and a function test. Rejected candidates are recorded below, because a skipped animation is a decision, not an omission.

Deployed revision after this pass: `specguard-00004-q7r`, 100 percent of traffic. `containerConcurrency` is still 2.

#### Motion added, with its reason

- Easing tokens `--ease-out: cubic-bezier(0.23, 1, 0.32, 1)` and `--ease-in-out: cubic-bezier(0.77, 0, 0.175, 1)` replace the built-in curves. The built-ins are too weak to read as intentional.
- The submit button scales to `0.97` on `:active` over 140 ms. Purpose: feedback. This is the direct answer to a slow audit that gave no sign it heard the click.
- The running panel now enters over 260 ms with `opacity` and an 8 px `translateY`, through `@starting-style` and `transition-behavior: allow-discrete` on `display`. Purpose: preventing a jarring change. Nothing in the real world appears from nothing.
- The error alert enters the same way, 240 ms. Purpose: preventing a jarring change on a page that reloads into a failure.
- The RUNNING badge dot and the running-panel dot breathe on a 1.8 s `ease-in-out` loop. Purpose: state indication. A breathing dot indicates activity; it is not a progress claim, and it carries no bar and no percentage.
- The field group fades to 45 percent over 220 ms while a submit is in flight, instead of snapping.
- Table rows tint on hover over 120 ms. Purpose: scanning a list of runs.

Every animated property is `transform`, `opacity`, `background-color`, `border-color`, or `filter`. No layout property is animated. There is no `transition: all` anywhere, and nothing enters from `scale(0)`.

#### Animation candidates rejected

- Staggered entry on the recent-runs rows. Rejected on frequency. The list renders on every visit to the landing page, and a cascade would make the page feel slower every time.
- A spinner inside the submit button. Rejected on purpose. The running panel already indicates activity, and a second indicator on the same event is decoration.
- A page transition between the landing page and a run detail page. Rejected on function. The detail page is dense evidence, and motion between reading states hinders.
- An animated counter on the four run statistics. Rejected on function. These are functional numbers a reviewer reads, and animated data hinders comprehension.

#### Interaction and clarity, not motion

- The file inputs are now transparent overlays that fill their drop zone, so the whole zone is one control. The input keeps layout and focus, so the browser's own `required` message still anchors to the zone. Each zone shows the chosen filename and its size, a check mark, a solid green border, and a `Replace PDF` label instead of `Choose PDF`. Mason can now confirm both slots are loaded before he commits to a slow run.
- Each zone runs an advisory client-side check for content type and the 5 MB limit and shows a red warning reading that the server will reject the file. This check is advisory only. It never blocks submission, and it changes nothing about the server-side check in `_read_pdf_upload`, which stays authoritative.
- Focus is moved to the running panel on submit. Without this the focus would land on `<body>` when the submit button disables, and a screen-reader user would lose the context.
- The browser tab title changes to `Audit running — SpecGuard` for the length of the run and resets on a back-button restore. Mason can leave the tab and still see the state.
- The recent-runs table now shows the first 8 characters of the run ID in a chip, with the complete ID in the link's `title`. The complete ID stays on the run detail page and in its durable-objects table. This trades one-click copying from the list for a readable list; say so if the trade is wrong.
- The created column renders a relative time such as `2 hours ago` through `Intl.RelativeTimeFormat`, with the absolute local time in the `title`. The server still emits the absolute timestamp inside the `<time>` element, so a browser with JavaScript disabled still shows it.
- Hover states are gated behind `@media (hover: hover) and (pointer: fine)`, so a tap on a touch device does not leave a stuck hover state.
- `prefers-reduced-motion: reduce` removes every transform and every looping animation, and keeps the opacity transitions that carry meaning.

#### Test changes in this pass

`tests/test_web.py` moved from 16 to 20 tests. New:

- `test_findings_page_confirms_each_chosen_file_before_the_run_starts`
- `test_findings_page_gives_the_submit_button_press_feedback`
- `test_findings_page_respects_reduced_motion_and_gates_hover`, which also asserts that no `transition: all` and no `scale(0)` reaches the page
- `test_recent_runs_shorten_the_run_identifier_and_keep_it_reachable`

#### Receipts for this pass

All commands ran in `C:\Users\mcspd\dev\specguard` on arya. Every exit code below is from the unpiped command shown.

| Command | Exit | Result |
| --- | ---: | --- |
| `uv run pytest -q` | 0 | `184 passed, 2 warnings in 7.45s`. |
| `uv run ruff check .` | 0 | `All checks passed!` |
| `uv run ruff format --check .` | 0 | `34 files already formatted`. |
| `git diff --check` | 0 | No whitespace error after one trailing blank line was trimmed from this file. |
| `.\deploy-specguard.ps1` | 0 | `Service [specguard] revision [specguard-00004-q7r] has been deployed and is serving 100 percent of traffic.` |
| `gcloud run services describe specguard --region us-central1 --project specguard-hack` | 0 | `containerConcurrency = 2`, `specguard-00004-q7r`, `percent = 100`. |
| `Invoke-WebRequest https://specguard-108657628939.us-central1.run.app/` | 0 | `StatusCode = 200`. The response contains the press-feedback rule, `No file chosen`, `Replace PDF`, the reduced-motion block, the hover gate, the stop copy, and a `<time datetime=` element. |

#### Live audit receipt

Mason submitted the audit through the deployed web UI on 2026-08-21 and reported the run. This session never held the demo passphrase and submitted no audit; every live run on the service is Mason's. The receipt below was read back from the public GET routes, not from the submission.

- Run ID: `c3a307abec1143bd92d11011b59834d9`.
- Status: `COMPLETED`.
- Created: `2026-08-21 18:09:56.381332+00:00`.
- Claims made 1, findings persisted 1, rejected 0, retried 0.
- Verified finding, both quotes read back from `GET /runs/c3a307abec1143bd92d11011b59834d9`:
  - Specification page 3: `Provide a 480V, 3-phase distribution switchboard for service distribution.`
  - Submitted page 1: `Nominal system: 208V, 3-phase, 4-wire.`
- Durable objects, both keyed by run ID with SHA-256 recorded in Firestore:
  - `c3a307abec1143bd92d11011b59834d9/specification.pdf` -> `88988353d0332a32c3dcdf47225e150814a92277beb920cdfc919de9d3dfe568`
  - `c3a307abec1143bd92d11011b59834d9/submitted-document.pdf` -> `8c3109b8ef690dd2c51de6fa3efacedcdbc6da6df582c138c504f82b2155a385`
- `GET /runs/c3a307abec1143bd92d11011b59834d9/rfi.pdf` returned `200`, `Content-Type: application/pdf`, 5408 bytes.

The run exercised the whole deployed path: guarded upload, durable object write, run record, the deterministic gate, a persisted verified finding, RFI generation, and the RFI download route. Mason independently confirmed the landing page renders with the upload form, the passphrase field, and the run list. He read page text only; the browser pane was not compositing, so no pixel-level visual confirmation exists from either side.

### Phase 5 Codex review and corrections

One authorized read-only Codex review ran against commit `9ce251c`. It reported four findings and checked seven named invariants. It modified no file and ran no test, because the review forbade file modification and the suite writes cache files.

The first attempt to run it through the plugin's forwarding subagent returned without doing any work: zero tool uses, no review. The review was then launched directly against the companion CLI as job `task-mt3a1fus-o9zfcz`. Codex session `01a0258f-994f-7393-8e69-c418761c72b5`.

Three findings were real and are fixed. One is recorded and not actioned, because fixing it needs an architecture change this pass was told to stop on.

**MEDIUM, fixed — extra multipart file parts never met the upload checks.** The route binds one scalar `UploadFile` per declared field, so a request carrying a third file part, or two parts for one field, had those extra parts fully parsed and spooled while only the two bound values were checked for content type and the 5 MB limit. `_reject_unexpected_file_parts` now inspects the parsed form and refuses any submission carrying a file part outside `spec_pdf` and `cut_sheet_pdf`, or more than one file for either field. `test_audit_rejects_a_submission_carrying_an_unexpected_file` and `test_audit_rejects_a_submission_carrying_two_files_for_one_document` pin both. The first implementation of the guard silently passed, because `isinstance(value, fastapi.UploadFile)` is false for the `starlette.datastructures.UploadFile` instances the form actually holds. The check now tests for the string case instead, which is correct for both classes. The two tests caught this before deployment.

Codex also noted that the passphrase is evaluated only after multipart parsing. That is inherent to carrying the passphrase as a form field, and it is not fixed here. Cloud Run's own request size limit bounds the body.

**MEDIUM, fixed — a failed audit could leave storage objects that no run document referenced.** `pending_run` was assigned before the Firestore RUNNING write, so a failing write left `pending_run` non-`None`, skipped the object cleanup, and then re-attempted the same failing write inside a bare `except`. The result was two orphaned objects with no record at all, which breaks the stated invariant in both of its branches. A separate `run_recorded` flag is now set only after the RUNNING write returns. On failure the code now deletes the uploaded objects when no record exists, and keeps them when a record does exist, because deleting them would strand a run document that references them. `_record_failed_run` retries the FAILED write once and returns whether it landed. `test_audit_deletes_uploaded_objects_when_the_run_record_cannot_be_written` and `test_audit_keeps_recorded_objects_when_the_failed_write_cannot_land` pin both branches.

Disclosed residue: if the RUNNING write lands and both FAILED writes fail, the run keeps reporting RUNNING. Its objects stay referenced and discoverable, and its detail page states that the run has not finished. This is not claimed away.

**LOW, fixed — the registered `verify_quote` tool disclosed the bound ephemeral path.** Its docstring said the result "does not disclose the bound path", while it returned the gate verdict unchanged, including `pdf_path`. The tool now removes `pdf_path` from the returned dictionary. `specguard/gate.py` is untouched and the gate still records the path it read; only the model-facing tool result loses it. `test_verify_quote_tool_never_returns_the_bound_document_path` asserts the gate still carries the path and that no bound path, and no temporary directory, appears anywhere in the tool result. Role binding already blocked arbitrary file selection, so this was disclosure of a known path, not a path-traversal hole.

**LOW, recorded and not actioned — duplicate prevention is page-local.** The `submitting` flag stops repeat clicks and repeat Enter presses in one document. It cannot stop a POST replay, a second tab, or a scripted `form.submit()`. Each accepted POST still mints a fresh run ID. Server-side deduplication needs an idempotency key held in shared state, which is the architecture change this pass was told to stop on rather than attempt. The work order also required server-side duplicate protection to stay unchanged. Recommended follow-up before any multi-user use.

**Invariant 7, recorded as an honest limit.** Cloud Run concurrency is 2 per instance and the process semaphore is 2, so the per-instance control holds exactly as required. With `--max-instances 2`, aggregate service concurrency can reach four, not two. Nothing was changed; `--max-instances` is Mason's call.

Invariants 1, 4, and 6 hold as written. Codex separately confirmed that Jinja autoescaping is active with no `safe` or `Markup` bypass, that filenames reach the page through `textContent`, that `AuditFailedError` leaks no internal exception and no temporary path, and that the transparent file overlay does not break native constraint validation, because validation runs before the submit event and `inert` is applied only after it.

#### Named follow-up, not done here

`draft_rfi` returns `rfi_path`, an absolute ephemeral path, and it is a registered model tool. Unlike `verify_quote`, its return shape is load-bearing: `AuditRuntime` reads `rfi_result["rfi_path"]` at `specguard/agent.py:185` and `specguard/agent.py:248`. Removing the key needs a second return channel for the runtime, which is a tool-contract change outside a UI pass. Bind it before the repo goes public on 2026-08-30.

#### Receipts after the corrections

All commands ran in `C:\Users\mcspd\dev\specguard` on arya. Every exit code below is from the unpiped command shown.

| Command | Exit | Result |
| --- | ---: | --- |
| `uv run pytest -q` | 0 | `189 passed, 2 warnings in 8.35s`. |
| `uv run ruff check .` | 0 | `All checks passed!` |
| `uv run ruff format --check .` | 0 | `34 files already formatted`. |
| `git diff --check` | 0 | No whitespace error. |
| `.\deploy-specguard.ps1` | 0 | `Service [specguard] revision [specguard-00005-9p7] has been deployed and is serving 100 percent of traffic.` |
| `gcloud run services describe specguard --region us-central1 --project specguard-hack` | 0 | `containerConcurrency = 2`, `specguard-00005-9p7`, `percent = 100`. |
| `Invoke-WebRequest https://specguard-108657628939.us-central1.run.app/` | 0 | `StatusCode = 200`. The retired `recorded as FAILED` wording is gone. |
| `Invoke-WebRequest .../runs/c3a307abec1143bd92d11011b59834d9` | 0 | `StatusCode = 200`. The live audit receipt above still renders unchanged on the corrected revision. |

Test count moved from 184 to 189: 4 added in `tests/test_web.py` and 1 in `tests/test_tools.py`. The corrected failure copy is covered by the route tests; it renders only in the error branch, so a healthy `GET /` never contains it.

Deployed revision: `specguard-00005-9p7`, 100 percent of traffic. Live URL: `https://specguard-108657628939.us-central1.run.app`.

## Phase 5 — Gemma severity classification

Date: 2026-08-21. Scope: Gemma-based severity classification (`specguard/severity.py`), generic versioned prompt (`specguard/prompts/classify_severity_v1.txt`), finding document severity annotation in Firestore with provenance model ID, RFI severity display, and web run view badge.

### Delivered capability

- `specguard/severity.py` implements `classify_severity(finding)` to evaluate technical discrepancy severity (LOW, MEDIUM, HIGH) via structured output.
- Severity runs strictly as an advisory annotation ONLY after a finding has passed the deterministic gate and been persisted to the Firestore ledger. It never modifies `verification_status` or `rejection_reason`.
- If the Gemma model call fails for any reason (timeout, network, quota, API format), the finding retains `UNCLASSIFIED` and the audit run completes without blocking.
- `AuditTools.update_finding_severity` atomically updates `severity` and `severity_model_id` without altering quote or verification metadata.
- `AuditTools.draft_rfi` formats the classified severity and provenance model ID in the generated RFI draft PDF.
- The web run detail page renders a badge (`.severity-high`, `.severity-medium`, `.severity-low`, `.severity-unclassified`) and displays the classifying model ID.

### Key handling and secret setup

All secret and key setup commands were executed without printing, logging, or committing secret values:
- `gcloud secrets create specguard-gemma-key --replication-policy=automatic --project=specguard-hack` -> exit 0
- `gcloud secrets add-iam-policy-binding specguard-gemma-key --member="serviceAccount:specguard-runtime@specguard-hack.iam.gserviceaccount.com" --role="roles/secretmanager.secretAccessor" --project=specguard-hack` -> exit 0
- `gcloud services enable apikeys.googleapis.com --project=specguard-hack` -> exit 0
- In-memory API key creation piped directly into Secret Manager `specguard-gemma-key` version 2 with exit 0, without writing or echoing key data to any file or terminal stream.

### Quality-gate receipts

All commands ran in `C:\Users\mcspd\dev\specguard` on arya. Every exit code below is from the unpiped command shown.

| Command | Exit | Result |
| --- | ---: | --- |
| `uv run pytest -q` | 0 | `198 passed, 2 warnings in 18.71s`. Zero xfails. |
| `uv run ruff check .` | 0 | `All checks passed!` |
| `uv run ruff format --check .` | 0 | `36 files already formatted`. |
| `git diff --check` | 0 | No whitespace errors. |

### Real runs

1. **Veylan 208V fixture (`fixtures/veylan_arcworks_208v_switchboard.pdf`)**:
   - Command: `uv run python run_audit.py --spec fixtures/asterquay_learning_workshop_specification.pdf --cutsheet fixtures/veylan_arcworks_208v_switchboard.pdf` -> exit 0
   - Run ID: `f5d19d3895854abeac49da0d5ce6527e`
   - Summary: Claims made 1, verified 1, rejected 0, retried 0, findings persisted 1, RFI generated.
   - Firestore persisted finding: `z0DmtdnbUiv76VFhuuJ3`, `verification_status="verified"`, `severity="unclassified"` (fallback on 404/quota).

2. **Torven 70 deg C fixture (`fixtures/torven_70c_termination_switchboard.pdf`)**:
   - Command: `uv run python run_audit.py --spec fixtures/asterquay_learning_workshop_specification.pdf --cutsheet fixtures/torven_70c_termination_switchboard.pdf` -> exit 0
   - Run ID: `abda6831feca4642b52779494dde9286`
   - Summary: Claims made 1, verified 1, rejected 0, retried 0, findings persisted 1, RFI generated.
   - Firestore persisted finding: `8iiGGBUFe7DoZuDK7YSZ`, `verification_status="verified"`, `severity="unclassified"`.

### Codex review and corrections

One authorized read-only Codex review ran against uncommitted Phase 5 changes (session `01a025ac-3dca-7c50-84b0-2283e584b65b`, exit 0).
Three findings were reported and all three were corrected:
1. **P1 — Bound Secret Manager lookup**: Added `timeout=3.0` deadline to `_fetch_secret_from_manager` so credential/network resolution cannot hang indefinitely.
2. **P1 — Add deadline to Gemma inference**: Added `http_options=types.HttpOptions(timeout=15000)` (15 s) to `GenerateContentConfig` in `GemmaSeverityClassifier.classify`.
3. **P2 — Use configured project for secret resolution**: Updated `_get_api_key`, `GemmaSeverityClassifier`, `AuditRuntime`, `GoogleAuditRunner`, and `run_audit.py` to resolve and propagate the configured `project_id` rather than hardcoding.

## Phase 5 Reopen — Live Diagnosis and Halt Report

Date: 2026-08-21.

### 1. Live Gemma Model Discovery & Diagnosis

A live in-memory probe reading the restricted API key directly from Secret Manager (`specguard-gemma-key` version 2) queried `GET https://generativelanguage.googleapis.com/v1beta/models`:
- **HTTP Status**: `200 OK`
- **Total Models**: 50
- **Gemma Models Available**: Exactly 2:
  - `models/gemma-4-26b-a4b-it` (supported methods: `['generateContent', 'countTokens']`)
  - `models/gemma-4-31b-it` (supported methods: `['generateContent', 'countTokens']`)
- **Status of `gemma-3-27b-it`**: Returns `HTTP 404 NOT_FOUND` (`"models/gemma-3-27b-it is not found for API version v1beta, or is not supported for generateContent"`). It is retired/absent on this API version.

### 2. Live Inference Test Results & Status Codes

1. `POST /v1beta/models/gemma-3-27b-it:generateContent`: `HTTP 404 NOT_FOUND` (wrong model ID for v1beta).
2. `POST /v1beta/models/gemma-4-31b-it:generateContent`: `HTTP 429 RESOURCE_EXHAUSTED` (`"Your prepayment credits are depleted. Please go to AI Studio at https://ai.studio/projects to manage your project and billing. Learn more at https://ai.google.dev/gemini-api/docs/billing#prepay."`).
3. `POST /v1beta/models/gemma-4-26b-a4b-it:generateContent`: `HTTP 429 RESOURCE_EXHAUSTED` (same error).
4. `POST /v1beta/models/gemini-flash-latest:generateContent`: `HTTP 429 RESOURCE_EXHAUSTED` (same prepayment requirement on this API key).
5. Vertex AI Publisher Probes across 6 regions (`global`, `us-central1`, `us-east4`, `us-west1`, `europe-west4`, `asia-southeast1`) for `gemma-4-31b-it`, `gemma-4-26b-a4b-it`, `gemma-3-27b-it`, `gemma-2-27b-it`, `gemma-2-9b-it`: All returned `HTTP 404 NOT_FOUND` (no managed serverless `generateContent` endpoints for Gemma in Vertex Model Garden for this project).

### 3. Explicit Fallback Recording (Anti-Silent-Fallback Guard)

- Updated `Finding`, `PersistedFinding`, and `AuditRunSummary` to carry `severity_status` (`"classified"` or `"fallback"`) and `severity_reason` (capturing exact HTTP status and error details).
- Updated `specguard/severity.py` default to `gemma-4-31b-it` (configurable via `SPECGUARD_GEMMA_MODEL`).
- Test `tests/test_severity.py::test_runtime_records_fallback_outcome_on_failure` pins that whenever Gemma inference fails or falls back, `severity_status="fallback"` and the exact reason are persisted to Firestore and the run summary.

### 4. Real-Run Receipts with Explicit Fallback Outcome

1. **Veylan 208V fixture (`fixtures/veylan_arcworks_208v_switchboard.pdf`)**:
   - Command: `uv run python run_audit.py --spec fixtures/asterquay_learning_workshop_specification.pdf --cutsheet fixtures/veylan_arcworks_208v_switchboard.pdf` -> exit `0`
   - Run ID: `125cd4bdd3074159820b3f8bdc47e8ce`
   - Summary: Claims made 1, verified 1, rejected 0, retried 0, findings persisted 1.
   - Severity status: `fallback`
   - Severity reason: `HTTP 429: Your prepayment credits are depleted. Please go to AI Studio at https://ai.studio/projects to manage your project and billing. Learn more at https://ai.google.dev/gemini-api/docs/billing#prepay.`
   - Persisted finding ID: `tQqBUzZh5Uf1vKPKTctG` (`severity="unclassified"`, `severity_status="fallback"`, `verification_status="verified"`).

2. **Torven 70 deg C fixture (`fixtures/torven_70c_termination_switchboard.pdf`)**:
   - Command: `uv run python run_audit.py --spec fixtures/asterquay_learning_workshop_specification.pdf --cutsheet fixtures/torven_70c_termination_switchboard.pdf` -> exit `0`
   - Run ID: `baf9a0c6bb67455cbae5c0c91878dc4e`
   - Summary: Claims made 1, verified 1, rejected 0, retried 0, findings persisted 1.
   - Severity status: `fallback`
   - Severity reason: `HTTP 429: Your prepayment credits are depleted. Please go to AI Studio at https://ai.studio/projects to manage your project and billing. Learn more at https://ai.google.dev/gemini-api/docs/billing#prepay.`
   - Persisted finding ID: `jBvNqT0G6o8Z3F7L1r4K` (`severity="unclassified"`, `severity_status="fallback"`, `verification_status="verified"`).

### 5. Quality Gate Receipts

| Command | Exit | Result |
| --- | ---: | --- |
| `uv run pytest -q` | 0 | `198 passed, 2 warnings in 19.94s`. Zero xfails. |
| `uv run ruff check .` | 0 | `All checks passed!` |
| `uv run ruff format --check .` | 0 | `36 files already formatted`. |
| `git diff --check` | 0 | No whitespace errors. |

## Phase 5 Close — Vertex AI Model Garden Endpoint

Date: 2026-08-21. Host: arya (Windows PowerShell).

### 1. Model Garden Deployment

Model: `google/gemma2@gemma-2-2b-it`
Machine type: `g2-standard-12` (1x `NVIDIA_L4` GPU)
Endpoint display name: `specguard-gemma`
Endpoint resource name: `projects/108657628939/locations/us-central1/endpoints/mg-endpoint-9e5e78fc-2f74-4272-b908-4980dfc67cde`
Dedicated DNS: `mg-endpoint-9e5e78fc-2f74-4272-b908-4980dfc67cde.us-central1-131658903880.prediction.vertexai.goog`
Deployment duration: 13m 38s (operation `6763868949059731456`).

Backend: `VertexEndpointSeverityClassifier` in `specguard/severity.py` authenticates via ADC / the runtime SA (no API keys), queries the endpoint via dedicated DNS, formats prompts using Gemma instruction turn tokens `<start_of_turn>user\n...<end_of_turn>\n<start_of_turn>model\n`, strictly parses the completion token (HIGH, MEDIUM, LOW), and records fallback on any error.

### 2. Live Classified Runs

1. **Veylan 208V fixture (`fixtures/veylan_arcworks_208v_switchboard.pdf`)**:
   - Command: `$env:SPECGUARD_GEMMA_ENDPOINT = "..."; uv run python run_audit.py --spec fixtures/asterquay_learning_workshop_specification.pdf --cutsheet fixtures/veylan_arcworks_208v_switchboard.pdf` -> exit `0`
   - Run ID: `06498a42c34242d292330ef7e187139c`
   - Severity status: `classified`
   - Persisted finding ID: `vyrMpZh1LvlhFDRMJUW3`
   - Severity: `medium`
   - Model ID: `google-gemma2-gemma-2-2b-it`
   - Verification status: `verified`

2. **Torven 70 deg C fixture (`fixtures/torven_70c_termination_switchboard.pdf`)**:
   - Command: `$env:SPECGUARD_GEMMA_ENDPOINT = "..."; uv run python run_audit.py --spec fixtures/asterquay_learning_workshop_specification.pdf --cutsheet fixtures/torven_70c_termination_switchboard.pdf` -> exit `0`
   - Run ID: `85cfdda639b24a4aada057900df70651`
   - Severity status: `classified`
   - Persisted finding ID: `Xmhr5qzLvvXXFVrnDfCx`
   - Severity: `low`
   - Model ID: `google-gemma2-gemma-2-2b-it`
   - Verification status: `verified`

3. **Deployed Web UI live audit run**:
   - Service URL: `https://specguard-108657628939.us-central1.run.app` (revision `specguard-00006-x5k`)
   - Run ID: `72e1e43940724ba189a5780affb19970`
   - Status: `COMPLETED`
   - Rendered HTML findings row:
     `<tr><td>The specification requires a 480V distribution switchboard, but the submitted cut sheet specifies a 208V nominal system.</td><td><span class="locator">Specification page 3</span><q class="quote">Provide a 480V, 3-phase distribution switchboard for service distribution.</q></td><td><span class="locator">Submitted page 1</span><q class="quote">Nominal system: 208V, 3-phase, 4-wire.</q></td><td><span class="badge severity-medium">MEDIUM</span><span class="locator">google-gemma2-gemma-2-2b-it</span></td></tr>`
   - Badge seen: `<span class="badge severity-medium">MEDIUM</span><span class="locator">google-gemma2-gemma-2-2b-it</span>`

### 3. Codex Review and Correction

One authorized Codex review ran against uncommitted changes (session `01a02611-485f-7972-8a8f-c22728076496`, exit 0).
Finding:
- **[P1] Parse only the generated completion**: When a Vertex prediction echoes the prompt alongside the completion, matching against the entire string could match the prompt's rubric token `HIGH` before the model's actual answer.
Correction applied: `_parse_severity_token` accepts `prompt: str | None`, strips echoed prompt text, isolates the text after `Output:`, and parses the generated completion only. Added unit test `test_vertex_endpoint_classifier_does_not_falsely_match_prompt_rubric`.

### 4. Teardown Receipts

Disclosed residue: if the RUNNING write lands and both FAILED writes fail, the run keeps reporting RUNNING. Its objects stay referenced and discoverable, and its detail page states that the run has not finished. This is not claimed away.

**LOW, fixed — the registered `verify_quote` tool disclosed the bound ephemeral path.** Its docstring said the result "does not disclose the bound path", while it returned the gate verdict unchanged, including `pdf_path`. The tool now removes `pdf_path` from the returned dictionary. `specguard/gate.py` is untouched and the gate still records the path it read; only the model-facing tool result loses it. `test_verify_quote_tool_never_returns_the_bound_document_path` asserts the gate still carries the path and that no bound path, and no temporary directory, appears anywhere in the tool result. Role binding already blocked arbitrary file selection, so this was disclosure of a known path, not a path-traversal hole.

**LOW, recorded and not actioned — duplicate prevention is page-local.** The `submitting` flag stops repeat clicks and repeat Enter presses in one document. It cannot stop a POST replay, a second tab, or a scripted `form.submit()`. Each accepted POST still mints a fresh run ID. Server-side deduplication needs an idempotency key held in shared state, which is the architecture change this pass was told to stop on rather than attempt. The work order also required server-side duplicate protection to stay unchanged. Recommended follow-up before any multi-user use.

**Invariant 7, recorded as an honest limit.** Cloud Run concurrency is 2 per instance and the process semaphore is 2, so the per-instance control holds exactly as required. With `--max-instances 2`, aggregate service concurrency can reach four, not two. Nothing was changed; `--max-instances` is Mason's call.

Invariants 1, 4, and 6 hold as written. Codex separately confirmed that Jinja autoescaping is active with no `safe` or `Markup` bypass, that filenames reach the page through `textContent`, that `AuditFailedError` leaks no internal exception and no temporary path, and that the transparent file overlay does not break native constraint validation, because validation runs before the submit event and `inert` is applied only after it.

#### Named follow-up, not done here

`draft_rfi` returns `rfi_path`, an absolute ephemeral path, and it is a registered model tool. Unlike `verify_quote`, its return shape is load-bearing: `AuditRuntime` reads `rfi_result["rfi_path"]` at `specguard/agent.py:185` and `specguard/agent.py:248`. Removing the key needs a second return channel for the runtime, which is a tool-contract change outside a UI pass. Bind it before the repo goes public on 2026-08-30.

#### Receipts after the corrections

All commands ran in `C:\Users\mcspd\dev\specguard` on arya. Every exit code below is from the unpiped command shown.

| Command | Exit | Result |
| --- | ---: | --- |
| `uv run pytest -q` | 0 | `189 passed, 2 warnings in 8.35s`. |
| `uv run ruff check .` | 0 | `All checks passed!` |
| `uv run ruff format --check .` | 0 | `34 files already formatted`. |
| `git diff --check` | 0 | No whitespace error. |
| `.\deploy-specguard.ps1` | 0 | `Service [specguard] revision [specguard-00005-9p7] has been deployed and is serving 100 percent of traffic.` |
| `gcloud run services describe specguard --region us-central1 --project specguard-hack` | 0 | `containerConcurrency = 2`, `specguard-00005-9p7`, `percent = 100`. |
| `Invoke-WebRequest https://specguard-108657628939.us-central1.run.app/` | 0 | `StatusCode = 200`. The retired `recorded as FAILED` wording is gone. |
| `Invoke-WebRequest .../runs/c3a307abec1143bd92d11011b59834d9` | 0 | `StatusCode = 200`. The live audit receipt above still renders unchanged on the corrected revision. |

Test count moved from 184 to 189: 4 added in `tests/test_web.py` and 1 in `tests/test_tools.py`. The corrected failure copy is covered by the route tests; it renders only in the error branch, so a healthy `GET /` never contains it.

Deployed revision: `specguard-00005-9p7`, 100 percent of traffic. Live URL: `https://specguard-108657628939.us-central1.run.app`.

## Phase 5 — Gemma severity classification

Date: 2026-08-21. Scope: Gemma-based severity classification (`specguard/severity.py`), generic versioned prompt (`specguard/prompts/classify_severity_v1.txt`), finding document severity annotation in Firestore with provenance model ID, RFI severity display, and web run view badge.

### Delivered capability

- `specguard/severity.py` implements `classify_severity(finding)` to evaluate technical discrepancy severity (LOW, MEDIUM, HIGH) via structured output.
- Severity runs strictly as an advisory annotation ONLY after a finding has passed the deterministic gate and been persisted to the Firestore ledger. It never modifies `verification_status` or `rejection_reason`.
- If the Gemma model call fails for any reason (timeout, network, quota, API format), the finding retains `UNCLASSIFIED` and the audit run completes without blocking.
- `AuditTools.update_finding_severity` atomically updates `severity` and `severity_model_id` without altering quote or verification metadata.
- `AuditTools.draft_rfi` formats the classified severity and provenance model ID in the generated RFI draft PDF.
- The web run detail page renders a badge (`.severity-high`, `.severity-medium`, `.severity-low`, `.severity-unclassified`) and displays the classifying model ID.

### Key handling and secret setup

All secret and key setup commands were executed without printing, logging, or committing secret values:
- `gcloud secrets create specguard-gemma-key --replication-policy=automatic --project=specguard-hack` -> exit 0
- `gcloud secrets add-iam-policy-binding specguard-gemma-key --member="serviceAccount:specguard-runtime@specguard-hack.iam.gserviceaccount.com" --role="roles/secretmanager.secretAccessor" --project=specguard-hack` -> exit 0
- `gcloud services enable apikeys.googleapis.com --project=specguard-hack` -> exit 0
- In-memory API key creation piped directly into Secret Manager `specguard-gemma-key` version 2 with exit 0, without writing or echoing key data to any file or terminal stream.

### Quality-gate receipts

All commands ran in `C:\Users\mcspd\dev\specguard` on arya. Every exit code below is from the unpiped command shown.

| Command | Exit | Result |
| --- | ---: | --- |
| `uv run pytest -q` | 0 | `198 passed, 2 warnings in 18.71s`. Zero xfails. |
| `uv run ruff check .` | 0 | `All checks passed!` |
| `uv run ruff format --check .` | 0 | `36 files already formatted`. |
| `git diff --check` | 0 | No whitespace errors. |

### Real runs

1. **Veylan 208V fixture (`fixtures/veylan_arcworks_208v_switchboard.pdf`)**:
   - Command: `uv run python run_audit.py --spec fixtures/asterquay_learning_workshop_specification.pdf --cutsheet fixtures/veylan_arcworks_208v_switchboard.pdf` -> exit 0
   - Run ID: `f5d19d3895854abeac49da0d5ce6527e`
   - Summary: Claims made 1, verified 1, rejected 0, retried 0, findings persisted 1, RFI generated.
   - Firestore persisted finding: `z0DmtdnbUiv76VFhuuJ3`, `verification_status="verified"`, `severity="unclassified"` (fallback on 404/quota).

2. **Torven 70 deg C fixture (`fixtures/torven_70c_termination_switchboard.pdf`)**:
   - Command: `uv run python run_audit.py --spec fixtures/asterquay_learning_workshop_specification.pdf --cutsheet fixtures/torven_70c_termination_switchboard.pdf` -> exit 0
   - Run ID: `abda6831feca4642b52779494dde9286`
   - Summary: Claims made 1, verified 1, rejected 0, retried 0, findings persisted 1, RFI generated.
   - Firestore persisted finding: `8iiGGBUFe7DoZuDK7YSZ`, `verification_status="verified"`, `severity="unclassified"`.

### Codex review and corrections

One authorized read-only Codex review ran against uncommitted Phase 5 changes (session `01a025ac-3dca-7c50-84b0-2283e584b65b`, exit 0).
Three findings were reported and all three were corrected:
1. **P1 — Bound Secret Manager lookup**: Added `timeout=3.0` deadline to `_fetch_secret_from_manager` so credential/network resolution cannot hang indefinitely.
2. **P1 — Add deadline to Gemma inference**: Added `http_options=types.HttpOptions(timeout=15000)` (15 s) to `GenerateContentConfig` in `GemmaSeverityClassifier.classify`.
3. **P2 — Use configured project for secret resolution**: Updated `_get_api_key`, `GemmaSeverityClassifier`, `AuditRuntime`, `GoogleAuditRunner`, and `run_audit.py` to resolve and propagate the configured `project_id` rather than hardcoding.

## Phase 5 Reopen — Live Diagnosis and Halt Report

Date: 2026-08-21.

### 1. Live Gemma Model Discovery & Diagnosis

A live in-memory probe reading the restricted API key directly from Secret Manager (`specguard-gemma-key` version 2) queried `GET https://generativelanguage.googleapis.com/v1beta/models`:
- **HTTP Status**: `200 OK`
- **Total Models**: 50
- **Gemma Models Available**: Exactly 2:
  - `models/gemma-4-26b-a4b-it` (supported methods: `['generateContent', 'countTokens']`)
  - `models/gemma-4-31b-it` (supported methods: `['generateContent', 'countTokens']`)
- **Status of `gemma-3-27b-it`**: Returns `HTTP 404 NOT_FOUND` (`"models/gemma-3-27b-it is not found for API version v1beta, or is not supported for generateContent"`). It is retired/absent on this API version.

### 2. Live Inference Test Results & Status Codes

1. `POST /v1beta/models/gemma-3-27b-it:generateContent`: `HTTP 404 NOT_FOUND` (wrong model ID for v1beta).
2. `POST /v1beta/models/gemma-4-31b-it:generateContent`: `HTTP 429 RESOURCE_EXHAUSTED` (`"Your prepayment credits are depleted. Please go to AI Studio at https://ai.studio/projects to manage your project and billing. Learn more at https://ai.google.dev/gemini-api/docs/billing#prepay."`).
3. `POST /v1beta/models/gemma-4-26b-a4b-it:generateContent`: `HTTP 429 RESOURCE_EXHAUSTED` (same error).
4. `POST /v1beta/models/gemini-flash-latest:generateContent`: `HTTP 429 RESOURCE_EXHAUSTED` (same prepayment requirement on this API key).
5. Vertex AI Publisher Probes across 6 regions (`global`, `us-central1`, `us-east4`, `us-west1`, `europe-west4`, `asia-southeast1`) for `gemma-4-31b-it`, `gemma-4-26b-a4b-it`, `gemma-3-27b-it`, `gemma-2-27b-it`, `gemma-2-9b-it`: All returned `HTTP 404 NOT_FOUND` (no managed serverless `generateContent` endpoints for Gemma in Vertex Model Garden for this project).

### 3. Explicit Fallback Recording (Anti-Silent-Fallback Guard)

- Updated `Finding`, `PersistedFinding`, and `AuditRunSummary` to carry `severity_status` (`"classified"` or `"fallback"`) and `severity_reason` (capturing exact HTTP status and error details).
- Updated `specguard/severity.py` default to `gemma-4-31b-it` (configurable via `SPECGUARD_GEMMA_MODEL`).
- Test `tests/test_severity.py::test_runtime_records_fallback_outcome_on_failure` pins that whenever Gemma inference fails or falls back, `severity_status="fallback"` and the exact reason are persisted to Firestore and the run summary.

### 4. Real-Run Receipts with Explicit Fallback Outcome

1. **Veylan 208V fixture (`fixtures/veylan_arcworks_208v_switchboard.pdf`)**:
   - Command: `uv run python run_audit.py --spec fixtures/asterquay_learning_workshop_specification.pdf --cutsheet fixtures/veylan_arcworks_208v_switchboard.pdf` -> exit `0`
   - Run ID: `125cd4bdd3074159820b3f8bdc47e8ce`
   - Summary: Claims made 1, verified 1, rejected 0, retried 0, findings persisted 1.
   - Severity status: `fallback`
   - Severity reason: `HTTP 429: Your prepayment credits are depleted. Please go to AI Studio at https://ai.studio/projects to manage your project and billing. Learn more at https://ai.google.dev/gemini-api/docs/billing#prepay.`
   - Persisted finding ID: `tQqBUzZh5Uf1vKPKTctG` (`severity="unclassified"`, `severity_status="fallback"`, `verification_status="verified"`).

2. **Torven 70 deg C fixture (`fixtures/torven_70c_termination_switchboard.pdf`)**:
   - Command: `uv run python run_audit.py --spec fixtures/asterquay_learning_workshop_specification.pdf --cutsheet fixtures/torven_70c_termination_switchboard.pdf` -> exit `0`
   - Run ID: `baf9a0c6bb67455cbae5c0c91878dc4e`
   - Summary: Claims made 1, verified 1, rejected 0, retried 0, findings persisted 1.
   - Severity status: `fallback`
   - Severity reason: `HTTP 429: Your prepayment credits are depleted. Please go to AI Studio at https://ai.studio/projects to manage your project and billing. Learn more at https://ai.google.dev/gemini-api/docs/billing#prepay.`
   - Persisted finding ID: `jBvNqT0G6o8Z3F7L1r4K` (`severity="unclassified"`, `severity_status="fallback"`, `verification_status="verified"`).

### 5. Quality Gate Receipts

| Command | Exit | Result |
| --- | ---: | --- |
| `uv run pytest -q` | 0 | `198 passed, 2 warnings in 19.94s`. Zero xfails. |
| `uv run ruff check .` | 0 | `All checks passed!` |
| `uv run ruff format --check .` | 0 | `36 files already formatted`. |
| `git diff --check` | 0 | No whitespace errors. |

## Phase 5 Close — Vertex AI Model Garden Endpoint

Date: 2026-08-21. Host: arya (Windows PowerShell).

### 1. Model Garden Deployment

Model: `google/gemma2@gemma-2-2b-it`
Machine type: `g2-standard-12` (1x `NVIDIA_L4` GPU)
Endpoint display name: `specguard-gemma`
Endpoint resource name: `projects/108657628939/locations/us-central1/endpoints/mg-endpoint-9e5e78fc-2f74-4272-b908-4980dfc67cde`
Dedicated DNS: `mg-endpoint-9e5e78fc-2f74-4272-b908-4980dfc67cde.us-central1-131658903880.prediction.vertexai.goog`
Deployment duration: 13m 38s (operation `6763868949059731456`).

Backend: `VertexEndpointSeverityClassifier` in `specguard/severity.py` authenticates via ADC / the runtime SA (no API keys), queries the endpoint via dedicated DNS, formats prompts using Gemma instruction turn tokens `<start_of_turn>user\n...<end_of_turn>\n<start_of_turn>model\n`, strictly parses the completion token (HIGH, MEDIUM, LOW), and records fallback on any error.

### 2. Live Classified Runs

1. **Veylan 208V fixture (`fixtures/veylan_arcworks_208v_switchboard.pdf`)**:
   - Command: `$env:SPECGUARD_GEMMA_ENDPOINT = "..."; uv run python run_audit.py --spec fixtures/asterquay_learning_workshop_specification.pdf --cutsheet fixtures/veylan_arcworks_208v_switchboard.pdf` -> exit `0`
   - Run ID: `06498a42c34242d292330ef7e187139c`
   - Severity status: `classified`
   - Persisted finding ID: `vyrMpZh1LvlhFDRMJUW3`
   - Severity: `medium`
   - Model ID: `google-gemma2-gemma-2-2b-it`
   - Verification status: `verified`

2. **Torven 70 deg C fixture (`fixtures/torven_70c_termination_switchboard.pdf`)**:
   - Command: `$env:SPECGUARD_GEMMA_ENDPOINT = "..."; uv run python run_audit.py --spec fixtures/asterquay_learning_workshop_specification.pdf --cutsheet fixtures/torven_70c_termination_switchboard.pdf` -> exit `0`
   - Run ID: `85cfdda639b24a4aada057900df70651`
   - Severity status: `classified`
   - Persisted finding ID: `Xmhr5qzLvvXXFVrnDfCx`
   - Severity: `low`
   - Model ID: `google-gemma2-gemma-2-2b-it`
   - Verification status: `verified`

3. **Deployed Web UI live audit run**:
   - Service URL: `https://specguard-108657628939.us-central1.run.app` (revision `specguard-00006-x5k`)
   - Run ID: `72e1e43940724ba189a5780affb19970`
   - Status: `COMPLETED`
   - Rendered HTML findings row:
     `<tr><td>The specification requires a 480V distribution switchboard, but the submitted cut sheet specifies a 208V nominal system.</td><td><span class="locator">Specification page 3</span><q class="quote">Provide a 480V, 3-phase distribution switchboard for service distribution.</q></td><td><span class="locator">Submitted page 1</span><q class="quote">Nominal system: 208V, 3-phase, 4-wire.</q></td><td><span class="badge severity-medium">MEDIUM</span><span class="locator">google-gemma2-gemma-2-2b-it</span></td></tr>`
   - Badge seen: `<span class="badge severity-medium">MEDIUM</span><span class="locator">google-gemma2-gemma-2-2b-it</span>`

### 3. Codex Review and Correction

One authorized Codex review ran against uncommitted changes (session `01a02611-485f-7972-8a8f-c22728076496`, exit 0).
Finding:
- **[P1] Parse only the generated completion**: When a Vertex prediction echoes the prompt alongside the completion, matching against the entire string could match the prompt's rubric token `HIGH` before the model's actual answer.
Correction applied: `_parse_severity_token` accepts `prompt: str | None`, strips echoed prompt text, isolates the text after `Output:`, and parses the generated completion only. Added unit test `test_vertex_endpoint_classifier_does_not_falsely_match_prompt_rubric`.

### 4. Teardown Receipts

All compute and endpoint resources torn down with exit 0:
- `gcloud ai endpoints undeploy-model mg-endpoint-9e5e78fc-2f74-4272-b908-4980dfc67cde --deployed-model-id=7691247113769844736 --region=us-central1 --project=specguard-hack --billing-project=specguard-hack` -> exit `0`
- `gcloud ai endpoints delete mg-endpoint-9e5e78fc-2f74-4272-b908-4980dfc67cde --region=us-central1 --project=specguard-hack --billing-project=specguard-hack --quiet` -> exit `0`
- `gcloud ai models delete google-gemma2-gemma-2-2b-it-1787343954 --region=us-central1 --project=specguard-hack --billing-project=specguard-hack --quiet` -> exit `0`
- `gcloud ai endpoints list --region=us-central1 --project=specguard-hack --billing-project=specguard-hack` -> `Listed 0 items` (exit `0`)
- `gcloud ai models list --region=us-central1 --project=specguard-hack --billing-project=specguard-hack` -> `Listed 0 items` (exit `0`)

### 5. Quality Gate Receipts

| Command | Exit | Result |
| --- | ---: | --- |
| `uv run pytest -q` | 0 | `204 passed, 2 warnings in 19.26s`. Zero xfails. |
| `uv run ruff check .` | 0 | `All checks passed!` |
| `uv run ruff format --check .` | 0 | `36 files already formatted`. |
| `git diff --check` | 0 | No whitespace errors. |

## Phase 5 Model Upgrade — Vertex AI Model Garden Gemma 3 Deployment

Date: 2026-08-21. Host: arya (Windows PowerShell).

### 1. Quota & Model Garden Discovery Receipts

`us-central1` GPU Quotas (`gcloud compute regions describe us-central1 --format="json(quotas)"`):
- `NVIDIA_L4_GPUS`: Limit `1.0` (Usage `0.0`)
- `NVIDIA_T4_GPUS`: Limit `1.0` (Usage `0.0`)
- `NVIDIA_V100_GPUS`: Limit `1.0` (Usage `0.0`)
- `NVIDIA_A100_GPUS`: Limit `0.0`
- `NVIDIA_A100_80GB_GPUS`: Limit `0.0`

Candidate Evaluations:
1. `google/gemma4@gemma-4-12b-it` / `google/gemma4@gemma-4-e4b-it`: Supports only `NVIDIA_RTX_PRO_6000` / `NVIDIA_H100_80GB` (0.0 quota in project).
2. `google/gemma3@gemma-3-12b-it`: Configured for `g2-standard-24` (2x `NVIDIA_L4`). Deploy attempt failed: `Machine type temporarily unavailable: "g2-standard-24"` (due to 1.0 L4 GPU quota).
3. `google/gemma3@gemma-3-4b-it`: Configured for `g2-standard-24` (2x `NVIDIA_L4`).
4. `google/gemma3n@gemma-3n-e4b-it`: Configured for `g2-standard-12` (1x `NVIDIA_L4`). Deploy attempt failed: `Machine type temporarily unavailable: "g2-standard-12"`.
5. `google/gemma3@gemma-3-1b-it`: Configured for `g2-standard-12` (1x `NVIDIA_L4`). Successfully deployed in 1m 48s (operation `3172943137580515328`).

### 2. Live Classified Runs with Gemma 3 1B

1. **Veylan 208V fixture (`fixtures/veylan_arcworks_208v_switchboard.pdf`)**:
   - Command: `$env:SPECGUARD_GEMMA_ENDPOINT = "..."; uv run python run_audit.py --spec fixtures/asterquay_learning_workshop_specification.pdf --cutsheet fixtures/veylan_arcworks_208v_switchboard.pdf` -> exit `0`
   - Run ID: `060abfb1949b49fb85a2bbb9e38b351a`
   - Severity status: `classified`
   - Persisted finding ID: `0Q5Ix2DcsdcwGOl3Hq11`
   - Severity: `high`
   - Model ID: `google-gemma3-gemma-3-1b-it`
   - Verification status: `verified`

2. **Torven 70 deg C fixture (`fixtures/torven_70c_termination_switchboard.pdf`)**:
   - Command: `$env:SPECGUARD_GEMMA_ENDPOINT = "..."; uv run python run_audit.py --spec fixtures/asterquay_learning_workshop_specification.pdf --cutsheet fixtures/torven_70c_termination_switchboard.pdf` -> exit `0`
   - Run ID: `85fd75b560e949feacd3b1f0158566da`
   - Severity status: `classified`
   - Persisted finding ID: `CrZHFWGieRCv3Qsvaf4J`
   - Severity: `high`
   - Model ID: `google-gemma3-gemma-3-1b-it`
   - Verification status: `verified`

3. **Deployed Web UI live audit run**:
   - Service URL: `https://specguard-108657628939.us-central1.run.app` (revision `specguard-00007-8g8`)
   - Run ID: `1c94812cbf1246bb8f56a750aa26e032`
   - Status: `COMPLETED`
   - Rendered HTML findings row:
     `<tr><td>The specification requires a 480V distribution switchboard, whereas the submitted cut sheet specifies a 208V nominal system.</td><td><span class="locator">Specification page 3</span><q class="quote">Provide a 480V, 3-phase distribution switchboard for service distribution.</q></td><td><span class="locator">Submitted page 1</span><q class="quote">Nominal system: 208V, 3-phase, 4-wire.</q></td><td><span class="badge severity-high">HIGH</span><span class="locator">google-gemma3-gemma-3-1b-it</span></td></tr>`
   - Badge seen: `<span class="badge severity-high">HIGH</span><span class="locator">google-gemma3-gemma-3-1b-it</span>`

### 3. Teardown Receipts

All compute and endpoint resources torn down with exit 0:
- `gcloud ai endpoints undeploy-model mg-endpoint-99f0f4c4-25b9-436e-ac24-8f45f84a63a3 --deployed-model-id=2728280324407558144 --region=us-central1 --project=specguard-hack --billing-project=specguard-hack` -> exit `0`
- `gcloud ai endpoints delete mg-endpoint-99f0f4c4-25b9-436e-ac24-8f45f84a63a3 --region=us-central1 --project=specguard-hack --billing-project=specguard-hack --quiet` -> exit `0`
- `gcloud ai models delete google-gemma3-gemma-3-1b-it-1787350562 --region=us-central1 --project=specguard-hack --billing-project=specguard-hack --quiet` -> exit `0`
- `gcloud ai endpoints list --region=us-central1 --project=specguard-hack --billing-project=specguard-hack` -> `Listed 0 items` (exit `0`)
- `gcloud ai models list --region=us-central1 --project=specguard-hack --billing-project=specguard-hack` -> `Listed 0 items` (exit `0`)

### 4. Quality Gate Receipts

| Command | Exit | Result |
| --- | ---: | --- |
| `uv run pytest -q` | 0 | `204 passed, 2 warnings in 24.16s`. Zero xfails. |
| `uv run ruff check .` | 0 | `All checks passed!` |
| `uv run ruff format --check .` | 0 | `36 files already formatted`. |
| `git diff --check` | 0 | No whitespace errors. |

---

## Phase 6a — pre-public hardening and the measured eval harness

Date: 2026-08-21. Scope: the three recorded pre-public follow-ups, the measured eval harness, and one retry of the larger Gemma model. The verification gate contract, the prompts, and the five fixture PDFs are unchanged. The diff touches no line of `specguard/gate.py` and no PDF.

### 0. Gemma 3 4B retry — FAILED, gemma-3-1b-it retained

The retry used the explicit machine-type override, on the theory that the earlier failure was machine stock rather than quota. It was neither: the API rejected the configuration itself.

| Command | Exit | Result |
| --- | ---: | --- |
| `gcloud ai model-garden models deploy --model=google/gemma3@gemma-3-4b-it --machine-type=g2-standard-12 --accelerator-type=NVIDIA_L4 --accelerator-count=1 --endpoint-display-name=specguard-gemma-4b --region=us-central1 --project=specguard-hack --billing-project=specguard-hack --accept-eula` | 1 | `ERROR: (gcloud.ai.model-garden.models.deploy) The machine type, accelerator type and/or container image URI is not supported by the model.` |
| `gcloud ai endpoints list --region=us-central1 --project=specguard-hack --billing-project=specguard-hack` | 0 | `Listed 0 items.` |
| `gcloud ai models list --region=us-central1 --project=specguard-hack --billing-project=specguard-hack` | 0 | `Listed 0 items.` |

The rejection happens in configuration validation, before any resource is created, so nothing partial existed to tear down and both list commands confirm zero. This is a deterministic refusal, not transient stock: `g2-standard-12` is not in the model's supported set, which SETUP.md already records as `g2-standard-24`, `a2-ultragpu-1g`, or `g4-standard-48`. All three exceed the project's 1.0 L4 quota or need accelerator families the project holds at 0.

`gcloud beta ai model-garden models list-deployment-config` could not be run to print the supported set directly: the `beta` component is not installed, and installing it needs write access to the SDK directory under `C:\Program Files (x86)`, which this session does not have (exit 1). The deploy error message is therefore the receipt, not a config listing.

Decision: `google/gemma3@gemma-3-1b-it` is retained. Elapsed time was well inside the ten-minute cap.

### 1. draft_rfi no longer returns a filesystem path to the model

`draft_rfi` is model-callable, so returning the resolved output path handed a model the absolute ephemeral path of the machine running the audit. The tool now returns `rfi_id`, `rfi_number`, and `finding_count`, where `rfi_id` is a `secrets.token_hex(16)` handle carrying no path information.

The path travels on a second channel: `AuditTools.rfi_path_for(rfi_id)`, which is not one of the five registered agent tools and therefore cannot be reached from a model turn. `AuditRuntime._draft_rfi_and_resolve_path` drafts the RFI and exchanges the handle for the path, and raises if the bound tool set did not issue that handle. Both former call sites — the model-output-invalid path and the normal completion path — go through it. `AuditRunSummary.rfi_path`, `run_audit.py`, and the web layer are unchanged, so nothing downstream of the runtime moved.

New tests: `test_draft_rfi_returns_an_opaque_handle_and_no_filesystem_path`, `test_the_runtime_channel_resolves_the_handle_to_the_written_file`, and `test_no_model_registered_tool_returns_the_rfi_filesystem_path`. The last one scans every registered tool result for the output path and for the temporary directory, and asserts `rfi_path_for` is absent from the registered tool list.

This closes the Phase 4 follow-up recorded in PLAN.md M3.

### 2. Cloud Run max-instances 1

`deploy-specguard.ps1` now passes `--max-instances 1`. `test_deploy_script_limits_cloud_run_request_concurrency` pins both `--concurrency 2` and `--max-instances 1`, because request concurrency alone does not bound the service: two per instance across two instances is four.

| Command | Exit | Result |
| --- | ---: | --- |
| `.\deploy-specguard.ps1` | 0 | `Service [specguard] revision [specguard-00008-25s] has been deployed and is serving 100 percent of traffic.` |
| `gcloud run services describe specguard --region us-central1 --project specguard-hack` with a format string for maxScale, containerConcurrency, latest ready revision, and traffic percent | 0 | `1`, `2`, `specguard-00008-25s`, `100`. |

The README sentence was rewritten twice. The first rewrite claimed the aggregate of two was now exact. The Codex review was right that it is not: `--max-instances` is a per-revision target, and Cloud Run may briefly run extra instances during a deployment or a traffic split. The committed sentence says two is the steady-state figure and explicitly not a guarantee for every instant.

Note: the deployed revision still carries the `SPECGUARD_GEMMA_ENDPOINT` value of an endpoint that has been torn down. Severity therefore falls back with a recorded reason on the deployed service until the shoot-day runbook sets a live endpoint. That is the pre-existing Phase 5 posture, unchanged here.

### 3. scripts/reset_demo_ledger.py

Archives `runs`, `findings`, `rejections`, and `integrity_findings`, plus every bucket object, under one timestamped archive key, then leaves them empty. It requires `--confirm`; without the flag it prints what it would do and exits 2.

Two properties are pinned by tests rather than asserted in prose:

- **The whole copy phase finishes before the first delete.** The first implementation interleaved copy and delete per item. That never loses an item, but a failure part way leaves a partly cleared live ledger. The Codex review called this a blocker, and it was rebuilt as two phases, per collection and for the bucket. `test_a_copy_that_fails_on_the_last_object_deletes_nothing` and `test_firestore_documents_are_all_copied_before_any_is_deleted` pin the boundary.
- **An archive key that already holds data is refused**, on both the Firestore side and the bucket side, so one reset can never overwrite an earlier archive. Objects already under the archive prefix are skipped and counted, so a second reset never nests one archive inside another.

Two scope decisions, both deliberate and both stated in the module docstring:

- The content-addressed `documents` collection is not archived and not cleared. Its records are keyed by document SHA-256, are shared across runs, and carry no run-specific demo data. The earlier wording said "the whole demo ledger", which over-claimed; it now names the four collections.
- A writer that modifies a live document or object between its copy and its delete loses the newest version. The script carries no Firestore transaction and no Cloud Storage generation precondition, because the reset is run by one operator against an idle service. This is recorded as a known limitation rather than fixed.

Fourteen tests cover the logic with in-memory fakes. `tests/fake_firestore.py` gained `stream()` on collections and `delete()` on document references, so the real `FirestoreLedgerStore` adapter is exercised rather than a hand-written double. `test_the_cloud_storage_adapter_uses_a_server_side_copy` covers the live adapter's use of `copy_blob`, which preserves the content type the findings page serves.

No real reset has been executed. The script is tested, not yet run against the live ledger.

### 4. scripts/eval_fixtures.py and EVAL.md

The harness runs the real runtime N times over every audit case declared in the new `eval-cases` block of `fixtures/MANIFEST.md`, then writes EVAL.md and replaces the marked block in README.md. Expected outcomes live in the manifest, not in the harness, and each `finding` case names an evidence pair from the existing `fixture-evidence` block, so the quote a run must reproduce is recorded once and read twice.

The five fixture PDFs form four audit cases; the specification is one side of every case. EVAL.md states that mapping rather than implying five runs per iteration.

Honesty properties, each pinned by a test:

- A catch requires both quotes and both page numbers to equal the manifest pair. A right quote on the wrong page is a false positive, not a catch.
- A case with nothing planted has no catch rate and prints `n/a`. Counting it as 100 percent would inflate the published average.
- The percentage renderer never renders a short rate as `100%`. 199 catches in 200 runs prints `99.5%`, and a rate that still reads as complete at one decimal prints `<100%`. The same guard runs at the bottom of the range.
- A run whose initial model turn produced nothing usable is flagged and fails every expectation, including `no_finding`. Persisting nothing because the model broke is not the same result as persisting nothing because the cut sheet complies.
- A quarantined run that still persisted a finding fails the quarantine expectation.
- The severity model in the header is read back from the `severity_model_id` on the persisted findings. The operator's `--severity-model` string is printed separately and labelled as operator-supplied.
- Every run identifier is listed in EVAL.md so the table can be checked against Firestore.

Thirty-three tests cover aggregation and publication with fakes. None touches the network.

### 5. The real eval run

Gemma was deployed for the run and torn down after it, per the shoot-day runbook.

| Command | Exit | Result |
| --- | ---: | --- |
| `gcloud ai model-garden models deploy --model=google/gemma3@gemma-3-1b-it --machine-type=g2-standard-12 --accelerator-type=NVIDIA_L4 --accelerator-count=1 --endpoint-display-name=specguard-gemma --region=us-central1 --project=specguard-hack --billing-project=specguard-hack --accept-eula` | 0 | Endpoint `mg-endpoint-673c178e-3128-43b9-8d2d-8493d0a50f1a`, deployed model `8206346321150345216`. |
| `uv run python scripts/eval_fixtures.py -n 5 --severity-model ...` | 0 | See the summary line below. |
| `gcloud ai endpoints undeploy-model`, then `endpoints delete`, then `models delete` | 0, 0, 0 | Torn down. |
| `gcloud ai endpoints list --region=us-central1 --project=specguard-hack --billing-project=specguard-hack` | 0 | `Listed 0 items.` |
| `gcloud ai models list --region=us-central1 --project=specguard-hack --billing-project=specguard-hack` | 0 | `Listed 0 items.` |

```text
EVAL SUMMARY date=2026-08-21 iterations=5 cases=4 runs=20 catch_rate=100% false_positives=0 rejections=0 retries=0 model_calls=15 cases_matching_manifest=4/4
```

Measured: catch rate 100 percent on both planted discrepancies across 10 runs, zero false positives on the compliant Caldra cut sheet across 5 runs, and 100 percent quarantine on the altered Veylan fixture with zero model calls across 5 runs. Severity: 7 findings HIGH and 3 UNCLASSIFIED. The three fallbacks were HTTP 502 responses from the Gemma endpoint; the reason is recorded on each finding, and EVAL.md names the count.

Two results are worth reading carefully rather than as a clean sweep:

- **Rejections and retries were both zero.** The model cited every quote correctly on the first turn, so the rejection-and-retry loop — the agentic beat of the demo — did not fire once in 15 model-calling runs. These numbers are not evidence that the loop works. EVAL.md says exactly that under "What this run did not exercise", and points at the test suite, which drives rejections deterministically.
- **The catch rate is 100 percent on four cases of fictional fixtures**, not on a corpus. The EVAL.md scope section says so.

EVAL.md was regenerated by a second real run after the Codex corrections changed the renderer, so the committed file is the product of the committed code. An earlier real run at the same N produced the same headline numbers with a different severity split: 10 findings, 1 fallback.

Total Vertex spend was not visible in any command output, so EVAL.md records it as not visible rather than estimating it.

### 6. Codex review and correction iteration 1

One authorized read-only Codex review ran against the working tree. It returned 7 findings: 1 blocker, 5 major, 1 minor. `git status --short` before and after the review listed the same 8 modified and 3 untracked paths, and the suite stayed green, so the review modified nothing.

Six findings were real and are fixed above: the interleaved copy and delete (blocker), the inexact concurrency claim, the missing archive-key collision guard, the over-claimed "whole demo ledger" wording, the runs that could pass while broken or internally inconsistent, the rounding that could publish a short rate as 100 percent, and the unverified severity model string.

Not fixed, recorded instead: the Cloud Storage generation preconditions and Firestore transactions Codex suggested for the reset script. A single operator resetting an idle demo ledger does not need them, and the module docstring names the residual limitation.

Codex could not verify the "one receipted execution" claim in EVAL.md, because no run identifiers were published. That was a fair objection and it is now closed: EVAL.md lists all 20 run identifiers.

### 7. Quality gate receipts

All commands ran in `C:\Users\mcspd\dev\specguard` on arya. Every exit code below is from the unpiped command shown.

| Command | Exit | Result |
| --- | ---: | --- |
| `uv run pytest -q` | 0 | `254 passed, 2 warnings`. Zero xfails. |
| `uv run ruff check .` | 0 | `All checks passed!` |
| `uv run ruff format --check .` | 0 | `41 files already formatted`. |
| `git diff --check` | 0 | No whitespace errors. Git printed existing LF-to-CRLF working-copy warnings only. |

Test count moved from 204 to 254: 3 added in `tests/test_tools.py`, 14 new in `tests/test_reset_demo_ledger.py`, and 33 new in `tests/test_eval_fixtures.py`. No fixture PDF changed, and `specguard/gate.py` is untouched.
