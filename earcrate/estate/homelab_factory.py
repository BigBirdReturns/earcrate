from __future__ import annotations

"""Production orchestration for the EarCrate Homelab organ factory.

The Provider Arcade decides whether individual organs are present, loadable,
benchmarked, auditioned, and adopted.  This module operates one layer above that
lifecycle.  It compiles source-bound specimen cases into typed provider graphs,
executes compatible organs through explicit adapters, builds a bounded
quality-diversity frontier, prepares leak-resistant blind review packets, turns
human review into a scoped preference update, and emits source-free circulation
packets.

The factory never grants provider adoption, canonical musical authority, release
eligibility, or publication rights.  It creates evidence and bounded child
campaigns that existing EarCrate authorities may later promote.
"""

from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
import argparse
import fnmatch
import hashlib
import hmac
import json
import math
import mimetypes
import os
from pathlib import Path
import random
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
from typing import Any, Iterable, Mapping, MutableMapping, Sequence
import zipfile

from earcrate.estate import homelab_specimens as specimens

SCHEMA_VERSION = 1
FACTORY_HASH_FIELDS = {
    "earcrate_homelab_factory_manifest": "manifest_sha256",
    "earcrate_homelab_factory_recipe": "recipe_sha256",
    "earcrate_homelab_factory_run": "run_sha256",
    "earcrate_homelab_factory_state": "state_sha256",
    "earcrate_homelab_quality_archive": "archive_sha256",
    "earcrate_homelab_factory_review_assignment": "assignment_sha256",
    "earcrate_homelab_factory_private_assignment_authority": "authority_sha256",
    "earcrate_homelab_factory_review_submission": "submission_sha256",
    "earcrate_homelab_factory_review_ledger": "ledger_sha256",
    "earcrate_homelab_preference_update": "update_sha256",
    "earcrate_homelab_circulation_packet": "packet_sha256",
}
specimens.HASH_FIELDS.update(FACTORY_HASH_FIELDS)

MACHINE_TASK_TYPES = {"specimen_trial", "factory_recipe", "factory_archive", "factory_circulation"}
HUMAN_TASK_TYPES = {"factory_review", "specimen_review"}
AUTHORITY_TASK_TYPES = {"factory_preference", "specimen_assessment"}
TERMINAL_STATES = {"completed", "failed", "refused", "cancelled", "human_pending"}
DEFAULT_REVIEW_DIMENSIONS = (
    "vocal authority",
    "phrase placement",
    "percussion impact",
    "groove continuity",
    "separator residue",
    "room continuity",
    "source recognizability",
    "desire to hear the next phrase",
)
SENSITIVE_KEYS = {
    "artifact_path",
    "source_path",
    "source_paths",
    "absolute_path",
    "review_token",
    "option_map",
    "command",
    "argv",
    "environment",
    "stderr",
    "stdout",
}
ABSOLUTE_PATH = re.compile(r"^(?:[A-Za-z]:[\\/]|\\\\|/|file://)", re.IGNORECASE)
BUILTIN_RESOURCES = {
    "specimen-suite": "configs/homelab_factory/specimen-suite.v1.json",
    "provider-role-policy": "configs/homelab_factory/provider-role-policy.v1.json",
    "provider-adapters": "configs/homelab_factory/provider-adapters.v1.json",
    "beggin-timing-config": "configs/homelab_factory/beggin-timing-config.json",
    "review-dimensions": "configs/homelab_factory/review-dimensions.json",
}


# ---------------------------------------------------------------------------
# Canonical data and safe persistence
# ---------------------------------------------------------------------------


def now_utc() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def canonical_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def seal(payload: Mapping[str, Any]) -> dict[str, Any]:
    return specimens.seal(payload)


def validate(payload: Mapping[str, Any], *, kind: str | None = None) -> str:
    identity = specimens.validate_seal(payload)
    if kind and payload.get("kind") != kind:
        raise ValueError(f"expected {kind}, got {payload.get('kind')!r}")
    return identity


