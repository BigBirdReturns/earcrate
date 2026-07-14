"""earcrate.study — reference-study capability (measured persona ground truth).

Pure, deterministic functions that turn a documented Girl Talk sample dataset
(the shared schema) into engine ground truth: a measured persona fingerprint,
the set of Girl-Talk-PROVEN compatibility edges, and a reference-calibrated copy
of a hand-tuned TasteSpec profile. No I/O beyond reading a JSON file in
``load_reference``, no DB, no core state, no clock, no randomness.
"""
from earcrate.study.reference import (
    load_reference,
    reference_fingerprint,
    reference_edges,
    calibrate_profile,
)

__all__ = [
    "load_reference",
    "reference_fingerprint",
    "reference_edges",
    "calibrate_profile",
]
