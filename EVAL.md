# SpecGuard measured evaluation

- Date: 2026-08-23
- Code revision these numbers describe: `1340748`
- Iterations per document pair: 5
- Audit model: `gemini-3.7-flash` via Vertex AI
- Severity model, measured from the persisted findings: none recorded on any persisted finding
- Severity endpoint, as the operator named it: no endpoint configured for this run
- Total Vertex spend: not visible in the run output
- Sections measured: 4 (one per agent mode and lane)
- Total real audits: 50

Every number below comes from one receipted execution of `scripts/eval_fixtures.py` against the deployed model path. The expected outcome of each case is read from the machine-readable blocks in `fixtures/MANIFEST.md`, not from this file.

## Method

One audit runs per document pair per iteration, and every case declared against that pair is scored from that one run. The messy package lane declares nine cases against one document pair, so five iterations are five audits, not forty-five. Auditing the same pair once per case would measure a workflow no reviewer performs and would multiply the spend by the number of planted discrepancies.

The two agent modes differ only in what the model is shown and which tools it may call. `full_text` sends every page of both documents up front and needs no tool call, though all five tools stay registered. `navigate` sends the submitted document in full plus a deterministic page index of the specification, and registers three read-only tools. The verification gate runs on every claim in both modes, and the runtime owns every write in both modes.

## What each column means

- **Catch rate** is the fraction of runs that persisted a finding whose two quotes and two page numbers equal the evidence pair the manifest records for that case. A near miss is not a catch. A case that plants nothing has no catch rate and reads `n/a`, because reporting 100 percent for an unmeasured case would inflate the average.
- **Decoy false positives** counts persisted findings that reproduced a compliant near-match pair the manifest records as a decoy. The wording differs between the two documents but the submission complies, so a finding here is a wording difference read as a conflict.
- **Unattributed false positives** counts persisted findings that matched no planted pair and no decoy pair. It belongs to the audit, not to any one case, which is why it appears only in the per-run table.
- **Rejections** counts claims the verification gate refused. A rejection is the gate working, not a failure of the run.
- **Retries** counts claims sent back to the model once after a gate rejection.
- **Quarantine rate** is the fraction of runs the text-layer integrity screen stopped before any model call.
- **Model turns** counts calls to the claim generator. The altered fixture must show zero.
- **Mean prompt tokens** is the mean of the exact prompt-token counts ADK reported, over the runs that reported one. No count is estimated.
- **Model tool calls per run** counts the function calls the model itself initiated, including the structured-output call ADK adds for this model.
- **Self-check rejections** counts model-initiated quote checks that answered that the quote was not on the cited page.
- **Runs that returned a rejected quote** counts runs where the model still returned a quote its own check had rejected. Together with the column beside it, this is the honest measure of whether the self-check changed anything.
- **Severity distribution** counts the Gemma severity labels across every persisted finding of that case.

## Results — `full_text` mode, original four-case lane

20 audits over 4 document pair(s), scoring 4 declared case(s).

| Case | Submitted document | Expected outcome | Catch rate | Decoy false positives | Quarantine rate | Severity distribution |
| --- | --- | --- | ---: | ---: | ---: | --- |
| `E-01` | `caldra_meridian_480v_switchboard.pdf` | `no_finding` | n/a | 0 | 0% | no findings |
| `E-02` | `veylan_arcworks_208v_switchboard.pdf` | `finding` | 100% | 0 | 0% | unclassified 5 |
| `E-03` | `torven_70c_termination_switchboard.pdf` | `finding` | 100% | 0 | 0% | unclassified 5 |
| `E-04` | `veylan_arcworks_208v_altered.pdf` | `quarantine` | n/a | 0 | 100% | no findings |

