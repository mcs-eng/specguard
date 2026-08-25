# SpecGuard: Devpost submission draft

Drafted 2026-08-21 for the All Things Agentic Hackathon. Every number below traces to a receipt named beside it. Placeholders are marked `[TODO]`. Mason fills the form; this file is the text he pastes.

## Contest requirements, recorded verbatim

Source: `https://allthingsagentichackathon.devpost.com/rules` and the hackathon home page, fetched 2026-08-21. The submission form at `https://devpost.com/submit-to/30845-all-things-agentic-hackathon/manage/submissions` is behind the Devpost login, so field-level character limits could not be read; the Devpost help article "How to enter a submission" names a "project name" and an "elevator pitch (tagline)" field and states no limits. Keep the tagline under 60 characters as a margin.

- Deadline: "August 31, 2026 (5:00 P.M. Pacific Time)".
- Video length: "It should not be longer than 4 minutes. If it is longer than 4 minutes, only the first 4 minutes may be evaluated."
- Video hosting: the video "must be uploaded to and made publicly visible on YouTube or Vimeo."
- Video content: it "must demonstrate the backend is running on Google Cloud (ie: Google Cloud Console, Cloud Run dashboard, Vertex AI logs, URL of .run, etc)" and include "a short overview of the problem your Project is solving, the value proposition as well as a demo of the application in action."
- Description: "Include a text description that should include a summary of the Project's features and functionality, technologies used". No length limit is stated.
- Track field: "Select one category which represents your project". Categories: "Taskmaster," "Collaborative Partner," and "Fortified Enterprise Fleet." Also: "The Sponsor and Administrator reserve the right to reassign a Submission from one category to another if applicable."
- Bonus models: "Earn 0.2 bonus points for each additional Google AI model successfully integrated (such as Gemma, Veo, or Lyria), up to a maximum of 0.6 total bonus points." Separate content and social bonuses: "A maximum of 0.2 points will be added" for each.
- AI tools: "Participants may use standard development tools, including frameworks, libraries, starter templates, and AI coding assistants, but must disclose any other pre-existing code or work incorporated into the Project." No field declaring AI assistant use is stated on the rules page; the disclosure duty is for pre-existing code.
- Mandatory stack: "Gemini 3.5 or newer accessed through Gemini API or Vertex AI, AND at least one Google Agent Framework: Google ADK, GenAI SDK, Antigravity SDK or GenKit AND at least one Google Cloud infrastructure service."
- Repository: "Include a URL to your private or public code repository (on Github, Gitlab or Bitbucket)." A private repo "must give access to testing@devpost.com and cloudhackathons@google.com."
- Also required: "URL to the hosted Project (if available) for judging and testing, such as web UI, Chrome Extension, mobile app, etc."; "Spin-up Instructions: A step-by-step guide in your README.md explaining how to set up and run the project locally or deploy it to the cloud."; "Include an Architecture Diagram with a clear visual representation of your system".
- Judging: "Innovation & Operational Utility (40%)", "Architectural Discipline & Tech Stack (30%)", "Demo & Production Readiness (30%)".

## Form fields

**Project name:** SpecGuard

**Tagline (59 characters):** Submittal audit: uncited claims are blocked from the ledger

**Category:** Taskmaster

**Hosted project URL:** `https://specguard-108657628939.us-central1.run.app` (Cloud Run, us-central1). Judges can run the four public sample audits without a passphrase: Caldra (compliant), Veylan 208V, Torven 70 deg C, and Veylan altered (integrity screen). `POST /audit` keeps arbitrary uploads behind the demo passphrase to protect the demo budget. The landing page lists sample runs only; an uploaded submittal is reachable by the run URL its uploader receives, and by nothing else.

**Repository URL:** `https://github.com/mcs-eng/specguard` (private until 2026-08-30, public before submission).

**Video URL:** `[TODO: YouTube or Vimeo link, public]`

**Additional Google AI model for the bonus:** Gemma (`google/gemma3@gemma-3-1b-it` on a Vertex AI Model Garden endpoint) as the severity annotation. One model, 0.2 points claimed.

**Pre-existing code disclosure:** None. Every line in the repository was written inside the contest window, and nothing was copied from an earlier project. (PLAN.md, Contest facts.)

## Inspiration

A construction submittal review compares a vendor's cut sheet against the project specification and asks one question over and over: does the product the vendor sent meet the requirement the spec wrote down. A 208V switchboard submitted against a 480V spec is an expensive miss. A termination rating stated in Fahrenheit on a cut sheet, against a Celsius requirement, is an easy one.

