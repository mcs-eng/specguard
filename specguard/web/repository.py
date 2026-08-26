"""Firestore reads and writes used by the public findings page."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from datetime import datetime
from typing import Any, Protocol

from google.cloud import firestore
from google.cloud.firestore_v1.base_query import FieldFilter

from specguard.submittal import format_submittal_number
from specguard.tools import (
    FINDINGS_COLLECTION,
    INTEGRITY_FINDINGS_COLLECTION,
    REJECTIONS_COLLECTION,
)

RUNS_COLLECTION = "runs"
SAMPLE_RUN_LIMITS_COLLECTION = "sample_run_limits"
SAMPLE_IP_LIMITS_COLLECTION = "sample_ip_limits"
GATE_CHECK_LIMITS_COLLECTION = "gate_check_limits"
TOKEN_MINT_LIMITS_COLLECTION = "token_mint_limits"
UPLOAD_SUBMISSION_TOKENS_COLLECTION = "upload_submission_tokens"
COUNTERS_COLLECTION = "counters"

#: The one document that holds the submittal sequence. One counter serves both
#: the submittal number and the RFI number, because one RFI exists per run.
SUBMITTAL_NUMBER_COUNTER = "submittal_number"


class SubmissionTokenRefused(Exception):
    """An upload submission token this service never minted, or one that expired."""


class RunRepository(Protocol):
    """The Firestore records that one findings-page request reads or writes."""

    def create_run(self, run: Mapping[str, Any]) -> None:
        """Store one completed, failed, or quarantined audit run."""

    def assign_submittal_number(self) -> str:
        """Take the next submittal number from the durable sequence."""

    def mint_submission_token(self, submission_token: str, *, expires_at: datetime) -> None:
        """Record one submission token the service issued, with its expiry."""

    def reserve_token_mint(self, *, hour: str, client_ip: str, hourly_limit: int) -> bool:
        """Atomically reserve one submission-token mint within the hourly limit."""

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
        """Return the newest stored audit runs, of every source."""

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        """Return one stored audit run."""

    def get_findings(self, run_id: str) -> list[dict[str, Any]]:
        """Return VERIFIED finding records for one run."""

    def get_rejections(self, run_id: str) -> list[dict[str, Any]]:
        """Return REJECTED claim records for one run."""

    def get_integrity_records(self, run_id: str) -> list[dict[str, Any]]:
        """Return QUARANTINED integrity records for one run."""


class FirestoreRunRepository:
    """Lazy Firestore repository for the SpecGuard web service.

    The client is built on first use so importing this module needs no
    credentials. A caller may supply one instead, which is how the transaction
    behaviour is exercised against an in-process fake rather than a project.
    """

    def __init__(self, *, project_id: str, client: Any | None = None) -> None:
        self._project_id = project_id
        self._client: Any | None = client

    def create_run(self, run: Mapping[str, Any]) -> None:
        """Write the run document under its public run identifier."""
        self._collection(RUNS_COLLECTION).document(str(run["run_id"])).set(dict(run))

    def assign_submittal_number(self) -> str:
        """Increment the durable submittal counter and return the value it gave.

        The read and the write are one transaction, so two runs starting at the
        same moment cannot be handed the same number: the second transaction
        sees its read invalidated and is retried against the value the first
        one wrote.

        A number is spent when a run is created, not when it completes. A run
        that fails keeps the number it was given, so the sequence has gaps
        rather than reissuing a number a reviewer may already have seen.
        """
        counter = self._collection(COUNTERS_COLLECTION).document(SUBMITTAL_NUMBER_COUNTER)

        @firestore.transactional
        def assign(transaction: firestore.Transaction) -> int:
            sequence = _counter_count(counter.get(transaction=transaction)) + 1
            transaction.set(counter, {"count": sequence})
            return sequence

        return format_submittal_number(assign(self._client_for_transactions().transaction()))

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

    def reserve_token_mint(self, *, hour: str, client_ip: str, hourly_limit: int) -> bool:
        """Reserve one submission-token mint, so a public read cannot write without bound.

        Minting on page render is what makes a token one-time and expiring, but
        it also means an unauthenticated GET writes a document. Without a cap, a
        crawler or a probe grows that collection for as long as it keeps asking.
        The cap is far above any human's reload count and turns an unbounded
        write surface into a bounded one.
        """
        return self._reserve_hourly(TOKEN_MINT_LIMITS_COLLECTION, hour, client_ip, hourly_limit)

    def reserve_gate_check(self, *, hour: str, client_ip: str, hourly_limit: int) -> bool:
        """Reserve one gate-playground check in a transaction that survives cold starts.

        The playground makes no model call and writes no run, so its only cost
        is one read of a committed fixture. The limit keeps that read from
        being used as free compute; it rations no scarce resource.
        """
        return self._reserve_hourly(GATE_CHECK_LIMITS_COLLECTION, hour, client_ip, hourly_limit)

    def _reserve_hourly(
        self, collection_name: str, hour: str, client_ip: str, hourly_limit: int
    ) -> bool:
        """Reserve one slot in an hourly per-address counter, or refuse it."""
        counter = self._collection(collection_name).document(_sample_ip_counter_id(hour, client_ip))

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

    def _collection(self, name: str) -> Any:
        return self._client_for_transactions().collection(name)

    def _client_for_transactions(self) -> Any:
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
