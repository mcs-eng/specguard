# SpecGuard Phase 3 — independent verification audit

Date: 2026-08-21. Reviewer: independent adversarial session (Fable, high effort), per the P3 review slot in the delegation map. Scope: the central ledger invariant, the retry loop, prompt hygiene, claims accuracy, test fidelity, and config/secrets. Production code, existing tests, fixtures, PLAN.md, and SETUP.md were not modified. New evidence lives in `tests/adversarial/` (15 passing invariant tests plus 1 strict xfail that pins finding F1).

The claim under audit, verbatim: **"Uncited claims are blocked from the ledger."**

## Verdict up front

**The ledger claim holds on the SpecGuard application path: yes.** Every write to the `findings` collection goes through `AuditTools.persist_finding`, which re-runs `specguard.gate.verify_quote` for both quotes at write time and refuses to create any Firestore reference on rejection. Sixteen adversarial bypass attempts confirm this (see the evidence section). The claim's scope boundary — a process with its own Firestore credentials can write directly — is disclosed in HANDOFF.md but not in README.md (finding F2). One adjacent control is missing: `draft_rfi` renders quote text it never verified (finding F1). Neither finding touches the ledger itself.

## Findings

### F1 — MAJOR: `draft_rfi` renders findings that were never persisted or verified

- File: [specguard/tools.py:160-169](specguard/tools.py) (`draft_rfi`).
- Scenario: construct a `PersistedFinding` by hand with the two correct document SHA-256 values (computable from the files, and also returned to the model in every successful `persist_finding` result) and invented quote text, then call `draft_rfi([forged])`. The hash check at tools.py:164-169 passes, because it binds the finding to the documents, not the quotes to the pages. The RFI PDF is generated with the invented text typeset under "Specification quote" and "Submitted document quote".
- Reachability: the standard runtime path is safe — `AuditRuntime.run` passes only `persist_finding` results, and its final `draft_rfi` call overwrites `rfi-{run_id}.pdf`. But `draft_rfi` is one of the four model-callable agent tools (agent.py:65-70), so the tool surface itself does not enforce the guarantee, and any non-standard caller inherits the gap. The RFI is the demo's visible artifact; it should inherit the ledger guarantee, not merely usually contain ledger content.
- Evidence: `tests/adversarial/test_write_surface.py::test_draft_rfi_refuses_a_fabricated_quote_even_with_correct_hashes` — `xfail(strict=True)` today; it will flip loudly to a failure when the control lands, forcing the xfail marker off.
- Minimal fix: in `draft_rfi`, re-run `gate.verify_quote` for each finding's two quotes against the bound source paths (both already held by `AuditTools`) and raise on any rejection. Alternative: look up each `finding_id` in the `findings` collection. The gate re-run is cheaper and needs no Firestore read.

### F2 — MINOR: README omits the scope boundary of the ledger claim

- File: [README.md:3-5](README.md); the disclosure exists only at [HANDOFF.md:318](HANDOFF.md).
- Scenario: a judge reads "Uncited claims are blocked from the ledger" in the README and takes it as a property of the Firestore collection. It is a property of the SpecGuard application path. There is no server-side Firestore rule; any process holding `datastore.user` credentials writes findings directly, unverified.
- Minimal fix: add HANDOFF's one sentence to the README contract section: the guarantee covers writes made through SpecGuard's persistence tool; direct Firestore access is outside it.

### F3 — MINOR: the hash-stability check has unguarded windows

- File: [specguard/tools.py:88-114](specguard/tools.py) (hashes before at 88-89, verify at 90-95, hashes after at 104-114) and the commit at tools.py:153.
- Scenario A (A-B-A): a document is replaced after the first hash, verified in its altered state, and restored before the second hash. Both hashes match; the altered content was what the gate read. Scenario B: the document changes after the second hash and before `batch.commit`; nothing detects it, though the stored quotes and hashes still describe the bytes that were actually verified, so the ledger record stays internally truthful.
- Assessment: requires a concurrent writer racing a local single-process run; HANDOFF.md:318 already scopes concurrent direct access out. Record as accepted, or note it beside the existing scope sentence. No code change recommended for the contest window.

### F4 — MINOR: a malformed model turn aborts the run instead of becoming a rejection

- File: [specguard/agent.py:112-114](specguard/agent.py) (`model_validate_json` on the final text; `RuntimeError` when no final response), consumed at agent.py:137 and agent.py:149.
- Scenario: the initial batch yields two claims; the first persists; the retry turn for the second returns text that is not valid `AuditClaimBatch` JSON. `model_validate_json` raises, `run()` unwinds, `run_audit.py` crashes with a traceback. The first finding is already in the ledger (gate-verified, so the invariant holds), but no RFI is drafted, no summary prints, and the malformed turn is not recorded in `rejections`.
- Test-fidelity link: `FakeClaimGenerator` and the adversarial `ScriptedGenerator` can only return well-formed batches, so no test exercises this path, and HANDOFF makes no claim about it — but "bounded one-retry loop" reads as if every model failure ends in a recorded rejection.
- Minimal fix: wrap both `generate_claims` calls in `AuditRuntime.run` with `try/except (ValidationError, RuntimeError)`, record a rejection with reason `model_output_invalid`, and continue (initial-turn failure: return an empty summary after drafting the no-findings RFI).

