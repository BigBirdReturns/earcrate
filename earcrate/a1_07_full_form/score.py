"""Build the A1-07 full-form performance score and apply a donor-band timing law.

Two things separate this from the gold-v9 core diagnostic.

First, the form. Gold-v9 re-timed a 18.95 s core; this extends the qualified
gold-v7 arc with an explicitly mapped body so the result reaches the 45-120 s
window where a groove can actually be judged.

Second, the grid. Gold-v9's `derive_slots` demands that every donor track occupy
an identical slot set, which is exactly what the retained positive arc violates:
harmonic material enters first, then bass, then drums. Holding that progressive
entry is the point of the arc, so the law here maps a shared slot GRID and each
track follows whichever slots it occupies.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping, Sequence

from .. import reference_zero as rz
from ..a1_07_gold_v8 import common as c
from .contract import FullFormError, law, section

PROTECTED_SOURCE = "gold_v6_reviewed_compound"
FRANKIE_SOURCE = "four_seasons_vocals"
BAND_SOURCES = ("maneskin_bass", "maneskin_drums", "maneskin_other")


def transformed_duration(source_start: int, source_end: int, scale: float) -> int:
    if scale <= 0:
        raise FullFormError("tempo scale must be positive")
    return max(1, round((source_end - source_start) / scale))


def clip_duration(clip: Mapping[str, Any], scale: float | None = None) -> int:
    tempo = float(clip.get("tempo_scale", 1.0)) if scale is None else float(scale)
    return transformed_duration(int(clip["source_start_sample"]), int(clip["source_end_sample"]), tempo)


def partition(score: Mapping[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """Split the arc into its Frankie, donor-band and protected-payoff tracks."""
    groups: dict[str, list[dict[str, Any]]] = {"frankie": [], "band": [], "protected": []}
    for track in score.get("tracks") or []:
        row = deepcopy(dict(track))
        sources = {str(clip.get("source_id")) for clip in row.get("clips") or []}
        if not sources:
            raise FullFormError(f"track has no clips: {row.get('track_id')}")
        if sources == {PROTECTED_SOURCE}:
            groups["protected"].append(row)
        elif sources == {FRANKIE_SOURCE}:
            groups["frankie"].append(row)
        elif sources <= set(BAND_SOURCES):
            groups["band"].append(row)
        else:
            raise FullFormError(f"track mixes source families: {row.get('track_id')} {sorted(sources)}")
    for key in groups:
        if not groups[key]:
            raise FullFormError(f"the parent arc has no {key} track")
    return groups


def band_grid(band_tracks: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """The shared slot grid, tolerant of progressive entry.

    A slot is one target position on the donor-band timeline. Tracks may occupy
    different subsets of it, but every track present at a slot must agree on the
    source window, or the slot does not describe one musical bar.
    """
    slots: dict[int, dict[str, Any]] = {}
    for track in band_tracks:
        for clip in track.get("clips") or []:
            target = int(clip["target_start_sample"])
            window = (int(clip["source_start_sample"]), int(clip["source_end_sample"]))
            entry = slots.setdefault(target, {"target": target, "window": window, "occupants": []})
            if entry["window"] != window:
                raise FullFormError(
                    f"donor tracks disagree on the source window at slot {target}: "
                    f"{entry['window']} vs {window}")
            entry["occupants"].append(str(track["track_id"]))
    ordered = [slots[key] for key in sorted(slots)]
    if len(ordered) < 2:
        raise FullFormError("at least two donor-band slots are required")

    # Preserve the parent's inter-slot overlap: the arc crossfades its bars by ~40 ms,
    # and a law that only rescaled durations would silently discard those joins.
    for index, entry in enumerate(ordered):
        start, end = entry["window"]
        entry["source_duration"] = end - start
        entry["parent_duration"] = transformed_duration(start, end, 1.0)
        if index + 1 < len(ordered):
            gap = ordered[index + 1]["target"] - entry["target"]
            entry["advance_gap"] = gap
        else:
            entry["advance_gap"] = None
    return ordered


def schedule(
    grid: Sequence[Mapping[str, Any]],
    candidate_id: str,
    *,
    phrase_slots: int = 4,
    bounds: tuple[float, float] = (0.92, 1.08),
) -> tuple[dict[int, tuple[int, float]], dict[str, Any]]:
    """Map each grid slot to its new target and tempo scale under one timing law.

    `bounds` is the duration-preservation cap. Uncapped fitting is what produced
    the rejected 'instrumental dragged down to the vocal' signature, so a law that
    cannot fit its span inside the cap takes the cap and leaves the residue as
    drift rather than stretching the band to hit a mark.
    """
    low, high = float(bounds[0]), float(bounds[1])

    def capped(value: float) -> tuple[float, bool]:
        clamped = min(high, max(low, value))
        return clamped, abs(clamped - value) > 1e-12

    source_total = sum(int(row["source_duration"]) for row in grid)
    span = int(grid[-1]["target"]) + int(grid[-1]["parent_duration"]) - int(grid[0]["target"])
    if span <= 0:
        raise FullFormError("invalid donor-band span")

    # `cues` re-anchors a slot to a fixed target instead of continuing the reflow.
    cues: dict[int, int] = {}
    clamped_slots: list[int] = []

    if candidate_id == "full-form-v1-single-speed":
        value, was_capped = capped(source_total / span)
        scales = [value] * len(grid)
        if was_capped:
            clamped_slots = list(range(len(grid)))
        facts: dict[str, Any] = {"law": "single-speed", "phase_resets": []}
    elif candidate_id == "full-form-v1-native-pocket":
        scales = [1.0] * len(grid)
        facts = {"law": "native-pocket", "phase_resets": []}
    elif candidate_id == "full-form-v1-phrase-reset":
        scales = [0.0] * len(grid)
        resets: list[int] = []
        for start in range(0, len(grid), phrase_slots):
            phrase = list(range(start, min(start + phrase_slots, len(grid))))
            phrase_source = sum(int(grid[i]["source_duration"]) for i in phrase)
            last = phrase[-1]
            phrase_span = (int(grid[last]["target"]) + int(grid[last]["parent_duration"])
                           - int(grid[phrase[0]]["target"]))
            if phrase_span <= 0:
                raise FullFormError("invalid phrase span")
            value, was_capped = capped(phrase_source / phrase_span)
            for i in phrase:
                scales[i] = value
                if was_capped:
                    clamped_slots.append(i)
            # The defining behaviour of this law: every phrase re-cues to the parent's
            # own anchor, so a capped phrase leaks no drift into the next one.
            cues[phrase[0]] = int(grid[phrase[0]]["target"])
            if start:
                resets.append(int(grid[phrase[0]]["target"]))
        facts = {"law": "phrase-reset", "phrase_slots": phrase_slots, "phase_resets": resets}
    else:
        raise FullFormError(f"unsupported timing law: {candidate_id}")

    plan: dict[int, tuple[int, float]] = {}
    target = int(grid[0]["target"])
    for index, row in enumerate(grid):
        if index in cues:
            target = cues[index]
        scale = float(scales[index])
        plan[int(row["target"])] = (int(target), scale)
        duration = transformed_duration(row["window"][0], row["window"][1], scale)
        gap = row["advance_gap"]
        if gap is None:
            target += duration
        else:
            # advance by the parent's own gap, rescaled by how much this slot changed
            overlap = int(row["parent_duration"]) - int(gap)
            target += max(1, duration - overlap)
    facts.update({
        "tempo_scales": sorted({round(value, 12) for value in scales}),
        "slot_count": len(grid),
        "band_start_sample": int(grid[0]["target"]),
        "band_end_sample": int(target),
        "duration_preservation_cap": [low, high],
        "capped_slot_count": len(set(clamped_slots)),
        "phase_cue_count": len(cues),
    })
    return plan, facts


def build_full_form_score(
    arc_score: Mapping[str, Any],
    contract: Mapping[str, Any],
    *,
    candidate_id: str,
    headroom_trim_db: float = 0.0,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Assemble setup + authored body + protected payoff under one timing law."""
    rz.validate_performance_score(arc_score)
    if arc_score["score_sha256"] != (contract.get("parent") or {}).get("arc_score_sha256"):
        raise FullFormError("parent arc score identity does not match the contract")

    rate = int(arc_score["timeline"]["sample_rate"])
    if rate != int((contract.get("timeline") or {}).get("sample_rate", rate)):
        raise FullFormError("contract and parent disagree on the timeline sample rate")

    def samples(seconds: float) -> int:
        return int(round(float(seconds) * rate))

    groups = partition(arc_score)
    phrase_map = contract["phrase_map"]
    body = section(contract, "body")
    payoff = section(contract, "payoff")
    grid_spec = phrase_map["band_grid"]
    gains = phrase_map["band_gain_arc"]["body"]

    child = deepcopy(dict(arc_score))
    child.pop("score_sha256", None)

    # --- 1. the authored body vocal --------------------------------------------
    body_phrase = next(row for row in phrase_map["vocal_phrases"]
                       if row["section_id"] == "body")
    if float(body_phrase.get("tempo_scale", 1.0)) != 1.0:
        raise FullFormError("the body vocal must not be time stretched")
    window = body_phrase["source_window_seconds"]
    body_clip = {
        "clip_id": "full-form-body-frankie",
        "source_id": FRANKIE_SOURCE,
        "source_start_sample": samples(window[0]),
        "source_end_sample": samples(window[1]),
        "target_start_sample": samples(body_phrase["destination_anchor_seconds"]),
        "tempo_scale": 1.0,
        "pitch_semitones": 0.0,
        "gain_db": float(body_phrase["gain_db"]),
        "pan": 0.0,
        "fade_in_samples": samples(0.02),
        "fade_out_samples": samples(0.25),
        "musical_function": "frankie_development_phrase_between_build_and_protected_payoff",
        "occurrence_id": "full_form_body",
        "locked": True,
    }

    # --- 2. the authored body band slots ---------------------------------------
    slot_samples = samples(float(grid_spec["slot_seconds"]))
    overlap = samples(0.04)
    body_source = samples(float(grid_spec["body_source_start_seconds"]))
    body_target = samples(float(body["start_seconds"]))
    body_slots = int(grid_spec["body_slots"])
    body_by_source: dict[str, list[dict[str, Any]]] = {name: [] for name in BAND_SOURCES}
    for index in range(body_slots):
        start = body_source + index * slot_samples
        end = start + slot_samples + overlap
        target = body_target + index * (slot_samples - overlap)
        for name in BAND_SOURCES:
            body_by_source[name].append({
                "clip_id": f"full-form-body-{name}-{index:02d}",
                "source_id": name,
                "source_start_sample": start,
                "source_end_sample": end,
                "target_start_sample": target,
                "tempo_scale": 1.0,
                "pitch_semitones": 2.8 if name != "maneskin_drums" else 0.0,
                "gain_db": float(gains[name]),
                "pan": 0.0,
                "fade_in_samples": overlap,
                "fade_out_samples": overlap,
                "musical_function": "stable_donor_pocket_under_frankie_development",
                "occurrence_id": "full_form_body",
                "locked": True,
            })

    # --- 3. graft the new clips onto their tracks ------------------------------
    frankie_ids = {str(t["track_id"]) for t in groups["frankie"]}
    protected_ids = {str(t["track_id"]) for t in groups["protected"]}
    for track in child["tracks"]:
        track_id = str(track["track_id"])
        if track_id in frankie_ids:
            track["clips"] = list(track.get("clips") or []) + [body_clip]
        elif track_id in protected_ids:
            for clip in track.get("clips") or []:
                clip["target_start_sample"] = samples(float(payoff["start_seconds"]))
        else:
            names = {str(clip["source_id"]) for clip in track.get("clips") or []}
            if len(names) != 1:
                raise FullFormError(f"donor track {track_id} carries mixed sources")
            track["clips"] = list(track.get("clips") or []) + body_by_source[names.pop()]

    # --- 4. apply the timing law to the whole donor-band grid ------------------
    band_ids = {str(t["track_id"]) for t in groups["band"]}
    band_tracks = [t for t in child["tracks"] if str(t["track_id"]) in band_ids]
    grid = band_grid(band_tracks)
    cap = contract["machine_gate"]["band_tempo_scale_bounds"]
    plan, facts = schedule(grid, candidate_id, bounds=(float(cap[0]), float(cap[1])))
    for track in band_tracks:
        for clip in track.get("clips") or []:
            original = int(clip["target_start_sample"])
            if original not in plan:
                raise FullFormError(f"band clip target absent from the grid: {original}")
            new_target, new_scale = plan[original]
            clip["target_start_sample"] = int(new_target)
            clip["tempo_scale"] = float(new_scale)

    # --- 4b. headroom -----------------------------------------------------------
    # Summing four elements at the arc's setup levels drives the body past full
    # scale, and the clipping that follows differs per candidate, which would
    # confound the very comparison this frontier exists to make. The trim is
    # applied ONLY to non-protected elements, so the protected payoff clip stays
    # at its original gain and remains sample-identical to gold-v6.
    if headroom_trim_db:
        for track in child["tracks"]:
            if str(track["track_id"]) in protected_ids:
                continue
            for clip in track.get("clips") or []:
                clip["gain_db"] = round(float(clip.get("gain_db", 0.0)) + float(headroom_trim_db), 6)

    # --- 5. close the score ----------------------------------------------------
    total = samples(float((contract["form"])["declared_total_seconds"]))
    ends = [int(clip["target_start_sample"]) + clip_duration(clip)
            for track in child["tracks"] for clip in track.get("clips") or []]
    child["timeline"]["duration_samples"] = max(total, max(ends))
    child["score_id"] = f"album-one-a1-07-{candidate_id}"
    child["title"] = f"A1-07 full-form v1 - {law(contract, candidate_id)['label']}"

    authority = dict(child.get("authority") or {})
    authority.update({
        "parent_score_sha256": arc_score["score_sha256"],
        "descent_id": contract["descent_id"],
        "timing_law": candidate_id,
        "headroom_trim_db": float(headroom_trim_db),
        "headroom_applied_to_protected_payoff": False,
        "musical_acceptance": False,
        "renderer_invented_decisions": False,
        "frankie_timing_changed": False,
        "status": "full_form_candidate",
    })
    child["authority"] = authority

    history = [deepcopy(dict(row)) for row in child.get("command_history") or []]
    sequence = max((int(row.get("sequence") or 0) for row in history), default=0) + 1
    history.append({
        "sequence": sequence,
        "command_id": f"{candidate_id}-full-form",
        "actor": "earcrate:a1-07-full-form-v1",
        "operation": "extend_form_and_apply_band_timing_law",
        "target": "donor-band and authored body",
        "parameters_sha256": c.sha256_bytes(c.canonical_json_bytes({
            "candidate_id": candidate_id,
            "contract_sha256": contract["contract_sha256"],
            "headroom_trim_db": float(headroom_trim_db),
            "schedule": {str(slot): {"target_start_sample": target, "tempo_scale": scale}
                         for slot, (target, scale) in sorted(plan.items())},
        })),
    })
    child["command_history"] = history

    child = rz.seal(child)
    rz.validate_performance_score(child)
    facts["timeline_duration_samples"] = int(child["timeline"]["duration_samples"])
    facts["timeline_duration_seconds"] = round(child["timeline"]["duration_samples"] / rate, 4)
    facts["payoff_start_sample"] = samples(float(payoff["start_seconds"]))
    return child, facts
