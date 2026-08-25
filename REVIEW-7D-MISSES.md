# SpecGuard Phase 7d-eval, checkpoint C1: inspection of the two published messy-lane misses

Date: 2026-08-25. Session: the Phase 7d-eval implementer, working from the folded tip `fa8e093` in the `phase-7d` worktree. Scope: `E-13` (evidence `M-03`, missing required listing, published at 40 percent in both modes) and `E-15` (evidence `M-05`, service-clearance conflict, published at 0 percent in both modes). This checkpoint is read-only. No fixture, prompt, index constant, or harness scoring rule was changed. No model run was executed: the inspection is built from the persisted Firestore records of the ten published messy-lane runs, from deterministic local extraction of the committed fixtures, and from the prompt and harness source. Vertex spend for this checkpoint: zero.

## Verdict up front

Neither miss is a detection failure of the kind the published numbers imply, and the two misses have different causes.

`E-13` is **harness mis-scoring**. All ten published messy-lane runs, five in `full_text` and five in `navigate`, persisted a verified finding for the missing listing, citing the correct specification page and the correct package page. Six of those ten were scored as misses because the model returned a shorter package quote than the one the manifest records. The scorer requires raw string equality, so a shorter span that carries the same evidence on the same page counts as a miss, and the same finding is then re-counted as an unattributed false positive. The runtime found this discrepancy 10 of 10 times.

`E-15` is **mis-specified against the deployed prompt contract**, and hard for a defensible reason. No run in either mode attempted it. No run cited package page 8 at all, and no run returned a specification quote about clearance or working space. Both governing pages were in front of the model in every navigate run, proven below. The prompt tells the model not to report "statements that can both be true", and the package states its clearance figure as a recommendation under a heading that calls its dimensions nominal and tells the reader not to infer set-out dimensions from the package. A recommendation of the smaller figure and a project requirement to maintain the larger one can both be true. The model followed the instruction it was given.

One published number is misleading regardless of which action Mason chooses. `EVAL.md` reports 13 unattributed false positives in the `full_text` messy lane and 3 in `navigate`. All 16 reproduce a planted pair on the correct two pages with a different quote span. None is an invention, and none matches a decoy. The records show **zero invented findings and zero decoy false positives across all ten messy-lane runs**.

## Receipts

Every claim above rests on one of the receipts below. Run identifiers are the ten `EVAL.md` run identifiers for the two messy-lane sections.

