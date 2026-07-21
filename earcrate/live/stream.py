from __future__ import annotations

import importlib.util
import math
from copy import deepcopy
from typing import Any, Mapping, Sequence

import numpy as np

from earcrate.live.crate import live_validate_crate_atlas
from earcrate.live.engine import live_engine_step, live_validate_engine_step
from earcrate.live.model import LiveError, live_validate_state
from earcrate.live.planner import (
    LIVE_SESSION_KIND,
    LIVE_SESSION_SCHEMA_VERSION,
    live_runtime_capability,
    live_validate_atlas,
    live_validate_session_plan,
)
from earcrate.live.runtime_fix import live_lower_session_to_midi
from earcrate.midi.model import (
    MidiTempoClock,
    midi_duration_seconds,
    midi_sha256_json,
)
from earcrate.rack.binding_stable import rack_compile_binding
from earcrate.rack.model import rack_validate_revision, rack_verify_sources
from earcrate.rack.render import (
    _event_extent_seconds,
    _load_zone_audio,
    _render_pass,
)
from earcrate.rack.render_fix import rack_compile_render_program

LIVE_PHRASE_BUFFER_SCHEMA_VERSION = 1
LIVE_PHRASE_BUFFER_KIND = "earcrate_live_phrase_buffer"
LIVE_BLOCK_STREAM_SCHEMA_VERSION = 1
LIVE_BLOCK_STREAM_KIND = "earcrate_live_block_stream"


def live_stream_capability() -> dict[str, Any]:
    return {
        "ready": True,
        "backend": "prepared_phrase_numpy_blocks",
        "requires_gpu": False,
        "requires_network": False,
        "requires_cloud": False,
        "audio_callback_performs_planning": False,
        "audio_callback_performs_library_search": False,
        "audio_callback_performs_sample_decode": False,
        "optional_sounddevice_ready": importlib.util.find_spec("sounddevice") is not None,
    }


def _live_local_phrase_session(
    atlas: Mapping[str, Any],
    step: Mapping[str, Any],
) -> dict[str, Any]:
    live_validate_atlas(atlas)
    live_validate_engine_step(step)
    committed = [deepcopy(dict(row)) for row in step["plan"]["committed_decisions"]]
    if not committed:
        raise LiveError("live phrase requires committed decisions")
    absolute_start = int(committed[0]["bar_index"])
    for local_index, decision in enumerate(committed):
        absolute = int(decision["bar_index"])
        if absolute != absolute_start + local_index:
            raise LiveError("live phrase decisions are not contiguous")
        decision["absolute_bar_index"] = absolute
        decision["bar_index"] = local_index
        for command in decision.get("commands") or []:
            command["absolute_bar_index"] = int(command.get("bar_index", absolute))
            command["bar_index"] = local_index
    session = {
        "schema_version": LIVE_SESSION_SCHEMA_VERSION,
        "kind": LIVE_SESSION_KIND,
        "source_semantic_sha256": str(atlas["source_semantic_sha256"]),
        "atlas_sha256": str(atlas["atlas_sha256"]),
        "target_bars": len(committed),
        "seed": int(step["state_before"]["seed"]),
        "initial_persona": str(committed[0]["persona"]),
        "controls": deepcopy(list(step.get("applied_controls") or [])),
        "applied_control_count": len(step.get("applied_controls") or []),
        "horizon_plans": [
            {
                "plan_sha256": str(step["plan"]["plan_sha256"]),
                "start_bar_index": absolute_start,
                "horizon_bars": int(step["plan"]["horizon_bars"]),
                "commit_bars": len(committed),
                "persona": str(step["plan"]["persona"]),
                "candidate_evaluations": int(step["plan"]["candidate_evaluations"]),
                "cumulative_score": float(step["plan"]["cumulative_score"]),
            }
        ],
        "decisions": committed,
        "final_state": deepcopy(dict(step["state_after"])),
        "state_history_sha256": midi_sha256_json(
            [step["state_before"]["state_sha256"], step["state_after"]["state_sha256"]]
        ),
        "runtime_capability": live_runtime_capability(),
        "absolute_start_bar_index": absolute_start,
        "source_step_sha256": str(step["step_sha256"]),
    }
    session["session_sha256"] = midi_sha256_json(session)
    live_validate_session_plan(session)
    return session


