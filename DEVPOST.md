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

**Hosted project URL:** `https://specguard-108657628939.us-central1.run.app` (Cloud Run, us-central1). Judges can run the four public sample audits without a passphrase: Caldra (compliant), Veylan 208V, Torven 70 deg C, and Veylan altered (integrity screen). `POST /audit` keeps arbitrary uploads behind the demo passphrase to protect the demo budget.

**Repository URL:** `https://github.com/mcs-eng/specguard` (private until 2026-08-30, public before submission).

**Video URL:** `[TODO: YouTube or Vimeo link, public]`

**Additional Google AI model for the bonus:** Gemma (`google/gemma3@gemma-3-1b-it` on a Vertex AI Model Garden endpoint) as the severity annotation. One model, 0.2 points claimed.

**Pre-existing code disclosure:** None. Every line in the repository was written inside the contest window, and nothing was copied from an earlier project. (PLAN.md, Contest facts.)

## Inspiration

A construction submittal review compares a vendor's cut sheet against the project specification and asks one question over and over: does the product the vendor sent meet the requirement the spec wrote down. A 208V switchboard submitted against a 480V spec is an expensive miss. A termination rating stated in Fahrenheit on a cut sheet, against a Celsius requirement, is an easy one.

An automated reviewer that can assert anything is worse than no reviewer, because a reviewer's output gets trusted. So the first design decision was the one that shapes everything else: the model may propose a discrepancy, but nothing the model says reaches the record unless a deterministic check finds the quoted words on the page the model cited. The second decision followed from treating a vendor PDF as untrusted input: a PDF's hidden text layer can differ from its visible page, so the text layer is screened before the model reads a word.

## What it does

You upload one specification PDF and one cut-sheet PDF. SpecGuard screens both text layers, sends the extracted text to one Google ADK agent on Gemini 3.7 Flash, and gets back discrepancy claims, each with one verbatim quote and one page number from each document. A deterministic gate checks that each quote is a contiguous substring of its cited page after one fixed normalization: NFKC, soft-hyphen handling, casefold, whitespace collapse, token boundaries. No fuzzy matching, no edit distance, no cross-page search. A rejected claim goes back to the model once with the machine-readable reason; a second miss is recorded in a separate rejections collection. Verified findings are written to Firestore, where the persistence tool runs the gate again before the write, and the runtime drafts an RFI PDF that carries the quotes, page numbers, and document SHA-256 values as chain-of-custody metadata. A Gemma model then labels each verified finding LOW, MEDIUM, or HIGH as an advisory annotation that can never write into the ledger.

If either document carries text hidden by PDF render mode 3, the run is quarantined before any model call: no finding, no RFI, one deterministic integrity record with the hidden spans for a human reviewer.

The findings page on Cloud Run shows each run's status (COMPLETED, QUARANTINED, FAILED, RUNNING), the verified quotes with their page locators, the rejection reasons, the severity label with its model identifier, the hidden-span evidence, and the durable objects keyed by run ID with their SHA-256.

## How it was built

- Gemini 3.7 Flash via Vertex AI at location `global` (the regional us-central1 endpoint returned 404 for Gemini 3.x during setup), temperature 0, structured output against a Pydantic schema.
- Google ADK 2.7.1: one `LlmAgent` with five function tools (`check_text_integrity`, `extract_pdf_text`, `verify_quote`, `persist_finding`, `draft_rfi`), each bound to a document role rather than a path, and `output_schema` for the claim batch. The runtime around the agent calls the tools itself; no receipt shows a model-initiated tool call, and the design does not depend on one.
- Cloud Run: FastAPI service, `--max-instances 1`, `--concurrency 2`, a two-slot in-process semaphore, 5 MB `application/pdf` uploads behind a demo passphrase.
- Firestore: `findings`, `rejections`, `integrity_findings`, `runs`, and a content-addressed `documents` collection keyed by SHA-256.
- Cloud Storage: uploads and RFI PDFs stored under `<run_id>/` with each object's SHA-256 recorded on the run document.
- Secret Manager: the demo passphrase, read by the Cloud Run revision; nothing secret is in the repository.
- Gemma: `google/gemma3@gemma-3-1b-it` deployed from Vertex Model Garden to a dedicated endpoint on `g2-standard-12` with one NVIDIA L4, called with Application Default Credentials, deployed only for demo and evaluation windows.
- PyMuPDF 1.28.2 (pinned) for extraction and span data, Pydantic models, Python 3.12, uv, 254 pytest tests that need no network or credentials, ruff.

## The honesty contract

