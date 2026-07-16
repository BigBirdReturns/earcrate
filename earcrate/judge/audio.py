from earcrate.core.deps import *
from earcrate.core.deps import _dt
from earcrate.ear.readiness import *
from scipy import optimize
def _metric_mono(y: np.ndarray) -> np.ndarray:
    """Downmix frames×channels for scalar spectral/timeline metrics."""
    x = np.asarray(y, dtype=np.float32)
    if x.ndim == 1:
        return x
    if x.ndim == 2:
        return np.mean(x, axis=1, dtype=np.float64).astype(np.float32)
    raise ValueError(f"audio must be mono or frames x channels, got {x.shape}")


def drydeck_metrics(y: np.ndarray, sr: int) -> Dict[str, float]:
    """Post-render metrics over mono or stereo audio with an absolute floor."""
    full = np.nan_to_num(np.asarray(y, dtype=np.float32), nan=0.0, posinf=0.0, neginf=0.0)
    mono = _metric_mono(full)
    frames = int(mono.shape[0])
    if frames == 0:
        return {
            "duration_s": 0.0, "rms_std_db": 0.0, "silence_ratio": 1.0,
            "active_coverage_ratio": 0.0, "audible_seconds": 0.0,
            "first_audible_s": None, "last_audible_s": None,
            "largest_silence_gap_s": 0.0, "low200_share": 0.0,
            "high3000_share": 0.0, "peak": 0.0, "global_rms": 0.0,
            "audible_rms_floor": 0.0,
        }
    frame = max(512, int(5.0 * sr))
    vals = [rms_value(mono[i:i + frame]) for i in range(0, max(1, frames - frame + 1), frame)]
    vals_db = 20 * np.log10(np.asarray(vals, dtype=np.float64) + 1e-9)

    hop_s = 0.05
    hop = max(512, int(hop_s * sr))
    rms_frames = [rms_value(mono[i:i + hop]) for i in range(0, max(1, frames - hop), hop)]
    arr = np.asarray(rms_frames, dtype=np.float64)
    peak = float(np.max(np.abs(full))) if full.size else 0.0
    global_rms = float(np.sqrt(np.mean(np.square(full.astype(np.float64))) + 1e-12))
    audible_floor = max(1e-4, peak * 0.010, global_rms * 0.18)
    active = arr >= audible_floor if arr.size else np.zeros(0, dtype=bool)
    active_coverage = float(np.mean(active)) if active.size else 0.0
    silence_ratio = 1.0 - active_coverage
    if active.size and bool(np.any(active)):
        active_idx = np.flatnonzero(active)
        first_audible_s = float(active_idx[0] * hop_s)
        last_audible_s = float((active_idx[-1] + 1) * hop_s)
        longest = cur = 0
        for flag in active:
            if flag:
                longest = max(longest, cur); cur = 0
            else:
                cur += 1
        longest = max(longest, cur)
        largest_gap_s = float(longest * hop_s)
    else:
        first_audible_s = None
        last_audible_s = None
        largest_gap_s = float(frames / max(1, sr))
    audible_seconds = float(active_coverage * frames / max(1, sr))

    n_fft = 4096
    hop_len = 2048
    try:
        stft = np.abs(librosa.stft(mono, n_fft=n_fft, hop_length=hop_len)) ** 2
        freqs = librosa.fft_frequencies(sr=sr, n_fft=n_fft)
        total = float(np.sum(stft) + 1e-12)
        low200 = float(np.sum(stft[freqs < 200]) / total)
        high3000 = float(np.sum(stft[freqs > 3000]) / total)
    except Exception:
        low200 = high3000 = 0.0
    return {
        "duration_s": float(frames / max(1, sr)),
        "rms_std_db": float(np.std(vals_db)),
        "silence_ratio": float(silence_ratio),
        "active_coverage_ratio": float(active_coverage),
        "audible_seconds": float(audible_seconds),
        "first_audible_s": first_audible_s,
        "last_audible_s": last_audible_s,
        "largest_silence_gap_s": float(largest_gap_s),
        "low200_share": low200,
        "high3000_share": high3000,
        "peak": peak,
        "global_rms": global_rms,
        "audible_rms_floor": float(audible_floor),
    }


# Ground-truth spectral/dynamics profile of REAL Girl Talk, measured by the
# desktop session over 24 tracks of the local "All Day" catalog and replicated
# in librosa. earcrate's exact drydeck definitions may differ slightly, so every
# fail/warn band sits OUTSIDE the observed range rather than on the mean:
#   rms_std_db    mean 5.31  range [3.23-7.62]
#   low200_share  mean 0.20  range [0.07-0.31]   (earcrate render measured 0.59)
#   high3000_share mean 0.31 range [0.19-0.53]   (earcrate render measured 0.031)
# The pre-ground-truth gate was INVERTED on the low end (it required low200 >=
# 0.38, i.e. it rewarded a bass mud wall) and floored presence at 0.030 -- 10x
# below real Girl Talk, which is why a render 10x too dark still "passed". This
# is the corrected calibration: low end is a CEILING, presence a real floor.
# FAIL bands sit OUTSIDE the observed real-GT range so a render that actually
# resembles Girl Talk never false-fails, while a mud-cave always fails:
#   low200 fail 0.45  = 1.45x the real-GT max (0.31); real renders measured 0.59
#   high3000 fail 0.09 = ~half the real-GT min (0.19); real renders measured 0.031
GT_SPECTRAL_PROFILE = {
    "rms_std_db":     {"target": 5.0,  "floor": 3.5},
    "low200_share":   {"ceiling_fail": 0.45, "ceiling_warn": 0.34, "floor_warn": 0.05},
    "high3000_share": {"target": 0.30, "floor_warn": 0.15, "floor_fail": 0.09},
}


