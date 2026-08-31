# SpecGuard demo video: shooting script (4:00)

Drafted 2026-08-21 from the Video section of PLAN.md and refreshed 2026-08-29 from the serving service. Shot times are targets. The contest rule is a hard ceiling: "It should not be longer than 4 minutes. If it is longer than 4 minutes, only the first 4 minutes may be evaluated." Host the file publicly on YouTube or Vimeo. The current shoot variant uses public fictional sample buttons and the already-serving Cloud Run revision; it neither enters the upload passphrase nor changes a Cloud resource.

## Continuous-segment rules (from PLAN.md, binding)

- The core segment, 0:43 to 2:47, is one unbroken screen recording of the deployed service. It is the only part of the video claimed as unbroken.
- Narration may be re-recorded over it.
- Slides before and after are separate cuts.
- Never claim the whole video is one take.
- The integrity beat is a separate cut after the continuous segment. Do not describe it as part of the unbroken recording.
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

Plan the continuous segment for one model run. It reserves the observed 10 to 50 s range, including a 50 s maximum wait. If the run has not completed by that point, stop the take and report the actual delay rather than pretending it completed.

The active sequence below ends at 4:00 and reserves 50 seconds for the one live model wait. The spoken text deliberately leaves quiet screen time; do not add narration, a second sample, or an extra evidence beat unless another beat is removed and the ending remains at or before 4:00.

## Shot list

Narration is written at about 150 words per minute. Word counts are in brackets.

### Shot 1, 0:00 to 0:18. Problem slide. Separate cut.

- On screen: one slide. Left: a spec line, "Provide a 480V, 3-phase distribution switchboard". Right: a cut-sheet line, "Nominal system: 208V, 3-phase, 4-wire". Below: "Submittal review misses cost real money."
- Narration [40]: "A submittal review asks whether a vendor product meets the spec. A 208-volt switchboard against a 480-volt requirement is an expensive miss. A reviewer that can assert anything is worse than none. SpecGuard cannot assert what it did not read."
- Pre-staged: the slide.
- Honesty boundary: no numbers, no accuracy claim.

### Shot 2, 0:18 to 0:43. Architecture. Separate cut.

- On screen: the README architecture diagram (or `output/pdf/specguard-architecture.pdf`) for 15 s; then 10 s on a terminal running the read-only Cloud Run receipt below, with the public `.run.app` URL visible in the browser. On the final deployed head, the receipt names the serving revision, immutable image digest, and recorded source SHA without a console login or a resource change.
- Narration [50]: "One Google ADK agent on Gemini 3.7 Flash reads both documents and proposes quoted, paginated claims. The runtime it cannot skip screens text layers, records model tool calls, requires a token-boundary match on the cited page, and verifies again before any write. The ready Cloud Run revision is the backend."
- Pre-staged: README or the PDF diagram; the public service; and this read-only command in a terminal:

```powershell
$expectedSource = git rev-parse HEAD
$service = gcloud run services describe specguard --region us-central1 --project specguard-hack --format=json | ConvertFrom-Json
$revision = $service.status.latestReadyRevisionName
$traffic = @($service.status.traffic | Where-Object { $_.revisionName -eq $revision })
if ($traffic.Count -ne 1 -or [int]$traffic[0].percent -ne 100) { throw "Latest ready revision does not serve 100 percent of traffic." }
gcloud run revisions describe $revision --region us-central1 --project specguard-hack --format="yaml(metadata.name,status.imageDigest,spec.containers[0].env)"
$servedSource = (Invoke-WebRequest https://specguard-108657628939.us-central1.run.app/health).Headers["X-SpecGuard-Source-Revision"]
if ($servedSource -ne $expectedSource) { throw "Serving source does not match this checkout." }
$servedSource
```
- Honesty boundary: "proposes" for the model, "cannot skip" for the runtime. The recorded tool calls are the receipt for what the model actually did; do not claim the model performs the verification. Do not call source/live alignment proven unless the SHA comparison succeeds and the revision receipt carries a nonempty image digest.

### Shot 3, 0:43 to 2:47. CONTINUOUS SEGMENT. One unbroken recording.

The recording starts on the landing page with the URL bar visible the whole time. Existing sample rows are harmless; do not reset the ledger for this shoot.

#### 3a, 0:43 to 0:51. Run the public 208V sample.

- On screen: click `Veylan 208V` under "Run a sample audit." The running panel appears: "Audit running. Do not submit again."
- Narration [18]: "A public fictional 208V sample, one click. It starts the deployed audit; the page does not invent progress."
- Pre-staged: landing page with the four public sample buttons visible.
- Honesty boundary: none beyond the page text.

#### 3b, 0:51 to 1:41. The wait.

- On screen: the running panel. Real latency, 10 to 50 s.
- Narration over the wait [37]: "Before the model responds, both text layers are screened and page-marked. It proposes structured claims. The gate accepts only a contiguous token-boundary quote on its cited page: no fuzzy match, no other page. A miss is rejected."
- If the wait outlasts the narration, hold on the panel without filling the silence.
- Honesty boundary: describe the runtime's actions, not the model's reasoning.

#### 3c, 1:41 to 2:14. The findings page.

