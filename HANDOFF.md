# SpecGuard Phase 1 — handoff

Date: 2026-08-20. Scope: verification gate, data schema, test suite. Pure local Python. No GCP call, no ADK, no network dependency at test time.

## Quality-gate receipts

All commands run in `C:\Users\mcspd\dev\specguard` on arya. Exit codes are unpiped.

| Command | Exit | Result |
| --- | --- | --- |
| `uv sync` | 0 | 15 packages installed |
| `uv run pytest -q` | 0 | `68 passed in 0.72s` |
| `uv run ruff check .` | 0 | `All checks passed!` |
| `uv run ruff format --check .` | 0 | `10 files already formatted` |
| `gh repo create mcs-eng/specguard --private --source=. --remote=origin --push` | 0 | `https://github.com/mcs-eng/specguard`, `* [new branch] HEAD -> main` |

Test count by stage: 36 at the first commit `ca48ab9`, 38 after the NFKC superscript limitation was pinned, 55 after Codex review iteration 1, 68 after the adversarial review iteration 2.

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
- All eight commits and their dates survive, so the repo still shows the real build progression through the contest window.

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

68 tests. Every case the work order names is present, and every known-bad case asserts the machine-readable rejection reason, not only the boolean.

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
