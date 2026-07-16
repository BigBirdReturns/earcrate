from earcrate.core.deps import *
from earcrate.core.util import json_dumps, sha256_text, sha256_file, now_utc, safe_name, ulidish, arrangement_sha
from earcrate.project.model import ScoreRevision, ProjectValidationError, ProjectNotFoundError
from earcrate.project.policy import compile_taste_policy
from earcrate.project.store import ProjectStore, _atomic_json
from earcrate.project.bridge import project_id_for, revision_from_arrangement, arrangement_for_render, rail_for_layer
from earcrate.project.commands import apply_project_command
from earcrate.project.export import export_project_bundle
from earcrate.judge.audio import resolve_project_master_actions


def project_store(core: Any) -> ProjectStore:
    c = core.ensure_config()
    root = (c.working_root / "projects").resolve()
    core.validate_not_master(root)
    core.validate_path_in_root(root, c.working_root)
    store = ProjectStore(root)
    store.initialize()
    return store


def _project_output_path(core: Any, revision: ScoreRevision, name: str = "") -> Path:
    c = core.ensure_config()
    title = safe_name(name or str(revision.intent.get("name") or "EarCrate Project"), "EarCrate Project")
    profile = str((revision.intent.get("taste_profile") or {}).get("id") or "taste")
    # The first render may create a mastering child revision. The visible WAV
    # name is therefore project-stable, while its INFO chunk and sidecar bind the
    # exact final revision and score identities.
    project_token = revision.project_id.replace(":", "-")[-16:]
    dst = (c.working_root / "renders" / f"{title}-{profile}-{ENGINE_VERSION}-{project_token}.wav").resolve()
    core.validate_not_master(dst)
    core.validate_path_in_root(dst, c.working_root / "renders")
    return dst


def _register_revision_mashup(core: Any, revision: ScoreRevision, mode: str, dst: Path) -> str:
    arrangement = arrangement_for_render(revision, mode)
    arr_sha = arrangement_sha(arrangement)
    mashup_id = ulidish()
    params = dict(arrangement.get("params") or {})
    core.conn().execute(
        "INSERT INTO mashups(id,name,seed,params_json,arrangement_json,render_path,created_at,engine_version,arrangement_sha) VALUES(?,?,?,?,?,?,?,?,?)",
        (
            mashup_id,
            str(revision.intent.get("name") or revision.project_id),
            int(revision.intent.get("seed") or 0),
            json.dumps(params, ensure_ascii=False),
            json.dumps(arrangement, ensure_ascii=False),
            str(dst),
            now_utc(),
            ENGINE_VERSION,
            arr_sha,
        ),
    )
    core.conn().commit()
    return mashup_id


def _candidate_count(params: Dict[str, Any], policy: Dict[str, Any]) -> int:
    explicit = int(params.get("candidate_count") or 0)
    if explicit:
        return max(2, min(24, explicit))
    density = policy.get("density_model") or {}
    breadth = int(density.get("beam_width") or density.get("max_layers") or 8)
    return max(4, min(16, breadth))


def _compile_candidates(core: Any, pool: List[Dict[str, Any]], params: Dict[str, Any],
                        *, external_mode: bool = False) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    taste_profile = str(params.get("taste_profile") or "girl_talk_v1")
    policy = compile_taste_policy(taste_profile)
    count = _candidate_count(params, policy)
    base_seed = int(params.get("seed") or 0) or core.next_render_seed(core.ensure_config().seed)
    candidates: List[Dict[str, Any]] = []
    failures: List[Dict[str, Any]] = []
    for offset in range(count):
        seed = base_seed + offset
        candidate_params = dict(params)
        candidate_params.update({
            "seed": seed,
            "taste_profile": taste_profile,
            "post_render_gate": True,
            "mix_mode": "tastespec_graph",
        })
        try:
            arrangement = core.compose_taste_arrangement(pool, candidate_params, seed)
            preflight = core.arrangement_preflight_gate(arrangement)
            taste_gate = core.taste_arrangement_gate(arrangement)
            score = core.score_arrangement(arrangement)
            passed = bool(preflight.get("passed")) and (external_mode or bool(taste_gate.get("passed")))
            score_total = float(score.get("total") or 0.0)
            # The actual project bridge is part of candidate feasibility. It checks
            # source identity, explicit stem choice, and transition tail capability.
            bridge_error = None
            revision_preview = None
            try:
                revision_preview = revision_from_arrangement(
                    core,
                    arrangement,
                    project_id=project_id_for(str(params.get("name") or "EarCrate Project"), arrangement),
                    static_gate_receipt={"preflight": preflight, "taste_gate": taste_gate},
                    compiler_receipt={"candidate_seed": seed, "candidate_score": score},
                )
            except Exception as exc:
                bridge_error = str(exc)
                passed = False
            row = {
                "seed": seed,
                "arrangement": arrangement,
                "score": score,
                "score_total": score_total,
                "preflight": preflight,
                "taste_gate": taste_gate,
                "bridge_error": bridge_error,
                "passed": passed,
                "revision_preview": revision_preview,
            }
            candidates.append(row)
        except Exception as exc:
            failures.append({"seed": seed, "error": str(exc), "exception_type": type(exc).__name__})
    viable = [row for row in candidates if row["passed"]]
    if not viable:
        evidence = [
            f"seed {row['seed']}: "
            + "; ".join((row["preflight"].get("failures") or []) + ([] if external_mode else (row["taste_gate"].get("failures") or [])))
            + (("; bridge: " + row["bridge_error"]) if row.get("bridge_error") else "")
            for row in candidates[:8]
        ] + [f"seed {row['seed']}: {row['error']}" for row in failures[:8]]
        raise PlanRejectedError(
            "project candidate search produced no executable TasteSpec score: " + " | ".join(evidence),
            (candidates[0]["arrangement"] if candidates else {}),
            (arrangement_sha(candidates[0]["arrangement"]) if candidates else ""),
        )
    viable.sort(key=lambda row: (-row["score_total"], row["seed"]))
    selected = viable[0]
    receipt_rows = []
    for row in sorted(candidates, key=lambda r: (-r["score_total"], r["seed"])):
        receipt_rows.append({
            "seed": row["seed"],
            "score": row["score"],
            "preflight": row["preflight"],
            "taste_gate": row["taste_gate"],
            "bridge_error": row["bridge_error"],
            "passed": row["passed"],
        })
    receipt_rows.extend(failures)
    selected["arrangement"]["candidate_search"] = {
        "count": count,
        "selected_seed": selected["seed"],
        "selected_score": selected["score"],
        "candidates": receipt_rows,
        "render_policy": "bounded deterministic search; only gate-passing, source-sealed, transition-renderable score revisions may win",
    }
    return selected, receipt_rows


