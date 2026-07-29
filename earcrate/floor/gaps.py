from __future__ import annotations

"""Executable register of the interoperability gaps the Floor is intended to close."""

from copy import deepcopy
from typing import Any

from .model import floor_sha256_json

FLOOR_GAP_REGISTER: tuple[dict[str, Any], ...] = (
    {
        "gap_id": "evidence_tier_isolation",
        "problem": "score, community notation, metadata, model inference, and blind PCM evidence are routinely conflated",
        "floor_contract": "ProviderRequest evidence_branch/evidence_tier plus ancestry custody",
        "status": "implemented",
    },
    {
        "gap_id": "provider_authority_ceiling",
        "problem": "model outputs often overwrite canonical application state",
        "floor_contract": "providers may emit evidence and proposals but never canonical musical authority",
        "status": "implemented",
    },
    {
        "gap_id": "source_performance_time_duality",
        "problem": "timeline formats often collapse source time into performance time",
        "floor_contract": "TimeMap with continuous, jump, loop, retrigger, reverse, and hold segments",
        "status": "implemented",
    },
    {
        "gap_id": "phrase_fungibility",
        "problem": "similarity vectors do not say whether material may occupy a musical slot",
        "floor_contract": "PhraseContract with hard constraints, transforms, identity, future obligations, and rights",
        "status": "implemented",
    },
    {
        "gap_id": "rights_assertion_vs_legal_decision",
        "problem": "rights metadata is easily laundered into an unsupported clearance claim",
        "floor_contract": "RightsEnvelope assertions with provider_may_not_decide_legality",
        "status": "implemented",
    },
    {
        "gap_id": "artifact_custody",
        "problem": "derived files are passed without content identity or source lineage",
        "floor_contract": "content-addressed input/output artifacts and invocation receipts",
        "status": "implemented",
    },
    {
        "gap_id": "artifact_path_containment",
        "problem": "plugins can write outside their negotiated artifact directory",
        "floor_contract": "path traversal, absolute paths, drive prefixes, and symlink refusal",
        "status": "implemented",
    },
    {
        "gap_id": "supply_chain_identity",
        "problem": "provider, executable, model, and license identities are frequently absent from results",
        "floor_contract": "manifest supply_chain plus executable identity in InvocationReceipt",
        "status": "implemented",
    },
    {
        "gap_id": "conformance_vs_quality",
        "problem": "successful execution is mistaken for musical accuracy",
        "floor_contract": "ConformanceReport and independent EvaluationLedger are separate objects",
        "status": "implemented",
    },
    {
        "gap_id": "evaluator_independence",
        "problem": "providers can grade themselves or share hidden identity with evaluators",
        "floor_contract": "EvaluationLedger refuses evaluator_id == provider_id",
        "status": "implemented",
    },
    {
        "gap_id": "multi_objective_selection",
        "problem": "one hidden scalar obscures hard failures and musical tradeoffs",
        "floor_contract": "hard gates followed by sealed lexicographic objective stages",
        "status": "implemented",
    },
    {
        "gap_id": "winner_authority",
        "problem": "a benchmark winner is easily mistaken for canonical truth",
        "floor_contract": "TournamentReport winner is fixture/policy scoped and non-authoritative",
        "status": "implemented",
    },
    {
        "gap_id": "review_causality",
        "problem": "edits overwrite outputs without retaining why or what must be recomputed",
        "floor_contract": "unapplied ReviewPatch with evidence and invalidation hints",
        "status": "implemented",
    },
    {
        "gap_id": "portable_research_object",
        "problem": "evidence, receipts, rights, and annotations travel in incompatible bundles",
        "floor_contract": "Floor crate with JAMS, PROV, ODRL, RO-Crate mappings and checksums",
        "status": "implemented",
    },
    {
        "gap_id": "language_runtime_neutrality",
        "problem": "plugin ecosystems require adoption of one host language or ABI",
        "floor_contract": "stdin/stdout JSON plus artifact directory reference protocol",
        "status": "implemented",
    },
    {
        "gap_id": "catalog_conflicts",
        "problem": "same provider/version can resolve to conflicting implementations",
        "floor_contract": "catalog refuses conflicting manifest identities",
        "status": "implemented",
    },
    {
        "gap_id": "network_declaration",
        "problem": "providers silently reach network services during supposedly local inference",
        "floor_contract": "request/manifest network policy and honest declaration-only host receipt",
        "status": "partial",
        "blocker": "reference host does not yet enforce an operating-system network sandbox",
    },
    {
        "gap_id": "resource_isolation",
        "problem": "plugins can exhaust memory, GPU, disk, or process resources",
        "floor_contract": "runtime/output limits in v1; OS/container resource isolation remains external",
        "status": "partial",
    },
    {
        "gap_id": "remote_attestation",
        "problem": "remote providers may return plausible receipts without proving the executed artifact",
        "floor_contract": "reserved supply-chain signatures and model identities",
        "status": "open",
    },
    {
        "gap_id": "privacy_data_locality",
        "problem": "music evidence may leave a device without an explicit locality policy",
        "floor_contract": "network declaration exists; jurisdiction and locality policy need a normative v2 contract",
        "status": "open",
    },
    {
        "gap_id": "realtime_callback_safety",
        "problem": "general providers are not suitable for audio callback execution",
        "floor_contract": "Floor v1 is control-plane/offline; prepared real-time blocks remain EarCrate live authority",
        "status": "boundary",
    },
    {
        "gap_id": "normative_license",
        "problem": "a noncommercial protocol definition cannot become a universal commercial floor",
        "floor_contract": "separate normative schemas/examples/conformance fixtures under an owner-approved permissive license",
        "status": "owner_decision_required",
    },
)