An automated reviewer that can assert anything is worse than no reviewer, because a reviewer's output gets trusted. So the first design decision was the one that shapes everything else: the model may propose a discrepancy, but nothing the model says reaches the record unless a deterministic check finds the quoted words on the page the model cited. The second decision followed from treating a vendor PDF as untrusted input: a PDF's hidden text layer can differ from its visible page, so the text layer is screened before the model reads a word.

## What it does

**The core is a gate and one call.** `specguard/gate.py` is 184 lines that answer one question — are these quoted characters on the page this claim cited — under one fixed normalization, with no fuzzy matching, no edit distance, and no cross-page search. The audit around it is a single model turn: one Google ADK agent on Gemini 3.7 Flash reads both documents and returns structured discrepancy claims, each carrying one verbatim quote and one page number per document. Every run of the 100-audit campaign that reached the model made exactly one call to that claim generator. That pair is the product. Everything else in SpecGuard — the runtime, the integrity screen, the eval harness, the committed tool-call receipts, the two reading modes — is the proof system that makes the pair checkable by a reader who does not trust it.

You upload one specification PDF and one cut-sheet PDF. SpecGuard screens both text layers, sends the extracted text to one Google ADK agent on Gemini 3.7 Flash, and gets back discrepancy claims, each with one verbatim quote and one page number from each document. A deterministic gate checks that each quote is a contiguous substring of its cited page after one fixed normalization: NFKC, soft-hyphen handling, casefold, whitespace collapse, token boundaries. No fuzzy matching, no edit distance, no cross-page search. A rejected claim goes back to the model once with the machine-readable reason; a second miss is recorded in a separate rejections collection. Verified findings are written to Firestore, where the persistence tool runs the gate again before the write, and the runtime drafts an RFI PDF that carries the quotes, page numbers, and document SHA-256 values as chain-of-custody metadata. A Gemma model then labels each verified finding LOW, MEDIUM, or HIGH as an advisory annotation. That annotation writes the severity fields on a record already in the ledger and nothing else: it cannot change a verification status, a rejection reason, a quote, or a claim, and it cannot create a findings record, because the update reads the finding first and refuses anything that is not a verified record of the same run.

If either document carries text any of the screen's six detectors flags, the run is quarantined before any model call: no finding, no RFI, one deterministic integrity record naming the detector, its evidence, and the hidden spans for a human reviewer.

The findings page on Cloud Run shows each run's status (COMPLETED, QUARANTINED, FAILED, RUNNING), the verified quotes with their page locators inside a window of the cited page, the rejection reasons, the severity label with the model identifier the endpoint reported or the configured endpoint label, and the durable objects keyed by run ID with their SHA-256. For a quarantined run it shows one row per hidden span: the page number, the span text, the font, and the size. No model read that text.

The passing label reads **QUOTES VERIFIED**, and the page states in one line what that does and does not mean: both quotes were found at their cited pages, and that is not a judgment that the discrepancy is real.

## How it was built

The gate is plain Python over PyMuPDF and needs none of the stack below; the claim call needs Gemini and ADK. The rest of this list is the apparatus that surrounds those two, in the order a claim passes through it.

- Gemini 3.7 Flash via Vertex AI at location `global` (the regional us-central1 endpoint returned 404 for Gemini 3.x during setup), temperature 0, structured output against a Pydantic schema.
- Google ADK 2.7.1: one `LlmAgent` with `output_schema` for the claim batch, in either of two modes. `full_text`, the deployed default, sends both documents up front. `navigate` sends the submitted document in full plus a page index of the specification and reads specification pages as it goes. Both modes register exactly the same three read-only tools (`check_text_integrity`, `extract_pdf_text`, `verify_quote`), each bound to a document role rather than a path, and neither registers the two write tools (`persist_finding`, `draft_rfi`); a test pins the two registrations as identical. In both modes every model-initiated call is recorded on the run and shown on its page, the runtime owns every write, and the gate verifies every claim before it reaches the ledger. The mode question was decided by a pre-registered eight-condition gate, not by preference, and it closed with the first 2026-08-25 campaign in favour of `full_text`; `navigate` stays selectable and fully receipted, and EVAL.md publishes both modes with the gate verdict line by line.
- Cloud Run: FastAPI service, `--max-instances 1`, `--concurrency 2`, a two-slot in-process semaphore, 5 MB `application/pdf` uploads behind a demo passphrase.
- Firestore: `findings`, `rejections`, `integrity_findings`, `runs`, and a content-addressed `documents` collection keyed by SHA-256.
- Cloud Storage: uploads and RFI PDFs stored under `<run_id>/` with each object's SHA-256 recorded on the run document.
- Secret Manager: the demo passphrase, read by the Cloud Run revision; nothing secret is in the repository.
- Gemma: `google/gemma3@gemma-3-1b-it` deployed from Vertex Model Garden to a dedicated endpoint on `g2-standard-12` with one NVIDIA L4, called with Application Default Credentials, deployed only for demo and evaluation windows.
- PyMuPDF 1.28.2 (pinned) for extraction and span data, Pydantic models, Python 3.12, uv, ruff, and an offline pytest suite that needs no network or credentials. Its size is not typed here: `scripts/record_test_count.py` runs the suite and writes the count and the revision it describes into README, so the published number always comes from a real run.

