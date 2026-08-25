# SpecGuard measured evaluation

- Date: 2026-08-25
- Code revision these numbers describe: `8900a76`
- Iterations per document pair: 10
- Audit model: `gemini-3.7-flash` via Vertex AI
- Severity model, measured from the persisted findings: none recorded on any persisted finding
- Severity endpoint, as the operator named it: no endpoint configured for this run
- Total Vertex spend: not visible in the run output
- Sections measured: 4 (one per agent mode and lane)
- Total real audits: 100

Every number below comes from one receipted execution of `scripts/eval_fixtures.py` against the deployed model path. The expected outcome of each case is read from the machine-readable blocks in `fixtures/MANIFEST.md`, not from this file.

## Method

One audit runs per document pair per iteration, and every case declared against that pair is scored from that one run. The messy package lane declares nine cases against one document pair, so 10 iterations are 10 audits, not 90. Auditing the same pair once per case would measure a workflow no reviewer performs and would multiply the spend by the number of planted discrepancies.

The two agent modes differ only in what the model is shown. `full_text` sends every page of both documents up front and needs no tool call. `navigate` sends the submitted document in full plus a deterministic page index of the specification. Both modes register the same three read-only tools and neither of the two that write. The verification gate runs on every claim in both modes, and the runtime owns every write in both modes.

Every model-initiated tool call behind the numbers below is committed to `EVAL-RECEIPTS.jsonl`: one JSON line per call, carrying the mode, lane, document pair, iteration, run identifier, turn index, tool name and the bounded arguments the runtime recorded, followed by one summary line per run. The tool-call columns can therefore be checked call by call rather than taken on this file's word.

## What each column means

- **Catch rate** is the fraction of runs that persisted a finding carrying the same evidence as the pair the manifest records for that case. Each side must cite the page the manifest records, and the persisted quote and the manifest quote must contain one another in either direction after the verification gate's own normalization, on the token boundaries that gate uses. A longer or shorter span of the same passage on the cited page is the same evidence. A quote that only overlaps the planted passage is not, and the planted words on another page are not. A case that plants nothing has no catch rate and reads `n/a`, because reporting 100 percent for an unmeasured case would inflate the average.
- **Decoy false positives** counts persisted findings that reproduced a compliant near-match pair the manifest records as a decoy, under the same evidence rule, so a shortened span of a decoy is counted here rather than as an invention. The wording differs between the two documents but the submission complies, so a finding here is a wording difference read as a conflict.
- **Unattributed false positives** counts persisted findings that carried neither a planted pair nor a decoy pair under that rule, and findings whose quotes were wide enough to carry more than one of them, which name no single discrepancy. It belongs to the audit, not to any one case, which is why it appears only in the per-run table.
- **Rejections** counts claims the verification gate refused. A rejection is the gate working, not a failure of the run.
- **Retries** counts claims sent back to the model once after a gate rejection.
- **Quarantine rate** is the fraction of runs the text-layer integrity screen stopped before any model call.
- **Model turns** counts calls to the claim generator. The altered fixture must show zero.
- **Mean prompt tokens** is the mean of the exact prompt-token counts ADK reported, over the runs that reported one. No count is estimated.
- **Model tool calls per run** counts the function calls the model itself initiated, including the structured-output call ADK adds for this model.
- **Self-check rejections** counts model-initiated quote checks that answered that the quote was not on the cited page.
- **Runs that returned a rejected quote** counts runs where the model still returned a quote its own check had rejected. Together with the column beside it, this is the honest measure of whether the self-check changed anything.
- **Severity distribution** counts the Gemma severity labels across every persisted finding of that case.

## What these numbers supersede

This file is written in full by `scripts/eval_fixtures.py` on every run. Nothing in it is carried forward by hand, so a section that stood in an earlier edition and is absent here was not preserved: it was replaced by this measurement. These numbers were measured at 10 audits per document pair on code revision `8900a76`. Any earlier published measurement taken at a different iteration count, a different code revision, or under a different scoring rule is superseded by this one, not corrected by it. The two records describe different runs and are not comparable cell by cell.

The self-check columns count calls and their anchor match, which is the corrected counting rule. An earlier edition counted distinct quote digests, so a model that checked the same quote twice was counted once. Every self-check number in this edition was measured under the corrected rule.

## Results — `full_text` mode, original four-case lane

40 audits over 4 document pair(s), scoring 4 declared case(s).