| Specification | Submitted document | Runs | Unattributed false positives | Rejections | Retries | Model turns | Mean prompt tokens | Model tool calls per run | Self-check rejections | Runs that returned a rejected quote |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `asterquay_learning_workshop_specification.pdf` | `caldra_meridian_480v_switchboard.pdf` | 5 | 0 | 0 | 0 | 5 | 4544.0 | 1.0 | 0 | 0 |
| `asterquay_learning_workshop_specification.pdf` | `veylan_arcworks_208v_switchboard.pdf` | 5 | 0 | 0 | 0 | 5 | 12120.0 | 3.0 | 6 | 5 |
| `asterquay_learning_workshop_specification.pdf` | `torven_70c_termination_switchboard.pdf` | 5 | 0 | 0 | 0 | 5 | 20825.4 | 5.0 | 5 | 3 |
| `asterquay_learning_workshop_specification.pdf` | `veylan_arcworks_208v_altered.pdf` | 5 | 0 | 0 | 0 | 0 | n/a | 0.0 | 0 | 0 |

- The verification gate rejected nothing and the runtime retried nothing in this lane. The model cited every quote correctly on the first turn, so the rejection-and-retry loop did not fire. These numbers are therefore not evidence that the loop works. The loop is covered by the test suite, which drives rejections deterministically.
- This lane ran in `full_text` mode, which sends every page of both documents up front, so the model needs no tool call to read them. The mode still registers all five tools, and the tool-call column records the calls the model chose to make, including the structured-output call ADK adds for this model.
- The model's own quote self-check rejected 11 quotes, and 8 runs still returned a quote their own check had rejected. The runtime never trusted that check: its gate ran on every claim, and again before any write.
- 10 of 10 persisted findings carry `unclassified` severity. Severity classification fell back for those findings and the reason is recorded on each one. A fallback never blocks an audit and never changes verification status.

Run identifiers behind this section:

- `caldra_meridian_480v_switchboard.pdf`: `49bf2ff5c899441fb32515f184db15b9`, `d1b60b8a11e1432c80f5c7af81f8645c`, `5f6e5f82a1d547689ba89c674524086d`, `4242c1065bfa4c91a27b6a0b1dceae0b`, `81bb491474e142328767382c73c0ec1c`
- `veylan_arcworks_208v_switchboard.pdf`: `a5dcda5063d3498ab7f35bf240d6ebc3`, `9f0f8243133a43b59699ac0e1a1b2ff8`, `b92a6314ae96477f919310c48b2dc8ec`, `a8100523fa4c4d828bfaca1ad2f53cae`, `1b868dbb272d4f3b92017b4a3a397b02`
- `torven_70c_termination_switchboard.pdf`: `00caae839ff3416db36cfd7055bf4f84`, `4668a7e3d3d44f20963d1a17790afc70`, `db240349b1264930a5d6c5933ba9ac75`, `0f6003791717490f898f7c49bb46c180`, `eebd6024600444a4a8aa070b2e0e2792`
- `veylan_arcworks_208v_altered.pdf`: `27575c11396c411da19a1a8bd04b1b42`, `77a9589de37e4be8950d92426e52ce5a`, `5fab38c2d6b44d3eb83503a5b158c0a8`, `32fb85c365f94d44b37c75514a2af372`, `7a6815f12d4948bbad7312a6b6c3e05a`

## Results — `full_text` mode, messy package lane

5 audits over 1 document pair(s), scoring 9 declared case(s).

| Case | Submitted document | Expected outcome | Catch rate | Decoy false positives | Quarantine rate | Severity distribution |
| --- | --- | --- | ---: | ---: | ---: | --- |
| `E-11` | `zarqelune_vantrel_package.pdf` | `finding` | 60% | 0 | 0% | unclassified 30 |
| `E-12` | `zarqelune_vantrel_package.pdf` | `finding` | 60% | 0 | 0% | unclassified 30 |
| `E-13` | `zarqelune_vantrel_package.pdf` | `finding` | 40% | 0 | 0% | unclassified 30 |
| `E-14` | `zarqelune_vantrel_package.pdf` | `finding` | 60% | 0 | 0% | unclassified 30 |
| `E-15` | `zarqelune_vantrel_package.pdf` | `finding` | 0% | 0 | 0% | unclassified 30 |
| `E-16` | `zarqelune_vantrel_package.pdf` | `finding` | 60% | 0 | 0% | unclassified 30 |
| `E-17` | `zarqelune_vantrel_package.pdf` | `finding` | 60% | 0 | 0% | unclassified 30 |
| `E-18` | `zarqelune_vantrel_package.pdf` | `no_finding` | n/a | 0 | 0% | unclassified 30 |
| `E-19` | `zarqelune_vantrel_package.pdf` | `no_finding` | n/a | 0 | 0% | unclassified 30 |

