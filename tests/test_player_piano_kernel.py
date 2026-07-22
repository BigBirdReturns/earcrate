from __future__ import annotations

import runpy
import subprocess
import sys
from pathlib import Path

from earcrate.music.heritage import music_buffalo_harvest_manifest, music_validate_buffalo_harvest
from earcrate.music.laws import MusicLawContext, music_commit_proof, music_prove_candidate
from earcrate.music.model import (
    MusicError,
    MusicHarmonyFrame,
    MusicNoLegalContinuation,
    MusicState,
    music_make_event,
)
from earcrate.music.player_piano import (
    MusicVoicePlan,
    music_compose_player_piano,
    music_conservatory_player_piano,
    music_electro_soul_player_piano,
)


def _fsharp_frame(start: int, end: int) -> MusicHarmonyFrame:
    return MusicHarmonyFrame(
        start_step=start,
        end_step=end,
        root_pc=6,
        pitch_classes=(6, 8, 10, 1, 3),
        stable_pitch_classes=(6, 10, 1),
        bass_pitch_classes=(6, 1),
        label="F#6/9",
        function="tonic",
    )


def _b_frame(start: int, end: int) -> MusicHarmonyFrame:
    return MusicHarmonyFrame(
        start_step=start,
        end_step=end,
        root_pc=11,
        pitch_classes=(11, 1, 3, 6, 8, 10),
        stable_pitch_classes=(11, 3, 6),
        bass_pitch_classes=(11, 6),
        label="Bmaj9",
        function="subdominant-color",
    )


def _context(
    *,
    end: int = 8,
    evidence: dict[str, dict[int, float]] | None = None,
    future: dict[str, tuple[int, ...]] | None = None,
    frames: tuple[MusicHarmonyFrame, ...] | None = None,
    targets: dict[str, float] | None = None,
) -> MusicLawContext:
    return MusicLawContext(
        harmony_frames=frames or (_fsharp_frame(0, end),),
        steps_per_beat=4,
        scale_pitch_classes=(6, 8, 10, 11, 1, 3, 5),
        role_ranges={
            "default": (0, 127),
            "sub_bass": (24, 42),
            "bass": (28, 52),
            "bass_articulation": (36, 60),
            "harmony": (40, 90),
            "lead": (60, 90),
            "counterline": (55, 82),
            "percussion": (0, 127),
            "fx": (0, 127),
        },
        evidence=evidence or {},
        future_steps=future or {},
        register_targets=targets or {"lead": 72.0, "bass": 42.0},
        metadata={"fixture": "pretty_lights_first_30_seconds_harmonic_language"},
    )


def _initial(end: int = 8) -> MusicState:
    return MusicState(phrase_start_step=0, phrase_end_step=end, current_step=0)


def test_reachable_tension_creates_and_then_discharges_an_obligation() -> None:
    evidence = {
        MusicLawContext.evidence_key("lead", 0): {65: 1.0},  # E# -> F# leading tone
        MusicLawContext.evidence_key("lead", 2): {66: 1.0},
    }
    context = _context(evidence=evidence, future={"lead": (0, 2, 4, 6)})
    program = music_conservatory_player_piano(voice_order=("lead",))
    state = _initial()

    tension = music_make_event(
        voice_id="lead",
        role="lead",
        start_step=0,
        duration_steps=1,
        pitch=65,
        operator="pass",
        source_ids=("reference:cqt:lead:0",),
    )
    first = music_prove_candidate(state, tension, context, program)
    assert first.legal is True
    assert len(first.obligations_after) == 1
    assert first.obligations_after[0].kind == "leading_tone_to_tonic"
    state = music_commit_proof(state, first)

    resolution = music_make_event(
        voice_id="lead",
        role="lead",
        start_step=2,
        duration_steps=1,
        pitch=66,
        operator="cadence_repair",
        source_ids=("reference:cqt:lead:2",),
    )
    second = music_prove_candidate(state, resolution, context, program)
    assert second.legal is True
    assert second.obligations_after == ()
    assert any(row.discharged_obligation_ids for row in second.verdicts)


def test_terminal_tension_is_not_constructible() -> None:
    context = _context(
        evidence={MusicLawContext.evidence_key("lead", 7): {65: 1.0}},
        future={"lead": (7,)},
    )
    program = music_conservatory_player_piano(voice_order=("lead",))
    event = music_make_event(
        voice_id="lead",
        role="lead",
        start_step=7,
        duration_steps=1,
        pitch=65,
        operator="pass",
        source_ids=("reference:cqt:lead:7",),
    )
    proof = music_prove_candidate(_initial(), event, context, program)
    assert proof.legal is False
    assert "tension_has_no_reachable_resolution" in proof.failures or "phrase_ends_on_unstable_pitch" in proof.failures
    try:
        music_commit_proof(_initial(), proof)
    except MusicError:
        pass
    else:
        raise AssertionError("an illegal terminal tension was committed")