| ID | Claim | Receipt |
| --- | --- | --- |
| R-01 | All seven planted quotes and both decoy quotes verify on their cited pages today. | `specguard.gate.verify_quote` run locally against the committed fixtures returned `verified=True` for every `M-*` pair checked, including both sides of `M-03` and both sides of `M-05`. The pairs are extractable and gate-verifiable; neither miss is an extraction defect. |
| R-02 | 60 findings were persisted across the ten messy-lane runs: 6 per run, in every run, in both modes. | Firestore `findings` collection, queried by `run_id` for all ten identifiers. |
| R-03 | 44 of those 60 matched the manifest pair exactly and were scored as catches. 16 cited the same two pages with an overlapping quote span and were scored as unattributed false positives. 0 matched a decoy. 0 matched nothing planted. | Offline classification of the same 60 records against the `fixture-evidence-messy` and `decoy-evidence-messy` blocks, using `gate.normalize` and substring containment for the second test. The 13-and-3 split of the 16 reproduces the `EVAL.md` unattributed false-positive counts exactly, which confirms the classification agrees with the harness about which findings the harness could not attribute. |
| R-04 | `E-13` was detected in 10 of 10 runs and scored at 2 of 5 in each mode. | Of the ten runs, four returned the full manifest package quote and were scored as catches. Six returned a contiguous tail of that manifest quote, the clause stating the listing is not included, on the same page 10, and were scored as misses. Every one of the ten returned the manifest specification quote on page 18 unchanged. |
| R-05 | The scorer requires raw string equality, not evidence equality. | `scripts/eval_fixtures.py::_matches_pair` compares the persisted quote text to the manifest quote text with `==`, for both sides, with no normalization and no containment. A finding that fails this test falls through to the unattributed-false-positive counter in `build_run_outcome`. |
| R-06 | `E-15` was never attempted. | No finding among the 60 cites package page 8. No finding among the 60 returns a specification quote containing "working space" or "clear". There is no gate rejection to explain the absence either: the `rejections` collection holds 0 records for all ten run identifiers, which agrees with the `EVAL.md` rejection counts of 0 for both messy sections. The model did not attempt the pair and fail the gate, and it did not paraphrase it. It never raised it. |
| R-07 | In `navigate` mode the model opened specification page 18 in all five runs. | The page-18 index line is the first 240 normalized characters of the page and ends mid-sentence part-way through the listing clause. It does not contain the `M-03` specification quote, the `M-06` quote, or the `M-05` quote. All five navigate runs returned the `M-03` and `M-06` specification quotes verbatim from page 18, and the gate verified each one against page 18. Those quotes could not have come from the index, so the page was opened through the read-only page-reading tool in every navigate run. Index blindness explains neither miss. |
| R-08 | The package side of `M-05` was in the prompt in full, in both modes. | `navigate` sends the submitted document in full (`agent.py::_build_navigate_message`); `full_text` sends both documents in full. Package page 8 required no tool call in either mode. |
| R-09 | The package states the clearance figure as a recommendation and disclaims it. | Normalized package page 8 reads, in order: a dimensions and service clearances heading, a line calling the selected product dimensions nominal package values, the dimensions table whose front-clearance row carries the recommended figure as its value cell against an installation-note reference cell, and a closing sentence directing that set-out dimensions be coordinated with the project room and not inferred from marketing drawings. |
| R-10 | The prompt excludes this class of pair. | Both prompts say: "Do not report omissions, preferences, formatting differences, or statements that can both be true." A nominal package recommendation and a project requirement to maintain a larger clearance are not mutually exclusive statements. The same clause is why the model's `M-03` behaviour is notable: `M-03` is close to an omission, which the prompt also excludes, and the model still reported it in all ten runs by quoting the one clause that states the absence explicitly. |
| R-11 | Every other planted pair states a flat product characteristic; `M-05` alone states vendor guidance. | The six caught pairs are a rating, a temperature, a listing declaration, a classification, a quantity, and a material. Each is what the product is. `M-05` is what the vendor suggests. |
| R-12 | The two modes differ in quote-span discipline, not in detection, on this lane. | Of the 30 findings per mode that reproduce a planted pair, `navigate` returned the manifest-exact span 27 times and `full_text` 17 times. All 13 `full_text` span variants take the form of the table row label joined to the value cell. The three `navigate` span variants are all `M-03`, and all three shorten rather than lengthen. |

## Classification and recommendation

### `E-13`, evidence `M-03`

**Classification: (c) harness mis-scoring.** A contributing mis-specification: the manifest package quote is a long sentence that bundles two review marks unrelated to the discrepancy with the one clause that carries it, and the prompt asks the model to describe only the conflict the two cited passages support. The narrower span the model returned six times out of ten is the better evidence anchor for the claim, and the gate verified it.

Three actions are available.

- **13-A, docs only.** Leave the measured 40 percent published and state what the records show beside it: the discrepancy was found in 10 of 10 runs, and the scorer requires the exact planted span. Cost: zero model runs, zero dollars, about one session hour. No published number changes.
- **13-B, change the scoring rule.** Replace raw equality in `_matches_pair` with same-page plus normalized containment in either direction, add known-good and known-bad tests for the new rule, then re-measure. Because the rule governs every published number, both lanes and both modes must re-run: 50 audits, roughly 2.9 million prompt tokens, an order of low single dollars. Consequences are stated in the next section.
- **13-C, change the fixture or the manifest quote.** Not recommended. The six variant spans are not identical to each other, so exact matching would still split, and any package change supersedes every number measured against the old bytes.