def live_validate_phrase_receipt(receipt: Mapping[str, Any]) -> None:
    if int(receipt.get("schema_version") or 0) != LIVE_PHRASE_BUFFER_SCHEMA_VERSION:
        raise LiveError(f"unsupported live phrase-buffer schema: {receipt.get('schema_version')}")
    if str(receipt.get("kind") or "") != LIVE_PHRASE_BUFFER_KIND:
        raise LiveError(f"unsupported live phrase-buffer kind: {receipt.get('kind')}")
    if not bool(receipt.get("complete")):
        raise LiveError("live phrase buffer must be complete")
    if int(receipt.get("frames") or 0) <= 0 or int(receipt.get("sample_rate") or 0) <= 0:
        raise LiveError("live phrase buffer requires positive frames and sample rate")
    if int(receipt.get("selected_event_count") or 0) != int(receipt.get("executed_event_count") or 0):
        raise LiveError("live phrase buffer did not execute every selected event")
    if int(receipt.get("truncated_event_count") or 0) or int(receipt.get("refused_event_count") or 0):
        raise LiveError("live phrase buffer contains truncated or refused events")
    expected = midi_sha256_json({key: value for key, value in receipt.items() if key != "phrase_sha256"})
    if str(receipt.get("phrase_sha256") or "") != expected:
        raise LiveError("phrase_sha256 does not match live phrase receipt")


