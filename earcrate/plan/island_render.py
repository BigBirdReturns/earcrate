"""Narrow render dispatch for globally-accounted island-set arrangements."""
from __future__ import annotations

import contextlib
import copy
import json
import shutil
import uuid
from pathlib import Path
from typing import Any, Dict, List, Mapping, Sequence, Tuple


# The ordinary post-render gate was designed for a complete user-facing set.
# A tempo island is an implementation slice of that governed whole. Local
# silence, spectral, source, and render-integrity failures remain fatal, but the
# complete-program dynamic-arc veto is deferred until the islands are assembled.
WHOLE_SET_ONLY_FAILURE_PREFIXES = (
    "rms_std_db catastrophically low; render is effectively flat",
)


def crossfade_pair(left: Any, right: Any, samples: int) -> Any:
    import numpy as np
    from earcrate.deck.dsp import dj_fade_curves

    left = np.asarray(left, dtype=np.float32)
    right = np.asarray(right, dtype=np.float32)
    count = min(max(0, int(samples)), left.size, right.size)
    if count <= 0:
        return np.concatenate([left, right]).astype(np.float32)
    incoming, outgoing = dj_fade_curves(count, "equal_power")
    return np.concatenate([
        left[:-count],
        left[-count:] * outgoing + right[:count] * incoming,
        right[count:],
    ]).astype(np.float32)


def combine_island_audio(
    masters: Sequence[Any],
    stems: Sequence[Mapping[str, Any]],
    transitions: Sequence[Mapping[str, Any]],
    sample_rate: int,
) -> Tuple[Any, Dict[str, Any]]:
    """Equal-power join existing renderer outputs on one whole-set timeline.

    The existing single-deck stem export is observational and pre-master. A final
    effects_transition residual makes the delivered role stems reconcile exactly
    to the whole-set master without falsifying those captures.
    """
    import numpy as np

    if not masters:
        raise RuntimeError("no island masters to combine")
    master = np.asarray(masters[0], dtype=np.float32)
    groups = sorted({group for mapping in stems for group in mapping})
    combined: Dict[str, Any] = {
        group: np.asarray(stems[0].get(group, np.zeros_like(masters[0])), dtype=np.float32)
        for group in groups
    }
    for index in range(1, len(masters)):
        overlap = int(round(float(transitions[index - 1].get("duration_s") or 0.0) * int(sample_rate)))
        master = crossfade_pair(master, masters[index], overlap)
        for group in groups:
            left = combined[group]
            right = np.asarray(
                stems[index].get(group, np.zeros(len(masters[index]), dtype=np.float32)),
                dtype=np.float32,
            )
            combined[group] = crossfade_pair(left, right, overlap)
    for group in list(combined):
        if len(combined[group]) < len(master):
            combined[group] = np.pad(combined[group], (0, len(master) - len(combined[group])))
        elif len(combined[group]) > len(master):
            combined[group] = combined[group][:len(master)]
    stem_sum = np.zeros_like(master)
    for audio in combined.values():
        stem_sum += np.asarray(audio, dtype=np.float32)
    combined["effects_transition"] = (master - stem_sum).astype(np.float32)
    return master, combined


def classify_segment_quality_gate(gate: Mapping[str, Any] | None) -> Dict[str, Any]:
    """Separate slice-local failures from complete-program dynamic-arc failures.

    This is deliberately a whitelist of one known whole-program criterion. A new
    failure message is fatal by default, so the island path cannot silently
    weaken future post-render laws.
    """
    raw = dict(gate or {})
    failures = [str(item) for item in raw.get("failures") or []]
    deferred = [
        failure
        for failure in failures
        if any(failure.startswith(prefix) for prefix in WHOLE_SET_ONLY_FAILURE_PREFIXES)
    ]
    fatal = [failure for failure in failures if failure not in deferred]
    return {
        "scope": "island_slice",
        "passed": not fatal,
        "fatal_failures": fatal,
        "deferred_to_whole_set": deferred,
        "warnings": [str(item) for item in raw.get("warnings") or []],
        "metrics": dict(raw.get("metrics") or {}),
    }


def _layer_role(layer: Mapping[str, Any]) -> str:
    return str(layer.get("role") or layer.get("render_role") or layer.get("ear_role") or "").strip()


def _layer_source(layer: Mapping[str, Any]) -> str:
    return str(
        layer.get("source_track_key")
        or layer.get("source_id")
        or layer.get("loop_id")
        or layer.get("atom_id")
        or ""
    ).strip()


def _section_signature(section: Mapping[str, Any]) -> Tuple[Any, ...]:
    layers = []
    for layer in section.get("layers") or []:
        layers.append((
            _layer_source(layer),
            _layer_role(layer),
            round(float(layer.get("gain_db") or 0.0), 6),
            str(layer.get("transform") or layer.get("playback_mode") or ""),
        ))
    return (
        str(section.get("type") or section.get("section") or ""),
        tuple(sorted(layers)),
    )


