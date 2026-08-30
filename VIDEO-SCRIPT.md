# SpecGuard demo video: shooting script (4:00)

Drafted 2026-08-21 from the Video section of PLAN.md and refreshed 2026-08-29 from the serving service. Shot times are targets. The contest rule is a hard ceiling: "It should not be longer than 4 minutes. If it is longer than 4 minutes, only the first 4 minutes may be evaluated." Host the file publicly on YouTube or Vimeo. The current shoot variant uses public fictional sample buttons and the already-serving Cloud Run revision; it neither enters the upload passphrase nor changes a Cloud resource.

## Continuous-segment rules (from PLAN.md, binding)

- The core segment, 1:00 to 3:00, is one unbroken screen recording of the deployed service. It is the only part of the video claimed as unbroken.
- Narration may be re-recorded over it.
- Slides before and after are separate cuts.
- Never claim the whole video is one take.
- The integrity beat (3:00 to 3:30) may ride inside the continuous segment if latency allows; otherwise it is its own cut and is not described as part of the unbroken segment.
- Pre-stage the public sample buttons, the tabs, and the terminal font. Expect 3 to 5 takes.

## Honesty boundary for the whole video

- The agent proposes; the runtime disposes. The deployed default is full-text mode: the model receives every page of both documents up front. Both modes register the same three read-only tools and neither of the two that write, so the model reads in either mode and writes in neither. The run page still lists every tool call the model chose to make. Say the model "proposed the claim"; do not say it owns the pipeline or performs the verification, because the runtime re-verifies every claim and owns every write. Navigate mode is built, measured, and selectable, and it is the mode to describe if a judge asks what happens when a specification is too large to send in one prompt. Do not describe the live demo as navigating by tool.
- If a judge asks which mode is better on the numbers, the answer is that the two 2026-08-25 campaigns disagree by a few case-runs in seventy and neither margin is a capability gap. Full text is the default because the mode question closed with the first campaign, and the handling of the second was recorded before it ran: its ship-gate output is informational, not a decision input. Do not present either campaign as settling the comparison, and do not average them.
- "VERIFIED" means the quoted characters were found on the cited page. Do not say "verified as correct" or "confirmed accurate".
- The rejection-and-retry loop does not fire live: across the 80 model-reaching runs of the 2026-08-25 campaign at code revision `8900a76` (EVAL.md) the gate rejected nothing, because the model cited every quote correctly on the first turn. It is proven by the test suite and shown from pytest on camera. Never imply it fires in the live demo. The live "blocked before the model" beat is the integrity quarantine, which is deterministic.
- SHA-256 is chain-of-custody metadata. Do not call it proof of accuracy.
- The Gemma label is advisory and falls back to UNCLASSIFIED with a recorded reason when the endpoint is down. Say "advisory" on camera. The current serving revision uses that fallback; do not enable an endpoint or claim live severity scoring for this shoot.
- The eval numbers are seven fictional fixtures, 100 runs across both agent modes and both lanes. Do not call them accuracy on real submittals. Every model-initiated tool call behind them is committed to `EVAL-RECEIPTS.jsonl`, so "receipted" is a claim the file backs; do not use the word for anything that file does not carry.
- Do not read the hidden sentence from the altered fixture into this script or any other prose file. It may appear on screen in the integrity record table; reading it aloud from the screen is allowed.

## Pacing receipts

Use these to size narration over waits. No receipt exists in HANDOFF.md for end-to-end audit duration or Cloud Run cold start; the numbers below come from a read-only Firestore query on 2026-08-21 (REVIEW-CLAIMS.md, receipt R-12) and from local pytest timings.

