from __future__ import annotations

import json
import math
import os
import tempfile
from copy import deepcopy
from pathlib import Path
from typing import Any, Mapping

import numpy as np
import soundfile as sf

from earcrate.mix.audio import (
    _mixscore_apply_pan,
    _mixscore_beat_to_frame,
    _mixscore_crossfader_side_gain,
    _mixscore_file_sha256,
    _mixscore_pcm_sha256,
    _mixscore_render_transport_span,
    _mixscore_safe_name,
)
from earcrate.mix.automation import (
    _mixscore_build_crossfader_envelope,
    _mixscore_build_gain_envelope,
    _mixscore_db_to_gain,
    _mixscore_execution_ledger,
    _mixscore_mark_event,
)
from earcrate.mix.model import (
    MIX_DECK_AUTOMATION_OPERATIONS,
    MIX_MASTER_AUTOMATION_OPERATIONS,
    MIX_RENDER_RECEIPT_KIND,
    MIX_RENDER_RECEIPT_SCHEMA_VERSION,
    MIX_TRANSPORT_OPERATIONS,
    MixScoreError,
    mixscore_load,
    mixscore_seal,
    mixscore_sha256_json,
)
from earcrate.mix.transport import (
    _mixscore_apply_transport_event,
    _mixscore_load_assets,
    _mixscore_new_deck_state,
)


