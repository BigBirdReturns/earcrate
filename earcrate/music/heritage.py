from __future__ import annotations

"""Executable harvest map for EarCrate/Jukebreaker's surviving organs.

The point of the map is architectural: no useful historical mechanism may be
silently reimplemented beside the new musical authority, and no retired rescue
path may sneak back as a fallback.
"""

from typing import Any, Mapping, Sequence

from earcrate.music.model import MusicError, music_sha256_json

MUSIC_BUFFALO_HARVEST: tuple[dict[str, Any], ...] = (
    {
        "organ": "immutable_project_score_and_receipts",
        "sources": ["earcrate.project", "runtime ledger", "manifest safety"],
        "destination": "causal score authority and proof lineage",
        "disposition": "preserve",
        "reason": "selected musical causes and every later lowering need content-addressed identity",
    },
    {
        "organ": "beat_state_and_role_activity",
        "sources": ["earcrate.analyze.beat_features", "earcrate.analyze.features"],
        "destination": "source-evidence fields consumed by player-piano laws and equations",
        "disposition": "adapt",
        "reason": "per-beat kick, bass, vocal, lead, groove, local harmony, and novelty are sensory evidence, not composition",
    },
    {
        "organ": "material_regions_and_ear_atoms",
        "sources": ["earcrate.materials.regions", "ear crate atom ledger"],
        "destination": "typed playable-cause candidates with provenance",
        "disposition": "adapt",
        "reason": "independent region boundaries and role probabilities remain the correct source vocabulary",
    },
    {
        "organ": "varispeed_transform_lattice",
        "sources": ["earcrate.deck.transform", "earcrate.deck.lattice"],
        "destination": "embodiment feasibility law and instrument realization search",
        "disposition": "preserve",
        "reason": "tempo/key feasibility must constrain composition before a cause is committed",
    },
    {
        "organ": "typed_dj_transitions",
        "sources": ["earcrate.plan.transitions", "earcrate.live.operators"],
        "destination": "formal operators in player-piano program graphs",
        "disposition": "adapt",
        "reason": "transitions are compositional operators with preconditions, obligations, and lowering contracts",
    },
    {
        "organ": "tastespec_profiles",
        "sources": ["profiles/*.json", "earcrate.tastespec"],
        "destination": "player-piano topology, law parameters, and lexicographic objective stages",
        "disposition": "adapt",
        "reason": "taste is a policy for arranging laws and equations, not one scalar score",
    },
    {
        "organ": "reference_answer_keys_and_recall",
        "sources": ["earcrate.study.reference", "sample lineage datasets"],
        "destination": "empirical calibration, causal alignment priors, and reconstruction benchmarks",
        "disposition": "preserve",
        "reason": "the masters' actual pairings are answer keys for discovery and source identity",
    },
    {
        "organ": "reference_evidence_bundle",
        "sources": ["earcrate.study.reference_bundle", "accepted grid revisions", "note/drum observations"],
        "destination": "audio-to-causal-score compiler input",
        "disposition": "preserve",
        "reason": "providers measure; accepted evidence and quantization receipts remain upstream authority",
    },
    {
        "organ": "exact_midi_ledger_and_execution",
        "sources": ["earcrate.midi", "execution ledgers"],
        "destination": "one lowering backend for proof-carrying causal scores",
        "disposition": "preserve",
        "reason": "MIDI must execute exactly but may not become the musical authority",
    },
    {
        "organ": "sealed_multizone_racks",
        "sources": ["earcrate.rack", "SFZ", "approved crate substitution"],
        "destination": "playable acoustic basis and orchestral inverse-rendering backend",
        "disposition": "preserve",
        "reason": "the same causal score must be realizable through different instrument dictionaries",
    },
    {
        "organ": "arrangement_anatomy",
        "sources": ["earcrate.midi.anatomy"],
        "destination": "derived form, motif, register, density, and orchestration evidence",
        "disposition": "preserve",
        "reason": "anatomy is useful evidence but cannot add or rewrite musical events",
    },
    {
        "organ": "one_bar_pattern_arranger",
        "sources": ["earcrate.midi.arranger"],
        "destination": "source-pattern provider subordinate to the player-piano constitution",
        "disposition": "demote",
        "reason": "fixed form plus energy/role scoring produced mechanically valid but generic and cute arrangements",
    },
    {
        "organ": "live_receding_horizon_runtime",
        "sources": ["earcrate.live"],
        "destination": "real-time executor for precompiled player-piano programs and sealed causes",
        "disposition": "preserve",
        "reason": "phrase-safe controls, callback purity, and exact execution are the correct runtime boundary",
    },
    {
        "organ": "audio_judge_and_residual_analysis",
        "sources": ["earcrate.judge.audio", "render gates"],
        "destination": "analyze-by-reconstruction residual router and perceptual critic",
        "disposition": "adapt",
        "reason": "broad energy similarity is evidence, not proof of musical identity",
    },
    {
        "organ": "floor_safe_rescue",
        "sources": ["v0.5.10 rescue path"],
        "destination": "none",
        "disposition": "retire",
        "reason": "a fallback render contradicts no-fallback composition by construction",
    },
    {
        "organ": "two_world_album_collision_alias",
        "sources": ["v0.5.6 two-world continuum"],
        "destination": "explicit player-piano programs when materially distinct",
        "disposition": "retire",
        "reason": "aliased modes are labels, not independent musical constitutions",
    },
    {
        "organ": "coarse_waveform_success_proxy",
        "sources": ["bar-energy correlation", "waveform envelope similarity"],
        "destination": "non-authoritative diagnostic term",
        "disposition": "demote",
        "reason": "the rejected phat-beats MIDI proved that coarse similarity can reward the wrong composition",
    },
    {
        "organ": "integration_snapshot_scaffold",
        "sources": ["draft PR 30 workflow-only branch"],
        "destination": "none",
        "disposition": "retire",
        "reason": "the stacked implementation branches now provide the real integration path",
    },
)