| What | Measured | Source |
| --- | ---: | --- |
| Web audit, run record to persisted finding, first run of the day (revision 00004/00005, no Gemma) | 48.1 s | run `c3a307ab…` |
| Web audit, same delta, warm revision, Gemma endpoint up | 10.7 s | run `72e1e439…` |
| Web audit, same delta, warm revision, Gemma endpoint up | 12.2 s | run `1c94812c…` |
| Gemma endpoint worst case when it returns 502 | up to 3 attempts x 15 s timeout plus 2 s sleeps, about 50 s, then fallback | `specguard/severity.py` |
| Quarantine of the altered fixture | no model call; seconds | EVAL.md E-04, 0 model calls in 10 runs per mode |
| `uv run pytest -q tests/test_gate.py` | 59 passed in 0.96 s | local, 2026-08-25 |
| `uv run pytest -q tests/test_gate.py tests/test_agent.py` | 68 passed in 9.15 s | local, 2026-08-25 |
| `uv run pytest -q` (whole suite) | see the count README records from its own run; about 70 s locally on 2026-08-25 | local, 2026-08-25 |
| Gemma 3 1B endpoint deploy | 1 min 48 s | SETUP.md, HANDOFF.md |

Plan the continuous segment for two model runs of 10 to 15 s each on a warm revision. Budget 50 s for each if the first take is cold. The prior-day timed dry run (checklist, T-1) replaces these estimates with a fresh number.

Narration budget, measured 2026-08-26 by counting every primary paragraph in this file: **579 words**, which is 3:44 of audio at 155 words per minute or 3:37 at 160. Against the 4:00 ceiling that leaves 16 to 23 seconds of unnarrated screen time, so the delivery must stay at or above 155 words per minute and long waits must carry narration. The bracket on each paragraph is its actual word count. An earlier draft of this script carried 778 words — unrecordable inside the ceiling — and was cut on 08-26 with every mandatory line, number, and page reference preserved.

## Shot list

Narration is written at about 150 words per minute. Word counts are in brackets.

### Shot 1, 0:00 to 0:20. Problem slide. Separate cut.

- On screen: one slide. Left: a spec line, "Provide a 480V, 3-phase distribution switchboard". Right: a cut-sheet line, "Nominal system: 208V, 3-phase, 4-wire". Below: "Submittal review misses cost real money."
- Narration [52]: "A submittal review asks one question: does the vendor's product meet what the spec requires. A 208-volt switchboard against a 480-volt spec is an expensive miss. A reviewer that can assert anything is worse than none: its output gets trusted. SpecGuard is built so it cannot assert what it did not read."
- Pre-staged: the slide.
- Honesty boundary: no numbers, no accuracy claim.

### Shot 2, 0:20 to 1:00. Architecture. Separate cut.

- On screen: the README architecture diagram (or `output/pdf/specguard-architecture.pdf`) for 20 s; then 10 s on a terminal running the read-only Cloud Run receipt below, with the public `.run.app` URL visible in the browser. This proves the serving revision without a console login or a resource change.
- Narration [67]: "One Google ADK agent on Gemini 3.7 Flash reads both documents and proposes claims, each with verbatim quotes and page numbers. Around it, a runtime the model cannot skip: an integrity screen on both text layers, a log of every tool call, a gate that must find each quote on its cited page, one bounded retry, and the gate again at write time. All on Cloud Run."
- Pre-staged: README or the PDF diagram; the public service; and this read-only command in a terminal:

```powershell
gcloud run services describe specguard --region us-central1 --project specguard-hack --format="yaml(status.url,status.latestReadyRevisionName,status.traffic)"
```
- Honesty boundary: "proposes" for the model, "cannot skip" for the runtime. The recorded tool calls are the receipt for what the model actually did; do not claim the model performs the verification.

### Shot 3, 1:00 to about 3:00. CONTINUOUS SEGMENT. One unbroken recording.

The recording starts on the landing page with the URL bar visible the whole time. Existing sample rows are harmless; do not reset the ledger for this shoot.

#### 3a, 1:00 to 1:12. Run the public 208V sample.

- On screen: click `Veylan 208V` under "Run a sample audit." The running panel appears: "Audit running. Do not submit again."
- Narration [31]: "Here is a fictional 208V sample a judge can run without a passphrase: one click. It is the deployed audit, and the page shows no progress because the server reports none."
- Pre-staged: landing page with the four public sample buttons visible.
- Honesty boundary: none beyond the page text.