| Specification | Submitted document | Runs | Unattributed false positives | Rejections | Retries | Model turns | Mean prompt tokens | Model tool calls per run | Self-check rejections | Runs that returned a rejected quote |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `nimbrin_thermal_annex_specification.pdf` | `zarqelune_vantrel_package.pdf` | 5 | 13 | 0 | 0 | 5 | 198345.2 | 22.0 | 4 | 3 |

- The verification gate rejected nothing and the runtime retried nothing in this lane. The model cited every quote correctly on the first turn, so the rejection-and-retry loop did not fire. These numbers are therefore not evidence that the loop works. The loop is covered by the test suite, which drives rejections deterministically.
- This lane ran in `full_text` mode, which sends every page of both documents up front, so the model needs no tool call to read them. The mode still registers all five tools, and the tool-call column records the calls the model chose to make, including the structured-output call ADK adds for this model.
- The model's own quote self-check rejected 4 quotes, and 3 runs still returned a quote their own check had rejected. The runtime never trusted that check: its gate ran on every claim, and again before any write.
- 270 of 270 persisted findings carry `unclassified` severity. Severity classification fell back for those findings and the reason is recorded on each one. A fallback never blocks an audit and never changes verification status.

Run identifiers behind this section:

- `zarqelune_vantrel_package.pdf`: `5adc49bd0c7748f694f00caaf66f0f80`, `2bd0901c9f924b838099d8d0114cb71c`, `4fc3793d490941f4a75c48d9d6a5e13d`, `787c56b70d5c4f3dbaf03186d206867f`, `9aa3fec730904c6cb9286d1f01d9eba8`

## Results — `navigate` mode, original four-case lane

20 audits over 4 document pair(s), scoring 4 declared case(s).

| Case | Submitted document | Expected outcome | Catch rate | Decoy false positives | Quarantine rate | Severity distribution |
| --- | --- | --- | ---: | ---: | ---: | --- |
| `E-01` | `caldra_meridian_480v_switchboard.pdf` | `no_finding` | n/a | 0 | 0% | no findings |
| `E-02` | `veylan_arcworks_208v_switchboard.pdf` | `finding` | 100% | 0 | 0% | unclassified 5 |
| `E-03` | `torven_70c_termination_switchboard.pdf` | `finding` | 100% | 0 | 0% | unclassified 5 |
| `E-04` | `veylan_arcworks_208v_altered.pdf` | `quarantine` | n/a | 0 | 100% | no findings |

| Specification | Submitted document | Runs | Unattributed false positives | Rejections | Retries | Model turns | Mean prompt tokens | Model tool calls per run | Self-check rejections | Runs that returned a rejected quote |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `asterquay_learning_workshop_specification.pdf` | `caldra_meridian_480v_switchboard.pdf` | 5 | 0 | 1 | 0 | 5 | 22719.4 | 6.8 | 0 | 0 |
| `asterquay_learning_workshop_specification.pdf` | `veylan_arcworks_208v_switchboard.pdf` | 5 | 0 | 0 | 0 | 5 | 34234.6 | 8.6 | 0 | 0 |
| `asterquay_learning_workshop_specification.pdf` | `torven_70c_termination_switchboard.pdf` | 5 | 0 | 0 | 0 | 5 | 36843.0 | 9.2 | 0 | 0 |
| `asterquay_learning_workshop_specification.pdf` | `veylan_arcworks_208v_altered.pdf` | 5 | 0 | 0 | 0 | 0 | n/a | 0.0 | 0 | 0 |

- The verification gate rejected 1 claims and the runtime retried 0, so the rejection-and-retry loop did fire in this lane.
- 1 case-runs produced no usable model turn and were recorded as unusable. A case containing such a run does not match the manifest, whatever its other counters say.
- The model's own quote self-check rejected nothing in this lane. The self-check therefore changed no answer here, and these numbers are not evidence that it would. The runtime gate ran on every claim regardless.
- 10 of 10 persisted findings carry `unclassified` severity. Severity classification fell back for those findings and the reason is recorded on each one. A fallback never blocks an audit and never changes verification status.