| Case | Submitted document | Expected outcome | Catch rate | Decoy false positives | Quarantine rate | Severity distribution |
| --- | --- | --- | ---: | ---: | ---: | --- |
| `E-01` | `caldra_meridian_480v_switchboard.pdf` | `no_finding` | n/a | 0 | 0% | no findings |
| `E-02` | `veylan_arcworks_208v_switchboard.pdf` | `finding` | 100% | 0 | 0% | unclassified 10 |
| `E-03` | `torven_70c_termination_switchboard.pdf` | `finding` | 100% | 0 | 0% | unclassified 10 |
| `E-04` | `veylan_arcworks_208v_altered.pdf` | `quarantine` | n/a | 0 | 100% | no findings |

| Specification | Submitted document | Runs | Unattributed false positives | Rejections | Retries | Model turns | Mean prompt tokens | Model tool calls per run | Self-check rejections | Runs that returned a rejected quote |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `asterquay_learning_workshop_specification.pdf` | `caldra_meridian_480v_switchboard.pdf` | 10 | 0 | 0 | 0 | 10 | 3562.0 | 1.0 | 0 | 0 |
| `asterquay_learning_workshop_specification.pdf` | `veylan_arcworks_208v_switchboard.pdf` | 10 | 0 | 0 | 0 | 10 | 9768.3 | 3.0 | 0 | 0 |
| `asterquay_learning_workshop_specification.pdf` | `torven_70c_termination_switchboard.pdf` | 10 | 0 | 0 | 0 | 10 | 8975.0 | 3.0 | 0 | 0 |
| `asterquay_learning_workshop_specification.pdf` | `veylan_arcworks_208v_altered.pdf` | 10 | 0 | 0 | 0 | 0 | n/a | 0.0 | 0 | 0 |

- The verification gate rejected nothing and the runtime retried nothing in this lane. The model cited every quote correctly on the first turn, so the rejection-and-retry loop did not fire. These numbers are therefore not evidence that the loop works. The loop is covered by the test suite, which drives rejections deterministically.
- This lane ran in `full_text` mode, which sends every page of both documents up front, so the model needs no tool call to read them. The mode registers the same three read-only tools `navigate` registers and neither of the two that write, and the tool-call column records the calls the model chose to make, including the structured-output call ADK adds for this model.
- The model's own quote self-check rejected nothing in this lane. The self-check therefore changed no answer here, and these numbers are not evidence that it would. The runtime gate ran on every claim regardless.
- 20 of 20 persisted findings carry `unclassified` severity. Severity classification fell back for those findings and the reason is recorded on each one. A fallback never blocks an audit and never changes verification status.

Run identifiers behind this section:

- `caldra_meridian_480v_switchboard.pdf`: `335a3d255d6d4326bfca2d8c13a7e217`, `30bd2d7de213469db3677b2e2988ebcd`, `90f80098d89f41668ee0ac31ccb98e88`, `f506dbabee6c40be9438bd98114e891f`, `d9bebc8e261542f5946bbe936a641118`, `dcae6e621868413884fff9de1b42731a`, `6ce5e71ae6414af6a75a1f7560bb681e`, `66dabc87e00a4197b05f41f8577be9da`, `0b539abf2a824b4eb3329b79b9e34c9b`, `0f01e8a418f841d38c887845c676a0a1`
- `veylan_arcworks_208v_switchboard.pdf`: `def34309ac5745e692bc80b107266085`, `bcbb1f0336d24194ac90f0d2d8bb8265`, `03ba6d7665df485d995dc9c5348940e1`, `1558bf2f192e4f23a7753084ac437f74`, `031e119f91a246e2924dc8e1a9717cfa`, `2996f488e1394f46a546111dcfe4db18`, `936de3cc22454bc8b19f80288044c03e`, `92254cba71d74d5ba70adec21b77d792`, `1b9fbb1a7727436ba2c37dc3916cab58`, `5d36634e538843179eb62c3b739f5f82`
- `torven_70c_termination_switchboard.pdf`: `3d7745da2228495da5c4af8e497f7ebf`, `aff641bd11994e28b9e3708d7ef44720`, `8a77fb7c4f414eb1a0d1204af6069de8`, `79b07ec3c5124eab83d28f1d814c7ac1`, `fe6b16ca1e66476db3088ee3d4145086`, `6a3f352af80c4e1c83b5c1c460b3b9df`, `4cd0112e7c884abcb44824f385e49a0d`, `2b327520708048599aef427f63b1b71d`, `17d412ecc8cb47e29d9f51e582246dd7`, `f407e2fce17d476ba53f567559768a63`
- `veylan_arcworks_208v_altered.pdf`: `d786a774719e4eefb4dbcd42b7f9528c`, `4f51bcfe5e5545e581b0d5f01fd92415`, `bd647e07d8dc4650b73ed878e7ce2a5b`, `b8837cc032d44691bd07d8e1c7eeedef`, `924f7e93f8d84e4e96f2095f1356aa3e`, `03b9401d091f454fb1b14a82d5719345`, `ed90749ecdac4b49aa13bcdb98f9de4f`, `0c94e82cfb27479fb3102ad805b29990`, `b37430ba909749c4ae2d48d1f1ca048d`, `2f1e7b3b784c4448ab35be4fab81c275`