MUSIC_BUFFALO_DISPOSITIONS = {"preserve", "adapt", "demote", "retire"}


def music_validate_buffalo_harvest(rows: Sequence[Mapping[str, Any]] = MUSIC_BUFFALO_HARVEST) -> None:
    organs: set[str] = set()
    for index, row in enumerate(rows):
        organ = str(row.get("organ") or "")
        if not organ or organ in organs:
            raise MusicError(f"buffalo harvest organ at index {index} is empty or duplicate: {organ!r}")
        organs.add(organ)
        disposition = str(row.get("disposition") or "")
        if disposition not in MUSIC_BUFFALO_DISPOSITIONS:
            raise MusicError(f"buffalo harvest {organ} has invalid disposition {disposition!r}")
        sources = row.get("sources")
        if not isinstance(sources, list) or not sources or not all(str(value) for value in sources):
            raise MusicError(f"buffalo harvest {organ} requires historical sources")
        if not str(row.get("reason") or ""):
            raise MusicError(f"buffalo harvest {organ} requires a reason")
        destination = str(row.get("destination") or "")
        if disposition != "retire" and not destination:
            raise MusicError(f"buffalo harvest {organ} requires a destination")


def music_buffalo_harvest_manifest() -> dict[str, Any]:
    music_validate_buffalo_harvest()
    rows = [dict(row) for row in MUSIC_BUFFALO_HARVEST]
    counts = {value: sum(1 for row in rows if row["disposition"] == value) for value in sorted(MUSIC_BUFFALO_DISPOSITIONS)}
    payload = {
        "kind": "earcrate_buffalo_harvest",
        "schema_version": 1,
        "organs": rows,
        "counts": counts,
    }
    payload["harvest_sha256"] = music_sha256_json(payload)
    return payload
