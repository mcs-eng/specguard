"""Human-facing submittal and RFI numbers for one audit run.

The hex run identifier stays the backend identity: it keys the Firestore run
document, the durable storage objects, and every URL. It is not what a
reviewer reads. What a reviewer reads is a short sequence number assigned once,
at run creation, from a counter incremented in a Firestore transaction:
``S-001``, ``S-002``, and so on.

One RFI exists per run, so the RFI reuses that run's sequence value rather than
drawing from a second counter. Submittal ``S-001`` carries ``RFI-001``. A
second counter would let the two drift, and there is nothing for them to
disagree about.

A run persisted before the field existed has no number to render. It falls back
to the short run identifier, plainly, rather than to an invented sequence
value: a made-up ``S-`` number would be indistinguishable from an assigned one.
"""

from __future__ import annotations

#: Prefix on the identifier a reviewer reads for the submittal under audit.
SUBMITTAL_PREFIX = "S-"

#: Prefix on the identifier a reviewer reads for the RFI drafted from it.
RFI_PREFIX = "RFI-"

#: Minimum width of the sequence value, zero-padded. A sequence past this width
#: is written in full. Padding is cosmetic; a number is never truncated to fit.
SEQUENCE_DIGITS = 3

#: How many characters of the hex run identifier the fallback renders. The same
#: length the recent-runs list already shortens a run identifier to.
SHORT_RUN_ID_LENGTH = 8


def format_submittal_number(sequence: int) -> str:
    """Render one assigned counter value as the submittal number.

    Raise for a sequence below one. A counter that answered zero or a negative
    value has not assigned anything, and rendering ``S-000`` would hide that.
    """
    if sequence < 1:
        raise ValueError("a submittal sequence value starts at 1")
    return f"{SUBMITTAL_PREFIX}{sequence:0{SEQUENCE_DIGITS}d}"


def rfi_number_for(submittal_number: str) -> str:
    """Return the RFI number that pairs with one submittal number.

    The sequence value is carried across unchanged, so ``S-001`` yields
    ``RFI-001``. A value that does not carry the submittal prefix is paired on
    its own text, so a caller cannot silently produce a differently numbered
    pair by passing something else.
    """
    return f"{RFI_PREFIX}{submittal_number.removeprefix(SUBMITTAL_PREFIX)}"


def short_run_id(run_id: str) -> str:
    """Return the shortened hex run identifier used as the legacy fallback."""
    return run_id[:SHORT_RUN_ID_LENGTH]


def submittal_label(submittal_number: str | None, run_id: str) -> str:
    """Return what a reader is shown for this run's identity.

    An assigned number is shown as it was assigned. A run that predates the
    field shows its short run identifier instead.
    """
    return submittal_number or short_run_id(run_id)


def rfi_label(submittal_number: str | None, run_id: str) -> str:
    """Return what a reader is shown for this run's RFI identity."""
    if submittal_number:
        return rfi_number_for(submittal_number)
    return short_run_id(run_id)
