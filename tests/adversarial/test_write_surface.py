"""The write surface itself: which code can touch Firestore, and what draft_rfi trusts."""

from __future__ import annotations

import ast
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


def test_only_the_tools_and_the_web_repository_acquire_firestore_collections() -> None:
    """The web repository is not read-only; only the tools write a claim record.

    The repository writes run records, submission tokens, and rate-limit
    counters. What it never writes is a findings, rejections, or integrity
    record, and that is what the next test proves for the ledger itself.
    """
    collection_users = [
        path.name for path, source in _runtime_sources().items() if ".collection(" in source
    ]
    assert collection_users == ["tools.py", "repository.py"]


#: Every Firestore call that can change a stored document. ``set`` and
#: ``create`` also create one.
_WRITE_METHODS = frozenset({"set", "update", "create", "delete", "add"})

#: The two functions allowed to write into the findings collection: the guarded
#: write, and the severity annotation that updates an already verified record
#: in place.
_ALLOWED_FINDING_WRITERS = frozenset({"persist_finding", "update_finding_severity"})


def _names_the_findings_collection(node: ast.AST) -> bool:
    """Report whether this expression reaches ``.collection(FINDINGS_COLLECTION)``."""
    for inner in ast.walk(node):
        if not isinstance(inner, ast.Call):
            continue
        function = inner.func
        if not isinstance(function, ast.Attribute) or function.attr != "collection":
            continue
        for argument in inner.args:
            if isinstance(argument, ast.Name) and argument.id == "FINDINGS_COLLECTION":
                return True
            if isinstance(argument, ast.Constant) and argument.value == "findings":
                return True
    return False


def _findings_writes(source: str) -> list[tuple[str | None, str, int]]:
    """Return every write into the findings collection as (function, method, line).

    A write is found two ways, because Firestore offers both. A document
    reference can be written directly, as in ``finding_ref.update(...)``. A
    reference can also be handed to a batch or a transaction, as in
    ``batch.set(finding_ref, ...)``. Both forms are matched here, and a
    reference bound to a local name is followed to the expression that made it,
    so renaming the variable hides nothing.
    """
    tree = ast.parse(source)
    parents: dict[ast.AST, ast.AST] = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[child] = node

    def enclosing_function(node: ast.AST) -> str | None:
        current: ast.AST | None = node
        while current is not None:
            if isinstance(current, ast.FunctionDef | ast.AsyncFunctionDef):
                return current.name
            current = parents.get(current)
        return None

    findings_names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and _names_the_findings_collection(node.value):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    findings_names.add(target.id)

    def reaches_findings(node: ast.AST) -> bool:
        if isinstance(node, ast.Name):
            return node.id in findings_names
        if isinstance(node, ast.Attribute | ast.Call | ast.Subscript):
            if _names_the_findings_collection(node):
                return True
            return any(
                isinstance(inner, ast.Name) and inner.id in findings_names
                for inner in ast.walk(node)
            )
        return False

    writes: list[tuple[str | None, str, int]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        method = node.func.attr
        if method not in _WRITE_METHODS:
            continue
        receiver_writes = reaches_findings(node.func.value)
        argument_writes = bool(node.args) and reaches_findings(node.args[0])
        if receiver_writes or argument_writes:
            writes.append((enclosing_function(node), method, node.lineno))
    return writes


def test_only_two_named_functions_write_into_the_findings_collection() -> None:
    """Any write into the ledger outside those two functions fails this test.

    The earlier version of this test looked for the literal text ``batch.set(``
    and so proved only that no *other module* used that one call form. It would
    have passed while ``tools.py`` grew a second ``finding_ref.set(...)``, a
    ``delete``, or a transaction write. This one parses the source instead.
    """
    offenders: list[str] = []
    found: dict[str, set[str]] = {}
    for path, source in _runtime_sources().items():
        for function_name, method, line in _findings_writes(source):
            if function_name in _ALLOWED_FINDING_WRITERS:
                found.setdefault(str(function_name), set()).add(method)
                continue
            offenders.append(f"{path.name}:{line} {function_name}() calls .{method}()")
    assert offenders == []

    # The allowlist must not be satisfiable by finding nothing at all. Both
    # writes still have to be there, or this test proves only that the parser
    # matched no code.
    assert found.get("persist_finding") == {"set"}
    assert found.get("update_finding_severity") == {"update"}


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
