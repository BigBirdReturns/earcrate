from earcrate.core.deps import *
from earcrate.core.util import json_dumps, sha256_text, sha256_file, now_utc, safe_name
from earcrate.analyze.decode import decoded_audio_sha256
from earcrate.providers import stem_capability
from earcrate.project.model import ScoreRevision, ProjectValidationError
from earcrate.project.policy import compile_taste_policy


def _copy(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False, allow_nan=False))


def project_id_for(name: str, arrangement: Dict[str, Any]) -> str:
    payload = {
        "name": str(name or "EarCrate Project"),
        "taste_profile": str((arrangement.get("params") or {}).get("taste_profile") or "girl_talk_v1"),
        "seed": int(arrangement.get("seed") or (arrangement.get("params") or {}).get("seed") or 0),
        "arrangement": arrangement,
    }
    return "project_" + sha256_text(json_dumps(payload))[:24]


def rail_for_layer(layer: Dict[str, Any]) -> str:
    explicit = str(layer.get("rail") or "")
    if explicit in {"floor", "foreground", "spark"}:
        return explicit
    role = str(layer.get("role") or "")
    ear = str(layer.get("ear_role") or "")
    if role == "vocal" or ear in {"VOX_HOOK", "VOX_VERSE", "VOX_SHOUT"}:
        return "foreground"
    if role in {"texture", "fx"} or ear in {"PICKUP_FILL", "DROP_HIT", "TRANSITION_TAIL", "TEXTURE"}:
        return "spark"
    return "floor"


def _clip_id(section_index: int, layer_index: int, section: Dict[str, Any], layer: Dict[str, Any]) -> str:
    existing = str(layer.get("clip_id") or "")
    if existing:
        return existing
    payload = {
        "section_index": section_index,
        "layer_index": layer_index,
        "bar_start": int(section.get("bar_start") or 0),
        "bars": int(section.get("bars") or 0),
        "loop_id": layer.get("loop_id"),
        "external_ref": layer.get("external_ref"),
        "role": layer.get("role"),
        "ear_role": layer.get("ear_role"),
        "bar_offset": int(layer.get("bar_offset") or 0),
        "bar_len": int(layer.get("bar_len") or section.get("bars") or 0),
        "gain_db": float(layer.get("gain_db") or 0.0),
    }
    return "clip_" + sha256_text(json_dumps(payload))[:24]


def _library_source_row(core: Any, loop_id: str) -> Dict[str, Any]:
    row = core.conn().execute(
        """SELECT l.*, f.id AS source_file_id, f.path, f.sha256 AS file_sha256,
                  f.audio_sha256, f.audio_sha256_scope, COALESCE(f.audio_generation,0) AS audio_generation,
                  f.sample_rate, f.channels, f.duration_s,
                  ft.bpm, ft.bpm_confidence, ft.key_root, ft.key_mode, ft.key_confidence
           FROM loops l JOIN files f ON f.id=l.file_id
           LEFT JOIN features ft ON ft.file_id=f.id
           WHERE l.id=?""",
        (str(loop_id),),
    ).fetchone()
    if not row:
        raise ProjectValidationError(f"selected loop is missing from the catalog: {loop_id}")
    data = dict(row)
    if data.get("audio_sha256_scope") != "full" or not data.get("audio_sha256"):
        raise ProjectValidationError(f"selected loop lacks a full decoded-PCM identity: {loop_id}")
    return data