def whole_set_form_gate(arrangement: Mapping[str, Any]) -> Dict[str, Any]:
    """Enforce withholding and content-changing form on the assembled set.

    A long-form island render may contain locally steady groove spans, but the
    governed whole must still exhibit role entry, role exit, withholding, and
    actual material change across its declared section boundaries.
    """
    sections = sorted(
        [dict(section) for section in arrangement.get("sections") or []],
        key=lambda section: (
            float(section.get("start_s") or 0.0),
            str(section.get("island_id") or ""),
            int(section.get("bar_start") or 0),
        ),
    )
    failures: List[str] = []
    transitions: List[Dict[str, Any]] = []
    role_sets: List[set[str]] = []
    signatures: List[Tuple[Any, ...]] = []
    for section in sections:
        roles = {_layer_role(layer) for layer in section.get("layers") or [] if _layer_role(layer)}
        role_sets.append(roles)
        signatures.append(_section_signature(section))

    if len(sections) < 3:
        failures.append("whole set has fewer than three declared sections")
    if role_sets and len({tuple(sorted(roles)) for roles in role_sets}) < 2:
        failures.append("whole-set role occupancy never changes")
    max_roles = max((len(roles) for roles in role_sets), default=0)
    if max_roles and not any(len(roles) < max_roles for roles in role_sets):
        failures.append("whole set never withholds a sounding role")

    has_entry = False
    has_exit = False
    for index in range(1, len(sections)):
        previous_roles = role_sets[index - 1]
        current_roles = role_sets[index]
        entering = sorted(current_roles - previous_roles)
        leaving = sorted(previous_roles - current_roles)
        material_changed = signatures[index] != signatures[index - 1]
        has_entry = has_entry or bool(entering)
        has_exit = has_exit or bool(leaving)
        transitions.append({
            "from_index": index - 1,
            "to_index": index,
            "roles_entering": entering,
            "roles_leaving": leaving,
            "material_changed": material_changed,
            "is_content_change": bool(entering or leaving or material_changed),
        })
    if sections and not has_entry:
        failures.append("whole set has no role-entry transition")
    if sections and not has_exit:
        failures.append("whole set has no role-exit transition")
    unchanged = [row for row in transitions if not row["is_content_change"]]
    if unchanged:
        failures.append(f"{len(unchanged)} whole-set section transition(s) change neither roles nor material")

    return {
        "scope": "governed_whole",
        "passed": not failures,
        "failures": failures,
        "section_count": len(sections),
        "distinct_role_sets": len({tuple(sorted(roles)) for roles in role_sets}),
        "has_role_entry": has_entry,
        "has_role_exit": has_exit,
        "has_withholding": bool(max_roles and any(len(roles) < max_roles for roles in role_sets)),
        "every_transition_changes_content": not unchanged,
        "transitions": transitions,
    }


def whole_set_quality_gate(
    master: Any,
    sample_rate: int,
    target_seconds: float,
    spectral_profile: Mapping[str, Any] | None = None,
) -> Dict[str, Any]:
    """Apply the complete-program audio gate to the assembled master."""
    from earcrate.judge.audio import drydeck_metrics, drydeck_quality_gate

    return drydeck_quality_gate(
        drydeck_metrics(master, int(sample_rate)),
        float(target_seconds),
        dict(spectral_profile) if spectral_profile is not None else None,
    )


