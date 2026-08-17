"""Render the A1-07 full-form candidates and emit their evidence package.

Every candidate renders twice from the same sealed score and must land on one
canonical PCM identity, the protected payoff region must survive sample-identical,
and the three candidates must differ from each other. A candidate that fails any
of those is not a weaker candidate — it is not evidence at all.
"""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import shutil
from typing import Any, Mapping, Sequence

from .. import reference_zero as rz
from ..a1_07_gold_v8 import common as c
from ..a1_07_gold_v8 import custody as v7custody
from ..a1_07_gold_v8.review import measure_loudness
from .bindings import index_custody, materialize_from_archive, rebind
from .contract import FullFormError, law
from .peaks import peak_conditions
from .provenance import adapter_tree_digest
from .score import build_full_form_score, clip_duration

ADAPTER_ID = "a1-07-full-form-v1"
ADAPTER_VERSION = "1.0.0"


def _section_digest(pcm: bytes, *, start: int, end: int, channels: int) -> str:
    frame = channels * 4
    return c.sha256_bytes(pcm[start * frame:end * frame])


PROBE_TRIM_DB = -12.0


def solve_headroom(
    arc_score: Mapping[str, Any],
    arc_bindings: Mapping[str, Any],
    contract: Mapping[str, Any],
    workspace: Path,
    *,
    candidate_id: str,
    custody_index: Mapping[str, Path],
    ffmpeg: str,
    ffprobe: str,
) -> dict[str, Any]:
    """Find the exact trim that lands the mix just under full scale.

    A fixed guess would either leave the body clipping or throw away level. One
    probe render at a known trim measures what the sum actually wants; the trim is
    then solved in closed form, so the final render is a single deterministic pass
    and never a search.
    """
    target = float(contract["headroom"]["target_peak_dbfs"])
    child, _ = build_full_form_score(arc_score, contract, candidate_id=candidate_id,
                                     headroom_trim_db=PROBE_TRIM_DB)
    bindings, _ = rebind(arc_bindings, custody_index, score=child)
    probe = workspace / "probe" / candidate_id
    probe.mkdir(parents=True, exist_ok=True)
    audio = probe / "probe.wav"
    rz.render_performance_score(child, bindings, output_path=audio,
                                receipt_path=probe / "probe.receipt.json",
                                verify_source_pcm=False, ffmpeg=ffmpeg, ffprobe=ffprobe)
    rate = int(child["timeline"]["sample_rate"])
    channels = int(child["timeline"]["channels"])
    observed = peak_conditions(audio, sample_rate=rate, channels=channels,
                               ffmpeg=ffmpeg, ffprobe=ffprobe)
    if observed["hard_clipped"]:
        raise FullFormError(
            f"{candidate_id}: the probe clipped even at {PROBE_TRIM_DB} dB; the score is far hotter "
            "than expected and the headroom solve cannot be trusted")
    probe_peak = float(observed["sample_peak_dbfs"])
    # The protected payoff is untrimmed, so it caps how far the mix can be lifted.
    trim = min(0.0, target - (probe_peak - PROBE_TRIM_DB))
    return {
        "probe_trim_db": PROBE_TRIM_DB,
        "probe_sample_peak_dbfs": probe_peak,
        "implied_untrimmed_peak_dbfs": round(probe_peak - PROBE_TRIM_DB, 4),
        "target_peak_dbfs": target,
        "solved_trim_db": round(trim, 4),
    }


