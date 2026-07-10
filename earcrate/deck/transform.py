from earcrate.core.deps import *
from earcrate.core.deps import _dt
from earcrate.deck.dsp import *
from earcrate.deck.dsp import pitch_distance
def drydeck_transform_limits(role: str) -> Dict[str, float]:
    """Non-degradation transform budgets for dry deck playback.

    v0.5.13 fixes the earlier category error: tempo fit and pitch fit are not
    always separate phase-vocoder operations. Two turntables use varispeed by
    default: changing deck speed changes tempo and pitch together with ordinary
    resampling. The gate therefore budgets two different risks:

    * varispeed_pct: clean deck-speed movement, safe over a wider range.
    * residual_pitch: synthetic correction after varispeed, kept very small.
    """
    role = str(role or "full")
    limits = {
        "drum_anchor": {"residual_pitch": 0.75, "varispeed": 8.0},
        "bass": {"residual_pitch": 0.90, "varispeed": 8.5},
        "vocal": {"residual_pitch": 1.15, "varispeed": 6.5},
        "harmony": {"residual_pitch": 1.25, "varispeed": 8.5},
        "texture": {"residual_pitch": 1.00, "varispeed": 8.5},
        "fx": {"residual_pitch": 1.00, "varispeed": 8.5},
        "full": {"residual_pitch": 0.90, "varispeed": 7.0},
    }
    d = dict(limits.get(role, limits["full"]))
    # Backward-compatible field names for older checks.
    d["pitch"] = d["residual_pitch"]
    d["stretch"] = d["varispeed"]
    return d


def semitones_from_speed_ratio(speed_ratio: float) -> float:
    if speed_ratio <= 0:
        return 0.0
    return float(12.0 * math.log(float(speed_ratio), 2))


def fold_bpm_to_target(source_bpm: float, target_bpm: float) -> Tuple[float, float]:
    """Return the tempo octave a DJ would actually use against target_bpm.

    Library analyzers routinely disagree by half-time or double-time, especially
    across hip-hop, pop, indie, drum breaks, and older dance records. A rigid
    63 -> 126 rejection is musically wrong: the phrase grid is the same, and
    the deck movement is effectively native. This helper folds tempo into the
    nearest octave before clean varispeed is evaluated.
    """
    src = float(source_bpm or target_bpm or 120.0)
    tgt = float(target_bpm or src or 120.0)
    if not (30.0 <= src <= 260.0) or not (40.0 <= tgt <= 240.0):
        return src, 1.0
    candidates: List[Tuple[float, float]] = []
    for k in range(-3, 4):
        mult = 2.0 ** k
        folded = src * mult
        if 35.0 <= folded <= 260.0:
            candidates.append((folded, mult))
    if not candidates:
        return src, 1.0
    folded, mult = min(candidates, key=lambda cm: abs(math.log(max(cm[0], 1e-9) / max(tgt, 1e-9), 2.0)))
    return float(folded), float(mult)


KEYLESS_ROLES = {"drum_anchor", "fx"}


def nearest_harmonic_shift(loop_key: Optional[int], target_key: Optional[int], natural_pitch: float) -> float:
    """Choose the target key shift closest to the pitch created by varispeed."""
    try:
        raw = float(pitch_distance(int(loop_key) % 12, int(target_key) % 12))
    except (TypeError, ValueError):
        # Missing/invalid key metadata only. A broader except here once swallowed
        # a missing-import NameError and silently disabled ALL key discipline
        # (v0.7.1); infrastructure failures must die loud, not defuse the gate.
        raw = 0.0
    candidates = [raw + 12.0 * k for k in range(-2, 3)]
    return float(min(candidates, key=lambda c: abs(c - natural_pitch)))


