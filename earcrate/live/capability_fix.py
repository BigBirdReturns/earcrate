from __future__ import annotations

from typing import Any

_live_capability_planner_module = None
import earcrate.live.planner as _live_capability_planner_module
from earcrate.live.playback import live_audio_device_capability
from earcrate.live.stream import live_stream_capability

_original_live_runtime_capability = (
    _live_capability_planner_module.live_runtime_capability
    if _live_capability_planner_module is not None
    else live_runtime_capability
)


def live_runtime_capability() -> dict[str, Any]:
    result = dict(_original_live_runtime_capability())
    result["prepared_stream"] = live_stream_capability()
    result["audio_device"] = live_audio_device_capability()
    result["runtime_boundary"] = {
        "planning_thread": "receding_horizon_cpu",
        "phrase_render_thread": "sealed_racks_to_prepared_pcm",
        "audio_callback": "buffer_swap_and_float32_copy_only",
        "callback_planning_count": 0,
        "callback_library_search_count": 0,
        "callback_sample_decode_count": 0,
        "callback_binding_count": 0,
    }
    return result


if _live_capability_planner_module is not None:
    _live_capability_planner_module.live_runtime_capability = live_runtime_capability