#### 3b, 1:12 to about 1:40. The wait.

- On screen: the running panel. Real latency, 10 to 50 s.
- Narration over the wait [53]: "While that runs: both text layers were already screened for hidden spans, and both documents went in with page markers. The model returns structured claims. The gate then requires each quote as a contiguous match on token boundaries on its cited page. No fuzzy matching, no other page. A miss is a rejection."
- If the wait outlasts the narration, hold on the panel. Fallback filler line [25]: "The model step is the only part of this run whose duration the server cannot report, so the page shows the wait as a wait rather than animate a guess."
- Honesty boundary: describe the runtime's actions, not the model's reasoning.

#### 3c, 1:40 to 2:05. The findings page.

- On screen: the run page. Point at, in order: the COMPLETED badge; the counters Claims made 1, Findings persisted 1, Rejected 0, Retried 0; the claim text; "Specification page 3" with `Provide a 480V, 3-phase distribution switchboard for service distribution.`; "Submitted page 1" with `Nominal system: 208V, 3-phase, 4-wire.`; the severity badge HIGH with `google-gemma3-gemma-3-1b-it` under it.
- Narration [66]: "One claim, one finding. VERIFIED means one thing: these exact characters were found on page 3 of the spec and page 1 of the cut sheet. It does not say the conclusion is right — a human reads that. Rejected zero and retried zero: the model cited correctly first time, so the retry loop did not fire on this run. The HIGH label is Gemma's advisory severity."
- If the badge reads UNCLASSIFIED, use the fallback line [35]: "Severity shows UNCLASSIFIED with a recorded reason: the Gemma endpoint did not answer this call. The label is advisory. The finding stands, because the gate, not the label, decides what enters the ledger."
- Honesty boundary: the sentence "the retry loop did not fire on this run" is mandatory when the counters read zero.

#### 3d, 2:05 to 2:15. The RFI.

- On screen: click "Open the RFI draft PDF". Show the header block reading RFI number and submittal number, the heading "DRAFT - HUMAN REVIEW REQUIRED", and the finding with its two quotes, page numbers, and severity line. Then jump to the last page and hold on the signature lines with the chain-of-custody block in small type below them, ending "They do not prove accuracy."
- Narration [34]: "The RFI carries the same quotes and pages. Each file's SHA-256 sits on the last page, past the signature, as chain of custody. In print on the page: the hashes do not prove accuracy."
- Honesty boundary: "chain of custody", never "proof".

#### 3e, 2:15 to 2:50. The subtle catch.

- On screen: back to the landing page; click the `Torven 70 deg C` public sample; wait; the run page shows "Specification page 5" `Conductor terminations shall be rated 90 deg C minimum.` and "Submitted page 2" `Field conductor termination rating: 158 deg F.`; the claim text names the conversion.
- Narration over the wait and the result [53]: "Second cut sheet. The spec wants terminations rated 90 degrees Celsius; this vendor states the rating only in Fahrenheit. 158 Fahrenheit is 70 Celsius — below the requirement. The gate verified both quotes, pages 5 and 2. The conversion is the model's claim, and the quotes give the reviewer the exact place to check."
- Honesty boundary: the conversion is the model's claim, verified only as text anchors.

#### 3f, 2:50 to about 3:20. The integrity beat. Inside the segment if latency allows; otherwise its own cut.

- On screen: back to the landing page; click the `Veylan altered (integrity screen)` public sample. Within seconds the run page shows the QUARANTINED badge and the notice "The text-layer integrity screen stopped this run. Reason: text_layer_integrity_screen. No model call was made and no RFI was drafted." Scroll to "Integrity records": page 1, two hidden spans, font and size, the document SHA-256. The first span reads `Nominal system: 209V, 3-phase, 4-wire.`; the second is a sentence addressed to an automated reviewer.
- Narration [66]: "Same vendor, same visible page. This copy has two hidden spans no reader can see: a 209-volt line under the visible 208, and a sentence addressed to an automated reviewer. The screen reads the character flags before the model reads a word: the run stops. No model call, no finding, no RFI. The screen detects this one method; the README lists what it does not detect."
- Pre-staged: nothing beyond the fixture.
- Honesty boundary: "detects this one method" is mandatory. Do not say "detects tampering" in general.

