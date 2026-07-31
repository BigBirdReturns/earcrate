from __future__ import annotations

"""Rhythm-obligated, proof-carrying adjacent moves for ``children_v1``.

The score's final eight performed measures are used only as a hereditary contract:
relative onsets, durations, register, and motif evidence. Their pitches are rotated,
transformed, and re-projected through a different inherited harmony trajectory before
the player-piano constitution may commit them. The result must preserve rhythmic
identity while changing both pitch sequence and harmony, execute exactly through
MIDI, and retain an illegal terminal-tension negative control.
"""

from copy import deepcopy
from pathlib import Path
from typing import Any, Mapping, Sequence

from earcrate.midi.codec import midi_write
from earcrate.midi.render import midi_render_ledger
from earcrate.music.law_context import MusicLawContext
from earcrate.music.laws import music_commit_proof, music_prove_candidate
from earcrate.music.model import (
    MusicError,
    MusicState,
    music_make_event,
    music_sha256_json,
)
from earcrate.music.player_piano import (
    MusicCompositionResult,
    MusicVoicePlan,
    music_context_payload,
    music_electro_soul_player_piano,
)

from .continuation import (
    CHILDREN_CONTINUATION_BARS,
    CHILDREN_CONTINUATION_KIND,
    CHILDREN_CONTINUATION_MIDI_PPQ,
    CHILDREN_CONTINUATION_SCHEMA_VERSION,
    _answer_key,
    _canonical_pcm_sha256,
    _continuation_frames,
    _continuation_midi,
    _frame_at,
    _literal_copy_check,
    _natural_minor,
    _nearest_pitch,
    _source_end_step,
    _voice_events,
)
from .model import SpecimenError, specimen_sha256_json, specimen_write_json_atomic


def _source_rhythm_templates(
    answer: Mapping[str, Any],
    *,
    bars: int,
) -> tuple[int, int, list[dict[str, Any]], list[dict[str, Any]]]:
    steps_per_bar = int(answer["steps_per_beat"]) * int((answer.get("meter") or {}).get("numerator") or 4)
    source_end = _source_end_step(answer)
    source_start = source_end - int(bars) * steps_per_bar
    if source_start < 0:
        raise SpecimenError("Children score is shorter than the requested continuation rhythm window")
    right = [
        deepcopy(dict(row))
        for row in _voice_events(answer, side="right")
        if source_start <= int(row["start_step"]) < source_end
    ]
    left = [
        deepcopy(dict(row))
        for row in _voice_events(answer, side="left")
        if source_start <= int(row["start_step"]) < source_end
    ]
    if not right or not left:
        raise SpecimenError("Children continuation requires a nonempty two-hand rhythm template")
    return source_start, source_end, right, left


def _stable_candidates(
    target: int,
    pitch_classes: Sequence[int],
    *,
    low: int,
    high: int,
) -> tuple[int, ...]:
    primary = _nearest_pitch(int(target), pitch_classes, int(low), int(high))
    return tuple(
        sorted(
            {
                primary,
                _nearest_pitch(primary - 3, pitch_classes, int(low), int(high)),
                _nearest_pitch(primary + 4, pitch_classes, int(low), int(high)),
            }
        )
    )