## Results — `full_text` mode, messy package lane

10 audits over 1 document pair(s), scoring 9 declared case(s).

| Case | Submitted document | Expected outcome | Catch rate | Decoy false positives | Quarantine rate | Severity distribution |
| --- | --- | --- | ---: | ---: | ---: | --- |
| `E-11` | `zarqelune_vantrel_package.pdf` | `finding` | 100% | 0 | 0% | unclassified 10 |
| `E-12` | `zarqelune_vantrel_package.pdf` | `finding` | 100% | 0 | 0% | unclassified 10 |
| `E-13` | `zarqelune_vantrel_package.pdf` | `finding` | 100% | 0 | 0% | unclassified 10 |
| `E-14` | `zarqelune_vantrel_package.pdf` | `finding` | 100% | 0 | 0% | unclassified 10 |
| `E-15` | `zarqelune_vantrel_package.pdf` | `finding` | 0% | 0 | 0% | no findings |
| `E-16` | `zarqelune_vantrel_package.pdf` | `finding` | 100% | 0 | 0% | unclassified 10 |
| `E-17` | `zarqelune_vantrel_package.pdf` | `finding` | 100% | 0 | 0% | unclassified 10 |
| `E-18` | `zarqelune_vantrel_package.pdf` | `no_finding` | n/a | 0 | 0% | no findings |
| `E-19` | `zarqelune_vantrel_package.pdf` | `no_finding` | n/a | 0 | 0% | no findings |

| Specification | Submitted document | Runs | Unattributed false positives | Rejections | Retries | Model turns | Mean prompt tokens | Model tool calls per run | Self-check rejections | Runs that returned a rejected quote |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `nimbrin_thermal_annex_specification.pdf` | `zarqelune_vantrel_package.pdf` | 10 | 0 | 0 | 0 | 10 | 152424.8 | 14.8 | 0 | 0 |

- The verification gate rejected nothing and the runtime retried nothing in this lane. The model cited every quote correctly on the first turn, so the rejection-and-retry loop did not fire. These numbers are therefore not evidence that the loop works. The loop is covered by the test suite, which drives rejections deterministically.
- This lane ran in `full_text` mode, which sends every page of both documents up front, so the model needs no tool call to read them. The mode registers the same three read-only tools `navigate` registers and neither of the two that write, and the tool-call column records the calls the model chose to make, including the structured-output call ADK adds for this model.
- The model's own quote self-check rejected nothing in this lane. The self-check therefore changed no answer here, and these numbers are not evidence that it would. The runtime gate ran on every claim regardless.
- 60 of 60 persisted findings carry `unclassified` severity. Severity classification fell back for those findings and the reason is recorded on each one. A fallback never blocks an audit and never changes verification status.

Run identifiers behind this section:

- `zarqelune_vantrel_package.pdf`: `efeef01a74a84246a17e867ef72c4047`, `98da3c752edf47b8a40b56cf362e9edc`, `db0a4cc0b4af4da09b92fc5cf5d1f1d9`, `5ccff812d979417ba3bffdf54263e161`, `5713ad9f17b549f7b2b3f1c8f4f4ff80`, `ba82af1c150945c8800e217868d3f995`, `8c69c68f80ed42689b80ff8dd876263d`, `b84b1ecb113e4b60ab1c63273ec73822`, `f0fdac46a03347bb9dbf54817bab291c`, `5931684f6ae24a64a614b71bbed8a589`

## Results — `navigate` mode, original four-case lane

40 audits over 4 document pair(s), scoring 4 declared case(s).

| Case | Submitted document | Expected outcome | Catch rate | Decoy false positives | Quarantine rate | Severity distribution |
| --- | --- | --- | ---: | ---: | ---: | --- |
| `E-01` | `caldra_meridian_480v_switchboard.pdf` | `no_finding` | n/a | 0 | 0% | no findings |
| `E-02` | `veylan_arcworks_208v_switchboard.pdf` | `finding` | 100% | 0 | 0% | unclassified 10 |
| `E-03` | `torven_70c_termination_switchboard.pdf` | `finding` | 100% | 0 | 0% | unclassified 10 |
| `E-04` | `veylan_arcworks_208v_altered.pdf` | `quarantine` | n/a | 0 | 100% | no findings |