End of the continuous segment. If 3f pushed past 3:30, shoot 3f as a separate cut and say nothing about it being continuous.

### Shot 4, about 3:00 to 3:30. Gate rejection from pytest, then the live playground. Separate cut. Two beats.

#### 4a, about 3:00 to 3:18. The tests.

- On screen: terminal, font 18 pt or larger. Run:

```powershell
uv run pytest tests/test_gate.py tests/test_agent.py -v
```

  About 9 to 10 s. The last lines on screen are the `tests/test_agent.py` names: `test_rejected_claim_gets_exactly_one_retry_then_rejection PASSED`, `test_one_retry_can_correct_the_quote_and_persist PASSED`, `test_retry_must_return_exactly_one_corrected_claim PASSED`, then the pass count — 68 on 2026-08-25. Read the count off the screen rather than saying it; the suite grows.
- Narration over the run [49]: "The rejection-and-retry loop is proven here, not by live luck. These tests drive a rejected quote through the runtime: the claim goes back to the model once with the machine-readable reason, a corrected quote is verified again before it persists, and a second miss is recorded as a rejection."
- Pre-staged: the command typed and ready; `uv sync` done; one dry run so the cache is warm.
- Honesty boundary: "proven here, not by live luck" is the mandatory framing.

#### 4b, about 3:18 to 3:30. Run the gate yourself.

- On screen: the browser tab already on `/gate`. The prefilled example is `Conductor terminations shall be rated 90 deg C minimum.` on page 5 of the specification, and the verdict card reads VERIFIED with page count 7 and no rejection reason. Click the first near-miss link, "One digit changed: 90 becomes 80". The card flips to REJECTED with the machine reason `quote_not_found_on_cited_page`. Do not type; one click is the whole beat.
- Narration [35]: "This is the same gate function, live. The real sentence verifies against page 5. Change one digit and it is refused, with the machine reason. No model runs here. Judges can try their own quotes."
- Pre-staged: the `/gate` tab loaded and scrolled so the verdict card and the near-miss links are both visible.
- Honesty boundary: "the same gate function" is accurate and is the claim to make. Do not say the page proves the ledger; it proves the gate verdict only.

### Shot 5, about 3:30 to 3:47. The eval table and its receipts. Separate cut.

- On screen: EVAL.md, the header naming the code revision and the deployed default, one results table, and the "Cases that did not match the manifest" section. Then a two-second flick to `EVAL-RECEIPTS.jsonl` scrolled anywhere in its middle: dense JSON lines, one per model-initiated tool call. Do not zoom in far enough to read one; the point is that the file exists and is committed.
- Narration [43]: "A hundred real runs on the fixtures. Both original-lane discrepancies caught in every run of both modes; no false positive, no decoy hit. On the hard lane, ninety in navigate against eighty-six in full text — three case-runs in seventy, noise, not a winner."
- If the timing carries the receipts flick, append the extended close [12]: "Every tool call behind those numbers is committed, one line each." Without the flick, end on "not a winner."
- Honesty boundary: "on the fixtures", never "accuracy". Read the current table before recording; the numbers below the table are regenerated by the harness, not typed here. Do not say navigate is better: name both numbers and call the gap noise.

### Shot 6, about 3:47 to 4:00. Close on the RFI. Separate cut.

- On screen: the generated RFI draft PDF, page 1, held still. The header block reads RFI number, submittal number, project, owner, and date, with no hex identifier anywhere on the page. Below it the findings table shows the claim, both quotes with their page numbers, and the severity. Below that the text-layer screen result for each document. Then one cut to the last page: the signature lines, and beneath them in small type the run identifier, the chain-of-custody hashes, and the line "They are chain-of-custody metadata only." Fade to the README headline, "Uncited claims are blocked from the ledger.", with the repo URL and the `.run.app` URL.
- Narration [30]: "What a reviewer receives: every claim with the quote and page it rests on, and hashes labelled chain of custody, not proof. Uncited claims are blocked from the ledger. SpecGuard."
- Pre-staged: the RFI PDF from the shot 3 run already open in its own tab at page 1, 125 percent zoom.
- Honesty boundary: "chain of custody", never "proof". The headline carries its README scope and nothing wider.