def _candidate_rows(
    template: Sequence[Mapping[str, Any]],
    *,
    source_start: int,
    continuation_start: int,
    continuation_end: int,
    harmony_frames: Sequence[Any],
    voice_id: str,
    role: str,
    low: int,
    high: int,
    pitch_class_field: str,
    steps_per_bar: int,
) -> tuple[list[dict[str, Any]], dict[str, dict[int, float]], tuple[int, ...]]:
    pitches = [int(row["pitch"]) for row in template]
    if not pitches:
        raise SpecimenError(f"Children {role} rhythm template contains no pitches")
    rows: list[dict[str, Any]] = []
    evidence: dict[str, dict[int, float]] = {}
    previous: int | None = None
    for index, source_event in enumerate(template):
        relative_step = int(source_event["start_step"]) - int(source_start)
        start_step = int(continuation_start) + relative_step
        duration_steps = min(int(source_event["duration_steps"]), int(continuation_end) - start_step)
        if duration_steps <= 0:
            raise SpecimenError("Children rhythm template escapes the continuation interval")
        frame = _frame_at(harmony_frames, start_step)
        rotated_pitch = pitches[(index + 1) % len(pitches)]
        if previous is None:
            target = rotated_pitch
        else:
            raw_interval = pitches[(index + 1) % len(pitches)] - pitches[index % len(pitches)]
            # Invert two interior bars and bound motion. This preserves the source
            # interval vocabulary without reproducing its literal contour.
            bar = relative_step // int(steps_per_bar)
            transformed = -raw_interval if bar in {1, 5} else raw_interval
            maximum = 4 if role == "lead" else 7
            transformed = max(-maximum, min(maximum, transformed))
            target = previous + transformed
        pitch_classes = getattr(frame, pitch_class_field)
        candidates = _stable_candidates(target, pitch_classes, low=low, high=high)
        if index == len(template) - 1:
            candidates = (
                _nearest_pitch(previous if previous is not None else target, (frame.root_pc,), low, high),
            )
        primary = min(candidates, key=lambda pitch: (abs(int(pitch) - int(target)), int(pitch)))
        previous = int(primary)
        evidence[MusicLawContext.evidence_key(voice_id, start_step)] = {
            int(pitch): (1.0 if int(pitch) == int(primary) else 0.55)
            for pitch in candidates
        }
        rows.append(
            {
                "start_step": start_step,
                "duration_steps": duration_steps,
                "candidates": tuple(int(pitch) for pitch in candidates),
                "primary_pitch": int(primary),
                "source_event_id": str(source_event["event_id"]),
                "relative_step": relative_step,
                "source_pitch": int(source_event["pitch"]),
            }
        )
    source_intervals = tuple(
        int(pitches[index + 1]) - int(pitches[index])
        for index in range(min(len(pitches) - 1, 8))
    )
    return rows, evidence, source_intervals


def _bar_counts(rows: Sequence[Mapping[str, Any]], *, origin: int, steps_per_bar: int, bars: int) -> list[int]:
    counts = [0 for _ in range(int(bars))]
    for row in rows:
        index = (int(row["start_step"]) - int(origin)) // int(steps_per_bar)
        if 0 <= index < int(bars):
            counts[index] += 1
    return counts


def _onset_residues(rows: Sequence[Mapping[str, Any]], *, origin: int, steps_per_bar: int) -> list[int]:
    return sorted({(int(row["start_step"]) - int(origin)) % int(steps_per_bar) for row in rows})


def _jaccard(left: Sequence[int], right: Sequence[int]) -> float:
    a, b = set(int(value) for value in left), set(int(value) for value in right)
    if not a and not b:
        return 1.0
    return round(len(a & b) / max(1, len(a | b)), 12)