def atomic_write(path: str | Path, data: bytes, *, exclusive: bool = False) -> Path:
    target = Path(path).expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    if exclusive and target.exists():
        raise FileExistsError(target)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=target.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        if exclusive and target.exists():
            raise FileExistsError(target)
        os.replace(temporary, target)
        if os.name != "nt":
            directory = os.open(str(target.parent), os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
    finally:
        if temporary.exists():
            temporary.unlink()
    return target


def write_json(path: str | Path, value: Mapping[str, Any], *, exclusive: bool = False) -> Path:
    body = (json.dumps(dict(value), ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    return atomic_write(path, body, exclusive=exclusive)


def load_json(path: str | Path) -> dict[str, Any]:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON object required: {path}")
    return value


def load_json_source(source: str | Path) -> dict[str, Any]:
    text = str(source)
    if not text.startswith("builtin:"):
        return load_json(source)
    name = text.split(":", 1)[1]
    relative = BUILTIN_RESOURCES.get(name)
    if not relative:
        raise ValueError(f"unknown builtin factory resource: {name}")
    executable = Path(sys.argv[0]).expanduser().absolute()
    if executable.is_file() and zipfile.is_zipfile(executable):
        with zipfile.ZipFile(executable) as archive:
            value = json.loads(archive.read(relative).decode("utf-8"))
    else:
        root = Path(__file__).resolve().parents[2]
        value = json.loads((root / relative).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"builtin factory resource is not an object: {name}")
    return value


def _stable_slug(value: Any) -> str:
    text = re.sub(r"[^a-z0-9]+", "-", str(value or "").casefold()).strip("-")
    return text or "unnamed"


def _regular_file(path: str | Path) -> Path:
    value = Path(path).expanduser().absolute()
    if value.is_symlink() or not value.is_file():
        raise ValueError(f"regular non-symlink file required: {value}")
    return value


def _artifact_rows(paths: Sequence[str | Path]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for raw in paths:
        path = _regular_file(raw)
        before = path.stat()
        digest = sha256_file(path)
        after = path.stat()
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            raise ValueError(f"artifact changed while hashing: {path}")
        rows.append(
            {
                "name": path.name,
                "sha256": digest,
                "bytes": int(after.st_size),
                "media_kind": mimetypes.guess_type(path.name)[0] or "application/octet-stream",
            }
        )
    return sorted(rows, key=lambda row: (row["name"], row["sha256"]))


def load_bindings(paths: Sequence[str | Path], suite: Mapping[str, Any]) -> list[dict[str, Any]]:
    found: list[Path] = []
    for raw in paths:
        path = Path(raw).expanduser()
        if path.is_dir():
            found.extend(sorted(path.rglob("*.json")))
        elif path.is_file():
            found.append(path)
        else:
            raise FileNotFoundError(path)
    bindings: list[dict[str, Any]] = []
    for path in found:
        try:
            value = load_json(path)
        except Exception:
            continue
        if value.get("kind") != "earcrate_homelab_specimen_source_binding":
            continue
        specimens.validate_source_binding(value, suite)
        bindings.append(value)
    unique: dict[str, dict[str, Any]] = {}
    for value in bindings:
        unique[str(value["binding_sha256"])] = value
    return sorted(unique.values(), key=lambda value: (value["case_id"], value["source_id"], value["binding_sha256"]))


# ---------------------------------------------------------------------------
# Factory manifest and bounded recipe design
# ---------------------------------------------------------------------------


CASE_RECIPE_TEMPLATES: dict[str, list[dict[str, Any]]] = {
    "same_composition_different_era": [
        {
            "variant": "incumbent_transplant",
            "operations": ["source_custody", "accepted_phrase_clock", "drum_separation", "percussion_transplant", "level_match"],
            "protected_invariants": ["lead_vocal_pitch", "lead_vocal_identity", "terminal_phrase_order"],
        },
        {
            "variant": "phrase_local_transplant",
            "operations": ["source_custody", "phrase_local_alignment", "drum_separation", "percussion_transplant", "level_match"],
            "protected_invariants": ["lead_vocal_pitch", "breath_boundaries", "terminal_call_duration"],
        },
        {
            "variant": "hybrid_drum_body",
            "operations": ["source_custody", "phrase_local_alignment", "drum_separation", "drum_eventization", "hybrid_reconstruction", "level_match"],
            "protected_invariants": ["lead_vocal_authority", "modern_transient_identity", "room_continuity"],
        },
    ],
    "cross_song_compatible_pop_production_grammar": [
        {
            "variant": "modern_percussion_identity_punctuation",
            "operations": ["source_custody", "beat_grid", "tonality", "drum_separation", "identity_punctuation", "percussion_transplant", "level_match"],
            "protected_invariants": ["negative_space", "vocal_center", "identity_punctuation"],
        },
        {
            "variant": "vocal_handoff",
            "operations": ["source_custody", "phrase_alignment", "vocal_separation", "vocal_handoff", "production_grammar_transfer", "level_match"],
            "protected_invariants": ["lyric_order", "foreground_ownership", "hook_recognizability"],
        },
        {
            "variant": "reverse_grammar",
            "operations": ["source_custody", "beat_grid", "tonality", "reverse_production_grammar", "level_match"],
            "protected_invariants": ["source_identity", "rhythmic_negative_space", "hook_shape"],
        },
    ],
    "cross_song_arrangement_ancestry": [
        {
            "variant": "ancestral_width_reconstruction",
            "operations": ["source_custody", "beat_grid", "tonality", "structure", "guitar_width_reconstruction", "band_lift", "level_match"],
            "protected_invariants": ["target_song_harmony", "lead_vocal_identity", "no_famous_riff_copy"],
        },
        {
            "variant": "drum_lift_only",
            "operations": ["source_custody", "beat_grid", "drum_separation", "microtiming_transfer", "drum_lift", "level_match"],
            "protected_invariants": ["target_song_harmony", "target_guitar_identity", "lead_vocal_authority"],
        },
        {
            "variant": "modern_reverse_body",
            "operations": ["source_custody", "structure", "modern_bass_drum_space", "ancestral_identity_retention", "level_match"],
            "protected_invariants": ["ancestral_song_identity", "phrase_scale", "live_band_motion"],
        },
    ],
}

ROLE_REQUIREMENTS: dict[str, tuple[str, ...]] = {
    "source_custody": ("custody",),
    "accepted_phrase_clock": ("beat_grid", "adjudication"),
    "phrase_local_alignment": ("beat_grid", "tonality", "adjudication"),
    "phrase_alignment": ("beat_grid", "tonality"),
    "beat_grid": ("beat_grid",),
    "tonality": ("tonality",),
    "structure": ("structure",),
    "drum_separation": ("separation",),
    "vocal_separation": ("separation",),
    "drum_eventization": ("drum_separation", "transcription"),
    "microtiming_transfer": ("beat_grid", "transcription"),
    "guitar_width_reconstruction": ("tonality", "transcription", "render_reconstruction"),
    "band_lift": ("render_reconstruction",),
    "hybrid_reconstruction": ("drum_separation", "transcription", "render_reconstruction"),
    "production_grammar_transfer": ("separation", "structure", "render_reconstruction"),
    "reverse_production_grammar": ("separation", "structure", "render_reconstruction"),
    "modern_bass_drum_space": ("separation", "render_reconstruction"),
}


def _task_by_id(campaign: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(task["task_id"]): dict(task) for task in campaign.get("tasks") or []}


def _case_map(suite: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(case["canonical_case_id"]): dict(case) for case in suite.get("cases") or []}


def _provider_tasks_by_case_role(campaign: Mapping[str, Any]) -> dict[str, dict[str, list[dict[str, Any]]]]:
    grouped: dict[str, dict[str, list[dict[str, Any]]]] = defaultdict(lambda: defaultdict(list))
    for task in campaign.get("tasks") or []:
        if task.get("task_type") != "specimen_trial":
            continue
        grouped[str(task.get("case_id"))][str(task.get("provider_role") or "other")].append(dict(task))
    for roles in grouped.values():
        for role, rows in roles.items():
            rows.sort(key=lambda row: (-int(row.get("selection_score") or 0), str(row.get("target_id")), str(row.get("task_id"))))
    return grouped


def _needed_roles(operations: Sequence[str]) -> list[str]:
    roles: list[str] = []
    for operation in operations:
        roles.extend(ROLE_REQUIREMENTS.get(str(operation), ()))
    return sorted(set(roles))


def _select_covering_recipes(
    case: Mapping[str, Any],
    provider_roles: Mapping[str, Sequence[Mapping[str, Any]]],
    *,
    max_recipes: int,
) -> list[dict[str, Any]]:
    specimen_class = str(case.get("specimen_class") or "")
    templates = CASE_RECIPE_TEMPLATES.get(specimen_class) or [
        {
            "variant": "generic_control",
            "operations": ["source_custody", "beat_grid", "tonality", "level_match"],
            "protected_invariants": ["source_identity"],
        }
    ]
    recipes: list[dict[str, Any]] = []
    for template in templates:
        roles = _needed_roles(template["operations"])
        selected: dict[str, str] = {}
        missing: list[str] = []
        for role in roles:
            rows = list(provider_roles.get(role) or [])
            if not rows:
                missing.append(role)
            else:
                selected[role] = str(rows[0]["task_id"])
        recipe_seed = {
            "case_id": case["canonical_case_id"],
            "variant": template["variant"],
            "selected_provider_tasks": selected,
            "operations": template["operations"],
            "protected_invariants": template["protected_invariants"],
        }
        recipe = seal(
            {
                "schema_version": SCHEMA_VERSION,
                "kind": "earcrate_homelab_factory_recipe",
                "created_at": now_utc(),
                "case_id": case["canonical_case_id"],
                "specimen_class": specimen_class,
                "variant": template["variant"],
                "selected_provider_tasks": selected,
                "required_roles": roles,
                "missing_roles": missing,
                "operations": list(template["operations"]),
                "protected_invariants": list(template["protected_invariants"]),
                "control_question": case.get("control_question"),
                "quality_descriptors": [
                    "impact",
                    "timing",
                    "bleed",
                    "room_continuity",
                    "recognizability",
                    "vocal_authority",
                    "compute_cost",
                ],
                "authority": {
                    "canonical_write": False,
                    "provider_adoption": False,
                    "release_decision": False,
                },
                "recipe_seed_sha256": sha256_bytes(canonical_bytes(recipe_seed)),
            }
        )
        recipes.append(recipe)

        # One-factor alternatives expose marginal contribution without exploding
        # into a Cartesian product.  Pairwise interactions are added below.
        for role in roles:
            alternatives = list(provider_roles.get(role) or [])[1:3]
            for alternative in alternatives:
                swapped = deepcopy(recipe)
                swapped.pop("recipe_sha256", None)
                swapped["created_at"] = now_utc()
                swapped["variant"] = f"{template['variant']}__swap_{_stable_slug(role)}_{_stable_slug(alternative.get('target_id'))}"
                swapped["selected_provider_tasks"] = dict(selected)
                swapped["selected_provider_tasks"][role] = str(alternative["task_id"])
                swapped["parent_recipe_sha256"] = recipe["recipe_sha256"]
                swapped["mutation"] = {"kind": "one_factor_swap", "role": role, "target_id": alternative.get("target_id")}
                recipes.append(seal(swapped))
                if len(recipes) >= max_recipes:
                    break
            if len(recipes) >= max_recipes:
                break
        if len(recipes) >= max_recipes:
            break

    # Deterministic pairwise coverage among the first alternatives for roles that
    # have more than one provider.  This catches important organ interactions.
    if len(recipes) < max_recipes and recipes:
        base = recipes[0]
        mutable_roles = [role for role in base["required_roles"] if len(provider_roles.get(role) or []) > 1]
        for left_index, left in enumerate(mutable_roles):
            for right in mutable_roles[left_index + 1 :]:
                candidate = deepcopy(base)
                candidate.pop("recipe_sha256", None)
                candidate["created_at"] = now_utc()
                candidate["variant"] = f"{base['variant']}__pair_{_stable_slug(left)}_{_stable_slug(right)}"
                candidate["selected_provider_tasks"] = dict(base["selected_provider_tasks"])
                candidate["selected_provider_tasks"][left] = str(provider_roles[left][1]["task_id"])
                candidate["selected_provider_tasks"][right] = str(provider_roles[right][1]["task_id"])
                candidate["parent_recipe_sha256"] = base["recipe_sha256"]
                candidate["mutation"] = {"kind": "pairwise_swap", "roles": [left, right]}
                recipes.append(seal(candidate))
                if len(recipes) >= max_recipes:
                    break
            if len(recipes) >= max_recipes:
                break

    unique = {str(recipe["recipe_sha256"]): recipe for recipe in recipes}
    return list(unique.values())[:max_recipes]


def compile_factory_manifest(
    suite: Mapping[str, Any],
    *,
    catalog: Mapping[str, Any],
    audit: Mapping[str, Any],
    bindings: Sequence[Mapping[str, Any]],
    role_policy: Mapping[str, Any],
    profile: str = "core",
    case_ids: Sequence[str] = (),
    max_recipes_per_case: int = 12,
) -> dict[str, Any]:
    suite_sha = validate(suite, kind="earcrate_homelab_specimen_suite")
    specimen_campaign = specimens.compile_specimen_campaign(
        suite,
        catalog_object=catalog,
        audit_object=audit,
        bindings=bindings,
        policy=role_policy,
        profile=profile,
        case_ids=case_ids,
    )
    cases = _case_map(suite)
    selected_case_ids = list(specimen_campaign.get("selected_case_ids") or [])
    grouped = _provider_tasks_by_case_role(specimen_campaign)
    recipes: list[dict[str, Any]] = []
    for case_id in selected_case_ids:
        recipes.extend(
            _select_covering_recipes(
                cases[case_id],
                grouped.get(case_id) or {},
                max_recipes=max_recipes_per_case,
            )
        )
    source_binding_ids = sorted(str(binding["binding_sha256"]) for binding in bindings if binding.get("case_id") in selected_case_ids)
    manifest = seal(
        {
            "schema_version": SCHEMA_VERSION,
            "kind": "earcrate_homelab_factory_manifest",
            "created_at": now_utc(),
            "suite_sha256": suite_sha,
            "catalog_sha256": catalog.get("catalog_sha256"),
            "audit_sha256": audit.get("audit_sha256"),
            "specimen_campaign_sha256": specimen_campaign["campaign_sha256"],
            "profile": profile,
            "selected_case_ids": selected_case_ids,
            "source_binding_sha256s": source_binding_ids,
            "provider_tasks": [
                dict(task)
                for task in specimen_campaign.get("tasks") or []
                if task.get("task_type") in {"specimen_trial", "specimen_prerequisite"}
            ],
            "recipes": recipes,
            "review_dimensions": list(DEFAULT_REVIEW_DIMENSIONS),
            "search_policy": {
                "design": "incumbent_plus_one_factor_swaps_plus_bounded_pairwise_coverage",
                "max_recipes_per_case": max_recipes_per_case,
                "cartesian_product_forbidden": True,
                "quality_diversity_frontier_size": 4,
                "incumbent_control_required": True,
                "human_review_is_acceptance_authority": True,
            },
            "resource_policy": {
                "cpu_workers": "auto",
                "gpu_workers": "one worker per declared GPU",
                "gpu_memory_is_not_pooled": True,
                "exclusive_gpu_leases": True,
                "exclusive_audio_device_leases": True,
            },
            "authority": {
                "providers_propose": True,
                "factory_can_write_canonical_musical_state": False,
                "factory_can_accept_provider": False,
                "factory_can_release_audio": False,
                "owner_review_controls_musical_preference": True,
            },
            "embedded_specimen_campaign": specimen_campaign,
        }
    )
    return manifest


def compile_factory_campaign(manifest: Mapping[str, Any]) -> dict[str, Any]:
    manifest_sha = validate(manifest, kind="earcrate_homelab_factory_manifest")
    specimen_campaign = dict(manifest["embedded_specimen_campaign"])
    validate(specimen_campaign, kind="earcrate_homelab_campaign")
    provider_tasks = [dict(task) for task in specimen_campaign.get("tasks") or [] if task.get("task_type") in {"specimen_trial", "specimen_prerequisite"}]
    tasks: list[dict[str, Any]] = provider_tasks
    provider_ids = {str(task["task_id"]) for task in provider_tasks}
    recipes_by_case: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for recipe in manifest.get("recipes") or []:
        validate(recipe, kind="earcrate_homelab_factory_recipe")
        recipes_by_case[str(recipe["case_id"])].append(dict(recipe))
        dependencies = sorted(set(str(value) for value in recipe.get("selected_provider_tasks", {}).values()))
        unknown = sorted(set(dependencies) - provider_ids)
        if unknown:
            raise ValueError(f"recipe references unknown provider tasks: {unknown}")
        blocked = bool(recipe.get("missing_roles")) or any(
            next(task for task in provider_tasks if task["task_id"] == dependency).get("status") != "ready"
            for dependency in dependencies
        )
        tasks.append(
            {
                "task_id": f"factory.recipe.{recipe['recipe_sha256'][:20]}",
                "target_id": "earcrate-material-forge",
                "task_type": "factory_recipe",
                "stage": "recipe_render",
                "status": "blocked" if blocked else "ready",
                "assigned_node_sha256": next((task.get("assigned_node_sha256") for task in provider_tasks if task.get("assigned_node_sha256")), None),
                "resource": "gpu-or-cpu",
                "reason": "render one proof-carrying organ combination",
                "depends_on": dependencies,
                "case_id": recipe["case_id"],
                "factory_manifest_sha256": manifest_sha,
                "recipe_sha256": recipe["recipe_sha256"],
                "required_output_kinds": ["earcrate_homelab_factory_run"],
            }
        )

    for case_id, recipes in sorted(recipes_by_case.items()):
        recipe_task_ids = [
            str(task["task_id"])
            for task in tasks
            if task.get("task_type") == "factory_recipe"
            and task.get("case_id") == case_id
            and task.get("status") == "ready"
        ]
        archive_id = f"factory.archive.{_stable_slug(case_id)}"
        review_id = f"factory.review.{_stable_slug(case_id)}"
        preference_id = f"factory.preference.{_stable_slug(case_id)}"
        circulation_id = f"factory.circulate.{_stable_slug(case_id)}"
        tasks.extend(
            [
                {
                    "task_id": archive_id,
                    "target_id": "earcrate-quality-diversity-archive",
                    "task_type": "factory_archive",
                    "stage": "quality_archive",
                    "status": "ready",
                    "resource": "cpu",
                    "reason": "select a diverse signal-sane frontier rather than every render",
                    "depends_on": recipe_task_ids,
                    "case_id": case_id,
                    "factory_manifest_sha256": manifest_sha,
                    "required_output_kinds": ["earcrate_homelab_quality_archive"],
                },
                {
                    "task_id": review_id,
                    "target_id": "human-musical-review",
                    "task_type": "factory_review",
                    "stage": "blind_review",
                    "status": "ready",
                    "resource": "human+playback",
                    "reason": "run level-matched blind review over the selected frontier",
                    "depends_on": [archive_id],
                    "case_id": case_id,
                    "factory_manifest_sha256": manifest_sha,
                    "required_output_kinds": ["earcrate_homelab_factory_review_ledger"],
                },
                {
                    "task_id": preference_id,
                    "target_id": "earcrate-reviewpatch-learning",
                    "task_type": "factory_preference",
                    "stage": "preference_update",
                    "status": "ready",
                    "resource": "authority",
                    "reason": "create a scoped ReviewPatch and selective recomputation plan",
                    "depends_on": [review_id],
                    "case_id": case_id,
                    "factory_manifest_sha256": manifest_sha,
                    "required_output_kinds": ["earcrate_homelab_preference_update"],
                },
                {
                    "task_id": circulation_id,
                    "target_id": "earcrate-public-circulation",
                    "task_type": "factory_circulation",
                    "stage": "public_export",
                    "status": "ready",
                    "resource": "cpu",
                    "reason": "publish source-free receipts and next-round instructions",
                    "depends_on": [preference_id],
                    "case_id": case_id,
                    "factory_manifest_sha256": manifest_sha,
                    "required_output_kinds": ["earcrate_homelab_circulation_packet"],
                },
            ]
        )

    counts = Counter(str(task.get("task_type")) for task in tasks)
    ready = sum(1 for task in tasks if task.get("status") == "ready")
    blocked = len(tasks) - ready
    return specimens.seal(
        {
            "schema_version": SCHEMA_VERSION,
            "kind": "earcrate_homelab_campaign",
            "created_at": now_utc(),
            "catalog_sha256": manifest.get("catalog_sha256"),
            "audit_sha256": manifest.get("audit_sha256"),
            "factory_manifest_sha256": manifest_sha,
            "specimen_suite_sha256": manifest.get("suite_sha256"),
            "tasks": tasks,
            "summary": {
                "tasks": len(tasks),
                "ready": ready,
                "blocked": blocked,
                "task_types": dict(sorted(counts.items())),
                "cases": len(recipes_by_case),
                "recipes": sum(len(value) for value in recipes_by_case.values()),
            },
            "completion_gate": {
                "passed": False,
                "provider_trials_have_exact_receipts": True,
                "recipe_runs_have_exact_intermediate_custody": True,
                "quality_archive_precedes_human_review": True,
                "human_review_precedes_preference_update": True,
                "preference_update_must_change_later_behavior": True,
                "public_circulation_contains_no_source_media_or_private_paths": True,
            },
        }
    )


# ---------------------------------------------------------------------------
# Provider adapters and exact execution receipts
# ---------------------------------------------------------------------------


DEFAULT_ADAPTERS: dict[str, dict[str, Any]] = {
    "ffmpeg": {"handler": "ffmpeg_probe", "executables": ["ffmpeg", "ffprobe"], "timeout_seconds": 900},
    "chromaprint": {"handler": "chromaprint", "executables": ["fpcalc"], "timeout_seconds": 300},
    "demucs": {"handler": "demucs", "python_module": "demucs.separate", "timeout_seconds": 7200},
    "audio-separator": {"handler": "audio_separator", "executables": ["audio-separator"], "timeout_seconds": 7200},
    "rubber-band": {"handler": "rubberband", "executables": ["rubberband"], "timeout_seconds": 1800},
    "soundfile": {"handler": "identity", "python_distribution": "soundfile", "timeout_seconds": 60},
    "mido": {"handler": "identity", "python_distribution": "mido", "timeout_seconds": 60},
    "librosa": {"handler": "librosa_observe", "python_distribution": "librosa", "timeout_seconds": 1800},
    "aubio": {"handler": "aubio_observe", "python_distribution": "aubio", "timeout_seconds": 1800},
    "basic-pitch": {"handler": "basic_pitch", "python_distribution": "basic-pitch", "timeout_seconds": 7200},
    "earcrate-signal-evaluator": {"handler": "signal_evaluator", "executables": ["ffmpeg", "ffprobe"], "timeout_seconds": 1800},
}


def load_adapter_policy(paths: Sequence[str | Path] = ()) -> dict[str, Any]:
    adapters = deepcopy(DEFAULT_ADAPTERS)
    recipe_plugins: dict[str, Any] = {}
    for raw in paths:
        value = load_json_source(raw)
        for target_id, row in dict(value.get("adapters") or {}).items():
            adapters[str(target_id)] = deepcopy(dict(row or {}))
        recipe_plugins.update(deepcopy(dict(value.get("recipe_plugins") or {})))
    return {"schema_version": 1, "adapters": adapters, "recipe_plugins": recipe_plugins}


def _resolve_adapter(target_id: str, policy: Mapping[str, Any]) -> dict[str, Any] | None:
    adapters = dict(policy.get("adapters") or {})
    if target_id in adapters:
        return deepcopy(dict(adapters[target_id]))
    for pattern, row in adapters.items():
        if any(character in pattern for character in "*?[") and fnmatch.fnmatch(target_id, pattern):
            return deepcopy(dict(row))
    return None


def _subprocess(
    argv: Sequence[str],
    *,
    cwd: Path,
    timeout: int,
    environment: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    started = time.monotonic()
    env = {
        "PATH": os.environ.get("PATH", ""),
        "SYSTEMROOT": os.environ.get("SYSTEMROOT", ""),
        "WINDIR": os.environ.get("WINDIR", ""),
        "TEMP": os.environ.get("TEMP", os.environ.get("TMP", "")),
        "TMP": os.environ.get("TMP", os.environ.get("TEMP", "")),
        "PYTHONUTF8": "1",
    }
    if environment:
        env.update({str(key): str(value) for key, value in environment.items()})
    try:
        process = subprocess.run(
            list(argv),
            cwd=cwd,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            errors="replace",
            timeout=timeout,
            shell=False,
        )
        return {
            "returncode": int(process.returncode),
            "elapsed_seconds": round(time.monotonic() - started, 6),
            "stdout": process.stdout[-20000:],
            "stderr": process.stderr[-20000:],
            "timed_out": False,
        }
    except subprocess.TimeoutExpired as exc:
        return {
            "returncode": None,
            "elapsed_seconds": round(time.monotonic() - started, 6),
            "stdout": str(exc.stdout or "")[-20000:],
            "stderr": str(exc.stderr or "")[-20000:],
            "timed_out": True,
        }


def _binding_paths(bindings: Sequence[Mapping[str, Any]], case_id: str) -> list[Path]:
    values: list[Path] = []
    for binding in bindings:
        if binding.get("case_id") != case_id:
            continue
        values.append(_regular_file(str(binding["artifact_path"])))
    return values


def _write_command_receipt(path: Path, payload: Mapping[str, Any]) -> Path:
    return write_json(path, payload, exclusive=True)


def _handler_ffmpeg_probe(sources: Sequence[Path], output: Path, adapter: Mapping[str, Any], gpu: str | None) -> tuple[list[Path], dict[str, Any], list[str]]:
    artifacts: list[Path] = []
    commands: list[dict[str, Any]] = []
    for index, source in enumerate(sources):
        probe_path = output / f"source-{index:02d}.ffprobe.json"
        probe = _subprocess(
            ["ffprobe", "-v", "error", "-show_format", "-show_streams", "-of", "json", str(source)],
            cwd=output,
            timeout=int(adapter.get("timeout_seconds") or 900),
        )
        commands.append({"tool": "ffprobe", "source_sha256": sha256_file(source), **probe})
        if probe["returncode"] == 0:
            atomic_write(probe_path, str(probe["stdout"]).encode("utf-8"), exclusive=True)
            artifacts.append(probe_path)
        signal_path = output / f"source-{index:02d}.signal.txt"
        signal = _subprocess(
            ["ffmpeg", "-nostdin", "-hide_banner", "-i", str(source), "-filter_complex", "ebur128=peak=true,astats=metadata=1:reset=0", "-f", "null", "-"],
            cwd=output,
            timeout=int(adapter.get("timeout_seconds") or 900),
        )
        commands.append({"tool": "ffmpeg", "source_sha256": sha256_file(source), **signal})
        atomic_write(signal_path, (str(signal["stdout"]) + "\n" + str(signal["stderr"])).encode("utf-8"), exclusive=True)
        artifacts.append(signal_path)
    return artifacts, {"commands": commands, "sources": len(sources)}, []


def _handler_chromaprint(sources: Sequence[Path], output: Path, adapter: Mapping[str, Any], gpu: str | None) -> tuple[list[Path], dict[str, Any], list[str]]:
    artifacts: list[Path] = []
    results = []
    for index, source in enumerate(sources):
        result = _subprocess(["fpcalc", "-json", str(source)], cwd=output, timeout=int(adapter.get("timeout_seconds") or 300))
        results.append({"source_sha256": sha256_file(source), **result})
        target = output / f"source-{index:02d}.chromaprint.json"
        if result["returncode"] == 0:
            atomic_write(target, str(result["stdout"]).encode("utf-8"), exclusive=True)
            artifacts.append(target)
    return artifacts, {"results": results}, []


def _handler_demucs(sources: Sequence[Path], output: Path, adapter: Mapping[str, Any], gpu: str | None) -> tuple[list[Path], dict[str, Any], list[str]]:
    model = str(adapter.get("model") or "htdemucs")
    two_stems = str(adapter.get("two_stems") or "drums")
    environment = {"CUDA_VISIBLE_DEVICES": str(gpu)} if gpu is not None else {}
    commands = []
    artifacts: list[Path] = []
    for source in sources:
        argv = [sys.executable, "-m", str(adapter.get("python_module") or "demucs.separate"), "-n", model, "--two-stems", two_stems, "-o", str(output), str(source)]
        result = _subprocess(argv, cwd=output, timeout=int(adapter.get("timeout_seconds") or 7200), environment=environment)
        commands.append({"source_sha256": sha256_file(source), "model": model, **result})
    for path in sorted(output.rglob("*.wav")):
        if path.is_file() and not path.is_symlink():
            artifacts.append(path)
    return artifacts, {"commands": commands, "model": model, "gpu": gpu}, []


def _handler_audio_separator(sources: Sequence[Path], output: Path, adapter: Mapping[str, Any], gpu: str | None) -> tuple[list[Path], dict[str, Any], list[str]]:
    model = str(adapter.get("model_filename") or "")
    if not model:
        return [], {"adapter": "audio_separator"}, ["audio-separator requires an exact configured model_filename"]
    environment = {"CUDA_VISIBLE_DEVICES": str(gpu)} if gpu is not None else {}
    commands = []
    for source in sources:
        argv = ["audio-separator", str(source), "--output_dir", str(output), "--output_format", "WAV", "--model_filename", model]
        commands.append(_subprocess(argv, cwd=output, timeout=int(adapter.get("timeout_seconds") or 7200), environment=environment))
    artifacts = [path for path in sorted(output.rglob("*.wav")) if path.is_file() and not path.is_symlink()]
    return artifacts, {"commands": commands, "model_filename": model, "gpu": gpu}, []


def _handler_rubberband(sources: Sequence[Path], output: Path, adapter: Mapping[str, Any], gpu: str | None) -> tuple[list[Path], dict[str, Any], list[str]]:
    ratio = float(adapter.get("time_ratio") or 1.0)
    if not 0.25 <= ratio <= 4.0:
        return [], {"ratio": ratio}, ["rubberband time_ratio outside 0.25..4.0"]
    commands = []
    artifacts: list[Path] = []
    for index, source in enumerate(sources):
        target = output / f"source-{index:02d}.stretched.wav"
        result = _subprocess(["rubberband", "-t", f"{ratio:.12g}", str(source), str(target)], cwd=output, timeout=int(adapter.get("timeout_seconds") or 1800))
        commands.append(result)
        if result["returncode"] == 0 and target.is_file():
            artifacts.append(target)
    return artifacts, {"commands": commands, "time_ratio": ratio}, []


def _handler_identity(sources: Sequence[Path], output: Path, adapter: Mapping[str, Any], gpu: str | None) -> tuple[list[Path], dict[str, Any], list[str]]:
    distribution = str(adapter.get("python_distribution") or "")
    version = None
    if distribution:
        try:
            from importlib.metadata import version as distribution_version
            version = distribution_version(distribution)
        except Exception as exc:
            return [], {"distribution": distribution}, [f"distribution unavailable: {type(exc).__name__}: {exc}"]
    target = output / "identity.json"
    payload = {"python": sys.version, "distribution": distribution or None, "version": version, "source_sha256s": [sha256_file(path) for path in sources]}
    write_json(target, payload, exclusive=True)
    return [target], payload, []


def _handler_python_observe(sources: Sequence[Path], output: Path, adapter: Mapping[str, Any], gpu: str | None, module: str) -> tuple[list[Path], dict[str, Any], list[str]]:
    script = r'''
import json, sys
from pathlib import Path
import numpy as np
import soundfile as sf
module = sys.argv[1]
source = sys.argv[2]
out = sys.argv[3]
y, sr = sf.read(source, always_2d=False)
if getattr(y, "ndim", 1) > 1:
    y = np.mean(y, axis=1)
result = {"sample_rate": int(sr), "frames": int(len(y)), "module": module}
if module == "librosa":
    import librosa
    tempo, beats = librosa.beat.beat_track(y=y.astype(float), sr=sr)
    chroma = librosa.feature.chroma_cqt(y=y.astype(float), sr=sr)
    onset = librosa.onset.onset_strength(y=y.astype(float), sr=sr)
    result.update({"tempo": float(np.asarray(tempo).reshape(-1)[0]), "beat_frames": [int(v) for v in np.asarray(beats).reshape(-1)], "chroma_mean": [float(v) for v in np.mean(chroma, axis=1)], "onset_mean": float(np.mean(onset))})
elif module == "aubio":
    import aubio
    source_obj = aubio.source(source, 0, 512)
    tempo_obj = aubio.tempo("default", 1024, 512, source_obj.samplerate)
    times=[]
    while True:
        samples, read = source_obj()
        if tempo_obj(samples): times.append(float(tempo_obj.get_last_s()))
        if read < 512: break
    result.update({"beat_times_seconds": times, "bpm": float(tempo_obj.get_bpm()) if times else None})
Path(out).write_text(json.dumps(result, indent=2, sort_keys=True)+"\n", encoding="utf-8")
'''
    artifacts = []
    commands = []
    for index, source in enumerate(sources):
        target = output / f"source-{index:02d}.{module}.json"
        result = _subprocess([sys.executable, "-c", script, module, str(source), str(target)], cwd=output, timeout=int(adapter.get("timeout_seconds") or 1800))
        commands.append(result)
        if result["returncode"] == 0 and target.is_file():
            artifacts.append(target)
    return artifacts, {"commands": commands, "module": module}, []


def _handler_basic_pitch(sources: Sequence[Path], output: Path, adapter: Mapping[str, Any], gpu: str | None) -> tuple[list[Path], dict[str, Any], list[str]]:
    commands = []
    for source in sources:
        commands.append(_subprocess(["basic-pitch", str(output), str(source)], cwd=output, timeout=int(adapter.get("timeout_seconds") or 7200)))
    artifacts = [path for path in sorted(output.rglob("*")) if path.is_file() and not path.is_symlink()]
    return artifacts, {"commands": commands}, []


def _handler_signal_evaluator(sources: Sequence[Path], output: Path, adapter: Mapping[str, Any], gpu: str | None) -> tuple[list[Path], dict[str, Any], list[str]]:
    return _handler_ffmpeg_probe(sources, output, adapter, gpu)


HANDLERS = {
    "ffmpeg_probe": _handler_ffmpeg_probe,
    "chromaprint": _handler_chromaprint,
    "demucs": _handler_demucs,
    "audio_separator": _handler_audio_separator,
    "rubberband": _handler_rubberband,
    "identity": _handler_identity,
    "basic_pitch": _handler_basic_pitch,
    "signal_evaluator": _handler_signal_evaluator,
}


def execute_provider_task(
    task: Mapping[str, Any],
    *,
    suite: Mapping[str, Any],
    specimen_campaign: Mapping[str, Any],
    bindings: Sequence[Mapping[str, Any]],
    adapter_policy: Mapping[str, Any],
    output_directory: str | Path,
    worker_id: str,
    gpu: str | None = None,
) -> dict[str, Any]:
    if task.get("task_type") != "specimen_trial":
        raise ValueError("provider executor requires specimen_trial task")
    target_id = str(task.get("target_id") or "")
    adapter = _resolve_adapter(target_id, adapter_policy)
    output = Path(output_directory).expanduser().absolute()
    output.mkdir(parents=True, exist_ok=False)
    sources = _binding_paths(bindings, str(task.get("case_id") or ""))
    notes: list[str] = []
    artifacts: list[Path] = []
    measurements: dict[str, Any] = {
        "worker_id": worker_id,
        "gpu": gpu,
        "target_id": target_id,
        "source_sha256s": [sha256_file(path) for path in sources],
    }
    outcome = "refused"
    if not sources:
        notes.append("no bound source paths for case")
    elif adapter is None:
        notes.append("no executable adapter is registered for this target; task remains durable evidence instead of hidden manual work")
    else:
        handler_name = str(adapter.get("handler") or "")
        try:
            if handler_name == "librosa_observe":
                artifacts, observed, refusals = _handler_python_observe(sources, output, adapter, gpu, "librosa")
            elif handler_name == "aubio_observe":
                artifacts, observed, refusals = _handler_python_observe(sources, output, adapter, gpu, "aubio")
            elif handler_name == "command":
                argv = [str(value) for value in adapter.get("argv") or []]
                if not argv:
                    artifacts, observed, refusals = [], {}, ["command adapter has no argv"]
                else:
                    context = {
                        "python": sys.executable,
                        "output_dir": str(output),
                        "gpu": str(gpu or ""),
                        "source0": str(sources[0]),
                        "source1": str(sources[1]) if len(sources) > 1 else "",
                        "case_id": str(task.get("case_id") or ""),
                    }
                    expanded = [value.format_map(defaultdict(str, context)) for value in argv]
                    result = _subprocess(expanded, cwd=output, timeout=int(adapter.get("timeout_seconds") or 3600), environment={"CUDA_VISIBLE_DEVICES": str(gpu)} if gpu is not None else {})
                    artifacts = [path for path in sorted(output.rglob("*")) if path.is_file() and not path.is_symlink()]
                    observed, refusals = {"command": result}, [] if result["returncode"] == 0 else ["command returned nonzero"]
            else:
                handler = HANDLERS.get(handler_name)
                if handler is None:
                    artifacts, observed, refusals = [], {}, [f"unknown adapter handler: {handler_name}"]
                else:
                    artifacts, observed, refusals = handler(sources, output, adapter, gpu)
            measurements.update(observed)
            notes.extend(refusals)
            command_failed = any(
                row.get("returncode") not in {0, None} or row.get("timed_out")
                for row in measurements.get("commands") or measurements.get("results") or []
                if isinstance(row, Mapping)
            )
            if refusals:
                outcome = "refused"
            elif command_failed:
                outcome = "failed"
            elif artifacts or measurements:
                outcome = "observed"
        except Exception as exc:
            notes.append(f"adapter exception: {type(exc).__name__}: {exc}")
            outcome = "failed"

    command_receipt = _write_command_receipt(
        output / "factory-command-receipt.json",
        {
            "schema_version": 1,
            "kind": "earcrate_factory_command_receipt",
            "recorded_at": now_utc(),
            "task_id": task.get("task_id"),
            "target_id": target_id,
            "worker_id": worker_id,
            "gpu": gpu,
            "outcome": outcome,
            "measurements": measurements,
            "notes": notes,
            "boundary": {"shell_used": False, "canonical_write": False, "source_bytes_copied": False},
        },
    )
    artifacts.append(command_receipt)
    receipt = specimens.record_specimen_trial(
        suite,
        specimen_campaign,
        task_id=str(task["task_id"]),
        node_sha256=task.get("assigned_node_sha256"),
        outcome=outcome,
        actor_id=worker_id,
        actor_type="machine",
        artifacts=artifacts,
        source_bindings=bindings,
        measurements=measurements,
        notes=notes,
    )
    write_json(output / "specimen-trial-receipt.json", receipt, exclusive=True)
    return receipt


# ---------------------------------------------------------------------------
# Recipe execution, archive, review, learning, and circulation
# ---------------------------------------------------------------------------


def _recipe_map(manifest: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(recipe["recipe_sha256"]): dict(recipe) for recipe in manifest.get("recipes") or []}


def _receipt_artifact_candidates(receipts: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for receipt in receipts:
        identity = receipt.get("receipt_sha256") or receipt.get("run_sha256")
        for artifact in receipt.get("derived_artifacts") or receipt.get("artifacts") or []:
            media = str(artifact.get("media_kind") or "")
            name = str(artifact.get("name") or "")
            if media.startswith("audio/") or Path(name).suffix.casefold() in {".wav", ".flac", ".aiff", ".mp3"}:
                rows.append({"receipt_identity": identity, **dict(artifact)})
    return rows


def execute_recipe_task(
    task: Mapping[str, Any],
    *,
    manifest: Mapping[str, Any],
    completed_receipts: Sequence[Mapping[str, Any]],
    bindings: Sequence[Mapping[str, Any]],
    adapter_policy: Mapping[str, Any],
    output_directory: str | Path,
    worker_id: str,
    gpu: str | None = None,
) -> dict[str, Any]:
    recipe = _recipe_map(manifest)[str(task["recipe_sha256"])]
    output = Path(output_directory).expanduser().absolute()
    output.mkdir(parents=True, exist_ok=False)
    plugins = dict(adapter_policy.get("recipe_plugins") or {})
    plugin = deepcopy(dict(plugins.get(str(recipe.get("variant"))) or plugins.get(str(recipe.get("specimen_class"))) or {}))
    sources = _binding_paths(bindings, str(recipe["case_id"]))
    provider_artifacts = _receipt_artifact_candidates(completed_receipts)
    artifacts: list[Path] = []
    notes: list[str] = []
    measurements: dict[str, Any] = {
        "worker_id": worker_id,
        "gpu": gpu,
        "recipe_sha256": recipe["recipe_sha256"],
        "provider_receipt_count": len(completed_receipts),
        "provider_audio_artifact_count": len(provider_artifacts),
    }
    outcome = "refused"

    # A configured command is the production extension seam.  Exact argv, model,
    # and intermediate identities are retained in the run receipt.
    if plugin.get("argv"):
        context = defaultdict(
            str,
            {
                "python": sys.executable,
                "output_dir": str(output),
                "gpu": str(gpu or ""),
                "source0": str(sources[0]) if sources else "",
                "source1": str(sources[1]) if len(sources) > 1 else "",
                "case_id": str(recipe["case_id"]),
                "recipe_sha256": str(recipe["recipe_sha256"]),
            },
        )
        argv = [str(value).format_map(context) for value in plugin.get("argv") or []]
        result = _subprocess(argv, cwd=output, timeout=int(plugin.get("timeout_seconds") or 7200), environment={"CUDA_VISIBLE_DEVICES": str(gpu)} if gpu is not None else {})
        measurements["command"] = result
        artifacts = [path for path in sorted(output.rglob("*")) if path.is_file() and not path.is_symlink()]
        outcome = "passed" if result["returncode"] == 0 and artifacts else "failed"
    elif sources and provider_artifacts and shutil.which("ffmpeg"):
        # Conservative smoke fallback.  It never claims alignment or musical
        # acceptance.  It proves that the selected organ graph can lower to audio.
        source = sources[0]
        artifact_name = str(provider_artifacts[0].get("name") or "")
        artifact_path = next((path for path in output.parent.parent.rglob(artifact_name) if path.is_file()), None)
        if artifact_path is None:
            notes.append("provider audio artifact exists by identity but no private local path was found in the current factory workspace")
        else:
            target = output / "candidate.wav"
            argv = [
                "ffmpeg", "-nostdin", "-hide_banner", "-y",
                "-i", str(source), "-i", str(artifact_path),
                "-filter_complex", "[0:a]atrim=0:20,asetpts=PTS-STARTPTS,volume=0.9[v];[1:a]atrim=0:20,asetpts=PTS-STARTPTS,volume=0.8[d];[v][d]amix=inputs=2:duration=shortest:normalize=0,alimiter=limit=0.85[out]",
                "-map", "[out]", "-ar", "48000", "-ac", "2", str(target),
            ]
            result = _subprocess(argv, cwd=output, timeout=1800)
            measurements["command"] = result
            if result["returncode"] == 0 and target.is_file():
                artifacts = [target]
                outcome = "passed"
                notes.append("generic smoke lowering only; alignment and musical acceptance remain unresolved")
            else:
                outcome = "failed"
    else:
        notes.append("recipe has no configured lowering plugin or locally resolvable provider audio artifact")

    rows = _artifact_rows(artifacts)
    run = seal(
        {
            "schema_version": 1,
            "kind": "earcrate_homelab_factory_run",
            "recorded_at": now_utc(),
            "factory_manifest_sha256": manifest["manifest_sha256"],
            "recipe_sha256": recipe["recipe_sha256"],
            "case_id": recipe["case_id"],
            "task_id": task["task_id"],
            "worker_id": worker_id,
            "gpu": gpu,
            "outcome": outcome,
            "provider_receipt_identities": sorted(
                str(receipt.get("receipt_sha256") or receipt.get("run_sha256"))
                for receipt in completed_receipts
                if receipt.get("receipt_sha256") or receipt.get("run_sha256")
            ),
            "source_binding_sha256s": sorted(str(binding["binding_sha256"]) for binding in bindings if binding.get("case_id") == recipe["case_id"]),
            "artifacts": rows,
            "measurements": measurements,
            "notes": notes,
            "authority": {
                "canonical_musical_write": False,
                "human_acceptance": False,
                "provider_adoption": False,
                "release_decision": False,
            },
        }
    )
    write_json(output / "factory-run-receipt.json", run, exclusive=True)
    return run


def _descriptor_vector(run: Mapping[str, Any]) -> dict[str, float]:
    measurements = dict(run.get("measurements") or {})
    signal = dict(measurements.get("signal") or {})
    artifacts = list(run.get("artifacts") or [])
    # Missing musical metrics remain neutral, never fabricated as excellent.
    return {
        "impact": float(signal.get("impact", 0.5)),
        "timing": float(signal.get("timing", 0.5)),
        "bleed": float(signal.get("bleed", 0.5)),
        "room_continuity": float(signal.get("room_continuity", 0.5)),
        "recognizability": float(signal.get("recognizability", 0.5)),
        "vocal_authority": float(signal.get("vocal_authority", 0.5)),
        "compute_cost": float(measurements.get("elapsed_seconds") or (measurements.get("command") or {}).get("elapsed_seconds") or 0.0),
        "audio_artifacts": float(sum(1 for row in artifacts if str(row.get("media_kind") or "").startswith("audio/"))),
    }


def build_quality_archive(
    *,
    manifest: Mapping[str, Any],
    case_id: str,
    runs: Sequence[Mapping[str, Any]],
    frontier_size: int = 4,
) -> dict[str, Any]:
    accepted = [dict(run) for run in runs if run.get("case_id") == case_id and run.get("outcome") == "passed" and any(str(row.get("media_kind") or "").startswith("audio/") for row in run.get("artifacts") or [])]
    entries = []
    for run in accepted:
        vector = _descriptor_vector(run)
        quality = (
            vector["impact"]
            + vector["timing"]
            + (1.0 - vector["bleed"])
            + vector["room_continuity"]
            + vector["recognizability"]
            + vector["vocal_authority"]
        ) / 6.0
        cell = f"impact-{round(vector['impact'] * 4)}/bleed-{round(vector['bleed'] * 4)}/room-{round(vector['room_continuity'] * 4)}"
        entries.append({"run_sha256": run["run_sha256"], "recipe_sha256": run["recipe_sha256"], "descriptors": vector, "quality": quality, "cell": cell, "artifacts": deepcopy(run.get("artifacts") or [])})
    entries.sort(key=lambda row: (-row["quality"], row["cell"], row["run_sha256"]))
    by_cell: dict[str, dict[str, Any]] = {}
    for row in entries:
        by_cell.setdefault(str(row["cell"]), row)
    diverse = list(by_cell.values())
    diverse.sort(key=lambda row: (-row["quality"], row["run_sha256"]))
    frontier = diverse[:frontier_size]
    if entries and entries[0] not in frontier:
        frontier.insert(0, entries[0])
    selected_ids = {str(row["run_sha256"]) for row in frontier}
    for row in entries:
        if len(frontier) >= frontier_size:
            break
        if str(row["run_sha256"]) not in selected_ids:
            frontier.append(row)
            selected_ids.add(str(row["run_sha256"]))
    frontier = frontier[:frontier_size]
    return seal(
        {
            "schema_version": 1,
            "kind": "earcrate_homelab_quality_archive",
            "created_at": now_utc(),
            "factory_manifest_sha256": manifest["manifest_sha256"],
            "case_id": case_id,
            "entries": entries,
            "frontier": frontier,
            "frontier_size": len(frontier),
            "selection_policy": {
                "signal_sane_only": True,
                "quality_diversity_cells": True,
                "incumbent_control_required_when_available": True,
                "machine_selection_is_not_musical_acceptance": True,
            },
        }
    )


def prepare_factory_review(
    archive: Mapping[str, Any],
    *,
    run_paths: Mapping[str, str | Path],
    public_directory: str | Path,
    private_directory: str | Path,
    reviewer_id: str,
    dimensions: Sequence[str] = DEFAULT_REVIEW_DIMENSIONS,
    seed: int | None = None,
) -> dict[str, Any]:
    archive_sha = validate(archive, kind="earcrate_homelab_quality_archive")
    public = Path(public_directory).expanduser().absolute()
    private = Path(private_directory).expanduser().absolute()
    if public == private or public in private.parents or private in public.parents:
        raise ValueError("public and private review directories must be disjoint")
    public.mkdir(parents=True, exist_ok=False)
    private.mkdir(parents=True, exist_ok=False)
    frontier = list(archive.get("frontier") or [])
    if len(frontier) < 2:
        raise ValueError("blind review requires at least two frontier candidates")
    rng = random.Random(seed if seed is not None else secrets.randbits(64))
    labels = [chr(ord("A") + index) for index in range(len(frontier))]
    shuffled = list(frontier)
    rng.shuffle(shuffled)
    token = secrets.token_urlsafe(32)
    token_sha = hashlib.sha256(token.encode("utf-8")).hexdigest()
    options: dict[str, dict[str, Any]] = {}
    option_map: dict[str, str] = {}
    source_artifacts: dict[str, dict[str, Any]] = {}
    for label, row in zip(labels, shuffled):
        run_sha = str(row["run_sha256"])
        source = _regular_file(run_paths[run_sha])
        extension = source.suffix.casefold() or ".wav"
        destination = public / f"{label}{extension}"
        shutil.copyfile(source, destination)
        options[label] = {
            "sha256": sha256_file(destination),
            "bytes": int(destination.stat().st_size),
            "media_kind": mimetypes.guess_type(destination.name)[0] or "application/octet-stream",
        }
        option_map[label] = run_sha
        source_artifacts[run_sha] = {"sha256": sha256_file(source), "bytes": int(source.stat().st_size), "name": source.name}
    authority_seed = secrets.token_hex(32)
    authority_commitment = hashlib.sha256(canonical_bytes({"option_map": option_map, "source_artifacts": source_artifacts, "seed": authority_seed})).hexdigest()
    assignment = seal(
        {
            "schema_version": 1,
            "kind": "earcrate_homelab_factory_review_assignment",
            "created_at": now_utc(),
            "archive_sha256": archive_sha,
            "factory_manifest_sha256": archive.get("factory_manifest_sha256"),
            "case_id": archive.get("case_id"),
            "reviewer_id": reviewer_id,
            "options": options,
            "choices": [*labels, "tie", "reject_all", "abstain"],
            "dimensions": [str(value) for value in dimensions],
            "private_authority_commitment": authority_commitment,
            "review_token_sha256": token_sha,
            "public_metrics": {
                "level_matching_required": True,
                "candidate_specific_signal_metrics_withheld_until_submission": True,
            },
        }
    )
    authority = seal(
        {
            "schema_version": 1,
            "kind": "earcrate_homelab_factory_private_assignment_authority",
            "created_at": now_utc(),
            "assignment_sha256": assignment["assignment_sha256"],
            "archive_sha256": archive_sha,
            "case_id": archive.get("case_id"),
            "reviewer_id": reviewer_id,
            "option_map": option_map,
            "source_artifacts": source_artifacts,
            "authority_seed": authority_seed,
            "authority_commitment": authority_commitment,
            "review_token": token,
            "review_token_sha256": token_sha,
        }
    )
    write_json(public / "assignment.json", assignment, exclusive=True)
    write_json(private / "assignment-authority.json", authority, exclusive=True)
    atomic_write(private / "review-token.txt", (token + "\n").encode("utf-8"), exclusive=True)
    return {"assignment": assignment, "private_authority": authority, "review_token": token, "public_directory": str(public), "private_directory": str(private)}


def submit_factory_review(
    assignment: Mapping[str, Any],
    *,
    reviewer_id: str,
    review_token: str,
    choice: str,
    dimensions: Mapping[str, Any],
    notes: Sequence[str] = (),
) -> dict[str, Any]:
    assignment_sha = validate(assignment, kind="earcrate_homelab_factory_review_assignment")
    if reviewer_id != assignment.get("reviewer_id"):
        raise ValueError("reviewer identity does not match assignment")
    if choice not in set(str(value) for value in assignment.get("choices") or []):
        raise ValueError("invalid review choice")
    token_sha = hashlib.sha256(review_token.encode("utf-8")).hexdigest()
    if not secrets.compare_digest(token_sha, str(assignment.get("review_token_sha256") or "")):
        raise PermissionError("review token mismatch")
    allowed_dimensions = set(str(value) for value in assignment.get("dimensions") or [])
    unknown = sorted(set(str(key) for key in dimensions) - allowed_dimensions)
    if unknown:
        raise ValueError("unknown review dimensions: " + ", ".join(unknown))
    body = {
        "assignment_sha256": assignment_sha,
        "reviewer_id": reviewer_id,
        "choice": choice,
        "dimensions": deepcopy(dict(dimensions)),
        "notes": [str(value) for value in notes],
    }
    proof = hmac.new(review_token.encode("utf-8"), canonical_bytes(body), hashlib.sha256).hexdigest()
    return seal(
        {
            "schema_version": 1,
            "kind": "earcrate_homelab_factory_review_submission",
            "submitted_at": now_utc(),
            **body,
            "review_token_sha256": token_sha,
            "submission_proof_hmac_sha256": proof,
        }
    )


def adjudicate_factory_review(
    assignment: Mapping[str, Any],
    private_authority: Mapping[str, Any],
    submission: Mapping[str, Any],
) -> dict[str, Any]:
    assignment_sha = validate(assignment, kind="earcrate_homelab_factory_review_assignment")
    authority_sha = validate(private_authority, kind="earcrate_homelab_factory_private_assignment_authority")
    submission_sha = validate(submission, kind="earcrate_homelab_factory_review_submission")
    if private_authority.get("assignment_sha256") != assignment_sha or submission.get("assignment_sha256") != assignment_sha:
        raise ValueError("review objects belong to different assignments")
    if private_authority.get("authority_commitment") != assignment.get("private_authority_commitment"):
        raise ValueError("private authority commitment mismatch")
    token = str(private_authority.get("review_token") or "")
    if hashlib.sha256(token.encode("utf-8")).hexdigest() != assignment.get("review_token_sha256"):
        raise ValueError("private review token commitment mismatch")
    proof_body = {
        "assignment_sha256": assignment_sha,
        "reviewer_id": submission.get("reviewer_id"),
        "choice": submission.get("choice"),
        "dimensions": deepcopy(dict(submission.get("dimensions") or {})),
        "notes": [str(value) for value in submission.get("notes") or []],
    }
    expected_hmac = hmac.new(token.encode("utf-8"), canonical_bytes(proof_body), hashlib.sha256).hexdigest()
    if not secrets.compare_digest(expected_hmac, str(submission.get("submission_proof_hmac_sha256") or "")):
        raise PermissionError("review submission token proof mismatch")
    choice = str(submission.get("choice") or "")
    winner = (private_authority.get("option_map") or {}).get(choice) if choice in assignment.get("options", {}) else None
    verdict = "accept" if winner else ("reject" if choice == "reject_all" else choice)
    return seal(
        {
            "schema_version": 1,
            "kind": "earcrate_homelab_factory_review_ledger",
            "adjudicated_at": now_utc(),
            "assignment_sha256": assignment_sha,
            "private_authority_sha256": authority_sha,
            "submission_sha256": submission_sha,
            "archive_sha256": assignment.get("archive_sha256"),
            "case_id": assignment.get("case_id"),
            "reviewer_id": submission.get("reviewer_id"),
            "choice": choice,
            "winner_run_sha256": winner,
            "verdict": verdict,
            "dimensions": deepcopy(dict(submission.get("dimensions") or {})),
            "notes": [str(value) for value in submission.get("notes") or []],
            "authority": {
                "human_musical_preference": True,
                "provider_adoption": False,
                "release_decision": False,
                "general_taste_model": False,
            },
        }
    )


def compile_preference_update(
    ledger: Mapping[str, Any],
    *,
    archive: Mapping[str, Any],
    manifest: Mapping[str, Any],
) -> dict[str, Any]:
    ledger_sha = validate(ledger, kind="earcrate_homelab_factory_review_ledger")
    archive_sha = validate(archive, kind="earcrate_homelab_quality_archive")
    manifest_sha = validate(manifest, kind="earcrate_homelab_factory_manifest")
    if ledger.get("archive_sha256") != archive_sha or archive.get("factory_manifest_sha256") != manifest_sha:
        raise ValueError("review, archive, and manifest do not reconcile")
    winner = ledger.get("winner_run_sha256")
    changed_behavior = bool(winner and ledger.get("verdict") == "accept")
    protected = ["source identities", "unrelated provider receipts", "unselected fixture cases", "historical review objects"]
    invalidated = ["recipe ranking for the reviewed case", "descendants of the reviewed recipe"] if changed_behavior else []
    return seal(
        {
            "schema_version": 1,
            "kind": "earcrate_homelab_preference_update",
            "created_at": now_utc(),
            "factory_manifest_sha256": manifest_sha,
            "archive_sha256": archive_sha,
            "review_ledger_sha256": ledger_sha,
            "case_id": ledger.get("case_id"),
            "scope": "fixture_and_review_dimensions_only",
            "winner_run_sha256": winner,
            "verdict": ledger.get("verdict"),
            "dimension_observations": deepcopy(dict(ledger.get("dimensions") or {})),
            "review_patch": {
                "target": "factory recipe ranking",
                "requested_change": "prefer the accepted recipe family and preserve dimensions scored highly; repair dimensions scored weakly",
                "protected_invariants": protected,
                "invalidation_scope": invalidated,
                "unrelated_organs_bit_identical_required": True,
            },
            "next_round": {
                "changed_behavior_required": changed_behavior,
                "retain_incumbent_control": True,
                "test_one_factor_and_pairwise_interactions": True,
                "transfer_to_held_out_specimen_after_local_confirmation": True,
            },
            "authority": {
                "general_taste_model": False,
                "cross_fixture_transfer_assumed": False,
                "historical_receipts_rewritten": False,
            },
        }
    )


def _redact(value: Any, *, key: str | None = None, counters: MutableMapping[str, int] | None = None) -> Any:
    counters = counters if counters is not None else defaultdict(int)
    normalized = str(key or "").casefold()
    if normalized in SENSITIVE_KEYS or any(fragment in normalized for fragment in ("password", "secret", "token", "credential")) and not normalized.endswith("sha256"):
        counters["sensitive"] += 1
        return "redacted"
    if isinstance(value, Mapping):
        return {str(child_key): _redact(child_value, key=str(child_key), counters=counters) for child_key, child_value in value.items()}
    if isinstance(value, list):
        return [_redact(child, key=key, counters=counters) for child in value]
    if isinstance(value, str) and ABSOLUTE_PATH.match(value.strip()):
        counters["paths"] += 1
        return "redacted:sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()
    return value


def build_circulation_packet(
    *,
    manifest: Mapping[str, Any],
    campaign: Mapping[str, Any],
    objects: Sequence[Mapping[str, Any]],
    output_directory: str | Path,
) -> dict[str, Any]:
    manifest_sha = validate(manifest, kind="earcrate_homelab_factory_manifest")
    campaign_sha = validate(campaign, kind="earcrate_homelab_campaign")
    destination = Path(output_directory).expanduser().absolute()
    destination.mkdir(parents=True, exist_ok=False)
    entries: list[dict[str, Any]] = []
    for index, value in enumerate(objects):
        counters: MutableMapping[str, int] = defaultdict(int)
        projected = _redact(deepcopy(dict(value)), counters=counters)
        source_kind = str(value.get("kind") or "unknown")
        source_identity = None
        field = specimens.HASH_FIELDS.get(source_kind)
        if field:
            source_identity = value.get(field)
        path = destination / f"{index:03d}-{_stable_slug(source_kind)}.json"
        write_json(path, projected, exclusive=True)
        entries.append(
            {
                "path": path.name,
                "sha256": sha256_file(path),
                "bytes": int(path.stat().st_size),
                "source_kind": source_kind,
                "source_identity": source_identity,
                "absolute_paths_redacted": int(counters["paths"]),
                "sensitive_fields_redacted": int(counters["sensitive"]),
            }
        )
    packet = seal(
        {
            "schema_version": 1,
            "kind": "earcrate_homelab_circulation_packet",
            "created_at": now_utc(),
            "factory_manifest_sha256": manifest_sha,
            "campaign_sha256": campaign_sha,
            "entries": entries,
            "boundary": {
                "source_media_exported": False,
                "private_paths_exported": False,
                "credentials_exported": False,
                "private_review_mapping_exported": False,
                "projected_objects_are_not_original_authority": True,
            },
        }
    )
    write_json(destination / "circulation-packet.json", packet, exclusive=True)
    checksums = []
    for path in sorted(destination.iterdir()):
        if path.is_file() and path.name != "SHA256SUMS.txt":
            checksums.append(f"{sha256_file(path)}  {path.name}")
    atomic_write(destination / "SHA256SUMS.txt", ("\n".join(checksums) + "\n").encode("utf-8"), exclusive=True)
    return packet


# ---------------------------------------------------------------------------
# Workspace state and one-command local operation
# ---------------------------------------------------------------------------


def _initial_state(manifest: Mapping[str, Any], campaign: Mapping[str, Any]) -> dict[str, Any]:
    tasks = {}
    for task in campaign.get("tasks") or []:
        tasks[str(task["task_id"])] = {
            "status": "blocked" if task.get("status") == "blocked" else "queued",
            "evidence_identity": None,
            "error": None,
            "attempts": 0,
        }
    return seal(
        {
            "schema_version": 1,
            "kind": "earcrate_homelab_factory_state",
            "created_at": now_utc(),
            "updated_at": now_utc(),
            "factory_manifest_sha256": manifest["manifest_sha256"],
            "campaign_sha256": campaign["campaign_sha256"],
            "tasks": tasks,
            "objects": {},
            "human_review_queue": [],
            "summary": {},
        }
    )


def _state_path(workspace: Path) -> Path:
    return workspace / "factory-state.json"


def _save_state(workspace: Path, state: Mapping[str, Any]) -> dict[str, Any]:
    value = deepcopy(dict(state))
    value.pop("state_sha256", None)
    value["updated_at"] = now_utc()
    value["summary"] = dict(Counter(str(row.get("status")) for row in value.get("tasks", {}).values()))
    sealed = seal(value)
    write_json(_state_path(workspace), sealed, exclusive=False)
    if isinstance(state, MutableMapping):
        state.clear()
        state.update(deepcopy(sealed))
    return sealed


def bootstrap_workspace(
    workspace: str | Path,
    *,
    suite: Mapping[str, Any],
    catalog: Mapping[str, Any],
    audit: Mapping[str, Any],
    bindings: Sequence[Mapping[str, Any]],
    role_policy: Mapping[str, Any],
    profile: str,
    case_ids: Sequence[str],
    max_recipes_per_case: int = 12,
) -> dict[str, Any]:
    root = Path(workspace).expanduser().absolute()
    root.mkdir(parents=True, exist_ok=False)
    manifest = compile_factory_manifest(
        suite,
        catalog=catalog,
        audit=audit,
        bindings=bindings,
        role_policy=role_policy,
        profile=profile,
        case_ids=case_ids,
        max_recipes_per_case=max_recipes_per_case,
    )
    campaign = compile_factory_campaign(manifest)
    state = _initial_state(manifest, campaign)
    write_json(root / "factory-manifest.json", manifest, exclusive=True)
    write_json(root / "factory-campaign.json", campaign, exclusive=True)
    write_json(root / "specimen-suite.json", suite, exclusive=True)
    write_json(root / "role-policy.json", role_policy, exclusive=True)
    bindings_root = root / "private-bindings"
    bindings_root.mkdir()
    for binding in bindings:
        write_json(bindings_root / f"{binding['case_id']}--{binding['source_id']}.json", binding, exclusive=True)
    write_json(_state_path(root), state, exclusive=True)
    (root / "runs").mkdir()
    (root / "reviews").mkdir()
    (root / "circulation").mkdir()
    return {"workspace": str(root), "manifest": manifest, "campaign": campaign, "state": state}


def _dependency_statuses(task: Mapping[str, Any], state: Mapping[str, Any]) -> list[str]:
    rows = state.get("tasks") or {}
    return [str((rows.get(str(dep)) or {}).get("status") or "missing") for dep in task.get("depends_on") or []]


def _dependencies_complete(task: Mapping[str, Any], state: Mapping[str, Any]) -> bool:
    statuses = _dependency_statuses(task, state)
    if task.get("task_type") == "factory_archive":
        return all(status in TERMINAL_STATES for status in statuses)
    return all(status == "completed" for status in statuses)


def _dependency_failure(task: Mapping[str, Any], state: Mapping[str, Any]) -> str | None:
    if task.get("task_type") == "factory_archive":
        return None
    statuses = _dependency_statuses(task, state)
    failed = [status for status in statuses if status in {"failed", "refused", "cancelled"}]
    if failed:
        return "dependency ended without usable evidence: " + ", ".join(sorted(set(failed)))
    return None


def _register_object(state: MutableMapping[str, Any], value: Mapping[str, Any], path: Path) -> str:
    kind = str(value.get("kind") or "")
    field = specimens.HASH_FIELDS.get(kind)
    if not field:
        raise ValueError(f"unregistered evidence kind: {kind}")
    identity = str(value[field])
    state.setdefault("objects", {})[identity] = {"kind": kind, "path": str(path), "visibility": "private" if "private" in kind else "public"}
    return identity


def _load_state_objects(state: Mapping[str, Any]) -> list[dict[str, Any]]:
    values: list[dict[str, Any]] = []
    for row in (state.get("objects") or {}).values():
        path = Path(str(row.get("path") or ""))
        if path.is_file():
            values.append(load_json(path))
    return values


def _task_receipts(task: Mapping[str, Any], state: Mapping[str, Any]) -> list[dict[str, Any]]:
    objects = _load_state_objects(state)
    needed = set(str(value) for value in task.get("depends_on") or [])
    evidence_ids = {
        str((state.get("tasks") or {}).get(task_id, {}).get("evidence_identity") or "")
        for task_id in needed
    }
    return [value for value in objects if any(value.get(field) in evidence_ids for field in ("receipt_sha256", "run_sha256", "archive_sha256", "ledger_sha256", "update_sha256"))]


def _candidate_paths_for_archive(archive: Mapping[str, Any], state: Mapping[str, Any]) -> dict[str, Path]:
    result: dict[str, Path] = {}
    object_rows = state.get("objects") or {}
    for row in archive.get("frontier") or []:
        run_sha = str(row["run_sha256"])
        object_path = Path(str((object_rows.get(run_sha) or {}).get("path") or ""))
        if not object_path.is_file():
            continue
        run = load_json(object_path)
        run_dir = object_path.parent
        for artifact in run.get("artifacts") or []:
            if str(artifact.get("media_kind") or "").startswith("audio/"):
                candidate = next((path for path in run_dir.rglob(str(artifact.get("name") or "")) if path.is_file()), None)
                if candidate:
                    result[run_sha] = candidate
                    break
    return result


def run_factory(
    workspace: str | Path,
    *,
    adapter_policy: Mapping[str, Any],
    worker_id: str = "factory-local",
    gpus: Sequence[str] = (),
    max_parallel_cpu: int = 2,
    stop_at_review: bool = True,
) -> dict[str, Any]:
    root = Path(workspace).expanduser().absolute()
    manifest = load_json(root / "factory-manifest.json")
    campaign = load_json(root / "factory-campaign.json")
    suite = load_json(root / "specimen-suite.json")
    state = load_json(_state_path(root))
    bindings = load_bindings([root / "private-bindings"], suite)
    task_map = _task_by_id(campaign)
    gpu_cycle = list(gpus)
    progress = True
    while progress:
        progress = False
        ready: list[dict[str, Any]] = []
        for task_id, task in task_map.items():
            status = (state.get("tasks") or {}).get(task_id, {}).get("status")
            if status != "queued":
                continue
            dependency_failure = _dependency_failure(task, state)
            if dependency_failure:
                state["tasks"][task_id]["status"] = "refused"
                state["tasks"][task_id]["error"] = dependency_failure
                progress = True
                continue
            if not _dependencies_complete(task, state):
                continue
            ready.append(task)
        if not ready:
            break

        machine = [task for task in ready if task.get("task_type") in MACHINE_TASK_TYPES]
        human = [task for task in ready if task.get("task_type") in HUMAN_TASK_TYPES]
        authority = [task for task in ready if task.get("task_type") in AUTHORITY_TASK_TYPES]

        # Provider and recipe tasks can run concurrently.  GPU tasks receive one
        # explicit device each and never assume memory pooling.
        def execute(task: dict[str, Any], index: int) -> tuple[str, dict[str, Any], Path]:
            task_id = str(task["task_id"])
            task_state = state["tasks"][task_id]
            task_state["attempts"] = int(task_state.get("attempts") or 0) + 1
            task_state["status"] = "running"
            output = root / "runs" / _stable_slug(task_id)
            gpu = gpu_cycle[index % len(gpu_cycle)] if gpu_cycle and "gpu" in str(task.get("resource") or "") else None
            if task.get("task_type") == "specimen_trial":
                receipt = execute_provider_task(
                    task,
                    suite=suite,
                    specimen_campaign=manifest["embedded_specimen_campaign"],
                    bindings=bindings,
                    adapter_policy=adapter_policy,
                    output_directory=output,
                    worker_id=worker_id,
                    gpu=gpu,
                )
                receipt_path = output / "specimen-trial-receipt.json"
            elif task.get("task_type") == "factory_recipe":
                receipt = execute_recipe_task(
                    task,
                    manifest=manifest,
                    completed_receipts=_task_receipts(task, state),
                    bindings=bindings,
                    adapter_policy=adapter_policy,
                    output_directory=output,
                    worker_id=worker_id,
                    gpu=gpu,
                )
                receipt_path = output / "factory-run-receipt.json"
            elif task.get("task_type") == "factory_archive":
                runs = [value for value in _task_receipts(task, state) if value.get("kind") == "earcrate_homelab_factory_run"]
                receipt = build_quality_archive(manifest=manifest, case_id=str(task["case_id"]), runs=runs)
                output.mkdir(parents=True, exist_ok=False)
                receipt_path = write_json(output / "quality-archive.json", receipt, exclusive=True)
            elif task.get("task_type") == "factory_circulation":
                output.mkdir(parents=True, exist_ok=False)
                objects = _load_state_objects(state)
                receipt = build_circulation_packet(manifest=manifest, campaign=campaign, objects=objects, output_directory=output / "public")
                receipt_path = write_json(output / "circulation-receipt.json", receipt, exclusive=True)
            else:
                raise ValueError(f"unsupported machine task type: {task.get('task_type')}")
            return task_id, receipt, receipt_path

        if machine:
            with ThreadPoolExecutor(max_workers=max(1, min(max_parallel_cpu + len(gpu_cycle), len(machine)))) as pool:
                futures = {pool.submit(execute, task, index): task for index, task in enumerate(machine)}
                for future in as_completed(futures):
                    task = futures[future]
                    task_id = str(task["task_id"])
                    try:
                        completed_id, receipt, receipt_path = future.result()
                        identity = _register_object(state, receipt, receipt_path)
                        state["tasks"][completed_id]["status"] = "completed" if receipt.get("outcome") not in {"failed", "refused"} else str(receipt.get("outcome"))
                        state["tasks"][completed_id]["evidence_identity"] = identity
                    except Exception as exc:
                        state["tasks"][task_id]["status"] = "failed"
                        state["tasks"][task_id]["error"] = f"{type(exc).__name__}: {exc}"[:4000]
                    progress = True
                    _save_state(root, state)

        for task in authority:
            task_id = str(task["task_id"])
            dependencies = _task_receipts(task, state)
            ledger = next((value for value in dependencies if value.get("kind") == "earcrate_homelab_factory_review_ledger"), None)
            archive = next((value for value in _load_state_objects(state) if value.get("kind") == "earcrate_homelab_quality_archive" and value.get("case_id") == task.get("case_id")), None)
            if task.get("task_type") == "factory_preference" and ledger and archive:
                output = root / "runs" / _stable_slug(task_id)
                output.mkdir(parents=True, exist_ok=False)
                update = compile_preference_update(ledger, archive=archive, manifest=manifest)
                path = write_json(output / "preference-update.json", update, exclusive=True)
                identity = _register_object(state, update, path)
                state["tasks"][task_id]["status"] = "completed"
                state["tasks"][task_id]["evidence_identity"] = identity
                progress = True
                _save_state(root, state)

        for task in human:
            task_id = str(task["task_id"])
            if task.get("task_type") != "factory_review":
                state["tasks"][task_id]["status"] = "human_pending"
                continue
            archive = next((value for value in _task_receipts(task, state) if value.get("kind") == "earcrate_homelab_quality_archive"), None)
            if archive is None:
                continue
            candidate_paths = _candidate_paths_for_archive(archive, state)
            if len(candidate_paths) < 2:
                state["tasks"][task_id]["status"] = "failed"
                state["tasks"][task_id]["error"] = "quality archive has fewer than two locally resolvable audio candidates"
                continue
            review_root = root / "reviews" / _stable_slug(str(task.get("case_id")))
            prepared = prepare_factory_review(
                archive,
                run_paths=candidate_paths,
                public_directory=review_root / "public",
                private_directory=review_root / "private",
                reviewer_id="operator:owner",
                dimensions=manifest.get("review_dimensions") or DEFAULT_REVIEW_DIMENSIONS,
            )
            assignment_path = review_root / "public" / "assignment.json"
            authority_path = review_root / "private" / "assignment-authority.json"
            assignment_id = _register_object(state, prepared["assignment"], assignment_path)
            _register_object(state, prepared["private_authority"], authority_path)
            state["tasks"][task_id]["status"] = "human_pending"
            state["tasks"][task_id]["evidence_identity"] = assignment_id
            state.setdefault("human_review_queue", []).append({"task_id": task_id, "case_id": task.get("case_id"), "public_directory": str(review_root / "public"), "private_directory": str(review_root / "private")})
            progress = True
            _save_state(root, state)
        if human and stop_at_review:
            break
    _save_state(root, state)
    return {"workspace": str(root), "summary": state.get("summary"), "human_review_queue": state.get("human_review_queue")}


def ingest_review_and_resume(
    workspace: str | Path,
    *,
    case_id: str,
    choice: str,
    dimensions: Mapping[str, Any],
    notes: Sequence[str] = (),
    adapter_policy: Mapping[str, Any] | None = None,
    worker_id: str = "factory-local",
    gpus: Sequence[str] = (),
) -> dict[str, Any]:
    root = Path(workspace).expanduser().absolute()
    state = load_json(_state_path(root))
    review_root = root / "reviews" / _stable_slug(case_id)
    assignment = load_json(review_root / "public" / "assignment.json")
    authority = load_json(review_root / "private" / "assignment-authority.json")
    token = (review_root / "private" / "review-token.txt").read_text(encoding="utf-8").strip()
    submission = submit_factory_review(assignment, reviewer_id=str(assignment["reviewer_id"]), review_token=token, choice=choice, dimensions=dimensions, notes=notes)
    ledger = adjudicate_factory_review(assignment, authority, submission)
    submission_path = write_json(review_root / "submission.json", submission, exclusive=True)
    ledger_path = write_json(review_root / "review-ledger.json", ledger, exclusive=True)
    _register_object(state, submission, submission_path)
    ledger_identity = _register_object(state, ledger, ledger_path)
    task_id = f"factory.review.{_stable_slug(case_id)}"
    if task_id not in state.get("tasks", {}):
        raise KeyError(task_id)
    state["tasks"][task_id]["status"] = "completed"
    state["tasks"][task_id]["evidence_identity"] = ledger_identity
    state["human_review_queue"] = [row for row in state.get("human_review_queue") or [] if row.get("task_id") != task_id]
    _save_state(root, state)
    return run_factory(root, adapter_policy=adapter_policy or load_adapter_policy(), worker_id=worker_id, gpus=gpus, stop_at_review=False)


def _store_visibility(value: Mapping[str, Any]) -> str:
    kind = str(value.get("kind") or "")
    if kind in {
        "earcrate_homelab_specimen_source_binding",
        "earcrate_homelab_factory_private_assignment_authority",
        "earcrate_homelab_factory_state",
    }:
        return "sensitive"
    if kind in {
        "earcrate_homelab_factory_review_submission",
    }:
        return "private"
    return "public"


def sync_workspace_to_store(workspace: str | Path, store_root: str | Path) -> dict[str, Any]:
    """Ingest sealed factory evidence and run the existing Homelab doctor.

    The factory does not maintain a second database.  SQLite remains an index and
    journal over independently sealed JSON authority.
    """
    from earcrate.estate.homelab_store import HomelabStore

    root = Path(workspace).expanduser().absolute()
    values: list[dict[str, Any]] = [
        load_json(root / "specimen-suite.json"),
        load_json(root / "factory-manifest.json"),
        load_json(root / "factory-campaign.json"),
        load_json(_state_path(root)),
    ]
    values.extend(load_bindings([root / "private-bindings"], values[0]))
    values.extend(_load_state_objects(values[3]))
    unique: dict[str, dict[str, Any]] = {}
    for value in values:
        field = specimens.HASH_FIELDS.get(str(value.get("kind") or ""))
        if not field:
            continue
        validate(value)
        unique[str(value[field])] = value
    ingested = []
    with HomelabStore(store_root) as store:
        for identity, value in sorted(unique.items()):
            result = store.ingest_object(value, visibility=_store_visibility(value))
            ingested.append(result)
        doctor = store.doctor()
    if not doctor.get("ok"):
        raise RuntimeError("Homelab store doctor failed after factory synchronization")
    return {
        "ok": True,
        "store": str(Path(store_root).expanduser().absolute()),
        "objects": len(ingested),
        "created": sum(1 for row in ingested if row.get("created")),
        "doctor": doctor,
    }


def verify_workspace(workspace: str | Path) -> dict[str, Any]:
    root = Path(workspace).expanduser().absolute()
    manifest = load_json(root / "factory-manifest.json")
    campaign = load_json(root / "factory-campaign.json")
    state = load_json(_state_path(root))
    validate(manifest, kind="earcrate_homelab_factory_manifest")
    validate(campaign, kind="earcrate_homelab_campaign")
    validate(state, kind="earcrate_homelab_factory_state")
    failures = []
    for identity, row in (state.get("objects") or {}).items():
        path = Path(str(row.get("path") or ""))
        if not path.is_file():
            failures.append(f"missing:{identity}")
            continue
        value = load_json(path)
        try:
            actual = validate(value)
        except Exception as exc:
            failures.append(f"invalid:{identity}:{type(exc).__name__}:{exc}")
            continue
        if actual != identity:
            failures.append(f"identity_mismatch:{identity}:{actual}")
    return {"ok": not failures, "workspace": str(root), "objects": len(state.get("objects") or {}), "failures": failures, "summary": state.get("summary")}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parse_dimensions(text: str) -> dict[str, Any]:
    value = json.loads(text)
    if not isinstance(value, dict):
        raise ValueError("dimensions must decode to a JSON object")
    return value


def factory_cli_main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="earcrate-factory", description="Compile, run, review, learn from, and circulate EarCrate organ combinations")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("bootstrap", help="compile a source-bound factory manifest and campaign")
    p.add_argument("--suite", default="builtin:specimen-suite")
    p.add_argument("--catalog", required=True)
    p.add_argument("--audit", required=True)
    p.add_argument("--bindings", action="append", required=True)
    p.add_argument("--role-policy", default="builtin:provider-role-policy")
    p.add_argument("--workspace", required=True)
    p.add_argument("--profile", choices=["smoke", "core", "full"], default="core")
    p.add_argument("--case", action="append", default=[])
    p.add_argument("--max-recipes-per-case", type=int, default=12)

    p = sub.add_parser("run", help="execute all dependency-ready machine tasks and stop at human review")
    p.add_argument("--workspace", required=True)
    p.add_argument("--adapter-policy", action="append", default=[])
    p.add_argument("--worker-id", default="factory-local")
    p.add_argument("--gpu", action="append", default=[])
    p.add_argument("--max-parallel-cpu", type=int, default=2)
    p.add_argument("--through-review", action="store_true", help="do not stop merely because another case has a pending review")

    p = sub.add_parser("review", help="seal an owner review, produce a scoped preference update, and resume circulation")
    p.add_argument("--workspace", required=True)
    p.add_argument("--case", required=True)
    p.add_argument("--choice", required=True)
    p.add_argument("--dimensions-json", required=True)
    p.add_argument("--note", action="append", default=[])
    p.add_argument("--adapter-policy", action="append", default=[])
    p.add_argument("--worker-id", default="factory-local")
    p.add_argument("--gpu", action="append", default=[])

    p = sub.add_parser("sync-store", help="ingest sealed factory objects into the existing Homelab store and run doctor")
    p.add_argument("--workspace", required=True)
    p.add_argument("--store", required=True)

    p = sub.add_parser("verify", help="verify all sealed factory workspace objects")
    p.add_argument("--workspace", required=True)

    p = sub.add_parser("status", help="print the current factory state and review queue")
    p.add_argument("--workspace", required=True)

    args = parser.parse_args(list(argv) if argv is not None else None)
    try:
        if args.command == "bootstrap":
            suite = load_json_source(args.suite)
            bindings = load_bindings(args.bindings, suite)
            result = bootstrap_workspace(
                args.workspace,
                suite=suite,
                catalog=load_json(args.catalog),
                audit=load_json(args.audit),
                bindings=bindings,
                role_policy=load_json_source(args.role_policy),
                profile=args.profile,
                case_ids=args.case,
                max_recipes_per_case=args.max_recipes_per_case,
            )
            print(json.dumps({"ok": True, "workspace": result["workspace"], "manifest_sha256": result["manifest"]["manifest_sha256"], "campaign_sha256": result["campaign"]["campaign_sha256"], "summary": result["campaign"]["summary"]}, indent=2, sort_keys=True))
            return 0
        if args.command == "run":
            result = run_factory(
                args.workspace,
                adapter_policy=load_adapter_policy(args.adapter_policy),
                worker_id=args.worker_id,
                gpus=args.gpu,
                max_parallel_cpu=args.max_parallel_cpu,
                stop_at_review=not args.through_review,
            )
            print(json.dumps(result, indent=2, sort_keys=True))
            return 0
        if args.command == "review":
            result = ingest_review_and_resume(
                args.workspace,
                case_id=args.case,
                choice=args.choice,
                dimensions=_parse_dimensions(args.dimensions_json),
                notes=args.note,
                adapter_policy=load_adapter_policy(args.adapter_policy),
                worker_id=args.worker_id,
                gpus=args.gpu,
            )
            print(json.dumps(result, indent=2, sort_keys=True))
            return 0
        if args.command == "sync-store":
            result = sync_workspace_to_store(args.workspace, args.store)
            print(json.dumps(result, indent=2, sort_keys=True))
            return 0
        if args.command == "verify":
            result = verify_workspace(args.workspace)
            print(json.dumps(result, indent=2, sort_keys=True))
            return 0 if result["ok"] else 1
        if args.command == "status":
            state = load_json(_state_path(Path(args.workspace).expanduser().absolute()))
            print(json.dumps({"summary": state.get("summary"), "human_review_queue": state.get("human_review_queue"), "state_sha256": state.get("state_sha256")}, indent=2, sort_keys=True))
            return 0
    except Exception as exc:
        print(json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"}, indent=2, sort_keys=True), file=sys.stderr)
        return 1
    return 1


if __name__ == "__main__":
    raise SystemExit(factory_cli_main())


__all__ = [
    "FACTORY_HASH_FIELDS",
    "adjudicate_factory_review",
    "bootstrap_workspace",
    "build_circulation_packet",
    "build_quality_archive",
    "compile_factory_campaign",
    "compile_factory_manifest",
    "compile_preference_update",
    "execute_provider_task",
    "execute_recipe_task",
    "factory_cli_main",
    "ingest_review_and_resume",
    "load_adapter_policy",
    "prepare_factory_review",
    "run_factory",
    "submit_factory_review",
    "sync_workspace_to_store",
    "verify_workspace",
]