SpecGuard defends one narrow, testable claim: uncited claims are blocked from the ledger. The gate proves that the quoted characters occur on the cited page and nothing more. It does not prove the finding is accurate, it does not claim zero hallucinations, and the README lists every known path to a wrong verification with the test that pins each one: casefolding merges `15 mW` and `15 MW`, NFKC folds `10²` to `102`, whitespace collapse splices columns, the gate reads the text layer rather than the visible page, and the schema cannot prove the gate ran. SHA-256 hashes are chain-of-custody metadata only. The Gemma severity label is advisory and falls back to UNCLASSIFIED with a recorded reason when the endpoint is down, which is the state a judge auditing the service later will see. The guarantee covers writes through SpecGuard's persistence tool; a process with its own Firestore credentials is outside it, and the README says so. A human reviews every finding.

## Challenges

**The text layer is not the page.** The gate verifies against what PyMuPDF extracts, and a PDF can carry text a reader never sees. The first version of the README had this as a bullet in the limitations list. The fix was to turn the limitation into a control: a deterministic integrity screen that reads MuPDF character flags, reports any span that is neither filled nor stroked (render mode 3, the mode OCR layers use), and quarantines the run before any model call. One fixture was built to be caught: a copy of the 208V cut sheet that renders pixel-identically and carries two hidden spans on page 1, one of them a sentence addressed to an automated reviewer. The screen detects render mode 3 and nothing else; clip-only mode 7, rasterized text, white-on-white text, zero alpha, and text outside the crop box are all recorded as undetected, four of them pinned by their own tests.

**Serving Gemma.** The Gemini API key path returned HTTP 429 behind the AI Studio prepay wall, and the Gemma 3 27B identifier did not exist there. Vertex publisher endpoints returned 404 for every Gemma identifier in six regions. Model Garden worked, inside a quota of one L4 GPU: Gemma 3 12B needs two L4s, Gemma 3 4B rejects the one-L4 machine type outright, and Gemma 3n hit a stock failure. Gemma 3 1B deployed in 1 minute 48 seconds and classified the two planted discrepancies HIGH. Every failed path is recorded as a fallback with its HTTP reason on the finding, never silently.

**Not over-reading a clean table.** The measured evaluation produced zero gate rejections and zero retries across 15 model-calling runs, because the model cited every quote correctly on the first turn. A clean table invites the reader to assume the retry loop was exercised. EVAL.md says it was not, and the loop is proven by tests that drive rejections deterministically.

## Accomplishments

All numbers from `EVAL.md` (20 real runs, 2026-08-21, every run identifier listed) and `HANDOFF.md`.

- Catch rate 100% on both planted discrepancies: 10 of 10 runs persisted exactly the expected quote pair and page numbers. A right quote on the wrong page would have counted as a false positive, not a catch.
- 0 false positives on the compliant cut sheet across 5 runs.
- 100% quarantine on the altered fixture across 5 runs, with 0 model calls.
- 7 of 10 findings labelled HIGH by Gemma; 3 recorded as UNCLASSIFIED with an HTTP 502 reason from the endpoint.
- 254 tests, ruff clean (`HANDOFF.md`, Phase 6a receipts).
- 16 adversarial bypass attempts against the ledger invariant, all refused (`REVIEW-P3.md`, `tests/adversarial/`).
- 2 hand-run mutations of the gate, both caught by the suite (`HANDOFF.md`, Phase 1).
- Live web runs on the deployed service: model step to persisted finding in 10.7 s and 12.2 s on a warm revision with the Gemma endpoint up, and 48.1 s on the first run of the day (Firestore `created_at` deltas on runs `72e1e439…`, `1c94812c…`, `c3a307ab…`).

## What's next

- Match inside one extracted block or table cell instead of the whole page, so whitespace collapse cannot splice columns.
- A structured comparison step that records the unit conversion (158 deg F is 70 deg C) as its own field beside the quote anchors.
- Rename the passing outcome from VERIFIED to something closer to what the gate proves, such as `text_anchor_found`.
- Server-side duplicate prevention for the upload route.
- Real vendor PDFs checked against the gate with `rawdict` extraction before the gate drives anything outside the fixture set.

## Built with

Python 3.12, Google ADK 2.7.1, Gemini 3.7 Flash, Vertex AI, Gemma 3 1B, Cloud Run, Firestore, Cloud Storage, Secret Manager, PyMuPDF, FastAPI, Pydantic, Uvicorn, Jinja2, uv, pytest, ruff.

## Receipts behind this draft

- Contest facts: the rules page and home page fetched 2026-08-21 (quoted above).
- Stack and pins: `pyproject.toml`, `specguard/agent.py`, `deploy-specguard.ps1`.
- Evaluation numbers: `EVAL.md`.
- Test count: `uv run pytest -q` on 2026-08-21, `254 passed, 2 warnings in 43.03s`, exit 0 (REVIEW-CLAIMS.md receipts).
- Adversarial count and mutation count: `REVIEW-P3.md`, `HANDOFF.md`.
- Gemma path history and deployment duration: `HANDOFF.md` Phase 5 Close and Phase 5 Model Upgrade.
- Web-run latency: read-only Firestore query on 2026-08-21 (REVIEW-CLAIMS.md, receipt R-12).
