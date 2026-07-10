from earcrate.core.deps import *
from earcrate.core.deps import _dt
from earcrate.deck.transform import *
from earcrate.deck.transform import _artifact_cost
def build_bpm_lattice(pool: List[Dict[str, Any]], target_bpm: Optional[float], steps_pct: Tuple[float, ...] = (0.0, 2.5, 5.0)) -> List[float]:
    """Candidate deck positions to score, instead of one rigid choke point.

    Combines (a) native BPM clusters actually present in the approved pool and
    (b) a symmetric lattice around any user target. This is the set a DJ would
    consider when deciding what speed to run the set at.
    """
    cands: set = set()
    native = sorted({round(float(x.get("bpm") or 0), 2) for x in pool if 60.0 <= float(x.get("bpm") or 0) <= 190.0})
    # Cluster native BPMs to within ~1.5% so we do not score 40 near-identical nodes.
    clustered: List[float] = []
    for b in native:
        if not any(abs(b - c) / c < 0.015 for c in clustered):
            clustered.append(b)
    for b in clustered:
        cands.add(round(b, 2))
    if target_bpm and target_bpm > 0:
        for s in steps_pct:
            cands.add(round(target_bpm * (1.0 + s / 100.0), 2))
            cands.add(round(target_bpm * (1.0 - s / 100.0), 2))
    if not cands:
        cands.add(round(float(target_bpm or 120.0), 2))
    return sorted(b for b in cands if 70.0 <= b <= 180.0) or [round(float(target_bpm or 120.0), 2)]


def score_bpm_lattice(pool: List[Dict[str, Any]], target_bpm: Optional[float], target_key: Optional[int],
                      user_stretch_budget: float, residual_pitch_budget: float,
                      steps_pct: Tuple[float, ...] = (0.0, 2.5, 5.0)) -> Dict[str, Any]:
    """Score each candidate deck BPM by total clean-transform cost over the pool.

    This is the actual 'lattice' the version was named after: for every candidate
    speed, ask each loop how cleanly varispeed (plus tiny residual pitch) moves it
    to that speed and the harmonic target, then rank the speeds. Pure and cheap —
    no rendering — so it doubles as the preflight readiness audit.
    """
    lattice = build_bpm_lattice(pool, target_bpm, steps_pct)
    scored: List[Dict[str, Any]] = []
    for bpm in lattice:
        total = 0.0
        usable = 0
        rejects = 0
        by_role: Dict[str, int] = {}
        for x in pool:
            role = str(x.get("role") or "full")
            plan = plan_varispeed_transform(role, float(x.get("bpm") or bpm), bpm,
                                            x.get("key_root"), target_key,
                                            user_stretch_budget, residual_pitch_budget)
            cost = _artifact_cost(plan)
            if cost >= 1e6:
                rejects += 1
                continue
            total += cost
            usable += 1
            by_role[role] = by_role.get(role, 0) + 1
        # Average cost over usable loops, penalised by how many loops this speed
        # renders unusable. A speed that keeps 40 loops clean beats one that keeps
        # 8 loops slightly cleaner.
        denom = max(1, usable)
        avg_cost = total / denom
        usable_ratio = usable / max(1, len(pool))
        plan_score = avg_cost + (1.0 - usable_ratio) * 4.0
        scored.append({
            "bpm": round(bpm, 2),
            "usable_loops": usable,
            "rejected_loops": rejects,
            "usable_ratio": round(usable_ratio, 3),
            "avg_transform_cost": round(avg_cost, 4),
            "plan_score": round(plan_score, 4),
            "usable_by_role": by_role,
            "is_user_target": bool(target_bpm and abs(bpm - float(target_bpm)) < 0.05),
        })
    scored.sort(key=lambda r: (r["plan_score"], not r["is_user_target"]))
    best = scored[0] if scored else {"bpm": round(float(target_bpm or 120.0), 2)}
    return {"lattice": scored, "best_bpm": best["bpm"], "best": best, "target_bpm": target_bpm}


# --- Girl Talk density model (the basis for readiness, not invented minimums) ------
# Documented sample density from his catalogued albums:
#   Feed the Animals (2008): ~300+ samples / ~53 min  -> ~5.7 samples/min
#   All Day (2010):          ~372 samples / ~71 min    -> ~5.2 samples/min
# => a new recognizable element roughly every 10-12 s, 2-4 layered at once,
#    ~15-25 distinct source songs per 4-5 min stretch. Foreground hooks rotate
#    fast; instrumental beds ride longer. These constants encode that.
