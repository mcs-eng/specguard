# SpecGuard Phase 1 — handoff

Date: 2026-08-20. Scope: verification gate, data schema, test suite. Pure local Python. No GCP call, no ADK, no network dependency at test time.

## Quality-gate receipts

All commands run in `C:\Users\mcspd\dev\specguard` on arya. Exit codes are unpiped.

| Command | Exit | Result |
| --- | --- | --- |
| `uv sync` | 0 | 15 packages installed |
| `uv run pytest -q` | 0 | `55 passed in 0.57s` |
| `uv run ruff check .` | 0 | `All checks passed!` |
| `uv run ruff format --check .` | 0 | `12 files already formatted` |
| `gh repo create mcs-eng/specguard --private --source=. --remote=origin --push` | 0 | `https://github.com/mcs-eng/specguard`, `* [new branch] HEAD -> main` |

Test count by stage: 36 at the first commit `ca48ab9`, 38 after the NFKC superscript limitation was pinned, 55 after the Codex review corrections.

## GitHub repository

Mason authorized repo creation mid-session, superseding the work order's "no remote and no push" line.

- Remote: `https://github.com/mcs-eng/specguard`, **private**, owner `mcs-eng`, branch `main`.
- Private matches the settled plan: private during build, public 2026-08-30 before submission.
- No AI attribution in any commit message or in the repo description.

**Blocker for the 2026-08-30 public flip.** `SETUP.md` is committed and contains the billing account ID `[redacted-billing-account]`, the GCP project number, the runtime service account email, the budget id, and the personal address `[redacted-address]`. None of this is a credential and all of it is Mason's own, so it is fine in a private repo under the knowledge boundary. It must not go public. Fold this into the 08-28 claims audit, which the plan already designates as the pre-publish sweep.

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

### 1. Soft hyphen strips its trailing whitespace

The work order states "strip soft hyphens (U+00AD)". Read literally, a page reading `trans\u00ad\nformer` normalizes to `trans former` — the line break survives as a space and the required known-good case ("quote split across a soft-hyphen line break") can never pass.

Implemented rule: remove each soft hyphen **together with any whitespace that immediately follows it**, before the whitespace-collapse step. This is the only reading under which the mandated known-good case is provable. It does not loosen the match: a *printed* hyphen (U+002D) at a line break is left in place and still causes a rejection. `test_visible_hyphen_is_not_a_soft_hyphen` proves that boundary.

Order of operations in `normalize()`: NFKC, then soft-hyphen removal, then casefold, then whitespace collapse, then strip.

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

55 tests. Every case the work order names is present, and every known-bad case asserts the machine-readable rejection reason, not only the boolean.

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

An adversarial review (`task-mt1xluh7-hi5jc2`) was requested by Mason mid-session and run against commit `3963f89`, which predates the boundary fix above. Its findings and the responses are recorded below.

<!-- CODEX-ADVERSARIAL -->

## Open questions for Phase 3

1. **Retry feedback shape.** `VerificationResult.normalized_quote` is the only field carrying the gate's view of the quote back to the agent. Phase 3 must decide whether the bounced-back rejection also shows the agent the normalized page text, or a near-miss window from it. Showing the page text makes the retry easier but risks the agent pattern-matching a quote out of the feedback rather than out of the document.
2. **Multi-quote findings.** `Finding.quotes` accepts a list, but nothing yet defines whether one failing quote rejects the whole finding or only that quote. The honest default is all-or-nothing; confirm before the ADK tool is written.
3. **Cut-sheet page locators.** The schema carries `spec_locator` and `cut_sheet_locator` as free text, and `CitedQuote.document_path` as the machine link. Phase 2 fixture design should confirm this is enough for the findings page to render a usable citation.
4. **Scanned documents.** The gate is proven against text-based PDFs only. If any demo document is ever a scan, the gate's guarantee does not hold and the README must say so. Keep the fixture set text-based.
5. **`extract_pdf_text` tool boundary.** `extract_page_text` currently raises `IndexError` for a bad page, while `verify_quote` returns a rejection. Phase 3 should decide which surface the ADK tool exposes so a bad page number never becomes an agent-visible exception.
6. **Multi-column extraction order.** The fixtures are single-column, so extraction order matches reading order. A two-column cut sheet can make PyMuPDF interleave text from both columns, which would break a legitimate quote and produce a false rejection. Phase 2 should either keep fixtures single-column or prove the gate against a two-column page before the demo depends on one.
7. **Document identity binding.** `verify_quote(quote, page_number, pdf_path)` takes the document path as an argument, while `CitedQuote` carries its own `document_path`. Nothing checks that they agree, so a claim citing one document could be verified against another. Phase 3 must bind them at the call site, or `verify_quote` should take the `CitedQuote` directly.
8. **Firestore write path.** Nothing persists yet. Decide whether the gate result is stored alongside the finding or recomputed on read, and whether a rejected finding is written at all or only counted.
