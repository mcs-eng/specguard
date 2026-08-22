"""In-memory Firestore test double for Phase 3 unit tests."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

from google.api_core.exceptions import NotFound


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

    def get(self) -> FakeDocumentSnapshot:
        collection = self.client.data.get(self.collection_name, {})
        exists = self.id in collection
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


class FakeFirestoreClient:
    def __init__(self) -> None:
        self.data: dict[str, dict[str, dict[str, Any]]] = {}
        self.batches: list[FakeBatch] = []
        self._ids: dict[str, int] = {}

    def collection(self, name: str) -> FakeCollectionReference:
        return FakeCollectionReference(self, name)

    def batch(self) -> FakeBatch:
        batch = FakeBatch(self)
        self.batches.append(batch)
        return batch

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
