from __future__ import annotations

"""Proof-carrying adjacent moves for the ``children_v1`` specimen.

The score answer key is evidence, not a phrase to copy. This compiler derives a
bounded harmonic/melodic neighborhood from that evidence, asks the existing player-
piano constitution to choose an eight-bar continuation, proves that every committed
note is legal, lowers the result to exact MIDI, and retains an intentionally illegal
terminal-tension negative control. It never consults the commercial recording or the
private library.
"""

from copy import deepcopy
from pathlib import Path
from typing import Any, Mapping, Sequence

from earcrate.midi.codec import midi_write
from earcrate.midi.model import midi_seal_ledger
from earcrate.midi.render import midi_compile_note_spans, midi_render_ledger
from earcrate.music.law_context import MusicLawContext
from earcrate.music.laws import music_commit_proof, music_prove_candidate
from earcrate.music.model import (
    MusicError,
    MusicHarmonyFrame,
    MusicState,
    music_make_event,
    music_pc,
)
from earcrate.music.player_piano import (
    MusicVoicePlan,
    music_compose_player_piano,
    music_electro_soul_player_piano,
)

from .children import CHILDREN_SPECIMEN_ID
from .model import (
    SpecimenError,
    specimen_read_json,
    specimen_seal_score_answer_key,
    specimen_sha256_json,
    specimen_validate_score_answer_key,
    specimen_write_json_atomic,
)

CHILDREN_CONTINUATION_SCHEMA_VERSION = 1
CHILDREN_CONTINUATION_KIND = "earcrate_children_adjacent_move_receipt"
CHILDREN_CONTINUATION_BARS = 8
CHILDREN_CONTINUATION_MIDI_PPQ = 480


def _answer_key(value: str | Path | Mapping[str, Any]) -> dict[str, Any]:
    raw = specimen_read_json(value) if not isinstance(value, Mapping) else deepcopy(dict(value))
    specimen_validate_score_answer_key(raw)
    sealed = specimen_seal_score_answer_key(raw)
    if str(sealed.get("specimen_id") or "") != CHILDREN_SPECIMEN_ID:
        raise SpecimenError("Children adjacent-move compiler received another specimen")
    return sealed


def _frame_from_dict(raw: Mapping[str, Any], *, start_step: int, end_step: int) -> MusicHarmonyFrame:
    return MusicHarmonyFrame(
        start_step=int(start_step),
        end_step=int(end_step),
        root_pc=int(raw["root_pc"]),
        pitch_classes=tuple(int(value) for value in raw["pitch_classes"]),
        stable_pitch_classes=tuple(int(value) for value in raw["stable_pitch_classes"]),
        bass_pitch_classes=tuple(int(value) for value in raw["bass_pitch_classes"]),
        label=str(raw.get("label") or ""),
        function=str(raw.get("function") or ""),
    )


def _source_end_step(answer: Mapping[str, Any]) -> int:
    harmony_end = max(int(row["end_step"]) for row in answer["harmony_frames"])
    event_end = max(
        (int(row["start_step"]) + int(row["duration_steps"]) for row in answer["events"]),
        default=0,
    )
    return max(harmony_end, event_end)


def _voice_events(answer: Mapping[str, Any], *, side: str) -> list[dict[str, Any]]:
    wanted = "right" if side == "right" else "left"
    rows = [
        deepcopy(dict(row))
        for row in answer["events"]
        if wanted in str(row.get("voice_id") or "").lower()
        or (wanted == "right" and str(row.get("role") or "") in {"lead", "melody", "melody_harmony"})
        or (wanted == "left" and str(row.get("role") or "") in {"bass", "sub_bass", "bass_harmony"})
    ]
    if not rows:
        rows = [deepcopy(dict(row)) for row in answer["events"] if row.get("pitch") is not None]
    rows.sort(key=lambda row: (int(row["start_step"]), str(row["event_id"])))
    if not rows:
        raise SpecimenError("Children answer key contains no pitched source events")
    return rows


