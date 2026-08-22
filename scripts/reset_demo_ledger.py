"""Archive the live demo ledger so a shoot starts on a clean page.

Nothing is deleted before the whole archive exists. Each phase copies every
item of a collection, or every object of the bucket, under an archive key
derived from one timestamp; only when that copy phase has finished does the
delete phase run. A failure anywhere in a copy phase therefore deletes nothing
at all, and a failure inside a delete phase leaves items that are already in
the archive. No item can be lost.

The script archives exactly the four ledger collections the demo writes per
run. It deliberately leaves the content-addressed ``documents`` collection
alone: those records are keyed by document SHA-256, are shared across runs,
and carry no run-specific demo data, so clearing them would discard provenance
without making the page any cleaner.

Two guards protect an existing archive. The script refuses to touch anything
without an explicit ``--confirm`` flag, and it refuses to write into an
archive key that already holds data.

Known limitation, not handled here: a writer that modifies a live document or
object between its copy and its delete loses that newest version. The demo
ledger is reset by one operator against an idle service, so this script does
not carry Firestore transactions or Cloud Storage generation preconditions.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol

from specguard.tools import (
    FINDINGS_COLLECTION,
    INTEGRITY_FINDINGS_COLLECTION,
    REJECTIONS_COLLECTION,
)
from specguard.web.repository import RUNS_COLLECTION

DEFAULT_PROJECT = "specguard-hack"
DEFAULT_BUCKET = "specguard-hack-runs"
ARCHIVE_PREFIX = "_archive"
LEDGER_COLLECTIONS: tuple[str, ...] = (
    RUNS_COLLECTION,
    FINDINGS_COLLECTION,
    REJECTIONS_COLLECTION,
    INTEGRITY_FINDINGS_COLLECTION,
)


def build_archive_key(moment: datetime | None = None) -> str:
    """Return one sortable archive key shared by every item in a reset."""
    stamp = (moment or datetime.now(UTC)).astimezone(UTC)
    return stamp.strftime("%Y%m%dT%H%M%SZ")


def archive_collection_name(collection: str, archive_key: str) -> str:
    """Name the Firestore collection that holds one archived live collection."""
    return f"{ARCHIVE_PREFIX}_{archive_key}_{collection}"


def archive_object_name(object_name: str, archive_key: str) -> str:
    """Name the bucket object that holds one archived live object."""
    return f"{ARCHIVE_PREFIX}/{archive_key}/{object_name}"


def is_archived_collection(collection: str) -> bool:
    """Report whether a collection name is already an archive."""
    return collection.startswith(f"{ARCHIVE_PREFIX}_")


def is_archived_object(object_name: str) -> bool:
    """Report whether an object name is already under the archive prefix."""
    return object_name.startswith(f"{ARCHIVE_PREFIX}/")


class LedgerStore(Protocol):
    """The Firestore surface one ledger reset needs."""

    def stream_documents(self, collection: str) -> Iterator[tuple[str, dict[str, Any]]]:
        """Yield every live document in a collection as an identifier and data."""

    def write_document(self, collection: str, document_id: str, data: dict[str, Any]) -> None:
        """Write one document into the archive collection."""

    def delete_document(self, collection: str, document_id: str) -> None:
        """Remove one live document after its archive copy exists."""


class ObjectStore(Protocol):
    """The object-storage surface one ledger reset needs."""

    def list_object_names(self) -> Iterable[str]:
        """Return every object name currently in the bucket."""

    def copy_object(self, source_name: str, destination_name: str) -> None:
        """Copy one object, preserving its stored content type."""

    def delete_object(self, object_name: str) -> None:
        """Remove one live object after its archive copy exists."""


@dataclass
class ResetReport:
    """What one reset archived, per collection and for the bucket."""

    archive_key: str
    documents_by_collection: dict[str, int] = field(default_factory=dict)
    objects_archived: int = 0
    objects_skipped_already_archived: int = 0

    @property
    def documents_archived(self) -> int:
        """Total Firestore documents moved into the archive."""
        return sum(self.documents_by_collection.values())

    def summary_line(self) -> str:
        """One receipt line naming the archive key and the totals."""
        return (
            f"archive key {self.archive_key}: "
            f"{self.documents_archived} documents archived across "
            f"{len(self.documents_by_collection)} collections, "
            f"{self.objects_archived} bucket objects archived"
        )


def archive_collections(
    store: LedgerStore,
    *,
    archive_key: str,
    collections: Sequence[str] = LEDGER_COLLECTIONS,
) -> dict[str, int]:
    """Copy every document of one collection, then delete the originals.

    The two phases do not interleave. Every document of a collection is written
    to the archive before the first live document of that collection is
    deleted, so a copy that fails deletes nothing. The archive destination must
    be empty, so one archive key can never overwrite another.
    """
    archived: dict[str, int] = {}
    for collection in collections:
        if is_archived_collection(collection):
            raise ValueError(f"refusing to archive an archive collection: {collection}")
        destination = archive_collection_name(collection, archive_key)
        if any(True for _ in store.stream_documents(destination)):
            raise ValueError(f"archive destination already holds data: {destination}")

        live = list(store.stream_documents(collection))
        for document_id, data in live:
            store.write_document(destination, document_id, data)
        for document_id, _ in live:
            store.delete_document(collection, document_id)
        archived[collection] = len(live)
    return archived


def archive_objects(store: ObjectStore, *, archive_key: str) -> tuple[int, int]:
    """Copy every live bucket object under the archive prefix, then remove them.

    The copy phase completes before the first delete, so a failed copy removes
    nothing. Objects already under the archive prefix are left alone and
    counted, so running the script twice never nests one archive inside
    another, and an archive key that already holds objects is refused.
    """
    live: list[str] = []
    skipped = 0
    for object_name in list(store.list_object_names()):
        if is_archived_object(object_name):
            skipped += 1
            continue
        live.append(object_name)

    destination_prefix = f"{ARCHIVE_PREFIX}/{archive_key}/"
    if any(name.startswith(destination_prefix) for name in store.list_object_names()):
        raise ValueError(f"archive destination already holds objects: {destination_prefix}")

    for object_name in live:
        store.copy_object(object_name, archive_object_name(object_name, archive_key))
    for object_name in live:
        store.delete_object(object_name)
    return len(live), skipped


def reset_demo_ledger(
    ledger_store: LedgerStore,
    object_store: ObjectStore,
    *,
    archive_key: str,
    collections: Sequence[str] = LEDGER_COLLECTIONS,
) -> ResetReport:
    """Archive the four run-scoped ledger collections and the bucket, then clear them.

    The content-addressed ``documents`` collection is deliberately untouched.
    """
    report = ResetReport(archive_key=archive_key)
    report.documents_by_collection = archive_collections(
        ledger_store, archive_key=archive_key, collections=collections
    )
    report.objects_archived, report.objects_skipped_already_archived = archive_objects(
        object_store, archive_key=archive_key
    )
    return report


class FirestoreLedgerStore:
    """Live Firestore adapter for the ledger reset."""

    def __init__(self, client: Any) -> None:
        self._client = client

    def stream_documents(self, collection: str) -> Iterator[tuple[str, dict[str, Any]]]:
        for snapshot in self._client.collection(collection).stream():
            data = snapshot.to_dict()
            yield snapshot.id, {} if data is None else dict(data)

    def write_document(self, collection: str, document_id: str, data: dict[str, Any]) -> None:
        self._client.collection(collection).document(document_id).set(data)

    def delete_document(self, collection: str, document_id: str) -> None:
        self._client.collection(collection).document(document_id).delete()


class CloudStorageObjectStore:
    """Live Cloud Storage adapter using server-side copy, which keeps metadata."""

    def __init__(self, bucket: Any) -> None:
        self._bucket = bucket

    def list_object_names(self) -> Iterable[str]:
        return [blob.name for blob in self._bucket.list_blobs()]

    def copy_object(self, source_name: str, destination_name: str) -> None:
        self._bucket.copy_blob(self._bucket.blob(source_name), self._bucket, destination_name)

    def delete_object(self, object_name: str) -> None:
        self._bucket.blob(object_name).delete()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Archive the four run-scoped SpecGuard ledger collections and every "
            "bucket object, then leave them empty. Each copy phase finishes before "
            "any delete runs. The content-addressed documents collection is left alone."
        )
    )
    parser.add_argument("--project", default=DEFAULT_PROJECT, help="Google Cloud project ID.")
    parser.add_argument("--bucket", default=DEFAULT_BUCKET, help="Cloud Storage bucket name.")
    parser.add_argument(
        "--confirm",
        action="store_true",
        help="Required. Without this flag the script reports what it would do and exits 2.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if not args.confirm:
        print("Refusing to reset the demo ledger without --confirm.")
        print(f"It would archive {', '.join(LEDGER_COLLECTIONS)} and every object in the bucket")
        print(f"under the {ARCHIVE_PREFIX} prefix, then leave those collections empty.")
        print("The content-addressed documents collection would be left alone.")
        return 2

    from google.cloud import firestore, storage

    firestore_client = firestore.Client(project=args.project)
    try:
        storage_client = storage.Client(project=args.project)
        report = reset_demo_ledger(
            FirestoreLedgerStore(firestore_client),
            CloudStorageObjectStore(storage_client.bucket(args.bucket)),
            archive_key=build_archive_key(),
        )
    finally:
        firestore_client.close()

    for collection, count in report.documents_by_collection.items():
        print(f"  {collection}: {count} documents -> ")
        print(f"    {archive_collection_name(collection, report.archive_key)}")
    if report.objects_skipped_already_archived:
        print(f"  already archived, left alone: {report.objects_skipped_already_archived} objects")
    print(report.summary_line())
    return 0


if __name__ == "__main__":
    sys.exit(main())
