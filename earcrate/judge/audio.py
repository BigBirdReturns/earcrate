from earcrate.core.deps import *
from earcrate.core.deps import _dt
from earcrate.ear.readiness import *
def drydeck_metrics(y: np.ndarray, sr: int) -> Dict[str, float]:
    """Post-render audio metrics with an absolute audible-coverage floor.

    v0.5.16 measured silence relative to the median frame RMS. That let a nearly
    empty two-minute file pass because the median was microscopic noise, not music.
    This gate now asks the user-facing question: how much of the render contains
    audible program material above a real floor?
    """
    y = np.nan_to_num(y.astype(np.float32), nan=0.0, posinf=0.0, neginf=0.0)
    if y.size == 0:
        return {
            "duration_s": 0.0, "rms_std_db": 0.0, "silence_ratio": 1.0,
            "active_coverage_ratio": 0.0, "audible_seconds": 0.0,
            "first_audible_s": None, "last_audible_s": None,
            "largest_silence_gap_s": 0.0, "low200_share": 0.0,
            "high3000_share": 0.0, "peak": 0.0, "global_rms": 0.0,
            "audible_rms_floor": 0.0,
        }
    frame = max(512, int(5.0 * sr))
    vals = []
    for i in range(0, max(1, y.size - frame + 1), frame):
        vals.append(rms_value(y[i:i+frame]))
    vals_db = 20 * np.log10(np.asarray(vals, dtype=np.float64) + 1e-9)

    hop_s = 0.05
    hop = max(512, int(hop_s * sr))
    rms_frames = []
    for i in range(0, max(1, y.size - hop), hop):
        rms_frames.append(rms_value(y[i:i+hop]))
    arr = np.asarray(rms_frames, dtype=np.float64)
    peak = float(np.max(np.abs(y))) if y.size else 0.0
    global_rms = rms_value(y)
    # Absolute plus relative floor. This catches the exact failure where the file
    # contains tiny dither/noise for 110 seconds and nine seconds of real material.
    audible_floor = max(1e-4, peak * 0.010, global_rms * 0.18)
    active = arr >= audible_floor if arr.size else np.zeros(0, dtype=bool)
    active_coverage = float(np.mean(active)) if active.size else 0.0
    silence_ratio = 1.0 - active_coverage
    if active.size and bool(np.any(active)):
        active_idx = np.flatnonzero(active)
        first_audible_s = float(active_idx[0] * hop_s)
        last_audible_s = float((active_idx[-1] + 1) * hop_s)
        # Longest consecutive inactive span in seconds.
        longest = 0
        cur = 0
        for flag in active:
            if flag:
                longest = max(longest, cur)
                cur = 0
            else:
                cur += 1
        longest = max(longest, cur)
        largest_gap_s = float(longest * hop_s)
    else:
        first_audible_s = None
        last_audible_s = None
        largest_gap_s = float(y.size / max(1, sr))
    audible_seconds = float(active_coverage * y.size / max(1, sr))

    n_fft = 4096
    hop_len = 2048
    try:
        stft = np.abs(librosa.stft(y, n_fft=n_fft, hop_length=hop_len)) ** 2
        freqs = librosa.fft_frequencies(sr=sr, n_fft=n_fft)
        total = float(np.sum(stft) + 1e-12)
        low200 = float(np.sum(stft[freqs < 200]) / total)
        high3000 = float(np.sum(stft[freqs > 3000]) / total)
    except Exception:
        low200 = 0.0
        high3000 = 0.0
    return {
        "duration_s": float(y.size / max(1, sr)),
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
    """(low200_share, high3000_share, total_power) with the SAME spectral
    definition the post-render gate uses (drydeck_metrics: |STFT 4096/2048|^2),
    so a finish pass that targets these shares is targeting the judge's ruler."""
    stft = np.abs(librosa.stft(x, n_fft=4096, hop_length=2048)) ** 2
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


def stable_presence_restore(y: np.ndarray, sr: int) -> np.ndarray:
    """MEASURED, target-directed finishing EQ toward the real-Girl-Talk balance.

    The previous version was a FIXED shelf (always -mud/+2.2x presence, blind to
    the mix in hand) and it demonstrably under-corrected: the calibrated gate
    kept measuring high3000_share ~0.067 against the 0.30 target and rejecting
    every render — the box's re-verification of #4 named the treble-dead chain
    the SOLE remaining blocker. This version closes the loop: it MEASURES the
    mix's low200/high3000 shares with the exact spectral ruler the gate uses,
    solves the shelf gains that move those shares to the ground-truth targets
    (low200 ~0.20 ceiling, high3000 ~0.30), applies them with smooth band edges,
    re-measures, and iterates (<=3 passes, cumulative gain bounded to +/-14 dB).

    Still honest: a pure time-invariant linear EQ per pass — it cannot
    manufacture material, coverage, or dynamics (rms_std_db is untouched by
    construction), so an arrangement with dead air or flat energy still fails
    the gate. It only stops TONE from vetoing otherwise-good material."""
    if y.size < sr // 2:
        return y.astype(np.float32)
    x = np.nan_to_num(y.astype(np.float32), nan=0.0, posinf=0.0, neginf=0.0)
    prof = GT_SPECTRAL_PROFILE
    high_target = float(prof["high3000_share"].get("target", 0.30))
    low_target = 0.20  # real-GT mean; ceiling_warn is 0.34
    GAIN_LIMIT = 10.0 ** (14.0 / 20.0)  # cumulative +/-14 dB safety bound
    cum_low = 1.0
    cum_high = 1.0
    for _ in range(3):
        low, high, _tot = _band_shares(x, sr)
        low_ok = low <= float(prof["low200_share"].get("ceiling_warn", 0.34))
        high_ok = high >= float(prof["high3000_share"].get("floor_warn", 0.15))
        if low_ok and high_ok:
            break
        # Solve the power multiplier that moves each band's share to its target,
        # holding the other bands fixed: share' = m*B / (T - B + m*B).
        def _solve(share: float, target: float) -> float:
            share = min(max(share, 1e-6), 1.0 - 1e-6)
            target = min(max(target, 1e-6), 1.0 - 1e-6)
            return (target * (1.0 - share)) / (share * (1.0 - target))
        m_low = _solve(low, low_target) if not low_ok else 1.0
        m_high = _solve(high, high_target) if not high_ok else 1.0
        a_low = math.sqrt(max(1e-6, m_low))
        a_high = math.sqrt(max(1e-6, m_high))
        # Bound the CUMULATIVE correction; a mix so broken it needs more than
        # +/-14 dB of shelf should fail the gate, not be EQ'd into a pass.
        a_low = max(1.0 / GAIN_LIMIT / cum_low, min(a_low, 1.0))          # low only ever cut
        a_high = max(1.0, min(a_high, GAIN_LIMIT / cum_high))             # high only ever boosted
        if abs(a_low - 1.0) < 1e-3 and abs(a_high - 1.0) < 1e-3:
            break
        cum_low *= a_low
        cum_high *= a_high
        n = int(2 ** math.ceil(math.log2(max(32, x.size))))
        spec = np.fft.rfft(x, n=n)
        freqs = np.fft.rfftfreq(n, 1 / sr)
        gain = _smooth_band_gain(freqs, 0.0, 200.0, a_low) * _smooth_band_gain(freqs, 3000.0, None, a_high)
        gain[freqs < 30] *= 0.60  # sub-rumble never helps; always tame it
        x = np.fft.irfft(spec * gain, n=n)[: x.size].astype(np.float32)
    peak = float(np.max(np.abs(x))) if x.size else 0.0
    if peak > 0.94:
        x *= 0.94 / peak
    return np.nan_to_num(x.astype(np.float32), nan=0.0, posinf=0.0, neginf=0.0)

def judge_audio_file(path: Path, ref_path: Optional[Path] = None) -> Dict[str, Any]:
    """Reference-comparison harness from Addendum A0."""
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
    return out