def live_render_next_phrase(
    crate_atlas: Mapping[str, Any],
    state: Mapping[str, Any],
    *,
    controls: Sequence[Mapping[str, Any]] | None = None,
    horizon_bars: int = 0,
    commit_bars: int = 0,
    beam_width: int = 32,
    candidate_limit: int = 12,
    target_bpm: float = 0.0,
    target_peak: float = 0.90,
) -> dict[str, Any]:
    """Plan one legal phrase and render it into memory before the audio callback needs it."""
    live_validate_crate_atlas(crate_atlas, verify_sources=True)
    live_validate_state(state)
    if not 0.0 < float(target_peak) <= 1.0:
        raise LiveError("live phrase target_peak must be in (0,1]")
    atlas = crate_atlas["live_material_atlas"]
    if str(state["atlas_sha256"]) != str(atlas["atlas_sha256"]):
        raise LiveError("live phrase state belongs to another crate atlas")
    step = live_engine_step(
        atlas,
        state,
        controls=controls,
        horizon_bars=horizon_bars,
        commit_bars=commit_bars,
        beam_width=beam_width,
        candidate_limit=candidate_limit,
    )
    phrase_session = _live_local_phrase_session(atlas, step)
    lowering = live_lower_session_to_midi(
        crate_atlas["source_midi_ledger"],
        atlas,
        phrase_session,
        target_bpm=target_bpm,
    )
    racks = [deepcopy(dict(rack)) for rack in crate_atlas["rack_revisions"]]
    for rack in racks:
        rack_validate_revision(rack)
        rack_verify_sources(rack)
    binding = rack_compile_binding(
        lowering["ledger"],
        racks,
        pitch_bend_range_semitones=2.0,
    )
    if not bool(binding.get("complete")):
        raise LiveError(
            "precompiled racks cannot execute the next live phrase: "
            + str(binding.get("unresolved") or [])
        )
    sample_rate = int(crate_atlas["sample_rate"])
    pitch_bend_range = float(binding["pitch_bend_range_semitones"])
    program = rack_compile_render_program(
        lowering["ledger"],
        binding,
        racks,
        sample_rate=sample_rate,
        pitch_bend_range_semitones=pitch_bend_range,
    )
    racks_by_sha = {str(rack["rack_sha256"]): rack for rack in racks}
    audio_cache: dict[tuple[str, str], np.ndarray] = {}
    for event in program["events"]:
        key = (str(event["rack_sha256"]), str(event["zone_id"]))
        if key not in audio_cache:
            rack = racks_by_sha[key[0]]
            zone = next(zone for zone in rack["zones"] if str(zone["zone_id"]) == key[1])
            audio_cache[key] = _load_zone_audio(zone)
    clock = MidiTempoClock(lowering["ledger"])
    natural_duration = max(
        midi_duration_seconds(lowering["ledger"]),
        max(
            (
                _event_extent_seconds(event, event, clock, pitch_bend_range)
                for event in program["events"]
            ),
            default=0.0,
        ),
    ) + 0.05
    total_frames = max(1, int(math.ceil(natural_duration * sample_rate)))
    audio = np.zeros((total_frames, 2), dtype=np.float32)
    outcomes = _render_pass(
        audio,
        list(program["events"]),
        program,
        clock,
        audio_cache,
        sample_rate=sample_rate,
        pitch_bend_range_semitones=pitch_bend_range,
        record_outcomes=True,
    )
    if len(outcomes) != len(program["events"]):
        raise LiveError("live phrase execution did not account for every selected event")
    counts = {
        status: sum(str(row["status"]) == status for row in outcomes)
        for status in ("executed", "truncated", "refused")
    }
    if counts["truncated"] or counts["refused"]:
        failures = [
            f"{row['event_id']}:{row['reason']}"
            for row in outcomes
            if str(row["status"]) != "executed"
        ]
        raise LiveError("live phrase sample execution failed: " + ", ".join(failures[:12]))
    peak_before = float(np.max(np.abs(audio))) if audio.size else 0.0
    scale = min(1.0, float(target_peak) / peak_before) if peak_before > 0.0 else 1.0
    audio *= np.float32(scale)
    pcm_sha256 = __import__("hashlib").sha256(np.asarray(audio, dtype="<f4", order="C").tobytes(order="C")).hexdigest()
    receipt = {
        "schema_version": LIVE_PHRASE_BUFFER_SCHEMA_VERSION,
        "kind": LIVE_PHRASE_BUFFER_KIND,
        "complete": True,
        "crate_atlas_sha256": str(crate_atlas["crate_atlas_sha256"]),
        "step_sha256": str(step["step_sha256"]),
        "state_before_sha256": str(state["state_sha256"]),
        "state_after_sha256": str(step["state_after"]["state_sha256"]),
        "absolute_start_bar_index": int(phrase_session["absolute_start_bar_index"]),
        "bars": int(phrase_session["target_bars"]),
        "persona": str(step["plan"]["committed_decisions"][0]["persona"]),
        "operators": [str(row["operator"]) for row in step["plan"]["committed_decisions"]],
        "midi_semantic_sha256": str(lowering["ledger"]["semantic_sha256"]),
        "binding_sha256": str(binding["binding_sha256"]),
        "program_sha256": str(program["program_sha256"]),
        "sample_rate": sample_rate,
        "channels": 2,
        "frames": total_frames,
        "duration_seconds": round(total_frames / sample_rate, 9),
        "selected_event_count": len(program["events"]),
        "executed_event_count": counts["executed"],
        "truncated_event_count": counts["truncated"],
        "refused_event_count": counts["refused"],
        "peak_before_scale": round(peak_before, 9),
        "applied_scale": round(scale, 12),
        "pcm_f32le_sha256": pcm_sha256,
        "materials_scanned_during_render": 0,
        "samples_decoded_during_callback": 0,
    }
    receipt["phrase_sha256"] = midi_sha256_json(receipt)
    live_validate_phrase_receipt(receipt)
    return {
        "audio": audio,
        "receipt": receipt,
        "step": step,
        "next_state": step["state_after"],
        "phrase_session": phrase_session,
        "midi_lowering": lowering,
        "binding": binding,
        "render_program": program,
        "execution_outcomes": outcomes,
    }