## Current shoot-day override — zero-change public-sample version

This section supersedes the historical `Shoot-day checklist` below. It was refreshed from the serving revision on 2026-08-29 and is the only shoot plan authorized for the current closeout.

1. Do not deploy, update, reset, warm, or tear down any Cloud resource. Do not enter the upload passphrase and do not open arbitrary uploads.
2. Before recording, run the read-only Cloud Run command in Shot 2. It must show a ready revision receiving 100% traffic. Keep the public `.run.app` service visible beside it.
3. For the continuous segment, click the public `Veylan 208V`, `Torven 70 deg C`, and `Veylan altered (integrity screen)` samples. Call them fictional public samples in narration; do not present them as operator uploads.
4. Use the run page's recorded `UNCLASSIFIED` fallback wording. The advisory label does not participate in verification.
5. Pre-stage the landing page, `/gate`, the Cloud Run receipt terminal, the architecture diagram, EVAL.md, and EVAL-RECEIPTS.jsonl. Read counters and run IDs from the screen; do not rely on examples in this file.
6. Mason records the narration. No generated voice is used. The published video is a separate action-time approval.

## Shoot-day checklist

All commands below run on arya (PowerShell) from `C:\Users\mcspd\dev\specguard`, with the Cloud SDK on `PATH` as `deploy-specguard.ps1` prefixes it. Record each command with its `$LASTEXITCODE`.

### T-1, the day before

1. Reset script dry run. Expect exit 2 and the "Refusing to reset the demo ledger without --confirm." message. Receipt the exit code.

```powershell
uv run python scripts/reset_demo_ledger.py
```

2. Timed dry run of one full audit through the deployed web UI with the Veylan 208V fixture, stopwatch from Run audit to the run page. Record the seconds. Also record the exact `severity_reason` text the finding carries with the endpoint down, read from the Firestore console; that text is what a judge sees later.
3. Quota check: `gcloud compute regions describe us-central1 --project specguard-hack --format="json(quotas)"` and confirm `NVIDIA_L4_GPUS` usage 0 of limit 1.
4. Fixture check: `uv run pytest -q tests/test_fixtures.py tests/test_integrity.py` exit 0.
5. Recorder test: one 30 s capture at the final resolution; browser zoom 125 percent; terminal font 18 pt or larger; OS notifications off.
6. Write the passphrase on paper beside the keyboard. It is not in the repo, the clipboard history, or this file.

### T, before the first take

1. Gemma endpoint up, SETUP.md "Shoot-Day Runbook" step 1 (about 2 min):

```powershell
gcloud ai model-garden models deploy --model=google/gemma3@gemma-3-1b-it --machine-type=g2-standard-12 --accelerator-type=NVIDIA_L4 --accelerator-count=1 --endpoint-display-name=specguard-gemma --region=us-central1 --project=specguard-hack --billing-project=specguard-hack --accept-eula
```

2. Read the new endpoint resource name, SETUP.md runbook step 2. The name changes on every deploy; `deploy-specguard.ps1` starts with `SPECGUARD_GEMMA_ENDPOINT=disabled`.

```powershell
$endpoint = (gcloud ai endpoints list --region=us-central1 --project=specguard-hack --filter="displayName:specguard-gemma" --format="value(name)")
```

```powershell
$endpoint
```

3. Point the deployed service at the new endpoint and hold one warm instance. This creates a new revision; `--max-instances 1` and `--concurrency 2` carry over from the last deploy.

```powershell
gcloud run services update specguard --region us-central1 --project specguard-hack --min-instances 1 --update-env-vars "SPECGUARD_GEMMA_ENDPOINT=$endpoint"
```

4. Verify the revision: the env list names the new endpoint and `minScale` reads 1.

