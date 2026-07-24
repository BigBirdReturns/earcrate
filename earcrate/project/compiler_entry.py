from __future__ import annotations

from typing import Any, Mapping

from .compiler_beam import _compile_beam
from .compiler_deck import _candidate_decks
from .compiler_gate import static_gate
from .compiler_source_crate import prepare_crate_sources
from .compiler_source_manifest import load_source_manifest, prepare_manifest_sources
from .model import default_tracks, make_transition_id, new_revision
from .policy import compile_policy
from .store import ProjectStore
from .util import ValidationError, deep_copy_json, random_id, sha256_json, stable_id


def _tracks_from_sections(sections: list[dict[str, Any]]) -> list[dict[str, Any]]:
    tracks = default_tracks()
    by_role = {str(track["role"]): track for track in tracks}
    for section in sections:
        for clip in section.get("clips") or []:
            rail = str(clip.get("role") or "")
            selected_track = None
            for candidate in tracks:
                if str(candidate["track_id"]) == str(clip.get("track_id") or ""):
                    selected_track = candidate
                    break
            if selected_track is None:
                ear = str(clip.get("ear_role") or "")
                if ear in {"VOX_HOOK", "VOX_VERSE", "VOX_SHOUT", "RIFF_ID"} and rail == "vocal":
                    selected_track = by_role["foreground"]
                elif ear in {"PICKUP_FILL", "DROP_HIT", "TRANSITION_TAIL"} or rail in {"fx", "texture"}:
                    selected_track = by_role["spark"]
                else:
                    ordinal = list(section.get("clips") or []).index(clip)
                    selected_track = by_role["floor" if ordinal == 0 else "foreground" if ordinal == 1 else "spark"]
            selected_track["clips"].append(deep_copy_json(clip))
    return tracks