### F5 — MINOR (claims accuracy): the agent's tool ownership is proven structurally, not behaviorally

- File: [specguard/agent.py:60-73](specguard/agent.py); test [tests/test_tools.py:111-120](tests/test_tools.py); receipts in HANDOFF Phase 3.
- Scenario: `test_agent_owns_exactly_the_four_required_tools` asserts the tools list on the constructed `LlmAgent`. No test and no receipt from real runs A-C shows the model invoking any tool; the deterministic `AuditRuntime` performs extraction, verification, persistence, and RFI drafting itself, and the model's only proven role is structured claim output. HANDOFF's receipt notes ADK emitted an experimental-feature warning for JSON-schema function declarations alongside `output_schema`, which leaves open whether tool calls ever fire in this configuration.
- Assessment: HANDOFF's own wording ("one Google ADK agent with exactly four tools") is accurate. PLAN's concept sentence ("ONE ADK agent that owns five deterministic tools") and any Devpost or video phrasing implying agent-driven tool use would exceed the evidence. Note the flip side is good for the invariant: even a genuine model call into `persist_finding` is re-gated (adversarial receipts below).
- Minimal fix: before the video script is written, either capture one receipted run showing a model-initiated tool call, or phrase the story as "the runtime enforces the gate around the agent".

### F6 — INFO: caller-set status is ignored in both directions

- File: [specguard/tools.py:81-158](specguard/tools.py).
- A finding hand-marked `REJECTED` (with a reason) whose quotes verify is persisted as `VERIFIED`. This is consistent — the write-time gate is the sole authority and `verification_status` input has no effect — but it is now pinned by `tests/adversarial/test_ledger_invariant.py::test_caller_status_has_no_authority_in_either_direction` so a future change is a conscious one.

### F7 — INFO: fake-fidelity notes

- [tests/fake_firestore.py](tests/fake_firestore.py): `FakeBatch.commit` applies writes sequentially with no atomicity, and merge is shallow, where real Firestore batches are atomic and merge is deep. Current usage (three flat `set` operations per finding) cannot expose the difference, so nothing is masked today; do not lean on the fake if batch usage grows.
- `FakeClaimGenerator` cannot emit malformed output (see F4).
- Everything else the fakes model matches the real contracts the code uses: auto-ID `collection().document()`, `batch().set(ref, data, merge=)`, `commit()`, `document().set()`.

### F8 — INFO: the summary counter `verified` means `findings_persisted`

- File: [specguard/agent.py:184-192](specguard/agent.py). `verified=len(persisted_findings)`: a claim whose quotes pass runtime verification but whose persistence is refused (for example by the hash check) counts as rejected, not verified. The two printed counters are always equal by construction. Labels only; the receipts in HANDOFF are internally consistent with this definition.

### F9 — INFO: retry claim identity is not enforced

- File: [specguard/agent.py:253-260](specguard/agent.py). The prompt tells the model "Do not add a different claim", but the runtime accepts any single claim that verifies. A swapped-in unrelated claim would persist with gate-verified quotes, which the invariant permits by design. Prompt-level control only; no cheap runtime comparator exists. Recorded, not actioned.

## Adversarial evidence (tests/adversarial/)

Sixteen tests, all negative-path unless noted. 15 pass; 1 is a strict xfail pinning F1.

- `test_ledger_invariant.py` — persistence bypass attempts: the gate demonstrably runs twice at write time even for a caller-set `VERIFIED` finding (call-counting monkeypatch, bound paths asserted); a fabricated quote under `VERIFIED` writes nothing and creates no batch; a quote that passed the public `verify_quote` tool and whose document was then replaced is refused at write time (stale verification carries no authority); one-quote, three-quote, and spec-cited-twice findings are refused with the exact machine reasons; a one-space quote passes the schema and dies at the gate; the stored finding document carries hashes and no local path or file name; caller-set `REJECTED` status is ignored (F6).
- `test_runtime_separation.py` — the one-retry cap binds even when the scripted model still holds a valid third answer (the third batch stays unconsumed); a retried claim is re-verified and a bad correction still persists nothing; the retry feedback JSON exposes exactly `rejection_reason`, `normalized_quote`, and `page_count` — no `pdf_path`, no file names, no unquoted page text (sentinel line asserted absent); a mixed good/bad batch yields one finding and one rejection with disjoint claim texts, exactly one committed batch, and an RFI containing only the persisted claim.
- `test_write_surface.py` — static scan: `.collection(` appears in no runtime module except `specguard/tools.py`, and `google.cloud` is imported only by `run_audit.py`; plus the F1 xfail.