## The honesty contract

SpecGuard defends one narrow, testable claim: uncited claims are blocked from the ledger. The gate proves that the quoted characters occur on the cited page and nothing more. It does not prove the finding is accurate, it does not claim zero hallucinations, and the README lists every known path to a wrong verification with the test that pins each one: casefolding merges `15 mW` and `15 MW`, NFKC folds `10²` to `102`, whitespace collapse splices columns, the gate reads the text layer rather than the visible page, and the schema cannot prove the gate ran. SHA-256 hashes are chain-of-custody metadata only. The Gemma severity label is advisory and falls back to UNCLASSIFIED with a recorded reason when the endpoint is down, which is the state a judge auditing the service later will see. The guarantee covers writes through SpecGuard's persistence tool; a process with its own Firestore credentials is outside it, and the README says so. A human reviews every finding.

## Challenges

**The text layer is not the page.** The gate verifies against what PyMuPDF extracts, and a PDF can carry text a reader never sees. The first version of the README had this as a bullet in the limitations list. The fix was to turn the limitation into a control: a deterministic integrity screen that quarantines the run before any model call. It began as one rule and now runs five: render mode 3 from the MuPDF character flags, a zero fill alpha, a sub-point effective glyph size, clip-only render mode 7 read from the content stream, and text placed outside the crop box. One fixture was built to be caught: a copy of the 208V cut sheet that renders pixel-identically and carries two hidden spans on page 1, one of them a sentence addressed to an automated reviewer. Each detector has a generated page it must flag and a near-miss it must leave alone. What the screen still does not detect is recorded and pinned: white-on-white text, text under a covering rectangle, rasterized text, and text outside the media box. The first two stay out on purpose, because both need the colour of what is painted behind the text, and a heuristic there would quarantine honest cut sheets and redacted submittals.

**Serving Gemma.** The Gemini API key path returned HTTP 429 behind the AI Studio prepay wall, and the Gemma 3 27B identifier did not exist there. Vertex publisher endpoints returned 404 for every Gemma identifier in six regions. Model Garden worked, inside a quota of one L4 GPU: Gemma 3 12B needs two L4s, Gemma 3 4B rejects the one-L4 machine type outright, and Gemma 3n hit a stock failure. Gemma 3 1B deployed in 1 minute 48 seconds and classified the two planted discrepancies HIGH. Every failed path is recorded as a fallback with its HTTP reason on the finding, never silently.

**Not over-reading the table.** The measured evaluation ran 100 audits: five document pairs, ten iterations, both agent modes. Twenty runs are the altered fixture, quarantined before any model call; the other 80 reach the model once each. Across those 80 runs the gate rejected nothing and the runtime retried nothing, because the model cited every quote correctly on the first turn. A clean table invites the reader to assume the retry loop was exercised. EVAL.md says it was not exercised at all here, and the loop is proven by tests that drive rejections deterministically.

## Accomplishments

All numbers from `EVAL.md` (100 real audits on 2026-08-25 against code revision `8900a76`, both agent modes and both lanes, every run identifier listed) and `HANDOFF.md`, with every model-initiated tool call behind them committed to `EVAL-RECEIPTS.jsonl`. They supersede two earlier records and are not pooled with either: the first 2026-08-25 campaign at revision `31ef186`, and the 2026-08-23 five-iteration measurement, which also ran under a stricter scoring rule and against an earlier package fixture. Where a number below names a different campaign, it says so.

