"""Album One authority: commissions, bindings, transitions.

Track packages describe music. This package owns the authority machinery they all
share, so a new commission does not arrive with its own hand-rolled ledger edits and
its own pile of hand-written gates.
"""

from .transitions import EVENTS, LedgerTransitionError, apply_transition, \
    plan_transition, verify

__all__ = ["EVENTS", "LedgerTransitionError", "apply_transition", "plan_transition",
           "verify"]
