"""Durable object storage for uploaded documents and generated RFIs."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Protocol

from google.cloud import storage


@dataclass(frozen=True)
class StoredObject:
    """Identity and content hash of one object written for an audit run."""

    object_name: str
    sha256: str
    content_type: str


class ObjectStorage(Protocol):
    """The small object-storage surface the findings page needs."""

    def upload_bytes(self, object_name: str, data: bytes, content_type: str) -> StoredObject:
        """Store bytes and return their durable identity."""

    def download_bytes(self, object_name: str) -> bytes:
        """Read one stored object."""

    def delete_object(self, object_name: str) -> None:
        """Remove one stored object when no run record can reference it."""


class CloudStorage:
    """Google Cloud Storage implementation for one configured bucket."""

    def __init__(self, *, bucket_name: str, project_id: str) -> None:
        self._bucket_name = bucket_name
        self._project_id = project_id
        self._client: storage.Client | None = None

    def upload_bytes(self, object_name: str, data: bytes, content_type: str) -> StoredObject:
        """Write one object and record the SHA-256 of the uploaded bytes."""
        blob = self._bucket().blob(object_name)
        blob.upload_from_string(data, content_type=content_type)
        return StoredObject(
            object_name=object_name,
            sha256=hashlib.sha256(data).hexdigest(),
            content_type=content_type,
        )

    def download_bytes(self, object_name: str) -> bytes:
        """Read one object by its run-scoped object name."""
        return self._bucket().blob(object_name).download_as_bytes()

    def delete_object(self, object_name: str) -> None:
        """Delete an unreferenced run-scoped object."""
        self._bucket().blob(object_name).delete()

    def _bucket(self) -> storage.Bucket:
        if self._client is None:
            self._client = storage.Client(project=self._project_id)
        return self._client.bucket(self._bucket_name)