**Recommendation: 13-A.** It produces a true, receipted, and stronger sentence than the published number does on its own, it costs nothing, and it does not disturb the fixture bytes before the freeze.

### `E-15`, evidence `M-05`

**Classification: (a) mis-specified against the deployed prompt contract.** The pair is not a direct conflict in which both documents explicitly state incompatible characteristics. The package never states the clearance it will provide. It recommends a nominal figure and disclaims the inference. The model's refusal follows the instruction it was given, and no run reached the gate.

Three actions are available.

- **15-A, docs only.** Leave the measured 0 percent published and explain it with R-06, R-08, R-09, and R-10: both governing pages were in front of the model, no run raised the pair, and the prompt's own exclusion covers it. State it as a fixture design finding, not as a model limitation the numbers do not support. Cost: zero model runs, zero dollars.
- **15-B, re-specify the package side.** Rebuild the package fixture through `fixtures/build_fixtures.py` so the front-clearance row states a flat provided value rather than a recommendation. This changes the package SHA-256, so every messy-lane number measured against the old bytes is superseded and the messy lane must re-run in both modes: 10 audits, roughly 2.13 million prompt tokens, cents to about a dollar. `MANIFEST.md` follows the rebuild. Any published package hash follows it too. The risk is real: the pair may still not be reported, and a second attempt doubles the cost inside the freeze window.
- **15-C, loosen the prompt's exclusion clause.** Not recommended. The clause is load-bearing for the two compliant decoys and for `E-01`. Changing it forces a re-measure of both lanes in both modes and risks new false positives on exactly the cases that currently hold at zero.

**Recommendation: 15-A.** The honest published sentence is available at no cost, and 15-B spends fixture churn and a re-measurement cascade on a case whose difficulty is a property of the planted text rather than of the runtime.

### Required regardless of the above

The unattributed-false-positive framing needs one sentence of correction in `EVAL.md`, and the `DEVPOST.md` messy-lane line should carry it. The column definition is accurate, but a reader takes "false positive" to mean invention, and R-03 shows there were none. This is a docs-only correction with no number change.

## What changes if Mason chooses 13-B

The numbers below are computed offline over the same 60 persisted findings under a same-page containment rule. They are not a harness run and must not be published as measured. They exist so the decision can be made with the consequences visible.

| Figure | Published, exact-span rule | Offline projection, containment rule |
| --- | ---: | ---: |
| `navigate` messy-lane catch rate | 77% | 86% |
| `full_text` messy-lane catch rate | 49% | 86% |
| `navigate` catch rate across every planted discrepancy (README headline) | 82% | 89% |
| Unattributed false positives, `full_text` messy | 13 | 0 |
| Unattributed false positives, `navigate` messy | 3 | 0 |
| `E-13` catch rate, both modes | 40% | 100% |
| `E-15` catch rate, both modes | 0% | 0% |

Two consequences follow and both are load-bearing.

- The ship-gate condition "messy-lane catch rate is at least full_text's" would pass on a tie rather than on a margin. The condition is written as "at least", so the verdict holds, but it holds with no separation.
- The README sentence that `navigate` "is the default because it measured a higher catch rate on the hardest fixture lane" would become false. A true replacement is available from R-12: `navigate` returned the manifest-exact quote span 27 times of 30 against `full_text`'s 17 of 30, and produced three span variants against thirteen. That is a claim about quote discipline, not about detection, and it is a weaker justification for the default than the one currently published.

A third consequence is a matter of taste rather than fact. The published 82 percent is a conservative number produced by a strict rule. Relaxing the rule raises the headline. The rule should be changed because evidence equality is the honest definition of a catch, or not at all.

## What was not done at this checkpoint

No commit touches `specguard/gate.py`, any fixture, any prompt, the page-index constant, or the harness scoring rule. No model run was executed, so the diagnostic-run allowance of two was not used and Vertex spend for this checkpoint is zero. The `Cases that did not match the manifest` block in `EVAL.md` and the board row that records these misses as ACCEPTED are unchanged, pending the decision.
