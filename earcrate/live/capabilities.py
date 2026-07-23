from __future__ import annotations

import importlib.util
from typing import Any

from earcrate.live.operators import live_technique_names


def live_stream_capability() -> dict[str, Any]:
    return {
        "ready": True,
        "backend": "prepared_phrase_numpy_blocks",
        "requires_gpu": False,
        "requires_network": False,
        "requires_cloud": False,
        "callback_contract": {
            "allowed": ["prepared_buffer_swap", "float32_frame_copy", "silence_on_underrun"],
            "forbidden": ["planning", "library_search", "sample_decode", "binding"],
        },
        "optional_sounddevice_ready": importlib.util.find_spec("sounddevice") is not None,
    }


def live_audio_device_capability() -> dict[str, Any]:
    return {
        "optional_backend": "sounddevice",
        "sounddevice_ready": importlib.util.find_spec("sounddevice") is not None,
        "requires_gpu": False,
        "requires_network": False,
        "requires_cloud": False,
        "queue_model": "single_producer_single_consumer_fixed_ring",
        "completion_history": "fixed_ring",
    }


def live_runtime_capability() -> dict[str, Any]:
    return {
        "ready": True,
        "planner_backend": "deterministic_python_cpu",
        "requires_gpu": False,
        "requires_network": False,
        "requires_cloud": False,
        "expensive_analysis_expected_offline": True,
        "techniques": live_technique_names(),
        "personas": ["club", "girl_talk", "minimal", "pretty_lights"],
        "prepared_stream": live_stream_capability(),
        "audio_device": live_audio_device_capability(),
        "runtime_boundary": {
            "planning_thread": "receding_horizon_cpu",
            "phrase_render_thread": "sealed_racks_to_prepared_pcm",
            "audio_callback": "fixed_ring_buffer_swap_and_float32_copy",
            "evidence": "measured_by_live_activity_recorder",
        },
    }