def _rhythm_obligation(
    *,
    source_right: Sequence[Mapping[str, Any]],
    source_left: Sequence[Mapping[str, Any]],
    continuation_events: Sequence[Mapping[str, Any]],
    source_origin: int,
    continuation_origin: int,
    steps_per_bar: int,
    bars: int,
) -> dict[str, Any]:
    continuation_right = [row for row in continuation_events if str(row.get("role") or "") == "lead"]
    continuation_left = [row for row in continuation_events if str(row.get("role") or "") == "bass"]
    source_total = len(source_right) + len(source_left)
    continuation_total = len(continuation_right) + len(continuation_left)
    lead_density = len(continuation_right) / max(1, len(source_right))
    bass_density = len(continuation_left) / max(1, len(source_left))
    total_density = continuation_total / max(1, source_total)
    source_lead_residues = _onset_residues(source_right, origin=source_origin, steps_per_bar=steps_per_bar)
    source_bass_residues = _onset_residues(source_left, origin=source_origin, steps_per_bar=steps_per_bar)
    continuation_lead_residues = _onset_residues(continuation_right, origin=continuation_origin, steps_per_bar=steps_per_bar)
    continuation_bass_residues = _onset_residues(continuation_left, origin=continuation_origin, steps_per_bar=steps_per_bar)
    lead_onset_overlap = _jaccard(source_lead_residues, continuation_lead_residues)
    bass_onset_overlap = _jaccard(source_bass_residues, continuation_bass_residues)
    source_duration_signature = sorted(
        [("lead", int(row["duration_steps"])) for row in source_right]
        + [("bass", int(row["duration_steps"])) for row in source_left]
    )
    continuation_duration_signature = sorted(
        (str(row["role"]), int(row["duration_steps"])) for row in continuation_events
    )
    duration_multiset_preserved = source_duration_signature == continuation_duration_signature
    source_bar_counts = {
        "lead": _bar_counts(source_right, origin=source_origin, steps_per_bar=steps_per_bar, bars=bars),
        "bass": _bar_counts(source_left, origin=source_origin, steps_per_bar=steps_per_bar, bars=bars),
    }
    continuation_bar_counts = {
        "lead": _bar_counts(continuation_right, origin=continuation_origin, steps_per_bar=steps_per_bar, bars=bars),
        "bass": _bar_counts(continuation_left, origin=continuation_origin, steps_per_bar=steps_per_bar, bars=bars),
    }
    passed = bool(
        0.60 <= total_density <= 1.50
        and 0.60 <= lead_density <= 1.60
        and 0.60 <= bass_density <= 1.60
        and lead_onset_overlap >= 0.75
        and bass_onset_overlap >= 0.75
        and duration_multiset_preserved
        and source_bar_counts == continuation_bar_counts
    )
    return {
        "rhythmic_identity_passed": passed,
        "source_event_count": source_total,
        "continuation_event_count": continuation_total,
        "density_ratio": round(total_density, 12),
        "lead_density_ratio": round(lead_density, 12),
        "bass_density_ratio": round(bass_density, 12),
        "lead_onset_grid_overlap": lead_onset_overlap,
        "bass_onset_grid_overlap": bass_onset_overlap,
        "duration_multiset_preserved": duration_multiset_preserved,
        "source_bar_note_counts": source_bar_counts,
        "continuation_bar_note_counts": continuation_bar_counts,
        "source_lead_onset_residues": source_lead_residues,
        "source_bass_onset_residues": source_bass_residues,
        "continuation_lead_onset_residues": continuation_lead_residues,
        "continuation_bass_onset_residues": continuation_bass_residues,
        "obligation": "retain the final eight-measure two-hand onset, duration, and density identity",
    }


def _pitch_harmony_novelty(
    *,
    source_right: Sequence[Mapping[str, Any]],
    source_left: Sequence[Mapping[str, Any]],
    continuation_events: Sequence[Mapping[str, Any]],
    answer: Mapping[str, Any],
    source_start: int,
    source_end: int,
    harmony_frames: Sequence[Any],
) -> dict[str, Any]:
    source_pitch_sequence = [int(row["pitch"]) for row in [*source_left, *source_right]]
    continuation_pitch_sequence = [
        int(row["pitch"])
        for row in sorted(continuation_events, key=lambda value: (str(value["role"]), int(value["start_step"])))
    ]
    source_harmony = [
        str(row.get("label") or "")
        for row in answer["harmony_frames"]
        if int(row["end_step"]) > int(source_start) and int(row["start_step"]) < int(source_end)
    ]
    continuation_harmony = [str(frame.label) for frame in harmony_frames]
    return {
        "source_pitch_sequence_sha256": specimen_sha256_json(source_pitch_sequence),
        "continuation_pitch_sequence_sha256": specimen_sha256_json(continuation_pitch_sequence),
        "pitch_sequence_changed": source_pitch_sequence != continuation_pitch_sequence,
        "source_harmony_sequence": source_harmony,
        "continuation_harmony_sequence": continuation_harmony,
        "harmony_sequence_changed": source_harmony != continuation_harmony,
    }


