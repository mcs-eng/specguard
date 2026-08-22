"""The demo ledger reset: it archives, and it never deletes before it copies."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from scripts.reset_demo_ledger import (
    ARCHIVE_PREFIX,
    LEDGER_COLLECTIONS,
    CloudStorageObjectStore,
    FirestoreLedgerStore,
    archive_collection_name,
    archive_object_name,
    archive_objects,
    build_archive_key,
    main,
    reset_demo_ledger,
)
from tests.fake_firestore import FakeFirestoreClient

ARCHIVE_KEY = "20260821T183000Z"
RUN_ID = "run-abc123"


class FakeObjectStore:
    """In-memory bucket that records the order of copies and deletes."""

    def __init__(self, objects: dict[str, bytes] | None = None) -> None:
        self.objects: dict[str, bytes] = dict(objects or {})
        self.operations: list[tuple[str, str]] = []

    def list_object_names(self) -> list[str]:
        return sorted(self.objects)

    def copy_object(self, source_name: str, destination_name: str) -> None:
        self.operations.append(("copy", destination_name))
        self.objects[destination_name] = self.objects[source_name]

    def delete_object(self, object_name: str) -> None:
        self.operations.append(("delete", object_name))
        self.objects.pop(object_name, None)


class FailingCopyObjectStore(FakeObjectStore):
    """A bucket whose copy fails, to prove a delete never runs without one."""

    def copy_object(self, source_name: str, destination_name: str) -> None:
        raise RuntimeError("copy failed")


class FailingLastCopyObjectStore(FakeObjectStore):
    """A bucket whose final copy fails, to prove the phase boundary holds."""

    def copy_object(self, source_name: str, destination_name: str) -> None:
        if len([kind for kind, _ in self.operations if kind == "copy"]) == 2:
            raise RuntimeError("copy failed")
        super().copy_object(source_name, destination_name)


class RecordingLedgerStore:
    """Wrap a ledger store and record the order of writes and deletes."""

    def __init__(self, inner: FirestoreLedgerStore) -> None:
        self._inner = inner
        self.operations: list[tuple[str, str]] = []

    def stream_documents(self, collection: str) -> Any:
        return self._inner.stream_documents(collection)

    def write_document(self, collection: str, document_id: str, data: dict[str, Any]) -> None:
        self.operations.append(("write", collection))
        self._inner.write_document(collection, document_id, data)

    def delete_document(self, collection: str, document_id: str) -> None:
        self.operations.append(("delete", collection))
        self._inner.delete_document(collection, document_id)


def _populated_client() -> FakeFirestoreClient:
    client = FakeFirestoreClient()
    client.collection("runs").document(RUN_ID).set({"run_id": RUN_ID, "status": "COMPLETED"})
    client.collection("findings").document("finding-1").set(
        {"run_id": RUN_ID, "claim_text": "A fictional claim."}
    )
    client.collection("findings").document("finding-2").set(
        {"run_id": RUN_ID, "claim_text": "A second fictional claim."}
    )
    client.collection("rejections").document("rejection-1").set(
        {"run_id": RUN_ID, "reason": "quote_not_found_on_cited_page"}
    )
    client.collection("integrity_findings").document("integrity-1").set(
        {"run_id": RUN_ID, "flagged_pages": [1]}
    )
    return client


def _bucket_objects() -> dict[str, bytes]:
    return {
        f"{RUN_ID}/specification.pdf": b"%PDF-1.7 spec",
        f"{RUN_ID}/submitted-document.pdf": b"%PDF-1.7 cut sheet",
        f"{RUN_ID}/rfi.pdf": b"%PDF-1.7 rfi",
    }


def test_reset_leaves_every_live_collection_empty() -> None:
    """The live ledger is empty after a reset, so the shoot starts on a clean page."""
    client = _populated_client()
    store = FakeObjectStore(_bucket_objects())

    reset_demo_ledger(FirestoreLedgerStore(client), store, archive_key=ARCHIVE_KEY)

    for collection in LEDGER_COLLECTIONS:
        assert client.data.get(collection, {}) == {}, collection
    assert [name for name in store.objects if not name.startswith(f"{ARCHIVE_PREFIX}/")] == []


def test_reset_archives_every_document_with_its_identifier_and_data() -> None:
    """Nothing is dropped: each archived document keeps its id and its fields."""
    client = _populated_client()
    store = FakeObjectStore(_bucket_objects())

    report = reset_demo_ledger(FirestoreLedgerStore(client), store, archive_key=ARCHIVE_KEY)

    assert client.data[archive_collection_name("runs", ARCHIVE_KEY)] == {
        RUN_ID: {"run_id": RUN_ID, "status": "COMPLETED"}
    }
    archived_findings = client.data[archive_collection_name("findings", ARCHIVE_KEY)]
    assert sorted(archived_findings) == ["finding-1", "finding-2"]
    assert archived_findings["finding-2"] == {
        "run_id": RUN_ID,
        "claim_text": "A second fictional claim.",
    }
    assert client.data[archive_collection_name("rejections", ARCHIVE_KEY)]["rejection-1"] == {
        "run_id": RUN_ID,
        "reason": "quote_not_found_on_cited_page",
    }
    assert archive_collection_name("integrity_findings", ARCHIVE_KEY) in client.data
    assert report.documents_by_collection == {
        "runs": 1,
        "findings": 2,
        "rejections": 1,
        "integrity_findings": 1,
    }
    assert report.documents_archived == 5


def test_reset_archives_every_bucket_object_under_the_timestamped_prefix() -> None:
    """Each object moves to the archive prefix and keeps its run-scoped name."""
    client = FakeFirestoreClient()
    store = FakeObjectStore(_bucket_objects())

    report = reset_demo_ledger(FirestoreLedgerStore(client), store, archive_key=ARCHIVE_KEY)

    assert sorted(store.objects) == [
        f"{ARCHIVE_PREFIX}/{ARCHIVE_KEY}/{RUN_ID}/rfi.pdf",
        f"{ARCHIVE_PREFIX}/{ARCHIVE_KEY}/{RUN_ID}/specification.pdf",
        f"{ARCHIVE_PREFIX}/{ARCHIVE_KEY}/{RUN_ID}/submitted-document.pdf",
    ]
    assert store.objects[f"{ARCHIVE_PREFIX}/{ARCHIVE_KEY}/{RUN_ID}/rfi.pdf"] == b"%PDF-1.7 rfi"
    assert report.objects_archived == 3
    assert report.objects_skipped_already_archived == 0


def test_the_whole_copy_phase_finishes_before_the_first_delete() -> None:
    """A copy that fails part way must delete nothing at all."""
    store = FakeObjectStore(_bucket_objects())

    archive_objects(store, archive_key=ARCHIVE_KEY)

    kinds = [operation[0] for operation in store.operations]
    assert kinds == ["copy", "copy", "copy", "delete", "delete", "delete"]


def test_a_copy_that_fails_on_the_last_object_deletes_nothing() -> None:
    """The phase boundary is what makes a partial archive safe."""
    store = FailingLastCopyObjectStore(_bucket_objects())

    with pytest.raises(RuntimeError, match="copy failed"):
        archive_objects(store, archive_key=ARCHIVE_KEY)

    live = [name for name in store.objects if not name.startswith(f"{ARCHIVE_PREFIX}/")]
    assert sorted(live) == sorted(_bucket_objects())
    assert "delete" not in [operation[0] for operation in store.operations]


def test_firestore_documents_are_all_copied_before_any_is_deleted() -> None:
    """The same phase boundary applies to each Firestore collection."""
    client = _populated_client()
    store = FakeObjectStore()
    recording = RecordingLedgerStore(FirestoreLedgerStore(client))

    reset_demo_ledger(recording, store, archive_key=ARCHIVE_KEY)

    archive = archive_collection_name("findings", ARCHIVE_KEY)
    findings_operations = [
        kind for kind, collection in recording.operations if collection in {"findings", archive}
    ]
    assert findings_operations == ["write", "write", "delete", "delete"]


def test_an_archive_key_that_already_holds_documents_is_refused() -> None:
    """One archive key must never overwrite an earlier archive."""
    client = _populated_client()
    client.collection(archive_collection_name("runs", ARCHIVE_KEY)).document("old").set(
        {"run_id": "an-earlier-run"}
    )

    with pytest.raises(ValueError, match="archive destination already holds data"):
        reset_demo_ledger(FirestoreLedgerStore(client), FakeObjectStore(), archive_key=ARCHIVE_KEY)

    assert client.data["runs"] != {}


def test_an_archive_key_that_already_holds_objects_is_refused() -> None:
    """The same guard protects the bucket side."""
    objects = _bucket_objects()
    objects[f"{ARCHIVE_PREFIX}/{ARCHIVE_KEY}/earlier-run/rfi.pdf"] = b"%PDF-1.7 earlier"
    store = FakeObjectStore(objects)

    with pytest.raises(ValueError, match="archive destination already holds objects"):
        archive_objects(store, archive_key=ARCHIVE_KEY)

    assert store.operations == []


def test_a_failed_copy_deletes_nothing() -> None:
    """If the archive write fails, the live object stays where it is."""
    store = FailingCopyObjectStore(_bucket_objects())

    with pytest.raises(RuntimeError, match="copy failed"):
        archive_objects(store, archive_key=ARCHIVE_KEY)

    assert sorted(store.objects) == sorted(_bucket_objects())
    assert store.operations == []


def test_a_second_reset_never_nests_one_archive_inside_another() -> None:
    """Objects already under the archive prefix are left alone and counted."""
    client = FakeFirestoreClient()
    store = FakeObjectStore(_bucket_objects())
    reset_demo_ledger(FirestoreLedgerStore(client), store, archive_key=ARCHIVE_KEY)

    second_key = "20260822T090000Z"
    store.objects["later-run/rfi.pdf"] = b"%PDF-1.7 later"
    report = reset_demo_ledger(FirestoreLedgerStore(client), store, archive_key=second_key)

    assert report.objects_archived == 1
    assert report.objects_skipped_already_archived == 3
    assert f"{ARCHIVE_PREFIX}/{second_key}/later-run/rfi.pdf" in store.objects
    assert f"{ARCHIVE_PREFIX}/{second_key}/{ARCHIVE_PREFIX}" not in str(sorted(store.objects))


def test_the_archive_key_is_one_sortable_utc_stamp() -> None:
    """One reset uses one key, so every archived item is grouped by that reset."""
    key = build_archive_key(datetime(2026, 8, 21, 18, 30, 0, tzinfo=UTC))

    assert key == ARCHIVE_KEY
    assert archive_collection_name("runs", key) == f"{ARCHIVE_PREFIX}_{ARCHIVE_KEY}_runs"
    assert archive_object_name("r/x.pdf", key) == f"{ARCHIVE_PREFIX}/{ARCHIVE_KEY}/r/x.pdf"


def test_the_reset_refuses_to_run_without_the_confirm_flag(capsys: Any) -> None:
    """No flag, no writes: the script reports what it would do and exits 2."""
    exit_code = main([])
    output = capsys.readouterr().out

    assert exit_code == 2
    assert "Refusing to reset the demo ledger without --confirm." in output
    assert "runs, findings, rejections, integrity_findings" in output


def test_the_report_summary_line_names_the_key_and_the_totals() -> None:
    """The receipt line is what a shoot-day operator pastes back."""
    client = _populated_client()
    store = FakeObjectStore(_bucket_objects())

    report = reset_demo_ledger(FirestoreLedgerStore(client), store, archive_key=ARCHIVE_KEY)

    assert report.summary_line() == (
        f"archive key {ARCHIVE_KEY}: 5 documents archived across 4 collections, "
        "3 bucket objects archived"
    )


def test_the_cloud_storage_adapter_uses_a_server_side_copy() -> None:
    """A server-side copy preserves the content type the findings page serves."""
    calls: list[tuple[str, str]] = []

    class FakeBlob:
        def __init__(self, name: str) -> None:
            self.name = name

        def delete(self) -> None:
            calls.append(("delete", self.name))

    class FakeBucket:
        def list_blobs(self) -> list[FakeBlob]:
            return [FakeBlob("run-1/rfi.pdf")]

        def blob(self, name: str) -> FakeBlob:
            return FakeBlob(name)

        def copy_blob(self, source: FakeBlob, bucket: Any, destination_name: str) -> None:
            assert bucket is self
            calls.append((source.name, destination_name))

    store = CloudStorageObjectStore(FakeBucket())
    archive_objects(store, archive_key=ARCHIVE_KEY)

    assert calls == [
        ("run-1/rfi.pdf", f"{ARCHIVE_PREFIX}/{ARCHIVE_KEY}/run-1/rfi.pdf"),
        ("delete", "run-1/rfi.pdf"),
    ]
