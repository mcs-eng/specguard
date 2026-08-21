# SpecGuard

SpecGuard audits a construction cut sheet against a specification. Every finding carries one verbatim quote and page locator from each source document. A deterministic gate must locate both quotes on their cited pages before the finding reaches the Firestore ledger.

**Uncited claims are blocked from the ledger.**

This repository is at Phase 3.5. It includes one Google ADK agent, the deterministic verification gate, a text-layer integrity screen that runs before the model reads anything, guarded Firestore persistence, a bounded one-retry loop, and RFI draft PDF generation. The gate establishes only that each quoted text anchor occurs on its cited page. It does not establish that the finding is accurate.

## Verification contract

This is the exact claim SpecGuard defends. `specguard/gate.py` implements it and `tests/test_gate.py` proves it.

The scope of this construction is the SpecGuard application path. A separate process with direct Firestore write credentials is outside the Python API guarantee and can write to Firestore independently. Concurrent modification of the source files during a run is outside the guarantee for the same reason.

1. **Extraction.** The gate extracts the text of the cited page with PyMuPDF (pinned version). It reads that page and no other page.
2. **Normalization.** The gate normalizes the quote and the page text with the same steps, in this order:
   a. Unicode NFKC normalization.
   b. Rejoin a word broken by a soft hyphen: where a soft hyphen (U+00AD) sits between two **letters**, remove it together with any whitespace that follows it. A soft hyphen next to a digit never joins, so `1<U+00AD>2` stays two tokens and does not become `12`.
   c. Remove every remaining soft hyphen, leaving the surrounding whitespace alone.
   d. Casefold.
   e. Collapse every run of whitespace to a single space.
   f. Strip leading and trailing whitespace.
3. **Match.** The claim verifies only if the normalized quote is a contiguous substring of the normalized text of the cited page, and the substring sits on token boundaries: the match may not begin or end in the middle of a word or a number. A digit at the edge of the quote may not sit against a character that binds to a number either, so a claim quoting `5 A` cannot ride on the page text `0.5 A`. There is no fuzzy matching, no edit distance, and no cross-page search.
4. **A miss is a rejection, always.** Every rejection carries a machine-readable reason: `page_out_of_range` or `quote_not_found_on_cited_page`.

### What the contract does not claim

The gate proves one narrow thing: the quoted characters appear on the page that was cited. Everything below is outside that proof. This list is deliberately long, because the honesty claim is only worth as much as the list of things it does not cover.

- It does not claim the model tells the truth. A quote that is absent from the cited page is rejected. A true quote attached to a wrong conclusion is not something this gate can detect.
- It does not claim zero hallucinations.
- **Normalization merges some strings that mean different things.** Every item below is a real path to a wrong verification, each pinned by a test:
  - Casefolding erases case-sensitive units. A page reading `15 mW` and a claim quoting `15 MW` normalize identically, a millionfold difference. Case-insensitive matching is required by the contract, so this is a known cost of it, not a defect.
  - NFKC folds superscripts and subscripts into plain digits. A page reading `10²` normalizes to `102`. Cut sheets use `mm²` often.
  - Whitespace collapse discards layout. Text from two columns, two table cells, or a header and a body can end up adjacent, so a quote can splice text that never appeared together on the page.
- **It reads the text layer, not the visible page.** A PDF whose text layer disagrees with what a human sees — hidden text, or an OCR layer over a scan — verifies against text the reader cannot see. A pure image scan carries no text and cannot verify anything. The gate is proven against generated text-based PDFs only. The gate itself is unchanged by the integrity screen described below; the screen discloses one form of this disagreement before the model reads anything, and the gate keeps reading the text layer.
- **The schema does not enforce that the gate ran.** `Finding.verification_status` is an ordinary field. The schema keeps the status and the rejection reason consistent, but a caller can construct a `VERIFIED` finding without calling `verify_quote`. The Firestore persistence tool does not trust that field. It re-verifies both quotes against the two source PDFs and performs no write if either quote rejects.
- SHA-256 in `DocumentRecord` is chain-of-custody metadata. It records which byte stream was read. It is not an accuracy mechanism and no part of the gate reads it.

## Text-layer integrity screen

A document from a third party is untrusted input. The known limitation above — the gate reads the text layer, not the visible page — describes a real gap between what a human reviewer reads and what an automated reviewer ingests. This screen discloses one way that gap opens. It narrows it. It does not close it.

