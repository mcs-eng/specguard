# SpecGuard measured evaluation

- Date: 2026-08-22
- Code revision these numbers describe: `616e9f1
 plus uncommitted changes`
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
| `E-02` | `veylan_arcworks_208v_switchboard.pdf` | `finding` | 100% | 0 | 0 | 0 | 0% | 5 | high 5 |
| `E-03` | `torven_70c_termination_switchboard.pdf` | `finding` | 100% | 0 | 0 | 0 | 0% | 5 | high 5 |
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

## Change from the previous published run

No number in this table moved from the 2026-08-22 run. Every case reports the same measurement it reported then.

## Cases that did not match the manifest

None. Every case matched its declared expected outcome in every run.

## Run identifiers

Every run below is a real Firestore run. These identifiers are the receipt behind the table: each one can be queried against the `findings`, `rejections`, and `integrity_findings` collections.

- `E-01` `caldra_meridian_480v_switchboard.pdf`: `9134a95286154248bfd075994dbbda3b`, `2571a097f280436099c72d54edfc2395`, `ffe7f914653d47f29754a6c99ac465dd`, `5cc86b4cffa146c7b4bb2ca5f8995281`, `1ff2f1a1f7ea46f2a456ae5ac909fce3`
- `E-02` `veylan_arcworks_208v_switchboard.pdf`: `57bf7a61bdf2443bb1bd2544cf16711b`, `fa18eb1afafb41c38598b0acc7d27705`, `72b1a2ed6e284573b397cabc360b9659`, `7b30609b900e49eca90e860b2a3b5a40`, `d16b7d86f1b54902bff2d92cc40d32a6`
- `E-03` `torven_70c_termination_switchboard.pdf`: `b591789741614dd0a03d5fe684d279f2`, `cc917312f05c4b398ed2c3970b08e13f`, `8956e5a2639f4ce98c5949fb81f63872`, `2b6d63e64d1c4508abb1184a8807a2bb`, `c350d41950c14e9092539d728fbdb390`
- `E-04` `veylan_arcworks_208v_altered.pdf`: `389a247376654592b423ae81d1e78b95`, `0ccb4299ba234780a3fdf9c67e6f718c`, `8c5a79f0819e45d3ba2a1ef17b70217a`, `221722141d4f402f9dfc4bacde96087f`, `dc6a727cbb804967b59c12a367c95330`

## Scope of these numbers

This evaluation measures five committed fictional fixtures, not a corpus of real submittals. It reports how the runtime behaved on documents built to carry known discrepancies. It is not evidence of accuracy on documents outside this set, and it is not a compliance determination.