def test_illegal_bass_relation_and_same_voice_overlap_are_rejected() -> None:
    context = _context(
        evidence={
            MusicLawContext.evidence_key("bass", 0): {42: 1.0, 48: 1.0},
            MusicLawContext.evidence_key("bass", 2): {42: 1.0},
        },
        future={"bass": (0, 2, 4, 6)},
    )
    program = music_conservatory_player_piano(voice_order=("bass",))
    bad = music_make_event(voice_id="bass", role="bass", start_step=0, duration_steps=2, pitch=48)
    bad_proof = music_prove_candidate(_initial(), bad, context, program)
    assert bad_proof.legal is False
    assert "illegal_bass_relation" in bad_proof.failures or "pitch_has_no_admissible_harmonic_function" in bad_proof.failures

    good = music_make_event(voice_id="bass", role="bass", start_step=0, duration_steps=4, pitch=42)
    good_proof = music_prove_candidate(_initial(), good, context, program)
    assert good_proof.legal is True
    state = music_commit_proof(_initial(), good_proof)
    overlap = music_make_event(voice_id="bass", role="bass", start_step=2, duration_steps=2, pitch=42)
    overlap_proof = music_prove_candidate(state, overlap, context, program)
    assert overlap_proof.legal is False
    assert "same_voice_overlap" in overlap_proof.failures


def test_same_equations_arranged_as_different_player_pianos_produce_distinct_valid_phrases() -> None:
    evidence = {}
    for step in (0, 2, 4, 6):
        evidence[MusicLawContext.evidence_key("lead", step)] = {66: 1.0, 70: 1.0, 73: 1.0, 78: 1.0}
    context = _context(evidence=evidence, future={"lead": (0, 2, 4, 6)}, targets={"lead": 72.0})
    plan = MusicVoicePlan(
        voice_id="lead",
        role="lead",
        onset_steps=(0, 2, 4, 6),
        duration_steps=2,
        pitch_pool=(66, 70, 73, 78),
        operator_candidates=("state", "register_rupture"),
        motif_intervals=(12, -8, 3),
        source_ids=("reference:upper-profile",),
        metadata={"phrase_restart_every": 1},
    )
    conservative = music_compose_player_piano(
        initial_state=_initial(),
        context=context,
        voice_plans=(plan,),
        program=music_conservatory_player_piano(voice_order=("lead",)),
        beam_width=48,
    )
    wide = music_compose_player_piano(
        initial_state=_initial(),
        context=context,
        voice_plans=(plan,),
        program=music_electro_soul_player_piano(voice_order=("lead",)),
        beam_width=48,
    )
    conservative_pitches = [event.pitch for event in conservative.final_state.events]
    wide_pitches = [event.pitch for event in wide.final_state.events]
    assert conservative_pitches != wide_pitches
    assert conservative.final_state.obligations == wide.final_state.obligations == ()
    assert all(proof.legal for proof in conservative.proofs)
    assert all(proof.legal for proof in wide.proofs)
    assert conservative.program.objective_stages != wide.program.objective_stages


def test_player_piano_refuses_when_only_illegal_future_exists_instead_of_falling_back() -> None:
    evidence = {
        MusicLawContext.evidence_key("lead", 0): {65: 1.0, 60: 0.05},
        MusicLawContext.evidence_key("lead", 2): {60: 1.0, 65: 0.05},
    }
    context = _context(evidence=evidence, future={"lead": (0, 2)})
    plan = MusicVoicePlan(
        voice_id="lead",
        role="lead",
        onset_steps=(0, 2),
        duration_steps=1,
        pitch_pool=(65, 60),
        operator_candidates=("state", "pass"),
        source_ids=("reference:negative-control",),
    )
    try:
        music_compose_player_piano(
            initial_state=_initial(),
            context=context,
            voice_plans=(plan,),
            program=music_conservatory_player_piano(voice_order=("lead",)),
            beam_width=32,
        )
    except MusicNoLegalContinuation as exc:
        assert "no legal continuation" in str(exc) or "cannot close" in str(exc)
    else:
        raise AssertionError("player piano emitted a fallback note after search exhausted")


