# SpecGuard Phase 1 — handoff

Date: 2026-08-20. Scope: verification gate, data schema, test suite. Pure local Python. No GCP call, no ADK, no network dependency at test time.

## Quality-gate receipts

All commands run in `C:\Users\mcspd\dev\specguard` on arya. Exit codes are unpiped.

| Command | Exit | Result |
| --- | --- | --- |
| `uv sync` | 0 | 15 packages installed |
| `uv run pytest -q` | 0 | `36 passed in 0.48s` |
| `uv run ruff check .` | 0 | `All checks passed!` |
| `uv run ruff format --check .` | 0 | `11 files already formatted` |
| `git commit` | 0 | `ca48ab9 Add verification gate, data schema, and test suite` |

`PYTHONIOENCODING=utf-8` was set for the pytest run only, so the terminal could print the ligature and soft-hyphen characters in failure output. It does not affect the result.

## Pinned dependency versions

Runtime (`[project.dependencies]`):

- `pymupdf==1.28.2` — exact pin, as required. Text extraction is the load-bearing behavior.
- `pydantic>=2.9,<3` — resolved to `pydantic 2.13.4`, locked in `uv.lock`.

Dev group (`[dependency-groups].dev`):

- `pytest>=8.3,<9` — resolved to `8.4.2`.
- `ruff>=0.6,<1` — resolved to `0.16.4`.
- `pymupdf-fonts==1.0.5` — **deviation from the work order, see below.**

`google-adk` is not installed and is not referenced anywhere. Python pin: 3.12.

## Two contract-precision decisions, both flagged

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

36 tests. Every case the work order names is present and is asserted on the rejection reason, not only on the boolean.

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

`DocumentRecord.sha256` is described as chain-of-custody metadata in `specguard/models.py`, in `specguard/gate.py`, and in `README.md`. Each place states that no part of the gate reads it. No code path passes it to `verify_quote`. `test_document_record_is_chain_of_custody_only` asserts only that the digest is stable across two reads of the same bytes.

## Codex review

Findings and the changes made in response are recorded in the section below.

<!-- CODEX-REVIEW -->

## Open questions for Phase 3

1. **Retry feedback shape.** `VerificationResult.normalized_quote` is the only field carrying the gate's view of the quote back to the agent. Phase 3 must decide whether the bounced-back rejection also shows the agent the normalized page text, or a near-miss window from it. Showing the page text makes the retry easier but risks the agent pattern-matching a quote out of the feedback rather than out of the document.
2. **Multi-quote findings.** `Finding.quotes` accepts a list, but nothing yet defines whether one failing quote rejects the whole finding or only that quote. The honest default is all-or-nothing; confirm before the ADK tool is written.
3. **Cut-sheet page locators.** The schema carries `spec_locator` and `cut_sheet_locator` as free text, and `CitedQuote.document_path` as the machine link. Phase 2 fixture design should confirm this is enough for the findings page to render a usable citation.
4. **Scanned documents.** The gate is proven against text-based PDFs only. If any demo document is ever a scan, the gate's guarantee does not hold and the README must say so. Keep the fixture set text-based.
5. **`extract_pdf_text` tool boundary.** `extract_page_text` currently raises `IndexError` for a bad page, while `verify_quote` returns a rejection. Phase 3 should decide which surface the ADK tool exposes so a bad page number never becomes an agent-visible exception.
6. **Multi-column extraction order.** The fixtures are single-column, so extraction order matches reading order. A two-column cut sheet can make PyMuPDF interleave text from both columns, which would break a legitimate quote and produce a false rejection. Phase 2 should either keep fixtures single-column or prove the gate against a two-column page before the demo depends on one.
7. **Firestore write path.** Nothing persists yet. Decide whether the gate result is stored alongside the finding or recomputed on read, and whether a rejected finding is written at all or only counted.
