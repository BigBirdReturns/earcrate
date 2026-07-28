from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any, Mapping

from earcrate.mix.audio import (
    _mixscore_circular_loop_audio,
    _mixscore_decode_stereo,
    _mixscore_file_sha256,
    _mixscore_pcm_sha256,
    _mixscore_resolve_path,
    _mixscore_source_beat_to_frame,
)
from earcrate.mix.model import MixScoreError, mixscore_seal


def _mixscore_load_assets(
    score: Mapping[str, Any],
    *,
    base_dir: Path,
) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    sample_rate = int(score["clock"]["sample_rate"])
    loaded: dict[str, dict[str, Any]] = {}
    bound = deepcopy(dict(score))
    bound.pop("score_sha256", None)
    bound_assets = {str(row["asset_id"]): row for row in bound["assets"]}
    for asset in score["assets"]:
        asset_id = str(asset["asset_id"])
        path = _mixscore_resolve_path(str(asset["path"]), base_dir)
        file_sha_before = _mixscore_file_sha256(path)
        expected = str(asset.get("expected_file_sha256") or "")
        if expected and expected != file_sha_before:
            raise MixScoreError(
                f"asset {asset_id} source identity changed: expected {expected}, found {file_sha_before}"
            )
        audio = _mixscore_decode_stereo(path, sample_rate)
        file_sha_after = _mixscore_file_sha256(path)
        if file_sha_before != file_sha_after:
            raise MixScoreError(f"asset {asset_id} changed while it was being decoded")
        bound_assets[asset_id]["expected_file_sha256"] = file_sha_before
        loaded[asset_id] = {
            "asset": deepcopy(dict(asset)),
            "path": path,
            "audio": audio,
            "file_sha256": file_sha_before,
            "decoded_pcm_f32le_sha256": _mixscore_pcm_sha256(audio),
            "frames": int(audio.shape[0]),
            "duration_seconds": round(audio.shape[0] / sample_rate, 9),
        }
    return loaded, mixscore_seal(bound)

def _mixscore_event_source_beat(event: Mapping[str, Any], loaded: Mapping[str, Any]) -> float | None:
    has_cue = bool(str(event.get("cue") or ""))
    has_beat = event.get("source_beat") is not None
    if has_cue and has_beat:
        raise MixScoreError(f"event {event['event_id']} cannot specify both cue and source_beat")
    if has_cue:
        cue = str(event["cue"])
        cues = loaded["asset"]["cues"]
        if cue not in cues:
            raise MixScoreError(
                f"event {event['event_id']} references missing cue {cue!r} on asset {loaded['asset']['asset_id']}"
            )
        return float(cues[cue])
    if has_beat:
        return float(event["source_beat"])
    return None

def _mixscore_new_deck_state(deck: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "deck_id": str(deck["deck_id"]),
        "asset_id": None,
        "source_frame": 0.0,
        "playing": False,
        "sync": True,
        "rate": 1.0,
        "loop": None,
    }

def _mixscore_require_loaded(state: Mapping[str, Any], event: Mapping[str, Any]) -> str:
    asset_id = str(state.get("asset_id") or "")
    if not asset_id:
        raise MixScoreError(f"event {event['event_id']} requires a loaded asset on deck {state['deck_id']}")
    return asset_id

