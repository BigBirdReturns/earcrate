"""Deterministic reference study and local reference-evidence compilation."""

from earcrate.study.reference import (
    calibrate_profile,
    load_reference,
    reference_edges,
    reference_fingerprint,
)
from earcrate.study.reference_bundle import (
    ReferenceBundleError,
    reference_compile_bundle,
    reference_validate_bundle,
    reference_write_bundle,
)
from earcrate.study.reference_grid import (
    reference_accept_grid,
    reference_propose_drum_observation_from_audio,
    reference_propose_grid_from_audio,
    reference_validate_drum_observation,
    reference_validate_grid,
    reference_validate_note_observation,
)

__all__ = [
    "ReferenceBundleError",
    "calibrate_profile",
    "load_reference",
    "reference_accept_grid",
    "reference_compile_bundle",
    "reference_edges",
    "reference_fingerprint",
    "reference_propose_drum_observation_from_audio",
    "reference_propose_grid_from_audio",
    "reference_validate_bundle",
    "reference_validate_drum_observation",
    "reference_validate_grid",
    "reference_validate_note_observation",
    "reference_write_bundle",
]
