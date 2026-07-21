from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping

from earcrate.live.crate import live_validate_crate_atlas
from earcrate.live.engine import live_engine_new
from earcrate.live.instrumentation import LiveActivityRecorder, live_activity_delta
from earcrate.live.model import LiveError, live_validate_state
from earcrate.live.playback import LiveAudioCallback, LiveSoundDevicePlayer
from earcrate.live.stream import live_render_next_phrase
from earcrate.midi.model import midi_sha256_json

LIVE_PERFORMANCE_HOST_SCHEMA_VERSION = 1
LIVE_PERFORMANCE_HOST_KIND = "earcrate_live_performance_host"


class LivePerformanceEngine:
    """Coordinate measured phrase planning and callback-safe playback."""

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
        activity_event_capacity: int = 512,
        preparation_history_capacity: int = 128,
    ):
        live_validate_crate_atlas(crate_atlas, verify_sources=True)
        if int(preparation_history_capacity) <= 0:
            raise LiveError("live preparation history capacity must be positive")
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
        self.activity_recorder = LiveActivityRecorder(event_capacity=activity_event_capacity)
        self.callback = LiveAudioCallback(
            sample_rate=int(crate_atlas["sample_rate"]),
            block_frames=int(block_frames),
            maximum_queued_phrases=int(maximum_queued_phrases),
            activity_recorder=self.activity_recorder,
        )
        self.beam_width = int(beam_width)
        self.candidate_limit = int(candidate_limit)
        self.target_bpm = float(target_bpm)
        self.target_peak = float(target_peak)
        self.pending_controls: list[dict[str, Any]] = []
        self.preparation_history_capacity = int(preparation_history_capacity)
        self._preparation_history: list[dict[str, Any] | None] = [None] * self.preparation_history_capacity
        self._preparation_count = 0
        self._player: LiveSoundDevicePlayer | None = None

    @property
    def planning_count(self) -> int:
        return int(self.activity_recorder.snapshot()["totals"]["planning"])

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

    def _store_preparation(self, summary: Mapping[str, Any]) -> None:
        slot = self._preparation_count % self.preparation_history_capacity
        self._preparation_history[slot] = deepcopy(dict(summary))
        self._preparation_count += 1

    def _preparation_history_receipt(self) -> list[dict[str, Any]]:
        retained = min(self._preparation_count, self.preparation_history_capacity)
        start = max(0, self._preparation_count - retained)
        rows = []
        for ordinal in range(start, self._preparation_count):
            value = self._preparation_history[ordinal % self.preparation_history_capacity]
            if value is not None:
                rows.append({"ordinal": ordinal, **deepcopy(value)})
        return rows

    def prepare_next_phrase(self) -> dict[str, Any]:
        controls = [
            {key: deepcopy(value) for key, value in row.items() if key != "control_queue_id"}
            for row in self.pending_controls
        ]
        state_before = deepcopy(self.state)
        activity_before = self.activity_recorder.snapshot()
        prepared = live_render_next_phrase(
            self.crate_atlas,
            state_before,
            controls=controls,
            beam_width=self.beam_width,
            candidate_limit=self.candidate_limit,
            target_bpm=self.target_bpm,
            target_peak=self.target_peak,
            activity_recorder=self.activity_recorder,
        )
        queue_receipt = self.callback.queue_phrase(prepared)
        self.state = deepcopy(prepared["next_state"])
        self.pending_controls.clear()
        activity = live_activity_delta(activity_before, self.activity_recorder.snapshot())
        callback_activity = activity["domains"]["audio_callback"]
        summary = {
            "planning_index": self._preparation_count + 1,
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
            "activity_delta": activity,
            "library_materials_scanned": int(activity["totals"]["library_search"]),
            "callback_planning_count": int(callback_activity["planning"]),
            "callback_library_search_count": int(callback_activity["library_search"]),
            "callback_sample_decode_count": int(callback_activity["sample_decode"]),
            "callback_binding_count": int(callback_activity["binding"]),
        }
        summary["preparation_sha256"] = midi_sha256_json(summary)
        self._store_preparation(summary)
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
        activity = self.activity_recorder.snapshot()
        value = {
            "schema_version": LIVE_PERFORMANCE_HOST_SCHEMA_VERSION,
            "kind": LIVE_PERFORMANCE_HOST_KIND,
            "crate_atlas_sha256": str(self.crate_atlas["crate_atlas_sha256"]),
            "current_state": deepcopy(self.state),
            "pending_controls": deepcopy(self.pending_controls),
            "planning_count": int(activity["totals"]["planning"]),
            "preparation_count": self._preparation_count,
            "preparation_history_capacity": self.preparation_history_capacity,
            "prepared_phrases": self._preparation_history_receipt(),
            "audio_callback": self.callback.receipt(),
            "device_open": self._player is not None,
            "activity_receipt": activity,
            "library_scans_after_initialization": int(activity["totals"]["library_search"]),
        }
        value["host_sha256"] = midi_sha256_json(value)
        return value
