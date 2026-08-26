"""In-memory Firestore test double for Phase 3 unit tests.

The transaction support added in Phase 7d is deliberately thin but not fake in
the part that matters. It models Firestore's optimistic concurrency: a
transaction records the version of every document it read, and its commit is
refused with ``Aborted`` when any of those documents changed in between. The
retry loop that answers that refusal is the real
``google.cloud.firestore.transactional`` decorator, not a copy of it, so a test
written against this double exercises the library's own retry behaviour.
"""

from __future__ import annotations

import secrets
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Any

from google.api_core.exceptions import Aborted, NotFound


@dataclass(frozen=True)
class FakeDocumentSnapshot:
    exists: bool
    _data: dict[str, Any] | None = None
    id: str = ""

    def to_dict(self) -> dict[str, Any] | None:
        return self._data


@dataclass(frozen=True)
class FakeDocumentReference:
    client: FakeFirestoreClient
    collection_name: str
    id: str

    def set(self, data: dict[str, Any], merge: bool = False) -> None:
        self.client._write(self.collection_name, self.id, data, merge=merge)

    def update(self, data: dict[str, Any]) -> None:
        """Merge into an existing document, or fail the way Firestore fails.

        Real Firestore raises ``NotFound`` when ``update`` names a document
        that does not exist; only ``set`` creates one. A fake that quietly
        created the document would hide exactly the write this test double
        exists to expose.
        """
        if self.id not in self.client.data.get(self.collection_name, {}):
            raise NotFound(f"no document {self.collection_name}/{self.id}")
        self.client._write(self.collection_name, self.id, data, merge=True)

    def get(self, transaction: FakeTransaction | None = None) -> FakeDocumentSnapshot:
        collection = self.client.data.get(self.collection_name, {})
        exists = self.id in collection
        if transaction is not None:
            transaction.record_read(self)
        return FakeDocumentSnapshot(exists=exists, _data=collection.get(self.id), id=self.id)

    def delete(self) -> None:
        self.client.data.get(self.collection_name, {}).pop(self.id, None)


class FakeCollectionReference:
    def __init__(self, client: FakeFirestoreClient, name: str) -> None:
        self._client = client
        self._name = name

    def document(self, document_id: str | None = None) -> FakeDocumentReference:
        if document_id is None:
            document_id = self._client.next_id(self._name)
        return FakeDocumentReference(self._client, self._name, document_id)

    def stream(self) -> Iterator[FakeDocumentSnapshot]:
        for document_id, data in list(self._client.data.get(self._name, {}).items()):
            yield FakeDocumentSnapshot(exists=True, _data=data, id=document_id)


class FakeBatch:
    def __init__(self, client: FakeFirestoreClient) -> None:
        self._client = client
        self._operations: list[tuple[FakeDocumentReference, dict[str, Any], bool]] = []
        self.committed = False

    def set(
        self,
        reference: FakeDocumentReference,
        data: dict[str, Any],
        merge: bool = False,
    ) -> None:
        self._operations.append((reference, data, merge))

    def commit(self) -> list[object]:
        for reference, data, merge in self._operations:
            self._client._write(reference.collection_name, reference.id, data, merge=merge)
        self.committed = True
        return []


class FakeTransaction:
    """A transaction with Firestore's read-version precondition and nothing else.

    Only the surface ``google.cloud.firestore.transactional`` drives is
    implemented: begin, commit, rollback, and the write queue. Writes are held
    until commit, and commit is refused when a document this transaction read
    has been written by anyone else in the meantime. That refusal is what makes
    the library retry, and the retry is what makes a counter sequential.
    """

    #: The library's own default. Named here so a test can read the bound.
    MAX_ATTEMPTS = 5

    def __init__(self, client: FakeFirestoreClient) -> None:
        self._client = client
        self._id: bytes | None = None
        self._max_attempts = self.MAX_ATTEMPTS
        self._read_only = False
        self._read_versions: dict[tuple[str, str], int] = {}
        self._writes: list[tuple[FakeDocumentReference, dict[str, Any], bool]] = []
        self.attempts = 0

    def record_read(self, reference: FakeDocumentReference) -> None:
        """Remember the version this transaction read a document at."""
        key = (reference.collection_name, reference.id)
        self._read_versions[key] = self._client.version(*key)

    def set(
        self,
        reference: FakeDocumentReference,
        data: dict[str, Any],
        merge: bool = False,
    ) -> None:
        self._writes.append((reference, data, merge))

    def _clean_up(self) -> None:
        self._id = None
        self._read_versions = {}
        self._writes = []

    def _begin(self, retry_id: bytes | None = None) -> None:
        self._clean_up()
        self.attempts += 1
        self._id = secrets.token_bytes(8)

    def _rollback(self) -> None:
        self._clean_up()

    def _commit(self) -> list[object]:
        for (collection_name, document_id), version in self._read_versions.items():
            if self._client.version(collection_name, document_id) != version:
                self._clean_up()
                raise Aborted("the transaction read a document that changed before it committed")
        for reference, data, merge in self._writes:
            self._client._write(reference.collection_name, reference.id, data, merge=merge)
        self._clean_up()
        return []


class FakeFirestoreClient:
    def __init__(self) -> None:
        self.data: dict[str, dict[str, dict[str, Any]]] = {}
        self.batches: list[FakeBatch] = []
        self._ids: dict[str, int] = {}
        self._versions: dict[tuple[str, str], int] = {}
        #: Called after every write. A test uses it to interleave a competing
        #: write between another transaction's read and its commit.
        self.on_write: Callable[[str, str], None] | None = None

    def collection(self, name: str) -> FakeCollectionReference:
        return FakeCollectionReference(self, name)

    def batch(self) -> FakeBatch:
        batch = FakeBatch(self)
        self.batches.append(batch)
        return batch

    def transaction(self) -> FakeTransaction:
        return FakeTransaction(self)

    def version(self, collection_name: str, document_id: str) -> int:
        """Return how many times one document has been written."""
        return self._versions.get((collection_name, document_id), 0)

    def next_id(self, collection_name: str) -> str:
        value = self._ids.get(collection_name, 0) + 1
        self._ids[collection_name] = value
        return f"{collection_name}-{value}"

    def _write(
        self,
        collection_name: str,
        document_id: str,
        data: dict[str, Any],
        *,
        merge: bool,
    ) -> None:
        collection = self.data.setdefault(collection_name, {})
        if merge and document_id in collection:
            collection[document_id] = {**collection[document_id], **data}
        else:
            collection[document_id] = dict(data)
        key = (collection_name, document_id)
        self._versions[key] = self._versions.get(key, 0) + 1
        if self.on_write is not None:
            self.on_write(collection_name, document_id)