- Original lane, both modes: catch rate 100% on both planted discrepancies over ten runs each, 0 false positives on the compliant cut sheet, and 100% quarantine on the altered fixture with 0 model turns. A catch requires the finding to carry the manifest evidence on the manifest's pages; a right quote on the wrong page is a false positive, not a catch.
- Messy lane (a 32-page specification and a 10-page two-product package with seven planted discrepancies and two compliant decoys): navigate caught 90% against full text's 86%, with 0 decoy false positives and 0 unattributed false positives in either mode. One planted case carries the whole gap and the whole shortfall: E-15 measures 30% in navigate and 0% in full text, and every other planted case on that lane measures 100% in both modes. The earlier `31ef186` campaign measured the same lane the other way round, full text 96% against navigate 94%. A few case-runs in seventy moved in each direction between two runs of the same fixtures; that is run-to-run variance, and the two campaigns are reported separately rather than averaged.
- The two misses the previous run published were inspected against its persisted records before anything changed, and each had a different cause. E-13 was a scoring artefact: the runtime found that discrepancy in 10 of 10 runs, and the harness demanded the exact planted quote span, so six runs that quoted a shorter span of the same sentence on the same page scored as misses. E-15 was a fixture-design finding: no run ever raised the pair, because the package stated its clearance as a disclaimed recommendation and both prompts forbid reporting statements that can both be true. The scorer now counts evidence equality, and the package row now states a flat product characteristic. No prompt changed, and nothing else on that page changed. Receipts in `REVIEW-7D-MISSES.md`, which describes the superseded run.
- Zero invented findings and zero decoy hits across every persisted messy-lane finding, in both modes. The 16 unattributed false positives the previous run reported were span variants of planted pairs, shown by an offline classification at the time; the harness now measures that directly and reports zero, so the claim no longer rests on a classification done outside it.
- The default followed an eight-condition ship gate fixed in writing before any run and evaluated by a pure function over the results. In the `31ef186` campaign navigate met seven conditions and missed one by a single case-run in seventy, so full text became the default and navigate stayed selectable and published. The rule was pre-registered precisely so a two-point gap could not be argued either way after the fact — and the same discipline was applied when the gap reversed. Before the `8900a76` campaign ran, its handling was recorded in advance: the mode question had closed, the new campaign would republish both modes, and its gate output would be recorded as informational rather than as a decision input, with the default remaining full text regardless of one-run noise in either direction. That campaign's gate then met all eight conditions, the verdict is published in EVAL.md exactly as it evaluated, and the default did not move.
- This measurement ran with the severity sentinel (`SPECGUARD_GEMMA_ENDPOINT=disabled`), so every finding carries UNCLASSIFIED with its recorded reason; the 2026-08-22 record, with a live Gemma endpoint labelling every finding HIGH, is published beside it. Nothing in the classifier changed between the records.
- ruff clean and an offline suite whose size README records from its own run (`HANDOFF.md` receipts).
- 16 adversarial bypass attempts against the ledger invariant, all refused (`REVIEW-P3.md`, `tests/adversarial/`).
- 2 hand-run mutations of the gate, both caught by the suite (`HANDOFF.md`, Phase 1).
- Live web runs on the deployed service: model step to persisted finding in 10.7 s and 12.2 s on a warm revision with the Gemma endpoint up, and 48.1 s on the first run of the day (Firestore `created_at` deltas on runs `72e1e439…`, `1c94812c…`, `c3a307ab…`).

## What's next

- Match inside one extracted block or table cell instead of the whole page, so whitespace collapse cannot splice columns.
- A structured comparison step that records the unit conversion (158 deg F is 70 deg C) as its own field beside the quote anchors.
- An account boundary on an upload run. Today the run URL is the capability: it is a 128-bit identifier, it is never listed or enumerated, and anyone holding it can read the run.
- Real vendor PDFs checked against the gate with `rawdict` extraction before the gate drives anything outside the fixture set.

Two entries that stood here are done. The passing outcome is renamed: it reads **QUOTES VERIFIED**, with one line of meaning beside it, on the page, in the RFI, and in the JSON export. Duplicate prevention on the upload route is server-side: the page mints a submission token, Firestore records it with a one-hour expiry, and an unminted or expired token is refused while a replay of a used one returns the original run.

## Built with

Python 3.12, Google ADK 2.7.1, Gemini 3.7 Flash, Vertex AI, Gemma 3 1B, Cloud Run, Firestore, Cloud Storage, Secret Manager, PyMuPDF, FastAPI, Pydantic, Uvicorn, Jinja2, uv, pytest, ruff.

## Receipts behind this draft

- Contest facts: the rules page and home page fetched 2026-08-21 (quoted above).
- Stack and pins: `pyproject.toml`, `specguard/agent.py`, `deploy-specguard.ps1`.
- Evaluation numbers: `EVAL.md`.
- Test count: generated into README by `scripts/record_test_count.py` from a real `uv run pytest -q` run, together with the code revision it describes. `HANDOFF.md` carries the exit code for each phase.
- Adversarial count and mutation count: `REVIEW-P3.md`, `HANDOFF.md`.
- Gemma path history and deployment duration: `HANDOFF.md` Phase 5 Close and Phase 5 Model Upgrade.
- Web-run latency: read-only Firestore query on 2026-08-21 (REVIEW-CLAIMS.md, receipt R-12).
