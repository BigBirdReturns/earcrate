#!/usr/bin/env python3
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import secrets
import subprocess
import sys
from typing import Any, Iterable, Mapping, Sequence

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from earcrate import reference_zero as rz

HEX64 = re.compile(r"^[0-9a-f]{64}$")
GIT_OID = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
TERMINAL_STATES = {"qualified", "rejected", "failed", "blocked"}
CHILDREN = ("gold-v7-arc", "gold-v7-interplay", "gold-v7-production")


class ContractError(RuntimeError):
    pass


def load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ContractError(f"invalid JSON: {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ContractError(f"JSON object required: {path}")
    return value


def canonical_sha256(payload: Mapping[str, Any], field: str) -> str:
    body = deepcopy(dict(payload))
    body.pop(field, None)
    encoded = json.dumps(
        body,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def legacy_review_sha256(payload: Mapping[str, Any], field: str) -> str:
    body = deepcopy(dict(payload))
    body.pop(field, None)
    encoded = (
        json.dumps(
            body,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def require_hex64(value: Any, label: str) -> str:
    text = str(value or "").lower()
    if not HEX64.fullmatch(text):
        raise ContractError(f"{label} must be a lowercase SHA-256")
    return text


def require_git_oid(value: Any, label: str) -> str:
    text = str(value or "").lower()
    if not GIT_OID.fullmatch(text):
        raise ContractError(f"{label} must be a 40- or 64-hex Git object id")
    return text


def load_contract(path: Path) -> dict[str, Any]:
    contract = load_json(path)
    if contract.get("kind") != "earcrate_track_iteration_contract":
        raise ContractError("wrong contract kind")
    if int(contract.get("schema_version") or 0) != 1:
        raise ContractError("unsupported contract schema")
    if contract.get("track_id") != "A1-07":
        raise ContractError("this runner is restricted to A1-07")
    declared = require_hex64(contract.get("contract_sha256"), "contract_sha256")
    observed = canonical_sha256(contract, "contract_sha256")
    if declared != observed:
        raise ContractError(
            f"contract seal mismatch: declared {declared}, observed {observed}"
        )
    rows = [dict(row) for row in contract.get("children") or []]
    if tuple(row.get("candidate_id") for row in rows) != CHILDREN:
        raise ContractError("contract must declare the exact three v7 children")
    if int((contract.get("machine_admission") or {}).get("minimum_qualified_children", 0)) != 2:
        raise ContractError("v7 requires a two-child machine gate")
    require_hex64(
        (contract.get("parent") or {}).get("owner_review_receipt_sha256"),
        "parent.owner_review_receipt_sha256",
    )
    return contract


def current_git_head() -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
        shell=False,
    )
    value = result.stdout.strip().lower()
    if result.returncode != 0 or not GIT_OID.fullmatch(value):
        raise ContractError("cannot resolve the exact Git head")
    return value


def verify_parent(contract: Mapping[str, Any], receipt: Path) -> dict[str, Any]:
    if not receipt.is_file() or receipt.is_symlink():
        raise ContractError(f"regular owner receipt required: {receipt}")
    expected = str(contract["parent"]["owner_review_receipt_sha256"])
    payload = load_json(receipt)
    declared = require_hex64(payload.get("review_sha256"), "review_sha256")
    observed = legacy_review_sha256(payload, "review_sha256")
    if declared != observed:
        raise ContractError(
            f"parent owner receipt seal mismatch: declared {declared}, observed {observed}"
        )
    if observed != expected:
        raise ContractError(
            f"wrong parent owner receipt: expected {expected}, observed {observed}"
        )
    return {
        "ok": True,
        "kind": "a1_07_gold_v7_parent_verification",
        "contract_sha256": contract["contract_sha256"],
        "owner_review_receipt_sha256": observed,
        "owner_review_receipt_file_sha256": rz.sha256_file(receipt),
    }


def _walk_strings(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, Mapping):
        for child in value.values():
            yield from _walk_strings(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_strings(child)


def _owner_commitments(receipt: Mapping[str, Any]) -> set[str]:
    return {
        text.lower()
        for text in _walk_strings(receipt)
        if HEX64.fullmatch(text.lower())
    }


def _atomic_write_json(path: Path, payload: Mapping[str, Any]) -> None:
    target = path.expanduser().absolute()
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(dict(payload), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, target)


def _copy_exclusive(source: Path, target: Path) -> None:
    source = source.expanduser().absolute()
    target = target.expanduser().absolute()
    if not source.is_file() or source.is_symlink():
        raise ContractError(f"regular source file required: {source}")
    if target.exists():
        raise ContractError(f"destination already exists: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)


def _return_template(contract: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "kind": "a1_07_gold_v7_estate_return",
        "contract_sha256": contract["contract_sha256"],
        "exact_branch_head": None,
        "parent_owner_review_receipt_sha256": contract["parent"][
            "owner_review_receipt_sha256"
        ],
        "parent_score_sha256": None,
        "parent_pcm_sha256": None,
        "child_score_sha256_by_candidate": {key: None for key in CHILDREN},
        "child_pcm_sha256_by_candidate": {key: None for key in CHILDREN},
        "reproduction_receipt_sha256_by_candidate": {key: None for key in CHILDREN},
        "machine_receipt_sha256_by_candidate": {key: None for key in CHILDREN},
        "machine_gate_result_by_candidate": {
            key: {"state": None, "reason": None} for key in CHILDREN
        },
        "declared_masks_by_candidate": {key: [] for key in CHILDREN},
        "qualified_child_count": 0,
        "owner_frontier_created": False,
        "review_public_path_or_null": None,
        "private_material_exported": False,
        "notes": [],
    }


def scaffold(
    contract: Mapping[str, Any],
    workspace: Path,
    *,
    parent_review_receipt: Path,
) -> dict[str, Any]:
    root = workspace.expanduser().absolute()
    if root.exists():
        raise ContractError(f"workspace already exists: {root}")
    verify_parent(contract, parent_review_receipt)
    root.mkdir(parents=True)
    incumbent = root / "incumbent"
    incumbent.mkdir()
    shutil.copyfile(parent_review_receipt, incumbent / "owner-review.receipt.json")
    owner_receipt = load_json(parent_review_receipt)
    authority_source = parent_review_receipt.parent / "assignment-authority.json"
    if not authority_source.is_file() or authority_source.is_symlink():
        raise ContractError(f"private assignment authority required: {authority_source}")
    authority = load_json(authority_source)
    authority_declared = require_hex64(
        authority.get("authority_sha256"), "authority_sha256"
    )
    authority_observed = legacy_review_sha256(authority, "authority_sha256")
    if authority_declared != authority_observed:
        raise ContractError("private assignment authority seal mismatch")
    if owner_receipt.get("private_authority_sha256") != authority_observed:
        raise ContractError("owner receipt does not bind the private assignment authority")
    shutil.copyfile(authority_source, incumbent / "assignment-authority.private.json")
    for child in contract["children"]:
        child_root = root / str(child["candidate_id"])
        (child_root / "authoring").mkdir(parents=True)
        (child_root / "render").mkdir()
        (child_root / "machine").mkdir()
        (child_root / "strategy.json").write_text(
            json.dumps(child, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    _atomic_write_json(root / "RETURN.private.json", _return_template(contract))
    (root / "NEXT_ACTIONS.md").write_text(
        "# A1-07 gold-v7 execution\n\n"
        + "\n".join(
            f"{index}. {action}"
            for index, action in enumerate(contract["estate_execution_order"], start=1)
        )
        + "\n\nNo owner frontier is legal below two qualified descendants.\n",
        encoding="utf-8",
    )
    return {
        "ok": True,
        "kind": "a1_07_gold_v7_workspace",
        "workspace": str(root),
        "contract_sha256": contract["contract_sha256"],
    }


def bind_incumbent(
    contract: Mapping[str, Any],
    *,
    workspace: Path,
    score_path: Path,
    audio_path: Path,
    bindings_path: Path,
    ffmpeg: str,
) -> dict[str, Any]:
    root = workspace.expanduser().absolute()
    ledger_path = root / "RETURN.private.json"
    incumbent = root / "incumbent"
    owner_receipt_path = incumbent / "owner-review.receipt.json"
    if not ledger_path.is_file() or not owner_receipt_path.is_file():
        raise ContractError("run scaffold before binding the incumbent")
    verify_parent(contract, owner_receipt_path)

    score = load_json(score_path)
    score_sha = rz.validate_performance_score(score)
    bindings = load_json(bindings_path)
    bindings_sha = rz.validate_source_bindings(bindings, score)
    timeline = dict(score["timeline"])
    pcm_sha = rz.canonical_pcm_sha256(
        audio_path,
        sample_rate=int(timeline["sample_rate"]),
        channels=int(timeline["channels"]),
        ffmpeg=ffmpeg,
    )
    container_sha = rz.sha256_file(audio_path)
    owner_receipt = load_json(owner_receipt_path)
    authority_path = incumbent / "assignment-authority.private.json"
    if not authority_path.is_file():
        raise ContractError("private assignment authority was not frozen")
    private_authority = load_json(authority_path)
    authority_sha = legacy_review_sha256(private_authority, "authority_sha256")
    if private_authority.get("authority_sha256") != authority_sha:
        raise ContractError("frozen private assignment authority seal mismatch")
    if owner_receipt.get("private_authority_sha256") != authority_sha:
        raise ContractError("owner receipt and frozen private authority disagree")
    commitments = _owner_commitments(owner_receipt) | _owner_commitments(private_authority)
    if score_sha not in commitments:
        raise ContractError("owner receipt does not commit the proposed gold-v6 score")
    if pcm_sha not in commitments and container_sha not in commitments:
        raise ContractError("owner receipt does not commit the proposed gold-v6 audio")

    _copy_exclusive(score_path, incumbent / "performance-score.json")
    _copy_exclusive(audio_path, incumbent / f"gold-v6{audio_path.suffix.lower() or '.wav'}")
    _copy_exclusive(bindings_path, incumbent / "source-bindings.private.json")

    ledger = load_json(ledger_path)
    ledger["exact_branch_head"] = current_git_head()
    ledger["parent_score_sha256"] = score_sha
    ledger["parent_pcm_sha256"] = pcm_sha
    ledger["notes"].append(
        {
            "kind": "incumbent_binding",
            "bindings_sha256": bindings_sha,
            "container_sha256": container_sha,
        }
    )
    _atomic_write_json(ledger_path, ledger)
    return {
        "ok": True,
        "kind": "a1_07_gold_v7_incumbent_binding",
        "score_sha256": score_sha,
        "bindings_sha256": bindings_sha,
        "container_sha256": container_sha,
        "canonical_pcm_sha256": pcm_sha,
        "exact_branch_head": ledger["exact_branch_head"],
    }


def _is_frankie_track(track: Mapping[str, Any], sources: Mapping[str, Mapping[str, Any]]) -> bool:
    header = " ".join(
        str(track.get(key) or "") for key in ("track_id", "role")
    ).lower()
    if "frankie" in header or "lead_vocal" in header or "lead-vocal" in header:
        return True
    for clip in track.get("clips") or []:
        source = sources.get(str(clip.get("source_id") or ""), {})
        text = f"{source.get('source_id', '')} {source.get('role', '')}".lower()
        if "four_seasons" in text and "vocal" in text:
            return True
    return False


def derive_production(
    *,
    parent_score_path: Path,
    parent_bindings_path: Path,
    output_dir: Path,
) -> dict[str, Any]:
    parent = load_json(parent_score_path)
    parent_sha = rz.validate_performance_score(parent)
    bindings = load_json(parent_bindings_path)
    rz.validate_source_bindings(bindings, parent)

    child = deepcopy(parent)
    child.pop("score_sha256", None)
    sources = {str(row["source_id"]): dict(row) for row in child["sources"]}
    vocal_tracks: list[str] = []
    band_tracks: list[str] = []
    for track in child["tracks"]:
        if track.get("processing"):
            raise ContractError(f"parent already has track processing: {track['track_id']}")
        if _is_frankie_track(track, sources):
            vocal_tracks.append(str(track["track_id"]))
            track["processing"] = [
                {"op": "highpass", "frequency_hz": 70.0, "poles": 2},
            ]
        else:
            band_tracks.append(str(track["track_id"]))
            track["processing"] = [
                {"op": "highpass", "frequency_hz": 28.0, "poles": 2},
                {
                    "op": "equalizer",
                    "frequency_hz": 2300.0,
                    "width_q": 0.8,
                    "gain_db": -1.5,
                },
            ]
    if not vocal_tracks or not band_tracks:
        raise ContractError("production child requires Frankie and donor-band tracks")

    master = dict(child.get("master") or {})
    if master.get("processing"):
        raise ContractError("parent already has master processing")
    master["processing"] = []
    master["gain_db"] = float(master.get("gain_db", 0.0)) - 0.5
    master["peak_limit_dbfs"] = -2.0
    child["master"] = master
    child["score_id"] = f"{child.get('score_id', 'gold-v6')}-gold-v7-production"
    child["title"] = f"{child.get('title', 'A1-07')} gold-v7 production"
    authority = dict(child.get("authority") or {})
    authority.update(
        {
            "parent_score_sha256": parent_sha,
            "iteration_contract": "gold-v7-production",
            "musical_acceptance": False,
            "renderer_invented_decisions": False,
        }
    )
    child["authority"] = authority
    child = rz.seal(child)

    child_bindings = deepcopy(bindings)
    child_bindings.pop("bindings_sha256", None)
    child_bindings["score_sha256"] = child["score_sha256"]
    child_bindings = rz.seal(child_bindings)

    root = output_dir.expanduser().absolute()
    if root.exists():
        raise ContractError(f"production output exists: {root}")
    root.mkdir(parents=True)
    rz.write_json(root / "performance-score.json", child, exclusive=True)
    rz.write_json(root / "source-bindings.private.json", child_bindings, exclusive=True)
    analysis = {
        "schema_version": 1,
        "kind": "a1_07_gold_v7_production_derivation",
        "parent_score_sha256": parent_sha,
        "child_score_sha256": child["score_sha256"],
        "vocal_tracks": vocal_tracks,
        "band_tracks": band_tracks,
        "authority": {"machine_hypothesis_only": True, "musical_acceptance": False},
    }
    analysis["derivation_sha256"] = canonical_sha256(analysis, "derivation_sha256")
    rz.write_json(root / "derivation.private.json", analysis, exclusive=True)
    return {
        "ok": True,
        "kind": analysis["kind"],
        "score_sha256": child["score_sha256"],
        "bindings_sha256": child_bindings["bindings_sha256"],
        "output_dir": str(root),
    }


def _source_from_audio(
    path: Path,
    *,
    source_id: str,
    role: str,
    sample_rate: int,
    channels: int,
    ffmpeg: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    artifact = path.expanduser().absolute()
    if not artifact.is_file() or artifact.is_symlink():
        raise ContractError(f"regular audio source required: {artifact}")
    container = rz.sha256_file(artifact)
    pcm = rz.canonical_pcm_sha256(
        artifact,
        sample_rate=sample_rate,
        channels=channels,
        ffmpeg=ffmpeg,
    )
    source = {
        "source_id": source_id,
        "role": role,
        "bytes": artifact.stat().st_size,
        "container_sha256": container,
        "canonical_pcm_sha256": pcm,
    }
    binding = {
        "source_id": source_id,
        "artifact_path": str(artifact),
        "bytes": artifact.stat().st_size,
        "container_sha256": container,
        "canonical_pcm_sha256": pcm,
    }
    return source, binding


def _decision_history(clips: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    history = []
    for index, clip in enumerate(clips, start=1):
        encoded = json.dumps(
            dict(clip), ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        history.append({
            "sequence": index,
            "command_id": f"gold-v7-author-{index:04d}",
            "actor": "local-estate-conductor",
            "operation": "place_clip",
            "target": clip["clip_id"],
            "parameters_sha256": hashlib.sha256(encoded).hexdigest(),
        })
    return history


def _write_score_and_bindings(
    output_dir: Path,
    score: Mapping[str, Any],
    binding_rows: Sequence[Mapping[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any], Path]:
    root = output_dir.expanduser().absolute()
    if root.exists():
        raise ContractError(f"authoring directory exists: {root}")
    root.mkdir(parents=True)
    sealed_score = rz.seal(score)
    bindings = rz.seal({
        "schema_version": 1,
        "kind": "earcrate_performance_source_bindings",
        "created_at": rz.now_utc(),
        "score_sha256": sealed_score["score_sha256"],
        "visibility": "sensitive",
        "bindings": [dict(row) for row in binding_rows],
    })
    rz.validate_performance_score(sealed_score)
    rz.validate_source_bindings(bindings, sealed_score)
    _atomic_write_json(root / "performance-score.json", sealed_score)
    _atomic_write_json(root / "source-bindings.private.json", bindings)
    return sealed_score, bindings, root


def derive_interplay(
    *,
    parent_score_path: Path,
    parent_audio_path: Path,
    parent_bindings_path: Path,
    four_seasons_drums_path: Path,
    output_dir: Path,
    ffmpeg: str,
) -> dict[str, Any]:
    parent = load_json(parent_score_path)
    parent_sha = rz.validate_performance_score(parent)
    parent_bindings = load_json(parent_bindings_path)
    rz.validate_source_bindings(parent_bindings, parent)
    timeline = dict(parent["timeline"])
    sample_rate = int(timeline["sample_rate"])
    channels = int(timeline["channels"])
    duration = int(timeline["duration_samples"])
    compound_source, compound_binding = _source_from_audio(
        parent_audio_path,
        source_id="gold_v6_reviewed_compound",
        role="protected_incumbent_compound",
        sample_rate=sample_rate,
        channels=channels,
        ffmpeg=ffmpeg,
    )
    source_index = _source_index(parent)
    binding_index = {
        str(row["source_id"]): dict(row)
        for row in parent_bindings.get("bindings") or []
    }
    modern_source = source_index["maneskin_drums"]
    modern_binding = binding_index["maneskin_drums"]
    original_source, original_binding = _source_from_audio(
        four_seasons_drums_path,
        source_id="four_seasons_drums",
        role="original_era_percussion_response",
        sample_rate=sample_rate,
        channels=channels,
        ffmpeg=ffmpeg,
    )
    fill_start = round(115.838 * sample_rate)
    fill_end = round(116.784 * sample_rate)
    fill_target_end = 671299
    fill_target_start = fill_target_end - (fill_end - fill_start)
    response_start = round(14.7447 * sample_rate)
    response_end = round(15.2000 * sample_rate)
    response_target_start = 412162
    response_target_end = response_target_start + response_end - response_start
    masks = [
        {
            "start_sample": response_target_start,
            "end_sample": response_target_end,
            "musical_function": "four_seasons_percussion_answer_on_bar_five_downbeat",
        },
        {
            "start_sample": fill_target_start,
            "end_sample": fill_target_end,
            "musical_function": "maneskin_fill_punctuation_resolving_to_bar_eight_downbeat",
        },
    ]
    clips = [
        {
            "clip_id": "gold-v7-interplay-protected-core",
            "source_id": "gold_v6_reviewed_compound",
            "source_start_sample": 0,
            "source_end_sample": duration,
            "target_start_sample": 0,
            "tempo_scale": 1.0,
            "pitch_semitones": 0.0,
            "gain_db": 0.0,
            "pan": 0.0,
            "fade_in_samples": 0,
            "fade_out_samples": 0,
            "musical_function": "protected_gold_v6_core_continuous_frankie_and_band_law",
            "occurrence_id": "gold_v6_reviewed_occurrence",
            "locked": True,
        },
        {
            "clip_id": "gold-v7-interplay-four-seasons-answer",
            "source_id": "four_seasons_drums",
            "source_start_sample": response_start,
            "source_end_sample": response_end,
            "target_start_sample": response_target_start,
            "tempo_scale": 1.0,
            "pitch_semitones": 0.0,
            "gain_db": -7.0,
            "pan": 0.0,
            "fade_in_samples": 240,
            "fade_out_samples": 480,
            "musical_function": masks[0]["musical_function"],
            "occurrence_id": "four_seasons_downbeat_14_7447",
            "locked": True,
        },
        {
            "clip_id": "gold-v7-interplay-maneskin-fill",
            "source_id": "maneskin_drums",
            "source_start_sample": fill_start,
            "source_end_sample": fill_end,
            "target_start_sample": fill_target_start,
            "tempo_scale": 1.0,
            "pitch_semitones": 0.0,
            "gain_db": -4.5,
            "pan": 0.0,
            "fade_in_samples": 240,
            "fade_out_samples": 480,
            "musical_function": masks[1]["musical_function"],
            "occurrence_id": "maneskin_fill_115_838_116_784",
            "locked": True,
        },
    ]
    score = {
        "schema_version": 1,
        "kind": "earcrate_performance_score",
        "created_at": rz.now_utc(),
        "score_id": "album-one-a1-07-gold-v7-interplay",
        "title": "A1-07 gold-v7 interplay - two bounded cross-era ownership events",
        "timeline": timeline,
        "sources": [compound_source, original_source, modern_source],
        "tracks": [
            {
                "track_id": "protected-gold-v6-core",
                "role": "protected_incumbent_compound",
                "ownership": "complete_gold_v6_core",
                "gain_db": 0.0,
                "pan": 0.0,
                "clips": [clips[0]],
            },
            {
                "track_id": "four-seasons-response",
                "role": "bounded_percussion_response",
                "ownership": "four_seasons_temporary_answer",
                "gain_db": 0.0,
                "pan": 0.0,
                "clips": [clips[1]],
            },
            {
                "track_id": "maneskin-fill-handoff",
                "role": "bounded_fill_punctuation",
                "ownership": "maneskin_temporary_fill",
                "gain_db": 0.0,
                "pan": 0.0,
                "clips": [clips[2]],
            },
        ],
        "master": {"gain_db": 0.0, "peak_limit_dbfs": None, "codec": "pcm_s24le"},
        "invariants": dict(parent["invariants"]),
        "authority": {
            "status": "bounded_gold_descendant",
            "allow_unused_sources": False,
            "parent_score_sha256": parent_sha,
            "protected_compound_parent_pcm_sha256": compound_source["canonical_pcm_sha256"],
            "iteration_contract": "gold-v7-interplay",
            "handoff_event_count": 2,
            "masked_seconds_total": sum(row["end_sample"] - row["start_sample"] for row in masks) / sample_rate,
            "frankie_edits": 0,
            "global_timing_changes": 0,
            "generated_lead_vocals": 0,
            "musical_acceptance": False,
        },
        "command_history": _decision_history(clips),
    }
    sealed_score, bindings, root = _write_score_and_bindings(
        output_dir,
        score,
        [compound_binding, original_binding, modern_binding],
    )
    _atomic_write_json(root / "mutation-masks.json", {
        "schema_version": 1,
        "kind": "a1_07_mutation_masks",
        "candidate_id": "gold-v7-interplay",
        "sample_rate": sample_rate,
        "masks": masks,
        "evidence": {
            "maneskin_fill_seconds": [115.838, 116.784],
            "four_seasons_downbeat_seconds": 14.7447,
            "earcrate_onset_boundary_used": True,
            "rubber_band_tempo_scale": 1.0,
        },
    })
    return {
        "ok": True,
        "kind": "a1_07_gold_v7_interplay_derivation",
        "score_sha256": sealed_score["score_sha256"],
        "bindings_sha256": bindings["bindings_sha256"],
        "masked_seconds_total": score["authority"]["masked_seconds_total"],
        "output_dir": str(root),
    }


def derive_arc(
    *,
    parent_score_path: Path,
    parent_audio_path: Path,
    parent_bindings_path: Path,
    output_dir: Path,
    ffmpeg: str,
) -> dict[str, Any]:
    parent = load_json(parent_score_path)
    parent_sha = rz.validate_performance_score(parent)
    parent_bindings = load_json(parent_bindings_path)
    rz.validate_source_bindings(parent_bindings, parent)
    parent_timeline = dict(parent["timeline"])
    sample_rate = int(parent_timeline["sample_rate"])
    channels = int(parent_timeline["channels"])
    parent_duration = int(parent_timeline["duration_samples"])
    core_start = round(19.2 * sample_rate)
    duration = core_start + parent_duration
    compound_source, compound_binding = _source_from_audio(
        parent_audio_path,
        source_id="gold_v6_reviewed_compound",
        role="protected_incumbent_compound",
        sample_rate=sample_rate,
        channels=channels,
        ffmpeg=ffmpeg,
    )
    source_index = _source_index(parent)
    tracks: list[dict[str, Any]] = []
    clips: list[dict[str, Any]] = []
    for parent_track in parent["tracks"]:
        track = deepcopy(dict(parent_track))
        track_id = str(track.get("track_id"))
        selected: list[dict[str, Any]] = []
        if _is_frankie_track(track, source_index):
            selected = [deepcopy(dict(row)) for row in track.get("clips") or []]
            for row in selected:
                row["clip_id"] = f"arc-build-{row['clip_id']}"
                row["musical_function"] = "continuous_frankie_build_clock_before_protected_payoff"
                row["occurrence_id"] = "gold_v7_arc_sparse_build_occurrence"
        elif track_id == "mk-other":
            selected = [deepcopy(dict(row)) for row in track.get("clips") or []]
            for index, row in enumerate(selected):
                row["clip_id"] = f"arc-build-{row['clip_id']}"
                row["gain_db"] = -16.0 if index < 3 else (-12.0 if index < 6 else -9.0)
                row["musical_function"] = "progressive_harmonic_ownership_under_frankie_clock"
                row["occurrence_id"] = "gold_v7_arc_sparse_build_occurrence"
        elif track_id == "mk-bass":
            selected = [deepcopy(dict(row)) for row in (track.get("clips") or [])[3:]]
            for index, row in enumerate(selected):
                row["clip_id"] = f"arc-build-{row['clip_id']}"
                row["gain_db"] = -7.0 if index < 3 else -4.0
                row["musical_function"] = "bass_enters_after_sparse_setup_under_frankie_clock"
                row["occurrence_id"] = "gold_v7_arc_sparse_build_occurrence"
        elif track_id == "mk-drums":
            selected = [deepcopy(dict(row)) for row in (track.get("clips") or [])[6:]]
            for row in selected:
                row["clip_id"] = f"arc-build-{row['clip_id']}"
                row["gain_db"] = -3.0
                row["musical_function"] = "drums_arrive_for_build_release_into_protected_payoff"
                row["occurrence_id"] = "gold_v7_arc_sparse_build_occurrence"
        if selected:
            track["track_id"] = f"arc-build-{track_id}"
            track["ownership"] = f"arc_build:{track.get('ownership', '')}"
            track["clips"] = selected
            tracks.append(track)
            clips.extend(selected)
    compound_clip = {
        "clip_id": "gold-v7-arc-protected-payoff",
        "source_id": "gold_v6_reviewed_compound",
        "source_start_sample": 0,
        "source_end_sample": parent_duration,
        "target_start_sample": core_start,
        "tempo_scale": 1.0,
        "pitch_semitones": 0.0,
        "gain_db": 0.0,
        "pan": 0.0,
        "fade_in_samples": 0,
        "fade_out_samples": 0,
        "musical_function": "sample_identical_gold_v6_payoff_after_sparse_build",
        "occurrence_id": "gold_v6_reviewed_occurrence",
        "locked": True,
    }
    tracks.append({
        "track_id": "protected-gold-v6-payoff",
        "role": "protected_incumbent_compound",
        "ownership": "complete_gold_v6_core",
        "gain_db": 0.0,
        "pan": 0.0,
        "clips": [compound_clip],
    })
    clips.append(compound_clip)
    timeline = dict(parent_timeline)
    timeline["duration_samples"] = duration
    score = {
        "schema_version": 1,
        "kind": "earcrate_performance_score",
        "created_at": rz.now_utc(),
        "score_id": "album-one-a1-07-gold-v7-arc",
        "title": "A1-07 gold-v7 arc - sparse conducted build into protected payoff",
        "timeline": timeline,
        "sources": [*deepcopy(parent["sources"]), compound_source],
        "tracks": tracks,
        "master": {"gain_db": 0.0, "peak_limit_dbfs": None, "codec": "pcm_s24le"},
        "invariants": dict(parent["invariants"]),
        "authority": {
            "status": "bounded_gold_descendant",
            "allow_unused_sources": False,
            "parent_score_sha256": parent_sha,
            "protected_compound_parent_pcm_sha256": compound_source["canonical_pcm_sha256"],
            "iteration_contract": "gold-v7-arc",
            "new_section_occurrences": 1,
            "protected_core_start_sample": core_start,
            "protected_core_duration_samples": parent_duration,
            "musical_acceptance": False,
        },
        "command_history": _decision_history(clips),
    }
    sealed_score, bindings, root = _write_score_and_bindings(
        output_dir,
        score,
        [*[dict(row) for row in parent_bindings["bindings"]], compound_binding],
    )
    return {
        "ok": True,
        "kind": "a1_07_gold_v7_arc_derivation",
        "score_sha256": sealed_score["score_sha256"],
        "bindings_sha256": bindings["bindings_sha256"],
        "duration_seconds": duration / sample_rate,
        "protected_core_start_sample": core_start,
        "output_dir": str(root),
    }


def _number(value: Any, *, label: str, low: float, high: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ContractError(f"{label} must be numeric") from exc
    if not math.isfinite(number) or not low <= number <= high:
        raise ContractError(f"{label} must be between {low} and {high}")
    return number


def _processing_filters(
    rows: Sequence[Mapping[str, Any]] | None,
    *,
    sample_rate: int,
    label: str,
) -> tuple[list[dict[str, Any]], list[str]]:
    normalized: list[dict[str, Any]] = []
    filters: list[str] = []
    for index, raw in enumerate(rows or []):
        row = dict(raw)
        op = str(row.get("op") or "").lower()
        prefix = f"{label}[{index}]"
        if op == "highpass":
            frequency = _number(row.get("frequency_hz"), label=f"{prefix}.frequency_hz", low=10.0, high=sample_rate * 0.45)
            poles = 2 if row.get("poles") is None else int(row["poles"])
            if poles not in {1, 2}:
                raise ContractError(f"{prefix}.poles must be 1 or 2")
            normalized.append({"op": op, "frequency_hz": frequency, "poles": poles})
            filters.append(f"highpass=f={frequency:.12g}:p={poles}")
        elif op == "equalizer":
            frequency = _number(row.get("frequency_hz"), label=f"{prefix}.frequency_hz", low=20.0, high=sample_rate * 0.45)
            width_q = _number(1.0 if row.get("width_q") is None else row["width_q"], label=f"{prefix}.width_q", low=0.1, high=12.0)
            gain_db = _number(row.get("gain_db"), label=f"{prefix}.gain_db", low=-12.0, high=12.0)
            normalized.append({"op": op, "frequency_hz": frequency, "width_q": width_q, "gain_db": gain_db})
            filters.append(f"equalizer=f={frequency:.12g}:t=q:w={width_q:.12g}:g={gain_db:.12g}")
        elif op == "compressor":
            threshold_db = _number(row.get("threshold_db"), label=f"{prefix}.threshold_db", low=-60.0, high=0.0)
            ratio = _number(row.get("ratio"), label=f"{prefix}.ratio", low=1.0, high=20.0)
            attack = _number(row.get("attack_ms"), label=f"{prefix}.attack_ms", low=0.01, high=2000.0)
            release = _number(row.get("release_ms"), label=f"{prefix}.release_ms", low=0.01, high=9000.0)
            makeup_db = _number(0.0 if row.get("makeup_db") is None else row["makeup_db"], label=f"{prefix}.makeup_db", low=-12.0, high=12.0)
            knee = _number(2.0 if row.get("knee") is None else row["knee"], label=f"{prefix}.knee", low=1.0, high=8.0)
            normalized.append({
                "op": op,
                "threshold_db": threshold_db,
                "ratio": ratio,
                "attack_ms": attack,
                "release_ms": release,
                "makeup_db": makeup_db,
                "knee": knee,
            })
            threshold = 10.0 ** (threshold_db / 20.0)
            makeup = 10.0 ** (makeup_db / 20.0)
            filters.append(
                "acompressor="
                f"threshold={threshold:.12g}:ratio={ratio:.12g}:attack={attack:.12g}:"
                f"release={release:.12g}:makeup={makeup:.12g}:knee={knee:.12g}"
            )
        else:
            raise ContractError(f"unsupported processing operation: {op!r}")
    return normalized, filters


def _render_processed_score(
    score: Mapping[str, Any],
    bindings: Mapping[str, Any],
    *,
    output_path: Path,
    receipt_path: Path,
    ffmpeg: str,
    ffprobe: str,
) -> dict[str, Any]:
    score_sha = rz.validate_performance_score(score)
    binding_sha = rz.validate_source_bindings(bindings, score)
    output = output_path.expanduser().absolute()
    if output.exists():
        raise ContractError(f"render output exists: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    stage_root = output.parent / f".{output.stem}.track-stages-{os.getpid()}"
    if stage_root.exists():
        raise ContractError(f"render stage directory exists: {stage_root}")
    stage_root.mkdir()
    try:
        binding_rows = {
            str(row["source_id"]): dict(row)
            for row in bindings.get("bindings") or []
        }
        source_rows = {
            str(row["source_id"]): dict(row)
            for row in score.get("sources") or []
        }
        stage_outputs: list[Path] = []
        stage_receipts: dict[str, str] = {}
        clip_receipts: list[dict[str, Any]] = []
        for index, source_track in enumerate(score["tracks"]):
            track = deepcopy(dict(source_track))
            track.pop("processing", None)
            used = {
                str(clip["source_id"])
                for clip in track.get("clips") or []
            }
            track_score = deepcopy(dict(score))
            track_score.pop("score_sha256", None)
            track_score["score_id"] = (
                f"{score.get('score_id', 'performance')}-stage-{track['track_id']}"
            )
            track_score["title"] = f"{score.get('title', 'Performance')} stage {track['track_id']}"
            track_score["sources"] = [source_rows[key] for key in sorted(used)]
            track_score["tracks"] = [track]
            track_score["master"] = {
                "gain_db": 0.0,
                "peak_limit_dbfs": None,
                "codec": "pcm_s24le",
            }
            authority = dict(track_score.get("authority") or {})
            authority["allow_unused_sources"] = False
            authority["render_stage_for_score_sha256"] = score_sha
            track_score["authority"] = authority
            track_score = rz.seal(track_score)
            track_bindings = deepcopy(dict(bindings))
            track_bindings.pop("bindings_sha256", None)
            track_bindings["score_sha256"] = track_score["score_sha256"]
            track_bindings["bindings"] = [binding_rows[key] for key in sorted(used)]
            track_bindings = rz.seal(track_bindings)
            stage_output = stage_root / f"track-{index:02d}.wav"
            stage_receipt_path = stage_root / f"track-{index:02d}.receipt.json"
            stage_receipt = render_score(
                track_score,
                track_bindings,
                output_path=stage_output,
                receipt_path=stage_receipt_path,
                ffmpeg=ffmpeg,
                ffprobe=ffprobe,
            )
            stage_outputs.append(stage_output)
            stage_receipts[str(source_track["track_id"])] = stage_receipt[
                "receipt_sha256"
            ]
            clip_receipts.extend(stage_receipt["clips"])

        timeline = dict(score["timeline"])
        sample_rate = int(timeline["sample_rate"])
        channels = int(timeline["channels"])
        duration_samples = int(timeline["duration_samples"])
        argv = [
            ffmpeg,
            "-nostdin",
            "-hide_banner",
            "-v",
            "error",
            "-y",
            "-filter_threads",
            "1",
            "-filter_complex_threads",
            "1",
            "-threads",
            "1",
        ]
        for stage_output in stage_outputs:
            argv.extend(["-i", str(stage_output)])
        filters: list[str] = []
        labels: list[str] = []
        processing_receipts: dict[str, Any] = {}
        for index, track in enumerate(score["tracks"]):
            normalized, operations = _processing_filters(
                track.get("processing"),
                sample_rate=sample_rate,
                label=f"track.{track['track_id']}.processing",
            )
            output_label = f"[t{index}]"
            chain = operations or ["anull"]
            filters.append(f"[{index}:a:0]{','.join(chain)}{output_label}")
            labels.append(output_label)
            if normalized:
                processing_receipts[str(track["track_id"])] = normalized
        master = dict(score.get("master") or {})
        normalized_master, master_operations = _processing_filters(
            master.get("processing"),
            sample_rate=sample_rate,
            label="master.processing",
        )
        master_chain = [
            f"amix=inputs={len(labels)}:duration=longest:normalize=0",
            *master_operations,
            f"apad=whole_len={duration_samples}",
            f"atrim=end_sample={duration_samples}",
            "asetpts=PTS-STARTPTS",
        ]
        master_gain = float(master.get("gain_db", 0.0))
        if abs(master_gain) > 1e-12:
            master_chain.append(f"volume={rz._linear_gain(master_gain):.12g}")
        if master.get("peak_limit_dbfs") not in {None, ""}:
            master_chain.append(
                f"alimiter=limit={rz._linear_gain(float(master['peak_limit_dbfs'])):.12g}:level=false"
            )
        filters.append(f"{''.join(labels)}{','.join(master_chain)}[out]")
        codec = str(master.get("codec") or "pcm_s24le")
        argv.extend([
            "-filter_complex", ";".join(filters), "-map", "[out]",
            "-ar", str(sample_rate), "-ac", str(channels), "-map_metadata", "-1",
            "-fflags", "+bitexact", "-flags:a", "+bitexact", "-c:a", codec,
            str(output),
        ])
        result = rz._run(argv, timeout=int(master.get("timeout_seconds") or 7200))
        if result.returncode != 0 or not output.is_file():
            if output.exists():
                output.unlink()
            raise ContractError(
                f"v7 processed render failed ({result.returncode}): {result.stderr[-3000:]}"
            )
        output_pcm = rz.canonical_pcm_sha256(
            output,
            sample_rate=sample_rate,
            channels=channels,
            ffmpeg=ffmpeg,
        )
        receipt = rz.seal({
            "schema_version": 1,
            "kind": "earcrate_performance_render_receipt",
            "rendered_at": rz.now_utc(),
            "score_sha256": score_sha,
            "bindings_sha256": binding_sha,
            "ffmpeg_version": rz.ffmpeg_version(ffmpeg),
            "output": {
                "name": output.name,
                "bytes": output.stat().st_size,
                "container_sha256": rz.sha256_file(output),
                "canonical_pcm_sha256": output_pcm,
                "sample_rate": sample_rate,
                "channels": channels,
                "duration_samples": duration_samples,
                "codec": codec,
            },
            "clip_count": len(clip_receipts),
            "clips": clip_receipts,
            "track_processing": processing_receipts,
            "master_processing": normalized_master,
            "track_stage_receipt_sha256": stage_receipts,
            "command": {
                "argv": argv,
                "returncode": 0,
                "stderr_tail": result.stderr[-3000:],
            },
            "ffprobe": rz.ffprobe_audio(output, ffprobe=ffprobe),
            "authority": {
                "renderer_invented_decisions": False,
                "all_selected_clips_accounted": True,
                "human_acceptance": False,
                "inference_success": False,
            },
        })
        rz.write_json(receipt_path, receipt, exclusive=True)
        return receipt
    finally:
        if stage_root.exists():
            shutil.rmtree(stage_root)


def render_score(
    score: Mapping[str, Any],
    bindings: Mapping[str, Any],
    *,
    output_path: Path,
    receipt_path: Path,
    ffmpeg: str,
    ffprobe: str,
) -> dict[str, Any]:
    score_sha = rz.validate_performance_score(score)
    binding_sha = rz.validate_source_bindings(bindings, score)
    if any(track.get("processing") for track in score.get("tracks") or []) or (
        score.get("master") or {}
    ).get("processing"):
        return _render_processed_score(
            score,
            bindings,
            output_path=output_path,
            receipt_path=receipt_path,
            ffmpeg=ffmpeg,
            ffprobe=ffprobe,
        )
    timeline = dict(score["timeline"])
    sample_rate = int(timeline["sample_rate"])
    channels = int(timeline["channels"])
    duration_samples = int(timeline["duration_samples"])
    source_index = rz._binding_index(bindings, score, verify_pcm=True, ffmpeg=ffmpeg)
    clips = rz._clip_rows(score)
    needs_rubberband = any(
        abs(float(clip.get("tempo_scale", 1.0)) - 1.0) > 1e-9
        or abs(float(clip.get("pitch_semitones", 0.0))) > 1e-9
        for _, clip in clips
    )
    if needs_rubberband and not rz._ffmpeg_has_filter("rubberband", ffmpeg):
        raise ContractError("Rubber Band filter is required")

    output = output_path.expanduser().absolute()
    if output.exists():
        raise ContractError(f"render output exists: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    argv = [
        ffmpeg,
        "-nostdin",
        "-hide_banner",
        "-v",
        "error",
        "-y",
        "-filter_threads",
        "1",
        "-filter_complex_threads",
        "1",
        "-threads",
        "1",
    ]
    filters: list[str] = []
    labels: list[str] = []
    track_labels: dict[str, list[str]] = {}
    clip_receipts: list[dict[str, Any]] = []
    for index, (track, clip) in enumerate(clips):
        source = source_index[str(clip["source_id"])]
        argv.extend(["-i", str(source.artifact_path)])
        source_start = int(clip["source_start_sample"])
        source_end = int(clip["source_end_sample"])
        target_start = int(clip["target_start_sample"])
        tempo = float(clip.get("tempo_scale", 1.0))
        pitch = float(clip.get("pitch_semitones", 0.0))
        gain_db = float(clip.get("gain_db", 0.0)) + float(track.get("gain_db", 0.0))
        pan = float(clip.get("pan", track.get("pan", 0.0)))
        fade_in = int(clip.get("fade_in_samples", 0))
        fade_out = int(clip.get("fade_out_samples", 0))
        transformed = max(1, round((source_end - source_start) / tempo))
        chain = [
            f"aformat=sample_rates={sample_rate}:channel_layouts={'mono' if channels == 1 else 'stereo'}",
            f"atrim=start_sample={source_start}:end_sample={source_end}",
            "asetpts=PTS-STARTPTS",
        ]
        if abs(tempo - 1.0) > 1e-9 or abs(pitch) > 1e-9:
            chain.append(f"rubberband=tempo={tempo:.12g}:pitch={2.0 ** (pitch / 12.0):.12g}")
        if abs(gain_db) > 1e-12:
            chain.append(f"volume={rz._linear_gain(gain_db):.12g}")
        if channels == 2 and abs(pan) > 1e-12:
            left, right = rz._pan_gains(pan)
            chain.append(f"pan=stereo|c0={left:.12g}*c0|c1={right:.12g}*c1")
        if fade_in:
            chain.append(f"afade=t=in:ss=0:ns={fade_in}")
        if fade_out:
            chain.append(f"afade=t=out:ss={max(0, transformed-fade_out)}:ns={fade_out}")
        if target_start:
            chain.append(f"adelay={target_start}S:all=1")
        label = f"[c{index}]"
        filters.append(f"[{index}:a:0]{','.join(chain)}{label}")
        labels.append(label)
        track_labels.setdefault(str(track["track_id"]), []).append(label)
        clip_receipts.append({
            "clip_id": clip["clip_id"],
            "track_id": track["track_id"],
            "source_id": clip["source_id"],
            "source_container_sha256": source.container_sha256,
            "target_start_sample": target_start,
            "transformed_duration_samples": transformed,
            "tempo_scale": tempo,
            "pitch_semitones": pitch,
        })

    processing_receipts: dict[str, Any] = {}
    for track_index, track in enumerate(score["tracks"]):
        normalized, ops = _processing_filters(
            track.get("processing"),
            sample_rate=sample_rate,
            label=f"track.{track['track_id']}.processing",
        )
        if not normalized:
            continue
        members = track_labels.get(str(track["track_id"]), [])
        if not members:
            raise ContractError(f"processed track has no clips: {track['track_id']}")
        chain = []
        if len(members) > 1:
            chain.append(f"amix=inputs={len(members)}:duration=longest:normalize=0")
        chain.extend(ops)
        output_label = f"[t{track_index}]"
        filters.append(f"{''.join(members)}{','.join(chain)}{output_label}")
        first = min(labels.index(member) for member in members)
        member_set = set(members)
        labels = [label for label in labels if label not in member_set]
        labels.insert(first, output_label)
        processing_receipts[str(track["track_id"])] = normalized

    master = dict(score.get("master") or {})
    normalized_master, master_ops = _processing_filters(
        master.get("processing"),
        sample_rate=sample_rate,
        label="master.processing",
    )
    master_chain = [f"amix=inputs={len(labels)}:duration=longest:normalize=0"]
    master_chain.extend(master_ops)
    master_chain.extend([
        f"apad=whole_len={duration_samples}",
        f"atrim=end_sample={duration_samples}",
        "asetpts=PTS-STARTPTS",
    ])
    master_gain = float(master.get("gain_db", 0.0))
    if abs(master_gain) > 1e-12:
        master_chain.append(f"volume={rz._linear_gain(master_gain):.12g}")
    if master.get("peak_limit_dbfs") not in {None, ""}:
        master_chain.append(
            f"alimiter=limit={rz._linear_gain(float(master['peak_limit_dbfs'])):.12g}:level=false"
        )
    filters.append(f"{''.join(labels)}{','.join(master_chain)}[out]")
    codec = str(master.get("codec") or "pcm_s24le")
    argv.extend([
        "-filter_complex", ";".join(filters), "-map", "[out]",
        "-ar", str(sample_rate), "-ac", str(channels), "-map_metadata", "-1",
        "-fflags", "+bitexact", "-flags:a", "+bitexact", "-c:a", codec,
        str(output),
    ])
    result = rz._run(argv, timeout=int(master.get("timeout_seconds") or 7200))
    if result.returncode != 0 or not output.is_file():
        if output.exists():
            output.unlink()
        raise ContractError(f"v7 render failed ({result.returncode}): {result.stderr[-3000:]}")
    output_pcm = rz.canonical_pcm_sha256(
        output,
        sample_rate=sample_rate,
        channels=channels,
        ffmpeg=ffmpeg,
    )
    receipt = rz.seal({
        "schema_version": 1,
        "kind": "earcrate_performance_render_receipt",
        "rendered_at": rz.now_utc(),
        "score_sha256": score_sha,
        "bindings_sha256": binding_sha,
        "ffmpeg_version": rz.ffmpeg_version(ffmpeg),
        "output": {
            "name": output.name,
            "bytes": output.stat().st_size,
            "container_sha256": rz.sha256_file(output),
            "canonical_pcm_sha256": output_pcm,
            "sample_rate": sample_rate,
            "channels": channels,
            "duration_samples": duration_samples,
            "codec": codec,
        },
        "clip_count": len(clip_receipts),
        "clips": clip_receipts,
        "track_processing": processing_receipts,
        "master_processing": normalized_master,
        "command": {"argv": argv, "returncode": 0, "stderr_tail": result.stderr[-3000:]},
        "ffprobe": rz.ffprobe_audio(output, ffprobe=ffprobe),
        "authority": {
            "renderer_invented_decisions": False,
            "all_selected_clips_accounted": True,
            "human_acceptance": False,
            "inference_success": False,
        },
    })
    rz.write_json(receipt_path, receipt, exclusive=True)
    return receipt


def render_twice(
    *,
    score_path: Path,
    bindings_path: Path,
    output_dir: Path,
    ffmpeg: str,
    ffprobe: str,
) -> dict[str, Any]:
    score = load_json(score_path)
    bindings = load_json(bindings_path)
    root = output_dir.expanduser().absolute()
    if root.exists():
        raise ContractError(f"render directory exists: {root}")
    root.mkdir(parents=True)
    codec = str((score.get("master") or {}).get("codec") or "pcm_s24le")
    extension = ".flac" if codec == "flac" else ".wav"
    receipts = []
    for label in ("a", "b"):
        receipts.append(
            render_score(
                score,
                bindings,
                output_path=root / f"render-{label}{extension}",
                receipt_path=root / f"render-{label}.json",
                ffmpeg=ffmpeg,
                ffprobe=ffprobe,
            )
        )
    a = receipts[0]["output"]
    b = receipts[1]["output"]
    if a["canonical_pcm_sha256"] != b["canonical_pcm_sha256"]:
        raise ContractError("independent renders disagree on canonical PCM")
    if a["container_sha256"] != b["container_sha256"]:
        raise ContractError("independent renders are not byte-identical")
    return {
        "ok": True,
        "kind": "a1_07_gold_v7_reproduction",
        "score_sha256": receipts[0]["score_sha256"],
        "canonical_pcm_sha256": a["canonical_pcm_sha256"],
        "container_sha256": a["container_sha256"],
        "render_a": str(root / f"render-a{extension}"),
        "render_b": str(root / f"render-b{extension}"),
        "receipt_a": str(root / "render-a.json"),
        "receipt_b": str(root / "render-b.json"),
    }


def _structural_clip(clip: Mapping[str, Any]) -> dict[str, Any]:
    fields = (
        "clip_id", "source_id", "source_start_sample", "source_end_sample",
        "target_start_sample", "tempo_scale", "pitch_semitones",
        "musical_function", "occurrence_id", "locked",
    )
    return {key: clip.get(key) for key in fields}


def _source_index(score: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(row["source_id"]): dict(row) for row in score.get("sources") or []}


def _clip_index(score: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(clip["clip_id"]): dict(clip)
        for track in score.get("tracks") or []
        for clip in track.get("clips") or []
    }


def _frankie_ids(score: Mapping[str, Any]) -> set[str]:
    ids = set()
    for row in score.get("sources") or []:
        text = f"{row.get('source_id', '')} {row.get('role', '')}".lower()
        if "frankie" in text or ("four_seasons" in text and "vocal" in text):
            ids.add(str(row["source_id"]))
    if not ids:
        raise ContractError("could not identify Frankie's source")
    return ids


def _require_frankie_unchanged(parent: Mapping[str, Any], candidate: Mapping[str, Any]) -> None:
    parent_sources = _source_index(parent)
    candidate_sources = _source_index(candidate)
    ids = _frankie_ids(parent)
    for source_id in ids:
        if candidate_sources.get(source_id) != parent_sources[source_id]:
            raise ContractError(f"Frankie source changed: {source_id}")
    parent_clips = {
        key: value for key, value in _clip_index(parent).items()
        if str(value.get("source_id")) in ids
    }
    candidate_clips = {
        key: value for key, value in _clip_index(candidate).items()
        if str(value.get("source_id")) in ids
    }
    if parent_clips != candidate_clips:
        raise ContractError("Frankie clip graph changed")


def _require_frankie_native(parent: Mapping[str, Any], candidate: Mapping[str, Any]) -> None:
    parent_sources = _source_index(parent)
    candidate_sources = _source_index(candidate)
    ids = _frankie_ids(parent)
    for source_id in ids:
        if candidate_sources.get(source_id) != parent_sources[source_id]:
            raise ContractError(f"Frankie source changed: {source_id}")
    candidate_clips = [
        clip
        for clip in _clip_index(candidate).values()
        if str(clip.get("source_id")) in ids
    ]
    if not candidate_clips:
        raise ContractError("arc requires Frankie-led build material")
    for clip in candidate_clips:
        if abs(float(clip.get("tempo_scale", 1.0)) - 1.0) > 1e-12:
            raise ContractError("Frankie timing transform is prohibited")
        if abs(float(clip.get("pitch_semitones", 0.0))) > 1e-12:
            raise ContractError("Frankie pitch transform is prohibited")


def _validate_receipts(
    score_sha: str,
    candidate_audio: Path,
    receipt_a_path: Path,
    receipt_b_path: Path,
    *,
    sample_rate: int,
    channels: int,
    ffmpeg: str,
) -> tuple[str, str, str]:
    pcm = rz.canonical_pcm_sha256(
        candidate_audio,
        sample_rate=sample_rate,
        channels=channels,
        ffmpeg=ffmpeg,
    )
    container = rz.sha256_file(candidate_audio)
    hashes = []
    for path in (receipt_a_path, receipt_b_path):
        receipt = load_json(path)
        receipt_sha = rz.validate_seal(
            receipt,
            kind="earcrate_performance_render_receipt",
        )
        if receipt.get("score_sha256") != score_sha:
            raise ContractError(f"render receipt belongs to another score: {path}")
        output = receipt.get("output") or {}
        if output.get("canonical_pcm_sha256") != pcm:
            raise ContractError(f"render receipt PCM mismatch: {path}")
        if output.get("container_sha256") != container:
            raise ContractError(f"render receipt container mismatch: {path}")
        hashes.append(receipt_sha)
    pair = canonical_sha256(
        {"receipts": hashes, "pcm": pcm, "container": container},
        "reproduction_pair_sha256",
    )
    return pcm, container, pair


def _load_masks(path: Path, *, candidate_id: str, sample_rate: int, duration: int) -> list[dict[str, Any]]:
    payload = load_json(path)
    if payload.get("kind") != "a1_07_mutation_masks" or payload.get("candidate_id") != candidate_id:
        raise ContractError("wrong mutation-mask authority")
    masks = [dict(row) for row in payload.get("masks") or []]
    if not 1 <= len(masks) <= 2:
        raise ContractError("interplay requires one or two masks")
    masks.sort(key=lambda row: int(row.get("start_sample", -1)))
    total = 0
    previous = 0
    for row in masks:
        start = int(row.get("start_sample", -1))
        end = int(row.get("end_sample", -1))
        if start < previous or end <= start or end > duration:
            raise ContractError("masks must be ordered, disjoint, and bounded")
        if not str(row.get("musical_function") or "").strip():
            raise ContractError("mask requires musical_function")
        total += end - start
        previous = end
    if total > round(sample_rate * 6.0):
        raise ContractError("interplay masks exceed six seconds")
    return masks


def _outside_masks_equal(parent: bytes, candidate: bytes, masks: list[dict[str, Any]], channels: int) -> None:
    if len(parent) != len(candidate):
        raise ContractError("interplay changed duration")
    frame_bytes = channels * 4
    cursor = 0
    for row in masks:
        start = int(row["start_sample"]) * frame_bytes
        end = int(row["end_sample"]) * frame_bytes
        if parent[cursor:start] != candidate[cursor:start]:
            raise ContractError("interplay changed PCM outside masks")
        cursor = end
    if parent[cursor:] != candidate[cursor:]:
        raise ContractError("interplay changed PCM outside masks")


def _decode_pcm(path: Path, sample_rate: int, channels: int, ffmpeg: str) -> bytes:
    result = subprocess.run(
        [
            ffmpeg, "-nostdin", "-v", "error", "-i", str(path), "-map", "0:a:0",
            "-ar", str(sample_rate), "-ac", str(channels), "-c:a", "pcm_s32le",
            "-f", "s32le", "-",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        shell=False,
    )
    if result.returncode != 0:
        raise ContractError(f"PCM decode failed: {result.stderr.decode('utf-8', 'replace')[-2000:]}")
    return result.stdout


def verify_candidate(
    contract: Mapping[str, Any],
    *,
    parent_score_path: Path,
    parent_audio_path: Path,
    candidate_id: str,
    candidate_score_path: Path,
    candidate_audio_path: Path,
    receipt_a_path: Path,
    receipt_b_path: Path,
    masks_path: Path | None,
    output_path: Path,
    ffmpeg: str,
) -> dict[str, Any]:
    if candidate_id not in CHILDREN:
        raise ContractError(f"undeclared candidate: {candidate_id}")
    parent = load_json(parent_score_path)
    candidate = load_json(candidate_score_path)
    parent_sha = rz.validate_performance_score(parent)
    candidate_sha = rz.validate_performance_score(candidate)
    timeline = dict(candidate["timeline"])
    sample_rate = int(timeline["sample_rate"])
    channels = int(timeline["channels"])
    if parent["timeline"]["sample_rate"] != sample_rate or parent["timeline"]["channels"] != channels:
        raise ContractError("candidate audio format differs from parent")
    candidate_pcm, candidate_container, reproduction_pair = _validate_receipts(
        candidate_sha,
        candidate_audio_path,
        receipt_a_path,
        receipt_b_path,
        sample_rate=sample_rate,
        channels=channels,
        ffmpeg=ffmpeg,
    )
    parent_pcm = rz.canonical_pcm_sha256(
        parent_audio_path,
        sample_rate=sample_rate,
        channels=channels,
        ffmpeg=ffmpeg,
    )
    if candidate_pcm == parent_pcm:
        raise ContractError("candidate is PCM-identical to gold-v6")
    checks = ["frankie_identity", "two_render_reproduction", "audio_distinctness"]
    masks: list[dict[str, Any]] = []

    if candidate_id == "gold-v7-production":
        _require_frankie_unchanged(parent, candidate)
        if parent["timeline"] != candidate["timeline"] or parent["sources"] != candidate["sources"]:
            raise ContractError("production child changed timeline or sources")
        parent_tracks = {str(row["track_id"]): dict(row) for row in parent["tracks"]}
        candidate_tracks = {str(row["track_id"]): dict(row) for row in candidate["tracks"]}
        if set(parent_tracks) != set(candidate_tracks):
            raise ContractError("production child changed track membership")
        for track_id, parent_track in parent_tracks.items():
            child_track = candidate_tracks[track_id]
            parent_clips = {str(row["clip_id"]): _structural_clip(row) for row in parent_track.get("clips") or []}
            child_clips = {str(row["clip_id"]): _structural_clip(row) for row in child_track.get("clips") or []}
            if parent_clips != child_clips:
                raise ContractError(f"production child changed arrangement: {track_id}")
        if masks_path is not None:
            raise ContractError("production child must not declare masks")
        checks.append("arrangement_structure_identity")
    elif candidate_id == "gold-v7-interplay":
        compound = _source_index(candidate).get("gold_v6_reviewed_compound")
        if not compound or compound.get("canonical_pcm_sha256") != parent_pcm:
            raise ContractError("interplay does not bind the protected gold-v6 PCM")
        if parent["timeline"] != candidate["timeline"]:
            raise ContractError("interplay changed timeline")
        if masks_path is None:
            raise ContractError("interplay requires masks")
        masks = _load_masks(
            masks_path,
            candidate_id=candidate_id,
            sample_rate=sample_rate,
            duration=int(timeline["duration_samples"]),
        )
        parent_bytes = _decode_pcm(parent_audio_path, sample_rate, channels, ffmpeg)
        candidate_bytes = _decode_pcm(candidate_audio_path, sample_rate, channels, ffmpeg)
        _outside_masks_equal(parent_bytes, candidate_bytes, masks, channels)
        checks.append("outside_mask_pcm_identity")
    else:
        _require_frankie_native(parent, candidate)
        if masks_path is not None:
            raise ContractError("arc child must not declare masks")
        duration = int(timeline["duration_samples"])
        if not round(sample_rate * 38.0) <= duration <= round(sample_rate * 62.0):
            raise ContractError("arc must be between 38 and 62 seconds")
        parent_bytes = _decode_pcm(parent_audio_path, sample_rate, channels, ffmpeg)
        candidate_bytes = _decode_pcm(candidate_audio_path, sample_rate, channels, ffmpeg)
        index = candidate_bytes.find(parent_bytes)
        if index < 0 or index % (channels * 4):
            raise ContractError("arc does not contain sample-identical gold-v6 PCM")
        start_sample = index // (channels * 4)
        masks = [{
            "start_sample": start_sample,
            "end_sample": start_sample + len(parent_bytes) // (channels * 4),
            "musical_function": "sample_identical_gold_v6_core",
        }]
        checks.append("sample_identical_embedded_core")

    output = output_path.expanduser().absolute()
    if output.exists():
        raise ContractError(f"machine receipt exists: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    qualified_audio = output.parent / f"qualified{candidate_audio_path.suffix.lower() or '.wav'}"
    _copy_exclusive(candidate_audio_path, qualified_audio)
    receipt = {
        "schema_version": 1,
        "kind": "a1_07_gold_v7_machine_receipt",
        "contract_sha256": contract["contract_sha256"],
        "candidate_id": candidate_id,
        "parent_score_sha256": parent_sha,
        "parent_pcm_sha256": parent_pcm,
        "candidate_score_sha256": candidate_sha,
        "candidate_pcm_sha256": candidate_pcm,
        "candidate_container_sha256": candidate_container,
        "qualified_audio_name": qualified_audio.name,
        "reproduction_pair_sha256": reproduction_pair,
        "declared_masks": masks,
        "checks": checks,
        "qualified": True,
        "authority": {
            "machine_qualified_only": True,
            "musical_acceptance": False,
            "album_master": False,
            "recovery_open": False,
        },
    }
    receipt["machine_receipt_sha256"] = canonical_sha256(
        receipt, "machine_receipt_sha256"
    )
    _atomic_write_json(output, receipt)
    return receipt


def record_result(
    contract: Mapping[str, Any],
    *,
    ledger_path: Path,
    candidate_id: str,
    state: str,
    reason: str,
    machine_receipt_path: Path | None,
) -> dict[str, Any]:
    if candidate_id not in CHILDREN or state not in TERMINAL_STATES:
        raise ContractError("invalid candidate or terminal state")
    if not reason.strip():
        raise ContractError("terminal result requires a reason")
    ledger = load_json(ledger_path)
    if ledger.get("contract_sha256") != contract["contract_sha256"]:
        raise ContractError("ledger belongs to another contract")
    if state == "qualified":
        if machine_receipt_path is None:
            raise ContractError("qualified result requires a machine receipt")
        receipt = load_json(machine_receipt_path)
        if receipt.get("kind") != "a1_07_gold_v7_machine_receipt" or receipt.get("candidate_id") != candidate_id:
            raise ContractError("wrong machine receipt")
        expected = canonical_sha256(receipt, "machine_receipt_sha256")
        if receipt.get("machine_receipt_sha256") != expected or receipt.get("qualified") is not True:
            raise ContractError("invalid machine receipt seal or authority")
        machine_root = ledger_path.parent / candidate_id / "machine"
        machine_root.mkdir(parents=True, exist_ok=True)
        stored_receipt = machine_root / "machine-receipt.json"
        if machine_receipt_path.expanduser().absolute() != stored_receipt.expanduser().absolute():
            _copy_exclusive(machine_receipt_path, stored_receipt)
        source_audio = machine_receipt_path.parent / str(receipt["qualified_audio_name"])
        stored_audio = machine_root / str(receipt["qualified_audio_name"])
        if source_audio.expanduser().absolute() != stored_audio.expanduser().absolute():
            _copy_exclusive(source_audio, stored_audio)
        ledger["child_score_sha256_by_candidate"][candidate_id] = receipt["candidate_score_sha256"]
        ledger["child_pcm_sha256_by_candidate"][candidate_id] = receipt["candidate_pcm_sha256"]
        ledger["reproduction_receipt_sha256_by_candidate"][candidate_id] = receipt["reproduction_pair_sha256"]
        ledger["machine_receipt_sha256_by_candidate"][candidate_id] = receipt["machine_receipt_sha256"]
        ledger["declared_masks_by_candidate"][candidate_id] = receipt["declared_masks"]
    else:
        for field in (
            "child_score_sha256_by_candidate", "child_pcm_sha256_by_candidate",
            "reproduction_receipt_sha256_by_candidate", "machine_receipt_sha256_by_candidate",
        ):
            ledger[field][candidate_id] = None
        ledger["declared_masks_by_candidate"][candidate_id] = []
    ledger["machine_gate_result_by_candidate"][candidate_id] = {
        "state": state,
        "reason": reason.strip(),
    }
    ledger["qualified_child_count"] = sum(
        1 for row in ledger["machine_gate_result_by_candidate"].values()
        if row.get("state") == "qualified"
    )
    _atomic_write_json(ledger_path, ledger)
    return {
        "ok": True,
        "kind": "a1_07_gold_v7_result_recorded",
        "candidate_id": candidate_id,
        "state": state,
        "qualified_child_count": ledger["qualified_child_count"],
    }


def _measure_loudness(
    path: Path,
    *,
    ffmpeg: str,
    target_true_peak_dbtp: float = -2.0,
) -> dict[str, float]:
    result = subprocess.run(
        [
            ffmpeg, "-nostdin", "-hide_banner", "-i", str(path),
            "-af", (
                "loudnorm=I=-14:"
                f"TP={target_true_peak_dbtp:.6f}:LRA=11:print_format=json"
            ),
            "-f", "null", "NUL",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
        shell=False,
    )
    if result.returncode != 0:
        raise ContractError(f"loudness measurement failed: {result.stderr[-2000:]}")
    start = result.stderr.rfind("{\n")
    end = result.stderr.rfind("}")
    if start < 0 or end < start:
        raise ContractError("loudness measurement returned no JSON")
    payload = json.loads(result.stderr[start : end + 1])
    return {
        "integrated_lufs": float(payload["input_i"]),
        "true_peak_dbtp": float(payload["input_tp"]),
        "loudness_range_lu": float(payload["input_lra"]),
        "threshold_lufs": float(payload["input_thresh"]),
        "target_offset_db": float(payload["target_offset"]),
    }


def _make_review_audio(
    source: Path,
    destination: Path,
    *,
    ffmpeg: str,
) -> dict[str, Any]:
    if destination.exists():
        raise ContractError(f"review destination already exists: {destination}")
    review_true_peak_target = -2.2
    before = _measure_loudness(
        source,
        ffmpeg=ffmpeg,
        target_true_peak_dbtp=review_true_peak_target,
    )
    stage = destination.with_name(f".{destination.stem}.loudnorm-stage.wav")
    if stage.exists():
        raise ContractError(f"review staging file already exists: {stage}")
    measured_filter = (
        f"loudnorm=I=-14:TP={review_true_peak_target:.6f}:LRA=11:"
        f"measured_I={before['integrated_lufs']:.9f}:"
        f"measured_TP={before['true_peak_dbtp']:.9f}:"
        f"measured_LRA={before['loudness_range_lu']:.9f}:"
        f"measured_thresh={before['threshold_lufs']:.9f}:"
        f"offset={before['target_offset_db']:.9f}:"
        "linear=true:print_format=summary"
    )
    try:
        first = subprocess.run(
            [
                ffmpeg, "-nostdin", "-hide_banner", "-v", "error", "-n",
                "-i", str(source), "-af", measured_filter,
                "-ar", "48000", "-ac", "2", "-map_metadata", "-1",
                "-fflags", "+bitexact", "-flags:a", "+bitexact",
                "-c:a", "pcm_f32le", str(stage),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
            shell=False,
        )
        if first.returncode != 0 or not stage.is_file():
            raise ContractError(f"review loudnorm pass failed: {first.stderr[-2000:]}")
        intermediate = _measure_loudness(stage, ffmpeg=ffmpeg)
        correction_gain_db = -14.0 - intermediate["integrated_lufs"]
        peak_limit = 10.0 ** (review_true_peak_target / 20.0)
        correction_filter = (
            f"volume={correction_gain_db:.9f}dB,"
            f"alimiter=limit={peak_limit:.12f}:attack=5:release=50:level=false"
        )
        second = subprocess.run(
            [
                ffmpeg, "-nostdin", "-hide_banner", "-v", "error", "-n",
                "-i", str(stage), "-af", correction_filter,
                "-ar", "48000", "-ac", "2", "-map_metadata", "-1",
                "-fflags", "+bitexact", "-flags:a", "+bitexact",
                "-c:a", "pcm_s24le", str(destination),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
            shell=False,
        )
        if second.returncode != 0 or not destination.is_file():
            raise ContractError(f"review correction pass failed: {second.stderr[-2000:]}")
        after = _measure_loudness(destination, ffmpeg=ffmpeg)
        if after["integrated_lufs"] > -13.85 or after["integrated_lufs"] < -14.20:
            raise ContractError(f"review loudness outside gate: {after['integrated_lufs']}")
        if after["true_peak_dbtp"] > -2.0:
            raise ContractError(f"review true peak outside gate: {after['true_peak_dbtp']}")
    except Exception:
        destination.unlink(missing_ok=True)
        raise
    finally:
        stage.unlink(missing_ok=True)
    return {
        "source_container_sha256": rz.sha256_file(source),
        "review_container_sha256": rz.sha256_file(destination),
        "bytes": destination.stat().st_size,
        "duration_seconds": float(rz.ffprobe_audio(destination)["format"]["duration"]),
        "normalization": "measured_two_pass_ebu_r128_with_true_peak_ceiling",
        "correction_gain_db": correction_gain_db,
        "source_measurement": before,
        **after,
    }


def prepare_frontier(
    contract: Mapping[str, Any],
    *,
    ledger_path: Path,
    ffmpeg: str,
    ffprobe: str,
) -> dict[str, Any]:
    ledger = load_json(ledger_path)
    qualified = [
        candidate_id
        for candidate_id in CHILDREN
        if ledger["machine_gate_result_by_candidate"][candidate_id].get("state") == "qualified"
    ]
    minimum = int(contract["machine_admission"]["minimum_qualified_children"])
    if len(qualified) < minimum:
        raise ContractError("owner frontier is prohibited below two qualified children")
    root = ledger_path.parent
    public = root / "review-public"
    private = root / "review-private"
    if public.exists() or private.exists():
        raise ContractError("owner frontier already exists")
    public.mkdir()
    private.mkdir()
    candidates: dict[str, Path] = {
        "gold-v6": next((root / "incumbent").glob("gold-v6.*")),
    }
    for candidate_id in qualified:
        machine = load_json(root / candidate_id / "machine" / "machine-receipt.json")
        candidates[candidate_id] = root / candidate_id / "machine" / str(
            machine["qualified_audio_name"]
        )
    labels = [chr(ord("A") + index) for index in range(len(candidates))]
    shuffled = sorted(candidates)
    secrets.SystemRandom().shuffle(shuffled)
    option_map = dict(zip(labels, shuffled))
    measurements: dict[str, Any] = {}
    options: dict[str, Any] = {}
    try:
        for label, candidate_id in option_map.items():
            destination = public / f"{label}.wav"
            measurements[candidate_id] = _make_review_audio(
                candidates[candidate_id], destination, ffmpeg=ffmpeg
            )
            options[label] = {
                "sha256": rz.sha256_file(destination),
                "bytes": destination.stat().st_size,
                "duration_seconds": measurements[candidate_id]["duration_seconds"],
                "media_kind": "audio/wav",
            }
        authority = {
            "schema_version": 1,
            "kind": "a1_07_gold_v7_private_assignment",
            "created_at": rz.now_utc(),
            "nonce": secrets.token_hex(32),
            "contract_sha256": contract["contract_sha256"],
            "option_map": option_map,
            "candidate_sources": {
                candidate_id: {
                    "path": str(path),
                    "sha256": rz.sha256_file(path),
                    "bytes": path.stat().st_size,
                }
                for candidate_id, path in candidates.items()
            },
            "measurements": measurements,
        }
        authority["authority_sha256"] = canonical_sha256(authority, "authority_sha256")
        assignment = {
            "schema_version": 1,
            "kind": "a1_07_gold_v7_public_assignment",
            "created_at": rz.now_utc(),
            "contract_sha256": contract["contract_sha256"],
            "private_authority_sha256": authority["authority_sha256"],
            "options": options,
            "choices": [*labels, "tie", "reject_all", "abstain"],
            "control_question": (
                "Which option, if any, improves the protected incumbent without breaking "
                "continuous Frankie, same-work identity, or the band-follows-singer law?"
            ),
            "instructions": (
                "Listen at a fixed playback level. The longer option must be judged first "
                "as a whole passage and then on its inherited payoff. Relative preference "
                "does not equal Album One acceptance; reject_all remains valid."
            ),
        }
        assignment["assignment_sha256"] = canonical_sha256(
            assignment, "assignment_sha256"
        )
        _atomic_write_json(private / "assignment-authority.json", authority)
        _atomic_write_json(public / "assignment.json", assignment)
        (public / "REVIEW.txt").write_text(
            "Choose A, B, C, D, tie, reject_all, or abstain.\n"
            "Which option, if any, improves gold-v6 without breaking continuous Frankie, "
            "same-work identity, or the band-follows-singer law?\n"
            "The longer option must work as a whole passage and preserve its inherited payoff.\n"
            "Relative preference does not equal Album One acceptance.\n",
            encoding="utf-8",
        )
    except Exception:
        shutil.rmtree(public, ignore_errors=True)
        shutil.rmtree(private, ignore_errors=True)
        raise
    ledger["owner_frontier_created"] = True
    ledger["review_public_path_or_null"] = str(public)
    ledger["notes"].append({
        "kind": "owner_frontier",
        "assignment_sha256": assignment["assignment_sha256"],
        "private_authority_sha256": authority["authority_sha256"],
        "audio_file_count": len(options),
        "target_lufs": -14.0,
        "peak_ceiling_dbtp": -2.0,
    })
    _atomic_write_json(ledger_path, ledger)
    return {
        "ok": True,
        "kind": "a1_07_gold_v7_owner_frontier",
        "qualified_children": qualified,
        "audio_file_count": len(options),
        "assignment_sha256": assignment["assignment_sha256"],
        "review_public": str(public),
    }


def verify_return(
    contract: Mapping[str, Any],
    *,
    ledger_path: Path,
    ffmpeg: str,
) -> dict[str, Any]:
    ledger = load_json(ledger_path)
    if ledger.get("contract_sha256") != contract["contract_sha256"]:
        raise ContractError("return belongs to another contract")
    head = require_git_oid(ledger.get("exact_branch_head"), "exact_branch_head")
    if head != current_git_head():
        raise ContractError("return ledger head does not match checkout")
    parent_score_sha = require_hex64(ledger.get("parent_score_sha256"), "parent_score_sha256")
    parent_pcm_sha = require_hex64(ledger.get("parent_pcm_sha256"), "parent_pcm_sha256")
    incumbent = ledger_path.parent / "incumbent"
    parent_score = load_json(incumbent / "performance-score.json")
    if rz.validate_performance_score(parent_score) != parent_score_sha:
        raise ContractError("incumbent score mismatch")
    audio_files = list(incumbent.glob("gold-v6.*"))
    if len(audio_files) != 1:
        raise ContractError("exactly one incumbent audio file required")
    timeline = parent_score["timeline"]
    actual_parent_pcm = rz.canonical_pcm_sha256(
        audio_files[0],
        sample_rate=int(timeline["sample_rate"]),
        channels=int(timeline["channels"]),
        ffmpeg=ffmpeg,
    )
    if actual_parent_pcm != parent_pcm_sha:
        raise ContractError("incumbent PCM mismatch")
    qualified = []
    for candidate_id in CHILDREN:
        row = ledger["machine_gate_result_by_candidate"][candidate_id]
        state = row.get("state")
        if state not in TERMINAL_STATES:
            raise ContractError(f"nonterminal child state: {candidate_id}")
        if state == "qualified":
            qualified.append(candidate_id)
            machine = load_json(
                ledger_path.parent / candidate_id / "machine" / "machine-receipt.json"
            )
            if machine.get("machine_receipt_sha256") != canonical_sha256(machine, "machine_receipt_sha256"):
                raise ContractError(f"machine receipt seal mismatch: {candidate_id}")
            if machine.get("parent_score_sha256") != parent_score_sha or machine.get("parent_pcm_sha256") != parent_pcm_sha:
                raise ContractError(f"machine receipt parent mismatch: {candidate_id}")
            if machine.get("candidate_score_sha256") != ledger["child_score_sha256_by_candidate"][candidate_id]:
                raise ContractError(f"candidate score mismatch: {candidate_id}")
            audio = ledger_path.parent / candidate_id / "machine" / machine["qualified_audio_name"]
            if rz.sha256_file(audio) != machine["candidate_container_sha256"]:
                raise ContractError(f"qualified audio changed: {candidate_id}")
        elif not str(row.get("reason") or "").strip():
            raise ContractError(f"terminal result lacks reason: {candidate_id}")
    if int(ledger.get("qualified_child_count", -1)) != len(qualified):
        raise ContractError("qualified count mismatch")
    minimum = int(contract["machine_admission"]["minimum_qualified_children"])
    frontier = bool(ledger.get("owner_frontier_created"))
    if len(qualified) < minimum and frontier:
        raise ContractError("owner frontier is prohibited below two qualified children")
    if len(qualified) >= minimum and not frontier:
        raise ContractError("qualified frontier threshold passed but owner frontier is absent")
    if frontier:
        public = ledger_path.parent / "review-public"
        private = ledger_path.parent / "review-private"
        declared_public = Path(str(ledger.get("review_public_path_or_null") or ""))
        if declared_public.resolve() != public.resolve():
            raise ContractError("review public path mismatch")
        assignment_path = public / "assignment.json"
        authority_path = private / "assignment-authority.json"
        if not assignment_path.is_file() or not authority_path.is_file():
            raise ContractError("owner frontier assignment files are incomplete")
        if not (public / "REVIEW.txt").is_file():
            raise ContractError("owner frontier instructions are absent")
        assignment = load_json(assignment_path)
        authority = load_json(authority_path)
        if assignment.get("assignment_sha256") != canonical_sha256(assignment, "assignment_sha256"):
            raise ContractError("public assignment seal mismatch")
        if authority.get("authority_sha256") != canonical_sha256(authority, "authority_sha256"):
            raise ContractError("private assignment authority seal mismatch")
        if assignment.get("private_authority_sha256") != authority.get("authority_sha256"):
            raise ContractError("public/private assignment binding mismatch")
        if assignment.get("contract_sha256") != contract["contract_sha256"] or authority.get("contract_sha256") != contract["contract_sha256"]:
            raise ContractError("owner frontier belongs to another contract")
        if "option_map" in assignment:
            raise ContractError("private option map leaked into public assignment")
        expected_candidates = {"gold-v6", *qualified}
        option_map = dict(authority.get("option_map") or {})
        options = dict(assignment.get("options") or {})
        expected_labels = [chr(ord("A") + index) for index in range(len(expected_candidates))]
        if sorted(option_map) != expected_labels or set(option_map.values()) != expected_candidates:
            raise ContractError("private option map does not match qualified frontier")
        if sorted(options) != expected_labels or len(options) > 4:
            raise ContractError("public option set does not match qualified frontier")
        audio_files = sorted(public.glob("*.wav"))
        if [path.stem for path in audio_files] != expected_labels:
            raise ContractError("public review audio set is incomplete")
        expected_public_files = {"assignment.json", "REVIEW.txt", *(f"{label}.wav" for label in expected_labels)}
        if {path.name for path in public.iterdir() if path.is_file()} != expected_public_files:
            raise ContractError("unexpected file in public owner frontier")
        for label, audio in zip(expected_labels, audio_files):
            row = dict(options[label])
            if row.get("sha256") != rz.sha256_file(audio) or int(row.get("bytes", -1)) != audio.stat().st_size:
                raise ContractError(f"public review audio changed: {label}")
            candidate_id = option_map[label]
            measurement = dict((authority.get("measurements") or {}).get(candidate_id) or {})
            if measurement.get("review_container_sha256") != row.get("sha256"):
                raise ContractError(f"review measurement binding mismatch: {label}")
    if bool(ledger.get("private_material_exported")):
        raise ContractError("private material export is prohibited")
    return {
        "ok": True,
        "kind": "a1_07_gold_v7_estate_return_verification",
        "qualified_children": qualified,
        "qualified_child_count": len(qualified),
        "owner_frontier_created": frontier,
    }


def refresh_head(
    contract: Mapping[str, Any],
    *,
    ledger_path: Path,
) -> dict[str, Any]:
    ledger = load_json(ledger_path)
    if ledger.get("contract_sha256") != contract["contract_sha256"]:
        raise ContractError("return belongs to another contract")
    previous = require_git_oid(ledger.get("exact_branch_head"), "exact_branch_head")
    current = current_git_head()
    ledger["exact_branch_head"] = current
    ledger["notes"].append({
        "kind": "exact_branch_head_refreshed",
        "previous_exact_branch_head": previous,
        "exact_branch_head": current,
    })
    _atomic_write_json(ledger_path, ledger)
    return {
        "ok": True,
        "kind": "a1_07_gold_v7_exact_branch_head_refresh",
        "previous_exact_branch_head": previous,
        "exact_branch_head": current,
    }


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description="Execute the A1-07 gold-v7 iteration")
    root.add_argument(
        "--contract",
        type=Path,
        default=Path("configs/album_one/a1-07/gold-v7-iteration.v1.json"),
    )
    sub = root.add_subparsers(dest="command", required=True)
    sub.add_parser("verify-contract")

    parent = sub.add_parser("verify-parent")
    parent.add_argument("--receipt", type=Path, required=True)

    scaffold_parser = sub.add_parser("scaffold")
    scaffold_parser.add_argument("--workspace", type=Path, required=True)
    scaffold_parser.add_argument("--parent-review-receipt", type=Path, required=True)

    bind = sub.add_parser("bind-incumbent")
    bind.add_argument("--workspace", type=Path, required=True)
    bind.add_argument("--score", type=Path, required=True)
    bind.add_argument("--audio", type=Path, required=True)
    bind.add_argument("--bindings", type=Path, required=True)
    bind.add_argument("--ffmpeg", default="ffmpeg")

    derive = sub.add_parser("derive-production")
    derive.add_argument("--parent-score", type=Path, required=True)
    derive.add_argument("--parent-bindings", type=Path, required=True)
    derive.add_argument("--output-dir", type=Path, required=True)

    interplay = sub.add_parser("derive-interplay")
    interplay.add_argument("--parent-score", type=Path, required=True)
    interplay.add_argument("--parent-audio", type=Path, required=True)
    interplay.add_argument("--parent-bindings", type=Path, required=True)
    interplay.add_argument("--four-seasons-drums", type=Path, required=True)
    interplay.add_argument("--output-dir", type=Path, required=True)
    interplay.add_argument("--ffmpeg", default="ffmpeg")

    arc = sub.add_parser("derive-arc")
    arc.add_argument("--parent-score", type=Path, required=True)
    arc.add_argument("--parent-audio", type=Path, required=True)
    arc.add_argument("--parent-bindings", type=Path, required=True)
    arc.add_argument("--output-dir", type=Path, required=True)
    arc.add_argument("--ffmpeg", default="ffmpeg")

    render = sub.add_parser("render-twice")
    render.add_argument("--score", type=Path, required=True)
    render.add_argument("--bindings", type=Path, required=True)
    render.add_argument("--output-dir", type=Path, required=True)
    render.add_argument("--ffmpeg", default="ffmpeg")
    render.add_argument("--ffprobe", default="ffprobe")

    candidate = sub.add_parser("verify-candidate")
    candidate.add_argument("--parent-score", type=Path, required=True)
    candidate.add_argument("--parent-audio", type=Path, required=True)
    candidate.add_argument("--candidate-id", choices=CHILDREN, required=True)
    candidate.add_argument("--candidate-score", type=Path, required=True)
    candidate.add_argument("--candidate-audio", type=Path, required=True)
    candidate.add_argument("--render-receipt-a", type=Path, required=True)
    candidate.add_argument("--render-receipt-b", type=Path, required=True)
    candidate.add_argument("--masks", type=Path)
    candidate.add_argument("--output", type=Path, required=True)
    candidate.add_argument("--ffmpeg", default="ffmpeg")

    record = sub.add_parser("record-result")
    record.add_argument("--ledger", type=Path, required=True)
    record.add_argument("--candidate-id", choices=CHILDREN, required=True)
    record.add_argument("--state", choices=sorted(TERMINAL_STATES), required=True)
    record.add_argument("--reason", required=True)
    record.add_argument("--machine-receipt", type=Path)

    frontier = sub.add_parser("prepare-frontier")
    frontier.add_argument("--ledger", type=Path, required=True)
    frontier.add_argument("--ffmpeg", default="ffmpeg")
    frontier.add_argument("--ffprobe", default="ffprobe")

    returned = sub.add_parser("verify-return")
    returned.add_argument("--ledger", type=Path, required=True)
    returned.add_argument("--ffmpeg", default="ffmpeg")
    refreshed = sub.add_parser("refresh-head")
    refreshed.add_argument("--ledger", type=Path, required=True)
    return root


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        contract = load_contract(args.contract)
        if args.command == "verify-contract":
            result = {
                "ok": True,
                "kind": contract["kind"],
                "contract_sha256": contract["contract_sha256"],
                "children": list(CHILDREN),
            }
        elif args.command == "verify-parent":
            result = verify_parent(contract, args.receipt)
        elif args.command == "scaffold":
            result = scaffold(
                contract,
                args.workspace,
                parent_review_receipt=args.parent_review_receipt,
            )
        elif args.command == "bind-incumbent":
            result = bind_incumbent(
                contract,
                workspace=args.workspace,
                score_path=args.score,
                audio_path=args.audio,
                bindings_path=args.bindings,
                ffmpeg=args.ffmpeg,
            )
        elif args.command == "derive-production":
            result = derive_production(
                parent_score_path=args.parent_score,
                parent_bindings_path=args.parent_bindings,
                output_dir=args.output_dir,
            )
        elif args.command == "derive-interplay":
            result = derive_interplay(
                parent_score_path=args.parent_score,
                parent_audio_path=args.parent_audio,
                parent_bindings_path=args.parent_bindings,
                four_seasons_drums_path=args.four_seasons_drums,
                output_dir=args.output_dir,
                ffmpeg=args.ffmpeg,
            )
        elif args.command == "derive-arc":
            result = derive_arc(
                parent_score_path=args.parent_score,
                parent_audio_path=args.parent_audio,
                parent_bindings_path=args.parent_bindings,
                output_dir=args.output_dir,
                ffmpeg=args.ffmpeg,
            )
        elif args.command == "render-twice":
            result = render_twice(
                score_path=args.score,
                bindings_path=args.bindings,
                output_dir=args.output_dir,
                ffmpeg=args.ffmpeg,
                ffprobe=args.ffprobe,
            )
        elif args.command == "verify-candidate":
            result = verify_candidate(
                contract,
                parent_score_path=args.parent_score,
                parent_audio_path=args.parent_audio,
                candidate_id=args.candidate_id,
                candidate_score_path=args.candidate_score,
                candidate_audio_path=args.candidate_audio,
                receipt_a_path=args.render_receipt_a,
                receipt_b_path=args.render_receipt_b,
                masks_path=args.masks,
                output_path=args.output,
                ffmpeg=args.ffmpeg,
            )
        elif args.command == "record-result":
            result = record_result(
                contract,
                ledger_path=args.ledger,
                candidate_id=args.candidate_id,
                state=args.state,
                reason=args.reason,
                machine_receipt_path=args.machine_receipt,
            )
        elif args.command == "prepare-frontier":
            result = prepare_frontier(
                contract,
                ledger_path=args.ledger,
                ffmpeg=args.ffmpeg,
                ffprobe=args.ffprobe,
            )
        elif args.command == "verify-return":
            result = verify_return(
                contract,
                ledger_path=args.ledger,
                ffmpeg=args.ffmpeg,
            )
        elif args.command == "refresh-head":
            result = refresh_head(contract, ledger_path=args.ledger)
        else:
            raise ContractError(f"unsupported command: {args.command}")
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    except (ContractError, rz.ValidationError, FileExistsError) as exc:
        print(
            json.dumps({"ok": False, "error": str(exc)}, indent=2, sort_keys=True),
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