def plan_varispeed_transform(role: str, source_bpm: float, target_bpm: float, loop_key: Optional[int], target_key: Optional[int], user_stretch_budget: float, residual_pitch_budget: float) -> Dict[str, Any]:
    """Plan the cleanest DJ transform for a loop.

    The renderer implements this plan by resampling the clip to the target loop
    length first. That is the clean varispeed operation. Only the small residual
    pitch difference, if any, is handed to synthetic pitch shifting.
    """
    role = str(role or "full")
    raw_src = float(source_bpm or target_bpm or 120.0)
    tgt = float(target_bpm or raw_src or 120.0)
    if not (30.0 <= raw_src <= 260.0):
        raw_src = tgt
    src, tempo_octave = fold_bpm_to_target(raw_src, tgt)
    speed_ratio = tgt / max(1e-9, src)
    varispeed_pct = abs(speed_ratio - 1.0) * 100.0
    natural_pitch = semitones_from_speed_ratio(speed_ratio)
    if role in KEYLESS_ROLES:
        # Percussive material has no musically meaningful key; the analyzer's key
        # estimate on a drum break is noise. Key-gating drums threw away roughly
        # three quarters of tempo-reachable floor material (measured v0.6.5).
        desired_shift = natural_pitch
    else:
        desired_shift = nearest_harmonic_shift(loop_key, target_key, natural_pitch)
    residual = float(desired_shift - natural_pitch)
    lim = drydeck_transform_limits(role)
    # The user's stretch/varispeed budget is an UPPER bound, clamped to the role-safe
    # ceiling. Previously this was min(lim, max(lim, user)) which algebraically always
    # collapsed to lim, so the UI knob did nothing. Honour the user value when it is a
    # positive number below the ceiling; otherwise fall back to the ceiling.
    ceil_v = float(lim["varispeed"])
    ub = float(user_stretch_budget) if user_stretch_budget else 0.0
    allowed_varispeed = min(ceil_v, ub) if ub > 0 else ceil_v
    # User pitch budget is an upper bound on synthetic correction, never above the role-safe ceiling.
    ceil_r = float(lim["residual_pitch"])
    rb = float(residual_pitch_budget) if residual_pitch_budget is not None else None
    allowed_residual = min(ceil_r, rb) if (rb is not None and rb > 0) else ceil_r
    violation = None
    if varispeed_pct > allowed_varispeed + 1e-6:
        violation = f"{role} varispeed {varispeed_pct:.2f}% exceeds clean deck limit {allowed_varispeed:.2f}%"
    elif abs(residual) > allowed_residual + 1e-6:
        violation = f"{role} residual_pitch {residual:+.2f} exceeds synthetic correction limit ±{allowed_residual:.2f}"
    mode = "native" if varispeed_pct < 0.15 and abs(residual) < 0.05 else ("varispeed" if abs(residual) < 0.15 else "varispeed_residual_pitch")
    return {
        "transform_mode": mode,
        "source_bpm_raw": float(raw_src),
        "source_bpm_folded": float(src),
        "tempo_octave_multiplier": float(tempo_octave),
        "speed_ratio": float(speed_ratio),
        "varispeed_pct": float(varispeed_pct),
        "natural_pitch_shift": float(natural_pitch),
        "desired_key_shift": float(desired_shift),
        "residual_pitch_shift": float(residual),
        "synthetic_pitch_shift": float(residual),
        "artifact_risk": "low" if not violation and abs(residual) <= 0.50 and varispeed_pct <= lim["varispeed"] * 0.75 else ("medium" if not violation else "reject"),
        "violation": violation,
    }


def _artifact_cost(plan: Dict[str, Any]) -> float:
    """Turn a transform plan into a scalar cost. Lower is cleaner.

    Varispeed is cheap (it is what a real deck does); synthetic residual pitch is
    expensive because that is the phase-vocoder smear we are trying to avoid; an
    outright budget violation is effectively infinite (the loop is unusable here).
    """
    if plan.get("violation"):
        return 1e6
    vpct = float(plan.get("varispeed_pct") or 0.0)
    resid = abs(float(plan.get("residual_pitch_shift") or 0.0))
    # 1% varispeed ~= 0.10 cost; 1 semitone synthetic ~= 1.0 cost. Synthetic is ~10x worse.
    return vpct * 0.10 + resid * 1.0



def drydeck_transform_violation(role: str, pitch_shift: float, stretch_pct: float) -> Optional[str]:
    lim = drydeck_transform_limits(role)
    if abs(float(pitch_shift)) > float(lim["residual_pitch"]) + 1e-6:
        return f"{role} residual_pitch {float(pitch_shift):+.2f} exceeds synthetic correction limit ±{float(lim['residual_pitch']):.2f}"
    if float(stretch_pct) > float(lim["varispeed"]) + 1e-6:
        return f"{role} varispeed {float(stretch_pct):.2f}% exceeds clean deck limit {float(lim['varispeed']):.2f}%"
    return None