def _external_source(core: Any, ref: Dict[str, Any]) -> Dict[str, Any]:
    path = Path(str(ref.get("path") or "")).expanduser().resolve()
    if not path.is_file():
        raise ProjectValidationError(f"external project source does not exist: {path}")
    sr = int(core.ensure_config().sample_rate)
    duration_s = float(ref.get("duration_s") or 0.0)
    if duration_s <= 0:
        try:
            info = sf.info(str(path))
            duration_s = float(info.frames) / max(1, int(info.samplerate))
        except Exception:
            duration_s = 0.0
    expected_pcm = str(ref.get("pcm_sha") or ref.get("pcm_sha256") or "")
    actual_pcm = decoded_audio_sha256(path, sr, duration_s)
    if expected_pcm and expected_pcm != actual_pcm:
        raise ProjectValidationError(f"external source identity changed before project import: {path}")
    try:
        info = sf.info(str(path))
        source_sr, channels = int(info.samplerate), int(info.channels)
        if duration_s <= 0:
            duration_s = float(info.frames) / max(1, source_sr)
    except Exception:
        source_sr, channels = sr, 1
    return {
        "kind": "project_external",
        "path": str(path),
        "file_sha256": sha256_file(path),
        "pcm_sha256": actual_pcm,
        "sample_rate": source_sr,
        "channels": channels,
        "duration_s": duration_s,
        "audio_generation": 0,
        "capabilities": {
            "seekable": True,
            "project_scoped": True,
            "identity_anchor": True,
            "stem_source": "external_target",
        },
    }


def _stem_choice(arrangement: Dict[str, Any], layer: Dict[str, Any]) -> Dict[str, Any]:
    if layer.get("external_ref"):
        return {"choice": "external_target", "provider": "project", "reason": "project-scoped source is already the authored stem"}
    stem_policy = str((arrangement.get("params") or {}).get("stem_policy") or "separated")
    if stem_policy == "intact_mix":
        return {"choice": "mix", "provider": "catalog", "reason": "arrangement explicitly selected intact_mix"}
    capability = stem_capability()
    role = str(layer.get("role") or "")
    requested = "vocals" if role == "vocal" else "no_vocals" if role in {"drum_anchor", "bass", "harmony", "full"} else "mix"
    if requested != "mix" and not capability.get("ready"):
        return {
            "choice": "mix", "provider": "catalog",
            "reason": "stem provider unavailable at compile time; mix is an explicit score choice, not a render fallback",
            "requested_but_unavailable": requested,
            "capability": capability,
        }
    return {
        "choice": requested,
        "provider": "configured_stem_provider" if requested != "mix" else "catalog",
        "reason": "compiled from stem_policy and machine capability",
        "capability": capability,
    }


def _source_ref_id(source: Dict[str, Any]) -> str:
    return "source_" + sha256_text(json_dumps({
        "kind": source.get("kind"), "path": source.get("path"),
        "pcm_sha256": source.get("pcm_sha256"),
    }))[:24]


