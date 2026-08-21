"""Adversarial invariant tests added by the Phase 3 independent review.

Each test exercises one potential bypass of the ledger invariant: no code
path may write to the findings collection without a passing
``specguard.gate.verify_quote`` result obtained at write time. A test here
passes only when the control blocks the bypass. A control that is absent is
pinned with ``xfail(strict=True)`` and documented in ``REVIEW-P3.md``.
"""