- On screen: the run page. Point at, in order: the COMPLETED badge; the counters Claims made 1, Findings persisted 1, Rejected 0, Retried 0; the claim text; "Specification page 3" with `Provide a 480V, 3-phase distribution switchboard for service distribution.`; "Submitted page 1" with `Nominal system: 208V, 3-phase, 4-wire.`; and the current `UNCLASSIFIED` severity reason.
- Narration [49]: "One claim, one finding. VERIFIED means only these exact characters appear on spec page 3 and cut-sheet page 1. It does not say the conclusion is correct; a human decides. The model cited correctly first time, so the retry loop did not run. Severity is advisory and currently unclassified."
- Honesty boundary: say that the retry loop did not run when the counters read zero.

#### 3d, 2:14 to 2:47. The RFI.

- On screen: click "Open the RFI draft PDF". Show the header block reading RFI number and submittal number, the heading "DRAFT - HUMAN REVIEW REQUIRED", and the finding with its two quotes, page numbers, and severity line. Then jump to the last page and hold on the signature lines with the chain-of-custody block in small type below them, ending "They do not prove accuracy."
- Narration [34]: "The RFI carries the same quotes and pages. Each file's SHA-256 sits on the last page, past the signature, as chain of custody. In print on the page: the hashes do not prove accuracy."
- Honesty boundary: "chain of custody", never "proof".

End of the continuous segment. The integrity screen is separate and must not extend the live-model wait.

### Shot 4, 2:47 to 3:10. Integrity screen. Separate cut.

- On screen: back to the landing page; click the `Veylan altered (integrity screen)` public sample. Within seconds the run page shows the QUARANTINED badge and the notice "The text-layer integrity screen stopped this run. Reason: text_layer_integrity_screen. No model call was made and no RFI was drafted." Scroll to "Integrity records": page 1, two hidden spans, font and size, the document SHA-256. The first span reads `Nominal system: 209V, 3-phase, 4-wire.`; the second is a sentence addressed to an automated reviewer.
- Narration [42]: "Same visible page, but this copy hides a 209-volt line and a sentence for an automated reviewer. Character flags stop it before the model reads a word: no model call, finding, or RFI. This screen detects that one method, not every concealment."
- Pre-staged: nothing beyond the fixture.
- Honesty boundary: "detects this one method" is mandatory. Do not say "detects tampering" in general.

### Shot 5, 3:10 to 3:30. Run the gate yourself. Separate cut.

- On screen: the browser tab already on `/gate`. The prefilled example is `Conductor terminations shall be rated 90 deg C minimum.` on page 5 of the specification, and the verdict card reads VERIFIED with page count 7 and no rejection reason. Click the first near-miss link, "One digit changed: 90 becomes 80". The card flips to REJECTED with the machine reason `quote_not_found_on_cited_page`. Do not type; one click is the whole beat.
- Narration [35]: "This is the same gate function, live. The real sentence verifies against page 5. Change one digit and it is refused, with the machine reason. No model runs here. Judges can try their own quotes."
- Pre-staged: the `/gate` tab loaded and scrolled so the verdict card and the near-miss links are both visible.
- Honesty boundary: "the same gate function" is accurate and is the claim to make. Do not say the page proves the ledger; it proves the gate verdict only.

### Shot 6, 3:30 to 4:00. Close on the evidence. Separate cut.

- On screen: the generated RFI draft PDF, page 1, held still. The header block reads RFI number, submittal number, project, owner, and date, with no hex identifier anywhere on the page. Below it the findings table shows the claim, both quotes with their page numbers, and the severity. Below that the text-layer screen result for each document. Then one cut to the last page: the signature lines, and beneath them in small type the run identifier, the chain-of-custody hashes, and the line "They are chain-of-custody metadata only." Fade to the README headline, "Uncited claims are blocked from the ledger.", with the repo URL and the `.run.app` URL.
- Narration [36]: "What persists is an RFI draft, not a decision: claim, quotes, pages, and hashes labelled chain of custody, not accuracy. The repository includes reproducible setup, evaluation receipts, and this architecture. A human reviews every claim. SpecGuard."
- Pre-staged: the RFI PDF from Shot 3 and the README headline with the repository and service URLs.
- Honesty boundary: "chain of custody", never "proof". The headline carries its README scope and nothing wider.

## Current shoot-day override — zero-change public-sample version

This section supersedes the historical `Shoot-day checklist` below. It was refreshed from the serving revision on 2026-08-29 and is the only shoot plan authorized for the current closeout.

1. Do not deploy, update, reset, warm, or tear down any Cloud resource. Do not enter the upload passphrase and do not open arbitrary uploads.
2. Before recording, run the read-only Cloud Run command in Shot 2. It must show a ready revision receiving 100% traffic. Keep the public `.run.app` service visible beside it.
3. For the continuous segment, click only the public `Veylan 208V` sample. Capture `Veylan altered (integrity screen)` as the separate integrity cut. Call both fictional public samples in narration; do not present them as operator uploads.
4. Use the run page's recorded `UNCLASSIFIED` fallback wording. The advisory label does not participate in verification.
5. Pre-stage the landing page, `/gate`, the Cloud Run receipt terminal, the architecture diagram, the completed-run RFI, and the README headline. Read counters and run IDs from the screen; do not rely on examples in this file.
6. Mason records the narration. No generated voice is used. The published video is a separate action-time approval.

## Historical shoot-day checklist (archived — do not execute for the current closeout)

The zero-change public-sample override above is authoritative. This dated record is retained only as historical planning evidence; it calls for paid and mutating work that is forbidden for the current closeout.

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