def _mixscore_apply_transport_event(
    state: dict[str, Any],
    event: Mapping[str, Any],
    loaded_assets: Mapping[str, Mapping[str, Any]],
    *,
    sample_rate: int,
) -> dict[str, Any]:
    op = str(event["op"])
    details: dict[str, Any] = {"deck_id": str(state["deck_id"])}
    if op == "load":
        asset_id = str(event["asset_id"])
        state.update(
            {
                "asset_id": asset_id,
                "source_frame": float(
                    _mixscore_source_beat_to_frame(loaded_assets[asset_id]["asset"], 0.0, sample_rate)
                ),
                "playing": False,
                "sync": True,
                "rate": 1.0,
                "loop": None,
            }
        )
        details.update({"asset_id": asset_id, "source_beat": 0.0})
    elif op == "play":
        if event.get("asset_id"):
            asset_id = str(event["asset_id"])
            state["asset_id"] = asset_id
            state["source_frame"] = float(
                _mixscore_source_beat_to_frame(loaded_assets[asset_id]["asset"], 0.0, sample_rate)
            )
            state["loop"] = None
        asset_id = _mixscore_require_loaded(state, event)
        loaded = loaded_assets[asset_id]
        source_beat = _mixscore_event_source_beat(event, loaded)
        if source_beat is not None:
            state["source_frame"] = float(
                _mixscore_source_beat_to_frame(loaded["asset"], source_beat, sample_rate)
            )
            state["loop"] = None
        state["sync"] = bool(event.get("sync", True))
        state["rate"] = float(event.get("rate", 1.0))
        state["playing"] = True
        details.update(
            {
                "asset_id": asset_id,
                "source_beat": source_beat,
                "sync": bool(state["sync"]),
                "rate": float(state["rate"]),
            }
        )
    elif op in {"stop", "cut"}:
        _mixscore_require_loaded(state, event)
        if not bool(state["playing"]):
            raise MixScoreError(f"event {event['event_id']} cannot {op} an already stopped deck")
        state["playing"] = False
        details["hard_edge"] = op == "cut"
    elif op in {"jump", "seek"}:
        asset_id = _mixscore_require_loaded(state, event)
        loaded = loaded_assets[asset_id]
        source_beat = _mixscore_event_source_beat(event, loaded)
        if source_beat is None:
            raise MixScoreError(f"event {event['event_id']} requires cue or source_beat")
        state["source_frame"] = float(
            _mixscore_source_beat_to_frame(loaded["asset"], source_beat, sample_rate)
        )
        state["loop"] = None
        details.update({"asset_id": asset_id, "source_beat": source_beat})
    elif op == "loop":
        asset_id = _mixscore_require_loaded(state, event)
        if not bool(state["playing"]):
            raise MixScoreError(f"event {event['event_id']} cannot engage a loop on a stopped deck")
        loaded = loaded_assets[asset_id]
        source_beat = _mixscore_event_source_beat(event, loaded)
        if source_beat is None:
            start_frame = int(round(float(state["source_frame"])))
            source_beat = (
                (start_frame / sample_rate - float(loaded["asset"]["downbeat_seconds"]))
                * float(loaded["asset"]["source_bpm"])
                / 60.0
            )
        else:
            start_frame = _mixscore_source_beat_to_frame(loaded["asset"], source_beat, sample_rate)
        end_source_beat = float(source_beat) + float(event["length_beats"])
        end_frame = _mixscore_source_beat_to_frame(loaded["asset"], end_source_beat, sample_rate)
        if start_frame < 0 or end_frame > int(loaded["frames"]):
            raise MixScoreError(f"event {event['event_id']} loop lies outside asset {asset_id}")
        crossfade_frames = int(round(float(event["crossfade_ms"]) * sample_rate / 1000.0))
        state["loop"] = {
            "start_frame": start_frame,
            "end_frame": end_frame,
            "crossfade_frames": crossfade_frames,
            "audio": _mixscore_circular_loop_audio(
                loaded,
                loop_start=start_frame,
                loop_end=end_frame,
                crossfade_frames=crossfade_frames,
            ),
        }
        state["source_frame"] = float(start_frame)
        details.update(
            {
                "asset_id": asset_id,
                "source_beat": round(float(source_beat), 9),
                "length_beats": float(event["length_beats"]),
                "crossfade_frames": crossfade_frames,
            }
        )
    elif op == "exit_loop":
        _mixscore_require_loaded(state, event)
        if state.get("loop") is None:
            raise MixScoreError(f"event {event['event_id']} cannot exit an inactive loop")
        state["loop"] = None
    elif op == "set_rate":
        _mixscore_require_loaded(state, event)
        state["rate"] = float(event["rate"])
        if "sync" in event:
            state["sync"] = bool(event["sync"])
        details.update({"rate": float(state["rate"]), "sync": bool(state["sync"])})
    elif op == "nudge":
        asset_id = _mixscore_require_loaded(state, event)
        loaded = loaded_assets[asset_id]
        if event.get("delta_source_beats") is not None:
            delta_frames = int(
                round(
                    float(event["delta_source_beats"])
                    * sample_rate
                    * 60.0
                    / float(loaded["asset"]["source_bpm"])
                )
            )
        else:
            delta_frames = int(round(float(event["delta_ms"]) * sample_rate / 1000.0))
        next_frame = int(round(float(state["source_frame"]))) + delta_frames
        if state.get("loop") is not None:
            loop = state["loop"]
            length = int(loop["end_frame"]) - int(loop["start_frame"])
            next_frame = int(loop["start_frame"]) + ((next_frame - int(loop["start_frame"])) % length)
        elif not 0 <= next_frame < int(loaded["frames"]):
            raise MixScoreError(f"event {event['event_id']} nudges the deck outside asset {asset_id}")
        state["source_frame"] = float(next_frame)
        details["delta_frames"] = delta_frames
    else:
        raise MixScoreError(f"internal transport dispatcher does not implement {op}")
    details["playing_after"] = bool(state["playing"])
    details["source_frame_after"] = int(round(float(state["source_frame"])))
    return details