def children_compose_adjacent_move(
    answer_key: str | Path | Mapping[str, Any],
    output_dir: str | Path,
    *,
    bars: int = CHILDREN_CONTINUATION_BARS,
    sample_rate: int = 8_000,
    overwrite: bool = False,
) -> dict[str, Any]:
    """Compose a rhythm-legible, proof-carrying Children-adjacent continuation."""
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
    source_start, source_end, source_right, source_left = _source_rhythm_templates(answer, bars=int(bars))
    continuation_start = source_end
    continuation_end = continuation_start + int(bars) * steps_per_bar
    harmony_frames = _continuation_frames(answer, start_step=continuation_start, bars=int(bars))

    lead_voice = "children_adjacent_right_hand"
    bass_voice = "children_adjacent_left_hand"
    bass_rows, bass_evidence, _bass_intervals = _candidate_rows(
        source_left,
        source_start=source_start,
        continuation_start=continuation_start,
        continuation_end=continuation_end,
        harmony_frames=harmony_frames,
        voice_id=bass_voice,
        role="bass",
        low=28,
        high=60,
        pitch_class_field="bass_pitch_classes",
        steps_per_bar=steps_per_bar,
    )
    lead_rows, lead_evidence, lead_intervals = _candidate_rows(
        source_right,
        source_start=source_start,
        continuation_start=continuation_start,
        continuation_end=continuation_end,
        harmony_frames=harmony_frames,
        voice_id=lead_voice,
        role="lead",
        low=55,
        high=96,
        pitch_class_field="stable_pitch_classes",
        steps_per_bar=steps_per_bar,
    )
    evidence = {**bass_evidence, **lead_evidence}
    scale = set(_natural_minor(int((answer.get("key_signature") or {}).get("tonic_pc", harmony_frames[-1].root_pc))))
    for frame in harmony_frames:
        scale.update(frame.pitch_classes)
    source_right_ids = tuple(str(row["event_id"]) for row in source_right)
    source_left_ids = tuple(str(row["event_id"]) for row in source_left)
    lead_average = sum(int(row["pitch"]) for row in source_right) / len(source_right)
    bass_average = sum(int(row["pitch"]) for row in source_left) / len(source_left)
    context = MusicLawContext(
        harmony_frames=tuple(harmony_frames),
        steps_per_beat=steps_per_beat,
        scale_pitch_classes=tuple(sorted(scale)),
        role_ranges={"default": (0, 127), "lead": (55, 96), "bass": (28, 60)},
        evidence=evidence,
        future_steps={
            bass_voice: tuple(int(row["start_step"]) for row in bass_rows),
            lead_voice: tuple(int(row["start_step"]) for row in lead_rows),
        },
        register_targets={
            "lead": float(lead_average),
            "bass": float(bass_average),
            lead_voice: float(lead_average),
            bass_voice: float(bass_average),
        },
        metadata={
            "specimen_id": str(answer["specimen_id"]),
            "answer_key_sha256": str(answer["answer_key_sha256"]),
            "adjacent_move": True,
            "rhythm_template_source_steps": [source_start, source_end],
        },
    )
    program = music_electro_soul_player_piano(voice_order=(bass_voice, lead_voice))
    plans = (
        MusicVoicePlan(
            voice_id=bass_voice,
            role="bass",
            onset_steps=tuple(int(row["start_step"]) for row in bass_rows),
            duration_steps=2,
            terminal_duration_steps=4,
            pitch_pool=tuple(sorted({pitch for row in bass_rows for pitch in row["candidates"]})),
            velocity=86,
            function="harmonic_support",
            operator_candidates=("state",),
            source_ids=source_left_ids,
            metadata={
                "exact_rhythm_template_sha256": specimen_sha256_json(
                    [(int(row["relative_step"]), int(row["duration_steps"])) for row in bass_rows]
                )
            },
        ),
        MusicVoicePlan(
            voice_id=lead_voice,
            role="lead",
            onset_steps=tuple(int(row["start_step"]) for row in lead_rows),
            duration_steps=1,
            terminal_duration_steps=4,
            pitch_pool=tuple(sorted({pitch for row in lead_rows for pitch in row["candidates"]})),
            velocity=94,
            function="motif_continuation",
            operator_candidates=("state",),
            motif_intervals=tuple(lead_intervals),
            source_ids=source_right_ids,
            metadata={
                "exact_rhythm_template_sha256": specimen_sha256_json(
                    [(int(row["relative_step"]), int(row["duration_steps"])) for row in lead_rows]
                )
            },
        ),
    )
    initial = MusicState(
        phrase_start_step=continuation_start,
        phrase_end_step=continuation_end,
        current_step=continuation_start,
        form_function="adjacent_continuation",
        metadata={
            "specimen_id": str(answer["specimen_id"]),
            "parent_answer_key_sha256": str(answer["answer_key_sha256"]),
        },
    )
    state = initial
    proofs: list[Any] = []
    selection_receipts: list[dict[str, Any]] = []
    for rows, voice_id, role, source_ids, motif_intervals in (
        (bass_rows, bass_voice, "bass", source_left_ids, ()),
        (lead_rows, lead_voice, "lead", source_right_ids, tuple(lead_intervals)),
    ):
        for index, row in enumerate(rows):
            terminal = int(row["start_step"]) + int(row["duration_steps"]) >= continuation_end
            legal_proofs = []
            refused_proofs = []
            for pitch in row["candidates"]:
                event = music_make_event(
                    voice_id=voice_id,
                    role=role,
                    start_step=int(row["start_step"]),
                    duration_steps=int(row["duration_steps"]),
                    pitch=int(pitch),
                    velocity=86 if role == "bass" else 94,
                    function="cadence" if terminal else ("harmonic_support" if role == "bass" else "motif_continuation"),
                    operator="state",
                    source_ids=source_ids,
                    metadata={
                        "allow_backfill": True,
                        "source_rhythm_event_id": str(row["source_event_id"]),
                        "source_relative_step": int(row["relative_step"]),
                        "source_pitch": int(row["source_pitch"]),
                        "phrase_restart": int(row["relative_step"]) % steps_per_bar == 0,
                        "transformation": "rotated contour projected through inherited harmony",
                    },
                )
                proof = music_prove_candidate(
                    state,
                    event,
                    context,
                    program,
                    motif_intervals=motif_intervals,
                    motif_index=index,
                )
                (legal_proofs if proof.legal else refused_proofs).append(proof)
            if not legal_proofs:
                failures = sorted({failure for proof in refused_proofs for failure in proof.failures})
                raise SpecimenError(
                    f"player-piano constitution found no legal {role} event at step {row['start_step']}: {failures}"
                )
            chosen = max(legal_proofs, key=lambda proof: (proof.rank_vector, proof.event.event_id))
            state = music_commit_proof(state, chosen)
            proofs.append(chosen)
            selection_receipts.append(
                {
                    "voice_id": voice_id,
                    "start_step": int(row["start_step"]),
                    "source_event_id": str(row["source_event_id"]),
                    "selected_event_id": str(chosen.event.event_id),
                    "selected_pitch": int(chosen.event.pitch),
                    "rank_vector": list(chosen.rank_vector),
                    "legal_alternative_count": len(legal_proofs),
                    "refused_alternative_count": len(refused_proofs),
                    "alternative_proof_sha256s": [proof.to_dict()["proof_sha256"] for proof in legal_proofs],
                }
            )
    if state.obligations:
        raise SpecimenError("Children dense continuation finished with unresolved musical obligations")
    composition = MusicCompositionResult(
        program=program,
        context_sha256=music_sha256_json(music_context_payload(context)),
        initial_state_sha256=initial.state_sha256,
        final_state=state,
        voice_plans=plans,
        proofs=tuple(proofs),
    )
    composition_value = composition.to_dict()
    continuation_events = [deepcopy(dict(row)) for row in composition_value["final_state"]["events"]]
    legal = bool(
        continuation_events
        and len(continuation_events) == len(source_right) + len(source_left)
        and all(bool(row.get("legal")) for row in composition_value["proofs"])
        and int(composition_value["open_obligation_count"]) == 0
        and min(int(row["start_step"]) for row in continuation_events) >= continuation_start
    )
    rhythmic_obligation = _rhythm_obligation(
        source_right=source_right,
        source_left=source_left,
        continuation_events=continuation_events,
        source_origin=source_start,
        continuation_origin=continuation_start,
        steps_per_bar=steps_per_bar,
        bars=int(bars),
    )
    novelty = _literal_copy_check(
        answer,
        continuation_events,
        start_step=continuation_start,
        end_step=continuation_end,
    )
    novelty.update(
        _pitch_harmony_novelty(
            source_right=source_right,
            source_left=source_left,
            continuation_events=continuation_events,
            answer=answer,
            source_start=source_start,
            source_end=source_end,
            harmony_frames=harmony_frames,
        )
    )
    if novelty["literal_copy_detected"] or not novelty["pitch_sequence_changed"] or not novelty["harmony_sequence_changed"]:
        raise SpecimenError("Children adjacent move did not clear pitch/harmony novelty obligations")
    if not rhythmic_obligation["rhythmic_identity_passed"]:
        raise SpecimenError("Children adjacent move lost the source rhythm and density identity")

    final_frame = _frame_at(harmony_frames, continuation_end - 1)
    illegal_pc = (int(final_frame.root_pc) - 1) % 12
    illegal_pitch = _nearest_pitch(
        int(continuation_events[-1]["pitch"]),
        (illegal_pc,),
        55,
        96,
    )
    negative_key = MusicLawContext.evidence_key(lead_voice, continuation_end - 1)
    negative_context = MusicLawContext(
        harmony_frames=context.harmony_frames,
        steps_per_beat=context.steps_per_beat,
        scale_pitch_classes=context.scale_pitch_classes,
        role_ranges=context.role_ranges,
        evidence={**context.evidence, negative_key: {illegal_pitch: 1.0}},
        future_steps=context.future_steps,
        register_targets=context.register_targets,
        metadata={**dict(context.metadata), "negative_control": True},
    )
    negative_state = MusicState(
        phrase_start_step=continuation_start,
        phrase_end_step=continuation_end,
        current_step=continuation_start,
        form_function="negative_control",
    )
    negative_event = music_make_event(
        voice_id=lead_voice,
        role="lead",
        start_step=continuation_end - 1,
        duration_steps=1,
        pitch=illegal_pitch,
        velocity=94,
        function="statement",
        operator="state",
        source_ids=source_right_ids,
        metadata={"negative_control": "terminal leading tone with no reachable destination"},
    )
    negative_proof = music_prove_candidate(negative_state, negative_event, negative_context, program)
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
        start_step=continuation_start,
        end_step=continuation_end,
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
    if (
        not bool(render.get("complete_execution"))
        or int(render.get("selected_event_count") or 0) != len(continuation_events)
        or int(render.get("executed_event_count") or 0) != len(continuation_events)
        or int(render.get("refused_event_count") or 0) != 0
    ):
        raise SpecimenError("Children adjacent-move neutral execution is incomplete")

    receipt = {
        "schema_version": CHILDREN_CONTINUATION_SCHEMA_VERSION,
        "kind": CHILDREN_CONTINUATION_KIND,
        "specimen_id": str(answer["specimen_id"]),
        "answer_key_sha256": str(answer["answer_key_sha256"]),
        "source_score_ledger_sha256": str(answer["score_ledger_sha256"]),
        "source_midi_semantic_sha256": str(answer["midi_semantic_sha256"]),
        "source_rhythm_start_step": int(source_start),
        "source_rhythm_end_step": int(source_end),
        "continuation_start_step": int(continuation_start),
        "continuation_end_step": int(continuation_end),
        "duration_bars": int(bars),
        "legal": legal,
        "negative_control_refused": negative_control_refused,
        "rhythmic_identity_passed": bool(rhythmic_obligation["rhythmic_identity_passed"]),
        "program_id": str(program.program_id),
        "program_sha256": str(program.program_sha256),
        "composition_sha256": str(composition_value["composition_sha256"]),
        "committed_event_count": len(continuation_events),
        "open_obligation_count": int(composition_value["open_obligation_count"]),
        "source_evidence": {
            "right_hand_event_ids": list(source_right_ids),
            "left_hand_event_ids": list(source_left_ids),
            "transformation": "exact rhythm inheritance plus rotated contour and harmony-frame reprojection",
        },
        "rhythmic_obligation": rhythmic_obligation,
        "harmony_frames": [frame.to_dict() for frame in harmony_frames],
        "selection_receipts": selection_receipts,
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
            "neutral_pcm_f32le_sha256": _canonical_pcm_sha256(neutral_path),
            "neutral_wav_sha256": str(render["output_sha256"]),
            "stem_count": len(render.get("stems") or []),
        },
        "boundary": {
            "commercial_recording_consulted": False,
            "private_library_consulted": False,
            "accepted_prefix_rewritten": False,
            "source_media_bundled": False,
        },
        "receipt_hash_policy": {
            "authority": "decoded stereo float32 PCM",
            "excluded_delivery_fields": ["midi.neutral_wav_sha256"],
            "reason": "WAV container metadata is not musical identity",
        },
    }
    receipt_payload = deepcopy(receipt)
    receipt_payload["midi"].pop("neutral_wav_sha256", None)
    receipt["receipt_sha256"] = specimen_sha256_json(receipt_payload)
    receipt_path = destination / "children.adjacent.receipt.json"
    specimen_write_json_atomic(receipt_path, receipt)
    return {
        "ok": True,
        "complete": True,
        "legal": True,
        "negative_control_refused": True,
        "rhythmic_identity_passed": True,
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
