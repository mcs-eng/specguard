# SpecGuard measured evaluation

- Date: 2026-08-22
- Code revision these numbers describe: `8854969`
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

Numbers moved from the 2026-08-21 run. These cases now measure differently: `E-02`, `E-03`. The table above is the current measurement; the earlier figures are superseded, not corrected.

## Cases that did not match the manifest

None. Every case matched its declared expected outcome in every run.

## Run identifiers

Every run below is a real Firestore run. These identifiers are the receipt behind the table: each one can be queried against the `findings`, `rejections`, and `integrity_findings` collections.

- `E-01` `caldra_meridian_480v_switchboard.pdf`: `2060d3cf7cba42db95d839a41ec7509c`, `9fc82b83197d4fedafc1060b973c8cf1`, `6974a9c8803646a8915e92adc5eb81b7`, `8db75761415e46299fa4f6aa6c770776`, `15a6753a0af9415e9273b4bc461806f1`
- `E-02` `veylan_arcworks_208v_switchboard.pdf`: `c97efb783d9f468fab074152742493b5`, `baad412500ce46fd9343981cd1aac533`, `a552449fabdd455c9dc55952cedb5fd3`, `01cb2fd25fbd40ab91393dff5a6f88d7`, `838db4b5cc6d4d79852dcf83325d0d8e`
- `E-03` `torven_70c_termination_switchboard.pdf`: `3dc4457a9a054c19915a8ef9585469a8`, `839174915421460188847a20279507cd`, `4c838e10d82e4d7cbeba96abb655a304`, `b6c7e0e5bb0a4a19ae9a5eae3b79c2e4`, `eea54cbf8fbd4fbcafc89b256300239f`
- `E-04` `veylan_arcworks_208v_altered.pdf`: `51daa926511a4e5c8b4baf9245405d43`, `9aaf25315ec847fa95b7fa0dd40fa944`, `92ec13ea181c48d2aaf0985378dfb3c2`, `61647854ff664661a7070a69a69b960c`, `0027c70e399d4d55bf34e152265c8912`

## Scope of these numbers

This evaluation measures five committed fictional fixtures, not a corpus of real submittals. It reports how the runtime behaved on documents built to carry known discrepancies. It is not evidence of accuracy on documents outside this set, and it is not a compliance determination.
