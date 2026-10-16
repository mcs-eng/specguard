"""Observable Firestore repository behavior against the in-process client."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta

import pytest

from specguard.web.repository import (
    GATE_CHECK_LIMITS_COLLECTION,
    RUNS_COLLECTION,
    SAMPLE_IP_LIMITS_COLLECTION,
    SAMPLE_RUN_LIMITS_COLLECTION,
    TOKEN_MINT_LIMITS_COLLECTION,
    UPLOAD_SUBMISSION_TOKENS_COLLECTION,
    FirestoreRunRepository,
    SubmissionTokenRefused,
)
from tests.fake_firestore import FakeFirestoreClient


def _repository(client: FakeFirestoreClient) -> FirestoreRunRepository:
    return FirestoreRunRepository(project_id="test-project", client=client)


def test_run_is_stored_and_read_by_its_public_identifier() -> None:
    client = FakeFirestoreClient()
    repository = _repository(client)
    run = {"run_id": "run-1", "status": "COMPLETED"}

    repository.create_run(run)

    assert repository.get_run("run-1") == run
    assert repository.get_run("missing") is None
    assert client.data[RUNS_COLLECTION] == {"run-1": run}


def test_upload_token_is_hashed_claimed_once_and_returns_its_existing_run() -> None:
    client = FakeFirestoreClient()
    repository = _repository(client)
    token = "fictional-one-time-token"
    now = datetime(2026, 10, 16, tzinfo=UTC)
    repository.mint_submission_token(token, expires_at=now + timedelta(minutes=5))

    assert repository.create_upload_run({"run_id": "run-1"}, token, now=now) is None
    assert repository.create_upload_run({"run_id": "run-2"}, token, now=now) == "run-1"

    token_records = client.data[UPLOAD_SUBMISSION_TOKENS_COLLECTION]
    assert list(token_records) == [hashlib.sha256(token.encode()).hexdigest()]
    assert token_records[next(iter(token_records))]["run_id"] == "run-1"
    assert client.data[RUNS_COLLECTION] == {"run-1": {"run_id": "run-1"}}


@pytest.mark.parametrize("expired", [False, True])
def test_upload_run_refuses_unminted_or_expired_tokens(expired: bool) -> None:
    client = FakeFirestoreClient()
    repository = _repository(client)
    now = datetime(2026, 10, 16, tzinfo=UTC)
    if expired:
        repository.mint_submission_token("expired-token", expires_at=now)
        token = "expired-token"
    else:
        token = "never-minted-token"

    with pytest.raises(SubmissionTokenRefused):
        repository.create_upload_run({"run_id": "run-1"}, token, now=now)

    assert client.data.get(RUNS_COLLECTION, {}) == {}


def test_sample_reservation_enforces_hourly_and_daily_limits_atomically() -> None:
    client = FakeFirestoreClient()
    repository = _repository(client)
    limits = {
        "day": "2026-10-16",
        "hour": "2026-10-16T10",
        "client_ip": "client-a",
        "hourly_limit": 2,
        "daily_limit": 1,
    }

    assert repository.reserve_sample_run(**limits) is True
    # A different address still meets the same exhausted daily allowance.
    assert repository.reserve_sample_run(**{**limits, "client_ip": "client-b"}) is False

    assert client.data[SAMPLE_RUN_LIMITS_COLLECTION]["2026-10-16"]["count"] == 1
    assert len(client.data[SAMPLE_IP_LIMITS_COLLECTION]) == 1
    assert "client-a" not in next(iter(client.data[SAMPLE_IP_LIMITS_COLLECTION]))


@pytest.mark.parametrize(
    ("reserve", "collection"),
    [
        ("reserve_token_mint", TOKEN_MINT_LIMITS_COLLECTION),
        ("reserve_gate_check", GATE_CHECK_LIMITS_COLLECTION),
    ],
)
def test_hourly_reservations_stop_at_the_configured_limit(reserve: str, collection: str) -> None:
    client = FakeFirestoreClient()
    repository = _repository(client)
    reserve_one = getattr(repository, reserve)
    limits = {"hour": "2026-10-16T10", "client_ip": "client-a", "hourly_limit": 1}

    assert reserve_one(**limits) is True
    assert reserve_one(**limits) is False
    assert sum(record["count"] for record in client.data[collection].values()) == 1
    assert "client-a" not in next(iter(client.data[collection]))
