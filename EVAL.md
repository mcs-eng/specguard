# SpecGuard measured evaluation

- Date: 2026-08-21
- Iterations per case: 5
- Audit model: `gemini-3.7-flash` via Vertex AI
- Severity model, measured from the persisted findings: `google-gemma3-gemma-3-1b-it`
- Severity endpoint, as the operator named it: `google-gemma3-gemma-3-1b-it` on a Vertex AI Model Garden endpoint (g2-standard-12, 1x NVIDIA_L4)
- Total Vertex spend: not visible in the run output
- Audit cases: 4, drawn from the five committed fixture PDFs
- Total real runs: 20

Every number below comes from one receipted execution of `scripts/eval_fixtures.py` against the deployed model path. The expected outcome of each case is read from the `eval-cases` block in `fixtures/MANIFEST.md`, not from this file.

## Results

| Case | Cut sheet | Expected outcome | Catch rate | False positives | Rejections | Retries | Quarantine rate | Model calls | Severity distribution |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| `E-01` | `caldra_meridian_480v_switchboard.pdf` | `no_finding` | n/a | 0 | 0 | 0 | 0% | 5 | no findings |
| `E-02` | `veylan_arcworks_208v_switchboard.pdf` | `finding` | 100% | 0 | 0 | 0 | 0% | 5 | high 3, unclassified 2 |
| `E-03` | `torven_70c_termination_switchboard.pdf` | `finding` | 100% | 0 | 0 | 0 | 0% | 5 | high 4, unclassified 1 |
| `E-04` | `veylan_arcworks_208v_altered.pdf` | `quarantine` | n/a | 0 | 0 | 0 | 100% | 0 | no findings |

## What each column means

- **Catch rate** is the fraction of runs that persisted a finding whose two quotes and two page numbers equal the evidence pair the manifest records for that case. A near miss is not a catch. A case that plants nothing has no catch rate and reads `n/a`, because reporting 100 percent for an unmeasured case would inflate the average.
- **False positives** counts every persisted finding that is not the expected pair. For the compliant cut sheet, every persisted finding is a false positive.
- **Rejections** counts claims the verification gate refused. A rejection is the gate working, not a failure of the run.
- **Retries** counts claims sent back to the model once after a gate rejection.
- **Quarantine rate** is the fraction of runs the text-layer integrity screen stopped before any model call.
- **Model calls** counts real audit turns. The altered fixture must show zero.
- **Severity distribution** counts the Gemma severity labels across every persisted finding of that case.

## Headline numbers

- Catch rate across every planted discrepancy: **100%** (10 of 10 runs).
- False positives on the compliant cut sheet: **0** across 5 runs.
- Quarantine rate on `veylan_arcworks_208v_altered.pdf`: **100%** with 0 model calls.

## What this run did not exercise

- The verification gate rejected nothing and the runtime retried nothing in this run. The model cited every quote correctly on the first turn, so the rejection-and-retry loop did not fire. These numbers are therefore not evidence that the loop works. The loop is covered by the test suite, which drives rejections deterministically.
- 3 of 10 persisted findings carry `unclassified` severity. Severity classification fell back for those findings and the reason is recorded on each one. A fallback never blocks an audit and never changes verification status.

## Cases that did not match the manifest

None. Every case matched its declared expected outcome in every run.

## Run identifiers

Every run below is a real Firestore run. These identifiers are the receipt behind the table: each one can be queried against the `findings`, `rejections`, and `integrity_findings` collections.

- `E-01` `caldra_meridian_480v_switchboard.pdf`: `24dcc831fd3f4b70ba443f5df6a4e48d`, `dd319c65825a4ab89416a507870e710f`, `9bac53ab44d745a683c8263729ff1775`, `f9be193e35d54a3d848c05ba938724db`, `29d9710ce2b043b9a01623f16efc3e90`
- `E-02` `veylan_arcworks_208v_switchboard.pdf`: `d702928eb1fe443b879167bedf14341a`, `d1bbd6f9383546b5818c56d56eb1fccb`, `8b4a29f7365a47dda5f23b90c6800aaf`, `c5caa746cf284b85bc9523706493e670`, `2b5c5078bf134d4c9dd30ee32be644f2`
- `E-03` `torven_70c_termination_switchboard.pdf`: `79e6f73c97fc4b99a2f0ecc19a1de756`, `c244c73adbda472d853aa3ad1d6b5d63`, `f420b3f5ac2046389fca28aff07e6a7d`, `52f096802b024bc7859f5b83671012fd`, `f46e5b48a2464593945bddeea186d887`
- `E-04` `veylan_arcworks_208v_altered.pdf`: `b4df1d9ede32493aa6f2c21f8fdb04c0`, `eebb7c6053fd4c36b9fcaeadb99534a4`, `5b55fc1217c142b0a79b20935dae2c17`, `70a1d606af8440f1b6faff226483fc71`, `81e8748240c747cca68f29ddf06a102d`

## Scope of these numbers

This evaluation measures five committed fictional fixtures, not a corpus of real submittals. It reports how the runtime behaved on documents built to carry known discrepancies. It is not evidence of accuracy on documents outside this set, and it is not a compliance determination.