FLOOR_STANDARDS_MAP: tuple[dict[str, str], ...] = (
    {"surface": "annotations", "standards": "JAMS", "posture": "mapping"},
    {"surface": "feature plugins", "standards": "Vamp", "posture": "adapter"},
    {"surface": "scores", "standards": "MusicXML / MNX", "posture": "interchange"},
    {"surface": "performance and devices", "standards": "MIDI 2.0 / MIDI-CI", "posture": "interchange"},
    {"surface": "DAW/timeline", "standards": "DAWproject / OpenTimelineIO", "posture": "lowering"},
    {"surface": "native DSP", "standards": "CLAP", "posture": "host adapter"},
    {"surface": "model execution", "standards": "ONNX", "posture": "provider runtime"},
    {"surface": "containers and signatures", "standards": "OCI / Sigstore / SLSA", "posture": "supply chain"},
    {"surface": "research object and provenance", "standards": "RO-Crate / W3C PROV", "posture": "mapping"},
    {"surface": "license and policy", "standards": "SPDX / ODRL", "posture": "assertion mapping"},
    {"surface": "music metadata and provenance", "standards": "DDEX / C2PA", "posture": "future adapter"},
    {"surface": "benchmarks", "standards": "mirdata / MIREX", "posture": "fixture/evaluation bridge"},
)


def floor_gap_register() -> dict[str, Any]:
    rows = [deepcopy(row) for row in FLOOR_GAP_REGISTER]
    payload = {
        "schema_version": 1,
        "kind": "earcrate_floor_gap_register",
        "gaps": rows,
        "counts": {
            status: sum(1 for row in rows if row["status"] == status)
            for status in sorted({row["status"] for row in rows})
        },
        "standards": [deepcopy(row) for row in FLOOR_STANDARDS_MAP],
    }
    payload["gap_register_sha256"] = floor_sha256_json(payload)
    return payload


__all__ = ["FLOOR_GAP_REGISTER", "FLOOR_STANDARDS_MAP", "floor_gap_register"]
