"""Narrow render dispatch for globally-accounted island-set arrangements."""
from __future__ import annotations

import contextlib
import copy
import json
import shutil
import uuid
from pathlib import Path
from typing import Any, Dict, List, Mapping, Sequence, Tuple


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
    one whole-set arrangement before any audio is written.
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
            segment = copy.deepcopy(island["arrangement"])
            segment.setdefault("params", {})["stem_export"] = True
            segment_sha = arrangement_sha(segment)
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
                    segment_sha,
                ),
            )
            db.commit()
            rendered = single_render(core, temp_id, temp_path)
            if not rendered.get("presented") or not temp_path.exists():
                raise RuntimeError(f"island {island.get('island_id')} refused in the existing renderer")
            audio, sr = sf.read(str(temp_path), dtype="float32", always_2d=False)
            if int(sr) != sample_rate:
                raise RuntimeError("island render sample-rate mismatch")
            if getattr(audio, "ndim", 1) > 1:
                audio = np.mean(audio, axis=1).astype(np.float32)
            masters.append(np.asarray(audio, dtype=np.float32))

            report_path = temp_path.with_suffix(".render_report.json")
            report = json.loads(report_path.read_text(encoding="utf-8")) if report_path.exists() else {}
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
                "arrangement_sha256": segment_sha,
                "render": rendered,
            })

        master, combined_stems = combine_island_audio(
            masters,
            stems,
            arrangement.get("transitions") or [],
            sample_rate,
        )
        sf.write(str(destination), master, sample_rate, subtype="PCM_24")
        stem_paths: Dict[str, str] = {}
        for group, audio in sorted(combined_stems.items()):
            path = destination.with_name(destination.stem + f".stem_{group}.wav")
            sf.write(str(path), np.asarray(audio, dtype=np.float32), sample_rate, subtype="PCM_24")
            stem_paths[group] = str(path)

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
            "stems": {"paths": stem_paths, "max_sum_residual": residual},
            "accounting": arrangement.get("accounting") or {},
        }
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
    "combine_island_audio",
    "crossfade_pair",
    "install_island_render_dispatch",
    "render_island_set",
]
