# SpecGuard

SpecGuard audits construction submittals against a specification and refuses to record a claim it cannot prove. Every claim carries a verbatim quote and a page locator. A deterministic gate checks the quote against the cited page before the claim reaches the ledger.

**Uncited claims are blocked from the ledger.**

This repository is at Phase 1. What exists today is the verification gate, the data schema, and the test suite that proves the gate. There is no agent, no cloud service, and no user interface yet.

## Verification contract

This is the exact claim SpecGuard defends. `specguard/gate.py` implements it and `tests/test_gate.py` proves it.

1. **Extraction.** The gate extracts the text of the cited page with PyMuPDF (pinned version). It reads that page and no other page.
2. **Normalization.** The gate normalizes the quote and the page text with the same steps, in this order:
   a. Unicode NFKC normalization.
   b. Remove each soft hyphen (U+00AD) together with any whitespace that immediately follows it.
   c. Casefold.
   d. Collapse every run of whitespace to a single space.
   e. Strip leading and trailing whitespace.
3. **Match.** The claim verifies only if the normalized quote is a contiguous substring of the normalized text of the cited page, and the substring sits on alphanumeric boundaries: the match may not begin or end in the middle of a word or a number. There is no fuzzy matching, no edit distance, and no cross-page search.
4. **A miss is a rejection, always.** Every rejection carries a machine-readable reason: `page_out_of_range` or `quote_not_found_on_cited_page`.

### What the contract does not claim

The gate proves one narrow thing: the quoted characters appear on the page that was cited. Everything below is outside that proof. This list is deliberately long, because the honesty claim is only worth as much as the list of things it does not cover.

- It does not claim the model tells the truth. A quote that is absent from the cited page is rejected. A true quote attached to a wrong conclusion is not something this gate can detect.
- It does not claim zero hallucinations.
- **Normalization merges some strings that mean different things.** Every item below is a real path to a wrong verification, each pinned by a test:
  - Casefolding erases case-sensitive units. A page reading `15 mW` and a claim quoting `15 MW` normalize identically, a millionfold difference. Case-insensitive matching is required by the contract, so this is a known cost of it, not a defect.
  - NFKC folds superscripts and subscripts into plain digits. A page reading `10²` normalizes to `102`. Cut sheets use `mm²` often.
  - Whitespace collapse discards layout. Text from two columns, two table cells, or a header and a body can end up adjacent, so a quote can splice text that never appeared together on the page.
- **It reads the text layer, not the visible page.** A PDF whose text layer disagrees with what a human sees — hidden text, or an OCR layer over a scan — verifies against text the reader cannot see. A pure image scan carries no text and cannot verify anything. The gate is proven against generated text-based PDFs only.
- **The schema does not enforce that the gate ran.** `Finding.verification_status` is an ordinary field. The schema keeps the status and the rejection reason consistent, but a caller can construct a `VERIFIED` finding without calling `verify_quote`. Binding the two is the job of the persistence path, which does not exist yet.
- SHA-256 in `DocumentRecord` is chain-of-custody metadata. It records which byte stream was read. It is not an accuracy mechanism and no part of the gate reads it.

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

All test fixture content is fictional.

## Usage

```python
from specguard.gate import verify_quote

result = verify_quote("Receptacles shall be specification grade", 1, "spec.pdf")
result.verified  # bool
result.rejection_reason  # None, or a machine-readable reason
```

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
