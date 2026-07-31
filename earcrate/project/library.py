from __future__ import annotations

"""Read-only handshake between an adopted causal revision and a real library."""

from collections import Counter, defaultdict
from copy import deepcopy
import json
from pathlib import Path
import re
import sqlite3
from typing import Any, Mapping, Sequence

from earcrate.midi.codec import midi_read
from earcrate.midi.model import midi_sha256_json
from earcrate.rack.demand import rack_compile_demands, rack_validate_demands
from earcrate.rack.multizone import rack_propose_from_atoms

from .custody import project_verify_semantic_adoption
from .store import ProjectStore
from .util import ProjectError, ValidationError, now_utc, sha256_json

LIBRARY_HANDSHAKE_SCHEMA = "earcrate/real-library-handshake@1"


def _load_workspace_config(path: str | Path) -> dict[str, Any]:
    source = Path(path).expanduser().resolve()
    if not source.is_file():
        raise ProjectError(f"workspace config not found: {source}")
    data = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValidationError("workspace config must be a JSON object")
    if data.get("config_json") and not data.get("agent_root"):
        nested = Path(str(data["config_json"])).expanduser()
        return _load_workspace_config(nested if nested.is_absolute() else source.parent / nested)
    required = [key for key in ("master_root", "working_root", "agent_root") if not data.get(key)]
    if required:
        raise ValidationError("workspace config is missing: " + ", ".join(required))
    data["config_path"] = str(source)
    return data


def _db_path(config: Mapping[str, Any]) -> Path:
    agent = Path(str(config["agent_root"])).expanduser().resolve()
    for name in ("earcrate.sqlite", "jukebreaker.sqlite"):
        candidate = agent / name
        if candidate.is_file():
            return candidate
    raise ProjectError(f"no EarCrate database found under {agent}")