Run identifiers behind this section:

- `caldra_meridian_480v_switchboard.pdf`: `96504345907146baadf194c295e250f3`, `f3d17c52768a4a49ac0ab775f2aedb76`, `84294847b82c42b5a37c02bdf9dd97a3`, `269e01a66dff4aa18b8b88caa7191ac0`, `19ede893273644b38c14e7af6b917741`
- `veylan_arcworks_208v_switchboard.pdf`: `ee5a80f50ee64b3f889b78d5cefb7397`, `2b4e25192016419cb3306eed22b0e1a9`, `556ea74070ba46dda503f906d7d88b10`, `4bae4b745f7f4f228f8d0be4cf0fa0b7`, `0bb84646dc3e4ffb80e582172517834f`
- `torven_70c_termination_switchboard.pdf`: `725c641f5d214fea8ba0eb8935ea328c`, `b397cd6340b14022a34243455e9dff43`, `068c174ae6be4e29849eecf837601678`, `cfdb540cd0224601b4593101e146a66f`, `053b99d12e1d433a9983ed3dd8f758d8`
- `veylan_arcworks_208v_altered.pdf`: `feac69941826472f85ab902bc0cecaa4`, `9d05a2a0cca74c1f9d19a1f44392d8a0`, `3c14e0afc9ab4194b26dcc7722d0a8e8`, `89f7fff9a75e4d13a04531b1af19411f`, `5d46527edf8f49e199d1a485a5e25d02`

## Results — `navigate` mode, messy package lane

5 audits over 1 document pair(s), scoring 9 declared case(s).

| Case | Submitted document | Expected outcome | Catch rate | Decoy false positives | Quarantine rate | Severity distribution |
| --- | --- | --- | ---: | ---: | ---: | --- |
| `E-11` | `zarqelune_vantrel_package.pdf` | `finding` | 100% | 0 | 0% | unclassified 30 |
| `E-12` | `zarqelune_vantrel_package.pdf` | `finding` | 100% | 0 | 0% | unclassified 30 |
| `E-13` | `zarqelune_vantrel_package.pdf` | `finding` | 40% | 0 | 0% | unclassified 30 |
| `E-14` | `zarqelune_vantrel_package.pdf` | `finding` | 100% | 0 | 0% | unclassified 30 |
| `E-15` | `zarqelune_vantrel_package.pdf` | `finding` | 0% | 0 | 0% | unclassified 30 |
| `E-16` | `zarqelune_vantrel_package.pdf` | `finding` | 100% | 0 | 0% | unclassified 30 |
| `E-17` | `zarqelune_vantrel_package.pdf` | `finding` | 100% | 0 | 0% | unclassified 30 |
| `E-18` | `zarqelune_vantrel_package.pdf` | `no_finding` | n/a | 0 | 0% | unclassified 30 |
| `E-19` | `zarqelune_vantrel_package.pdf` | `no_finding` | n/a | 0 | 0% | unclassified 30 |

| Specification | Submitted document | Runs | Unattributed false positives | Rejections | Retries | Model turns | Mean prompt tokens | Model tool calls per run | Self-check rejections | Runs that returned a rejected quote |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `nimbrin_thermal_annex_specification.pdf` | `zarqelune_vantrel_package.pdf` | 5 | 3 | 0 | 0 | 5 | 227771.2 | 27.6 | 0 | 0 |

- The verification gate rejected nothing and the runtime retried nothing in this lane. The model cited every quote correctly on the first turn, so the rejection-and-retry loop did not fire. These numbers are therefore not evidence that the loop works. The loop is covered by the test suite, which drives rejections deterministically.
- The model's own quote self-check rejected nothing in this lane. The self-check therefore changed no answer here, and these numbers are not evidence that it would. The runtime gate ran on every claim regardless.
- 270 of 270 persisted findings carry `unclassified` severity. Severity classification fell back for those findings and the reason is recorded on each one. A fallback never blocks an audit and never changes verification status.

Run identifiers behind this section:

