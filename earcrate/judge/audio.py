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


def drydeck_quality_gate(metrics: Dict[str, float], target_seconds: float) -> Dict[str, Any]:
    """Post-render gate with a user-audible coverage contract.

    A render is not successful merely because it is a correctly sized WAV. For a
    sketch of one minute or longer, most of the timeline must contain audible
    program material, the first music must arrive promptly, and the largest dead
    gap must stay bounded.
    """
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
        if rms_std < 1.6:
            failures.append("rms_std_db catastrophically low; render is effectively flat")
        elif rms_std < 3.0:
            warnings.append("rms_std_db below target 3.0; usable sketch but dynamics need more arc")
        if low200 < 0.38:
            failures.append("low200_share below catastrophic floor 0.38; floor authority missing")
        elif low200 < 0.46:
            warnings.append("low200_share below target 0.46; bass authority weak")
        if high3000 < 0.018:
            failures.append("high3000_share catastrophically low; likely cave/muffle")
        elif high3000 < 0.030:
            warnings.append("high3000_share below target 0.030; presence repair recommended")
    return {"passed": not failures, "failures": failures, "warnings": warnings, "metrics": metrics}

def stable_presence_restore(y: np.ndarray, sr: int) -> np.ndarray:
    """Small deterministic dry-deck presence repair before the final gate.

    This is not an effect. It is a conservative anti-cave corrective EQ: trim
    sub-rumble and low-mid buildup, recover a little upper-mid/high presence,
    and keep peak headroom. It is intentionally mild so a bad arrangement still
    fails the gate instead of being disguised.
    """
    if y.size < sr // 2:
        return y.astype(np.float32)
    x = np.nan_to_num(y.astype(np.float32), nan=0.0, posinf=0.0, neginf=0.0)
    n = int(2 ** math.ceil(math.log2(max(32, x.size))))
    spec = np.fft.rfft(x, n=n)
    freqs = np.fft.rfftfreq(n, 1 / sr)
    gain = np.ones_like(freqs, dtype=np.float32)
    gain[freqs < 32] *= 0.65
    gain[(freqs >= 180) & (freqs <= 520)] *= 0.88
    gain[(freqs >= 2400) & (freqs <= 9500)] *= 1.35
    gain[(freqs > 9500) & (freqs <= 14000)] *= 1.16
    repaired = np.fft.irfft(spec * gain, n=n)[:x.size].astype(np.float32)
    # Blend the repair with the original to avoid brittle highs.
    out = (0.50 * x + 0.50 * repaired).astype(np.float32)
    peak = float(np.max(np.abs(out))) if out.size else 0.0
    if peak > 0.94:
        out *= 0.94 / peak
    return np.nan_to_num(out.astype(np.float32), nan=0.0, posinf=0.0, neginf=0.0)

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
    gates = {
        "rms_std_db": render["rms_std_db"] >= 4.5,
        "silence_ratio": render["silence_ratio"] <= 0.22,
        "low200_share": render["low200_share"] >= 0.48,
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


