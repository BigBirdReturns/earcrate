from earcrate.core.deps import *
from earcrate.core.deps import _dt
from earcrate.deck.lattice import *
GT_SECONDS_PER_EVENT = 11.0     # new sample-event cadence
GT_MIN_LAYERS = 2               # simultaneous recognizable elements
GT_MAX_LAYERS = 4
GT_SOURCES_PER_MINUTE = 5.5     # distinct source songs introduced per minute


def girl_talk_targets(track_seconds: float) -> Dict[str, int]:
    """What a Girl Talk-density track of this length actually requires."""
    t = max(15.0, float(track_seconds or 120.0))
    return {
        "sample_events": max(4, round(t / GT_SECONDS_PER_EVENT)),
        "distinct_sources": max(4, round((t / 60.0) * GT_SOURCES_PER_MINUTE)),
        "min_layers": GT_MIN_LAYERS,
        "max_layers": GT_MAX_LAYERS,
    }


def crate_readiness_audit(pool: List[Dict[str, Any]], target_bpm: Optional[float], target_key: Optional[int],
                          user_stretch_budget: float, residual_pitch_budget: float,
                          track_seconds: float = 120.0) -> Dict[str, Any]:
    """Render-free readiness, grounded in Girl Talk sample density.

    Instead of arbitrary role minimums, this answers the real question: can this
    pool sustain a track of length T at Girl Talk density (a new element every
    ~11s, 2-4 layers, rotating sources)? It names the true bottleneck — which for
    random full-mix songs is almost always clean drum beds and isolatable vocals,
    the two roles Girl Talk hand-sourced and that stems recover.
    """
    lat = score_bpm_lattice(pool, target_bpm, target_key, user_stretch_budget, residual_pitch_budget)
    chosen = float((target_bpm if target_bpm else lat["best_bpm"]) or 120.0)
    tgt = girl_talk_targets(track_seconds)
    roles = {"drum_anchor": 0, "bass": 0, "vocal": 0, "harmony": 0, "texture": 0, "fx": 0, "full": 0}
    tiers = {"native": 0, "varispeed": 0, "varispeed_residual_pitch": 0, "reject": 0}
    bpm_window = {"within_2_5pct": 0, "within_5pct": 0, "within_8pct": 0, "beyond": 0}
    per_source: Dict[str, int] = {}
    usable_sources: set = set()
    usable_total = 0
    for x in pool:
        role = str(x.get("role") or "full")
        # Count usability the way a deck works: varispeed to tempo, let pitch follow
        # (target_key=None). The arranger's key-era router places each loop near its
        # native key, so scoring every loop against ONE forced key understates the pool.
        plan = plan_varispeed_transform(role, float(x.get("bpm") or chosen), chosen,
                                        x.get("key_root"), None,
                                        user_stretch_budget, residual_pitch_budget)
        src = str(x.get("title") or x.get("path") or "unknown")
        per_source[src] = per_source.get(src, 0) + 1
        if plan.get("violation"):
            tiers["reject"] += 1
        else:
            tiers[plan["transform_mode"]] = tiers.get(plan["transform_mode"], 0) + 1
            if role in roles:
                roles[role] += 1
            usable_total += 1
            usable_sources.add(src)
        vpct = float(plan.get("varispeed_pct") or 0.0)
        if vpct <= 2.5:
            bpm_window["within_2_5pct"] += 1
        elif vpct <= 5.0:
            bpm_window["within_5pct"] += 1
        elif vpct <= 8.0:
            bpm_window["within_8pct"] += 1
        else:
            bpm_window["beyond"] += 1

    # Capacity: a bed rider (drum_anchor or full) + a foreground (vocal/harmony) must
    # coexist to sustain the 2-4 layer texture. beds and foregrounds are the axes.
    beds = roles["drum_anchor"] + roles["full"]
    foreground = roles["vocal"] + roles["harmony"]
    # How many distinct sample-events can this pool actually supply without over-reusing
    # any one loop more than ~twice (Girl Talk rarely repeats a foreground within a track).
    event_capacity = min(usable_total * 2, len(usable_sources) * 3 + roles["full"] * 2)
    source_capacity = len(usable_sources)

    dominance = sorted(per_source.items(), key=lambda kv: kv[1], reverse=True)[:5]
    dominance_pct = [{"source": s, "loops": n, "share": round(n / max(1, len(pool)), 3)} for s, n in dominance]

    warnings: List[str] = []
    # Grounded checks, each with its basis stated for the user.
    if event_capacity < tgt["sample_events"]:
        warnings.append(f"Pool supplies ~{event_capacity} sample-events; a {int(track_seconds)}s Girl Talk-density track wants ~{tgt['sample_events']} (a new element every ~{int(GT_SECONDS_PER_EVENT)}s). Add loops or shorten the track.")
    if source_capacity < tgt["distinct_sources"]:
        warnings.append(f"Only {source_capacity} distinct source songs are deck-safe here; Girl Talk pulls ~{tgt['distinct_sources']} for this length. Output will sound repetitive.")
    if beds < 1:
        warnings.append("No clean drum/bed rider at this speed. This is the #1 bottleneck for random full-mix songs — stems recover drum beds. Turn on stem separation.")
    if foreground < 1:
        warnings.append("No isolatable vocal/harmony foreground \u2265 0.65 likelihood. The other classic full-mix bottleneck — stems recover vocals.")
    if roles["bass"] < 1 and roles["full"] < 2:
        warnings.append("No dedicated bass owner and few full-mix loops to carry low end; expect a thin bottom.")
    if dominance_pct and dominance_pct[0]["share"] > 0.20:
        # Girl Talk foreground reuse within a track is low (~15-20% ceiling per source).
        warnings.append(f"Source concentration: '{dominance_pct[0]['source']}' is {int(dominance_pct[0]['share']*100)}% of the pool (Girl Talk keeps any one source under ~20% within a track).")

    ready = (event_capacity >= tgt["sample_events"] and source_capacity >= max(4, tgt["distinct_sources"] // 2)
             and beds >= 1 and foreground >= 1)
    return {
        "chosen_bpm": round(chosen, 2),
        "pool_size": len(pool),
        "ready": ready,
        "track_seconds": int(track_seconds),
        "girl_talk_targets": tgt,
        "capacity": {"sample_events": event_capacity, "distinct_sources": source_capacity,
                     "beds": beds, "foreground": foreground, "usable_loops": usable_total},
        "usable_by_role": roles,
        "transform_tiers": tiers,
        "native_bpm_window": bpm_window,
        "source_dominance": dominance_pct,
        "warnings": warnings,
        "bpm_lattice": lat["lattice"][:8],
        "recommended_bpm": lat["best_bpm"],
    }


