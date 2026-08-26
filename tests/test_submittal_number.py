"""The human-facing submittal sequence, its pairing, and its fallback."""

from __future__ import annotations

import pytest

from specguard.submittal import (
    format_submittal_number,
    rfi_label,
    rfi_number_for,
    short_run_id,
    submittal_label,
)
from specguard.web.repository import (
    COUNTERS_COLLECTION,
    SUBMITTAL_NUMBER_COUNTER,
    FirestoreRunRepository,
)
from tests.fake_firestore import FakeDocumentReference, FakeFirestoreClient

RUN_ID = "9f3c21ab7e5d4c108b6a2f4e7c1d8093"


def _repository(client: FakeFirestoreClient) -> FirestoreRunRepository:
    """Build the production repository over an in-process Firestore double."""
    return FirestoreRunRepository(project_id="test-project", client=client)


@pytest.mark.parametrize(
    ("sequence", "expected"),
    [(1, "S-001"), (9, "S-009"), (42, "S-042"), (999, "S-999"), (1000, "S-1000")],
)
def test_a_sequence_value_is_padded_but_never_truncated(sequence: int, expected: str) -> None:
    """Padding is cosmetic. A number past the pad width is written in full."""
    assert format_submittal_number(sequence) == expected


@pytest.mark.parametrize("sequence", [0, -1])
def test_a_sequence_value_below_one_is_refused(sequence: int) -> None:
    """A counter that answered zero assigned nothing; ``S-000`` would hide that."""
    with pytest.raises(ValueError, match="starts at 1"):
        format_submittal_number(sequence)


def test_the_rfi_number_reuses_the_submittal_sequence_value() -> None:
    """One RFI per run, so one sequence. ``S-001`` carries ``RFI-001``."""
    assert rfi_number_for("S-001") == "RFI-001"
    assert rfi_number_for("S-042") == "RFI-042"
    assert rfi_number_for(format_submittal_number(7)) == "RFI-007"


def test_a_run_with_no_assigned_number_falls_back_to_the_short_run_id() -> None:
    """No invented ``S-`` number: an unnumbered run says so by showing its id."""
    assert submittal_label(None, RUN_ID) == "9f3c21ab"
    assert rfi_label(None, RUN_ID) == "9f3c21ab"
    assert short_run_id(RUN_ID) == "9f3c21ab"
    assert submittal_label("S-003", RUN_ID) == "S-003"
    assert rfi_label("S-003", RUN_ID) == "RFI-003"


def test_the_counter_assigns_sequential_numbers() -> None:
    client = FakeFirestoreClient()
    repository = _repository(client)

    assigned = [repository.assign_submittal_number() for _ in range(4)]

    assert assigned == ["S-001", "S-002", "S-003", "S-004"]
    assert client.data[COUNTERS_COLLECTION][SUBMITTAL_NUMBER_COUNTER] == {"count": 4}


def test_one_counter_serves_both_numbers_and_nothing_else_is_written() -> None:
    """No second counter exists to drift from this one."""
    client = FakeFirestoreClient()

    submittal_number = _repository(client).assign_submittal_number()

    assert list(client.data) == [COUNTERS_COLLECTION]
    assert list(client.data[COUNTERS_COLLECTION]) == [SUBMITTAL_NUMBER_COUNTER]
    assert rfi_number_for(submittal_number) == "RFI-001"


def test_two_assignments_racing_each_other_never_share_a_number(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A competing write between the read and the commit costs a retry, not a number.

    The read-then-write is one transaction, so the assignment that lost the
    race has its commit refused and runs again against the value the winner
    wrote. Without the transaction both callers would read the same count and
    two runs would carry the same submittal number.
    """
    client = FakeFirestoreClient()
    repository = _repository(client)
    original_get = FakeDocumentReference.get
    raced: list[bool] = []

    def get_then_race(self: FakeDocumentReference, transaction: object | None = None) -> object:
        snapshot = original_get(self, transaction)  # type: ignore[arg-type]
        if transaction is not None and not raced:
            raced.append(True)
            # A second instance assigns 41 while this transaction is open.
            client._write(COUNTERS_COLLECTION, SUBMITTAL_NUMBER_COUNTER, {"count": 41}, merge=False)
        return snapshot

    monkeypatch.setattr(FakeDocumentReference, "get", get_then_race)

    assigned = repository.assign_submittal_number()

    assert raced == [True]
    assert assigned == "S-042"
    assert client.data[COUNTERS_COLLECTION][SUBMITTAL_NUMBER_COUNTER] == {"count": 42}