def drydeck_quality_gate(metrics: Dict[str, float], target_seconds: float,
                         spectral_profile: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Post-render gate with a user-audible coverage contract.

    A render is not successful merely because it is a correctly sized WAV. For a
    sketch of one minute or longer, most of the timeline must contain audible
    program material, the first music must arrive promptly, and the largest dead
    gap must stay bounded.

    The spectral/dynamics targets default to the REAL Girl Talk distribution
    (GT_SPECTRAL_PROFILE), but a PERSONA may pass its own ``spectral_profile`` so
    it is judged on its OWN aesthetic: a warm, vinyl-rolled-off chopped-soul remix
    (Pretty Lights) legitimately has less >3kHz air than a bright modern collage,
    and must not fail a Girl-Talk presence floor to be "correct". Coverage/timing
    rules are persona-independent (silence is silence).
    """
    prof = spectral_profile or GT_SPECTRAL_PROFILE
    failures: List[str] = []
    warnings: List[str] = []
    if target_seconds >= 60:
        rms_std = float(metrics.get("rms_std_db", 0.0))
        low200 = float(metrics.get("low200_share", 0.0))
        high3000 = float(metrics.get("high3000_share", 0.0))
        silence = float(metrics.get("silence_ratio", 1.0))
        active = float(metrics.get("active_coverage_ratio", 0.0))
        audible_seconds = float(metrics.get("audible_seconds", 0.0))
        largest_gap = float(metrics.get("largest_silence_gap_s", target_seconds))
        first_audible = metrics.get("first_audible_s")
        peak = float(metrics.get("peak", 0.0))
        if peak < 0.01:
            failures.append("peak below audible floor; render is effectively empty")
        if audible_seconds < min(target_seconds * 0.62, target_seconds - 12.0):
            failures.append("audible coverage catastrophically low; render is mostly silence")
        elif active < 0.76:
            warnings.append("audible coverage below target 0.76; sketch has too much empty timeline")
        if silence > 0.38:
            failures.append("silence_ratio catastrophically high under absolute floor")
        elif silence > 0.24:
            warnings.append("silence_ratio above target under absolute floor")
        if first_audible is None:
            failures.append("no audible program material detected")
        elif float(first_audible) > 8.0:
            failures.append(f"first audible material starts too late at {float(first_audible):.2f}s")
        if largest_gap > max(14.0, target_seconds * 0.18):
            failures.append(f"largest silent gap too long at {largest_gap:.2f}s")
        # Dynamics: real Girl Talk rms_std_db mean 5.31 [3.23-7.62]. Target ~5;
        # a usable sketch clears ~3.5; below 1.6 the render is effectively flat.
        rms_floor = prof["rms_std_db"]["floor"]
        rms_target = prof["rms_std_db"]["target"]
        if rms_std < 1.6:
            failures.append("rms_std_db catastrophically low; render is effectively flat")
        elif rms_std < rms_floor:
            warnings.append(
                "rms_std_db %.2f below target ~%.1f (real Girl Talk ~5.3); dynamics need more arc"
                % (rms_std, rms_target))
        # Low end is a CEILING, not a floor. Real Girl Talk low200_share is
        # ~0.20 [0.07-0.31]; earcrate renders measured 0.59 -- a bass mud wall
        # from beds that were never high-passed. The old gate REWARDED that
        # (required low200 >= 0.38); it is inverted here to catch the mud.
        lo = prof["low200_share"]
        if low200 > lo["ceiling_fail"]:
            failures.append(
                "low200_share %.2f is a low-end mud wall (real Girl Talk ~0.20, ceiling %.2f); high-pass the beds"
                % (low200, lo["ceiling_fail"]))
        elif low200 > lo["ceiling_warn"]:
            warnings.append(
                "low200_share %.2f above real-GT range (~0.20); tame the low end" % (low200,))
        elif low200 < lo["floor_warn"]:
            warnings.append(
                "low200_share %.2f very thin (real GT ~0.20); floor authority weak" % (low200,))
        # Presence: real Girl Talk high3000_share mean 0.31 [0.19-0.53]; earcrate
        # renders measured 0.031 -- 10x too dark. The old floor 0.030 let that
        # pass; recalibrated to the real distribution.
        hi = prof["high3000_share"]
        if high3000 < hi["floor_fail"]:
            failures.append(
                "high3000_share %.3f catastrophically dark (real Girl Talk ~0.31); presence is dead" % (high3000,))
        elif high3000 < hi["floor_warn"]:
            warnings.append(
                "high3000_share %.3f below target ~%.2f (real GT ~0.31); lift presence" % (high3000, hi["target"]))
    return {"passed": not failures, "failures": failures, "warnings": warnings, "metrics": metrics}

def _band_shares(x: np.ndarray, sr: int) -> Tuple[float, float, float]:
    """(low200, high3000, power) on the judge's mono downmix ruler."""
    mono = _metric_mono(x)
    stft = np.abs(librosa.stft(mono, n_fft=4096, hop_length=2048)) ** 2
    freqs = librosa.fft_frequencies(sr=sr, n_fft=4096)
    total = float(np.sum(stft) + 1e-12)
    low = float(np.sum(stft[freqs < 200]) / total)
    high = float(np.sum(stft[freqs > 3000]) / total)
    return low, high, total


def _smooth_band_gain(freqs: np.ndarray, lo_hz: float, hi_hz: Optional[float],
                      amp: float, width_frac: float = 0.18) -> np.ndarray:
    """Amplitude gain curve that applies ``amp`` inside [lo_hz, hi_hz) with
    logistic edges (no brick-wall zippering). hi_hz=None means 'to Nyquist'."""
    g = np.ones_like(freqs, dtype=np.float64)
    if lo_hz <= 0.0:
        ramp_in = np.ones_like(freqs, dtype=np.float64)
    else:
        w_lo = max(18.0, lo_hz * width_frac)
        ramp_in = 1.0 / (1.0 + np.exp(np.clip(-(freqs - lo_hz) / w_lo, -60.0, 60.0)))
    if hi_hz is None:
        mask = ramp_in
    else:
        w_hi = max(18.0, hi_hz * width_frac)
        ramp_out = 1.0 / (1.0 + np.exp(np.clip((freqs - hi_hz) / w_hi, -60.0, 60.0)))
        mask = ramp_in * ramp_out
    return (1.0 + (amp - 1.0) * mask).astype(np.float64)


def _presence_shelf_weight(freqs: np.ndarray, lo_hz: float = 3000.0,
                           hi_hz: float = 4000.0) -> np.ndarray:
    """Lower-knee-anchored shelf weight: 0 at/below lo_hz, 1 at/above hi_hz,
    quintic smootherstep (C2-continuous, no zippering) in between, interpolated
    in LOG frequency so the transition behaves consistently in musical terms.

    A logistic curve centered AT the gate's measurement boundary (the previous
    approach) is only 50% of its amplitude at that exact boundary and doesn't
    reach full gain until several widths above it -- which both under-delivers
    right where the gate starts counting AND spills real boost into the
    1.4-3kHz vocal-presence/intelligibility range this shelf has no business
    touching. Anchoring the LOWER knee at the measurement boundary means zero
    gain is applied at or below it -- no collateral lift into protected
    territory -- while the plateau is reached by a musically reasonable
    ~0.4-octave transition (3.0-4.0kHz default), not deep into the midrange."""
    if lo_hz <= 0 or hi_hz <= lo_hz:
        raise ValueError("Require 0 < lo_hz < hi_hz")
    safe_freqs = np.maximum(freqs, np.finfo(float).tiny)
    t = np.log2(safe_freqs / lo_hz) / np.log2(hi_hz / lo_hz)
    t = np.clip(t, 0.0, 1.0)
    return t * t * t * (t * (t * 6.0 - 15.0) + 10.0)


def _presence_shelf_gain(freqs: np.ndarray, shelf_db: float, lo_hz: float = 3000.0,
                         hi_hz: float = 4000.0) -> np.ndarray:
    """Amplitude gain for the presence shelf; gain interpolated in dB (not
    linear amplitude) across the transition, per the lower-knee weight."""
    weight = _presence_shelf_weight(freqs, lo_hz, hi_hz)
    return np.power(10.0, shelf_db * weight / 20.0)


def _solve_presence_shelf_db(freqs: np.ndarray, bin_power: np.ndarray, target_share: float,
                             max_db: float, boundary_hz: float = 3000.0,
                             shelf_lo_hz: float = 3000.0, shelf_hi_hz: float = 4000.0) -> Optional[float]:
    """Required plateau shelf gain (dB) to reach ``target_share``, solved
    against the ACTUAL shelf response and the track's ACTUAL per-bin power --
    not an idealized step-function formula. Returns 0.0 if already there,
    None if unreachable within max_db (the caller must refuse, not clamp)."""
    if bin_power.sum() <= 0:
        return None
    counted = freqs > boundary_hz

    def _predicted_share(shelf_db: float) -> float:
        power_gain = np.power(10.0, shelf_db * _presence_shelf_weight(freqs, shelf_lo_hz, shelf_hi_hz) / 10.0)
        corrected = bin_power * power_gain
        return float(corrected[counted].sum() / corrected.sum())

    if _predicted_share(0.0) >= target_share:
        return 0.0
    if _predicted_share(max_db) < target_share:
        return None
    return float(optimize.brentq(lambda db: _predicted_share(db) - target_share, 0.0, max_db, xtol=1e-4))


SYSTEM_PRESENCE_CEILING_DB = 6.0
# Absolute backstop above which the smooth-shelf finishing EQ is not validated,
# regardless of what any persona's own thresholds would otherwise permit.


def _log_odds_gain_db(target: float, floor_fail: float) -> float:
    """Ideal shelf gain (dB) to move a render at a persona's own floor_fail up
    to its restoration target, in log-odds space (the natural space for a
    share/(1-share) ratio, not a linear or raw-ratio one).

    Derivation: for an ideal filter that multiplies all power above 3kHz by K
    and leaves everything below unchanged, the resulting high-band share is
    s' = Ks / ((1-s) + Ks). Solving s'=target for K gives
    K = odds(target) / odds(share), so the two thresholds (target and
    floor_fail) that already exist in every persona's authored TasteSpec
    directly imply a defensible gain ceiling -- no new measurement needed."""
    def _odds(p: float) -> float:
        p = min(max(p, 1e-9), 1.0 - 1e-9)
        return p / (1.0 - p)
    return 10.0 * math.log10(_odds(target) / _odds(floor_fail))


def stable_presence_restore(y: np.ndarray, sr: int, return_receipt: bool = False,
                            spectral_profile: Optional[Dict[str, Any]] = None) -> Any:
    """Conservative measured finishing EQ with an auditable correction limit.

    Finishing may trim excess low end, but it may only make a small presence
    correction to material the arrangement already contains. If the calibrated
    presence floor would require a larger broad shelf, the shelf is refused and
    the render gate must reject the arrangement. That distinction is important:
    a large >3 kHz boost can turn an otherwise insignificant hiss or transform
    residue into enough spectral power to pass a naive gate.

    The correction ceiling is NOT a flat constant across personas -- it is
    min(SYSTEM_PRESENCE_CEILING_DB, the persona's own log-odds reach from its
    authored floor_fail to its restoration target). A persona that authors a
    wider floor_fail-to-target gap (e.g. an intentionally warmer/darker remix
    persona) legitimately tolerates a larger correction than one authored with
    a narrow gap -- using data every persona already carries, not a new
    subsystem. (2026-07-15: replaced a flat 3.0dB cap that was never checked
    against the 22 personas' own authored floor_fail/target pairs; see
    .agent/journal for the derivation.)

    ``return_receipt`` lets the publication path record whether presence was
    naturally present, modestly corrected, or refused as an attempted rescue.
    The default ndarray return keeps analysis callers backward compatible."""
    x = np.nan_to_num(y.astype(np.float32), nan=0.0, posinf=0.0, neginf=0.0)
    # Finishing and judgment must use the same ruler.  Pretty Lights is
    # intentionally warmer/darker than Girl Talk; forcing every persona toward
    # the GT presence floor can turn a correct rolled-off mix into needless
    # sharpness (or, worse, promote faint separation residue).  The default is
    # unchanged for callers without a persona.
    prof = spectral_profile or GT_SPECTRAL_PROFILE
    before_low, before_high, _ = _band_shares(x, sr) if x.size else (0.0, 0.0, 0.0)
    receipt: Dict[str, Any] = {
        "policy": "presence_is_not_noise_rescue_v1",
        "before_low200_share": before_low,
        "before_high3000_share": before_high,
        "low_cut_db": 0.0,
        "high_boost_db": 0.0,
        "required_high_boost_db": 0.0,
        "high_boost_cap_db": SYSTEM_PRESENCE_CEILING_DB,
        "persona_reach_db": None,
        "presence_rescue_refused": False,
        "passed": True,
        "spectral_profile": "persona" if spectral_profile is not None else "girl_talk_default",
    }
    if y.size < sr // 2:
        out = x.astype(np.float32)
        receipt.update({"after_low200_share": before_low, "after_high3000_share": before_high})
        return (out, receipt) if return_receipt else out

    # Finishing targets the acceptance floor, not the reference mean.  Chasing
    # the 0.30 mean with a broad shelf was the loophole that promoted hiss.
    high_floor = float(prof["high3000_share"].get("floor_warn", 0.15))
    # A narrow margin prevents the smooth shelf from landing a few floating
    # point ulps below the hard floor; it is not an attempt to chase the mean.
    high_target = min(high_floor + 0.01, 1.0)
    low_target = 0.20  # real-GT mean; ceiling_warn is 0.34
    # The cap is derived from THIS persona's own authored floor_fail -- the
    # gain that would be legitimate to move a render sitting at that persona's
    # own "clearly failing" floor up to the restoration target -- capped by an
    # absolute system backstop so no persona's authored gap can imply an
    # unbounded shelf. Never a flat number applied identically to every genre.
    high_floor_fail = float(prof["high3000_share"].get("floor_fail", high_floor * 0.6))
    persona_reach_db = _log_odds_gain_db(high_target, high_floor_fail)
    receipt["persona_reach_db"] = persona_reach_db
    receipt["high_boost_cap_db"] = min(SYSTEM_PRESENCE_CEILING_DB, persona_reach_db)
    LOW_GAIN_LIMIT = 10.0 ** (14.0 / 20.0)
    cum_low = 1.0
    cum_high = 1.0

    def _solve(share: float, target: float) -> float:
        share = min(max(share, 1e-6), 1.0 - 1e-6)
        target = min(max(target, 1e-6), 1.0 - 1e-6)
        return (target * (1.0 - share)) / (share * (1.0 - target))

    def _apply(low_amp: float = 1.0, high_shelf_db: float = 0.0) -> None:
        nonlocal x
        n = int(2 ** math.ceil(math.log2(max(32, x.size))))
        spec = np.fft.rfft(x, n=n)
        freqs = np.fft.rfftfreq(n, 1 / sr)
        gain = _smooth_band_gain(freqs, 0.0, 200.0, low_amp)
        if high_shelf_db:
            gain = gain * _presence_shelf_gain(freqs, high_shelf_db)
        gain[freqs < 30] *= 0.60
        x = np.fft.irfft(spec * gain, n=n)[: x.size].astype(np.float32)

    def _bin_power_for_high_solve() -> Tuple[np.ndarray, np.ndarray]:
        """Per-frequency-bin power (summed over time frames), same STFT
        definition _band_shares uses, so the solver predicts against the exact
        ruler the gate measures with."""
        stft = np.abs(librosa.stft(_metric_mono(x), n_fft=4096, hop_length=2048)) ** 2
        freqs = librosa.fft_frequencies(sr=sr, n_fft=4096)
        return freqs, stft.sum(axis=1)

    # Low-end correction cannot invent high-frequency material.  Perform it
    # first, then judge how much presence boost the corrected mix still needs.
    for _ in range(3):
        low, high, _tot = _band_shares(x, sr)
        low_ok = low <= float(prof["low200_share"].get("ceiling_warn", 0.34))
        if low_ok:
            break
        a_low = math.sqrt(max(1e-6, _solve(low, low_target)))
        a_low = max(1.0 / LOW_GAIN_LIMIT / cum_low, min(a_low, 1.0))
        if abs(a_low - 1.0) < 1e-3:
            break
        cum_low *= a_low
        _apply(low_amp=a_low)

    # Response-aware: solve for the shelf gain against the ACTUAL post-low-cut
    # spectrum and the ACTUAL shelf response (lower-knee-anchored, no
    # collateral lift into 1.4-3kHz), not an idealized step-function formula.
    # One correct solve, not iterative approximation toward one.
    _, high, _tot = _band_shares(x, sr)
    if high < high_floor:
        solve_freqs, bin_power = _bin_power_for_high_solve()
        required_db = _solve_presence_shelf_db(
            solve_freqs, bin_power, high_target,
            max_db=receipt["high_boost_cap_db"], boundary_hz=3000.0,
        )
        if required_db is None:
            receipt["presence_rescue_refused"] = True
            receipt["passed"] = False
            # required_high_boost_db still reported for the receipt/refusal
            # message even though we refuse to apply it: solve once more
            # without the cap to report an honest (if unenforced) figure.
            uncapped = _solve_presence_shelf_db(
                solve_freqs, bin_power, high_target,
                max_db=SYSTEM_PRESENCE_CEILING_DB * 4.0, boundary_hz=3000.0,
            )
            receipt["required_high_boost_db"] = float(uncapped) if uncapped is not None else float(SYSTEM_PRESENCE_CEILING_DB * 4.0)
        elif required_db > 1e-3:
            receipt["required_high_boost_db"] = required_db
            cum_high = 10.0 ** (required_db / 20.0)
            _apply(high_shelf_db=required_db)
            # Verify against the real render, the same way the gate will --
            # a predicted solve is not a receipt until it's been measured.
            _, verify_high, _ = _band_shares(x, sr)
            if verify_high < high_floor - 1e-4:
                receipt["presence_rescue_refused"] = True
                receipt["passed"] = False

    peak = float(np.max(np.abs(x))) if x.size else 0.0
    if peak > 0.94:
        x *= 0.94 / peak
    out = np.nan_to_num(x.astype(np.float32), nan=0.0, posinf=0.0, neginf=0.0)
    after_low, after_high, _ = _band_shares(out, sr)
    receipt.update({
        "low_cut_db": 20.0 * math.log10(max(cum_low, 1e-12)),
        "high_boost_db": 20.0 * math.log10(max(cum_high, 1e-12)),
        "after_low200_share": after_low,
        "after_high3000_share": after_high,
    })
    if after_high < high_floor:
        receipt["passed"] = False
    return (out, receipt) if return_receipt else out



def _project_master_action_id(kind: str, parameters: Dict[str, Any], evidence: Dict[str, Any]) -> str:
    return "master_" + sha256_text(json_dumps({"kind": kind, "parameters": parameters, "evidence": evidence}))[:24]


def _apply_project_eq_action(x: np.ndarray, sr: int, action: Dict[str, Any]) -> np.ndarray:
    kind = str(action.get("kind") or "")
    params = dict(action.get("parameters") or {})
    audio = np.asarray(x, dtype=np.float32)
    frames = int(audio.shape[0])
    n = int(2 ** math.ceil(math.log2(max(32, frames))))
    spec = np.fft.rfft(audio, n=n, axis=0)
    freqs = np.fft.rfftfreq(n, 1 / sr)
    gain = np.ones_like(freqs, dtype=np.float64)
    if kind == "low_shelf":
        gain *= _smooth_band_gain(freqs, 0.0, 200.0, 10.0 ** (float(params["gain_db"]) / 20.0))
        gain[freqs < 30] *= 0.60
    elif kind == "presence_shelf":
        gain *= _presence_shelf_gain(
            freqs, float(params["gain_db"]),
            float(params.get("lower_knee_hz") or 3000.0),
            float(params.get("upper_knee_hz") or 4000.0),
        )
    else:
        raise ValueError(f"not an EQ master action: {kind}")
    shaped = gain if audio.ndim == 1 else gain[:, None]
    return np.fft.irfft(spec * shaped, n=n, axis=0)[:frames].astype(np.float32)



def _integrated_lufs_value(y: np.ndarray, sr: int) -> float:
    if y.size < sr // 2 or float(np.max(np.abs(y))) < 1e-9:
        return -180.0
    try:
        meter = pyln.Meter(sr)
        value = float(meter.integrated_loudness(y.astype(np.float64)))
        return value if np.isfinite(value) else -180.0
    except Exception:
        return -180.0


def resolve_project_master_actions(y: np.ndarray, sr: int,
                                   compiled_policy: Dict[str, Any]) -> Dict[str, Any]:
    """Resolve the complete project mastering plan without hiding a DSP choice.

    The resolver works on a premaster, mutating only a private working copy so it
    can solve sequential actions. The returned actions are immutable score data;
    the publication renderer later applies exactly these values and refuses any
    drift. Legacy non-project rendering continues through stable_presence_restore.
    """
    x = np.nan_to_num(y.astype(np.float32), nan=0.0, posinf=0.0, neginf=0.0)
    spectral = dict(compiled_policy.get("spectral_profile") or GT_SPECTRAL_PROFILE)
    policy = dict(compiled_policy.get("mastering_policy") or {})
    before_low, before_high, _ = _band_shares(x, sr) if x.size else (0.0, 0.0, 0.0)
    actions: List[Dict[str, Any]] = []
    receipt: Dict[str, Any] = {
        "policy": "explicit_project_mastering_v1",
        "before_low200_share": before_low,
        "before_high3000_share": before_high,
        "passed": True,
        "refusal": None,
        "actions": actions,
    }
    if x.size < sr // 2:
        receipt.update({"after_low200_share": before_low, "after_high3000_share": before_high})
        return receipt

    low_cfg = dict(policy.get("low_shelf") or {})
    low_warn = float(low_cfg.get("ceiling_warn", spectral["low200_share"].get("ceiling_warn", 0.34)))
    low_target = float(low_cfg.get("target_share", spectral["low200_share"].get("target", 0.20)))
    max_cut_db = float(low_cfg.get("max_cut_db", 14.0))
    if bool(low_cfg.get("allowed", True)) and before_low > low_warn:
        share = min(max(before_low, 1e-6), 1.0 - 1e-6)
        target = min(max(low_target, 1e-6), 1.0 - 1e-6)
        power_gain = (target * (1.0 - share)) / (share * (1.0 - target))
        gain_db = 10.0 * math.log10(max(power_gain, 1e-12))
        gain_db = max(-abs(max_cut_db), min(0.0, gain_db))
        params = {"gain_db": gain_db, "low_hz": 0.0, "high_hz": 200.0}
        evidence = {
            "before_low200_share": before_low,
            "target_low200_share": low_target,
            "ceiling_warn": low_warn,
            "max_cut_db": max_cut_db,
        }
        action = {"kind": "low_shelf", "parameters": params, "evidence": evidence}
        action["action_id"] = _project_master_action_id("low_shelf", params, evidence)
        actions.append(action)
        x = _apply_project_eq_action(x, sr, action)

    low_after, high_after_low, _ = _band_shares(x, sr)
    hi_cfg = dict(policy.get("presence_shelf") or {})
    high_floor = float(hi_cfg.get("floor_warn", spectral["high3000_share"].get("floor_warn", 0.15)))
    high_target = float(hi_cfg.get("target_share", min(high_floor + 0.01, 1.0)))
    high_floor_fail = float(hi_cfg.get("floor_fail", spectral["high3000_share"].get("floor_fail", high_floor * 0.6)))
    persona_reach_db = _log_odds_gain_db(high_target, high_floor_fail)
    cap_db = min(float(hi_cfg.get("system_ceiling_db", SYSTEM_PRESENCE_CEILING_DB)), persona_reach_db)
    if bool(hi_cfg.get("allowed", True)) and high_after_low < high_floor:
        stft = np.abs(librosa.stft(_metric_mono(x), n_fft=4096, hop_length=2048)) ** 2
        freqs = librosa.fft_frequencies(sr=sr, n_fft=4096)
        required_db = _solve_presence_shelf_db(
            freqs,
            stft.sum(axis=1),
            high_target,
            max_db=cap_db,
            boundary_hz=3000.0,
            shelf_lo_hz=float(hi_cfg.get("lower_knee_hz", 3000.0)),
            shelf_hi_hz=float(hi_cfg.get("upper_knee_hz", 4000.0)),
        )
        if required_db is None:
            uncapped = _solve_presence_shelf_db(
                freqs,
                stft.sum(axis=1),
                high_target,
                max_db=float(hi_cfg.get("system_ceiling_db", SYSTEM_PRESENCE_CEILING_DB)) * 4.0,
                boundary_hz=3000.0,
                shelf_lo_hz=float(hi_cfg.get("lower_knee_hz", 3000.0)),
                shelf_hi_hz=float(hi_cfg.get("upper_knee_hz", 4000.0)),
            )
            receipt["passed"] = False
            receipt["refusal"] = {
                "kind": "presence_cap_exceeded",
                "required_high_boost_db": float(uncapped) if uncapped is not None else float(hi_cfg.get("system_ceiling_db", 6.0)) * 4.0,
                "high_boost_cap_db": cap_db,
            }
        elif required_db > 1e-4:
            params = {
                "gain_db": float(required_db),
                "lower_knee_hz": float(hi_cfg.get("lower_knee_hz", 3000.0)),
                "upper_knee_hz": float(hi_cfg.get("upper_knee_hz", 4000.0)),
            }
            evidence = {
                "before_high3000_share": high_after_low,
                "target_high3000_share": high_target,
                "required_high_boost_db": float(required_db),
                "persona_reach_db": persona_reach_db,
                "high_boost_cap_db": cap_db,
                "response": "lower-knee log-frequency C2 smootherstep",
            }
            action = {"kind": "presence_shelf", "parameters": params, "evidence": evidence}
            action["action_id"] = _project_master_action_id("presence_shelf", params, evidence)
            actions.append(action)
            x = _apply_project_eq_action(x, sr, action)

    target_lufs = float(policy.get("integrated_lufs", -14.0))
    measured_lufs = _integrated_lufs_value(x, sr)
    loudness_gain_db = 0.0 if measured_lufs <= -170.0 else min(target_lufs - measured_lufs, 1.5)
    params = {"gain_db": loudness_gain_db, "target_lufs": target_lufs}
    evidence = {"measured_lufs": measured_lufs, "target_lufs": target_lufs, "upward_makeup_cap_db": 1.5}
    action = {"kind": "loudness_normalize", "parameters": params, "evidence": evidence}
    action["action_id"] = _project_master_action_id("loudness_normalize", params, evidence)
    actions.append(action)
    x = (x.astype(np.float64) * (10.0 ** (loudness_gain_db / 20.0))).astype(np.float32)

    ceiling = float(policy.get("peak_ceiling", 0.891))
    peak = float(np.max(np.abs(x))) if x.size else 0.0
    peak_gain_db = 0.0 if peak <= ceiling or peak <= 0 else 20.0 * math.log10(ceiling / peak)
    params = {"gain_db": peak_gain_db, "ceiling": ceiling}
    evidence = {"peak_before": peak, "ceiling": ceiling}
    action = {"kind": "peak_guard", "parameters": params, "evidence": evidence}
    action["action_id"] = _project_master_action_id("peak_guard", params, evidence)
    actions.append(action)
    x = (x.astype(np.float64) * (10.0 ** (peak_gain_db / 20.0))).astype(np.float32)

    final_low, final_high, _ = _band_shares(x, sr)
    receipt.update({
        "after_low200_share": final_low,
        "after_high3000_share": final_high,
        "persona_reach_db": persona_reach_db,
        "high_boost_cap_db": cap_db,
        "action_count": len(actions),
        "predicted_integrated_lufs": _integrated_lufs_value(x, sr),
        "predicted_peak": float(np.max(np.abs(x))) if x.size else 0.0,
    })
    if final_high < high_floor - 1e-4:
        receipt["passed"] = False
        receipt["refusal"] = receipt.get("refusal") or {
            "kind": "presence_verification_failed",
            "after_high3000_share": final_high,
            "floor_warn": high_floor,
        }
    return receipt


def apply_project_master_actions(y: np.ndarray, sr: int,
                                 actions: List[Dict[str, Any]]) -> Tuple[np.ndarray, Dict[str, Any]]:
    """Apply exactly the actions sealed into a score revision.

    This function never solves, clamps, substitutes or chooses an action. Any
    malformed or unsupported action is a hard render failure.
    """
    x = np.nan_to_num(y.astype(np.float32), nan=0.0, posinf=0.0, neginf=0.0)
    executions: List[Dict[str, Any]] = []
    for action in actions:
        kind = str(action.get("kind") or "")
        params = dict(action.get("parameters") or {})
        before_low, before_high, _ = _band_shares(x, sr) if x.size else (0.0, 0.0, 0.0)
        before_peak = float(np.max(np.abs(x))) if x.size else 0.0
        before_lufs = _integrated_lufs_value(x, sr)
        if kind in {"low_shelf", "presence_shelf"}:
            x = _apply_project_eq_action(x, sr, action)
        elif kind in {"loudness_normalize", "peak_guard"}:
            gain_db = float(params["gain_db"])
            x = (x.astype(np.float64) * (10.0 ** (gain_db / 20.0))).astype(np.float32)
        else:
            raise ValueError(f"unsupported project master action: {kind}")
        after_low, after_high, _ = _band_shares(x, sr) if x.size else (0.0, 0.0, 0.0)
        executions.append({
            "action_id": str(action.get("action_id") or ""),
            "kind": kind,
            "parameters": params,
            "executed": True,
            "before": {"low200_share": before_low, "high3000_share": before_high, "peak": before_peak, "integrated_lufs": before_lufs},
            "after": {"low200_share": after_low, "high3000_share": after_high,
                      "peak": float(np.max(np.abs(x))) if x.size else 0.0,
                      "integrated_lufs": _integrated_lufs_value(x, sr)},
        })
    return np.nan_to_num(x.astype(np.float32), nan=0.0, posinf=0.0, neginf=0.0), {
        "policy": "explicit_project_mastering_v1",
        "action_count": len(actions),
        "actions": json.loads(json.dumps(actions, ensure_ascii=False)),
        "executions": executions,
        "passed": len(executions) == len(actions),
        "modifies_audio": bool(actions),
    }


def periodic_cycle_receipt(y: np.ndarray, cycle_samples: int) -> Dict[str, Any]:
    """Verify repeated render context before publishing a seamless middle cycle.

    A loop is not certified by fading its edges.  The arrangement must render at
    least four consecutive copies; the two interior cycles must be the same
    signal (within numerical/render state tolerance).  Publishing cycle two then
    preserves the already-rendered cycle-two -> cycle-three transition at its
    file boundary, with a full cycle of DSP context on either side.
    """
    n = int(cycle_samples)
    frames = int(np.asarray(y).shape[0])
    if n <= 0 or frames < n * 4:
        return {
            "passed": False,
            "cycle_samples": n,
            "available_samples": frames,
            "repeat_error_db": 0.0,
            "reason": "seamless loop verification requires four complete rendered cycles",
        }
    middle = y[n:2 * n].astype(np.float64)
    following = y[2 * n:3 * n].astype(np.float64)
    signal_rms = float(np.sqrt(np.mean(middle * middle)) + 1e-12)
    error_rms = float(np.sqrt(np.mean((middle - following) ** 2)) + 1e-12)
    error_db = float(20.0 * np.log10(error_rms / signal_rms))
    return {
        "passed": error_db <= -60.0,
        "cycle_samples": n,
        "available_samples": frames,
        "repeat_error_db": error_db,
        "threshold_db": -60.0,
        "crop_start_sample": n,
        "crop_end_sample": 2 * n,
        "rule": "cycle two must match cycle three; publish cycle two with rendered context on both sides",
    }

def reference_fidelity_gates(render: Dict[str, Any], reference: Dict[str, Any]) -> Dict[str, Any]:
    """Reference-relative acceptance kept separate from the generic GT v1.1 gate.

    A supplied producer reference may intentionally live outside Girl Talk's
    spectral distribution.  We never weaken or overwrite the v1.1 verdict; this
    second verdict asks whether the render preserves the supplied reference's
    dynamics, silence density, low-band balance, tempo, and tonal diversity.
    """
    ref_bpm = float(reference.get("bpm") or 0.0)
    render_bpm = float(render.get("bpm") or 0.0)
    bpm_tolerance = max(3.0, abs(ref_bpm) * 0.05) if ref_bpm > 0 else 0.0
    gates = {
        "rms_std_delta": abs(float(render.get("rms_std_db") or 0.0) - float(reference.get("rms_std_db") or 0.0)) <= 1.5,
        "silence_ratio_delta": abs(float(render.get("silence_ratio") or 0.0) - float(reference.get("silence_ratio") or 0.0)) <= 0.12,
        "low200_share_delta": abs(float(render.get("low200_share") or 0.0) - float(reference.get("low200_share") or 0.0)) <= 0.15,
        "bpm_delta": (abs(render_bpm - ref_bpm) <= bpm_tolerance) if ref_bpm > 0 else True,
        "tonal_diversity_not_collapsed": int(render.get("distinct_pcs") or 0) >= max(1, int(reference.get("distinct_pcs") or 0) - 2),
    }
    return {
        "gates": gates,
        "passed": bool(all(gates.values())),
        "tolerances": {
            "rms_std_db_absolute_delta_max": 1.5,
            "silence_ratio_absolute_delta_max": 0.12,
            "low200_share_absolute_delta_max": 0.15,
            "bpm_absolute_delta_max": round(bpm_tolerance, 3),
            "tonal_diversity_allowed_drop": 2,
        },
        "rule": "reference fidelity is additive; it never replaces or weakens generic v1.1 gates",
    }


def judge_audio_file(path: Path, ref_path: Optional[Path] = None,
                     spectral_profile: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Reference-comparison harness from Addendum A0.

    ``spectral_profile`` (a persona's merged spectral target, as returned by
    ``EarcrateCore._persona_spectral_profile``) adds an ADDITIVE persona verdict
    next to the generic v1.1 gates: the v1.1 verdict is never weakened, but a
    persona-correct render (e.g. Pretty Lights' warm low end failing the Girl
    Talk mud ceiling) is no longer reported as simply broken."""
    def metrics_one(p: Path) -> Dict[str, Any]:
        sr = 22050
        y = decode_audio(p, sr=sr)
        y = np.nan_to_num(y.astype(np.float32), nan=0.0, posinf=0.0, neginf=0.0)
        frame = max(512, int(5.0 * sr))
        vals = []
        for i in range(0, max(1, y.size - frame + 1), frame):
            vals.append(rms_value(y[i:i+frame]))
        if not vals:
            vals = [rms_value(y)]
        vals = np.asarray(vals, dtype=np.float64)
        db = 20 * np.log10(np.maximum(vals, 1e-9))
        rms_std_db = float(np.std(db))
        hop = 512
        frame2 = 2048
        rms = librosa.feature.rms(y=y, frame_length=frame2, hop_length=hop)[0]
        med = float(np.median(rms)) if rms.size else 0.0
        silence_ratio = float(np.mean(rms < max(1e-9, med * 0.1))) if rms.size else 0.0
        S = np.abs(librosa.stft(y, n_fft=2048, hop_length=1024)) ** 2
        freqs = librosa.fft_frequencies(sr=sr, n_fft=2048)
        total = float(np.sum(S) + 1e-12)
        low200_share = float(np.sum(S[freqs < 200]) / total)
        try:
            tempo, _ = librosa.beat.beat_track(y=y, sr=sr)
            bpm = float(np.asarray(tempo).reshape(-1)[0])
        except Exception:
            bpm = 0.0
        pcs = []
        win = int(10.0 * sr)
        for start in range(0, max(1, y.size), win):
            seg = y[start:start+win]
            if seg.size < sr:
                continue
            try:
                chroma = librosa.feature.chroma_cqt(y=seg, sr=sr)
            except Exception:
                chroma = librosa.feature.chroma_stft(y=seg, sr=sr)
            v = np.mean(chroma, axis=1)
            pcs.append(int(np.argmax(v)) if np.sum(v) > 0 else 0)
        peak = float(np.max(np.abs(y))) if y.size else 0.0
        crest_db = float(20 * np.log10(max(peak, 1e-9) / max(rms_value(y), 1e-9)))
        info = read_wav_info_chunk(p) if p.suffix.lower() == ".wav" else {}
        return {
            "path": str(p),
            "rms_std_db": round(rms_std_db, 3),
            "silence_ratio": round(silence_ratio, 4),
            "low200_share": round(low200_share, 4),
            "dominant_pc_sequence": pcs,
            "distinct_pcs": int(len(set(pcs))),
            "bpm": round(bpm, 3),
            "crest_db": round(crest_db, 3),
            "render_sha256": sha256_file(p),
            "engine": info.get("IENG") or "",
            "arrangement_sha": info.get("ISBJ") or "",
        }
    render = metrics_one(path)
    out: Dict[str, Any] = {"render": render, "engine": render.get("engine") or ENGINE_VERSION}
    # low200 is a CEILING, consistent with drydeck_quality_gate + the real Girl
    # Talk distribution (~0.20 [0.07-0.31]). The old v1_1 rule REQUIRED low200 >=
    # 0.48, which directly contradicted the dry-deck mud ceiling (fail > 0.45): a
    # real-GT-like render (0.20) failed here while a mud wall (0.5) failed there,
    # so nothing could satisfy both judges. See GT_SPECTRAL_PROFILE.
    gates = {
        "rms_std_db": render["rms_std_db"] >= 3.5,
        "silence_ratio": render["silence_ratio"] <= 0.22,
        "low200_share": render["low200_share"] <= GT_SPECTRAL_PROFILE["low200_share"]["ceiling_fail"],
        "distinct_pcs": render["distinct_pcs"] >= 4,
    }
    out["v1_1_gates"] = gates
    out["passes_all_v1_1_gates"] = bool(all(gates.values()))
    if spectral_profile:
        lo = spectral_profile.get("low200_share") or {}
        hi = spectral_profile.get("high3000_share") or {}
        rms = spectral_profile.get("rms_std_db") or {}
        persona_gates = {
            "rms_std_db": render["rms_std_db"] >= float(rms.get("floor", 3.5)),
            "silence_ratio": render["silence_ratio"] <= 0.22,
            "low200_share": render["low200_share"] <= float(lo.get("ceiling_fail", GT_SPECTRAL_PROFILE["low200_share"]["ceiling_fail"])),
            "distinct_pcs": render["distinct_pcs"] >= 4,
        }
        out["persona_gates"] = persona_gates
        out["passes_all_persona_gates"] = bool(all(persona_gates.values()))
        out["persona_gate_rule"] = "additive persona verdict; v1.1 gates above are unchanged"
    if ref_path:
        ref = metrics_one(ref_path)
        out["reference"] = ref
        out["delta_vs_reference"] = {
            "rms_std_db": round(render["rms_std_db"] - ref["rms_std_db"], 3),
            "silence_ratio": round(render["silence_ratio"] - ref["silence_ratio"], 4),
            "low200_share": round(render["low200_share"] - ref["low200_share"], 4),
            "distinct_pcs": int(render["distinct_pcs"] - ref["distinct_pcs"]),
            "bpm": round(render["bpm"] - ref["bpm"], 3),
        }
        fidelity = reference_fidelity_gates(render, ref)
        out["reference_fidelity_gates"] = fidelity["gates"]
        out["passes_reference_fidelity"] = fidelity["passed"]
        out["reference_fidelity_tolerances"] = fidelity["tolerances"]
        out["reference_fidelity_rule"] = fidelity["rule"]
    return out