def _open_read_only(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    return connection


def _tables(db: sqlite3.Connection) -> set[str]:
    return {str(row[0]) for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}


def _columns(db: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in db.execute(f"PRAGMA table_info({table})").fetchall()}


def _expr(alias: str, columns: set[str], column: str, default: str, output: str | None = None) -> str:
    return f"{alias}.{column} AS {output or column}" if column in columns else f"{default} AS {output or column}"


def _required_ear_roles(revision: Mapping[str, Any]) -> dict[str, list[str]]:
    roles: dict[str, set[str]] = defaultdict(set)
    performance_roles: set[str] = set()
    for track in revision.get("tracks") or []:
        performance_roles.update(map(str, track.get("performance_roles") or []))
    mapping = {
        "kick": {"DRUM_BREAK"}, "snare": {"DRUM_BREAK"}, "hat": {"DRUM_BREAK", "TEXTURE"},
        "cymbal": {"TRANSITION_TAIL", "TEXTURE", "DRUM_BREAK"},
        "percussion": {"DRUM_BREAK", "PICKUP_FILL"}, "bass": {"BASS_RIFF"},
        "sub_bass": {"BASS_RIFF"}, "harmony": {"BED_CHORD", "RIFF_ID"},
        "pad": {"BED_CHORD", "TEXTURE"}, "lead": {"RIFF_ID", "BED_CHORD"},
        "impact": {"DROP_HIT", "RIFF_ID"},
        "fx": {"PICKUP_FILL", "DROP_HIT", "TRANSITION_TAIL", "TEXTURE"},
        "texture": {"TEXTURE", "TRANSITION_TAIL"},
    }
    for role in sorted(performance_roles):
        if role in {"sample_trigger", "vocal_guide"}:
            continue
        roles[role].update(mapping.get(role, {"TEXTURE"}))
    return {key: sorted(value) for key, value in sorted(roles.items())}


def _symbolic_track_names(revision: Mapping[str, Any]) -> set[str]:
    symbolic = {"sample_trigger", "vocal_guide"}
    return {str(track.get("name") or "") for track in revision.get("tracks") or [] if symbolic.intersection(map(str, track.get("performance_roles") or []))}


def _rack_demand_projection(ledger: Mapping[str, Any], revision: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    demand = rack_compile_demands(ledger)
    symbolic_names = _symbolic_track_names(revision)
    retained = [slot for slot in demand["slots"] if str(slot.get("track_name") or "") not in symbolic_names]
    excluded = [slot for slot in demand["slots"] if str(slot.get("track_name") or "") in symbolic_names]
    projected = deepcopy(demand)
    projected["slots"] = retained
    projected["slot_count"] = len(retained)
    projected["selected_event_count"] = sum(len(slot.get("events") or []) for slot in retained)
    projected["demand_sha256"] = midi_sha256_json({key: value for key, value in projected.items() if key != "demand_sha256"})
    rack_validate_demands(projected)
    return projected, {"source_demand_sha256": demand["demand_sha256"], "projected_demand_sha256": projected["demand_sha256"], "source_slot_count": len(demand["slots"]), "rack_slot_count": len(retained), "excluded_symbolic_slot_count": len(excluded), "excluded_symbolic_slots": [{"slot_id": slot.get("slot_id"), "track_name": slot.get("track_name"), "event_count": slot.get("event_count"), "reason": "SourcePhrase_or_neutral_vocal_guide_not_a_rack_demand"} for slot in excluded]}


def _approved_counts(db: sqlite3.Connection, profile: str) -> dict[str, int]:
    if "ear_atoms" not in _tables(db):
        return {}
    rows = db.execute("SELECT ear_role, COUNT(*) AS n FROM ear_atoms WHERE taste_profile=? AND status='approved' GROUP BY ear_role ORDER BY ear_role", (str(profile),)).fetchall()
    return {str(row["ear_role"]): int(row["n"]) for row in rows}


def _approved_atoms(db: sqlite3.Connection, profile: str, required_roles: Sequence[str], *, per_role_limit: int) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    tables = _tables(db)
    missing = sorted({"ear_atoms", "loops", "files"} - tables)
    if missing:
        raise ValidationError("library database is missing required tables: " + ", ".join(missing))
    ac, lc, fc = _columns(db, "ear_atoms"), _columns(db, "loops"), _columns(db, "files")
    tc = _columns(db, "tracks") if "tracks" in tables else set()
    xc = _columns(db, "features") if "features" in tables else set()
    select = [
        _expr("a", ac, "id", "''", "atom_id"), _expr("a", ac, "preview_path", "NULL"),
        _expr("a", ac, "ear_role", "'TEXTURE'"), _expr("a", ac, "render_role", "'texture'"),
        _expr("a", ac, "score", "0.0", "atom_score"), _expr("a", ac, "hook_score", "0.0"),
        _expr("a", ac, "bed_score", "0.0"), _expr("a", ac, "floor_score", "0.0"),
        _expr("a", ac, "bass_score", "0.0"), _expr("a", ac, "spark_score", "0.0"),
        _expr("a", ac, "intelligibility", "0.0"), _expr("a", ac, "low_share", "0.0"),
        _expr("a", ac, "mid_share", "0.0"), _expr("a", ac, "high_share", "0.0"),
        _expr("a", ac, "loopability", "0.0"), _expr("a", ac, "transient_density", "0.0"),
        _expr("a", ac, "metrics_json", "'{}'"), _expr("a", ac, "start_s", _expr("l", lc, "start_s", "0.0").split(" AS ")[0], "start_s"),
        _expr("a", ac, "end_s", _expr("l", lc, "end_s", "0.0").split(" AS ")[0], "end_s"),
        _expr("a", ac, "bars", _expr("l", lc, "bars", "0").split(" AS ")[0], "bars"),
        _expr("l", lc, "id", "''", "loop_id"), _expr("l", lc, "role", "'texture'", "role"),
        _expr("l", lc, "stem", "'mix'", "stem"), _expr("l", lc, "segment_id", "NULL"),
        _expr("f", fc, "id", "''", "file_id"), _expr("f", fc, "path", "''", "path"),
        _expr("f", fc, "duration_s", "0.0", "duration_s"), _expr("f", fc, "audio_sha256", "NULL"),
        _expr("f", fc, "audio_generation", "0"), _expr("t", tc, "artist", "NULL"),
        _expr("t", tc, "album", "NULL"), _expr("t", tc, "title", "NULL"), _expr("t", tc, "year", "NULL"),
        _expr("x", xc, "bpm", _expr("a", ac, "bpm", "120.0").split(" AS ")[0], "bpm"),
        _expr("x", xc, "key_root", _expr("a", ac, "key_root", "0").split(" AS ")[0], "key_root"),
        _expr("x", xc, "key_mode", "1"), _expr("x", xc, "energy", "0.0"), _expr("x", xc, "vocal_likelihood", "0.0"),
    ]
    joins = ["JOIN loops l ON l.id=a.loop_id", "JOIN files f ON f.id=a.file_id"]
    joins.append("LEFT JOIN tracks t ON t.file_id=f.id" if "tracks" in tables else "LEFT JOIN (SELECT NULL artist,NULL album,NULL title,NULL year,NULL file_id) t ON 1=0")
    joins.append("LEFT JOIN features x ON x.file_id=f.id" if "features" in tables else "LEFT JOIN (SELECT NULL bpm,NULL key_root,NULL key_mode,NULL energy,NULL vocal_likelihood,NULL file_id) x ON 1=0")
    freshness = []
    if "present" in fc: freshness.append("COALESCE(f.present,1)=1")
    if "audio_sha256_scope" in fc and "audio_sha256" in fc: freshness.append("f.audio_sha256_scope='full' AND f.audio_sha256 IS NOT NULL")
    tail = " AND " + " AND ".join(freshness) if freshness else ""
    sql = f"SELECT {', '.join(select)} FROM ear_atoms a {' '.join(joins)} WHERE a.taste_profile=? AND a.status='approved' AND a.ear_role=?{tail} ORDER BY a.score DESC,a.id LIMIT ?"
    atoms: list[dict[str, Any]] = []; queried: dict[str, int] = {}
    for role in sorted(set(required_roles)):
        rows = db.execute(sql, (str(profile), str(role), max(1, int(per_role_limit)))).fetchall(); queried[role] = len(rows)
        for row in rows:
            item = dict(row)
            try: metrics = json.loads(str(item.get("metrics_json") or "{}"))
            except Exception: metrics = {}
            item.update({key: value for key, value in metrics.items() if key not in item or item.get(key) in {None, 0, 0.0, ""}})
            item["id"] = item.get("loop_id") or item.get("atom_id"); item["score"] = float(item.get("atom_score") or 0.0)
            item["role"] = item.get("render_role") or item.get("role") or "texture"; item["source_track_key"] = str(item.get("file_id") or item.get("path") or "")
            item["dry_quality_score"] = max(float(item.get("dry_quality_score") or 0.0), item["score"] * 0.65)
            item["dry_high3000_share"] = float(item.get("high_share") or 0.0); item["dry_low200_share"] = float(item.get("low_share") or 0.0); item["dry_quality_veto"] = False
            atoms.append(item)
    dedup: dict[str, dict[str, Any]] = {}; missing_paths = []; usable: Counter[str] = Counter()
    for atom in atoms:
        key = str(atom.get("atom_id") or atom.get("id") or "")
        if not key or key in dedup: continue
        if not Path(str(atom.get("path") or "")).is_file(): missing_paths.append({"atom_id": key, "ear_role": atom.get("ear_role"), "path": atom.get("path")}); continue
        dedup[key] = atom; usable[str(atom.get("ear_role") or "")] += 1
    return list(dedup.values()), {"queried_roles": queried, "usable_roles": dict(sorted(usable.items())), "freshness_predicates": freshness, "returned_atom_count": len(dedup), "missing_source_path_count": len(missing_paths), "missing_source_paths": missing_paths[:50]}


def _identity_tokens(label: str) -> list[str]:
    stop = {"the", "opening", "verse", "phrase", "vocal", "source", "recording"}
    return [token for token in re.findall(r"[A-Za-z0-9]+", str(label).lower()) if len(token) >= 3 and token not in stop]


def _source_phrase_candidates(db: sqlite3.Connection, intentions: Sequence[Mapping[str, Any]], limit: int = 12) -> list[dict[str, Any]]:
    if not {"files", "tracks"}.issubset(_tables(db)): return []
    results = []
    for intent in intentions:
        label = str(intent.get("identity_label") or ""); tokens = _identity_tokens(label)[:6]
        if not tokens: results.append({"intent_id": intent.get("intent_id"), "identity_label": label, "candidates": []}); continue
        clauses, args = [], []
        for token in tokens:
            clauses.append("(LOWER(COALESCE(t.artist,'')) LIKE ? OR LOWER(COALESCE(t.title,'')) LIKE ? OR LOWER(COALESCE(t.album,'')) LIKE ?)"); args.extend([f"%{token}%"] * 3)
        args.append(max(1, int(limit)))
        rows = [dict(row) for row in db.execute(f"SELECT f.id file_id,f.path,f.duration_s,f.audio_sha256,t.artist,t.title,t.album FROM files f LEFT JOIN tracks t ON t.file_id=f.id WHERE {' AND '.join(clauses)} ORDER BY f.path LIMIT ?", args).fetchall()]
        for row in rows: row.update({"path_exists": Path(str(row.get("path") or "")).is_file(), "binding_status": "catalog_match_unbound", "publication_eligibility": "unreviewed"})
        results.append({"intent_id": intent.get("intent_id"), "identity_label": label, "tokens": tokens, "candidates": rows})
    return results


def _logical_role_coverage(role_map: Mapping[str, Sequence[str]], approved: Mapping[str, int], fresh: Mapping[str, int]) -> tuple[dict[str, Any], dict[str, Any]]:
    coverage, shortages = {}, {}
    for logical, alternatives in sorted(role_map.items()):
        row = {"acceptable_ear_roles": list(alternatives), "approved_inventory_count": sum(int(approved.get(role, 0)) for role in alternatives), "fresh_usable_count": sum(int(fresh.get(role, 0)) for role in alternatives)}
        row["satisfied"] = row["fresh_usable_count"] > 0; coverage[logical] = row
        if not row["satisfied"]: shortages[logical] = row
    return coverage, shortages


def project_real_library_handshake(store: ProjectStore, project_id: str, *, workspace_config: str | Path, revision_sha: str | None = None, taste_profile: str | None = None, per_role_limit: int = 2000, maximum_transpose_semitones: float = 18.0, max_zones_per_slot: int = 8, combination_beam_width: int = 64) -> dict[str, Any]:
    revision = store.load_revision(project_id, revision_sha); adoption = project_verify_semantic_adoption(store, project_id, revision["revision_sha"])
    config = _load_workspace_config(workspace_config); database = _db_path(config)
    profile = str(taste_profile or (((revision.get("intent") or {}).get("taste_profile") or {}).get("id") or ""))
    if not profile: raise ValidationError("a taste profile is required for library handshake")
    role_map = _required_ear_roles(revision); required_roles = sorted({role for values in role_map.values() for role in values})
    with _open_read_only(database) as db:
        db.execute("BEGIN"); before = int(db.execute("PRAGMA data_version").fetchone()[0]); table_names = _tables(db)
        counts = _approved_counts(db, profile); atoms, atom_receipt = _approved_atoms(db, profile, required_roles, per_role_limit=per_role_limit)
        intentions = list((revision.get("semantic_state") or {}).get("source_phrase_intentions") or []); phrase_candidates = _source_phrase_candidates(db, intentions)
        after = int(db.execute("PRAGMA data_version").fetchone()[0]); db.execute("ROLLBACK")
    midi_path = store.resolve_artifact(project_id, revision["performance"]["artifacts"]["midi"]); ledger = midi_read(midi_path); rack_demand, projection = _rack_demand_projection(ledger, revision)
    solver_error = None
    if atoms and rack_demand.get("slots"):
        try:
            proposal = rack_propose_from_atoms(rack_demand, atoms, taste_profile=profile, top_k=8, maximum_transpose_semitones=float(maximum_transpose_semitones), max_zones_per_slot=max(1, int(max_zones_per_slot)), combination_beam_width=max(1, int(combination_beam_width)))
            rack_plan = {"complete": bool(proposal.get("complete")), "slot_count": len(proposal.get("slots") or []), "zone_count": int(proposal.get("zone_count") or 0), "unresolved": proposal.get("unresolved") or [], "proposal": proposal}
        except Exception as exc:
            solver_error = {"error_type": type(exc).__name__, "message": str(exc)}; rack_plan = {"complete": False, "slot_count": len(rack_demand.get("slots") or []), "zone_count": 0, "unresolved": [], "proposal": None}
    else:
        solver_error = {"error_type": "NoApprovedMaterial" if not atoms else "NoRackDemands", "message": "no usable approved EarAtoms" if not atoms else "score contains no non-symbolic rack demands"}; rack_plan = {"complete": False, "slot_count": len(rack_demand.get("slots") or []), "zone_count": 0, "unresolved": [], "proposal": None}
    coverage, shortages = _logical_role_coverage(role_map, counts, atom_receipt.get("usable_roles") or {})
    unresolved_phrases = [row["intent_id"] for row in phrase_candidates if not any(bool(candidate.get("path_exists")) for candidate in row.get("candidates") or [])]
    handshake_ok = bool(adoption["adoption_ok"] and table_names and before == after)
    receipt = {"schema": LIBRARY_HANDSHAKE_SCHEMA, "created_at": now_utc(), "ok": handshake_ok, "ok_scope": "library_handshake", "status": {"project_semantic_adoption_ok": bool(adoption["adoption_ok"]), "library_opened_read_only": True, "candidate_search_ready": bool(not shortages and atoms and solver_error is None), "complete_rack_binding_possible": bool(rack_plan["complete"]), "source_phrase_discovery_ready": not unresolved_phrases, "ready_to_materialize": bool(handshake_ok and not shortages and rack_plan["complete"] and not unresolved_phrases), "source_phrase_execution_ready": False, "production_graph_ready": False, "publication_ok": False}, "project": {"project_id": project_id, "revision_sha": revision["revision_sha"], "midi_semantic_sha256": ledger["semantic_sha256"], "required_ear_roles": role_map, "logical_role_coverage": coverage, "rack_demand_projection": projection}, "library": {"config_path": str(config["config_path"]), "database_path": str(database), "taste_profile": profile, "tables": sorted(table_names), "approved_counts": counts, "queried_atom_count": len(atoms), "atom_query_receipt": atom_receipt, "read_only": True, "snapshot": {"data_version_before": before, "data_version_after": after, "consistent": before == after}}, "shortages": {"role_shortages": shortages, "rack_unresolved": rack_plan["unresolved"], "rack_solver_error": solver_error, "source_phrase_unresolved_intent_ids": unresolved_phrases}, "source_phrase_candidates": phrase_candidates, "rack_proposal": rack_plan["proposal"], "mutations": {"library_writes": 0, "source_decodes": 0, "sample_materializations": 0}}
    receipt["handshake_sha256"] = sha256_json({key: value for key, value in receipt.items() if key != "handshake_sha256"})
    run = store.new_run(project_id, revision["revision_sha"], "real_library_handshake"); store.write_run_artifact(run, "library-handshake.json", receipt); store.finish_run(run, handshake_ok, receipt)
    return receipt


__all__ = ["LIBRARY_HANDSHAKE_SCHEMA", "project_real_library_handshake"]