`specguard/integrity.py` reads the file's bytes once, into one immutable snapshot, and derives the SHA-256, the page count, and the span evidence from that snapshot, so a replacement during a screen cannot make the hash describe one byte stream while the evidence describes another. It reads PyMuPDF span data only. It renders no image, runs no OCR, and compares no pixels. A PDF text-showing operator carries a render mode, and MuPDF records the outcome of that mode on every character. The screen reports a span whose characters are neither filled nor stroked: nothing is painted for the reader, and the text layer still carries the characters. That is render mode 3, the mode an OCR layer uses over a scanned image.

### What it detects

- Text made invisible by render mode 3, which a text-layer reader still ingests. The report names each such span, its page, its font and size, and the raw character flags the rule read.
- The visible text of every page beside it. On a page with no invisible span, that string is byte-identical to `page.get_text()`, so a clean page is reported exactly as the verification gate reads it.

### What it does not detect

- **Rasterized text.** Text drawn as an image carries no span and no render mode. A pure image scan carries nothing for this screen to read.
- **Clip-only render mode 7.** MuPDF reports the same character flags for a clip-only span as for a filled-and-clipped span, so the screen cannot separate hidden text from painted text in that mode. `tests/test_integrity.py::test_clip_only_render_mode_is_a_known_limitation` pins that gap.
- **Other concealment methods.** A fill colour matching the background, a zero alpha set through the graphics state, a glyph placed outside the crop box, or a rectangle drawn over painted text all leave the span filled or stroked. This screen does not report any of them.
- **Intent.** A flagged page is a disclosure, not a verdict. The screen states that the text layer disagrees with the visible page and shows the disagreeing spans. It does not decide why they are there.

### What the runtime does with a flagged document

The screen runs first, on both bound documents, before any extracted text is assembled into a model message. `AuditRuntime.run` takes no argument that can skip it, so no caller of `run()` can opt out.

When either bound document carries at least one invisible span, the run is quarantined:

- No model call is made. The model receives no text from either document, because the model message carries both.
- The runtime attempts to write one deterministic integrity record per flagged document, into its own `integrity_findings` collection, holding the span text, the page numbers, the document SHA-256, and the screen identity. The persistence tool takes one role name and reads the file again itself, so no caller and no model can author or edit that record. It is not a claim finding and it never passes through the verification gate.
- If that write is refused, or if the written record's hash does not match the hash the screen read, the summary reports the refusal reason instead of a record identifier. The quarantine still stands; only the record is missing.
- No claim finding is persisted and no RFI is drafted.
- The run summary reports the quarantine: the reason, each flagged document, its flagged pages, its hidden-span count, and its SHA-256.

The runtime and its tools must be bound to the same two documents; a split binding is refused when the runtime is constructed. After extraction, the runtime re-reads both hashes and refuses to send text if either document changed since the screen read it. A writer that replaces a document and restores it inside that window is outside the guarantee, exactly as recorded above for the gate.

`check_text_integrity`, `extract_pdf_text`, and `verify_quote` are model-facing tools bound to a document role rather than a path. The agent can address only the specification or submitted document already bound to the audit. `check_text_integrity` returns the flag summary only — never hidden-span text — because handing that text back to the model would reopen the disclosure the screen exists to close. `extract_pdf_text` returns raw page text from its bound document, so the quarantine still stops a flagged document before any extraction happens. The runtime does not depend on the model calling any tool.

## Gemma severity classification

SpecGuard uses Gemma deployed on a Vertex AI endpoint (`google-gemma3-gemma-3-1b-it` via Vertex Model Garden) to classify the technical severity (LOW, MEDIUM, HIGH) of verified discrepancy claims. Gemma runs strictly as a post-verification advisory annotation on findings that have already passed the deterministic verification gate and been persisted to the Firestore ledger. Severity classification is an advisory annotation only: it is not a path into the ledger, it never modifies verification status or rejection reasons, and when the endpoint is offline or unavailable, classification gracefully falls back to `UNCLASSIFIED` with the raw reason recorded on the finding and run summary without blocking or failing the audit.

## What the test suite proves

Known-good cases that verify:

- An exact quote.
- A quote that differs only in letter case.
- A quote that differs in whitespace, including line breaks on either side.
- A quote whose word is split across a soft-hyphen line break on the page.
- A quote containing characters that NFKC normalizes, such as an fi ligature and a no-break space.

