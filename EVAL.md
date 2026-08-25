# SpecGuard measured evaluation

- Date: 2026-08-25
- Code revision these numbers describe: `31ef186`
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

The two agent modes differ only in what the model is shown and which tools it may call. `full_text` sends every page of both documents up front and needs no tool call, though all five tools stay registered. `navigate` sends the submitted document in full plus a deterministic page index of the specification, and registers three read-only tools. The verification gate runs on every claim in both modes, and the runtime owns every write in both modes.

## What each column means

- **Catch rate** is the fraction of runs that persisted a finding carrying the same evidence as the pair the manifest records for that case. Each side must cite the page the manifest records, and the persisted quote and the manifest quote must contain one another in either direction after the verification gate's own normalization, on the token boundaries that gate uses. A longer or shorter span of the same passage on the cited page is the same evidence. A quote that only overlaps the planted passage is not, and the planted words on another page are not. A case that plants nothing has no catch rate and reads `n/a`, because reporting 100 percent for an unmeasured case would inflate the average.
- **Decoy false positives** counts persisted findings that reproduced a compliant near-match pair the manifest records as a decoy, under the same evidence rule, so a shortened span of a decoy is counted here rather than as an invention. The wording differs between the two documents but the submission complies, so a finding here is a wording difference read as a conflict.
- **Unattributed false positives** counts persisted findings that carried neither a planted pair nor a decoy pair under that rule. It belongs to the audit, not to any one case, which is why it appears only in the per-run table.
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

This file is written in full by `scripts/eval_fixtures.py` on every run. Nothing in it is carried forward by hand, so a section that stood in an earlier edition and is absent here was not preserved: it was replaced by this measurement. These numbers were measured at 10 audits per document pair on code revision `31ef186`. Any earlier published measurement taken at a different iteration count, a different code revision, or under a different scoring rule is superseded by this one, not corrected by it. The two records describe different runs and are not comparable cell by cell.

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
| `asterquay_learning_workshop_specification.pdf` | `caldra_meridian_480v_switchboard.pdf` | 10 | 0 | 0 | 0 | 10 | 4544.0 | 1.0 | 0 | 0 |
| `asterquay_learning_workshop_specification.pdf` | `veylan_arcworks_208v_switchboard.pdf` | 10 | 0 | 0 | 0 | 10 | 16831.2 | 3.8 | 11 | 0 |
| `asterquay_learning_workshop_specification.pdf` | `torven_70c_termination_switchboard.pdf` | 10 | 0 | 0 | 0 | 10 | 18324.0 | 4.3 | 22 | 0 |
| `asterquay_learning_workshop_specification.pdf` | `veylan_arcworks_208v_altered.pdf` | 10 | 0 | 0 | 0 | 0 | n/a | 0.0 | 0 | 0 |

- The verification gate rejected nothing and the runtime retried nothing in this lane. The model cited every quote correctly on the first turn, so the rejection-and-retry loop did not fire. These numbers are therefore not evidence that the loop works. The loop is covered by the test suite, which drives rejections deterministically.
- This lane ran in `full_text` mode, which sends every page of both documents up front, so the model needs no tool call to read them. The mode still registers all five tools, and the tool-call column records the calls the model chose to make, including the structured-output call ADK adds for this model.
- The model's own quote self-check rejected 33 quotes, and 0 runs still returned a quote their own check had rejected. The runtime never trusted that check: its gate ran on every claim, and again before any write.
- 20 of 20 persisted findings carry `unclassified` severity. Severity classification fell back for those findings and the reason is recorded on each one. A fallback never blocks an audit and never changes verification status.

Run identifiers behind this section:

- `caldra_meridian_480v_switchboard.pdf`: `380be6c7419f470897c58f1f944e12d8`, `679fa9c4c6d145af8f34158f9dff4c68`, `34152f29230043769c296d322ae09c25`, `6c0a90b31a66495fb131edf4002b9333`, `6259049d5e14425a85a70035b725b28e`, `50028dcd515c49f7a599021cea88ff26`, `cd99e9469c0445d691c51e26aa691e87`, `411c5c3426fc437d8685e5270cbaafad`, `33b4143545c445cb97b1b8b64a455b4c`, `e3b9e489e85d498ca4c6b4c8f2422c13`
- `veylan_arcworks_208v_switchboard.pdf`: `9e6038298c044337b7087725a82c6f6c`, `a39cec5b0d4c4093bf623fdce4d692a2`, `19a971d0e5a347479bbd094ebf64068e`, `19258b0536fe47a4b0a32ce9c00d4a82`, `739aa18d024a4d76a00f081012697168`, `cdf8da1a6afa40e4acfce8e50a91b636`, `2e6f4fee900c4b379bdc9e91ddedf62a`, `e7e03cd45aa34d87a929721059c5ef7a`, `6f63a9aa5a644e5abfc4e99e68b7e259`, `8333cfe1ff9d4713b2b776ebc500aab7`
- `torven_70c_termination_switchboard.pdf`: `978ea5bb33f64fd884336af60449c395`, `8c2f199dc27c4f2a9692de813f1a4c20`, `2a98ba4d2c0f440ebb0844535d709408`, `a6437adeba974198a4ab22ef33c84645`, `e91f57c3095f4650a17d78eb7db4a7c0`, `f8f2f968e36f4414b2a7cc649dc8a847`, `a537d88906a14207aa137e9540e08ef1`, `378352bafddc46ab87687725450c005e`, `8cc9ba0b881f4317aaee271a141dd839`, `8f2ffe50d2ae4ee484a744ad0703a9cb`
- `veylan_arcworks_208v_altered.pdf`: `72f4861ce9ae4f408530d1fe42713183`, `6ae55adfb64e4c77805eda8cb9d8ae52`, `b587631d5dbd435e8af2baa64dd3ea5a`, `771ad8436e634d07b033ff6662283fd8`, `11072b1ff1654d9e9891b479aa8fe41c`, `1564bf5d5e86474083839e162f7c0c89`, `83b8d0bd6ce04df4bbb297f1b57cb39b`, `93dad022b6f547459e4da3080582515a`, `d641661951f24a048621632539283fb8`, `017bf516c4d348429c9e5da8507e4544`

## Results — `full_text` mode, messy package lane

10 audits over 1 document pair(s), scoring 9 declared case(s).

| Case | Submitted document | Expected outcome | Catch rate | Decoy false positives | Quarantine rate | Severity distribution |
| --- | --- | --- | ---: | ---: | ---: | --- |
| `E-11` | `zarqelune_vantrel_package.pdf` | `finding` | 100% | 0 | 0% | unclassified 10 |
| `E-12` | `zarqelune_vantrel_package.pdf` | `finding` | 100% | 0 | 0% | unclassified 10 |
| `E-13` | `zarqelune_vantrel_package.pdf` | `finding` | 90% | 0 | 0% | unclassified 9 |
| `E-14` | `zarqelune_vantrel_package.pdf` | `finding` | 100% | 0 | 0% | unclassified 10 |
| `E-15` | `zarqelune_vantrel_package.pdf` | `finding` | 80% | 0 | 0% | unclassified 8 |
| `E-16` | `zarqelune_vantrel_package.pdf` | `finding` | 100% | 0 | 0% | unclassified 10 |
| `E-17` | `zarqelune_vantrel_package.pdf` | `finding` | 100% | 0 | 0% | unclassified 10 |
| `E-18` | `zarqelune_vantrel_package.pdf` | `no_finding` | n/a | 0 | 0% | no findings |
| `E-19` | `zarqelune_vantrel_package.pdf` | `no_finding` | n/a | 0 | 0% | no findings |

| Specification | Submitted document | Runs | Unattributed false positives | Rejections | Retries | Model turns | Mean prompt tokens | Model tool calls per run | Self-check rejections | Runs that returned a rejected quote |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `nimbrin_thermal_annex_specification.pdf` | `zarqelune_vantrel_package.pdf` | 10 | 0 | 0 | 0 | 10 | 258667.6 | 21.4 | 20 | 0 |

- The verification gate rejected nothing and the runtime retried nothing in this lane. The model cited every quote correctly on the first turn, so the rejection-and-retry loop did not fire. These numbers are therefore not evidence that the loop works. The loop is covered by the test suite, which drives rejections deterministically.
- This lane ran in `full_text` mode, which sends every page of both documents up front, so the model needs no tool call to read them. The mode still registers all five tools, and the tool-call column records the calls the model chose to make, including the structured-output call ADK adds for this model.
- The model's own quote self-check rejected 20 quotes, and 0 runs still returned a quote their own check had rejected. The runtime never trusted that check: its gate ran on every claim, and again before any write.
- 67 of 67 persisted findings carry `unclassified` severity. Severity classification fell back for those findings and the reason is recorded on each one. A fallback never blocks an audit and never changes verification status.

Run identifiers behind this section:

- `zarqelune_vantrel_package.pdf`: `07c19062352f4268b70fd5051ad9e1f7`, `1092e97bacb54baf9bff8d692e2e7186`, `0f9611e6328a4d7585461e20c777471b`, `e25070421ab046db84ef29c870b07044`, `c9d2a80996c1431a8bdc2eb12672b562`, `7d6fa72a381844ceafba3f6717fc5155`, `c224585696f94734a6a4b8f54bd6db1e`, `bc6516c4a8e44db0b9ec95bb82a5a4a2`, `2f62ff0d95d04c0aa4e014ef571b540a`, `351cf73be6194f78a39a185d4f73193a`

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
| `asterquay_learning_workshop_specification.pdf` | `caldra_meridian_480v_switchboard.pdf` | 10 | 0 | 0 | 0 | 10 | 28686.4 | 7.9 | 0 | 0 |
| `asterquay_learning_workshop_specification.pdf` | `veylan_arcworks_208v_switchboard.pdf` | 10 | 0 | 0 | 0 | 10 | 28617.7 | 8.8 | 0 | 0 |
| `asterquay_learning_workshop_specification.pdf` | `torven_70c_termination_switchboard.pdf` | 10 | 0 | 0 | 0 | 10 | 32992.1 | 9.7 | 0 | 0 |
| `asterquay_learning_workshop_specification.pdf` | `veylan_arcworks_208v_altered.pdf` | 10 | 0 | 0 | 0 | 0 | n/a | 0.0 | 0 | 0 |

- The verification gate rejected nothing and the runtime retried nothing in this lane. The model cited every quote correctly on the first turn, so the rejection-and-retry loop did not fire. These numbers are therefore not evidence that the loop works. The loop is covered by the test suite, which drives rejections deterministically.
- The model's own quote self-check rejected nothing in this lane. The self-check therefore changed no answer here, and these numbers are not evidence that it would. The runtime gate ran on every claim regardless.
- 20 of 20 persisted findings carry `unclassified` severity. Severity classification fell back for those findings and the reason is recorded on each one. A fallback never blocks an audit and never changes verification status.

Run identifiers behind this section:

- `caldra_meridian_480v_switchboard.pdf`: `b42189e3efc04219abda1a08e7d8c195`, `9ed863e3b5044c83930f6365db49e450`, `11d51efb6e9a467ca70de53d2f7e961b`, `11da4cd3669b4c7cae7421abceab72e2`, `3ea38696e48444c2a5b0cff0ff86d9e2`, `6b0b05f6db074f72aa522aad9890a4b9`, `f2a6794351f94f3eaf71d0bd449ee3fe`, `4f85aebb47a340439c9ffb6d89c27bc6`, `8e478eaddae0472abf8aa33c3788cdf3`, `5b32a0b401104946b7b62555d9731dcc`
- `veylan_arcworks_208v_switchboard.pdf`: `c47811d66d0a46f7965b7063b6ba49e1`, `0ee94a4b298741839d73b8ba4b8bac9c`, `72f72a87f93c4cf49a5a528b637f7404`, `ce25b18f8eda4242a782114ea8fcce44`, `f78a18f2de194156a1e22d602d41e729`, `3e5474e0c7484608aed0a11f97b72920`, `ef57b798fe064ac9b63efe2da6e3fdd2`, `ccf16500eb8144399496bb3e2173504a`, `d5a8aa1ffce24081bc7d250aa930c856`, `1c9e6d90360d4ac3adbfcaa815b3ecff`
- `torven_70c_termination_switchboard.pdf`: `69c281806f7f41cf95ffc84602cf2cb1`, `b22a97eb724b488fa3cb79461279788e`, `eeb9481f20cc4699b7181caa80f4d269`, `6702e0bd5ef1416499a899a85fe26fa0`, `1385e984b0e340769777317b808399c4`, `f12054c661ad4940a6472b000d055c77`, `6c1f2a84b2a447eaaac085c98a2fbdbe`, `abc3ca7855244f2b9d292871ac3a4f8a`, `5cf30a6999a94dbe8db89612b07eccde`, `3b1796e33f4046ae8f205ce63e73af51`
- `veylan_arcworks_208v_altered.pdf`: `f44ab2d654074b889557df8676b6fdee`, `9e6a62ad9752438289a248e4f16ccb5c`, `aa3ea8b6bbe4430ea7dc0afb836a8053`, `a5ec5365409a4b4fb229bd555aee1d33`, `08d41828029a4a9f8e38649110d2245d`, `1fa3f95a0f114d2fa7e095fe3c489624`, `ce242a913cb548f4b8d85ff9fae4dcdb`, `9e3bb7d1baa543e9b12f14e152a9c16c`, `f0a85d82e6bc4e278f4cef3624764d92`, `4912032724104ec09615c96db180bae6`

## Results — `navigate` mode, messy package lane

10 audits over 1 document pair(s), scoring 9 declared case(s).