def _ensemble_fixture() -> tuple[MusicLawContext, tuple[MusicVoicePlan, ...], MusicState]:
    end = 32
    frames = (_fsharp_frame(0, 16), _b_frame(16, end))
    onsets = (0, 8, 16, 24)
    voice_rows = (
        ("sub", "sub_bass", (30, 35), 30.0),
        ("bass_art", "bass_articulation", (42, 47), 44.0),
        ("harmony_low", "harmony", (54, 59), 56.0),
        ("harmony_mid", "harmony", (58, 63), 61.0),
        ("harmony_high", "harmony", (61, 66), 65.0),
        ("upper", "lead", (66, 71), 70.0),
        ("counter", "counterline", (70, 75), 73.0),
        ("kick", "percussion", (36,), 36.0),
        ("snare", "percussion", (38,), 38.0),
        ("hats", "percussion", (42,), 42.0),
        ("fx", "fx", (None,), 60.0),
    )
    evidence: dict[str, dict[int, float]] = {}
    future: dict[str, tuple[int, ...]] = {}
    targets: dict[str, float] = {}
    plans = []
    for voice_id, role, pitches, target in voice_rows:
        future[voice_id] = onsets
        targets[voice_id] = target
        for step in onsets:
            frame = frames[0] if step < 16 else frames[1]
            if role == "sub_bass":
                pitch = 30 if frame.root_pc == 6 else 35
            elif role == "bass_articulation":
                pitch = 42 if frame.root_pc == 6 else 47
            elif role == "harmony":
                candidates = [p for p in pitches if p % 12 in frame.stable_pitch_classes]
                pitch = candidates[0] if candidates else next(p for p in range(40, 90) if p % 12 in frame.stable_pitch_classes and abs(p - target) < 8)
            elif role in {"lead", "counterline"}:
                candidates = [p for p in pitches if p % 12 in frame.stable_pitch_classes]
                pitch = candidates[0] if candidates else next(p for p in range(55, 90) if p % 12 in frame.stable_pitch_classes and abs(p - target) < 9)
            elif role == "fx":
                pitch = -1
            else:
                pitch = pitches[0]
            evidence[MusicLawContext.evidence_key(voice_id, step)] = {int(pitch): 1.0}
        plans.append(
            MusicVoicePlan(
                voice_id=voice_id,
                role=role,
                onset_steps=onsets,
                duration_steps=8,
                terminal_duration_steps=8,
                pitch_pool=pitches,
                velocity=104 if role == "percussion" else 82,
                operator_candidates=("state", "octave_double") if role in {"harmony", "lead", "counterline"} else ("state",),
                motif_intervals=(0, 5, 0, -5),
                source_ids=(f"reference:{voice_id}",),
            )
        )
    context = _context(end=end, evidence=evidence, future=future, frames=frames, targets=targets)
    return context, tuple(plans), _initial(end)


def test_pretty_lights_language_fixture_compiles_an_eleven_voice_proof_carrying_ensemble() -> None:
    context, plans, state = _ensemble_fixture()
    order = tuple(plan.voice_id for plan in plans)
    program = music_electro_soul_player_piano(voice_order=order)
    first = music_compose_player_piano(
        initial_state=state,
        context=context,
        voice_plans=plans,
        program=program,
        beam_width=24,
    )
    second = music_compose_player_piano(
        initial_state=state,
        context=context,
        voice_plans=plans,
        program=program,
        beam_width=24,
    )
    voices = {event.voice_id for event in first.final_state.events}
    assert len(voices) == 11
    assert len(first.final_state.events) == 44
    assert first.final_state.obligations == ()
    assert all(proof.legal and proof.event.source_ids for proof in first.proofs)
    assert first.composition_sha256 == second.composition_sha256
    assert first.to_dict()["open_obligation_count"] == 0


def test_buffalo_harvest_routes_or_explicitly_retires_every_historical_organ() -> None:
    music_validate_buffalo_harvest()
    manifest = music_buffalo_harvest_manifest()
    assert manifest["counts"]["preserve"] >= 5
    assert manifest["counts"]["adapt"] >= 4
    assert manifest["counts"]["retire"] >= 2
    assert manifest["counts"]["demote"] >= 2
    assert len(manifest["organs"]) == len({row["organ"] for row in manifest["organs"]})
    assert len(manifest["harvest_sha256"]) == 64


def test_player_piano_single_file_namespace(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parent.parent
    build = subprocess.run([sys.executable, str(root / "build" / "make_singlefile.py")], cwd=root, capture_output=True, text=True)
    assert build.returncode == 0, build.stdout + build.stderr
    namespace = runpy.run_path(str(root / "dist" / "earcrate.py"), run_name="earcrate_player_piano_singlefile_gate")
    manifest = namespace["music_buffalo_harvest_manifest"]()
    assert manifest["kind"] == "earcrate_buffalo_harvest"
    assert namespace["music_conservatory_player_piano"]().program_id == "conservatory_v1"
