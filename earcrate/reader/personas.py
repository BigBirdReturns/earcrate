from __future__ import annotations

"""Reader personas configure attention and abstention; they never override PCM evidence."""

import json
from pathlib import Path
from typing import Any, Mapping

from earcrate.reader.model import ReaderError, reader_sha256_json


_PRETTY_LIGHTS_READER = {
    "persona_id": "remix_prettylights_reader_v2",
    "schema": "earcrate/reader-persona@1",
    "attention": {
        "pulse": 0.92,
        "recurrence": 1.0,
        "low_end": 0.90,
        "floor": 0.94,
        "harmonic_chop": 0.96,
        "foreground_source_phrase": 1.0,
        "texture": 0.82,
        "production_motion": 0.84,
    },
    "recurrence": {
        "same_phase_prior": 0.08,
        "same_phase_minimum_cosine": 0.80,
        "cross_phase_minimum_cosine": 0.93,
        "minimum_cluster_medoid_cosine": 0.76,
    },
    "phrase": {
        "minimum_cycle_beats": 4,
        "maximum_cycle_beats": 16,
    },
    "abstention": {
        "sustain_without_continuous_evidence": True,
        "symbolic_events_are_silent": True,
        "ambiguous_harmony_becomes_source_object": True,
    },
    "unique_residual": {
        "enabled": True,
        "foreground_low_hz": 180.0,
        "foreground_high_hz": 6500.0,
        "side_low_hz": 450.0,
        "side_high_hz": 8500.0,
        "side_gain": 0.22,
    },
}


def reader_persona_prettylights() -> dict[str, Any]:
    value = json.loads(json.dumps(_PRETTY_LIGHTS_READER))
    value["persona_sha256"] = reader_sha256_json(value)
    return value


def reader_load_persona(value: str | Path | Mapping[str, Any] | None) -> dict[str, Any]:
    if value in {None, "", "remix_prettylights_reader_v2", "prettylights"}:
        return reader_persona_prettylights()
    if isinstance(value, Mapping):
        out = dict(value)
    else:
        path = Path(value).expanduser().resolve()
        if not path.is_file():
            raise FileNotFoundError(path)
        out = json.loads(path.read_text(encoding="utf-8"))
    if str(out.get("schema") or "") != "earcrate/reader-persona@1":
        raise ReaderError("unsupported reader persona schema")
    if not str(out.get("persona_id") or ""):
        raise ReaderError("reader persona requires persona_id")
    out.pop("persona_sha256", None)
    out["persona_sha256"] = reader_sha256_json(out)
    return out


__all__ = ["reader_persona_prettylights", "reader_load_persona"]