def project_import_arrangement(core: Any, arrangement: Dict[str, Any], *, name: str = "",
                               project_id: str = "", created_by: Optional[Dict[str, Any]] = None,
                               static_gate_receipt: Optional[Dict[str, Any]] = None,
                               compiler_receipt: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    title = safe_name(name or str((arrangement.get("params") or {}).get("name") or "EarCrate Project"), "EarCrate Project")
    pid = str(project_id or project_id_for(title, arrangement))
    revision = revision_from_arrangement(
        core,
        arrangement,
        project_id=pid,
        created_by=created_by or {"actor": "compiler", "reason": "project_import", "compiler_version": "integrated_score_v1"},
        static_gate_receipt=static_gate_receipt,
        compiler_receipt=compiler_receipt,
    )
    store = project_store(core)
    record = store.create(title, revision, metadata={
        "taste_profile": str((revision.intent.get("taste_profile") or {}).get("id") or ""),
        "score_sha": revision.score_sha,
        "origin": str((created_by or {}).get("reason") or "project_import"),
    })
    return {"ok": True, "project": record.to_dict(), "revision": revision.to_dict()}


def project_compile(core: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    c = core.ensure_config()
    taste_profile = str(params.get("taste_profile") or "girl_talk_v1")
    target_seconds = float(params.get("target_seconds") or 120.0)
    readiness = core.taste_readiness(taste_profile, target_seconds)
    if readiness.get("crate_stale") and not params.get("allow_stale_crate"):
        raise RuntimeError("STALE CRATE: " + str(readiness.get("crate_stale_reason") or "engine/analyzer version changed"))
    if not readiness.get("ready"):
        raise RuntimeError("TasteSpec crate is not ready: " + "; ".join(readiness.get("failures") or []))
    pool = core.approved_atom_pool(taste_profile)
    title = safe_name(str(params.get("name") or "EarCrate Set"), "EarCrate Set")
    selected, candidates = _compile_candidates(core, pool, {**dict(params), "name": title})
    arrangement = selected["arrangement"]
    pid = str(params.get("project_id") or project_id_for(title, arrangement))
    revision = revision_from_arrangement(
        core,
        arrangement,
        project_id=pid,
        created_by={"actor": "compiler", "reason": "initial_compile", "compiler_version": "integrated_score_v1"},
        static_gate_receipt={"preflight": selected["preflight"], "taste_gate": selected["taste_gate"]},
        compiler_receipt={
            "candidate_search": arrangement["candidate_search"],
            "readiness": readiness,
            "source": "existing EarAtom pool + compatibility graph + TasteSpec composer",
        },
    )
    store = project_store(core)
    record = store.create(title, revision, metadata={
        "taste_profile": taste_profile,
        "score_sha": revision.score_sha,
        "readiness": readiness,
    })
    return {
        "ok": True,
        "project_id": pid,
        "revision_sha": revision.revision_sha,
        "score_sha": revision.score_sha,
        "project": record.to_dict(),
        "revision": revision.to_dict(),
        "arrangement": revision.arrangement,
        "seed": selected["seed"],
        "readiness": readiness,
        "candidate_search": arrangement["candidate_search"],
    }


def project_proposal(core: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    compiled = project_compile(core, params)
    revision = ScoreRevision.from_dict(compiled["revision"])
    dst = _project_output_path(core, revision, str(params.get("name") or ""))
    op = {
        "op_id": ulidish(),
        "type": "render_project",
        "args": {"project_id": revision.project_id, "revision_sha": revision.revision_sha, "dst": str(dst)},
        "preconditions": {"dst_absent": True},
    }
    manifest = core.write_manifest(
        "tastespec_project",
        int(revision.intent.get("seed") or 0),
        f"Render project '{compiled['project']['name']}' revision {revision.revision_sha[:10]}",
        [op],
    )
    return {
        **compiled,
        "manifest": manifest,
        "dst": str(dst),
        "engine_version": ENGINE_VERSION,
        "arrangement_sha": arrangement_sha(revision.arrangement),
        "tastespec": revision.intent["taste_profile"],
    }


def project_list(core: Any) -> Dict[str, Any]:
    return {"ok": True, "items": project_store(core).list_projects()}


def project_show(core: Any, project_id: str, revision_sha: str = "") -> Dict[str, Any]:
    store = project_store(core)
    record = store.load_project(project_id)
    revision = store.load_revision(project_id, revision_sha or None)
    return {"ok": True, "project": record.to_dict(), "revision": revision.to_dict()}


def project_history(core: Any, project_id: str) -> Dict[str, Any]:
    store = project_store(core)
    return {"ok": True, "project": store.load_project(project_id).to_dict(), "commands": store.history(project_id)}


def project_runs(core: Any, project_id: str) -> Dict[str, Any]:
    """List revision-bound render receipts for one visible project.

    The Workbench consumes these receipts to show publication history without
    scanning the global render directory or guessing which WAV belongs to which
    revision. Corrupt receipts remain visible as errors instead of disappearing.
    """
    store = project_store(core)
    project = store.load_project(project_id)
    render_dir = store.project_dir(project_id) / "renders"
    items: List[Dict[str, Any]] = []
    if render_dir.exists():
        for path in sorted(render_dir.glob("*.json"), key=lambda x: x.stat().st_mtime_ns, reverse=True):
            try:
                row = json.loads(path.read_text(encoding="utf-8"))
                if not isinstance(row, dict):
                    raise ValueError("receipt is not an object")
                row = dict(row)
                row["receipt_path"] = str(path)
                items.append(row)
            except Exception as exc:
                items.append({"receipt_path": str(path), "error": str(exc)})
    return {"ok": True, "project": project.to_dict(), "items": items}


def project_edit(core: Any, project_id: str, command: Dict[str, Any]) -> Dict[str, Any]:
    store = project_store(core)
    revision = store.load_revision(project_id)
    child = apply_project_command(core, revision, command)
    record = store.save_revision(child, revision.revision_sha, command)
    return {"ok": True, "project": record.to_dict(), "revision": child.to_dict()}


def project_undo(core: Any, project_id: str) -> Dict[str, Any]:
    record = project_store(core).undo(project_id)
    return {"ok": True, "project": record.to_dict()}


def project_redo(core: Any, project_id: str) -> Dict[str, Any]:
    record = project_store(core).redo(project_id)
    return {"ok": True, "project": record.to_dict()}


def _locked_targets(revision: ScoreRevision, target_type: str) -> set:
    out = {str(x.get("target_id")) for x in revision.locks if str(x.get("target_type")) == target_type}
    if target_type == "clip":
        out.update(str(c.get("clip_id")) for t in revision.tracks for c in (t.get("clips") or []) if c.get("locked"))
    return out


def _overlay_locks(base: ScoreRevision, fresh: Dict[str, Any]) -> Dict[str, Any]:
    arrangement = json.loads(json.dumps(fresh, ensure_ascii=False))
    locked_clips = _locked_targets(base, "clip")
    if locked_clips:
        old_layers = {
            str(layer.get("clip_id")): (si, dict(layer))
            for si, sec in enumerate(base.arrangement.get("sections") or [])
            for layer in (sec.get("layers") or [])
            if str(layer.get("clip_id")) in locked_clips
        }
        for clip_id, (section_index, old_layer) in old_layers.items():
            if section_index >= len(arrangement.get("sections") or []):
                raise ProjectValidationError(f"locked clip {clip_id} has no section in recompiled form")
            rail = rail_for_layer(old_layer)
            section = arrangement["sections"][section_index]
            layers = [l for l in (section.get("layers") or []) if rail_for_layer(l) != rail]
            layers.append(old_layer)
            section["layers"] = layers
    locked_transitions = _locked_targets(base, "transition")
    if locked_transitions:
        old_by_index = {
            int(t.get("section_index") or 0): t for t in base.transitions
            if str(t.get("transition_id")) in locked_transitions
        }
        for index, transition in old_by_index.items():
            if index >= len(arrangement.get("sections") or []):
                raise ProjectValidationError(f"locked transition {transition.get('transition_id')} has no section")
            old_raw = dict(base.arrangement["sections"][index].get("transition_in") or {})
            arrangement["sections"][index]["transition_in"] = old_raw
    return arrangement


def project_recompile(core: Any, project_id: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    params = dict(params or {})
    store = project_store(core)
    base = store.load_revision(project_id)
    taste_profile = str((base.intent.get("taste_profile") or {}).get("id") or "girl_talk_v1")
    pool = core.approved_atom_pool(taste_profile)
    compile_params = dict(base.arrangement.get("params") or {})
    compile_params.update(params)
    compile_params["taste_profile"] = taste_profile
    compile_params["name"] = str(base.intent.get("name") or "EarCrate Project")
    selected, _ = _compile_candidates(core, pool, compile_params, external_mode=bool(compile_params.get("external_foreground")))
    arrangement = _overlay_locks(base, selected["arrangement"])
    preflight = core.arrangement_preflight_gate(arrangement)
    taste_gate = core.taste_arrangement_gate(arrangement)
    if not preflight.get("passed") or (not compile_params.get("external_foreground") and not taste_gate.get("passed")):
        raise ProjectValidationError("locked constraints make recompile invalid: " + "; ".join((preflight.get("failures") or []) + (taste_gate.get("failures") or [])))
    child = revision_from_arrangement(
        core,
        arrangement,
        project_id=project_id,
        parent_revision_sha=base.revision_sha,
        created_by={"actor": "compiler", "reason": "recompile_unlocked", "compiler_version": "integrated_score_v1"},
        locks=base.locks,
        master_actions=[],
        static_gate_receipt={"preflight": preflight, "taste_gate": taste_gate},
        compiler_receipt={"candidate_search": arrangement.get("candidate_search") or {}, "preserved_locks": base.locks},
    )
    record = store.save_revision(child, base.revision_sha, {"actor": "compiler", "kind": "recompile_unlocked", "payload": params})
    return {"ok": True, "project": record.to_dict(), "revision": child.to_dict()}


def project_export(core: Any, project_id: str, destination: str = "", revision_sha: str = "") -> Dict[str, Any]:
    store = project_store(core)
    revision = store.load_revision(project_id, revision_sha or None)
    c = core.ensure_config()
    target = Path(destination).expanduser().resolve() if destination else (store.project_dir(project_id) / "exports").resolve()
    core.validate_not_master(target)
    core.validate_path_in_root(target, c.working_root)
    return export_project_bundle(revision, target)


def project_render(core: Any, project_id: str, dst: Optional[Path] = None,
                   revision_sha: str = "") -> Dict[str, Any]:
    store = project_store(core)
    revision = store.load_revision(project_id, revision_sha or None)
    project = store.load_project(project_id)
    if revision.revision_sha != project.active_revision_sha and not revision_sha:
        raise ProjectValidationError("project head moved before render")
    c = core.ensure_config()
    if not revision.master_actions:
        premaster_path = (store.project_dir(project_id) / "premasters" / f"{revision.score_sha}.premaster.wav").resolve()
        premaster_id = _register_revision_mashup(core, revision, "premaster", premaster_path)
        premaster_result = core.render_mashup(premaster_id, premaster_path)
        if premaster_result.get("type") == "render_rejected":
            raise ProjectValidationError("premaster could not execute: " + str(premaster_result.get("failure_kind") or "render rejected"))
        premaster_audio, _premaster_sr = sf.read(str(premaster_path), dtype="float32", always_2d=False)
        if premaster_audio.ndim > 1:
            premaster_audio = np.mean(premaster_audio, axis=1).astype(np.float32)
        policy = dict((revision.intent.get("compiled_policy") or {}))
        resolved = resolve_project_master_actions(premaster_audio.astype(np.float32), int(c.sample_rate), policy)
        if not resolved.get("passed"):
            raise ProjectValidationError("mastering resolver refused publication: " + str(resolved.get("refusal") or "outside persona envelope"))
        child = ScoreRevision.build(
            project_id=revision.project_id,
            parent_revision_sha=revision.revision_sha,
            created_by={"actor": "mastering_resolver", "reason": "explicit_master_plan", "resolver_version": "project_mastering_v1"},
            intent=revision.intent,
            arrangement=revision.arrangement,
            source_registry=revision.source_registry,
            tracks=revision.tracks,
            transitions=revision.transitions,
            master_actions=list(resolved.get("actions") or []),
            decisions=revision.decisions + [{
                "decision_id": "mastering_" + sha256_text(json_dumps(resolved))[:24],
                "kind": "mastering_plan",
                "selected": [x.get("kind") for x in (resolved.get("actions") or [])],
                "selected_score": 1.0,
                "allowed_by_policy": True,
                "renderable": True,
                "chosen_parameters": {"actions": resolved.get("actions") or []},
                "alternatives": [],
                "evidence": resolved,
            }],
            locks=revision.locks,
            static_gate_receipt=revision.static_gate_receipt,
            compiler_receipt={**revision.compiler_receipt, "mastering_resolution": resolved, "premaster": premaster_result},
        )
        store.save_revision(child, revision.revision_sha, {
            "actor": "mastering_resolver", "kind": "resolve_mastering", "payload": {"actions": resolved.get("actions") or []}
        })
        revision = child
    final_dst = Path(dst).resolve() if dst is not None else _project_output_path(core, revision, project.name)
    core.validate_not_master(final_dst)
    core.validate_path_in_root(final_dst, c.working_root / "renders")
    final_id = _register_revision_mashup(core, revision, "final", final_dst)
    result = core.render_mashup(final_id, final_dst)
    if result.get("type") == "render_rejected":
        return result
    result = dict(result)
    result.update({
        "type": "render_project",
        "project_id": revision.project_id,
        "revision_sha": revision.revision_sha,
        "score_sha": revision.score_sha,
    })
    receipt_path = store.project_dir(project_id) / "renders" / f"{Path(result['path']).stem}.json"
    _atomic_json(receipt_path, result)
    store.checkpoint(project_id, "render_verified", revision.revision_sha)
    return result

def project_preview(core: Any, project_id: str, *, start_beat: float = 0.0,
                    duration_beats: float = 16.0, dst: Optional[Path] = None,
                    revision_sha: str = "") -> Dict[str, Any]:
    """Render the exact revision and publish a bounded audition crop.

    The preview is derived from the same verified WAV as publication. It does not
    invent a second low-fidelity renderer or bypass project/mastering receipts.
    """
    rendered = project_render(core, project_id, revision_sha=revision_sha)
    if rendered.get("type") == "render_rejected" or not rendered.get("path"):
        return rendered
    store = project_store(core)
    revision = store.load_revision(project_id, str(rendered.get("revision_sha") or "") or None)
    bpm = float(revision.arrangement.get("bpm") or 0.0)
    if bpm <= 0:
        raise ProjectValidationError("project preview requires a positive BPM")
    if start_beat < 0 or duration_beats <= 0:
        raise ProjectValidationError("preview beat range must be nonnegative with positive duration")
    audio, audio_sr = sf.read(str(rendered["path"]), dtype="float32", always_2d=True)
    start_frame = max(0, int(round(float(start_beat) * 60.0 / bpm * audio_sr)))
    end_frame = min(audio.shape[0], int(round((float(start_beat) + float(duration_beats)) * 60.0 / bpm * audio_sr)))
    if end_frame <= start_frame:
        raise ProjectValidationError("preview range is outside the rendered project")
    target = Path(dst).expanduser().resolve() if dst is not None else (
        store.project_dir(project_id) / "previews" /
        f"{revision.revision_sha}-{start_beat:.3f}-{duration_beats:.3f}.wav"
    ).resolve()
    c = core.ensure_config()
    core.validate_not_master(target)
    core.validate_path_in_root(target, c.working_root)
    target.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(target), audio[start_frame:end_frame], audio_sr, subtype="PCM_24")
    receipt = {
        "ok": True,
        "type": "project_preview",
        "project_id": project_id,
        "revision_sha": revision.revision_sha,
        "score_sha": revision.score_sha,
        "source_render": str(rendered["path"]),
        "path": str(target),
        "start_beat": float(start_beat),
        "duration_beats": float(duration_beats),
        "start_frame": start_frame,
        "end_frame": end_frame,
        "sample_rate": int(audio_sr),
        "channels": int(audio.shape[1]),
        "render_report": rendered.get("report"),
    }
    _atomic_json(target.with_suffix(".preview.json"), receipt)
    return receipt

def project_acceptance(core: Any, destination: str) -> Dict[str, Any]:
    """Drive the integrated project lifecycle in an isolated visible workspace.

    This is a user-runnable acceptance receipt, not a hidden test helper. It uses
    the actual EarcrateCore, project store, multideck renderer, explicit mastering,
    command history, exports and source revalidation.
    """
    root = Path(destination).expanduser().resolve()
    if root.exists() and any(root.iterdir()):
        raise ProjectValidationError(f"acceptance destination must be empty: {root}")
    root.mkdir(parents=True, exist_ok=True)
    master = root / "music"
    work = root / "work"
    agent = root / "agent"
    for path in (master, work, agent):
        path.mkdir(parents=True, exist_ok=True)
    core.configure({
        "master_root": str(master),
        "working_root": str(work),
        "agent_root": str(agent),
        "sample_rate": 16000,
        "workers": 1,
    })
    sr = 16000
    duration_s = 16.0
    t = np.arange(int(sr * duration_s), dtype=np.float64) / sr
    floor = (0.18 * np.sin(2 * np.pi * 92.0 * t)
             + 0.08 * np.sin(2 * np.pi * 220.0 * t)
             + 0.025 * np.random.default_rng(11).normal(size=t.size)).astype(np.float32)
    gate = (0.5 + 0.5 * (np.sin(2 * np.pi * 2.0 * t) > 0)).astype(np.float64)
    vocal = (0.14 * np.sin(2 * np.pi * 440.0 * t) * gate
             + 0.05 * np.sin(2 * np.pi * 3200.0 * t) * gate
             + 0.02 * np.random.default_rng(22).normal(size=t.size)).astype(np.float32)
    floor_path = root / "floor.wav"
    vocal_path = root / "vocal.wav"
    sf.write(str(floor_path), floor, sr, subtype="FLOAT")
    sf.write(str(vocal_path), vocal, sr, subtype="FLOAT")
    arrangement = {
        "bpm": 96.0, "target_key": 0, "seed": 78,
        "params": {
            "taste_profile": "remix_prettylights_v1", "target_seconds": 10.0,
            "name": "Integrated Acceptance", "post_render_gate": True,
            "vocal_bed_ducking": True, "stem_policy": "intact_mix",
        },
        "sections": [
            {
                "bar_start": 0, "bars": 2, "type": "sustain", "target_key": 0,
                "transition_in": {"type": "start", "xfade_beats": 0},
                "layers": [
                    {"loop_id": "accept-floor-a", "external_ref": {"path": str(floor_path), "duration_s": duration_s, "start_s": 0.0, "len_s": 5.0}, "role": "harmony", "ear_role": "BED_CHORD", "bar_offset": 0, "bar_len": 2, "gain_db": -8.0},
                    {"loop_id": "accept-vocal-a", "external_ref": {"path": str(vocal_path), "duration_s": duration_s, "start_s": 0.0, "len_s": 5.0}, "role": "vocal", "ear_role": "VOX_HOOK", "bar_offset": 0, "bar_len": 2, "gain_db": -5.0},
                ],
            },
            {
                "bar_start": 2, "bars": 2, "type": "drop", "target_key": 0,
                "transition_in": {"type": "beatmatch_blend", "xfade_beats": 2, "curve": "equal_power", "bass_policy": "one_low_owner", "low_cutoff_hz": 170},
                "layers": [
                    {"loop_id": "accept-floor-b", "external_ref": {"path": str(floor_path), "duration_s": duration_s, "start_s": 6.0, "len_s": 5.0}, "role": "harmony", "ear_role": "BED_CHORD", "bar_offset": 0, "bar_len": 2, "gain_db": -7.0},
                    {"loop_id": "accept-vocal-b", "external_ref": {"path": str(vocal_path), "duration_s": duration_s, "start_s": 6.0, "len_s": 5.0}, "role": "vocal", "ear_role": "VOX_HOOK", "bar_offset": 0, "bar_len": 2, "gain_db": -4.0},
                ],
            },
        ],
    }
    imported = core.project_import_arrangement(
        arrangement, name="Integrated Acceptance",
        created_by={"actor": "acceptance", "reason": "integrated_cli_acceptance"},
        static_gate_receipt={"preflight": {"passed": True}, "taste_gate": {"passed": True}},
        compiler_receipt={"acceptance": "integrated_full_app_v1"},
    )
    project_id = imported["project"]["project_id"]
    first = core.project_render(project_id)
    if first.get("type") != "render_project" or not first.get("path"):
        raise ProjectValidationError("acceptance initial render did not publish")
    first_file_sha = sha256_file(Path(first["path"]))
    mastered_sha = str(first["revision_sha"])
    active = ScoreRevision.from_dict(core.project_show(project_id)["revision"])
    vocal_clip = next(c for t0 in active.tracks if t0.get("track_id") == "foreground" for c in t0.get("clips") or [])
    edited = core.project_edit(project_id, {
        "actor": "acceptance", "kind": "set_pan",
        "payload": {"clip_id": vocal_clip["clip_id"], "pan": 0.30, "override_policy": True},
    })
    edited_sha = str(edited["revision"]["revision_sha"])
    second = core.project_render(project_id)
    second_file_sha = sha256_file(Path(second["path"]))
    if second_file_sha == first_file_sha:
        raise ProjectValidationError("acceptance edit did not change the rendered artifact")
    core.project_undo(project_id)
    restored = core.project_render(project_id)
    restored_file_sha = sha256_file(Path(restored["path"]))
    if restored_file_sha != first_file_sha:
        raise ProjectValidationError("acceptance undo did not restore the exact artifact")
    core.project_redo(project_id)
    preview = core.project_preview(project_id, start_beat=2.0, duration_beats=4.0)
    exports = core.project_export(project_id)
    reopened = type(core)()
    reopened.load_config_if_present()
    reopened_head = reopened.project_show(project_id)["project"]["active_revision_sha"]
    if reopened_head != edited_sha:
        raise ProjectValidationError("acceptance restart did not reopen the edited head")
    original = floor_path.read_bytes()
    mutated = False
    try:
        changed_audio, changed_sr = sf.read(str(floor_path), dtype="float32", always_2d=False)
        changed_audio = np.asarray(changed_audio, dtype=np.float32)
        changed_audio[: min(200, changed_audio.shape[0])] *= -1.0
        sf.write(str(floor_path), changed_audio, changed_sr, subtype="FLOAT")
        try:
            mutation_result = reopened.project_render(project_id)
            mutated = mutation_result.get("type") == "render_rejected"
        except ProjectValidationError:
            mutated = True
    finally:
        floor_path.write_bytes(original)
    if not mutated:
        raise ProjectValidationError("acceptance source mutation was not refused")
    report = json.loads(Path(first["report"]).read_text(encoding="utf-8"))
    blend = next(t0 for t0 in report.get("transitions") or [] if t0.get("type") == "beatmatch_blend")
    receipt = {
        "ok": True, "acceptance": "integrated_full_app_v1",
        "engine_version": ENGINE_VERSION, "root": str(root),
        "project_id": project_id, "mastered_revision_sha": mastered_sha,
        "edited_revision_sha": edited_sha,
        "first_render_file_sha256": first_file_sha,
        "edited_render_file_sha256": second_file_sha,
        "undo_render_file_sha256": restored_file_sha,
        "edit_changes_render": second_file_sha != first_file_sha,
        "undo_restores_render": restored_file_sha == first_file_sha,
        "restart_reopens_active_revision": reopened_head == edited_sha,
        "source_change_refused": mutated,
        "all_selected_clips_executed": bool(report.get("render_integrity", {}).get("passed")),
        "all_transitions_executed": int(report.get("render_integrity", {}).get("planned_transition_count") or 0) == int(report.get("render_integrity", {}).get("executed_transition_count") or -1),
        "overlap_tail_executed": bool(blend.get("executed") and blend.get("applied") and int(blend.get("tail_deck_count") or 0) >= 1),
        "mastering_is_revision_data": bool(report.get("finishing", {}).get("actions")),
        "stereo_pan_executed": int(sf.info(str(second["path"])).channels) == 2,
        "preview": preview, "exports": exports,
        "first_render": first, "edited_render": second,
    }
    _atomic_json(root / "acceptance_receipt.json", receipt)
    return receipt


def project_piano(core: Any, *, personas: Optional[List[str]] = None,
                  max_iterations: int = 8, max_keeps: int = 0, max_seconds: float = 0.0,
                  target_seconds: float = 120.0, seed_base: int = 0,
                  name_prefix: str = "Piano", run_id: str = "", resume: bool = True) -> Dict[str, Any]:
    """The player piano: an unattended compile -> render -> keep/discard loop that
    runs entirely through immutable project revisions.

    Safe by construction on top of the v0.9 project authority: every attempt is a
    durable, content-addressed project revision; publication is the existing
    verification-gated render (a gate-refused set is DISCARDED, never a corrupt
    WAV); source mutation is refused. The loop is BOUNDED (max_iterations, and
    optionally max_keeps / max_seconds) and KILL-SAFE — the run receipt is
    rewritten atomically after every iteration, so a power cut leaves valid
    partial state and the next call with the same run_id RESUMES from where it
    stopped instead of redoing work. It invents no renderer and lowers no gate;
    it only decides which persona to compile next and records the verdict.
    """
    c = core.ensure_config()
    personas = [str(p) for p in (personas or ["girl_talk_v1"]) if str(p)] or ["girl_talk_v1"]
    max_iterations = max(1, int(max_iterations))
    runs_dir = (c.working_root / "piano").resolve()
    core.validate_not_master(runs_dir)
    core.validate_path_in_root(runs_dir, c.working_root)
    runs_dir.mkdir(parents=True, exist_ok=True)
    rid = str(run_id) or ("piano_" + ulidish())
    receipt_path = runs_dir / f"{rid}.json"

    attempts: List[Dict[str, Any]] = []
    started_at = now_utc()
    if resume and receipt_path.exists():
        with contextlib.suppress(Exception):
            prev = json.loads(receipt_path.read_text(encoding="utf-8"))
            attempts = list(prev.get("attempts") or [])
            started_at = str(prev.get("started_at") or started_at)

    def _by(verdict: str) -> List[Dict[str, Any]]:
        return [a for a in attempts if a.get("verdict") == verdict]

    def _flush(stop_reason: str = "", complete: bool = False) -> Dict[str, Any]:
        keeps = _by("kept")
        payload = {
            "ok": True, "type": "piano_run", "run_id": rid, "engine_version": ENGINE_VERSION,
            "started_at": started_at, "updated_at": now_utc(),
            "personas": personas, "max_iterations": max_iterations, "max_keeps": int(max_keeps),
            "max_seconds": float(max_seconds), "target_seconds": float(target_seconds),
            "attempted": len(attempts), "kept": len(keeps),
            "discarded": len(_by("discarded")), "errored": len(_by("error")),
            "keeps": [{"path": a.get("path"), "project_id": a.get("project_id"),
                       "revision_sha": a.get("revision_sha"), "persona": a.get("persona")} for a in keeps],
            "attempts": attempts,
            "stop_reason": stop_reason, "complete": bool(complete),
        }
        _atomic_json(receipt_path, payload)
        return payload

    _flush()  # a valid receipt exists before the first potentially-slow render
    t0 = time.time()
    stop_reason = "max_iterations"
    for i in range(len(attempts), max_iterations):
        if max_keeps and len(_by("kept")) >= int(max_keeps):
            stop_reason = "max_keeps"; break
        if max_seconds and (time.time() - t0) >= float(max_seconds):
            stop_reason = "max_seconds"; break
        persona = personas[i % len(personas)]
        seed = int(seed_base) + i
        rec: Dict[str, Any] = {"iteration": i, "persona": persona, "seed": seed, "at": now_utc()}
        try:
            compiled = core.project_compile({
                "taste_profile": persona, "target_seconds": float(target_seconds),
                "name": f"{name_prefix} {rid[-6:]} #{i:03d}", "seed": seed,
            })
            rec["project_id"] = compiled["project_id"]
            rec["revision_sha"] = compiled["revision_sha"]
            result = core.project_render(compiled["project_id"])
            if result.get("type") == "render_project" and result.get("path"):
                rec["verdict"] = "kept"
                rec["path"] = result["path"]
                rec["report"] = result.get("report")
                rec["revision_sha"] = str(result.get("revision_sha") or rec["revision_sha"])
            else:
                rec["verdict"] = "discarded"
                rec["reason"] = str(result.get("failure_kind") or result.get("type") or "render rejected")
        except ProjectValidationError as exc:
            # A principled refusal (gate, mastering envelope, source identity) is a
            # DISCARD, not an error — the loop is meant to throw sets away.
            rec["verdict"] = "discarded"
            rec["reason"] = f"refused: {exc}"
        except Exception as exc:  # pragma: no cover - defensive; keeps the night shift alive
            rec["verdict"] = "error"
            rec["reason"] = f"{type(exc).__name__}: {exc}"
        attempts.append(rec)
        _flush()
    return _flush(stop_reason=stop_reason, complete=True)


def project_piano_runs(core: Any) -> Dict[str, Any]:
    """List player-piano run receipts (working_root/piano/*.json) newest first, so
    the Workbench morning-triage view can show kept / discarded / refused attempts
    without scanning anything by hand."""
    c = core.ensure_config()
    runs_dir = (c.working_root / "piano").resolve()
    items: List[Dict[str, Any]] = []
    if runs_dir.exists():
        for path in sorted(runs_dir.glob("*.json"), key=lambda x: x.stat().st_mtime_ns, reverse=True):
            try:
                row = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(row, dict):
                    row = dict(row)
                    row["receipt_path"] = str(path)
                    items.append(row)
            except Exception as exc:
                items.append({"receipt_path": str(path), "error": str(exc)})
    return {"ok": True, "items": items}


def _attempt_atom_ids(core: Any, project_id: str, revision_sha: str) -> List[str]:
    """Every distinct approved-atom id that a piano attempt's revision actually
    used, so a triage keep/reject can feed the exact material back as judgments.
    The atom identity is carried on the arrangement layers (the bridge does not
    copy it onto the canonical clip), so read it there."""
    store = project_store(core)
    revision = store.load_revision(project_id, revision_sha or None)
    ids: List[str] = []
    seen = set()
    for section in (revision.arrangement.get("sections") or []):
        for layer in (section.get("layers") or []):
            aid = str(layer.get("atom_id") or "")
            if aid and aid not in seen:
                seen.add(aid)
                ids.append(aid)
    return ids


def project_piano_triage(core: Any, run_id: str, iteration: int, verdict: str) -> Dict[str, Any]:
    """Morning triage: keep/reject a piano attempt and write the judgment THROUGH
    the existing atom-judgment path so it becomes M4 training data. A keep marks
    every atom the attempt used approved; a reject marks them rejected. The verdict
    is recorded back on the run receipt so the view reflects it and re-triage is
    idempotent. Never fabricates: an attempt with no atom material judges nothing
    and says so."""
    verdict = str(verdict or "").lower()
    if verdict not in {"keep", "reject"}:
        raise ProjectValidationError("triage verdict must be 'keep' or 'reject'")
    c = core.ensure_config()
    receipt_path = (c.working_root / "piano" / f"{run_id}.json").resolve()
    core.validate_path_in_root(receipt_path, c.working_root)
    if not receipt_path.exists():
        raise ProjectValidationError(f"piano run not found: {run_id}")
    run = json.loads(receipt_path.read_text(encoding="utf-8"))
    attempt = next((a for a in (run.get("attempts") or []) if int(a.get("iteration", -1)) == int(iteration)), None)
    if attempt is None:
        raise ProjectValidationError(f"attempt {iteration} not found in run {run_id}")
    project_id = str(attempt.get("project_id") or "")
    persona = str(attempt.get("persona") or "girl_talk_v1")
    status = "approved" if verdict == "keep" else "rejected"
    judged = 0
    if project_id:
        with contextlib.suppress(Exception):
            for atom_id in _attempt_atom_ids(core, project_id, str(attempt.get("revision_sha") or "")):
                core.set_atom_judgment(atom_id, persona, status, "", verdict == "keep", False, f"piano_triage:{run_id}#{iteration}")
                judged += 1
    attempt["triage"] = {"verdict": verdict, "status": status, "atoms_judged": judged, "at": now_utc()}
    _atomic_json(receipt_path, run)
    return {"ok": True, "run_id": run_id, "iteration": int(iteration), "verdict": verdict,
            "status": status, "atoms_judged": judged, "persona": persona,
            "note": ("fed M4 as %d atom judgment(s)" % judged) if judged else
                    "attempt used no approved-atom material (e.g. an imported/external set) — verdict recorded, nothing to train on"}