| Specification | Submitted document | Runs | Unattributed false positives | Rejections | Retries | Model turns | Mean prompt tokens | Model tool calls per run | Self-check rejections | Runs that returned a rejected quote |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `asterquay_learning_workshop_specification.pdf` | `caldra_meridian_480v_switchboard.pdf` | 10 | 0 | 0 | 0 | 10 | 26025.8 | 7.3 | 0 | 0 |
| `asterquay_learning_workshop_specification.pdf` | `veylan_arcworks_208v_switchboard.pdf` | 10 | 0 | 0 | 0 | 10 | 36246.7 | 9.0 | 0 | 0 |
| `asterquay_learning_workshop_specification.pdf` | `torven_70c_termination_switchboard.pdf` | 10 | 0 | 0 | 0 | 10 | 32976.7 | 9.5 | 0 | 0 |
| `asterquay_learning_workshop_specification.pdf` | `veylan_arcworks_208v_altered.pdf` | 10 | 0 | 0 | 0 | 0 | n/a | 0.0 | 0 | 0 |

- The verification gate rejected nothing and the runtime retried nothing in this lane. The model cited every quote correctly on the first turn, so the rejection-and-retry loop did not fire. These numbers are therefore not evidence that the loop works. The loop is covered by the test suite, which drives rejections deterministically.
- The model's own quote self-check rejected nothing in this lane. The self-check therefore changed no answer here, and these numbers are not evidence that it would. The runtime gate ran on every claim regardless.
- 20 of 20 persisted findings carry `unclassified` severity. Severity classification fell back for those findings and the reason is recorded on each one. A fallback never blocks an audit and never changes verification status.

Run identifiers behind this section:

- `caldra_meridian_480v_switchboard.pdf`: `8189f91955f7497b90f78f3629e7e83c`, `4b4ee42887a448c18f019efa918ff601`, `5d8351cf9fe642d6aed877388cec25f6`, `f731eb2ff8bc45b08ac65d507c53cfde`, `d226f9722fd24cd9ae956887fc7bd6b4`, `71e8ca0867fd4e0ab408f80935f69016`, `1dc66685c2644897b4d493dfd5da6e89`, `8864c044983b4b4083afb41b1809d6a1`, `b2904dec05294681a7576feb67cbb1d1`, `ea10ce2898964c568f077aafbee90a3e`
- `veylan_arcworks_208v_switchboard.pdf`: `421fbeeb270b4df78ad6dc6a929ba85c`, `c88eb4fc32124f50a50200a127ec4adb`, `6471b4470fe4474bbcc9922c025347b3`, `ed90bbaad71f4df48e3c00f8b132ab17`, `f054d6d1f9274cc288970aaf31b4e0cf`, `5a5c5d0f3a12422d92b4abbb427a93ea`, `c5491b2449834eeeac49a61c2bf71e0d`, `ee7d2ba7b186438487d0a33684f020ca`, `f2e2cea20f4146a588880ce18b91d406`, `203f3d0290d3420db1fed0b69bac13af`
- `torven_70c_termination_switchboard.pdf`: `a0f79b9242924fa7b8046af7dc992f15`, `fae8ecfc978c4e348c369da0cf73bcb0`, `19e613de942b478998dd6b2fc93da775`, `dabe0aa5032148c1a33e7ee4290093e0`, `f6f3b2dde62c4a9a81267f5b0be3030f`, `39cf02327c7746b1bffd8374b2664821`, `01ef6360a732404c82c6d488424bf8b7`, `2a5442f609b1467c8716c3f4b80999c8`, `4161be3eac3744f5b05b7fab3cf974f1`, `fe73f44aa8c847df9744e43fe8e0cbee`
- `veylan_arcworks_208v_altered.pdf`: `881414061367475891385f2df65d9884`, `b07cee82ca6643358e6c6adc5f1202b5`, `7fb1e3dbe9a645bea9b582d292a807ef`, `3d9ef727d0624d568300c0ac0fe7b8bc`, `cc899d15bdf74b498e3748fc2f2dab86`, `c8dbafb66fb84697bea79e67e868450e`, `ce7cb65af835415a9aacd590345e2258`, `7b59fde8594c433da029dcfbbfe35aeb`, `0c36d21278b546229318fdd5d6ed8a62`, `edd7cd6e108d42ed9362de1d347df19e`

## Results — `navigate` mode, messy package lane

10 audits over 1 document pair(s), scoring 9 declared case(s).

