"""Load and validate the a1-07-full-form-v1 descent contract."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from ..a1_07_gold_v8 import common as c

DESCENT_ID = "a1-07-full-form-v1"
TRACK_ID = "A1-07"
LAW_ORDER = (
    "full-form-v1-single-speed",
    "full-form-v1-native-pocket",
    "full-form-v1-phrase-reset",
)


class FullFormError(RuntimeError):
    pass


def contract_path(repo_root: Path) -> Path:
    return repo_root / "configs" / "album_one" / "a1-07" / "full-form-v1.v1.json"


def load_contract(path: Path) -> dict[str, Any]:
    value = c.load_json(path)
    if value.get("kind") != "earcrate_track_descent_contract":
        raise FullFormError("wrong contract kind")
    if value.get("track_id") != TRACK_ID:
        raise FullFormError("the full-form adapter is restricted to A1-07")
    if value.get("descent_id") != DESCENT_ID:
        raise FullFormError(f"wrong descent id: {value.get('descent_id')}")
    c.validate_seal(value, "contract_sha256")

    ids = tuple(str(row.get("candidate_id") or "") for row in value.get("timing_laws") or [])
    if ids != LAW_ORDER:
        raise FullFormError("timing law order does not match the full-form contract")

    form = value.get("form") or {}
    low, high = float(form.get("minimum_seconds", 0)), float(form.get("maximum_seconds", 0))
    if not (0 < low < high):
        raise FullFormError("form window is not a positive interval")
    declared = float(form.get("declared_total_seconds", 0))
    if not (low <= declared <= high):
        raise FullFormError(
            f"declared form duration {declared} is outside the {low}-{high} s window")

    sections = {str(row.get("section_id")) for row in form.get("sections") or []}
    if sections != {"setup", "body", "payoff"}:
        raise FullFormError("the full form must declare exactly setup, body and payoff")

    # A frontier that varies more than one mechanism cannot discriminate. The body is
    # authored once and reused, so the ONLY audible difference is the band timing law.
    phrase_map = value.get("phrase_map") or {}
    if not phrase_map.get("vocal_phrases"):
        raise FullFormError("the contract carries no explicit vocal phrase map")
    for phrase in phrase_map["vocal_phrases"]:
        for key in ("phrase_id", "section_id", "source_id", "source_window_seconds",
                    "destination_anchor_seconds", "timing_law", "reset_behaviour",
                    "gain_db", "transition_treatment"):
            if key not in phrase:
                raise FullFormError(
                    f"phrase {phrase.get('phrase_id')!r} is missing required field {key!r}")
    if not (phrase_map.get("vocal_invariants") or {}).get("frankie_time_stretch_forbidden"):
        raise FullFormError("Frankie time stretch must remain forbidden")

    gate = value.get("machine_gate") or {}
    bounds = gate.get("band_tempo_scale_bounds")
    if not (isinstance(bounds, list) and len(bounds) == 2 and 0 < bounds[0] < 1 < bounds[1]):
        raise FullFormError("machine gate must bound band tempo scale around 1.0")
    return value


def law(contract: Mapping[str, Any], candidate_id: str) -> dict[str, Any]:
    for row in contract.get("timing_laws") or []:
        if str(row.get("candidate_id")) == candidate_id:
            return dict(row)
    raise FullFormError(f"unknown timing law: {candidate_id}")


def section(contract: Mapping[str, Any], section_id: str) -> dict[str, Any]:
    for row in (contract.get("form") or {}).get("sections") or []:
        if str(row.get("section_id")) == section_id:
            return dict(row)
    raise FullFormError(f"unknown form section: {section_id}")