def _compile_transitions(sections: list[dict[str, Any]], policy: Mapping[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    transitions: list[dict[str, Any]] = []
    decisions: list[dict[str, Any]] = []
    allowed = set((policy.get("transition_policy") or {}).get("allowed") or [])
    for index in range(1, len(sections)):
        previous = sections[index - 1]
        current = sections[index]
        outgoing = [str(clip["clip_id"]) for clip in previous.get("clips") or []]
        incoming = [str(clip["clip_id"]) for clip in current.get("clips") or []]
        technique = "hard_cut"
        if "beatmatch_blend" in allowed and previous.get("type") not in {"drop", "breakdown"} and current.get("type") not in {"drop"}:
            technique = "beatmatch_blend"
        elif "hard_cut_pickup" in allowed and current.get("type") == "drop":
            technique = "hard_cut_pickup"
        elif allowed:
            technique = sorted(allowed)[0]
        boundary = float(current.get("start_beat") or 0.0)
        transition_id = make_transition_id(boundary, outgoing, incoming)
        transition = {
            "transition_id": transition_id,
            "boundary_beat": boundary,
            "outgoing_clip_ids": outgoing,
            "incoming_clip_ids": incoming,
            "technique": technique,
            "duration_beats": 0.0 if technique.startswith("hard_cut") else min(4.0, float(current.get("duration_beats") or 4.0)),
            "curve": "equal_power" if "blend" in technique else "step",
            "capability_receipt": {"available": True, "reason": "compiled by canonical project compiler"},
            "decision_id": stable_id("decision", {"transition": transition_id, "technique": technique}),
        }
        transitions.append(transition)
        decisions.append({
            "decision_id": transition["decision_id"],
            "kind": "transition",
            "selected": technique,
            "boundary_beat": boundary,
            "alternatives": sorted(allowed - {technique}),
        })
    return transitions, decisions


def compile_project(
    store: ProjectStore,
    *,
    name: str,
    profile: str | Mapping[str, Any],
    source_manifest: str | Mapping[str, Any] | None = None,
    from_crate: bool = False,
    target_seconds: float = 120.0,
    seed: int = 1,
    sample_rate: int = 44_100,
    analysis_seconds: float = 180.0,
    constraints: Mapping[str, Any] | None = None,
    mode: str = "automatic",
    beam_width: int = 12,
    project_id: str | None = None,
) -> dict[str, Any]:
    if bool(source_manifest) == bool(from_crate):
        raise ValidationError("compile requires exactly one of source_manifest or from_crate")
    if float(target_seconds) <= 0.0:
        raise ValidationError("target_seconds must be positive")
    policy_bundle = compile_policy(profile)
    policy = policy_bundle["compiled_policy"]
    profile_row = policy["profile"]
    policy_receipt = policy_bundle["receipt"]
    if from_crate:
        sources, candidates, source_receipt = prepare_crate_sources(str(profile_row["id"]), sample_rate=int(sample_rate))
    else:
        manifest = load_source_manifest(source_manifest or {})
        sources, candidates, source_receipt = prepare_manifest_sources(
            manifest,
            sample_rate=int(sample_rate),
            analysis_seconds=float(analysis_seconds),
        )
    decks = _candidate_decks(candidates, policy, dict(constraints or {}), limit=4)
    if not decks:
        raise ValidationError("no transform-feasible deck exists")
    deck = decks[0]
    form_variant = str((constraints or {}).get("form_variant") or "balanced")
    result = _compile_beam(
        sources=sources,
        candidates=candidates,
        policy=policy,
        deck=deck,
        target_seconds=float(target_seconds),
        seed=int(seed),
        form_variant=form_variant,
        beam_width=max(1, int(beam_width)),
    )
    tracks = _tracks_from_sections(list(result["sections"]))
    transitions, transition_decisions = _compile_transitions(list(result["sections"]), policy)
    decisions = list(result.get("decisions") or []) + transition_decisions
    gate = static_gate(
        tracks=tracks,
        transitions=transitions,
        sources=sources,
        policy=policy,
        bpm=float(result["bpm"]),
        total_bars=int(result["total_bars"]),
    )
    if not gate["passed"]:
        raise ValidationError("compiled project failed its TasteSpec gate: " + ", ".join(gate["failures"]))
    pid = str(project_id or random_id("project"))
    revision = new_revision(
        project_id=pid,
        parent_revision_sha=None,
        created_by={"actor": "earcrate", "reason": "compile_project"},
        intent={
            "taste_profile": {"id": profile_row["id"], "version": profile_row["version"], "hash": profile_row["hash"]},
            "seed": int(seed),
            "target_seconds": float(target_seconds),
            "mode": str(mode),
            "compiled_policy": policy,
            "compiled_policy_sha": sha256_json(policy),
        },
        sources=sources,
        tracks=tracks,
        transitions=transitions,
        decisions=decisions,
        static_gate_receipt=gate,
        compiler_receipt={
            "schema": "earcrate/project-compiler-receipt@1",
            "policy_receipt": policy_receipt,
            "source_receipt": source_receipt,
            "candidate_count": len(candidates),
            "decks": decks,
            "selected_deck": deck,
            "beam": {key: value for key, value in result.items() if key != "sections"},
            "compiler_sha256": sha256_json({"deck": deck, "sections": result["sections"], "decisions": decisions}),
        },
        tempo_map=[{"beat": 0.0, "bpm": float(result["bpm"]), "meter": [4, 4]}],
        compile_request={
            "name": str(name),
            "profile": str(profile_row["id"]),
            "target_seconds": float(target_seconds),
            "seed": int(seed),
            "sample_rate": int(sample_rate),
            "analysis_seconds": float(analysis_seconds),
            "constraints": dict(constraints or {}),
            "from_crate": bool(from_crate),
            "form_variant": form_variant,
        },
    )
    created = store.create_project(name, revision, project_id=pid)
    return {
        "ok": True,
        "project": created["project"],
        "revision": created["revision"],
        "path": created["path"],
        "selected_deck": deck,
        "static_gate": gate,
    }
