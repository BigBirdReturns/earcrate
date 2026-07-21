from __future__ import annotations

import json
import os
import tempfile
from copy import deepcopy
from pathlib import Path
from typing import Any, Mapping

from earcrate.midi.model import midi_jsonable, midi_sha256_json, midi_validate_ledger
from earcrate.midi.render import midi_compile_note_spans
from earcrate.rack.demand import rack_compile_demands
from earcrate.midi.anatomy_grid import (
    AnatomyError,
    _bar_cells,
    _bar_grid,
    _event_slot_maps,
    _feature_vectors,
    _novelty,
    _role_order,
    midi_time_signature_map,
)
from earcrate.midi.anatomy_structure import (
    _event_assignments,
    _fingerprint,
    _motifs,
    _sections,
    _structural_payload,
)

ANATOMY_SCHEMA_VERSION = 1
ANATOMY_KIND = "earcrate_arrangement_anatomy"


def midi_arrangement_anatomy(
    ledger: Mapping[str, Any],
    *,
    minimum_section_bars: int = 2,
    maximum_section_bars: int = 16,
    section_penalty: float = 0.22,
    boundary_reward: float = 0.32,
    motif_subdivisions: int = 16,
) -> dict[str, Any]:
    """Compile a deterministic, source-event-complete arrangement anatomy."""
    midi_validate_ledger(ledger)
    if minimum_section_bars <= 0 or maximum_section_bars < minimum_section_bars:
        raise AnatomyError("section bar limits are invalid")
    if section_penalty < 0 or boundary_reward < 0:
        raise AnatomyError("section penalty and boundary reward must be nonnegative")
    demand = rack_compile_demands(ledger)
    compiled = midi_compile_note_spans(ledger)
    slots, event_map = _event_slot_maps(demand)
    spans = list(compiled["note_spans"])
    selected_ids = {str(span["event_id"]) for span in spans}
    if selected_ids != set(event_map):
        raise AnatomyError("demand and compiled note spans disagree on selected event IDs")
    bars = _bar_grid(ledger, spans)
    cells, onset_to_bar = _bar_cells(ledger, bars, spans, event_map)
    if set(onset_to_bar) != selected_ids:
        raise AnatomyError("bar mapping did not account for every selected MIDI event")
    roles = _role_order(cells)
    vectors = _feature_vectors(cells, roles)
    novelty = _novelty(vectors, cells)
    sections = _sections(
        cells,
        vectors,
        novelty,
        minimum_bars=minimum_section_bars,
        maximum_bars=maximum_section_bars,
        section_penalty=section_penalty,
        boundary_reward=boundary_reward,
    )
    motifs = _motifs(demand, bars, subdivisions=motif_subdivisions)
    assignments = _event_assignments(event_map, onset_to_bar, sections)
    fingerprint = _fingerprint(cells, sections, motifs, roles)
    anatomy = {
        "schema_version": ANATOMY_SCHEMA_VERSION,
        "kind": ANATOMY_KIND,
        "semantic_sha256": str(ledger["semantic_sha256"]),
        "demand_sha256": str(demand["demand_sha256"]),
        "configuration": {
            "minimum_section_bars": int(minimum_section_bars),
            "maximum_section_bars": int(maximum_section_bars),
            "section_penalty": float(section_penalty),
            "boundary_reward": float(boundary_reward),
            "motif_subdivisions": int(motif_subdivisions),
        },
        "selected_event_count": len(spans),
        "mapped_event_count": len(assignments),
        "bar_count": len(cells),
        "section_count": len(sections),
        "slot_count": len(slots),
        "roles": roles,
        "time_signatures": midi_time_signature_map(ledger),
        "slots": [slots[key] for key in sorted(slots)],
        "bars": cells,
        "boundary_novelty": novelty,
        "sections": sections,
        "motifs": motifs,
        "event_assignments": assignments,
        "fingerprint": fingerprint,
        "compile_diagnostics": deepcopy(compiled["diagnostics"]),
    }
    anatomy["structural_sha256"] = midi_sha256_json(_structural_payload(anatomy))
    anatomy["anatomy_sha256"] = midi_sha256_json(anatomy)
    midi_validate_arrangement_anatomy(anatomy)
    return anatomy


