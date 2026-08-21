"""Adversarial invariant tests added by the Phase 3 independent review.

Each test exercises one potential bypass of the ledger invariant: no code
path may write to the findings collection without a passing
``specguard.gate.verify_quote`` result obtained at write time. A test here
passes only when the control blocks the bypass. Every control this suite
pins is present, so the suite carries no expected failure; ``REVIEW-P3.md``
records the one finding that was pinned with a strict xfail until its
control landed.
"""