def canonicalize_arrangement(core: Any, arrangement: Dict[str, Any]) -> Tuple[Dict[str, Any], Dict[str, Dict[str, Any]], List[Dict[str, Any]]]:
    arr = _copy(arrangement)
    sections = list(arr.get("sections") or [])
    if not sections:
        raise ProjectValidationError("cannot create a project from an arrangement with no sections")
    registry: Dict[str, Dict[str, Any]] = {}
    tracks: Dict[str, Dict[str, Any]] = {
        "floor": {"track_id": "floor", "role": "floor", "gain_db": 0.0, "muted": False, "solo": False, "clips": []},
        "foreground": {"track_id": "foreground", "role": "foreground", "gain_db": 0.0, "muted": False, "solo": False, "clips": []},
        "spark": {"track_id": "spark", "role": "spark", "gain_db": 0.0, "muted": False, "solo": False, "clips": []},
    }
    bpm = float(arr.get("bpm") or 0.0)
    if bpm <= 0:
        raise ProjectValidationError("arrangement BPM must be positive")
    for section_index, section in enumerate(sections):
        layers = list(section.get("layers") or [])
        for layer_index, layer in enumerate(layers):
            cid = _clip_id(section_index, layer_index, section, layer)
            layer["clip_id"] = cid
            rail = rail_for_layer(layer)
            layer["rail"] = rail
            if layer.get("external_ref"):
                source = _external_source(core, dict(layer.get("external_ref") or {}))
                source_start_s = float((layer.get("external_ref") or {}).get("start_s") or 0.0)
                source_len_s = (layer.get("external_ref") or {}).get("len_s")
                source_end_s = source["duration_s"] if source_len_s is None else source_start_s + float(source_len_s)
                loop_id = str(layer.get("loop_id") or cid)
            else:
                loop_id = str(layer.get("loop_id") or "")
                if not loop_id:
                    raise ProjectValidationError(f"clip {cid} has neither loop_id nor external_ref")
                row = _library_source_row(core, loop_id)
                source = {
                    "kind": "library",
                    "path": str(Path(row["path"]).expanduser().resolve()),
                    "file_id": str(row["source_file_id"]),
                    "file_sha256": str(row.get("file_sha256") or ""),
                    "pcm_sha256": str(row["audio_sha256"]),
                    "sample_rate": int(row.get("sample_rate") or core.ensure_config().sample_rate),
                    "channels": int(row.get("channels") or 1),
                    "duration_s": float(row.get("duration_s") or 0.0),
                    "audio_generation": int(row.get("audio_generation") or 0),
                    "capabilities": {
                        "seekable": True, "catalog_managed": True,
                        "bpm": row.get("bpm"), "bpm_confidence": row.get("bpm_confidence"),
                        "key_root": row.get("key_root"), "key_mode": row.get("key_mode"),
                        "key_confidence": row.get("key_confidence"),
                    },
                }
                source_start_s = float(layer.get("source_start_s", row.get("start_s") or 0.0))
                source_end_s = float(layer.get("source_end_s", row.get("end_s") or 0.0))
            ref_id = _source_ref_id(source)
            registry.setdefault(ref_id, {**source, "source_ref_id": ref_id})
            stem = _stem_choice(arr, layer)
            layer["source_ref_id"] = ref_id
            layer["stem_choice"] = stem
            layer["source_start_s"] = source_start_s
            layer["source_end_s"] = source_end_s
            bar_offset = int(layer.get("bar_offset") or 0)
            bar_len = int(layer.get("bar_len") or section.get("bars") or 0)
            clip = {
                "clip_id": cid,
                "section_index": section_index,
                "track_id": rail,
                "rail": rail,
                "source_ref_id": ref_id,
                "loop_id": loop_id,
                "role": str(layer.get("role") or "full"),
                "ear_role": str(layer.get("ear_role") or ""),
                "stem_choice": _copy(stem),
                "source_start_s": source_start_s,
                "source_end_s": source_end_s,
                "timeline_start_beat": (int(section.get("bar_start") or 0) + bar_offset) * 4.0,
                "duration_beats": bar_len * 4.0,
                "gain_db": float(layer.get("gain_db") or 0.0),
                "pan": float(layer.get("pan") or 0.0),
                "fade_in_ms": float(layer.get("fade_in_ms") or 14.0),
                "fade_out_ms": float(layer.get("fade_out_ms") or 14.0),
                "muted": bool(layer.get("muted", False)),
                "solo": bool(layer.get("solo", False)),
                "locked": bool(layer.get("locked", False)),
                "transform": {
                    "mode": layer.get("transform_mode"),
                    "speed_ratio": layer.get("speed_ratio"),
                    "varispeed_pct": layer.get("varispeed_pct"),
                    "natural_pitch_shift": layer.get("natural_pitch_shift"),
                    "desired_key_shift": layer.get("desired_key_shift"),
                    "residual_pitch_shift": layer.get("residual_pitch_shift", layer.get("pitch_shift")),
                    "artifact_risk": layer.get("artifact_risk"),
                },
                "metadata": {
                    "world": layer.get("world"),
                    "source_track_key": layer.get("source_track_key"),
                    "dry_high3000_share": layer.get("dry_high3000_share"),
                    "dry_quality_score": layer.get("dry_quality_score"),
                },
            }
            tracks[rail]["clips"].append(clip)
        section["layers"] = layers
    arr["sections"] = sections
    ordered_tracks = [tracks[k] for k in ("floor", "foreground", "spark")]
    return arr, registry, ordered_tracks