| Case | Submitted document | Expected outcome | Catch rate | Decoy false positives | Quarantine rate | Severity distribution |
| --- | --- | --- | ---: | ---: | ---: | --- |
| `E-11` | `zarqelune_vantrel_package.pdf` | `finding` | 100% | 0 | 0% | unclassified 10 |
| `E-12` | `zarqelune_vantrel_package.pdf` | `finding` | 100% | 0 | 0% | unclassified 10 |
| `E-13` | `zarqelune_vantrel_package.pdf` | `finding` | 100% | 0 | 0% | unclassified 10 |
| `E-14` | `zarqelune_vantrel_package.pdf` | `finding` | 100% | 0 | 0% | unclassified 10 |
| `E-15` | `zarqelune_vantrel_package.pdf` | `finding` | 30% | 0 | 0% | unclassified 3 |
| `E-16` | `zarqelune_vantrel_package.pdf` | `finding` | 100% | 0 | 0% | unclassified 10 |
| `E-17` | `zarqelune_vantrel_package.pdf` | `finding` | 100% | 0 | 0% | unclassified 10 |
| `E-18` | `zarqelune_vantrel_package.pdf` | `no_finding` | n/a | 0 | 0% | no findings |
| `E-19` | `zarqelune_vantrel_package.pdf` | `no_finding` | n/a | 0 | 0% | no findings |

| Specification | Submitted document | Runs | Unattributed false positives | Rejections | Retries | Model turns | Mean prompt tokens | Model tool calls per run | Self-check rejections | Runs that returned a rejected quote |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `nimbrin_thermal_annex_specification.pdf` | `zarqelune_vantrel_package.pdf` | 10 | 0 | 0 | 0 | 10 | 274534.9 | 27.2 | 0 | 0 |

- The verification gate rejected nothing and the runtime retried nothing in this lane. The model cited every quote correctly on the first turn, so the rejection-and-retry loop did not fire. These numbers are therefore not evidence that the loop works. The loop is covered by the test suite, which drives rejections deterministically.
- The model's own quote self-check rejected nothing in this lane. The self-check therefore changed no answer here, and these numbers are not evidence that it would. The runtime gate ran on every claim regardless.
- 63 of 63 persisted findings carry `unclassified` severity. Severity classification fell back for those findings and the reason is recorded on each one. A fallback never blocks an audit and never changes verification status.

Run identifiers behind this section:

- `zarqelune_vantrel_package.pdf`: `83b6a153ec974234ba84a863a9528e65`, `3c4180ccf8974286b89c0ee1b20f5e21`, `d4ec0737a57f419db579593c22f4d06c`, `a6beed5a9b0c40b7a58fc7fc44b43d96`, `95807c6c72754b648a3c3704144ab044`, `09c5cd0ed2ea4298b669c53dfb7a80bb`, `d98205489a854234ae66059c23ad60bd`, `9f8fefc3586a40b19d6aee9cf64791c6`, `ad8fc3147e0a476e8d182163950526c5`, `4345769231b14904b5a762fd5de9f708`

## Ship gate

```
SHIP GATE navigate SHIPS as default
  [PASS] E-02 catch rate is 100%: measured 100%
  [PASS] E-03 catch rate is 100%: measured 100%
  [PASS] E-01 false positives are 0: measured 0
  [PASS] E-04 quarantine rate is 100% with 0 model turns: measured 100% with 0 model turns
  [PASS] no original-lane catch-rate regression against full_text: every case held or improved
  [PASS] no original-lane false-positive regression against full_text: navigate 0, full_text 0
  [PASS] messy-lane catch rate is at least full_text's: navigate 90%, full_text 86%
  [PASS] messy-lane decoy false positives are at most full_text's: navigate 0, full_text 0
```

The conditions above were fixed in the phase work order before any run. A pure function evaluates them over these results, and the test suite exercises that same function against known-good and known-bad inputs. No condition was relaxed and no run was repeated to reach this verdict.

## Cases that did not match the manifest

- `E-15` in `full_text` mode (`zarqelune_vantrel_package.pdf`), expected `finding`: 0 of 10 runs caught the expected pair, 0 decoy false positives, 0 quarantines, 10 model turns.
- `E-15` in `navigate` mode (`zarqelune_vantrel_package.pdf`), expected `finding`: 3 of 10 runs caught the expected pair, 0 decoy false positives, 0 quarantines, 10 model turns.

These numbers are published as measured. The README states the same figures and does not describe the runtime as catching everything.

## Scope of these numbers

This evaluation measures seven committed fictional fixtures, not a corpus of real submittals. It reports how the runtime behaved on documents built to carry known discrepancies. It is not evidence of accuracy on documents outside this set, and it is not a compliance determination.
