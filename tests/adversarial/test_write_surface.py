"""The write surface itself: which code can touch Firestore, and what draft_rfi trusts."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from specguard.models import PersistedFinding, PersistedQuote
from tests.adversarial.harness import RUN_ID, Harness

REPO_ROOT = Path(__file__).parents[2]


def _runtime_sources() -> dict[Path, str]:
    files = sorted((REPO_ROOT / "specguard").rglob("*.py"))
    files.append(REPO_ROOT / "run_audit.py")
    return {path: path.read_text(encoding="utf-8") for path in files}


def test_only_the_tools_and_read_only_web_repository_acquire_firestore_collections() -> None:
    """The web repository may read records, but only tools may write claim records."""
    collection_users = [
        path.name for path, source in _runtime_sources().items() if ".collection(" in source
    ]
    assert collection_users == ["tools.py", "repository.py"]

    finding_writers = [
        path.name
        for path, source in _runtime_sources().items()
        if "FINDINGS_COLLECTION" in source and "batch.set(" in source
    ]
    assert finding_writers == ["tools.py"]


def test_firestore_clients_exist_only_at_the_cli_and_web_boundaries() -> None:
    """The audit and web adapters construct clients and inject them into tools."""
    importers = [
        path.name
        for path, source in _runtime_sources().items()
        if "from google.cloud import firestore" in source
    ]
    assert importers == ["repository.py", "runtime.py", "run_audit.py"]


def test_draft_rfi_refuses_a_fabricated_quote_even_with_correct_hashes(tmp_path: Path) -> None:
    """A forged PersistedFinding with correct document hashes must be refused.

    The document hashes bind the finding to the right byte streams, but they
    say nothing about the quote text. ``draft_rfi`` re-verifies each quote
    through the gate against the two bound source PDFs and raises before it
    renders anything.
    """
    harness = Harness(tmp_path)
    spec_hash = hashlib.sha256(harness.spec.read_bytes()).hexdigest()
    cut_hash = hashlib.sha256(harness.cut_sheet.read_bytes()).hexdigest()
    forged = PersistedFinding(
        finding_id="finding-forged",
        run_id=RUN_ID,
        claim_text="Forged claim that was never persisted or verified.",
        spec_quote=PersistedQuote(
            text="This sentence is absent from the specification.",
            page_number=1,
            document_sha256=spec_hash,
        ),
        cut_sheet_quote=PersistedQuote(
            text="This sentence is absent from the submitted document.",
            page_number=1,
            document_sha256=cut_hash,
        ),
    )
    with pytest.raises(ValueError):
        harness.tools.draft_rfi([forged])