def mixscore_render(
    score: Mapping[str, Any],
    *,
    base_dir: str | Path | None = None,
) -> dict[str, Any]:
    input_score = mixscore_seal(score)
    root = Path(base_dir or ".").expanduser().resolve()
    loaded_assets, bound_score = _mixscore_load_assets(input_score, base_dir=root)
    sample_rate = int(bound_score["clock"]["sample_rate"])
    bpm = float(bound_score["clock"]["bpm"])
    total_frames = _mixscore_beat_to_frame(bound_score["end_beat"], sample_rate=sample_rate, bpm=bpm)
    if total_frames <= 0:
        raise MixScoreError("MixScore duration produces zero output frames")

    ledger_rows = {
        str(event["event_id"]): {
            "event_id": str(event["event_id"]),
            "ordinal": int(event["ordinal"]),
            "op": str(event["op"]),
            "deck_id": event.get("deck_id"),
            "at_beat": event.get("at_beat"),
            "from_beat": event.get("from_beat"),
            "to_beat": event.get("to_beat"),
            "status": "pending",
            "details": {},
        }
        for event in bound_score["events"]
    }
    decks_by_id = {str(deck["deck_id"]): deck for deck in bound_score["decks"]}
    transports: dict[str, np.ndarray] = {}
    final_states: dict[str, dict[str, Any]] = {}
    for deck_id, deck in decks_by_id.items():
        state = _mixscore_new_deck_state(deck)
        deck_audio = np.zeros((total_frames, 2), dtype=np.float32)
        events = [
            event
            for event in bound_score["events"]
            if str(event.get("deck_id") or "") == deck_id and str(event["op"]) in MIX_TRANSPORT_OPERATIONS
        ]
        events.sort(key=lambda row: (float(row["at_beat"]), int(row["ordinal"])))
        cursor = 0
        index = 0
        while index < len(events):
            event_frame = min(
                total_frames,
                _mixscore_beat_to_frame(events[index]["at_beat"], sample_rate=sample_rate, bpm=bpm),
            )
            if event_frame < cursor:
                raise MixScoreError(f"deck {deck_id} transport events are not time ordered")
            deck_audio[cursor:event_frame] = _mixscore_render_transport_span(
                state,
                loaded_assets,
                output_frames=event_frame - cursor,
                master_bpm=bpm,
            )
            cursor = event_frame
            same_frame: list[Mapping[str, Any]] = []
            while index < len(events):
                frame = min(
                    total_frames,
                    _mixscore_beat_to_frame(events[index]["at_beat"], sample_rate=sample_rate, bpm=bpm),
                )
                if frame != event_frame:
                    break
                same_frame.append(events[index])
                index += 1
            for event in same_frame:
                details = _mixscore_apply_transport_event(
                    state,
                    event,
                    loaded_assets,
                    sample_rate=sample_rate,
                )
                details["master_frame"] = event_frame
                _mixscore_mark_event(ledger_rows, event, details=details)
        deck_audio[cursor:total_frames] = _mixscore_render_transport_span(
            state,
            loaded_assets,
            output_frames=total_frames - cursor,
            master_bpm=bpm,
        )
        transports[deck_id] = deck_audio
        final_states[deck_id] = {
            "asset_id": state.get("asset_id"),
            "source_frame": int(round(float(state["source_frame"]))),
            "playing": bool(state["playing"]),
            "sync": bool(state["sync"]),
            "rate": float(state["rate"]),
            "loop_active": state.get("loop") is not None,
        }

    crossfader_events = [
        event for event in bound_score["events"] if str(event["op"]) in MIX_MASTER_AUTOMATION_OPERATIONS
    ]
    crossfader = _mixscore_build_crossfader_envelope(
        crossfader_events,
        ledger_rows,
        total_frames=total_frames,
        sample_rate=sample_rate,
        bpm=bpm,
    )

    stems: dict[str, np.ndarray] = {}
    deck_receipts: list[dict[str, Any]] = []
    master_gain = np.float32(_mixscore_db_to_gain(float(bound_score["master_gain_db"])))
    for deck_id, deck in decks_by_id.items():
        automation_events = [
            event
            for event in bound_score["events"]
            if str(event.get("deck_id") or "") == deck_id
            and str(event["op"]) in MIX_DECK_AUTOMATION_OPERATIONS
        ]
        gain, muted, pan = _mixscore_build_gain_envelope(
            deck,
            automation_events,
            ledger_rows,
            total_frames=total_frames,
            sample_rate=sample_rate,
            bpm=bpm,
        )
        side_gain = _mixscore_crossfader_side_gain(crossfader, str(deck["crossfader_side"]))
        total_gain = gain * side_gain * (~muted).astype(np.float32) * master_gain
        rendered = _mixscore_apply_pan(transports[deck_id], pan)
        rendered *= total_gain[:, None]
        stems[deck_id] = rendered.astype(np.float32)
        deck_receipts.append(
            {
                "deck_id": deck_id,
                "crossfader_side": str(deck["crossfader_side"]),
                "active_frame_count": int(np.count_nonzero(np.max(np.abs(rendered), axis=1) > 1e-8)),
                "peak_before_master_scale": round(float(np.max(np.abs(rendered))) if rendered.size else 0.0, 9),
                "pcm_f32le_sha256_before_master_scale": _mixscore_pcm_sha256(rendered),
                "final_transport_state": final_states[deck_id],
            }
        )

    stack = np.stack([stems[deck_id] for deck_id in decks_by_id], axis=0)
    master = np.sum(stack.astype(np.float64), axis=0).astype(np.float32)
    peak_before = float(np.max(np.abs(master))) if master.size else 0.0
    peak_ceiling = float(bound_score["peak_ceiling"])
    scale = min(1.0, peak_ceiling / peak_before) if peak_before > 0.0 else 1.0
    if scale < 1.0:
        scale32 = np.float32(scale)
        for deck_id in stems:
            stems[deck_id] = (stems[deck_id] * scale32).astype(np.float32)
        stack = np.stack([stems[deck_id] for deck_id in decks_by_id], axis=0)
        master = np.sum(stack.astype(np.float64), axis=0).astype(np.float32)
    reconciled = np.sum(
        np.stack([stems[deck_id] for deck_id in decks_by_id], axis=0).astype(np.float64),
        axis=0,
    ).astype(np.float32)
    reconciliation_error = float(np.max(np.abs(reconciled - master))) if master.size else 0.0
    if reconciliation_error > 1e-7:
        raise MixScoreError(f"deck stems do not reconcile to master: max error {reconciliation_error}")

    execution = _mixscore_execution_ledger(bound_score, ledger_rows)
    asset_receipts = [
        {
            "asset_id": asset_id,
            "score_path": str(loaded["asset"]["path"]),
            "file_sha256": str(loaded["file_sha256"]),
            "decoded_pcm_f32le_sha256": str(loaded["decoded_pcm_f32le_sha256"]),
            "frames": int(loaded["frames"]),
            "duration_seconds": float(loaded["duration_seconds"]),
        }
        for asset_id, loaded in sorted(loaded_assets.items())
    ]
    receipt = {
        "schema_version": MIX_RENDER_RECEIPT_SCHEMA_VERSION,
        "kind": MIX_RENDER_RECEIPT_KIND,
        "complete": True,
        "input_score_sha256": str(input_score["score_sha256"]),
        "bound_score_sha256": str(bound_score["score_sha256"]),
        "execution_ledger_sha256": str(execution["ledger_sha256"]),
        "sample_rate": sample_rate,
        "channels": 2,
        "frames": total_frames,
        "duration_seconds": round(total_frames / sample_rate, 9),
        "deck_count": len(decks_by_id),
        "asset_count": len(loaded_assets),
        "selected_event_count": int(execution["selected_event_count"]),
        "executed_event_count": int(execution["executed_event_count"]),
        "refused_event_count": 0,
        "assets": asset_receipts,
        "decks": deck_receipts,
        "peak_before_master_scale": round(peak_before, 9),
        "peak_after_master_scale": round(float(np.max(np.abs(master))) if master.size else 0.0, 9),
        "master_scale": round(scale, 12),
        "stem_reconciliation_max_abs": round(reconciliation_error, 12),
        "master_pcm_f32le_sha256": _mixscore_pcm_sha256(master),
        "stem_pcm_f32le_sha256": {
            deck_id: _mixscore_pcm_sha256(stems[deck_id]) for deck_id in sorted(stems)
        },
        "requires_network": False,
        "requires_cloud": False,
        "requires_gpu": False,
    }
    receipt["receipt_sha256"] = mixscore_sha256_json(receipt)
    return {
        "audio": master,
        "stems": stems,
        "input_score": input_score,
        "sealed_score": bound_score,
        "execution_ledger": execution,
        "receipt": receipt,
    }