- `zarqelune_vantrel_package.pdf`: `39d28870baab43a2b6152e1d7f384a83`, `c0dfd238263642febdf8847d50ed8084`, `85aa7f74836a4baa80bbd9ab97d4fb85`, `fa584e6a343b4c18a0375de7b0d0ef77`, `c3fd5d8ca65b453eb8239d6ad3b59b38`

## Ship gate

```
SHIP GATE navigate SHIPS as default
  [PASS] E-02 catch rate is 100%: measured 100%
  [PASS] E-03 catch rate is 100%: measured 100%
  [PASS] E-01 false positives are 0: measured 0
  [PASS] E-04 quarantine rate is 100% with 0 model turns: measured 100% with 0 model turns
  [PASS] no original-lane catch-rate regression against full_text: every case held or improved
  [PASS] no original-lane false-positive regression against full_text: navigate 0, full_text 0
  [PASS] messy-lane catch rate is at least full_text's: navigate 77%, full_text 49%
  [PASS] messy-lane decoy false positives are at most full_text's: navigate 0, full_text 0
```

The conditions above were fixed in the phase work order before any run. A pure function evaluates them over these results, and the test suite exercises that same function against known-good and known-bad inputs. No condition was relaxed and no run was repeated to reach this verdict.

## Cases that did not match the manifest

- `E-11` (`zarqelune_vantrel_package.pdf`), expected `finding`: 3 of 5 runs caught the expected pair, 0 decoy false positives, 0 quarantines, 5 model turns.
- `E-12` (`zarqelune_vantrel_package.pdf`), expected `finding`: 3 of 5 runs caught the expected pair, 0 decoy false positives, 0 quarantines, 5 model turns.
- `E-13` (`zarqelune_vantrel_package.pdf`), expected `finding`: 2 of 5 runs caught the expected pair, 0 decoy false positives, 0 quarantines, 5 model turns.
- `E-14` (`zarqelune_vantrel_package.pdf`), expected `finding`: 3 of 5 runs caught the expected pair, 0 decoy false positives, 0 quarantines, 5 model turns.
- `E-15` (`zarqelune_vantrel_package.pdf`), expected `finding`: 0 of 5 runs caught the expected pair, 0 decoy false positives, 0 quarantines, 5 model turns.
- `E-16` (`zarqelune_vantrel_package.pdf`), expected `finding`: 3 of 5 runs caught the expected pair, 0 decoy false positives, 0 quarantines, 5 model turns.
- `E-17` (`zarqelune_vantrel_package.pdf`), expected `finding`: 3 of 5 runs caught the expected pair, 0 decoy false positives, 0 quarantines, 5 model turns.
- `E-18` (`zarqelune_vantrel_package.pdf`), expected `no_finding`: 0 of 5 runs caught the expected pair, 0 decoy false positives, 0 quarantines, 5 model turns.
- `E-19` (`zarqelune_vantrel_package.pdf`), expected `no_finding`: 0 of 5 runs caught the expected pair, 0 decoy false positives, 0 quarantines, 5 model turns.
- `E-01` (`caldra_meridian_480v_switchboard.pdf`), expected `no_finding`: 0 of 5 runs caught the expected pair, 0 decoy false positives, 0 quarantines, 5 model turns.
- `E-13` (`zarqelune_vantrel_package.pdf`), expected `finding`: 2 of 5 runs caught the expected pair, 0 decoy false positives, 0 quarantines, 5 model turns.
- `E-15` (`zarqelune_vantrel_package.pdf`), expected `finding`: 0 of 5 runs caught the expected pair, 0 decoy false positives, 0 quarantines, 5 model turns.
- `E-18` (`zarqelune_vantrel_package.pdf`), expected `no_finding`: 0 of 5 runs caught the expected pair, 0 decoy false positives, 0 quarantines, 5 model turns.
- `E-19` (`zarqelune_vantrel_package.pdf`), expected `no_finding`: 0 of 5 runs caught the expected pair, 0 decoy false positives, 0 quarantines, 5 model turns.

These numbers are published as measured. The README states the same figures and does not describe the runtime as catching everything.

## Scope of these numbers

This evaluation measures seven committed fictional fixtures, not a corpus of real submittals. It reports how the runtime behaved on documents built to carry known discrepancies. It is not evidence of accuracy on documents outside this set, and it is not a compliance determination.
