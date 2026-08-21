"""Firestore reads and writes used by the public findings page."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol

from google.cloud import firestore
from google.cloud.firestore_v1.base_query import FieldFilter

from specguard.tools import (
    FINDINGS_COLLECTION,
    INTEGRITY_FINDINGS_COLLECTION,
    REJECTIONS_COLLECTION,
)

RUNS_COLLECTION = "runs"


class RunRepository(Protocol):
    """The Firestore records that one findings-page request reads or writes."""

    def create_run(self, run: Mapping[str, Any]) -> None:
        """Store one completed or quarantined audit run."""

    def list_runs(self, limit: int = 20) -> list[dict[str, Any]]:
        """Return the newest stored audit runs."""

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        """Return one stored audit run."""

    def get_findings(self, run_id: str) -> list[dict[str, Any]]:
        """Return VERIFIED finding records for one run."""

    def get_rejections(self, run_id: str) -> list[dict[str, Any]]:
        """Return REJECTED claim records for one run."""

    def get_integrity_records(self, run_id: str) -> list[dict[str, Any]]:
        """Return QUARANTINED integrity records for one run."""


class FirestoreRunRepository:
    """Lazy Firestore repository for the SpecGuard web service."""

    def __init__(self, *, project_id: str) -> None:
        self._project_id = project_id
        self._client: firestore.Client | None = None

    def create_run(self, run: Mapping[str, Any]) -> None:
        """Write the run document under its public run identifier."""
        self._collection(RUNS_COLLECTION).document(str(run["run_id"])).set(dict(run))

    def list_runs(self, limit: int = 20) -> list[dict[str, Any]]:
        """Read the recent runs in reverse creation order."""
        query = (
            self._collection(RUNS_COLLECTION)
            .order_by("created_at", direction=firestore.Query.DESCENDING)
            .limit(limit)
        )
        return [_snapshot_data(snapshot) for snapshot in query.stream()]

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        """Read one run document, if it exists."""
        snapshot = self._collection(RUNS_COLLECTION).document(run_id).get()
        return _snapshot_data(snapshot) if snapshot.exists else None

    def get_findings(self, run_id: str) -> list[dict[str, Any]]:
        """Read the persisted verified findings for one run."""
        return self._records_for_run(FINDINGS_COLLECTION, run_id)

    def get_rejections(self, run_id: str) -> list[dict[str, Any]]:
        """Read the final rejection records for one run."""
        return self._records_for_run(REJECTIONS_COLLECTION, run_id)

    def get_integrity_records(self, run_id: str) -> list[dict[str, Any]]:
        """Read the human-only integrity evidence for one run."""
        return self._records_for_run(INTEGRITY_FINDINGS_COLLECTION, run_id)

    def _records_for_run(self, collection_name: str, run_id: str) -> list[dict[str, Any]]:
        query = self._collection(collection_name).where(filter=FieldFilter("run_id", "==", run_id))
        return [_snapshot_data(snapshot) for snapshot in query.stream()]

    def _collection(self, name: str) -> firestore.CollectionReference:
        if self._client is None:
            self._client = firestore.Client(project=self._project_id)
        return self._client.collection(name)


def _snapshot_data(snapshot: Any) -> dict[str, Any]:
    """Copy a Firestore snapshot into template-safe data."""
    data = snapshot.to_dict()
    return {} if data is None else dict(data)