def _mixscore_atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    except Exception:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise


def _mixscore_atomic_wav(path: Path, audio: np.ndarray, sample_rate: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    os.close(fd)
    try:
        sf.write(
            temp_name,
            np.asarray(audio, dtype=np.float32),
            int(sample_rate),
            subtype="FLOAT",
            format="WAV",
        )
        with open(temp_name, "rb") as handle:
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    except Exception:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise


def mixscore_render_to_files(
    score_or_path: Mapping[str, Any] | str | Path,
    output_path: str | Path,
    *,
    stems_dir: str | Path | None = None,
    base_dir: str | Path | None = None,
) -> dict[str, Any]:
    if isinstance(score_or_path, Mapping):
        score = deepcopy(dict(score_or_path))
        root = Path(base_dir or ".").expanduser().resolve()
    else:
        score, root = mixscore_load(score_or_path)
    result = mixscore_render(score, base_dir=root)
    output = Path(output_path).expanduser().resolve()
    sample_rate = int(result["receipt"]["sample_rate"])
    stem_root = Path(stems_dir).expanduser().resolve() if stems_dir is not None else output.parent / f"{output.stem}.stems"
    stem_paths = {
        deck_id: stem_root / f"{_mixscore_safe_name(deck_id)}.wav" for deck_id in sorted(result["stems"])
    }
    base = output.with_suffix("")
    score_path = base.with_name(base.name + ".mixscore.sealed.json")
    ledger_path = base.with_name(base.name + ".events.json")
    receipt_path = base.with_name(base.name + ".receipt.json")

    for deck_id, path in stem_paths.items():
        _mixscore_atomic_wav(path, result["stems"][deck_id], sample_rate)
    _mixscore_atomic_wav(output, result["audio"], sample_rate)
    _mixscore_atomic_json(score_path, result["sealed_score"])
    _mixscore_atomic_json(ledger_path, result["execution_ledger"])
    _mixscore_atomic_json(receipt_path, result["receipt"])
    return {
        "ok": True,
        "complete": True,
        "output_path": str(output),
        "stems_dir": str(stem_root),
        "stem_paths": {deck_id: str(path) for deck_id, path in stem_paths.items()},
        "sealed_score_path": str(score_path),
        "execution_ledger_path": str(ledger_path),
        "receipt_path": str(receipt_path),
        "receipt": deepcopy(result["receipt"]),
    }


def _mixscore_synthesize_demo_asset(
    *,
    sample_rate: int,
    bpm: float,
    beats: int,
    identity: str,
) -> np.ndarray:
    frames_per_beat = sample_rate * 60.0 / bpm
    total_frames = int(math.ceil(beats * frames_per_beat))
    time = np.arange(total_frames, dtype=np.float64) / sample_rate
    audio = np.zeros((total_frames, 2), dtype=np.float64)
    rng = np.random.default_rng(1701 if identity == "A" else 2027)
    for beat in range(beats):
        start = int(round(beat * frames_per_beat))
        end = min(total_frames, start + int(round(frames_per_beat * 0.72)))
        if end <= start:
            continue
        local = np.arange(end - start, dtype=np.float64) / sample_rate
        if identity == "A":
            kick = np.sin(2.0 * math.pi * (78.0 - 36.0 * np.minimum(local / 0.18, 1.0)) * local)
            kick *= np.exp(-local * 18.0)
            bass_frequency = (55.0, 65.406, 73.416, 82.407)[(beat // 4) % 4]
            bass = 0.22 * np.sin(2.0 * math.pi * bass_frequency * local) * np.exp(-local * 3.0)
            signal = 0.62 * kick + bass
            audio[start:end, 0] += signal
            audio[start:end, 1] += signal * 0.92
        else:
            notes = (220.0, 277.183, 329.628, 369.994, 329.628, 277.183, 246.942, 277.183)
            frequency = notes[beat % len(notes)]
            envelope = np.minimum(1.0, local * 70.0) * np.exp(-local * 4.8)
            voice = (
                np.sin(2.0 * math.pi * frequency * local)
                + 0.34 * np.sin(2.0 * math.pi * frequency * 2.0 * local)
                + 0.16 * np.sin(2.0 * math.pi * frequency * 3.0 * local)
            )
            noise = rng.normal(0.0, 1.0, end - start) * np.exp(-local * 28.0)
            signal = 0.25 * voice * envelope + 0.04 * noise
            audio[start:end, 0] += signal * 0.82
            audio[start:end, 1] += signal
    if identity == "A":
        audio[:, 0] += 0.035 * np.sin(2.0 * math.pi * 110.0 * time)
        audio[:, 1] += 0.032 * np.sin(2.0 * math.pi * 110.0 * time)
    peak = float(np.max(np.abs(audio)))
    if peak > 0.88:
        audio *= 0.88 / peak
    return audio.astype(np.float32)


def mixscore_build_demo(
    output_dir: str | Path,
    *,
    sample_rate: int = 24_000,
) -> dict[str, Any]:
    root = Path(output_dir).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    asset_a_path = root / "demo-deck-a.wav"
    asset_b_path = root / "demo-deck-b.wav"
    _mixscore_atomic_wav(
        asset_a_path,
        _mixscore_synthesize_demo_asset(sample_rate=sample_rate, bpm=120.0, beats=40, identity="A"),
        sample_rate,
    )
    _mixscore_atomic_wav(
        asset_b_path,
        _mixscore_synthesize_demo_asset(sample_rate=sample_rate, bpm=114.0, beats=40, identity="B"),
        sample_rate,
    )
    score = mixscore_seal(
        {
            "schema_version": 1,
            "kind": "earcrate_mix_score",
            "title": "EarCrate two-deck transport proof",
            "clock": {"bpm": 120.0, "beats_per_bar": 4, "sample_rate": int(sample_rate)},
            "end_beat": 24.0,
            "peak_ceiling": 0.92,
            "master_gain_db": -1.0,
            "assets": [
                {
                    "asset_id": "deck-a-source",
                    "path": asset_a_path.name,
                    "source_bpm": 120.0,
                    "downbeat_seconds": 0.0,
                    "cues": {"start": 0.0, "hook": 16.0, "break": 24.0},
                    "expected_file_sha256": _mixscore_file_sha256(asset_a_path),
                },
                {
                    "asset_id": "deck-b-source",
                    "path": asset_b_path.name,
                    "source_bpm": 114.0,
                    "downbeat_seconds": 0.0,
                    "cues": {"start": 0.0, "verse": 4.0, "hook": 24.0},
                    "expected_file_sha256": _mixscore_file_sha256(asset_b_path),
                },
            ],
            "decks": [
                {"deck_id": "A", "crossfader_side": "A", "gain_db": -1.5, "pan": -0.08},
                {"deck_id": "B", "crossfader_side": "B", "gain_db": -2.0, "pan": 0.08},
            ],
            "events": [
                {"at_beat": 0.0, "deck_id": "A", "op": "load", "asset_id": "deck-a-source"},
                {"at_beat": 0.0, "deck_id": "A", "op": "play", "cue": "start", "sync": True},
                {"at_beat": 0.0, "deck_id": "B", "op": "load", "asset_id": "deck-b-source"},
                {"at_beat": 0.0, "op": "set_crossfader", "position": -1.0},
                {"at_beat": 4.0, "deck_id": "B", "op": "play", "cue": "verse", "sync": True},
                {
                    "from_beat": 4.0,
                    "to_beat": 8.0,
                    "deck_id": "B",
                    "op": "fade",
                    "from_db": -120.0,
                    "to_db": -3.0,
                    "curve": "equal_power",
                },
                {
                    "from_beat": 4.0,
                    "to_beat": 8.0,
                    "op": "crossfade",
                    "from_position": -1.0,
                    "to_position": 0.0,
                    "curve": "s_curve",
                },
                {"at_beat": 8.0, "deck_id": "A", "op": "jump", "cue": "hook"},
                {
                    "at_beat": 12.0,
                    "deck_id": "B",
                    "op": "loop",
                    "source_beat": 12.0,
                    "length_beats": 2.0,
                    "crossfade_ms": 8.0,
                },
                {
                    "from_beat": 12.0,
                    "to_beat": 16.0,
                    "op": "crossfade",
                    "from_position": 0.0,
                    "to_position": 0.45,
                    "curve": "s_curve",
                },
                {"at_beat": 16.0, "deck_id": "B", "op": "exit_loop"},
                {"at_beat": 16.0, "deck_id": "A", "op": "set_gain", "gain_db": -3.0},
                {"at_beat": 20.0, "deck_id": "A", "op": "cut"},
                {"at_beat": 20.0, "deck_id": "B", "op": "jump", "cue": "hook"},
                {
                    "from_beat": 20.0,
                    "to_beat": 22.0,
                    "op": "crossfade",
                    "from_position": 0.45,
                    "to_position": 1.0,
                    "curve": "s_curve",
                },
            ],
            "metadata": {
                "purpose": "exercise simultaneous playback, tempo sync, fade, hard cut, cue jump, loop, and crossfader",
                "synthetic_sources": True,
            },
        }
    )
    score_path = root / "demo.mixscore.json"
    _mixscore_atomic_json(score_path, score)
    rendered = mixscore_render_to_files(
        score_path,
        root / "demo-mix.wav",
        stems_dir=root / "demo-stems",
    )
    return {
        "ok": True,
        "complete": True,
        "demo_dir": str(root),
        "score_path": str(score_path),
        "asset_paths": [str(asset_a_path), str(asset_b_path)],
        **rendered,
    }