def transition_records(arrangement: Dict[str, Any], tracks: List[Dict[str, Any]], source_registry: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    clips = [clip for track in tracks for clip in (track.get("clips") or [])]
    by_section: Dict[int, List[Dict[str, Any]]] = {}
    for clip in clips:
        by_section.setdefault(int(clip["section_index"]), []).append(clip)
    bpm = float(arrangement["bpm"])
    records: List[Dict[str, Any]] = []
    overlap_types = {"beatmatch_blend", "bass_swap", "hook_blend_over_bed"}
    zero_types = {"", "start", "impact_drop", "hard_cut", "hard_cut_to_air", "hard_cut_pickup", "bed_ride"}
    for index, section in enumerate(arrangement.get("sections") or []):
        raw = dict(section.get("transition_in") or {})
        typ = str(raw.get("type") or ("start" if index == 0 else "hard_cut"))
        boundary_beat = int(section.get("bar_start") or 0) * 4.0
        incoming = [c for c in by_section.get(index, []) if abs(float(c["timeline_start_beat"]) - boundary_beat) < 1e-6]
        outgoing = list(by_section.get(index - 1, [])) if index > 0 else []
        xfade_beats = 0.0 if typ in zero_types else float(raw.get("xfade_beats") or 0.0)
        required: List[str] = []
        reasons: List[str] = []
        if typ in overlap_types:
            required.append("outgoing_tail")
            need_s = xfade_beats * 60.0 / bpm
            available_s = max((float(source_registry[c["source_ref_id"]].get("duration_s") or 0.0) - float(c["source_end_s"]) for c in outgoing), default=0.0)
            if not outgoing:
                reasons.append("no outgoing clips at boundary")
            elif available_s + 1e-9 < need_s:
                reasons.append(f"outgoing source tail {available_s:.3f}s shorter than required {need_s:.3f}s")
        if typ == "acapella_bridge":
            required.append("vocal_material")
            if not any(c.get("role") == "vocal" for c in outgoing + incoming):
                reasons.append("no vocal clip at boundary")
        transition_id = str(raw.get("transition_id") or "transition_" + sha256_text(json_dumps({
            "section": index, "boundary_beat": boundary_beat, "type": typ,
            "outgoing": sorted(c["clip_id"] for c in outgoing),
            "incoming": sorted(c["clip_id"] for c in incoming),
            "xfade_beats": xfade_beats,
        }))[:24])
        raw["transition_id"] = transition_id
        section["transition_in"] = raw
        records.append({
            "transition_id": transition_id,
            "section_index": index,
            "boundary_beat": boundary_beat,
            "technique": typ,
            "duration_beats": xfade_beats,
            "curve": str(raw.get("curve") or ("none" if xfade_beats <= 0 else "equal_power")),
            "bass_policy": str(raw.get("bass_policy") or "one_low_owner"),
            "outgoing_clip_ids": sorted(c["clip_id"] for c in outgoing),
            "incoming_clip_ids": sorted(c["clip_id"] for c in incoming),
            "required_capabilities": required,
            "renderable": not reasons,
            "rejected_because": reasons,
            "parameters": _copy(raw),
        })
    return records


def decision_records(arrangement: Dict[str, Any], transitions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    decisions: List[Dict[str, Any]] = []
    search = dict(arrangement.get("candidate_search") or {})
    if search:
        decisions.append({
            "decision_id": "candidate_search",
            "kind": "candidate_selection",
            "selected": {k: search.get(k) for k in ("selected_seed", "selected_score", "mode") if k in search},
            "selected_score": float((search.get("selected_score") or {}).get("total", search.get("selected_score") or 0.0) if isinstance(search.get("selected_score"), dict) else search.get("selected_score") or 0.0),
            "allowed_by_policy": bool((search.get("selected_preflight") or {}).get("passed", True)),
            "renderable": True,
            "alternatives": _copy(search.get("alternatives") or []),
            "evidence": _copy(search),
        })
    ledger = (arrangement.get("taste_ledger") or {}).get("graph_receipts") or []
    for index, receipt in enumerate(ledger):
        decisions.append({
            "decision_id": f"pair:{index}", "kind": "compatibility_edge",
            "selected": {"left": receipt.get("left"), "right": receipt.get("right"), "relation": receipt.get("relation")},
            "selected_score": float(receipt.get("score") or 0.0),
            "allowed_by_policy": True, "renderable": True,
            "alternatives": [], "evidence": _copy(receipt),
        })
    for transition in transitions:
        decisions.append({
            "decision_id": transition["transition_id"], "kind": "transition",
            "selected": transition["technique"], "selected_score": 1.0 if transition["renderable"] else 0.0,
            "allowed_by_policy": True, "renderable": bool(transition["renderable"]),
            "chosen_parameters": {"duration_beats": transition["duration_beats"], "curve": transition["curve"], "bass_policy": transition["bass_policy"]},
            "alternatives": _copy(transition.get("parameters", {}).get("alternatives") or []),
            "evidence": {"required_capabilities": transition["required_capabilities"], "rejected_because": transition["rejected_because"]},
        })
    return decisions


def revision_from_arrangement(core: Any, arrangement: Dict[str, Any], *, project_id: str,
                              parent_revision_sha: Optional[str] = None,
                              created_by: Optional[Dict[str, Any]] = None,
                              locks: Optional[List[Dict[str, Any]]] = None,
                              master_actions: Optional[List[Dict[str, Any]]] = None,
                              static_gate_receipt: Optional[Dict[str, Any]] = None,
                              compiler_receipt: Optional[Dict[str, Any]] = None) -> ScoreRevision:
    taste_profile = str((arrangement.get("params") or {}).get("taste_profile") or "girl_talk_v1")
    policy = compile_taste_policy(taste_profile)
    arr, registry, tracks = canonicalize_arrangement(core, arrangement)
    transitions = transition_records(arr, tracks, registry)
    hard_constraints = policy.get("hard_constraints") or {}
    if hard_constraints.get("forbid_silent_layer_drop", True):
        bad = [t for t in transitions if not t.get("renderable")]
        if bad:
            details = "; ".join(f"{t['technique']}@{t['boundary_beat']}: {', '.join(t['rejected_because'])}" for t in bad[:6])
            raise ProjectValidationError("score contains transition(s) the selected sources cannot execute: " + details)
    profile = {
        "id": policy["profile_id"], "version": policy["version"],
        "hash": policy["source_profile_hash"],
        "compiled_policy_sha": policy["compiled_policy_sha"],
    }
    intent = {
        "name": str((arrangement.get("params") or {}).get("name") or "EarCrate Project"),
        "taste_profile": profile,
        "seed": int(arrangement.get("seed") or (arrangement.get("params") or {}).get("seed") or 0),
        "target_seconds": float((arrangement.get("params") or {}).get("target_seconds") or 0.0),
        "mode": str((arrangement.get("params") or {}).get("composition_mode") or policy.get("mode") or "taste_compiler"),
        "target_bpm": float(arrangement.get("bpm") or 0.0),
        "target_key": int(arrangement.get("target_key") or 0),
        "compiled_policy": policy,
    }
    receipt = {
        "compiler": "earcrate_integrated_score_v1",
        "legacy_arrangement_sha": sha256_text(json_dumps(arrangement)),
        "source_count": len(registry),
        "clip_count": sum(len(t["clips"]) for t in tracks),
        "transition_count": len(transitions),
        "policy_consumers": policy["consumers"],
        "policy_derivation": policy["derivation_receipt"],
        "renderer_contract": "revision hash -> exact arrangement -> existing EarCrate multideck DSP; no renderer-authored musical decisions",
        **_copy(compiler_receipt or {}),
    }
    return ScoreRevision.build(
        project_id=project_id,
        parent_revision_sha=parent_revision_sha,
        created_by=created_by or {"actor": "compiler", "reason": "arrangement_import", "compiler_version": "integrated_score_v1"},
        intent=intent,
        arrangement=arr,
        source_registry=registry,
        tracks=tracks,
        transitions=transitions,
        master_actions=master_actions or [],
        decisions=decision_records(arr, transitions),
        locks=locks or [],
        static_gate_receipt=static_gate_receipt or {},
        compiler_receipt=receipt,
    )


def arrangement_for_render(revision: ScoreRevision, mode: str) -> Dict[str, Any]:
    revision.validate()
    arrangement = _copy(revision.arrangement)
    params = dict(arrangement.get("params") or {})
    params.update({
        "project_render_mode": str(mode),
        "project_id": revision.project_id,
        "project_revision_sha": revision.revision_sha,
        "project_score_sha": revision.score_sha,
        "project_master_actions": _copy(revision.master_actions),
        # Premaster is an intermediate creative-record artifact. It must execute
        # every selected event, but it is intentionally not judged as a finished
        # publication before the explicit master plan exists.
        "post_render_gate": False if str(mode) == "premaster" else True,
    })
    arrangement["params"] = params
    arrangement["project"] = {
        "project_id": revision.project_id,
        "revision_sha": revision.revision_sha,
        "score_sha": revision.score_sha,
        "parent_revision_sha": revision.parent_revision_sha,
        "revision_created_at": revision.created_at,
    }
    # The score, not the renderer, resolves mute/solo. Keep a receipt of the full
    # authored clip set while handing the DSP only the executable selection.
    track_by_id = {str(t.get("track_id")): t for t in revision.tracks}
    any_track_solo = any(bool(t.get("solo")) for t in revision.tracks)
    any_clip_solo = any(bool(c.get("solo")) for t in revision.tracks for c in (t.get("clips") or []))
    active_ids = set()
    muted_ids = []
    for track in revision.tracks:
        track_active = not bool(track.get("muted")) and (not any_track_solo or bool(track.get("solo")))
        for clip in track.get("clips") or []:
            active = track_active and not bool(clip.get("muted")) and (not any_clip_solo or bool(clip.get("solo")))
            if active:
                active_ids.add(str(clip.get("clip_id")))
            else:
                muted_ids.append(str(clip.get("clip_id")))
    for section in arrangement.get("sections") or []:
        selected_layers = []
        for layer in section.get("layers") or []:
            if str(layer.get("clip_id") or "") not in active_ids:
                continue
            source_ref_id = str(layer.get("source_ref_id") or "")
            source = revision.source_registry.get(source_ref_id) or {}
            if layer.get("external_ref"):
                # Seal the compile-time sound identity into the executable input.
                # The renderer re-decodes and compares this value immediately before
                # reading the source window, closing the import-to-render race.
                ext_ref = dict(layer.get("external_ref") or {})
                ext_ref["pcm_sha256"] = str(source.get("pcm_sha256") or "")
                ext_ref["file_sha256"] = str(source.get("file_sha256") or "")
                ext_ref["duration_s"] = float(source.get("duration_s") or ext_ref.get("duration_s") or 0.0)
                layer["external_ref"] = ext_ref
            selected_layers.append(layer)
        section["layers"] = selected_layers
    arrangement["project_execution_selection"] = {
        "selected_clip_ids": sorted(active_ids),
        "muted_or_unsoloed_clip_ids": sorted(muted_ids),
        "selected_count": len(active_ids),
    }
    return arrangement
