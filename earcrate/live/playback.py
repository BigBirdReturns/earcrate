from __future__ import annotations

from copy import deepcopy
from typing import Any, Callable, Mapping

import numpy as np

from earcrate.live.capabilities import live_audio_device_capability
from earcrate.live.instrumentation import (
    LiveActivityRecorder,
    live_activity_context,
    live_activity_pop,
    live_activity_swap,
)
from earcrate.live.model import LiveError
from earcrate.live.stream import live_validate_phrase_receipt
from earcrate.midi.model import midi_sha256_json

LIVE_AUDIO_CALLBACK_SCHEMA_VERSION = 1
LIVE_AUDIO_CALLBACK_KIND = "earcrate_live_audio_callback"


class LiveAudioCallback:
    """Single-producer/single-consumer fixed-ring callback.

    Queue validation and PCM copying into owned phrase buffers happen on the
    producer thread. The callback uses no lock, performs no library or planning
    work, and writes completion data into a fixed ring rather than appending to
    an unbounded Python list.
    """

    def __init__(
        self,
        *,
        sample_rate: int,
        block_frames: int = 512,
        maximum_queued_phrases: int = 2,
        completion_capacity: int = 128,
        activity_recorder: LiveActivityRecorder | None = None,
    ):
        if int(sample_rate) <= 0 or int(block_frames) <= 0:
            raise LiveError("live audio callback requires positive sample rate and block size")
        if int(maximum_queued_phrases) <= 0:
            raise LiveError("live audio callback queue capacity must be positive")
        if int(completion_capacity) <= 0:
            raise LiveError("live audio completion capacity must be positive")
        self.sample_rate = int(sample_rate)
        self.block_frames = int(block_frames)
        self.maximum_queued_phrases = int(maximum_queued_phrases)
        self.completion_capacity = int(completion_capacity)
        self.activity_recorder = activity_recorder or LiveActivityRecorder()
        self._callback_activity_context = live_activity_context(
            self.activity_recorder,
            "audio_callback",
        )

        self._queue_audio: list[np.ndarray | None] = [None] * self.maximum_queued_phrases
        self._queue_receipts: list[dict[str, Any] | None] = [None] * self.maximum_queued_phrases
        self._write_sequence = 0
        self._read_sequence = 0

        self._current_audio: np.ndarray | None = None
        self._current_receipt: dict[str, Any] | None = None
        self._offset = 0
        self._callback_count = 0
        self._frames_requested = 0
        self._frames_from_phrases = 0
        self._silence_frames = 0
        self._underrun_count = 0

        self._completion_hashes: list[str | None] = [None] * self.completion_capacity
        self._completion_frames: list[int] = [0] * self.completion_capacity
        self._completion_callback_indices: list[int] = [0] * self.completion_capacity
        self._completion_count = 0

    def _occupied_phrase_count(self) -> int:
        queued = self._write_sequence - self._read_sequence
        return queued + (1 if self._current_audio is not None else 0)

    def queue_phrase(self, phrase: Mapping[str, Any]) -> dict[str, Any]:
        audio = np.asarray(phrase.get("audio"), dtype=np.float32)
        receipt = phrase.get("receipt")
        if audio.ndim != 2 or audio.shape[1] != 2 or audio.shape[0] <= 0:
            raise LiveError("queued live phrase must contain nonempty stereo float PCM")
        if not np.isfinite(audio).all():
            raise LiveError("queued live phrase contains non-finite PCM")
        if not isinstance(receipt, Mapping):
            raise LiveError("queued live phrase requires a receipt")
        live_validate_phrase_receipt(receipt)
        if int(receipt["sample_rate"]) != self.sample_rate:
            raise LiveError("queued live phrase sample rate does not match device callback")
        if int(receipt["frames"]) != int(audio.shape[0]):
            raise LiveError("queued live phrase frame count disagrees with receipt")
        if self._occupied_phrase_count() >= self.maximum_queued_phrases:
            raise LiveError("live audio callback queue is full")

        prepared_audio = np.asarray(audio, dtype=np.float32, order="C").copy()
        prepared_receipt = deepcopy(dict(receipt))
        slot = self._write_sequence % self.maximum_queued_phrases
        if self._queue_audio[slot] is not None or self._queue_receipts[slot] is not None:
            raise LiveError("live audio SPSC queue slot was not released")
        self._queue_audio[slot] = prepared_audio
        self._queue_receipts[slot] = prepared_receipt
        self._write_sequence += 1
        return {
            "phrase_sha256": str(prepared_receipt["phrase_sha256"]),
            "queued_position": self._write_sequence - self._read_sequence,
            "frames": int(prepared_audio.shape[0]),
        }

    def _take_next_phrase(self) -> bool:
        if self._read_sequence >= self._write_sequence:
            return False
        slot = self._read_sequence % self.maximum_queued_phrases
        audio = self._queue_audio[slot]
        receipt = self._queue_receipts[slot]
        if audio is None or receipt is None:
            raise LiveError("live audio SPSC queue published an incomplete slot")
        self._current_audio = audio
        self._current_receipt = receipt
        self._queue_audio[slot] = None
        self._queue_receipts[slot] = None
        self._read_sequence += 1
        self._offset = 0
        return True

    def _record_completion(self) -> None:
        assert self._current_audio is not None and self._current_receipt is not None
        slot = self._completion_count % self.completion_capacity
        self._completion_hashes[slot] = str(self._current_receipt["phrase_sha256"])
        self._completion_frames[slot] = int(self._current_audio.shape[0])
        self._completion_callback_indices[slot] = self._callback_count
        self._completion_count += 1

    def render_into(self, outdata: np.ndarray) -> None:
        target = np.asarray(outdata)
        if target.ndim != 2 or target.shape[1] != 2:
            raise LiveError("audio callback output buffer must be stereo")
        if target.dtype != np.float32:
            raise LiveError("audio callback output buffer must be float32")
        previous = live_activity_swap(self._callback_activity_context)
        try:
            target.fill(0.0)
            requested = int(target.shape[0])
            written = 0
            while written < requested:
                if self._current_audio is None or self._offset >= int(self._current_audio.shape[0]):
                    if self._current_audio is not None:
                        self._record_completion()
                        self._current_audio = None
                        self._current_receipt = None
                        self._offset = 0
                    if not self._take_next_phrase():
                        self._underrun_count += 1
                        self._silence_frames += requested - written
                        break
                assert self._current_audio is not None
                available = min(requested - written, int(self._current_audio.shape[0]) - self._offset)
                target[written : written + available] = self._current_audio[
                    self._offset : self._offset + available
                ]
                self._offset += available
                written += available
                self._frames_from_phrases += available
            self._callback_count += 1
            self._frames_requested += requested
        finally:
            live_activity_pop(previous)

    def callback(self, outdata: np.ndarray, frames: int, time_info: Any = None, status: Any = None) -> None:
        if int(frames) != int(outdata.shape[0]):
            raise LiveError("audio backend frame count disagrees with callback buffer")
        self.render_into(outdata)

    def receipt(self) -> dict[str, Any]:
        queued = []
        for sequence in range(self._read_sequence, self._write_sequence):
            receipt = self._queue_receipts[sequence % self.maximum_queued_phrases]
            if receipt is not None:
                queued.append(str(receipt["phrase_sha256"]))
        retained = min(self._completion_count, self.completion_capacity)
        start = max(0, self._completion_count - retained)
        completions = []
        for ordinal in range(start, self._completion_count):
            slot = ordinal % self.completion_capacity
            completions.append(
                {
                    "ordinal": ordinal,
                    "phrase_sha256": self._completion_hashes[slot],
                    "frames": self._completion_frames[slot],
                    "callback_index": self._completion_callback_indices[slot],
                }
            )
        activity = self.activity_recorder.snapshot()
        callback_activity = activity["domains"]["audio_callback"]
        value = {
            "schema_version": LIVE_AUDIO_CALLBACK_SCHEMA_VERSION,
            "kind": LIVE_AUDIO_CALLBACK_KIND,
            "sample_rate": self.sample_rate,
            "block_frames": self.block_frames,
            "maximum_queued_phrases": self.maximum_queued_phrases,
            "queue_model": "single_producer_single_consumer_fixed_ring",
            "callback_count": self._callback_count,
            "frames_requested": self._frames_requested,
            "frames_from_phrases": self._frames_from_phrases,
            "silence_frames": self._silence_frames,
            "underrun_count": self._underrun_count,
            "current_phrase_sha256": None if self._current_receipt is None else str(self._current_receipt["phrase_sha256"]),
            "current_phrase_remaining_frames": 0 if self._current_audio is None else int(self._current_audio.shape[0]) - self._offset,
            "queued_phrase_sha256s": queued,
            "completed_phrase_count": self._completion_count,
            "completion_capacity": self.completion_capacity,
            "completed_phrases": completions,
            "activity_receipt": activity,
            "callback_planning_count": int(callback_activity["planning"]),
            "callback_library_search_count": int(callback_activity["library_search"]),
            "callback_sample_decode_count": int(callback_activity["sample_decode"]),
            "callback_binding_count": int(callback_activity["binding"]),
        }
        value["callback_sha256"] = midi_sha256_json(value)
        return value


class LiveSoundDevicePlayer:
    """Optional sounddevice host around `LiveAudioCallback`."""

    def __init__(
        self,
        callback_engine: LiveAudioCallback,
        *,
        device: Any = None,
        output_stream_factory: Callable[..., Any] | None = None,
    ):
        self.callback_engine = callback_engine
        if output_stream_factory is None:
            try:
                import sounddevice as sounddevice_module
            except Exception as exc:
                raise LiveError("hardware playback requires the optional sounddevice package") from exc
            output_stream_factory = sounddevice_module.OutputStream
        self._stream = output_stream_factory(
            samplerate=callback_engine.sample_rate,
            blocksize=callback_engine.block_frames,
            channels=2,
            dtype="float32",
            device=device,
            callback=callback_engine.callback,
        )

    def start(self) -> None:
        self._stream.start()

    def stop(self) -> None:
        self._stream.stop()

    def close(self) -> None:
        self._stream.close()

    def __enter__(self) -> "LiveSoundDevicePlayer":
        self.start()
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        try:
            self.stop()
        finally:
            self.close()