def midi_validate_arrangement_anatomy(anatomy: Mapping[str, Any]) -> None:
    if int(anatomy.get("schema_version") or 0) != ANATOMY_SCHEMA_VERSION:
        raise AnatomyError(f"unsupported anatomy schema: {anatomy.get('schema_version')}")
    if str(anatomy.get("kind") or "") != ANATOMY_KIND:
        raise AnatomyError(f"unsupported anatomy kind: {anatomy.get('kind')}")
    bars = anatomy.get("bars")
    sections = anatomy.get("sections")
    assignments = anatomy.get("event_assignments")
    if not isinstance(bars, list) or not bars:
        raise AnatomyError("anatomy requires bars")
    previous_end = 0
    for index, bar in enumerate(bars):
        if int(bar.get("bar_index", -1)) != index:
            raise AnatomyError("anatomy bars are not indexed contiguously")
        if int(bar["start_tick"]) != previous_end or int(bar["end_tick"]) <= int(bar["start_tick"]):
            raise AnatomyError("anatomy bars are not a contiguous positive grid")
        previous_end = int(bar["end_tick"])
    if not isinstance(sections, list) or not sections:
        raise AnatomyError("anatomy requires sections")
    expected_bar = 0
    for index, section in enumerate(sections):
        if int(section.get("section_index", -1)) != index:
            raise AnatomyError("anatomy sections are not indexed contiguously")
        if int(section["start_bar_index"]) != expected_bar or int(section["end_bar_index"]) <= expected_bar:
            raise AnatomyError("anatomy sections do not form a contiguous partition")
        expected_bar = int(section["end_bar_index"])
    if expected_bar != len(bars):
        raise AnatomyError("anatomy sections do not cover every bar")
    if not isinstance(assignments, list):
        raise AnatomyError("anatomy event_assignments must be a list")
    event_ids = [str(row.get("event_id") or "") for row in assignments]
    if not all(event_ids) or len(event_ids) != len(set(event_ids)):
        raise AnatomyError("anatomy event assignments must be unique and nonempty")
    if len(assignments) != int(anatomy.get("selected_event_count") or 0):
        raise AnatomyError("anatomy selected_event_count disagrees with assignments")
    if len(assignments) != int(anatomy.get("mapped_event_count") or 0):
        raise AnatomyError("anatomy mapped_event_count disagrees with assignments")
    structural = midi_sha256_json(_structural_payload(anatomy))
    if str(anatomy.get("structural_sha256") or "") != structural:
        raise AnatomyError("structural_sha256 does not match anatomy structure")
    expected = midi_sha256_json({key: value for key, value in anatomy.items() if key != "anatomy_sha256"})
    if str(anatomy.get("anatomy_sha256") or "") != expected:
        raise AnatomyError("anatomy_sha256 does not match anatomy contents")


def midi_write_arrangement_anatomy(
    ledger: Mapping[str, Any],
    output_path: str | Path,
    *,
    overwrite: bool = False,
    **configuration: Any,
) -> dict[str, Any]:
    anatomy = midi_arrangement_anatomy(ledger, **configuration)
    destination = Path(output_path).expanduser().resolve()
    if destination.exists() and not overwrite:
        raise FileExistsError(f"refusing to overwrite arrangement anatomy: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(midi_jsonable(anatomy), ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    fd, temp_name = tempfile.mkstemp(prefix=destination.name + ".", suffix=".tmp", dir=str(destination.parent))
    os.close(fd)
    temp = Path(temp_name)
    try:
        temp.write_text(text, encoding="utf-8")
        os.replace(temp, destination)
    finally:
        if temp.exists():
            temp.unlink()
    return {
        "ok": True,
        "path": str(destination),
        "semantic_sha256": anatomy["semantic_sha256"],
        "anatomy_sha256": anatomy["anatomy_sha256"],
        "structural_sha256": anatomy["structural_sha256"],
        "selected_event_count": anatomy["selected_event_count"],
        "bar_count": anatomy["bar_count"],
        "section_count": anatomy["section_count"],
        "motif_count": len(anatomy["motifs"]),
    }
