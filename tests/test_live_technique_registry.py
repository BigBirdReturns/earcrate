from __future__ import annotations

from earcrate.live.model import live_persona_names, live_persona_policy
from earcrate.live.operators import (
    LIVE_TECHNIQUE_NAMES,
    live_apply_technique,
    live_pattern_layers,
)


def _pattern(pattern_id: str, source_bar: int, slots: list[tuple[str, str, str, str]]) -> dict:
    return {
        "pattern_id": pattern_id,
        "source_bar_index": source_bar,
        "source_energy": 0.72,
        "slots": [
            {
                "slot_id": slot_id,
                "track_name": track_name,
                "role": role,
                "category": category,
                "mode": "trigger" if category == "floor" else "pitched",
                "program": 0,
            }
            for slot_id, track_name, role, category in slots
        ],
    }


def test_every_live_technique_has_a_compatible_explicit_lowering() -> None:
    active_pattern = _pattern(
        "pattern-active",
        0,
        [
            ("active-floor", "Active Drums", "drums", "floor"),
            ("active-bass", "Active Bass", "bass", "bass"),
        ],
    )
    candidate = _pattern(
        "pattern-candidate",
        1,
        [
            ("candidate-floor", "Candidate Drums", "drums", "floor"),
            ("candidate-bass", "Candidate Bass", "bass", "bass"),
            ("candidate-pad", "Candidate Pad", "pad", "harmony"),
            ("candidate-lead", "Candidate Lead", "lead", "foreground"),
            ("candidate-fx", "Candidate FX", "sound_fx", "fx"),
        ],
    )
    active_layers = live_pattern_layers(active_pattern)
    seen_command_ids = set()
    for technique in LIVE_TECHNIQUE_NAMES:
        result = live_apply_technique(
            technique,
            active_layers=active_layers,
            candidate_pattern=candidate,
            maximum_layers=6,
            category_priority=["floor", "bass", "harmony", "foreground", "fx"],
        )
        assert result["compatible"] is True, (technique, result["failures"])
        assert result["layers"], technique
        assert result["commands"], technique
        assert len(result["layers"]) <= 6
        for command in result["commands"]:
            assert command["kind"] and command["technique"] == technique
            assert command["command_id"] not in seen_command_ids
            seen_command_ids.add(command["command_id"])


def test_operator_preconditions_refuse_instead_of_falling_back() -> None:
    candidate = _pattern(
        "pattern-empty-context",
        2,
        [("candidate-pad", "Candidate Pad", "pad", "harmony")],
    )
    loop = live_apply_technique(
        "loop_extend",
        active_layers=[],
        candidate_pattern=candidate,
        maximum_layers=4,
        category_priority=["floor", "bass", "harmony", "foreground", "fx"],
    )
    swap = live_apply_technique(
        "foreground_swap",
        active_layers=[],
        candidate_pattern=candidate,
        maximum_layers=4,
        category_priority=["floor", "bass", "harmony", "foreground", "fx"],
    )
    assert loop["compatible"] is False and "no_active_layers_to_loop" in loop["failures"]
    assert swap["compatible"] is False
    assert "candidate_has_no_foreground" in swap["failures"]
    assert "foreground_swap_requires_active_base" in swap["failures"]


def test_every_persona_assigns_a_weight_to_every_technique() -> None:
    for name in live_persona_names():
        policy = live_persona_policy(name)
        assert set(policy["technique_weights"]) == set(LIVE_TECHNIQUE_NAMES)
        assert all(float(policy["technique_weights"][technique]) > 0.0 for technique in LIVE_TECHNIQUE_NAMES)
