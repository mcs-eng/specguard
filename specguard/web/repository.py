"""Firestore reads and writes used by the public findings page."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from datetime import datetime
from typing import Any, Protocol

from google.cloud import firestore
from google.cloud.firestore_v1.base_query import FieldFilter

from specguard.tools import (
    FINDINGS_COLLECTION,
    INTEGRITY_FINDINGS_COLLECTION,
    REJECTIONS_COLLECTION,
)

RUNS_COLLECTION = "runs"
SAMPLE_RUN_LIMITS_COLLECTION = "sample_run_limits"
SAMPLE_IP_LIMITS_COLLECTION = "sample_ip_limits"
GATE_CHECK_LIMITS_COLLECTION = "gate_check_limits"
UPLOAD_SUBMISSION_TOKENS_COLLECTION = "upload_submission_tokens"


class SubmissionTokenRefused(Exception):
    """An upload submission token this service never minted, or one that expired."""


class RunRepository(Protocol):
    """The Firestore records that one findings-page request reads or writes."""

    def create_run(self, run: Mapping[str, Any]) -> None:
        """Store one completed, failed, or quarantined audit run."""

    def mint_submission_token(self, submission_token: str, *, expires_at: datetime) -> None:
        """Record one submission token the service issued, with its expiry."""

    def create_upload_run(
        self, run: Mapping[str, Any], submission_token: str, *, now: datetime
    ) -> str | None:
        """Claim a minted token and create its run, or return the run it already owns.

        Raise :class:`SubmissionTokenRefused` for a token this service never
        minted, or for one whose expiry has passed.
        """

    def get_submission_token(self, submission_token: str) -> dict[str, Any] | None:
        """Return the stored record for a submission token, if one exists."""

    def reserve_sample_run(
        self,
        *,
        day: str,
        hour: str,
        client_ip: str,
        hourly_limit: int,
        daily_limit: int,
    ) -> bool:
        """Atomically reserve one sample run within both durable limits."""

    def reserve_gate_check(self, *, hour: str, client_ip: str, hourly_limit: int) -> bool:
        """Atomically reserve one gate-playground check within the hourly limit."""

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

    def mint_submission_token(self, submission_token: str, *, expires_at: datetime) -> None:
        """Record a token this service issued, keyed by its digest rather than its value."""
        self._collection(UPLOAD_SUBMISSION_TOKENS_COLLECTION).document(
            _submission_token_id(submission_token)
        ).set({"expires_at": expires_at, "run_id": None})

    def create_upload_run(
        self, run: Mapping[str, Any], submission_token: str, *, now: datetime
    ) -> str | None:
        """Claim a minted token and create its run in one Firestore transaction."""
        run_id = str(run["run_id"])
        run_ref = self._collection(RUNS_COLLECTION).document(run_id)
        token_ref = self._collection(UPLOAD_SUBMISSION_TOKENS_COLLECTION).document(
            _submission_token_id(submission_token)
        )

        @firestore.transactional
        def create(transaction: firestore.Transaction) -> str | None:
            token = token_ref.get(transaction=transaction)
            if not token.exists:
                raise SubmissionTokenRefused("the upload submission token was never minted")
            record = token.to_dict() or {}
            recorded_run_id = record.get("run_id")
            if recorded_run_id:
                return str(recorded_run_id)
            if _token_has_expired(record, now):
                raise SubmissionTokenRefused("the upload submission token expired")
            transaction.set(run_ref, dict(run))
            transaction.set(token_ref, {"run_id": run_id}, merge=True)
            return None

        return create(self._client_for_transactions().transaction())

    def get_submission_token(self, submission_token: str) -> dict[str, Any] | None:
        """Read one token record without exposing the token value in its document ID."""
        snapshot = (
            self._collection(UPLOAD_SUBMISSION_TOKENS_COLLECTION)
            .document(_submission_token_id(submission_token))
            .get()
        )
        return _snapshot_data(snapshot) if snapshot.exists else None

    def reserve_sample_run(
        self,
        *,
        day: str,
        hour: str,
        client_ip: str,
        hourly_limit: int,
        daily_limit: int,
    ) -> bool:
        """Reserve both sample limits in one transaction that survives cold starts."""
        daily_counter = self._collection(SAMPLE_RUN_LIMITS_COLLECTION).document(day)
        ip_counter = self._collection(SAMPLE_IP_LIMITS_COLLECTION).document(
            _sample_ip_counter_id(hour, client_ip)
        )

        @firestore.transactional
        def reserve(transaction: firestore.Transaction) -> bool:
            daily_snapshot = daily_counter.get(transaction=transaction)
            ip_snapshot = ip_counter.get(transaction=transaction)
            daily_count = _counter_count(daily_snapshot)
            ip_count = _counter_count(ip_snapshot)
            if daily_count >= daily_limit or ip_count >= hourly_limit:
                return False
            transaction.set(daily_counter, {"day": day, "count": daily_count + 1})
            transaction.set(ip_counter, {"hour": hour, "count": ip_count + 1})
            return True

        return reserve(self._client_for_transactions().transaction())

    def reserve_gate_check(self, *, hour: str, client_ip: str, hourly_limit: int) -> bool:
        """Reserve one gate-playground check in a transaction that survives cold starts.

        The playground makes no model call and writes no run, so its only cost
        is one read of a committed fixture. The limit keeps that read from
        being used as free compute; it rations no scarce resource.
        """
        counter = self._collection(GATE_CHECK_LIMITS_COLLECTION).document(
            _sample_ip_counter_id(hour, client_ip)
        )

        @firestore.transactional
        def reserve(transaction: firestore.Transaction) -> bool:
            snapshot = counter.get(transaction=transaction)
            count = _counter_count(snapshot)
            if count >= hourly_limit:
                return False
            transaction.set(counter, {"hour": hour, "count": count + 1})
            return True

        return reserve(self._client_for_transactions().transaction())

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
        return self._client_for_transactions().collection(name)

    def _client_for_transactions(self) -> firestore.Client:
        if self._client is None:
            self._client = firestore.Client(project=self._project_id)
        return self._client


def _submission_token_id(submission_token: str) -> str:
    """Return a safe opaque Firestore key for an upload submission token."""
    return hashlib.sha256(submission_token.encode("utf-8")).hexdigest()


def _sample_ip_counter_id(hour: str, client_ip: str) -> str:
    """Keep the internal rate-limit key stable without storing the raw IP address."""
    return hashlib.sha256(f"{hour}:{client_ip}".encode()).hexdigest()


def _token_has_expired(record: Mapping[str, Any], now: datetime) -> bool:
    """Treat a token with no readable expiry as expired, never as valid."""
    expires_at = record.get("expires_at")
    if not isinstance(expires_at, datetime):
        return True
    return expires_at <= now


def _counter_count(snapshot: Any) -> int:
    """Read a Firestore counter snapshot as a non-negative integer."""
    return int(snapshot.to_dict().get("count", 0)) if snapshot.exists else 0


def _snapshot_data(snapshot: Any) -> dict[str, Any]:
    """Copy a Firestore snapshot into template-safe data."""
    data = snapshot.to_dict()
    return {} if data is None else dict(data)