## Verdict per audit item

1. **Central invariant — HOLDS on the application path.** `persist_finding` re-verifies both quotes at write time and ignores caller-set status; no other runtime code can acquire a Firestore collection reference (static-scan receipt); hand-built findings are re-gated; the `rejections` collection stays disjoint from `findings` with no double-counting; a mixed batch persists per-finding atomically with rejected claims excluded. `draft_rfi` is the one surface that accepts unverified input — F1, outside the ledger. Out-of-band Firestore writers are outside the guarantee, disclosed in HANDOFF, missing from README — F2.
2. **Retry loop — HOLDS.** The cap is exactly one retry per claim, enforced by the runtime rather than by model exhaustion (receipted); the retried claim is re-verified; feedback carries only the three `VerificationResult` fields, with no fixture knowledge.
3. **Prompt hygiene — PASS.** `specguard/prompts/audit_claims_v1.txt` (the only prompt file) and `run_audit.py` are fully generic. A repo-wide grep of runtime code for fixture vocabulary hit only the sampling parameter `temperature=0` in agent.py:72, which is not fixture knowledge. The runtime never reads `fixtures/MANIFEST.md`, and the initial and retry messages leak no file names or paths (receipted).
4. **Claims accuracy — PASS with two exceptions.** F2 (README scope sentence) and F5 (agent tool-use narrative). The invisible-text-layer limitation is stated plainly at README.md:34 and pinned by `test_invisible_text_layer_verifies_known_limitation`. SHA-256 is described as chain-of-custody only, never as accuracy proof, in models.py, gate.py, README.md:36, and inside the generated RFI ("They do not prove accuracy."). Every HANDOFF Phase 3 line-number citation in the persistence attack map was checked against the code and matches. The receipted test count (98) reproduced exactly before my additions.
5. **Test fidelity — PASS with notes.** The fakes mirror the contracts the code actually uses (F7 notes the unexercised divergences). The existing retry-cap test proves at most two model turns; my adversarial version strengthens it by proving the cap binds while a valid answer remains. No Phase 3 test claims more than the behavior it exercises; the one behavior claimed but untested anywhere is the malformed-model-turn path (F4).
6. **Config and secrets — PASS.** `location="global"` is the default and only location for the single Gemini construction site (agent.py:27, 49); `temperature=0` is set in `GenerateContentConfig` (agent.py:72) — set in code; whether ADK applies it on the wire was not verifiable offline. Firestore is constructed with project only (run_audit.py:47). The tracked tree contains no credential: the secret-pattern grep hit only prose about secrets and the identifier "token boundaries". The GCP project ID `specguard-hack` at run_audit.py:16 is a non-credential identifier permitted by the knowledge boundary. `artifacts/`, `dist/`, `SETUP.md`, and `PLAN.md` are untracked as intended.

## What this audit did not do

The three real Vertex/Firestore runs (A, B, C) were not re-executed; their receipts in HANDOFF are taken as recorded. ADK's wire-level behavior (temperature application, whether tool calls can fire beside `output_schema`) was not exercised against the network. No production code, existing test, fixture, PLAN.md, or SETUP.md file was modified.

## Overall

**Does the ledger claim hold? Yes** — for every write path that exists in this repository, verified by code reading, a static write-surface scan, and sixteen adversarial tests. Fix F1 and add the F2 sentence before the repo goes public; both route through the implementation session.

## Receipts

All commands ran in `C:\Users\mcspd\dev\specguard` on arya. Every exit code is from the unpiped command shown.

| Command | Exit | Result |
| --- | ---: | --- |
| `git fetch --all` | 0 | Branch `phase-2-demo-fixtures` is local-only; origin/main at 187f430. |
| `git grep -n -i -E "AIza\|BEGIN (RSA \|EC \|OPENSSH )?PRIVATE KEY\|gserviceaccount\|api[_-]?key\|secret\|token\|password\|credential" -- . ':!uv.lock'` | 0 | Hits are prose about secrets and the identifier "token boundaries"; no credential. |
| `git grep -n -i -E "caldra\|veylan\|torven\|asterquay\|manifest\|switchboard\|voltage\|temperature\|planted" -- specguard run_audit.py` | 0 | One hit: `temperature=0` sampling parameter, agent.py:72. |
| `uv run pytest -q` (baseline, before additions) | 0 | `98 passed, 1 warning in 4.35s` — matches the HANDOFF receipt. |
| `uv run pytest -q` (with tests/adversarial) | 0 | `113 passed, 1 xfailed, 1 warning in 5.60s`. The xfail is the F1 pin. |
| `uv run pytest -q tests/adversarial` | 0 | `15 passed, 1 xfailed`. |
| `uv run ruff check .` | 0 | `All checks passed!` |
| `uv run ruff format --check .` | 0 | `24 files already formatted`. |