def _distinct_tail_frames(answer: Mapping[str, Any], count: int = 4) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()
    for raw in reversed(list(answer["harmony_frames"])):
        key = (
            str(raw.get("label") or ""),
            int(raw["root_pc"]),
            tuple(int(value) for value in raw["pitch_classes"]),
        )
        if key in seen:
            continue
        seen.add(key)
        selected.append(deepcopy(dict(raw)))
        if len(selected) >= count:
            break
    selected.reverse()
    if not selected:
        raise SpecimenError("Children answer key contains no harmony frames")
    while len(selected) < count:
        selected.insert(0, deepcopy(selected[0]))
    return selected[-count:]


def _continuation_frames(
    answer: Mapping[str, Any],
    *,
    start_step: int,
    bars: int,
) -> tuple[MusicHarmonyFrame, ...]:
    source = _distinct_tail_frames(answer, count=min(4, max(1, bars)))
    steps_per_bar = int(answer["steps_per_beat"]) * int((answer.get("meter") or {}).get("numerator") or 4)
    rows: list[MusicHarmonyFrame] = []
    for bar in range(int(bars)):
        raw = source[min(len(source) - 1, bar * len(source) // max(1, int(bars)))]
        a = int(start_step) + bar * steps_per_bar
        b = a + steps_per_bar
        rows.append(_frame_from_dict(raw, start_step=a, end_step=b))
    # Adjacent bar frames with identical harmony are merged so the canonical context
    # retains the actual harmonic changes rather than an arbitrary bar raster.
    merged: list[MusicHarmonyFrame] = []
    for frame in rows:
        if (
            merged
            and merged[-1].label == frame.label
            and merged[-1].root_pc == frame.root_pc
            and merged[-1].pitch_classes == frame.pitch_classes
            and merged[-1].end_step == frame.start_step
        ):
            previous = merged[-1]
            merged[-1] = MusicHarmonyFrame(
                start_step=previous.start_step,
                end_step=frame.end_step,
                root_pc=previous.root_pc,
                pitch_classes=previous.pitch_classes,
                stable_pitch_classes=previous.stable_pitch_classes,
                bass_pitch_classes=previous.bass_pitch_classes,
                label=previous.label,
                function=previous.function,
            )
        else:
            merged.append(frame)
    return tuple(merged)


def _nearest_pitch(target: int, pitch_classes: Sequence[int], low: int, high: int) -> int:
    pcs = {music_pc(value) for value in pitch_classes}
    candidates = [pitch for pitch in range(int(low), int(high) + 1) if music_pc(pitch) in pcs]
    if not candidates:
        raise SpecimenError("no pitch in the role range satisfies the continuation harmony")
    return min(candidates, key=lambda pitch: (abs(int(pitch) - int(target)), int(pitch)))


def _frame_at(frames: Sequence[MusicHarmonyFrame], step: int) -> MusicHarmonyFrame:
    frame = next((row for row in frames if row.contains(int(step))), None)
    if frame is None:
        raise SpecimenError(f"continuation has no harmony frame at step {step}")
    return frame


def _natural_minor(tonic_pc: int) -> tuple[int, ...]:
    return tuple(sorted({music_pc(int(tonic_pc) + interval) for interval in (0, 2, 3, 5, 7, 8, 10)}))


def _candidate_material(
    answer: Mapping[str, Any],
    frames: Sequence[MusicHarmonyFrame],
    *,
    start_step: int,
    end_step: int,
) -> dict[str, Any]:
    steps_per_beat = int(answer["steps_per_beat"])
    beats_per_bar = int((answer.get("meter") or {}).get("numerator") or 4)
    steps_per_bar = steps_per_beat * beats_per_bar
    right = _voice_events(answer, side="right")
    left = _voice_events(answer, side="left")
    right_tail = right[-min(12, len(right)):]
    left_tail = left[-min(8, len(left)):]
    right_pitches = [int(row["pitch"]) for row in right_tail if row.get("pitch") is not None]
    left_pitches = [int(row["pitch"]) for row in left_tail if row.get("pitch") is not None]
    if not right_pitches or not left_pitches:
        raise SpecimenError("Children continuation requires pitched evidence in both score hands")

    lead_voice = "children_adjacent_right_hand"
    bass_voice = "children_adjacent_left_hand"
    lead_onsets = tuple(range(int(start_step), int(end_step), steps_per_beat * 2))
    bass_onsets = tuple(range(int(start_step), int(end_step), steps_per_bar))
    evidence: dict[str, dict[int, float]] = {}
    lead_pool: set[int] = set()
    bass_pool: set[int] = set()
    lead_primary: list[int] = []
    bass_primary: list[int] = []

    # Rotate rather than repeat the source tail. Each target is then projected onto
    # the current frame's stable pitch classes, making the transformation explicit.
    for index, step in enumerate(lead_onsets):
        source_pitch = right_pitches[(index + 1) % len(right_pitches)]
        frame = _frame_at(frames, step)
        primary = _nearest_pitch(source_pitch, frame.stable_pitch_classes, 55, 96)
        alternatives = sorted(
            {
                primary,
                _nearest_pitch(primary - 3, frame.stable_pitch_classes, 55, 96),
                _nearest_pitch(primary + 4, frame.stable_pitch_classes, 55, 96),
            }
        )
        lead_primary.append(primary)
        lead_pool.update(alternatives)
        evidence[MusicLawContext.evidence_key(lead_voice, step)] = {
            pitch: (1.0 if pitch == primary else 0.55) for pitch in alternatives
        }

    for index, step in enumerate(bass_onsets):
        source_pitch = left_pitches[(index + 1) % len(left_pitches)]
        frame = _frame_at(frames, step)
        primary = _nearest_pitch(source_pitch, frame.bass_pitch_classes, 28, 60)
        alternatives = sorted(
            {
                primary,
                _nearest_pitch(primary - 7, frame.bass_pitch_classes, 28, 60),
                _nearest_pitch(primary + 7, frame.bass_pitch_classes, 28, 60),
            }
        )
        bass_primary.append(primary)
        bass_pool.update(alternatives)
        evidence[MusicLawContext.evidence_key(bass_voice, step)] = {
            pitch: (1.0 if pitch == primary else 0.50) for pitch in alternatives
        }

    final_frame = _frame_at(frames, int(end_step) - 1)
    illegal_pc = music_pc(final_frame.root_pc - 1)
    illegal_pitch = _nearest_pitch(lead_primary[-1], (illegal_pc,), 55, 96)
    evidence[MusicLawContext.evidence_key(lead_voice, int(end_step) - 1)] = {illegal_pitch: 1.0}

    source_event_ids = tuple(str(row["event_id"]) for row in [*right_tail, *left_tail])
    motif_intervals = tuple(
        int(right_pitches[index + 1]) - int(right_pitches[index])
        for index in range(min(len(right_pitches) - 1, 8))
    )
    return {
        "lead_voice": lead_voice,
        "bass_voice": bass_voice,
        "lead_onsets": lead_onsets,
        "bass_onsets": bass_onsets,
        "lead_pool": tuple(sorted(lead_pool)),
        "bass_pool": tuple(sorted(bass_pool)),
        "lead_primary": tuple(lead_primary),
        "bass_primary": tuple(bass_primary),
        "illegal_pitch": illegal_pitch,
        "evidence": evidence,
        "source_event_ids": source_event_ids,
        "right_source_event_ids": tuple(str(row["event_id"]) for row in right_tail),
        "left_source_event_ids": tuple(str(row["event_id"]) for row in left_tail),
        "motif_intervals": motif_intervals,
        "lead_register_target": sum(right_pitches) / len(right_pitches),
        "bass_register_target": sum(left_pitches) / len(left_pitches),
    }


def _event_signature(events: Sequence[Mapping[str, Any]], *, origin: int) -> tuple[tuple[Any, ...], ...]:
    by_voice: dict[str, list[Mapping[str, Any]]] = {}
    for event in events:
        if event.get("pitch") is None:
            continue
        role = str(event.get("role") or "")
        voice = "bass" if "bass" in role or "left" in str(event.get("voice_id") or "") else "lead"
        by_voice.setdefault(voice, []).append(event)
    rows: list[tuple[Any, ...]] = []
    for voice, voice_events in sorted(by_voice.items()):
        ordered = sorted(voice_events, key=lambda row: (int(row["start_step"]), int(row["pitch"])))
        first_pitch = int(ordered[0]["pitch"])
        for row in ordered:
            rows.append(
                (
                    voice,
                    int(row["start_step"]) - int(origin),
                    int(row["duration_steps"]),
                    int(row["pitch"]) - first_pitch,
                )
            )
    return tuple(rows)


def _literal_copy_check(
    answer: Mapping[str, Any],
    continuation_events: Sequence[Mapping[str, Any]],
    *,
    start_step: int,
    end_step: int,
) -> dict[str, Any]:
    length = int(end_step) - int(start_step)
    continuation_signature = _event_signature(continuation_events, origin=int(start_step))
    source_events = list(answer["events"])
    source_end = _source_end_step(answer)
    matches: list[int] = []
    step = int(answer["steps_per_beat"]) * int((answer.get("meter") or {}).get("numerator") or 4)
    for origin in range(0, max(0, source_end - length) + 1, max(1, step)):
        window = [
            row
            for row in source_events
            if int(origin) <= int(row["start_step"]) < int(origin) + length
        ]
        if len(window) != len(continuation_events):
            continue
        if _event_signature(window, origin=origin) == continuation_signature:
            matches.append(origin)
    return {
        "literal_copy_detected": bool(matches),
        "matching_source_window_starts": matches,
        "continuation_signature_sha256": specimen_sha256_json(continuation_signature),
        "searched_source_window_count": max(0, (max(0, source_end - length) // max(1, step)) + 1),
        "comparison": "voice-relative onset, duration, and interval signature over equal-length bar-aligned windows",
    }


def _midi_track(name: str, rows: Sequence[tuple[int, int, bool, Mapping[str, Any]]]) -> dict[str, Any]:
    ordered = sorted(rows, key=lambda row: (int(row[0]), int(row[1]), specimen_sha256_json(row[3])))
    events = [
        {
            "tick": int(tick),
            "order": index,
            "is_meta": bool(is_meta),
            "message": deepcopy(dict(message)),
        }
        for index, (tick, _priority, is_meta, message) in enumerate(ordered)
    ]
    return {"track_index": 0, "name": str(name), "events": events}


def _continuation_midi(
    events: Sequence[Mapping[str, Any]],
    *,
    start_step: int,
    end_step: int,
    steps_per_beat: int,
    tempo_bpm: float,
    meter: Mapping[str, Any],
) -> dict[str, Any]:
    ppq = CHILDREN_CONTINUATION_MIDI_PPQ
    if ppq % int(steps_per_beat):
        raise SpecimenError("continuation MIDI PPQ must divide evenly by steps_per_beat")
    ticks_per_step = ppq // int(steps_per_beat)
    total_ticks = (int(end_step) - int(start_step)) * ticks_per_step
    numerator = int(meter.get("numerator") or 4)
    denominator = int(meter.get("denominator") or 4)
    conductor_rows: list[tuple[int, int, bool, Mapping[str, Any]]] = [
        (0, 0, True, {"type": "track_name", "name": "Children Adjacent Move Conductor"}),
        (0, 1, True, {"type": "set_tempo", "tempo": max(1, int(round(60_000_000.0 / float(tempo_bpm))))}),
        (0, 2, True, {"type": "time_signature", "numerator": numerator, "denominator": denominator, "clocks_per_click": 24, "notated_32nd_notes_per_beat": 8}),
        (total_ticks, 9, True, {"type": "end_of_track"}),
    ]
    tracks: dict[str, list[tuple[int, int, bool, Mapping[str, Any]]]] = {
        "lead": [(0, 0, True, {"type": "track_name", "name": "Children Adjacent Right Hand"}), (0, 1, False, {"type": "program_change", "channel": 0, "program": 80})],
        "bass": [(0, 0, True, {"type": "track_name", "name": "Children Adjacent Left Hand"}), (0, 1, False, {"type": "program_change", "channel": 1, "program": 33})],
    }
    for event in events:
        if event.get("pitch") is None:
            continue
        role = str(event.get("role") or "")
        lane = "bass" if role == "bass" else "lead"
        channel = 1 if lane == "bass" else 0
        start = (int(event["start_step"]) - int(start_step)) * ticks_per_step
        stop = start + int(event["duration_steps"]) * ticks_per_step
        note = int(event["pitch"])
        velocity = int(event.get("velocity") or 96)
        tracks[lane].append((start, 4, False, {"type": "note_on", "channel": channel, "note": note, "velocity": velocity}))
        tracks[lane].append((stop, 3, False, {"type": "note_off", "channel": channel, "note": note, "velocity": 0}))
    tracks["lead"].append((total_ticks, 9, True, {"type": "end_of_track"}))
    tracks["bass"].append((total_ticks, 9, True, {"type": "end_of_track"}))
    raw_tracks = [
        _midi_track("Children Adjacent Move Conductor", conductor_rows),
        _midi_track("Children Adjacent Right Hand", tracks["lead"]),
        _midi_track("Children Adjacent Left Hand", tracks["bass"]),
    ]
    for index, track in enumerate(raw_tracks):
        track["track_index"] = index
    ledger = midi_seal_ledger(
        {
            "schema_version": 1,
            "kind": "earcrate_midi_ledger",
            "midi_type": 1,
            "ticks_per_beat": ppq,
            "tracks": raw_tracks,
            "metadata": {
                "specimen_id": CHILDREN_SPECIMEN_ID,
                "continuation_source_start_step": int(start_step),
            },
        }
    )
    compiled = midi_compile_note_spans(ledger)
    expected = sorted(
        (
            "bass" if str(row.get("role") or "") == "bass" else "lead",
            int(row["start_step"]) - int(start_step),
            int(row["duration_steps"]),
            int(row["pitch"]),
        )
        for row in events
        if row.get("pitch") is not None
    )
    actual = sorted(
        (
            "bass" if int(row["track_index"]) == 2 else "lead",
            int(round(int(row["start_tick"]) / ticks_per_step)),
            int(round((int(row["end_tick"]) - int(row["start_tick"])) / ticks_per_step)),
            int(row["note"]),
        )
        for row in compiled["note_spans"]
    )
    if actual != expected:
        raise SpecimenError("continuation MIDI lowering does not preserve committed music events")
    return ledger


def children_compose_adjacent_move(
    answer_key: str | Path | Mapping[str, Any],
    output_dir: str | Path,
    *,
    bars: int = CHILDREN_CONTINUATION_BARS,
    sample_rate: int = 8_000,
    overwrite: bool = False,
) -> dict[str, Any]:
    """Compose, prove, lower, and render one bounded Children-adjacent move."""
    if int(bars) <= 0 or int(bars) > 32:
        raise SpecimenError("Children continuation bars must be in 1..32")
    if int(sample_rate) < 8_000:
        raise SpecimenError("Children continuation sample_rate must be at least 8000")
    answer = _answer_key(answer_key)
    destination = Path(output_dir).expanduser().resolve()
    if destination.exists() and any(destination.iterdir()) and not overwrite:
        raise FileExistsError(f"refusing to overwrite nonempty continuation output: {destination}")
    destination.mkdir(parents=True, exist_ok=True)

    steps_per_beat = int(answer["steps_per_beat"])
    beats_per_bar = int((answer.get("meter") or {}).get("numerator") or 4)
    steps_per_bar = steps_per_beat * beats_per_bar
    start_step = _source_end_step(answer)
    end_step = start_step + int(bars) * steps_per_bar
    frames = _continuation_frames(answer, start_step=start_step, bars=int(bars))
    material = _candidate_material(answer, frames, start_step=start_step, end_step=end_step)
    key = dict(answer.get("key_signature") or {})
    tonic_pc = int(key.get("tonic_pc", frames[-1].root_pc))
    scale = set(_natural_minor(tonic_pc))
    for frame in frames:
        scale.update(frame.pitch_classes)
    context = MusicLawContext(
        harmony_frames=tuple(frames),
        steps_per_beat=steps_per_beat,
        scale_pitch_classes=tuple(sorted(scale)),
        role_ranges={"default": (0, 127), "lead": (55, 96), "bass": (28, 60)},
        evidence=material["evidence"],
        future_steps={
            material["lead_voice"]: tuple(material["lead_onsets"]),
            material["bass_voice"]: tuple(material["bass_onsets"]),
        },
        register_targets={
            material["lead_voice"]: float(material["lead_register_target"]),
            material["bass_voice"]: float(material["bass_register_target"]),
            "lead": float(material["lead_register_target"]),
            "bass": float(material["bass_register_target"]),
        },
        metadata={
            "specimen_id": CHILDREN_SPECIMEN_ID,
            "answer_key_sha256": answer["answer_key_sha256"],
            "adjacent_move": True,
        },
    )
    program = music_electro_soul_player_piano(
        voice_order=(material["bass_voice"], material["lead_voice"])
    )
    plans = (
        MusicVoicePlan(
            voice_id=material["bass_voice"],
            role="bass",
            onset_steps=tuple(material["bass_onsets"]),
            duration_steps=steps_per_bar,
            terminal_duration_steps=steps_per_bar,
            pitch_pool=tuple(material["bass_pool"]),
            velocity=86,
            function="harmonic_support",
            operator_candidates=("state", "pass"),
            motif_intervals=(),
            source_ids=tuple(material["left_source_event_ids"]),
            metadata={"transformation": "rotated source register projected to structural bass tones"},
        ),
        MusicVoicePlan(
            voice_id=material["lead_voice"],
            role="lead",
            onset_steps=tuple(material["lead_onsets"]),
            duration_steps=steps_per_beat * 2,
            terminal_duration_steps=steps_per_beat * 2,
            pitch_pool=tuple(material["lead_pool"]),
            velocity=94,
            function="motif_continuation",
            operator_candidates=("state", "pass", "register_rupture"),
            motif_intervals=tuple(material["motif_intervals"]),
            source_ids=tuple(material["right_source_event_ids"]),
            metadata={
                "transformation": "one-position motif rotation plus harmony-frame projection",
                "phrase_restart_every": 4,
            },
        ),
    )
    initial = MusicState(
        phrase_start_step=start_step,
        phrase_end_step=end_step,
        current_step=start_step,
        form_function="adjacent_continuation",
        metadata={"specimen_id": CHILDREN_SPECIMEN_ID, "parent_answer_key_sha256": answer["answer_key_sha256"]},
    )
    composition = music_compose_player_piano(
        initial_state=initial,
        context=context,
        voice_plans=plans,
        program=program,
        beam_width=64,
    )
    composition_value = composition.to_dict()
    continuation_events = [deepcopy(dict(row)) for row in composition_value["final_state"]["events"]]
    legal = bool(
        continuation_events
        and all(bool(row.get("legal")) for row in composition_value["proofs"])
        and int(composition_value["open_obligation_count"]) == 0
        and min(int(row["start_step"]) for row in continuation_events) >= start_step
    )
    novelty = _literal_copy_check(
        answer,
        continuation_events,
        start_step=start_step,
        end_step=end_step,
    )
    if novelty["literal_copy_detected"]:
        raise SpecimenError("player piano produced a literal source-window copy")

    negative_state = MusicState(
        phrase_start_step=start_step,
        phrase_end_step=end_step,
        current_step=start_step,
        form_function="negative_control",
    )
    negative_event = music_make_event(
        voice_id=material["lead_voice"],
        role="lead",
        start_step=end_step - 1,
        duration_steps=1,
        pitch=int(material["illegal_pitch"]),
        velocity=94,
        function="statement",
        operator="state",
        source_ids=tuple(material["right_source_event_ids"]),
        metadata={"negative_control": "terminal leading tone with no reachable destination"},
    )
    negative_proof = music_prove_candidate(negative_state, negative_event, context, program)
    commit_refused = False
    try:
        music_commit_proof(negative_state, negative_proof)
    except MusicError:
        commit_refused = True
    negative_control_refused = bool(not negative_proof.legal and commit_refused)
    if not legal or not negative_control_refused:
        raise SpecimenError("Children adjacent-move proof did not satisfy its legal/illegal control contract")

    midi_ledger = _continuation_midi(
        continuation_events,
        start_step=start_step,
        end_step=end_step,
        steps_per_beat=steps_per_beat,
        tempo_bpm=float(answer["tempo_bpm"]),
        meter=dict(answer["meter"]),
    )
    midi_path = destination / "children.adjacent.mid"
    neutral_path = destination / "children.adjacent.neutral.wav"
    stems_dir = destination / "children.adjacent.stems"
    midi_write(midi_ledger, midi_path, overwrite=overwrite)
    render = midi_render_ledger(
        midi_ledger,
        neutral_path,
        stems_dir=stems_dir,
        sample_rate=int(sample_rate),
        waveform="triangle",
        overwrite=overwrite,
    )
    if not bool(render.get("complete_execution")) or int(render.get("selected_event_count") or 0) != len(continuation_events):
        raise SpecimenError("Children adjacent-move neutral execution is incomplete")

    receipt = {
        "schema_version": CHILDREN_CONTINUATION_SCHEMA_VERSION,
        "kind": CHILDREN_CONTINUATION_KIND,
        "specimen_id": CHILDREN_SPECIMEN_ID,
        "answer_key_sha256": str(answer["answer_key_sha256"]),
        "source_score_ledger_sha256": str(answer["score_ledger_sha256"]),
        "source_midi_semantic_sha256": str(answer["midi_semantic_sha256"]),
        "continuation_start_step": int(start_step),
        "continuation_end_step": int(end_step),
        "duration_bars": int(bars),
        "legal": legal,
        "negative_control_refused": negative_control_refused,
        "program_id": str(program.program_id),
        "program_sha256": str(program.program_sha256),
        "composition_sha256": str(composition_value["composition_sha256"]),
        "committed_event_count": len(continuation_events),
        "open_obligation_count": int(composition_value["open_obligation_count"]),
        "source_evidence": {
            "right_hand_event_ids": list(material["right_source_event_ids"]),
            "left_hand_event_ids": list(material["left_source_event_ids"]),
            "transformation": "rotated motif/register evidence projected through inherited harmony frames",
        },
        "harmony_frames": [frame.to_dict() for frame in frames],
        "composition": composition_value,
        "negative_control": {
            "event": negative_event.to_dict(),
            "proof": negative_proof.to_dict(),
            "commit_refused": commit_refused,
        },
        "novelty": novelty,
        "midi": {
            "semantic_sha256": str(midi_ledger["semantic_sha256"]),
            "selected_event_count": int(render["selected_event_count"]),
            "executed_event_count": int(render["executed_event_count"]),
            "refused_event_count": int(render["refused_event_count"]),
            "program_sha256": str(render["program_sha256"]),
            "execution_sha256": str(render["execution_sha256"]),
            "neutral_wav_sha256": str(render["output_sha256"]),
            "stem_count": len(render.get("stems") or []),
        },
        "boundary": {
            "commercial_recording_consulted": False,
            "private_library_consulted": False,
            "accepted_prefix_rewritten": False,
            "source_media_bundled": False,
        },
    }
    receipt["receipt_sha256"] = specimen_sha256_json(receipt)
    receipt_path = destination / "children.adjacent.receipt.json"
    specimen_write_json_atomic(receipt_path, receipt)
    return {
        "ok": True,
        "complete": True,
        "legal": True,
        "negative_control_refused": True,
        "output_dir": str(destination),
        "receipt_path": str(receipt_path),
        "midi_path": str(midi_path),
        "neutral_path": str(neutral_path),
        "stems_dir": str(stems_dir),
        "receipt": receipt,
    }


__all__ = [
    "CHILDREN_CONTINUATION_SCHEMA_VERSION",
    "CHILDREN_CONTINUATION_KIND",
    "CHILDREN_CONTINUATION_BARS",
    "children_compose_adjacent_move",
]
