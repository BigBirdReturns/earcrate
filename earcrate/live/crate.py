from __future__ import annotations

import hashlib
import json
import os
import tempfile
from copy import deepcopy
from pathlib import Path
from typing import Any, Mapping, Sequence

from earcrate.live.model import LiveError
from earcrate.live.planner import live_atlas_from_midi, live_validate_atlas
from earcrate.live.runtime_fix import live_build_session
from earcrate.midi.codec import midi_write
from earcrate.midi.model import midi_sha256_json, midi_validate_ledger
from earcrate.rack.binding_stable import rack_compile_binding
from earcrate.rack.model import (
    rack_validate_revision,
    rack_verify_sources,
)
from earcrate.rack.multizone import rack_build_from_atoms
from earcrate.rack.render_fix import rack_render_ledger

LIVE_CRATE_ATLAS_SCHEMA_VERSION = 1
LIVE_CRATE_ATLAS_KIND = "earcrate_live_crate_atlas"
LIVE_CRATE_SESSION_SCHEMA_VERSION = 1
LIVE_CRATE_SESSION_KIND = "earcrate_live_crate_session"


def _live_crate_atomic_json(path: str | Path, value: Mapping[str, Any], *, overwrite: bool = False) -> dict[str, Any]:
    destination = Path(path).expanduser().resolve()
    if destination.exists() and not overwrite:
        raise FileExistsError(f"refusing to overwrite live crate artifact: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(dict(value), ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    fd, temporary_name = tempfile.mkstemp(prefix=destination.name + ".", suffix=".tmp", dir=str(destination.parent))
    os.close(fd)
    temporary = Path(temporary_name)
    try:
        temporary.write_text(text, encoding="utf-8")
        os.replace(temporary, destination)
    finally:
        if temporary.exists():
            temporary.unlink()
    return {
        "path": str(destination),
        "bytes": destination.stat().st_size,
        "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
    }


def live_crate_atlas_payload(atlas: Mapping[str, Any]) -> dict[str, Any]:
    out = deepcopy(dict(atlas))
    out.pop("crate_atlas_sha256", None)
    return out


def live_compute_crate_atlas_sha256(atlas: Mapping[str, Any]) -> str:
    return midi_sha256_json(live_crate_atlas_payload(atlas))


def live_validate_crate_atlas(atlas: Mapping[str, Any], *, verify_sources: bool = False) -> None:
    if int(atlas.get("schema_version") or 0) != LIVE_CRATE_ATLAS_SCHEMA_VERSION:
        raise LiveError(f"unsupported live crate atlas schema: {atlas.get('schema_version')}")
    if str(atlas.get("kind") or "") != LIVE_CRATE_ATLAS_KIND:
        raise LiveError(f"unsupported live crate atlas kind: {atlas.get('kind')}")
    source = atlas.get("source_midi_ledger")
    live_atlas = atlas.get("live_material_atlas")
    racks = atlas.get("rack_revisions")
    if not isinstance(source, Mapping):
        raise LiveError("live crate atlas requires its exact source MIDI ledger")
    midi_validate_ledger(source)
    if not isinstance(live_atlas, Mapping):
        raise LiveError("live crate atlas requires its live material atlas")
    live_validate_atlas(live_atlas)
    if str(source["semantic_sha256"]) != str(live_atlas["source_semantic_sha256"]):
        raise LiveError("live crate source MIDI and material atlas identities disagree")
    if not isinstance(racks, list) or not racks:
        raise LiveError("live crate atlas requires sealed rack revisions")
    rack_hashes = []
    for rack in racks:
        rack_validate_revision(rack)
        if verify_sources:
            rack_verify_sources(rack)
        rack_hashes.append(str(rack["rack_sha256"]))
    if len(rack_hashes) != len(set(rack_hashes)):
        raise LiveError("live crate atlas rack revisions must be unique")
    build = atlas.get("rack_build")
    if not isinstance(build, Mapping) or not bool(build.get("complete")):
        raise LiveError("live crate atlas requires a complete source-rack build")
    if str(build.get("build_sha256") or "") != str(atlas.get("rack_build_sha256") or ""):
        raise LiveError("live crate atlas rack-build identity disagrees with its receipt")
    expected = live_compute_crate_atlas_sha256(atlas)
    if str(atlas.get("crate_atlas_sha256") or "") != expected:
        raise LiveError("crate_atlas_sha256 does not match live crate atlas contents")


def live_compile_crate_atlas(
    source_ledger: Mapping[str, Any],
    atoms: Sequence[Mapping[str, Any]],
    output_root: str | Path,
    *,
    taste_profile: str = "",
    top_k: int = 8,
    maximum_transpose_semitones: float = 18.0,
    loopability_threshold: float = 0.58,
    max_zones_per_slot: int = 8,
    combination_beam_width: int = 64,
    sample_rate: int = 44_100,
    compile_sfz: bool = True,
    overwrite: bool = False,
) -> dict[str, Any]:
    """Spend the expensive library search once and seal a reusable live crate."""
    midi_validate_ledger(source_ledger)
    root = Path(output_root).expanduser().resolve()
    atlas_path = root / "live-crate-atlas.json"
    if atlas_path.exists() and not overwrite:
        raise FileExistsError(f"refusing to overwrite live crate atlas: {atlas_path}")
    live_atlas = live_atlas_from_midi(source_ledger)
    rack_build = rack_build_from_atoms(
        source_ledger,
        atoms,
        root / "library-racks",
        taste_profile=taste_profile,
        top_k=top_k,
        maximum_transpose_semitones=maximum_transpose_semitones,
        loopability_threshold=loopability_threshold,
        max_zones_per_slot=max_zones_per_slot,
        combination_beam_width=combination_beam_width,
        sample_rate=sample_rate,
        apply=True,
        overwrite=overwrite,
        compile_sfz=compile_sfz,
    )
    if not bool(rack_build.get("complete")) or not bool((rack_build.get("binding") or {}).get("complete")):
        raise LiveError("approved library did not produce an event-complete source rack build")
    rack_revisions = [deepcopy(dict(rack)) for rack in rack_build["rack_revisions"]]
    for rack in rack_revisions:
        rack_validate_revision(rack)
        rack_verify_sources(rack)
    public_build = {
        key: deepcopy(value)
        for key, value in rack_build.items()
        if key not in {"rack_revisions", "binding"}
    }
    public_build["source_binding_sha256"] = str(rack_build["binding"]["binding_sha256"])
    atlas = {
        "schema_version": LIVE_CRATE_ATLAS_SCHEMA_VERSION,
        "kind": LIVE_CRATE_ATLAS_KIND,
        "source_semantic_sha256": str(source_ledger["semantic_sha256"]),
        "live_atlas_sha256": str(live_atlas["atlas_sha256"]),
        "rack_build_sha256": str(rack_build["build_sha256"]),
        "taste_profile": str(taste_profile),
        "sample_rate": int(sample_rate),
        "configuration": {
            "top_k": int(top_k),
            "maximum_transpose_semitones": float(maximum_transpose_semitones),
            "loopability_threshold": float(loopability_threshold),
            "max_zones_per_slot": int(max_zones_per_slot),
            "combination_beam_width": int(combination_beam_width),
            "compile_sfz": bool(compile_sfz),
        },
        "source_midi_ledger": deepcopy(dict(source_ledger)),
        "live_material_atlas": live_atlas,
        "rack_revisions": rack_revisions,
        "rack_build": public_build,
    }
    atlas["crate_atlas_sha256"] = live_compute_crate_atlas_sha256(atlas)
    live_validate_crate_atlas(atlas, verify_sources=True)
    write = _live_crate_atomic_json(atlas_path, atlas, overwrite=overwrite)
    return {"atlas": atlas, "write": write}


def live_load_crate_atlas(path: str | Path, *, verify_sources: bool = True) -> dict[str, Any]:
    source = Path(path).expanduser().resolve()
    value = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise LiveError(f"live crate atlas must be a JSON object: {source}")
    atlas = dict(value)
    live_validate_crate_atlas(atlas, verify_sources=verify_sources)
    return atlas


def live_validate_crate_session(session: Mapping[str, Any]) -> None:
    if int(session.get("schema_version") or 0) != LIVE_CRATE_SESSION_SCHEMA_VERSION:
        raise LiveError(f"unsupported live crate session schema: {session.get('schema_version')}")
    if str(session.get("kind") or "") != LIVE_CRATE_SESSION_KIND:
        raise LiveError(f"unsupported live crate session kind: {session.get('kind')}")
    if not bool(session.get("complete")):
        raise LiveError("live crate session must be complete")
    if not str(session.get("crate_atlas_sha256") or ""):
        raise LiveError("live crate session requires a crate atlas identity")
    binding = session.get("generated_binding")
    if not isinstance(binding, Mapping) or not bool(binding.get("complete")):
        raise LiveError("live crate session requires an event-complete generated binding")
    expected = midi_sha256_json({key: value for key, value in session.items() if key != "crate_session_sha256"})
    if str(session.get("crate_session_sha256") or "") != expected:
        raise LiveError("crate_session_sha256 does not match live crate session contents")


def live_run_crate_session(
    crate_atlas: Mapping[str, Any],
    *,
    target_bars: int = 64,
    persona: str = "club",
    seed: int = 1,
    controls: Sequence[Mapping[str, Any]] | None = None,
    target_energy: float | None = None,
    density: float | None = None,
    risk: float | None = None,
    maximum_layers: int | None = None,
    horizon_bars: int = 0,
    phrase_bars: int = 0,
    beam_width: int = 32,
    candidate_limit: int = 12,
    target_bpm: float = 0.0,
    render_path: str | Path | None = None,
    stems_dir: str | Path | None = None,
    overwrite: bool = False,
) -> dict[str, Any]:
    """Plan on CPU, bind to precompiled racks, and optionally render without a library scan."""
    live_validate_crate_atlas(crate_atlas, verify_sources=True)
    source_ledger = crate_atlas["source_midi_ledger"]
    build = live_build_session(
        source_ledger,
        target_bars=target_bars,
        persona=persona,
        seed=seed,
        controls=controls,
        target_energy=target_energy,
        density=density,
        risk=risk,
        maximum_layers=maximum_layers,
        horizon_bars=horizon_bars,
        phrase_bars=phrase_bars,
        beam_width=beam_width,
        candidate_limit=candidate_limit,
        target_bpm=target_bpm,
    )
    if str(build["atlas"]["atlas_sha256"]) != str(crate_atlas["live_atlas_sha256"]):
        raise LiveError("live code or source performance changed after crate compilation")
    racks = [deepcopy(dict(rack)) for rack in crate_atlas["rack_revisions"]]
    generated_binding = rack_compile_binding(
        build["midi_ledger"],
        racks,
        pitch_bend_range_semitones=2.0,
    )
    if not bool(generated_binding.get("complete")):
        raise LiveError(
            "precompiled live racks cannot execute the generated session: "
            + json.dumps(generated_binding.get("unresolved") or [], ensure_ascii=False, sort_keys=True)
        )
    render = None
    if render_path is not None:
        render = rack_render_ledger(
            build["midi_ledger"],
            generated_binding,
            racks,
            render_path,
            stems_dir=stems_dir,
            sample_rate=int(crate_atlas["sample_rate"]),
            overwrite=overwrite,
        )
        if not bool(render.get("complete_execution")):
            raise LiveError("rack render did not execute every selected live event")
    session = {
        "schema_version": LIVE_CRATE_SESSION_SCHEMA_VERSION,
        "kind": LIVE_CRATE_SESSION_KIND,
        "complete": True,
        "crate_atlas_sha256": str(crate_atlas["crate_atlas_sha256"]),
        "live_session_sha256": str(build["session"]["session_sha256"]),
        "midi_semantic_sha256": str(build["midi_ledger"]["semantic_sha256"]),
        "cpu_program_sha256": str(build["cpu_program"]["program_sha256"]),
        "cpu_execution_sha256": str(build["cpu_execution"]["execution_sha256"]),
        "generated_binding_sha256": str(generated_binding["binding_sha256"]),
        "target_bars": int(target_bars),
        "persona": str(persona),
        "declared_library_material_count": int(crate_atlas["live_material_atlas"]["declared_material_count"]),
        "library_materials_scanned_during_execution": int(build["cpu_execution"]["materials_scanned_during_execution"]),
        "generated_event_count": int(generated_binding["selected_event_count"]),
        "bound_event_count": int(generated_binding["bound_event_count"]),
        "generated_binding": generated_binding,
        "render": render,
    }
    session["crate_session_sha256"] = midi_sha256_json(session)
    live_validate_crate_session(session)
    return {"build": build, "session": session, "binding": generated_binding, "render": render}


def live_write_crate_session(
    result: Mapping[str, Any],
    output_root: str | Path,
    *,
    overwrite: bool = False,
) -> dict[str, Any]:
    root = Path(output_root).expanduser().resolve()
    paths = [
        root / "live-session.json",
        root / "live-session.mid",
        root / "live-session.binding.json",
        root / "live-session.cpu-program.json",
        root / "live-session.cpu-execution.json",
    ]
    if not overwrite:
        conflicts = [str(path) for path in paths if path.exists()]
        if conflicts:
            raise FileExistsError("refusing partial live crate session write: " + ", ".join(conflicts))
    root.mkdir(parents=True, exist_ok=True)
    build = result["build"]
    session_write = _live_crate_atomic_json(paths[0], result["session"], overwrite=overwrite)
    midi_receipt = midi_write(build["midi_ledger"], paths[1], overwrite=overwrite)
    binding_write = _live_crate_atomic_json(paths[2], result["binding"], overwrite=overwrite)
    program_write = _live_crate_atomic_json(paths[3], build["cpu_program"], overwrite=overwrite)
    execution_write = _live_crate_atomic_json(paths[4], build["cpu_execution"], overwrite=overwrite)
    return {
        "session": session_write,
        "midi": midi_receipt,
        "binding": binding_write,
        "cpu_program": program_write,
        "cpu_execution": execution_write,
    }