class LiveBlockStream:
    """Copy already-prepared phrase audio in fixed blocks; planning never runs here."""

    def __init__(self, *, sample_rate: int, block_frames: int = 512):
        if int(sample_rate) <= 0 or int(block_frames) <= 0:
            raise LiveError("live block stream requires positive sample rate and block size")
        self.sample_rate = int(sample_rate)
        self.block_frames = int(block_frames)
        self._audio: np.ndarray | None = None
        self._receipt: dict[str, Any] | None = None
        self._offset = 0
        self._blocks = 0
        self._frames_delivered = 0
        self._underruns = 0
        self._phrase_history: list[dict[str, Any]] = []

    @property
    def ready(self) -> bool:
        return self._audio is not None and self._offset < int(self._audio.shape[0])

    @property
    def remaining_frames(self) -> int:
        return 0 if self._audio is None else max(0, int(self._audio.shape[0]) - self._offset)

    def load_phrase(self, phrase: Mapping[str, Any]) -> None:
        if self.ready:
            raise LiveError("cannot replace a live phrase before its prepared frames are consumed")
        audio = np.asarray(phrase.get("audio"), dtype=np.float32)
        receipt = phrase.get("receipt")
        if audio.ndim != 2 or audio.shape[1] != 2 or audio.shape[0] <= 0:
            raise LiveError("prepared live phrase audio must be nonempty stereo PCM")
        if not np.isfinite(audio).all():
            raise LiveError("prepared live phrase contains non-finite PCM")
        if not isinstance(receipt, Mapping):
            raise LiveError("prepared live phrase requires a receipt")
        live_validate_phrase_receipt(receipt)
        if int(receipt["sample_rate"]) != self.sample_rate:
            raise LiveError("prepared live phrase sample rate does not match the block stream")
        if int(receipt["frames"]) != int(audio.shape[0]):
            raise LiveError("prepared live phrase frame count does not match its receipt")
        self._audio = np.asarray(audio, dtype=np.float32, order="C")
        self._receipt = deepcopy(dict(receipt))
        self._offset = 0

    def read_block(self, frames: int | None = None) -> np.ndarray:
        count = int(frames or self.block_frames)
        if count <= 0:
            raise LiveError("live block size must be positive")
        block = np.zeros((count, 2), dtype=np.float32)
        if not self.ready:
            self._underruns += 1
            self._blocks += 1
            self._frames_delivered += count
            return block
        assert self._audio is not None
        available = min(count, int(self._audio.shape[0]) - self._offset)
        block[:available] = self._audio[self._offset : self._offset + available]
        self._offset += available
        self._blocks += 1
        self._frames_delivered += count
        if self._offset >= int(self._audio.shape[0]):
            assert self._receipt is not None
            self._phrase_history.append(
                {
                    "phrase_sha256": str(self._receipt["phrase_sha256"]),
                    "frames": int(self._audio.shape[0]),
                    "blocks_at_completion": self._blocks,
                }
            )
        return block

    def receipt(self) -> dict[str, Any]:
        value = {
            "schema_version": LIVE_BLOCK_STREAM_SCHEMA_VERSION,
            "kind": LIVE_BLOCK_STREAM_KIND,
            "sample_rate": self.sample_rate,
            "block_frames": self.block_frames,
            "blocks_delivered": self._blocks,
            "frames_delivered": self._frames_delivered,
            "underrun_count": self._underruns,
            "ready": self.ready,
            "remaining_frames": self.remaining_frames,
            "phrase_history": deepcopy(self._phrase_history),
            "callback_planning_count": 0,
            "callback_library_search_count": 0,
            "callback_sample_decode_count": 0,
        }
        value["stream_sha256"] = midi_sha256_json(value)
        return value