def render_candidate(
    arc_score: Mapping[str, Any],
    arc_bindings: Mapping[str, Any],
    contract: Mapping[str, Any],
    destination: Path,
    *,
    candidate_id: str,
    custody_index: Mapping[str, Path],
    ffmpeg: str,
    ffprobe: str,
    headroom: Mapping[str, Any],
) -> dict[str, Any]:
    """Build one candidate, render it twice, and prove the two renders agree."""
    child, facts = build_full_form_score(
        arc_score, contract, candidate_id=candidate_id,
        headroom_trim_db=float(headroom["solved_trim_db"]))
    bindings, moves = rebind(arc_bindings, custody_index, score=child)

    destination.mkdir(parents=True, exist_ok=False)
    c.atomic_write_json(destination / "performance-score.json", child)
    c.atomic_write_json(destination / "source-bindings.private.json", bindings)

    receipts: list[dict[str, Any]] = []
    for suffix in ("a", "b"):
        receipts.append(rz.render_performance_score(
            child,
            bindings,
            output_path=destination / f"render-{suffix}.wav",
            receipt_path=destination / f"render-{suffix}.receipt.json",
            verify_source_pcm=True,
            ffmpeg=ffmpeg,
            ffprobe=ffprobe,
        ))

    pcm_ids = [row["output"]["canonical_pcm_sha256"] for row in receipts]
    if len(set(pcm_ids)) != 1:
        raise FullFormError(
            f"{candidate_id} did not reproduce: {pcm_ids[0]} vs {pcm_ids[1]}")

    rate = int(child["timeline"]["sample_rate"])
    channels = int(child["timeline"]["channels"])
    audio = destination / "render-a.wav"
    pcm = c.decode_s32(audio, sample_rate=rate, channels=channels, ffmpeg=ffmpeg)
    frames = c.frame_count(pcm, channels)

    # Per-section identities: the stage-level evidence a whole-file digest hides.
    stage_digests: dict[str, Any] = {}
    for row in (contract["form"])["sections"]:
        start = int(round(float(row["start_seconds"]) * rate))
        end = min(frames, int(round(float(row["end_seconds"]) * rate)))
        stage_digests[str(row["section_id"])] = {
            "start_sample": start,
            "end_sample": end,
            "duration_seconds": round((end - start) / rate, 4),
            "pcm_sha256": _section_digest(pcm, start=start, end=end, channels=channels),
        }

    lufs, peak = measure_loudness(audio, ffmpeg=ffmpeg)
    conditions = peak_conditions(audio, sample_rate=rate, channels=channels,
                                 ffmpeg=ffmpeg, ffprobe=ffprobe)
    return {
        "candidate_id": candidate_id,
        # No review label here. Labels are permuted per pack under a private nonce,
        # so a label recorded in machine evidence would contradict the pack and
        # decode a verdict to the wrong timing law.
        "label": law(contract, candidate_id)["label"],
        "score_sha256": child["score_sha256"],
        "bindings_sha256": bindings["bindings_sha256"],
        "canonical_pcm_sha256": pcm_ids[0],
        "render_receipt_sha256": [row["receipt_sha256"] for row in receipts],
        "reproduced_identically": True,
        "duration_seconds": round(frames / rate, 4),
        "duration_samples": frames,
        "timing_facts": facts,
        "stage_pcm_identities": stage_digests,
        "signal": {"integrated_lufs": lufs, "peak_dbfs": peak},
        "peak_conditions": conditions,
        "binding_relocations": moves,
        "headroom": dict(headroom),
        "command_vector": [row["command"] for row in receipts],
        "clip_count": int(receipts[0]["clip_count"]),
        "artifacts": {
            "score": str(destination / "performance-score.json"),
            "render_a": str(destination / "render-a.wav"),
            "render_b": str(destination / "render-b.wav"),
        },
        "_pcm": pcm,
    }