Known-bad cases that reject:

- A quote that is absent from the document.
- A real quote cited to the wrong page.
- A quote that spans two pages.
- A quote with one digit changed, such as 208 against 209.
- A quote with the unit changed, such as V against kV.
- A page number outside the document.
- An empty quote.
- A quote whose leading digit rides on a longer number: `5 A` against `0.5 A`, `5 kPa` against `-5 kPa`, `5% ` against `±5%`, `500 kcmil` against `12,500 kcmil`, `1 unit` against `AHU-1 unit`.
- A quote that merges digits across a soft hyphen: `NEMA 12` against a page whose `NEMA 1` is broken by a soft hyphen before a `2`.

Known limitations, each pinned by a test so the limitation cannot quietly disappear:

- A page whose invisible text layer disagrees with the visible page verifies against the invisible text.
- A page with no text layer at all verifies nothing.
- Casefolding makes `15 mW` and `15 MW` identical.
- NFKC folds `10²` to `102`.
- Whitespace collapse makes text from separate columns adjacent.
- The schema cannot prove the gate was ever run.

Text-layer integrity screen:

- The altered demo fixture is flagged, on page 1, with both hidden span texts reported exactly.
- The four original demo fixtures produce zero flags. That is the false-positive check.
- On a clean page, the reported visible text equals the page text the gate reads.
- The altered fixture renders pixel-for-pixel identically to the unaltered one, so the difference is in the text layer alone.
- A quarantined document produces no model call and no model-visible message carrying the hidden text.
- The persisted integrity record carries the span evidence, the page numbers, and the document SHA-256, and two runs over one file store the same evidence.
- `AuditRuntime.run` accepts no argument that could skip the screen.
- Replacing the file mid-screen cannot split the reported hash from the reported evidence.
- The same bytes screen identically through a relative and an absolute path.
- A document replaced after the screen and before extraction never reaches the model.
- A runtime whose tools are bound to a different document pair is refused at construction.
- No registered agent tool except `extract_pdf_text` returns hidden span text.
- Clip-only render mode 7 is not detected, pinned by its own test. White-on-white text, zero fill alpha, and text outside the crop box are likewise not detected, each pinned by its own test.

Mutation testing is not automated. Two mutations were run by hand once and both were caught: reading page 2 for every later citation (`document[min(page_number - 1, 1)]`) failed 2 tests, and replacing `casefold()` with `lower()` failed 1. The receipts are in `HANDOFF.md`; re-run them by hand if the gate changes.

All test fixture content is fictional.

## Usage

```python
from specguard.gate import verify_quote

result = verify_quote("Receptacles shall be specification grade", 1, "spec.pdf")
result.verified  # bool
result.rejection_reason  # None, or a machine-readable reason
```

Run a complete audit with local Application Default Credentials:

```powershell
uv run python run_audit.py --spec path\to\specification.pdf --cutsheet path\to\cut-sheet.pdf
```

The command prints claims made, rejected, retried, findings persisted, and the generated RFI path. The model receives only extracted PDF text with one-based page markers. It does not receive fixture manifests or source file names.

When the integrity screen flags either document, the command prints the quarantine instead: the reason, each flagged document, its flagged pages, its hidden-span count, its SHA-256, and the identifier of the integrity record. No model call is made for that run.

## Development

Install and run the quality gates with [uv](https://docs.astral.sh/uv/):

```bash
uv sync
```

```bash
uv run pytest
```

```bash
uv run ruff check .
```

## Running it

Set `SPECGUARD_PROJECT`, `SPECGUARD_RUNS_BUCKET`, and `SPECGUARD_DEMO_PASSPHRASE` in the local environment first. The passphrase is not stored in this repository.

### arya (PowerShell)

```powershell
uv run uvicorn specguard.web.app:app --host 127.0.0.1 --port 8080
```

Deployment URL: recorded in `HANDOFF.md` after the Cloud Run deployment.

The public GET routes are read-only. `POST /audit` requires the demo passphrase, accepts only `application/pdf`, and limits each upload to 5 MB. Each Cloud Run instance accepts at most two concurrent requests and runs at most two in-flight audits. Cloud Run compute is ephemeral. The uploaded PDFs and generated RFI PDFs are durable Cloud Storage objects keyed by run ID, with each object SHA-256 recorded in the Firestore run document. A failed audit remains visible as `FAILED` with its stored source-object records.