```powershell
gcloud run services describe specguard --region us-central1 --project specguard-hack --format=yaml | Select-String -Pattern "minScale|SPECGUARD_GEMMA_ENDPOINT|latestReadyRevisionName" -Context 0,1
```

5. Endpoint warm-up calls — only if the 08-29 Gemma decision deploys the endpoint; with the sentinel disabled, skip this step. Confirm the model is attached, then run one warm-up audit through the web UI with the Veylan 208V fixture. The warm-up audit exercises Cloud Run, Vertex Gemini, and the Gemma endpoint in one pass. Check the run page: COMPLETED, one finding, HIGH (or another label) with `google-gemma3-gemma-3-1b-it` under it. If it reads UNCLASSIFIED, read the `severity_reason` in the Firestore console and wait one minute before a second warm-up; the endpoint returned HTTP 502 on 3 of 10 classification calls during the Phase 6a measurement (HANDOFF.md, Phase 6a — the current EVAL.md ran sentinel-disabled and makes no classification calls).

```powershell
gcloud ai endpoints describe $endpoint --region=us-central1 --project=specguard-hack --format="value(deployedModels[0].id)"
```

6. Ledger reset, first live run of the script. The warm-up run is archived with everything else, so the landing page starts empty. Expect the summary line "archive key …: N documents archived across 4 collections, M bucket objects archived".

```powershell
uv run python scripts/reset_demo_ledger.py --confirm
```

7. Reload the landing page and confirm "No audit runs are stored yet."
8. Tabs, in order: landing page; `/gate` with the prefilled example loaded and the verdict card visible (shot 4b); Cloud Run console service page; Vertex AI endpoints page; Firestore console on `findings`; README at the architecture diagram; EVAL.md; `EVAL-RECEIPTS.jsonl` scrolled to its middle for the shot 5 flick. Close every other tab and window. The RFI tab for shot 6 opens during shot 3d and stays open.
9. File explorer open on `fixtures\` showing the seven PDFs.
10. Terminal ready with `uv run pytest tests/test_gate.py tests/test_agent.py -v` typed and a warm cache (run it once before the take).
11. Fallback narration line for UNCLASSIFIED printed beside the keyboard (shot 3c).
12. Between takes: run the reset again if the "Recent runs" list must start empty. Each reset uses a new timestamped archive key and refuses to overwrite an earlier one.

### After the shoot, teardown

1. SETUP.md runbook step 3. Undeploy, delete the endpoint, delete the model. Then verify both lists read "Listed 0 items."

```powershell
$deployedId = (gcloud ai endpoints describe $endpoint --region=us-central1 --project=specguard-hack --format="value(deployedModels[0].id)")
```

```powershell
gcloud ai endpoints undeploy-model $endpoint --deployed-model-id=$deployedId --region=us-central1 --project=specguard-hack --billing-project=specguard-hack
```

```powershell
gcloud ai endpoints delete $endpoint --region=us-central1 --project=specguard-hack --billing-project=specguard-hack --quiet
```

```powershell
gcloud ai models list --region=us-central1 --project=specguard-hack --billing-project=specguard-hack
```

  Delete the listed model with `gcloud ai models delete <name> --region=us-central1 --project=specguard-hack --billing-project=specguard-hack --quiet`, then:

```powershell
gcloud ai endpoints list --region=us-central1 --project=specguard-hack --billing-project=specguard-hack
```

2. Release the warm instance and set the deployed revision to the severity-disabled sentinel:

```powershell
gcloud run services update specguard --region us-central1 --project specguard-hack --min-instances 0 --update-env-vars "SPECGUARD_GEMMA_ENDPOINT=disabled"
```

   A later finding records `UNCLASSIFIED`, `severity_status=fallback`, and `severity_reason=severity endpoint not deployed outside demo windows`. The audit still completes. `deploy-specguard.ps1` sets the same sentinel on later deployments.

3. Confirm the service still answers: open the landing page; the shoot's runs are listed.
4. Archive the recordings and the take log (run IDs shown on camera) outside the repo.
5. Check the billing page against the $25 alert.
