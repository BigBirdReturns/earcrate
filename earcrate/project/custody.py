from __future__ import annotations

"""Gate 8.0 historical custody and semantic adoption.

MIDI remains a transparent compositional control layer.  Custody preserves the
selected artifact family exactly; adoption adds replayable musical meaning in a
child revision without altering the accepted note ledger.
"""

from collections import defaultdict
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
from typing import Any, Mapping, Sequence

from earcrate.midi.codec import midi_read
from earcrate.midi.render import midi_compile_note_spans, midi_render_file
from earcrate.music.director import music_render_director_score, music_validate_stage_score

from .causal_revision import CAUSAL_PERFORMANCE_SCHEMA, causal_seal_revision
from .store import ProjectStore
from .util import ProjectError, ValidationError, atomic_write_json, now_utc, random_id, sha256_file, sha256_json, stable_id

SEED_SELECTION_SCHEMA = "earcrate/first30-seed-selection@1"
CUSTODY_GATE_SCHEMA = "earcrate/gate8-custody-gate@1"
SEMANTIC_ADOPTION_SCHEMA = "earcrate/gate8-semantic-state@1"
PRODUCER_STATUSES = {"accepted", "conditional", "rejected", "pending"}


def _load_object(path: str | Path, label: str) -> dict[str, Any]:
    source = Path(path).expanduser().resolve()
    if not source.is_file():
        raise ProjectError(f"missing {label}: {source}")
    try:
        value = json.loads(source.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ValidationError(f"invalid {label}: {source}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValidationError(f"{label} must contain a JSON object")
    return value


def _canonical_json_hash(value: Mapping[str, Any], excluded: Sequence[str] = ()) -> str:
    return sha256_json({str(k): v for k, v in value.items() if str(k) not in set(excluded)})


def _canonical_pcm_sha256(path: str | Path) -> str:
    import numpy as np
    import soundfile as sf
    audio, sample_rate = sf.read(str(Path(path).expanduser().resolve()), always_2d=True, dtype="float32")
    payload = int(sample_rate).to_bytes(4, "little") + int(audio.shape[1]).to_bytes(2, "little")
    payload += np.asarray(audio, dtype="<f4").tobytes(order="C")
    return hashlib.sha256(payload).hexdigest()


def _audio_info(path: str | Path) -> tuple[int, float]:
    import soundfile as sf
    info = sf.info(str(Path(path).expanduser().resolve()))
    return int(info.samplerate), float(info.frames / info.samplerate)


def _score_family(score: Mapping[str, Any]) -> str:
    if str(score.get("schema") or "") == "earcrate/dj-stage-score@1":
        return "dj_stage_score"
    if str(score.get("kind") or "") == "earcrate_player_piano_composition":
        return "player_piano_composition"
    raise ValidationError("unsupported causal-score artifact family")


def _historical_renderer_identity(score: Mapping[str, Any]) -> str:
    return "earcrate_dj_director_proof_renderer_v1" if _score_family(score) == "dj_stage_score" else "earcrate_neutral_midi_renderer_v1"


def _midi_note_ledger(compiled: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows = [{"track_index": int(span.get("track_index") or 0), "channel": int(span.get("channel") or 0), "note": int(span.get("note") or 0), "velocity": int(span.get("velocity") or 0), "program": int(span.get("program") or 0), "start_tick": int(span.get("start_tick") or 0), "end_tick": int(span.get("end_tick") or 0)} for span in compiled.get("note_spans") or []]
    rows.sort(key=lambda row: (row["start_tick"], row["track_index"], row["channel"], row["note"], row["end_tick"]))
    return rows


def _score_note_ledger(score: Mapping[str, Any]) -> list[dict[str, Any]]:
    if _score_family(score) != "dj_stage_score":
        return []
    rows = []
    for event in score.get("events") or []:
        if str(event.get("kind") or "") != "note":
            continue
        rows.append({"event_id": str(event.get("event_id") or ""), "track": str(event.get("track") or ""), "channel": int(event.get("channel") or 0), "start_tick": int(event.get("start_tick") or 0), "duration_tick": int(event.get("duration_tick") or 0), "pitch": int(event.get("pitch") or 0), "velocity": int(event.get("velocity") or 0), "role": str(event.get("role") or ""), "rail": str(event.get("rail") or ""), "section_id": str(event.get("section_id") or ""), "source_event_ids": sorted(map(str, event.get("source_event_ids") or []))})
    rows.sort(key=lambda row: (row["start_tick"], row["track"], row["channel"], row["pitch"], row["event_id"]))
    return rows


def _score_track_rows(score: Mapping[str, Any]) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    if _score_family(score) == "player_piano_composition":
        events = [{"track": str(e.get("voice_id") or e.get("role") or "Unassigned"), "role": str(e.get("role") or ""), "rail": "", "kind": "note"} for e in ((score.get("final_state") or {}).get("events") or [])]
    else:
        events = list(score.get("events") or [])
    for event in events:
        if str(event.get("kind") or "") == "marker":
            continue
        name = str(event.get("track") or "Unassigned")
        row = grouped.setdefault(name, {"track_id": stable_id("track", {"name": name}), "role": "aux", "name": name, "clips": [], "performance_event_count": 0, "performance_roles": set(), "performance_rails": set()})
        row["performance_event_count"] += 1
        row["performance_roles"].add(str(event.get("role") or ""))
        row["performance_rails"].add(str(event.get("rail") or ""))
    priority = {"foreground": 3, "floor": 2, "spark": 1, "": 0}
    for row in grouped.values():
        rails = sorted(row["performance_rails"], key=lambda value: (-priority.get(value, 0), value))
        selected = rails[0] if rails else "aux"
        row["role"] = selected if selected in {"floor", "foreground", "spark"} else "aux"
        row["performance_roles"] = sorted(filter(None, row["performance_roles"]))
        row["performance_rails"] = sorted(filter(None, row["performance_rails"]))
    return sorted(grouped.values(), key=lambda row: (row["role"], row["name"]))


def _verify_score_midi_pair(score: Mapping[str, Any], midi_path: str | Path) -> dict[str, Any]:
    family = _score_family(score)
    if family == "dj_stage_score":
        music_validate_stage_score(score)
        score_ledger = _score_note_ledger(score)
        score_notes = len(score_ledger)
        score_hash = str(score.get("score_sha256") or _canonical_json_hash(score, ("score_sha256",)))
        expected_tracks = int((score.get("metrics") or {}).get("sounding_track_count") or 0)
    else:
        source_events = list(((score.get("final_state") or {}).get("events") or []))
        if not source_events:
            raise ValidationError("player-piano composition contains no events")
        score_notes = len(source_events)
        score_ledger = []
        score_hash = str(score.get("composition_sha256") or _canonical_json_hash(score, ("composition_sha256",)))
        expected_tracks = len({str(e.get("voice_id") or "") for e in source_events if e.get("voice_id")})
    ledger = midi_read(Path(midi_path).expanduser().resolve())
    compiled = midi_compile_note_spans(ledger)
    midi_ledger = _midi_note_ledger(compiled)
    occupied = int((compiled.get("diagnostics") or {}).get("occupied_track_count") or 0)
    if score_notes != len(midi_ledger):
        raise ValidationError(f"score/MIDI note count mismatch: score={score_notes}, MIDI={len(midi_ledger)}")
    if expected_tracks and occupied != expected_tracks:
        raise ValidationError(f"score/MIDI sounding-track mismatch: score={expected_tracks}, MIDI={occupied}")
    note_ledger = score_ledger if family == "dj_stage_score" else midi_ledger
    return {"score_family": family, "historical_renderer_identity": _historical_renderer_identity(score), "score_sha256": score_hash, "midi_semantic_sha256": str(ledger["semantic_sha256"]), "midi_type": int(ledger["midi_type"]), "ticks_per_beat": int(ledger["ticks_per_beat"]), "declared_track_count": len(ledger["tracks"]), "occupied_track_count": occupied, "note_count": len(midi_ledger), "note_ledger_sha256": sha256_json(note_ledger), "score_note_ledger": score_ledger, "midi_note_ledger": midi_ledger}


def _producer_status(verdict: Mapping[str, Any], explicit: str | None) -> str:
    status = str(explicit or verdict.get("verdict") or "pending").lower().strip()
    if status not in PRODUCER_STATUSES:
        raise ValidationError(f"producer status must be one of {sorted(PRODUCER_STATUSES)}")
    return status


def project_seed_selection_receipt(*, family_id: str, midi_path: str | Path, score_path: str | Path, evidence_path: str | Path | None, plan_path: str | Path | None, historical_neutral_render: str | Path, producer_verdict_path: str | Path, producer_status: str | None, actor: str, reason: str, supersedes: Sequence[str] = ()) -> dict[str, Any]:
    score = _load_object(score_path, "causal score")
    pair = _verify_score_midi_pair(score, midi_path)
    verdict = _load_object(producer_verdict_path, "producer verdict")
    status = _producer_status(verdict, producer_status)
    neutral = Path(historical_neutral_render).expanduser().resolve()
    if not neutral.is_file():
        raise ProjectError(f"missing historical neutral render: {neutral}")
    artifacts: dict[str, Any] = {"midi": {"path": str(Path(midi_path).resolve()), "raw_sha256": sha256_file(midi_path), "semantic_sha256": pair["midi_semantic_sha256"], "note_count": pair["note_count"], "track_count": pair["declared_track_count"]}, "score": {"path": str(Path(score_path).resolve()), "raw_sha256": sha256_file(score_path), "canonical_payload_sha256": pair["score_sha256"], "score_family": pair["score_family"]}, "historical_neutral_render": {"path": str(neutral), "raw_sha256": sha256_file(neutral), "canonical_pcm_sha256": _canonical_pcm_sha256(neutral), "renderer_identity": pair["historical_renderer_identity"]}, "producer_verdict": {"path": str(Path(producer_verdict_path).resolve()), "raw_sha256": sha256_file(producer_verdict_path), "verdict": str(verdict.get("verdict") or status)}}
    for key, path in (("evidence", evidence_path), ("plan", plan_path)):
        if path:
            value = _load_object(path, key)
            artifacts[key] = {"path": str(Path(path).resolve()), "raw_sha256": sha256_file(path), "canonical_payload_sha256": _canonical_json_hash(value, tuple(k for k in value if str(k).endswith("_sha256")))}
    receipt = {"schema": SEED_SELECTION_SCHEMA, "family_id": str(family_id), "selection_status": "selected_for_custody", "selected_at": now_utc(), "actor": str(actor), "reason": str(reason), "producer_status": status, "publication_ok": status == "accepted" and bool(verdict.get("publication_ok")), "superseded_families": sorted(set(map(str, supersedes))), "artifacts": artifacts, "canonicalization": {"json": "UTF-8 sorted-key canonical JSON", "pcm": "sample-rate + channel-count + interleaved little-endian float32"}}
    receipt["selection_sha256"] = _canonical_json_hash(receipt, ("selection_sha256",))
    return receipt


def _write_json_artifact(store: ProjectStore, project_id: str, label: str, value: Mapping[str, Any]) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="earcrate-gate8-") as directory:
        path = Path(directory) / f"{label}.json"
        atomic_write_json(path, dict(value))
        row = store.import_artifact(project_id, path, label=label)
    row["canonical_payload_sha256"] = _canonical_json_hash(value, tuple(k for k in value if str(k).endswith("_sha256")))
    return row


def project_import_causal_score(store: ProjectStore, *, name: str, family_id: str, midi_path: str | Path, score_path: str | Path, evidence_path: str | Path | None, plan_path: str | Path | None, historical_neutral_render: str | Path, producer_verdict_path: str | Path, profile: str | Mapping[str, Any], producer_status: str | None = None, actor: str = "producer", reason: str = "Gate 8.0 historical custody import", supersedes: Sequence[str] = (), project_id: str | None = None) -> dict[str, Any]:
    selection = project_seed_selection_receipt(family_id=family_id, midi_path=midi_path, score_path=score_path, evidence_path=evidence_path, plan_path=plan_path, historical_neutral_render=historical_neutral_render, producer_verdict_path=producer_verdict_path, producer_status=producer_status, actor=actor, reason=reason, supersedes=supersedes)
    score = _load_object(score_path, "causal score")
    pair = _verify_score_midi_pair(score, midi_path)
    pid = str(project_id or random_id("project"))
    inputs = {"midi": (midi_path, "seed-midi"), "score": (score_path, "seed-score"), "historical_neutral_render": (historical_neutral_render, "historical-neutral"), "producer_verdict": (producer_verdict_path, "producer-verdict")}
    if evidence_path: inputs["evidence"] = (evidence_path, "seed-evidence")
    if plan_path: inputs["plan"] = (plan_path, "seed-plan")
    artifacts = {key: store.import_artifact(pid, path, label=label) for key, (path, label) in inputs.items()}
    artifacts["midi"].update({"semantic_sha256": pair["midi_semantic_sha256"], "note_count": pair["note_count"], "declared_track_count": pair["declared_track_count"], "occupied_track_count": pair["occupied_track_count"]})
    artifacts["score"].update({"score_sha256": pair["score_sha256"], "score_family": pair["score_family"]})
    artifacts["historical_neutral_render"].update({"canonical_pcm_sha256": _canonical_pcm_sha256(historical_neutral_render), "renderer_identity": pair["historical_renderer_identity"]})
    artifacts["seed_selection"] = _write_json_artifact(store, pid, "seed-selection", selection)
    _, duration = _audio_info(historical_neutral_render)
    profile_id = str(profile.get("id") if isinstance(profile, Mapping) else profile)
    performance = {"schema": CAUSAL_PERFORMANCE_SCHEMA, "stage": "historical_custody", "family_id": str(family_id), "artifacts": artifacts, "midi_semantic_sha256": pair["midi_semantic_sha256"], "score_sha256": pair["score_sha256"], "note_ledger_sha256": pair["note_ledger_sha256"], "note_count": pair["note_count"], "duration_seconds": float(score.get("duration_seconds") or duration), "score_family": pair["score_family"], "historical_renderer_identity": pair["historical_renderer_identity"], "final_renderer_identity": None, "producer_status": selection["producer_status"], "publication_ok": False}
    gate = {"schema": CUSTODY_GATE_SCHEMA, "passed": True, "scope": "historical_custody", "midi_semantics_preserved": True, "historical_neutral_pcm_preserved": True, "score_midi_note_count_equal": True, "recomposition_count": 0, "unknown_semantics_preserved": True, "publication_ok": False}
    gate["gate_sha256"] = _canonical_json_hash(gate, ("gate_sha256",))
    revision = {"schema_version": 1, "project_id": pid, "parent_revision_sha": None, "created_at": now_utc(), "created_by": {"actor": str(actor), "reason": str(reason)}, "intent": {"taste_profile": {"id": profile_id, "version": "1.0.0", "hash": ""}, "seed": 0, "target_seconds": performance["duration_seconds"], "mode": "causal_score_custody"}, "sources": {}, "tempo_map": [{"beat": 0.0, "bpm": float(score.get("tempo_bpm") or 120.0), "meter": [4, 4]}], "tracks": _score_track_rows(score), "transitions": [], "automation": [], "mastering": {"state": "unresolved", "actions": []}, "decisions": [], "locks": [{"path": "performance.note_ledger", "reason": "historical custody", "actor": str(actor)}, {"path": "performance.artifacts", "reason": "hash-bound seed family", "actor": str(actor)}], "static_gate_receipt": gate, "compiler_receipt": {"schema": "earcrate/gate8-custody-import-receipt@1", "selection_sha256": selection["selection_sha256"], "no_recomposition": True}, "compile_request": {"kind": "historical_custody_import", "family_id": str(family_id)}, "authority_kind": "causal_score", "performance": performance, "semantic_state": {"schema": SEMANTIC_ADOPTION_SCHEMA, "status": "unadopted", "known_unknowns": ["motif and phrase state not adopted", "eligible SourcePhrase bindings unresolved", "sealed rack bindings unresolved", "final production graph unresolved"]}, "custody": {"seed_selection_sha256": selection["selection_sha256"], "gate": gate}}
    revision = causal_seal_revision(revision)
    created = store.create_project(name, revision, project_id=pid)
    verification = project_verify_custody(store, pid, created["revision"]["revision_sha"])
    return {"ok": verification["custody_ok"], "ok_scope": "historical_custody", **created, "selection": selection, "verification": verification}


def _resolve_score(store: ProjectStore, project_id: str, revision: Mapping[str, Any]) -> tuple[dict[str, Any], Path]:
    path = store.resolve_artifact(project_id, revision["performance"]["artifacts"]["score"])
    return _load_object(path, "project score"), path


def _rerender_historical(score: Mapping[str, Any], midi_path: Path, output: Path, sample_rate: int) -> None:
    if _score_family(score) == "dj_stage_score":
        music_render_director_score(score, output, sample_rate=sample_rate, overwrite=True)
    else:
        midi_render_file(midi_path, output, sample_rate=sample_rate, overwrite=True)


def project_verify_custody(store: ProjectStore, project_id: str, revision_sha: str | None = None) -> dict[str, Any]:
    revision = store.load_revision(project_id, revision_sha)
    performance = revision.get("performance") or {}
    score, _ = _resolve_score(store, project_id, revision)
    midi = store.resolve_artifact(project_id, performance["artifacts"]["midi"])
    neutral = store.resolve_artifact(project_id, performance["artifacts"]["historical_neutral_render"])
    pair = _verify_score_midi_pair(score, midi)
    sample_rate, _ = _audio_info(neutral)
    with tempfile.TemporaryDirectory(prefix="earcrate-custody-verify-") as directory:
        rerender = Path(directory) / "neutral.wav"
        _rerender_historical(score, midi, rerender, sample_rate)
        rerender_pcm = _canonical_pcm_sha256(rerender)
    expected_pcm = str(performance["artifacts"]["historical_neutral_render"].get("canonical_pcm_sha256") or "")
    checks = {"midi_semantic_matches": pair["midi_semantic_sha256"] == str(performance.get("midi_semantic_sha256") or ""), "score_hash_matches": pair["score_sha256"] == str(performance.get("score_sha256") or ""), "note_ledger_matches": pair["note_ledger_sha256"] == str(performance.get("note_ledger_sha256") or ""), "historical_artifact_pcm_matches": _canonical_pcm_sha256(neutral) == expected_pcm, "historical_rerender_pcm_matches": rerender_pcm == expected_pcm, "artifacts_verified": bool(store.validate_store(project_id)["ok"])}
    return {"schema": "earcrate/gate8-custody-verification@1", "project_id": project_id, "revision_sha": revision["revision_sha"], "custody_ok": all(checks.values()), "checks": checks, "score_midi_pair": pair, "historical_pcm_sha256": expected_pcm, "rerender_pcm_sha256": rerender_pcm, "failures": [key for key, value in checks.items() if not value]}


def project_adoption_readiness(score: Mapping[str, Any]) -> dict[str, Any]:
    failures: list[str] = []
    if _score_family(score) != "dj_stage_score":
        return {"ready": False, "failures": ["unsupported_score_schema"]}
    sections = list(score.get("sections") or []); dialogue = list(score.get("source_dialogue") or []); obligations = list(score.get("phrase_obligations") or [])
    motif_ids = sorted({str((event.get("metadata") or {}).get("motif_id") or "") for event in score.get("events") or [] if (event.get("metadata") or {}).get("motif_id")})
    foreground_events = [event for event in score.get("events") or [] if str(event.get("rail") or "") == "foreground" or str(event.get("role") or "") in {"vocal", "sample_trigger"}]
    if not sections: failures.append("missing_sections")
    if not motif_ids: failures.append("missing_motif_state")
    if not dialogue: failures.append("missing_source_dialogue")
    if not obligations: failures.append("missing_phrase_obligations")
    if not foreground_events: failures.append("missing_foreground_intention")
    return {"ready": not failures, "failures": failures, "section_count": len(sections), "motif_ids": motif_ids, "source_dialogue_count": len(dialogue), "obligation_count": len(obligations), "foreground_event_count": len(foreground_events)}


def _derive_semantic_state(score: Mapping[str, Any], annotations: Mapping[str, Any] | None = None) -> dict[str, Any]:
    readiness = project_adoption_readiness(score)
    if not readiness["ready"]:
        raise ValidationError("score is custody-valid but not semantic-adoption-ready: " + ",".join(readiness["failures"]))
    motifs: dict[str, list[str]] = defaultdict(list); source_intents: list[dict[str, Any]] = []
    for event in score.get("events") or []:
        metadata = event.get("metadata") or {}; motif = str(metadata.get("motif_id") or "")
        if motif: motifs[motif].append(str(event.get("event_id") or ""))
        if metadata.get("literal_identity") or str(event.get("role") or "") in {"vocal", "sample_trigger"}:
            source_intents.append({"intent_id": "source-intent:" + str(event.get("event_id") or ""), "identity_label": str(metadata.get("sample_slice") or event.get("track") or "foreground source"), "role": str(event.get("role") or "foreground"), "destination_start_tick": int(event.get("start_tick") or 0), "destination_end_tick": int(event.get("start_tick") or 0) + int(event.get("duration_tick") or 0), "status": "symbolic_intention"})
    ownership = [{"section_id": str(row.get("section_id") or ""), "start_seconds": float(row.get("start_seconds") or 0.0), "end_seconds": float(row.get("end_seconds") or 0.0), "foreground_owner": row.get("foreground_owner"), "floor_owner": row.get("floor_owner"), "low_end_owner": row.get("low_end_owner"), "rails": list(row.get("rails") or []), "operator_stack": list(row.get("operator_stack") or [])} for row in score.get("sections") or []]
    state = {"schema": SEMANTIC_ADOPTION_SCHEMA, "status": "adopted", "sections": deepcopy(list(score.get("sections") or [])), "motifs": [{"motif_id": key, "event_ids": sorted(filter(None, values)), "recurrence_count": len(values)} for key, values in sorted(motifs.items())], "source_dialogue": deepcopy(list(score.get("source_dialogue") or [])), "phrase_obligations": deepcopy(list(score.get("phrase_obligations") or [])), "ownership_timeline": ownership, "groove_state": {"ticks_per_beat": int(score.get("ticks_per_beat") or 480), "grid_offset_tick": int(score.get("grid_offset_tick") or 0), "tempo_bpm": float(score.get("tempo_bpm") or 120.0)}, "source_phrase_intentions": source_intents, "human_locks": [], "known_unknowns": ["eligible source regions unresolved until source alignment", "final rack and production bindings unresolved"]}
    for key, value in dict(annotations or {}).items(): state[str(key)] = deepcopy(value)
    state["semantic_state_sha256"] = sha256_json({key: value for key, value in state.items() if key != "semantic_state_sha256"})
    return state


def project_adopt_causal_semantics(store: ProjectStore, project_id: str, *, revision_sha: str | None = None, annotations: Mapping[str, Any] | None = None, actor: str = "producer", reason: str = "Gate 8.0 semantic adoption", expected_head: str | None = None) -> dict[str, Any]:
    project = store.load_project(project_id); head = str(project.get("active_revision_sha") or ""); base_sha = str(revision_sha or head)
    if expected_head is not None and head != expected_head: raise ValidationError(f"project head moved: expected {expected_head}, found {head}")
    if base_sha != head: raise ValidationError("semantic adoption must extend the active project head")
    parent = store.load_revision(project_id, base_sha)
    if str((parent.get("performance") or {}).get("stage") or "") != "historical_custody": raise ValidationError("semantic adoption requires a historical-custody parent")
    score, _ = _resolve_score(store, project_id, parent); state = _derive_semantic_state(score, annotations)
    pair = _verify_score_midi_pair(score, store.resolve_artifact(project_id, parent["performance"]["artifacts"]["midi"]))
    if pair["note_ledger_sha256"] != parent["performance"]["note_ledger_sha256"]: raise ValidationError("semantic adoption would change accepted notes")
    child = deepcopy(parent); child.pop("revision_sha", None); child.pop("created_at", None); child["parent_revision_sha"] = head; child["created_by"] = {"actor": str(actor), "reason": str(reason)}
    child["performance"]["stage"] = "semantic_adoption"; child["semantic_state"] = state
    child.setdefault("decisions", []).append({"decision_id": "semantic-adoption:" + state["semantic_state_sha256"][:20], "kind": "semantic_adoption", "actor": str(actor), "reason": str(reason), "accepted_note_ledger_unchanged": True})
    child["static_gate_receipt"] = {"passed": True, "gate": "semantic_adoption", "accepted_note_ledger_unchanged": True, "continuation_ready": True}
    sealed = causal_seal_revision(child)
    committed = store.commit_revision(project_id, sealed, expected_head=head, event="causal_semantics_adopted", event_payload={"semantic_state_sha256": state["semantic_state_sha256"]})
    verification = project_verify_semantic_adoption(store, project_id, sealed["revision_sha"])
    return {"ok": verification["adoption_ok"], **committed, "verification": verification}


def project_verify_semantic_adoption(store: ProjectStore, project_id: str, revision_sha: str | None = None) -> dict[str, Any]:
    revision = store.load_revision(project_id, revision_sha); performance = revision.get("performance") or {}; state = revision.get("semantic_state") or {}; parent_sha = str(revision.get("parent_revision_sha") or ""); parent = store.load_revision(project_id, parent_sha) if parent_sha else None
    checks = {"stage_is_semantic_adoption": str(performance.get("stage") or "") in {"semantic_adoption", "source_execution", "production"}, "semantic_state_adopted": str(state.get("status") or "") == "adopted", "sections_present": bool(state.get("sections")), "motifs_present": bool(state.get("motifs")), "source_dialogue_present": bool(state.get("source_dialogue")), "ownership_present": bool(state.get("ownership_timeline")), "source_phrase_intentions_present": bool(state.get("source_phrase_intentions")), "note_ledger_unchanged": bool(parent and parent.get("performance", {}).get("note_ledger_sha256") == performance.get("note_ledger_sha256")), "semantic_hash_valid": state.get("semantic_state_sha256") == sha256_json({key: value for key, value in state.items() if key != "semantic_state_sha256"})}
    return {"schema": "earcrate/gate8-semantic-adoption-verification@1", "project_id": project_id, "revision_sha": revision["revision_sha"], "adoption_ok": all(checks.values()), "checks": checks, "failures": [key for key, value in checks.items() if not value]}


def project_render_causal_score(store: ProjectStore, project_id: str, output_path: str | Path, *, revision_sha: str | None = None, overwrite: bool = False) -> dict[str, Any]:
    revision = store.load_revision(project_id, revision_sha); score, _ = _resolve_score(store, project_id, revision); output = Path(output_path).expanduser().resolve(); sample_rate, _ = _audio_info(store.resolve_artifact(project_id, revision["performance"]["artifacts"]["historical_neutral_render"]))
    if _score_family(score) == "dj_stage_score": receipt = music_render_director_score(score, output, sample_rate=sample_rate, overwrite=overwrite)
    else: receipt = midi_render_file(store.resolve_artifact(project_id, revision["performance"]["artifacts"]["midi"]), output, sample_rate=sample_rate, overwrite=overwrite)
    return {"ok": True, "project_id": project_id, "revision_sha": revision["revision_sha"], "output": str(output), "raw_sha256": sha256_file(output), "canonical_pcm_sha256": _canonical_pcm_sha256(output), "render_receipt": receipt}


__all__ = ["_score_note_ledger", "_score_track_rows", "_verify_score_midi_pair", "project_seed_selection_receipt", "project_import_causal_score", "project_verify_custody", "project_adoption_readiness", "project_adopt_causal_semantics", "project_verify_semantic_adoption", "project_render_causal_score"]