def qualify(
    rows: Sequence[Mapping[str, Any]],
    contract: Mapping[str, Any],
    *,
    protected_pcm: bytes,
    channels: int,
    rate: int,
) -> dict[str, Any]:
    """Apply the contract's machine gate to the rendered frontier."""
    gate = contract["machine_gate"]
    form = contract["form"]
    low, high = float(form["minimum_seconds"]), float(form["maximum_seconds"])
    cap = gate["band_tempo_scale_bounds"]
    payoff = next(r for r in form["sections"] if r["section_id"] == "payoff")
    payoff_start = int(round(float(payoff["start_seconds"]) * rate))
    protected_frames = c.frame_count(protected_pcm, channels)

    checks: list[dict[str, Any]] = []
    for row in rows:
        duration_ok = low <= float(row["duration_seconds"]) <= high
        scales = row["timing_facts"]["tempo_scales"]
        cap_ok = all(float(cap[0]) <= float(v) <= float(cap[1]) for v in scales)
        law_id = row["candidate_id"]
        if law_id.endswith("native-pocket"):
            law_ok = scales == [1.0]
        elif law_id.endswith("single-speed"):
            law_ok = len(scales) == 1
        else:
            law_ok = len(scales) <= (row["timing_facts"]["slot_count"] // 4) + 1
        payoff_ok = c.compare_region_offsets(
            row["_pcm"], protected_pcm,
            a_start_frame=payoff_start, b_start_frame=0,
            frames=protected_frames, channels=channels)
        # A render that measured as near-silence would pass every structural check
        # above, so the signal floor is part of qualification, not a footnote.
        lufs = float(row["signal"]["integrated_lufs"])
        audible = lufs >= float(gate["signal_floor_integrated_lufs"])
        checks.append({
            "candidate_id": law_id,
            "duration_within_form_window": duration_ok,
            "reproduced_identically": bool(row["reproduced_identically"]),
            "band_tempo_within_cap": cap_ok,
            "timing_law_honoured": bool(law_ok),
            "payoff_sample_identical_to_gold_v6": bool(payoff_ok),
            "above_signal_floor": bool(audible),
            "integrated_lufs": lufs,
            "raw_true_peak_dbfs": float(row["signal"]["peak_dbfs"]),
            "raw_true_peak_over_ceiling": (
                float(row["signal"]["peak_dbfs"]) > float(gate["review_cut_true_peak_ceiling_dbfs"])),
            # Mastering readiness is a SEPARATE verdict from review admissibility.
            # The peak condition is common to every candidate, so it cannot
            # discriminate between timing laws and must not block the audition;
            # it does block promotion of whichever candidate wins.
            "peak_conditions": row["peak_conditions"],
            "no_hard_clipping": not row["peak_conditions"]["hard_clipped"],
            "qualified": bool(duration_ok and row["reproduced_identically"]
                              and cap_ok and law_ok and payoff_ok and audible
                              and not row["peak_conditions"]["hard_clipped"]),
        })

    identities = [row["canonical_pcm_sha256"] for row in rows]
    distinct = len(set(identities)) == len(identities)
    qualified = [row for row in checks if row["qualified"]]
    return {
        "per_candidate": checks,
        "candidate_pcm_distinct": distinct,
        "qualified_count": len(qualified),
        "frontier_admissible": bool(distinct and len(qualified) >= 2),
        "frontier_rule": "no owner frontier if fewer than two candidates qualify",
        "any_candidate_hard_clipped": any(
            row["peak_conditions"]["hard_clipped"] for row in rows),
        "flat_top_counts": {row["candidate_id"]: row["peak_conditions"]["flat_top_sample_count"]
                            for row in rows},
        "distortion_is_equal_across_candidates": len({
            row["peak_conditions"]["flat_top_sample_count"] for row in rows}) == 1,
        "promotion_rule": "A winning letter advances to owner_frontier_selected only. "
                          "Album-master promotion is a separate decision.",
    }


def build(
    v7_workspace: Path,
    core_archive: Path,
    output: Path,
    contract: Mapping[str, Any],
    repo_root: Path,
    *,
    ffmpeg: str = "ffmpeg",
    ffprobe: str = "ffprobe",
) -> dict[str, Any]:
    """Qualify the bindings, render the frontier, and seal the evidence package."""
    output = output.expanduser().absolute()
    if output.exists():
        raise FullFormError(f"output exists: {output}")

    # 1. The private bindings must qualify before a single sample is rendered.
    v7custody.verify_inputs(v7_workspace, ffmpeg=ffmpeg)
    declared = contract["required_bindings"]["beggin_core_private_store"]["container_sha256"]
    observed = c.sha256_file(core_archive)
    if observed != declared:
        raise FullFormError(f"CORE archive identity mismatch: {observed}")

    arc_root = v7_workspace / "gold-v7-arc" / "authoring" / "derived"
    arc_score = c.load_json(arc_root / "performance-score.json")
    arc_bindings = c.load_json(arc_root / "source-bindings.private.json")

    # The stems the arc consumes live inside the private CORE archive; the renderer
    # needs them as loose files. Materialize exactly those, by identity, next to it.
    identities = {k: v for k, v in contract["source_identities"].items()
                  if isinstance(v, str) and c.HEX64.fullmatch(v)}
    stem_root = core_archive.parent / "core-stems"
    extracted = materialize_from_archive(
        core_archive, stem_root,
        {k: v for k, v in identities.items() if k != "gold_v6_reviewed_compound"})
    custody_index = index_custody([v7_workspace, stem_root])

    rate = int(arc_score["timeline"]["sample_rate"])
    channels = int(arc_score["timeline"]["channels"])
    protected = v7_workspace / "incumbent" / "gold-v6.wav"
    protected_pcm = c.decode_s32(protected, sample_rate=rate, channels=channels, ffmpeg=ffmpeg)

    output.mkdir(parents=True)

    # One trim for the WHOLE frontier, not one per candidate. A per-candidate trim
    # would make the options differ in level as well as in timing law, and the
    # louder option wins listening comparisons for reasons that are not musical.
    solves = {
        str(row["candidate_id"]): solve_headroom(
            arc_score, arc_bindings, contract, output,
            candidate_id=str(row["candidate_id"]), custody_index=custody_index,
            ffmpeg=ffmpeg, ffprobe=ffprobe)
        for row in contract["timing_laws"]
    }
    shared_trim = min(float(row["solved_trim_db"]) for row in solves.values())
    headroom = {
        "policy": "one trim shared by every candidate, taken from the hottest",
        "target_peak_dbfs": float(contract["headroom"]["target_peak_dbfs"]),
        "solved_trim_db": shared_trim,
        "applied_to": "all non-protected elements",
        "protected_payoff_untrimmed": True,
        "per_candidate_solve": solves,
    }

    rows: list[dict[str, Any]] = []
    for row in contract["timing_laws"]:
        candidate_id = str(row["candidate_id"])
        rows.append(render_candidate(
            arc_score, arc_bindings, contract,
            output / "candidates" / candidate_id,
            candidate_id=candidate_id,
            custody_index=custody_index,
            ffmpeg=ffmpeg, ffprobe=ffprobe,
            headroom=headroom,
        ))
    shutil.rmtree(output / "probe", ignore_errors=True)

    gate = qualify(rows, contract, protected_pcm=protected_pcm, channels=channels, rate=rate)
    public_rows = [{k: v for k, v in row.items() if k != "_pcm"} for row in rows]

    first_receipt = c.load_json(
        output / "candidates" / str(contract["timing_laws"][0]["candidate_id"]) / "render-a.receipt.json")
    renderer_identity = {
        "ffmpeg_version": first_receipt.get("ffmpeg_version"),
        "ffprobe": first_receipt.get("ffprobe"),
    }

    manifest = {
        "kind": "earcrate_a1_07_full_form_adapter_manifest",
        "schema_version": 1,
        "adapter_id": ADAPTER_ID,
        "adapter_version": ADAPTER_VERSION,
        "track_id": "A1-07",
        "descent_id": contract["descent_id"],
        "contract_sha256": contract["contract_sha256"],
        "earcrate_git_head": c.current_git_head(repo_root),
        # The head is context; this digest is the predicate. See provenance.py.
        "adapter_tree": adapter_tree_digest(repo_root),
        "renderer_identity": renderer_identity,
        "timeline": {"sample_rate": rate, "channels": channels},
        "form": contract["form"],
        "phrase_map_sha256": c.sha256_bytes(c.canonical_json_bytes(contract["phrase_map"])),
        "bindings": {
            "a1_07_gold_v7_workspace": {
                "artifact_path": str(v7_workspace),
                "qualified_by": "earcrate.a1_07_gold_v8.custody.verify_inputs",
            },
            "beggin_core_private_store": {
                "artifact_path": str(core_archive),
                "container_sha256": observed,
                "materialized_sources": extracted,
            },
        },
        "parent": contract["parent"],
        "headroom": headroom,
        "candidates": public_rows,
        "machine_gate": gate,
        "authority": {
            "human_acceptance": False,
            "musical_acceptance": False,
            "machine_qualification_only": True,
            "note": "A technically valid full-form invocation may raise the music-producing and "
                    "realization-ready counters. It does not make an accepted album master.",
        },
    }
    manifest = c.seal(manifest, "manifest_sha256")
    c.atomic_write_json(output / "ADAPTER_MANIFEST.json", manifest)

    # The public projection carries mechanism, never media, paths or credentials.
    public = c.seal({
        "kind": "earcrate_a1_07_full_form_public_projection",
        "schema_version": 1,
        "adapter_id": ADAPTER_ID,
        "adapter_version": ADAPTER_VERSION,
        "descent_id": contract["descent_id"],
        "contract_sha256": contract["contract_sha256"],
        "manifest_sha256": manifest["manifest_sha256"],
        "form_seconds": contract["form"]["declared_total_seconds"],
        "candidates": [
            {
                "label": row["label"],
                "canonical_pcm_sha256": row["canonical_pcm_sha256"],
                "duration_seconds": row["duration_seconds"],
                "tempo_scales": row["timing_facts"]["tempo_scales"],
                "phase_resets": len(row["timing_facts"]["phase_resets"]),
                "integrated_lufs": row["signal"]["integrated_lufs"],
                "sample_peak_dbfs": row["peak_conditions"]["sample_peak_dbfs"],
                "oversampled_true_peak_dbtp": row["peak_conditions"]["oversampled_true_peak_dbtp"],
                "flat_top_sample_count": row["peak_conditions"]["flat_top_sample_count"],
                "hard_clipped": row["peak_conditions"]["hard_clipped"],
            }
            for row in public_rows
        ],
        "machine_gate": gate,
        "private_paths_included": False,
        "source_audio_exported": False,
    }, "projection_sha256")
    c.atomic_write_json(output / "PUBLIC_PROJECTION.json", public)

    # The owner pack exists only if the frontier is admissible. Building it anyway
    # would put an unqualified frontier in front of the owner.
    pack: dict[str, Any] | None = None
    if gate["frontier_admissible"]:
        from .review import write_review_pack
        pack = write_review_pack(
            output, contract,
            [row for row in public_rows
             if next(x["qualified"] for x in gate["per_candidate"]
                     if x["candidate_id"] == row["candidate_id"])],
            v7_workspace / "gold-v7-arc" / "machine" / "qualified.wav",
            ffmpeg=ffmpeg,
        )

    return {
        "workspace": str(output),
        "manifest_sha256": manifest["manifest_sha256"],
        "projection_sha256": public["projection_sha256"],
        "machine_gate": gate,
        "candidates": public_rows,
        "review_pack": pack,
    }
