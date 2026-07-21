from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping

from earcrate.live.crate import live_validate_crate_atlas
from earcrate.live.engine import live_engine_new
from earcrate.live.model import LiveError, live_validate_state
from earcrate.live.playback import LiveAudioCallback, LiveSoundDevicePlayer
from earcrate.live.stream import live_render_next_phrase
from earcrate.midi.model import midi_sha256_json

LIVE_PERFORMANCE_HOST_SCHEMA_VERSION = 1
LIVE_PERFORMANCE_HOST_KIND = "earcrate_live_performance_host"


class LivePerformanceEngine:
    """Coordinate phrase planning and callback-safe playback without library scans.

    `prepare_next_phrase` is intentionally a control-thread operation. It applies
    queued controls, plans one legal phrase, binds and renders that phrase from
    the precompiled rack atlas, then queues immutable PCM for the callback. The
    callback never calls this method and never sees the full library.
    """

    def __init__(
        self,
        crate_atlas: Mapping[str, Any],
        *,
        state: Mapping[str, Any] | None = None,
        persona: str = "club",
        seed: int = 1,
        target_energy: float | None = None,
        density: float | None = None,
        risk: float | None = None,
        maximum_layers: int | None = None,
        phrase_bars: int = 0,
        horizon_bars: int = 0,
        block_frames: int = 512,
        maximum_queued_phrases: int = 2,
        beam_width: int = 32,
        candidate_limit: int = 12,
        target_bpm: float = 0.0,
        target_peak: float = 0.90,
    ):
        live_validate_crate_atlas(crate_atlas, verify_sources=True)
        self.crate_atlas = deepcopy(dict(crate_atlas))
        if state is None:
            state = live_engine_new(
                crate_atlas["live_material_atlas"],
                persona=persona,
                seed=seed,
                target_energy=target_energy,
                density=density,
                risk=risk,
                maximum_layers=maximum_layers,
                phrase_bars=phrase_bars,
                horizon_bars=horizon_bars,
            )
        live_validate_state(state)
        if str(state["atlas_sha256"]) != str(crate_atlas["live_atlas_sha256"]):
            raise LiveError("live performance state belongs to another crate atlas")
        self.state = deepcopy(dict(state))
        self.callback = LiveAudioCallback(
            sample_rate=int(crate_atlas["sample_rate"]),
            block_frames=int(block_frames),
            maximum_queued_phrases=int(maximum_queued_phrases),
        )
        self.beam_width = int(beam_width)
        self.candidate_limit = int(candidate_limit)
        self.target_bpm = float(target_bpm)
        self.target_peak = float(target_peak)
        self.pending_controls: list[dict[str, Any]] = []
        self.prepared_phrases: list[dict[str, Any]] = []
        self.planning_count = 0
        self._player: LiveSoundDevicePlayer | None = None

    def queue_control(self, control: Mapping[str, Any]) -> dict[str, Any]:
        command = str(control.get("command") or "").strip()
        if not command:
            raise LiveError("live performance control requires a command")
        row = deepcopy(dict(control))
        row["control_queue_id"] = "live_control_queue_" + midi_sha256_json(
            {
                "state_sha256": self.state["state_sha256"],
                "ordinal": len(self.pending_controls),
                "control": row,
            }
        )[:24]
        self.pending_controls.append(row)
        return {
            "control_queue_id": row["control_queue_id"],
            "pending_control_count": len(self.pending_controls),
        }

    def prepare_next_phrase(self) -> dict[str, Any]:
        controls = [
            {key: deepcopy(value) for key, value in row.items() if key != "control_queue_id"}
            for row in self.pending_controls
        ]
        state_before = deepcopy(self.state)
        prepared = live_render_next_phrase(
            self.crate_atlas,
            state_before,
            controls=controls,
            beam_width=self.beam_width,
            candidate_limit=self.candidate_limit,
            target_bpm=self.target_bpm,
            target_peak=self.target_peak,
        )
        queue_receipt = self.callback.queue_phrase(prepared)
        self.state = deepcopy(prepared["next_state"])
        self.pending_controls.clear()
        self.planning_count += 1
        summary = {
            "planning_index": self.planning_count,
            "state_before_sha256": str(state_before["state_sha256"]),
            "state_after_sha256": str(self.state["state_sha256"]),
            "phrase_sha256": str(prepared["receipt"]["phrase_sha256"]),
            "step_sha256": str(prepared["step"]["step_sha256"]),
            "absolute_start_bar_index": int(prepared["receipt"]["absolute_start_bar_index"]),
            "bars": int(prepared["receipt"]["bars"]),
            "persona": str(prepared["receipt"]["persona"]),
            "operators": list(prepared["receipt"]["operators"]),
            "selected_event_count": int(prepared["receipt"]["selected_event_count"]),
            "queue": queue_receipt,
            "library_materials_scanned": 0,
            "callback_planning_count": 0,
            "callback_library_search_count": 0,
            "callback_sample_decode_count": 0,
            "callback_binding_count": 0,
        }
        summary["preparation_sha256"] = midi_sha256_json(summary)
        self.prepared_phrases.append(summary)
        return summary

    def start_device(self, *, device: Any = None, output_stream_factory: Any = None) -> None:
        if self._player is not None:
            raise LiveError("live audio device is already open")
        player = LiveSoundDevicePlayer(
            self.callback,
            device=device,
            output_stream_factory=output_stream_factory,
        )
        player.start()
        self._player = player

    def stop_device(self) -> None:
        if self._player is None:
            return
        try:
            self._player.stop()
        finally:
            self._player.close()
            self._player = None

    def receipt(self) -> dict[str, Any]:
        value = {
            "schema_version": LIVE_PERFORMANCE_HOST_SCHEMA_VERSION,
            "kind": LIVE_PERFORMANCE_HOST_KIND,
            "crate_atlas_sha256": str(self.crate_atlas["crate_atlas_sha256"]),
            "current_state": deepcopy(self.state),
            "pending_controls": deepcopy(self.pending_controls),
            "planning_count": self.planning_count,
            "prepared_phrases": deepcopy(self.prepared_phrases),
            "audio_callback": self.callback.receipt(),
            "device_open": self._player is not None,
            "library_scans_after_initialization": 0,
            "gpu_calls_after_initialization": 0,
            "network_calls_after_initialization": 0,
            "cloud_calls_after_initialization": 0,
        }
        value["host_sha256"] = midi_sha256_json(value)
        return value
