"""Album One authority: commissions, bindings, transitions, mastering, references.

Track packages describe music. This package owns the authority machinery they all
share, so a new commission does not arrive with its own hand-rolled ledger edits and
its own pile of hand-written gates.

What is absent is as deliberate as what is here. There is no arrangement graph, no
performance realizer and no frontier builder, because those still carry A1-07's
assumptions and A1-02 exists to challenge them. See `docs/EXTRACTION_BOUNDARY.md`.
"""

from .acceptance import AcceptanceValidationError, build_receipt, validate_verdict
from .bindings import BindingError, SourceBinding, edition_candidate, readiness_report
from .commission import CommissionError, TrackCommission, from_ledger
from .mastering import MasteringContractError, MasteringPlan, Stage, SignalTarget,     validate_execution
from .system_reference import Challenge, SystemReferenceError, advance, prepare,     result_receipt
from .transitions import EVENTS, LedgerTransitionError, apply_transition,     plan_transition, verify

__all__ = [
    "AcceptanceValidationError", "BindingError", "Challenge", "CommissionError",
    "EVENTS", "LedgerTransitionError", "MasteringContractError", "MasteringPlan",
    "SignalTarget", "SourceBinding", "Stage", "SystemReferenceError", "TrackCommission",
    "advance", "apply_transition", "build_receipt", "edition_candidate", "from_ledger",
    "plan_transition", "prepare", "readiness_report", "result_receipt",
    "validate_execution", "validate_verdict", "verify",
]