def _persist_parent_rejection(
    core: Any,
    mashup_id: str,
    destination: Path,
    report: Mapping[str, Any],
    failure_kind: str,
) -> Dict[str, Any]:
    from earcrate.core.deps import ENGINE_VERSION

    config = core.ensure_config()
    db = core.conn()
    reject_dir = (Path(config.agent_root) / "rejected_renders" / ENGINE_VERSION).resolve()
    if hasattr(core, "validate_path_in_root"):
        core.validate_path_in_root(reject_dir, Path(config.agent_root) / "rejected_renders")
    reject_dir.mkdir(parents=True, exist_ok=True)
    report_path = reject_dir / (Path(destination).stem + ".render_report.json")
    body = dict(report)
    body["render_failure"] = {
        "kind": failure_kind,
        "message": "multi-island whole-set publication refused",
    }
    report_path.write_text(json.dumps(body, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    db.execute(
        "UPDATE mashups SET render_path=NULL,engine_version=?,arrangement_sha=?,render_report_path=? WHERE id=?",
        (ENGINE_VERSION, str(body.get("arrangement_sha") or ""), str(report_path), mashup_id),
    )
    db.commit()
    return {
        "type": "render_rejected",
        "path": None,
        "report": str(report_path),
        "failure_kind": failure_kind,
        "quality_gate": body.get("quality_gate"),
        "form_gate": body.get("form_gate"),
        "engine_version": ENGINE_VERSION,
        "arrangement_sha": body.get("arrangement_sha"),
        "seconds": round(float(body.get("seconds") or 0.0), 6),
        "island_count": len(body.get("islands") or []),
        "presented": False,
    }


def render_island_set(
    core: Any,
    mashup_id: str,
    destination: Path,
    arrangement: Mapping[str, Any],
    single_render: Any,
) -> Dict[str, Any]:
    """Render each exact-deck segment through the existing renderer, then join.

    Segment renders are temporary implementation details. Source selection,
    turnover, island identity, transitions, and accounting are all fixed by the
    one whole-set arrangement before any audio is written. Complete-program
    dynamic arc is judged only after the governed whole has been assembled.
    """
    import numpy as np
    import soundfile as sf
    from earcrate.core.deps import ENGINE_VERSION, arrangement_sha, now_utc

    config = core.ensure_config()
    db = core.conn()
    sample_rate = int(config.sample_rate)
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    # The ordinary renderer accepts outputs only beneath working_root/renders.
    # Segment renders are implementation details, but they still pass through
    # that renderer and therefore must live inside the same validated root.
    render_root = Path(config.working_root) / "renders"
    render_root.mkdir(parents=True, exist_ok=True)
    scratch = render_root / f".island-render-{uuid.uuid4().hex}"
    scratch.mkdir(parents=True, exist_ok=False)
    temp_ids: List[str] = []
    masters: List[Any] = []
    stems: List[Dict[str, Any]] = []
    island_receipts: List[Dict[str, Any]] = []
    try:
        for index, island in enumerate(arrangement.get("islands") or []):
            planned_segment = copy.deepcopy(island["arrangement"])
            planned_segment_sha = arrangement_sha(planned_segment)
            segment = copy.deepcopy(planned_segment)
            segment_params = segment.setdefault("params", {})
            segment_params["stem_export"] = True
            segment_params["post_render_gate"] = False
            segment_params["quality_gate_scope"] = "island_slice_of_governed_whole"
            runtime_segment_sha = arrangement_sha(segment)
            temp_id = f"isl_{uuid.uuid4().hex}"
            temp_ids.append(temp_id)
            temp_path = scratch / f"island-{index:03d}.wav"
            db.execute(
                "INSERT INTO mashups(id,name,seed,params_json,arrangement_json,render_path,created_at,engine_version,arrangement_sha) VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    temp_id,
                    f"island-{index:03d}",
                    int(segment.get("seed") or 0),
                    json.dumps(segment.get("params") or {}, ensure_ascii=False),
                    json.dumps(segment, ensure_ascii=False),
                    str(temp_path),
                    now_utc(),
                    ENGINE_VERSION,
                    runtime_segment_sha,
                ),
            )
            db.commit()
            rendered = single_render(core, temp_id, temp_path)
            if not rendered.get("presented") or not temp_path.exists():
                raise RuntimeError(
                    f"island {island.get('island_id')} refused in the existing renderer; "
                    f"report={rendered.get('report')}"
                )
            audio, sr = sf.read(str(temp_path), dtype="float32", always_2d=False)
            if int(sr) != sample_rate:
                raise RuntimeError("island render sample-rate mismatch")
            if getattr(audio, "ndim", 1) > 1:
                audio = np.mean(audio, axis=1).astype(np.float32)
            masters.append(np.asarray(audio, dtype=np.float32))

            report_path = temp_path.with_suffix(".render_report.json")
            report = json.loads(report_path.read_text(encoding="utf-8")) if report_path.exists() else {}
            segment_gate = classify_segment_quality_gate(report.get("quality_gate"))
            if not segment_gate["passed"]:
                raise RuntimeError(
                    f"island {island.get('island_id')} failed slice-local quality gate: "
                    + "; ".join(segment_gate["fatal_failures"])
                )
            stem_map: Dict[str, Any] = {}
            for group, path_value in sorted(((report.get("stems") or {}).get("paths") or {}).items()):
                stem_audio, stem_sr = sf.read(str(path_value), dtype="float32", always_2d=False)
                if int(stem_sr) != sample_rate:
                    raise RuntimeError("island stem sample-rate mismatch")
                if getattr(stem_audio, "ndim", 1) > 1:
                    stem_audio = np.mean(stem_audio, axis=1).astype(np.float32)
                stem_map[str(group)] = np.asarray(stem_audio, dtype=np.float32)
            stems.append(stem_map)
            island_receipts.append({
                "island_id": island.get("island_id"),
                "planned_arrangement_sha256": planned_segment_sha,
                "runtime_arrangement_sha256": runtime_segment_sha,
                "render": rendered,
                "segment_quality_gate": segment_gate,
            })

        master, combined_stems = combine_island_audio(
            masters,
            stems,
            arrangement.get("transitions") or [],
            sample_rate,
        )
        target_seconds = float(
            arrangement.get("requested_duration_s")
            or arrangement.get("duration_s")
            or (master.size / max(1, sample_rate))
        )
        whole_quality = whole_set_quality_gate(master, sample_rate, target_seconds)
        form_gate = whole_set_form_gate(arrangement)

        stem_sum = np.zeros_like(master)
        for audio in combined_stems.values():
            stem_sum += np.asarray(audio, dtype=np.float32)
        residual = float(np.max(np.abs(master - stem_sum))) if master.size else 0.0
        report = {
            "kind": "earcrate_island_set_render",
            "engine_version": ENGINE_VERSION,
            "arrangement_sha": arrangement_sha(dict(arrangement)),
            "render_timestamp": now_utc(),
            "sample_rate": sample_rate,
            "seconds": master.size / sample_rate,
            "islands": island_receipts,
            "transitions": arrangement.get("transitions") or [],
            "global_source_ledger": arrangement.get("global_source_ledger") or [],
            "quality_gate": whole_quality,
            "form_gate": form_gate,
            "quality_scope": {
                "segment": "all local criteria enforced; complete-program flatness deferred",
                "whole": "full audio gate and role/material form gate enforced before publication",
            },
            "stems": {"paths": {}, "max_sum_residual": residual},
            "accounting": arrangement.get("accounting") or {},
        }
        if not whole_quality.get("passed", True):
            return _persist_parent_rejection(core, mashup_id, destination, report, "whole_set_quality_gate")
        if target_seconds >= 60.0 and not form_gate.get("passed", True):
            return _persist_parent_rejection(core, mashup_id, destination, report, "whole_set_form_gate")

        sf.write(str(destination), master, sample_rate, subtype="PCM_24")
        stem_paths: Dict[str, str] = {}
        for group, audio in sorted(combined_stems.items()):
            path = destination.with_name(destination.stem + f".stem_{group}.wav")
            sf.write(str(path), np.asarray(audio, dtype=np.float32), sample_rate, subtype="PCM_24")
            stem_paths[group] = str(path)
        report["stems"]["paths"] = stem_paths
        report_path = destination.with_suffix(".render_report.json")
        report_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        db.execute(
            "UPDATE mashups SET render_path=?,engine_version=?,arrangement_sha=?,render_report_path=? WHERE id=?",
            (str(destination), ENGINE_VERSION, report["arrangement_sha"], str(report_path), mashup_id),
        )
        db.commit()
        return {
            "type": "render_mashup",
            "path": str(destination),
            "report": str(report_path),
            "engine_version": ENGINE_VERSION,
            "arrangement_sha": report["arrangement_sha"],
            "seconds": round(master.size / sample_rate, 6),
            "island_count": len(masters),
            "source_count": len(arrangement.get("global_source_ledger") or []),
            "source_reuse": int((arrangement.get("accounting") or {}).get("source_reuse") or 0),
            "quality_gate": whole_quality,
            "form_gate": form_gate,
            "stems": stem_paths,
            "stem_sum_residual": residual,
            "presented": True,
        }
    finally:
        for temp_id in temp_ids:
            with contextlib.suppress(Exception):
                db.execute("DELETE FROM mashups WHERE id=?", (temp_id,))
        with contextlib.suppress(Exception):
            db.commit()
        shutil.rmtree(scratch, ignore_errors=True)


def install_island_render_dispatch(core_class: Any) -> Any:
    if getattr(core_class, "_island_render_installed", False):
        return core_class
    from earcrate.plan.islands import ISLAND_SET_KIND

    original = core_class.render_mashup

    def dispatch(self: Any, mashup_id: str, destination: Path) -> Dict[str, Any]:
        row = self.conn().execute(
            "SELECT arrangement_json FROM mashups WHERE id=?",
            (mashup_id,),
        ).fetchone()
        if not row:
            return original(self, mashup_id, destination)
        arrangement = json.loads(row["arrangement_json"])
        if arrangement.get("kind") != ISLAND_SET_KIND:
            return original(self, mashup_id, destination)
        return render_island_set(self, mashup_id, Path(destination), arrangement, original)

    core_class._single_deck_render_mashup = original
    core_class.render_mashup = dispatch
    core_class._island_render_installed = True
    return core_class


__all__ = [
    "WHOLE_SET_ONLY_FAILURE_PREFIXES",
    "classify_segment_quality_gate",
    "combine_island_audio",
    "crossfade_pair",
    "install_island_render_dispatch",
    "render_island_set",
    "whole_set_form_gate",
    "whole_set_quality_gate",
]
