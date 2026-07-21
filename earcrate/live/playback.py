from __future__ import annotations

import importlib.util
import threading
from collections import deque
from copy import deepcopy
from typing import Any, Callable, Mapping

import numpy as np

from earcrate.live.model import LiveError
from earcrate.live.stream import live_validate_phrase_receipt
from earcrate.midi.model import midi_sha256_json

LIVE_AUDIO_CALLBACK_SCHEMA_VERSION = 1
LIVE_AUDIO_CALLBACK_KIND = "earcrate_live_audio_callback"


def live_audio_device_capability() -> dict[str, Any]:
    return {
        "optional_backend": "sounddevice",
        "sounddevice_ready": importlib.util.find_spec("sounddevice") is not None,
        "requires_gpu": False,
        "requires_network": False,
        "requires_cloud": False,
        "callback_plans": False,
        "callback_searches_library": False,
        "callback_decodes_samples": False,
        "callback_binds_events": False,
    }


class LiveAudioCallback:
    """A callback-safe queue of already-rendered phrase buffers.

    Queue validation happens outside the callback. The callback only swaps a
    prepared buffer under a short lock, copies float32 frames, and emits silence
    with an explicit underrun count when no prepared phrase exists.
    """

    def __init__(self, *, sample_rate: int, block_frames: int = 512, maximum_queued_phrases: int = 2):
        if int(sample_rate) <= 0 or int(block_frames) <= 0:
            raise LiveError("live audio callback requires positive sample rate and block size")
        if int(maximum_queued_phrases) <= 0:
            raise LiveError("live audio callback queue capacity must be positive")
        self.sample_rate = int(sample_rate)
        self.block_frames = int(block_frames)
        self.maximum_queued_phrases = int(maximum_queued_phrases)
        self._lock = threading.Lock()
        self._queue: deque[tuple[np.ndarray, dict[str, Any]]] = deque()
        self._current_audio: np.ndarray | None = None
        self._current_receipt: dict[str, Any] | None = None
        self._offset = 0
        self._callback_count = 0
        self._frames_requested = 0
        self._frames_from_phrases = 0
        self._silence_frames = 0
        self._underrun_count = 0
        self._completed: list[dict[str, Any]] = []

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
        prepared_audio = np.asarray(audio, dtype=np.float32, order="C").copy()
        prepared_receipt = deepcopy(dict(receipt))
        with self._lock:
            queued_count = len(self._queue) + (1 if self._current_audio is not None else 0)
            if queued_count >= self.maximum_queued_phrases:
                raise LiveError("live audio callback queue is full")
            self._queue.append((prepared_audio, prepared_receipt))
            position = len(self._queue)
        return {
            "phrase_sha256": str(prepared_receipt["phrase_sha256"]),
            "queued_position": position,
            "frames": int(prepared_audio.shape[0]),
        }

    def _take_next_phrase(self) -> bool:
        with self._lock:
            if not self._queue:
                return False
            self._current_audio, self._current_receipt = self._queue.popleft()
            self._offset = 0
            return True

    def render_into(self, outdata: np.ndarray) -> None:
        target = np.asarray(outdata)
        if target.ndim != 2 or target.shape[1] != 2:
            raise LiveError("audio callback output buffer must be stereo")
        if target.dtype != np.float32:
            raise LiveError("audio callback output buffer must be float32")
        target.fill(0.0)
        requested = int(target.shape[0])
        written = 0
        while written < requested:
            if self._current_audio is None or self._offset >= int(self._current_audio.shape[0]):
                if self._current_audio is not None:
                    assert self._current_receipt is not None
                    self._completed.append(
                        {
                            "phrase_sha256": str(self._current_receipt["phrase_sha256"]),
                            "frames": int(self._current_audio.shape[0]),
                            "callback_index": self._callback_count,
                        }
                    )
                    self._current_audio = None
                    self._current_receipt = None
                    self._offset = 0
                if not self._take_next_phrase():
                    self._underrun_count += 1
                    self._silence_frames += requested - written
                    break
            assert self._current_audio is not None
            available = min(requested - written, int(self._current_audio.shape[0]) - self._offset)
            target[written : written + available] = self._current_audio[self._offset : self._offset + available]
            self._offset += available
            written += available
            self._frames_from_phrases += available
        self._callback_count += 1
        self._frames_requested += requested

    def callback(self, outdata: np.ndarray, frames: int, time_info: Any = None, status: Any = None) -> None:
        if int(frames) != int(outdata.shape[0]):
            raise LiveError("audio backend frame count disagrees with callback buffer")
        self.render_into(outdata)

    def receipt(self) -> dict[str, Any]:
        with self._lock:
            queued = [str(receipt["phrase_sha256"]) for _audio, receipt in self._queue]
        value = {
            "schema_version": LIVE_AUDIO_CALLBACK_SCHEMA_VERSION,
            "kind": LIVE_AUDIO_CALLBACK_KIND,
            "sample_rate": self.sample_rate,
            "block_frames": self.block_frames,
            "maximum_queued_phrases": self.maximum_queued_phrases,
            "callback_count": self._callback_count,
            "frames_requested": self._frames_requested,
            "frames_from_phrases": self._frames_from_phrases,
            "silence_frames": self._silence_frames,
            "underrun_count": self._underrun_count,
            "current_phrase_sha256": None if self._current_receipt is None else str(self._current_receipt["phrase_sha256"]),
            "current_phrase_remaining_frames": 0 if self._current_audio is None else int(self._current_audio.shape[0]) - self._offset,
            "queued_phrase_sha256s": queued,
            "completed_phrases": deepcopy(self._completed),
            "callback_planning_count": 0,
            "callback_library_search_count": 0,
            "callback_sample_decode_count": 0,
            "callback_binding_count": 0,
        }
        value["callback_sha256"] = midi_sha256_json(value)
        return value


class LiveSoundDevicePlayer:
    """Optional sounddevice host around `LiveAudioCallback`.

    Device I/O is deliberately optional. Core planning, phrase rendering, and
    deterministic tests do not import or require sounddevice.
    """

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