| Case | Submitted document | Expected outcome | Catch rate | Decoy false positives | Quarantine rate | Severity distribution |
| --- | --- | --- | ---: | ---: | ---: | --- |
| `E-11` | `zarqelune_vantrel_package.pdf` | `finding` | 100% | 0 | 0% | unclassified 10 |
| `E-12` | `zarqelune_vantrel_package.pdf` | `finding` | 100% | 0 | 0% | unclassified 10 |
| `E-13` | `zarqelune_vantrel_package.pdf` | `finding` | 100% | 0 | 0% | unclassified 10 |
| `E-14` | `zarqelune_vantrel_package.pdf` | `finding` | 100% | 0 | 0% | unclassified 10 |
| `E-15` | `zarqelune_vantrel_package.pdf` | `finding` | 60% | 0 | 0% | unclassified 6 |
| `E-16` | `zarqelune_vantrel_package.pdf` | `finding` | 100% | 0 | 0% | unclassified 10 |
| `E-17` | `zarqelune_vantrel_package.pdf` | `finding` | 100% | 0 | 0% | unclassified 10 |
| `E-18` | `zarqelune_vantrel_package.pdf` | `no_finding` | n/a | 0 | 0% | no findings |
| `E-19` | `zarqelune_vantrel_package.pdf` | `no_finding` | n/a | 0 | 0% | no findings |

| Specification | Submitted document | Runs | Unattributed false positives | Rejections | Retries | Model turns | Mean prompt tokens | Model tool calls per run | Self-check rejections | Runs that returned a rejected quote |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `nimbrin_thermal_annex_specification.pdf` | `zarqelune_vantrel_package.pdf` | 10 | 0 | 0 | 0 | 10 | 312349.0 | 31.9 | 0 | 0 |

- The verification gate rejected nothing and the runtime retried nothing in this lane. The model cited every quote correctly on the first turn, so the rejection-and-retry loop did not fire. These numbers are therefore not evidence that the loop works. The loop is covered by the test suite, which drives rejections deterministically.
- The model's own quote self-check rejected nothing in this lane. The self-check therefore changed no answer here, and these numbers are not evidence that it would. The runtime gate ran on every claim regardless.
- 66 of 66 persisted findings carry `unclassified` severity. Severity classification fell back for those findings and the reason is recorded on each one. A fallback never blocks an audit and never changes verification status.

Run identifiers behind this section:

- `zarqelune_vantrel_package.pdf`: `73b855cdfc3e470ab182954a9da32093`, `d2632231c3e24e13a6ee817f055ae4f5`, `607fd3835d5c41e99fd99e9697771b41`, `d0cd96909a0545159853178feea577e7`, `9cfba60c54594dd187c032e9d647ff85`, `2ea145d7660541de95a0748011e7c2a1`, `18430e3cdede49279707d894c61996d3`, `1beee85d8fc44e188b0270a3bcb1d931`, `b1e6a463fd104079974323ebe7477639`, `30665b58e20d4b1eaa435a7f4aac2795`

## Ship gate

```
SHIP GATE navigate does NOT ship
  [PASS] E-02 catch rate is 100%: measured 100%
  [PASS] E-03 catch rate is 100%: measured 100%
  [PASS] E-01 false positives are 0: measured 0
  [PASS] E-04 quarantine rate is 100% with 0 model turns: measured 100% with 0 model turns
  [PASS] no original-lane catch-rate regression against full_text: every case held or improved
  [PASS] no original-lane false-positive regression against full_text: navigate 0, full_text 0
  [FAIL] messy-lane catch rate is at least full_text's: navigate 94%, full_text 96%
  [PASS] messy-lane decoy false positives are at most full_text's: navigate 0, full_text 0
```

The conditions above were fixed in the phase work order before any run. A pure function evaluates them over these results, and the test suite exercises that same function against known-good and known-bad inputs. No condition was relaxed and no run was repeated to reach this verdict.

## Cases that did not match the manifest

- `E-13` (`zarqelune_vantrel_package.pdf`), expected `finding`: 9 of 10 runs caught the expected pair, 0 decoy false positives, 0 quarantines, 10 model turns.
- `E-15` (`zarqelune_vantrel_package.pdf`), expected `finding`: 8 of 10 runs caught the expected pair, 0 decoy false positives, 0 quarantines, 10 model turns.
- `E-15` (`zarqelune_vantrel_package.pdf`), expected `finding`: 6 of 10 runs caught the expected pair, 0 decoy false positives, 0 quarantines, 10 model turns.

These numbers are published as measured. The README states the same figures and does not describe the runtime as catching everything.

## Scope of these numbers

This evaluation measures seven committed fictional fixtures, not a corpus of real submittals. It reports how the runtime behaved on documents built to carry known discrepancies. It is not evidence of accuracy on documents outside this set, and it is not a compliance determination.
