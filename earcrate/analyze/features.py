from earcrate.core.deps import *
from earcrate.core.deps import _dt
from earcrate.core.config import *
def _clamp01(x: float) -> float:
    return 0.0 if x < 0.0 else (1.0 if x > 1.0 else float(x))


def _estimate_downbeats(y: np.ndarray, sr: int, beat_frames: np.ndarray) -> np.ndarray:
    if beat_frames.size < 4:
        return np.array([], dtype=np.float32)
    rms = librosa.feature.rms(y=y)[0]
    scores = []
    for phase in range(4):
        s = 0.0
        for bf in beat_frames[phase::4]:
            idx = min(len(rms) - 1, max(0, int(bf)))
            s += float(rms[idx])
        scores.append(s)
    phase = int(np.argmax(scores))
    return librosa.frames_to_time(beat_frames[phase::4], sr=sr).astype(np.float32)


def _vocal_likelihood(y: np.ndarray, sr: int) -> float:
    if y.size < 2048:
        return 0.0
    S = np.abs(librosa.stft(y, n_fft=2048, hop_length=1024))
    freqs = librosa.fft_frequencies(sr=sr, n_fft=2048)
    total = float(np.sum(S) + 1e-9)
    band = (freqs >= 300) & (freqs <= 3400)
    band_ratio = float(np.sum(S[band, :]) / total)
    flat = librosa.feature.spectral_flatness(S=S)[0]
    flat_var = float(np.var(flat))
    score = 0.75 * min(1.0, band_ratio / 0.55) + 0.25 * min(1.0, flat_var / 0.02)
    return float(max(0.0, min(1.0, score)))


def _estimate_sections(y: np.ndarray, sr: int, beats: np.ndarray, downbeats: np.ndarray) -> List[Dict[str, Any]]:
    sections = []
    duration = y.size / sr
    if downbeats.size >= 2:
        starts = list(downbeats[::4])
        if not starts or starts[0] > 0.1:
            starts = [0.0] + starts
        starts = [float(x) for x in starts if float(x) < duration]
        starts.append(float(duration))
    else:
        starts = list(np.arange(0, duration, 16.0)) + [duration]
    for i in range(len(starts) - 1):
        a, b = starts[i], starts[i + 1]
        if b - a < 1.0:
            continue
        seg = y[int(a * sr): int(b * sr)]
        e = float(np.sqrt(np.mean(seg ** 2))) if seg.size else 0.0
        if i == 0:
            label = "intro"
        elif i == len(starts) - 2:
            label = "outro"
        elif e > np.percentile(np.abs(y), 70) * 0.45:
            label = "chorus"
        else:
            label = "verse"
        sections.append({"start": round(a, 3), "end": round(b, 3), "label": label, "energy": round(e, 6)})
    return sections


def compute_pcm_features(y: np.ndarray, sr: int) -> Dict[str, Any]:
    """Pure DSP: PCM in, feature dict out. No DB, no self, no file I/O.

    Isolated at module level so it can run inside a worker process. This is the
    heavy part of analysis (onset, tempo, beats, key, loudness, sections)."""
    if float(np.max(np.abs(y))) < 1e-5:
        return {"bpm": 120.0, "bpm_confidence": 0.0, "beats": np.array([], dtype=np.float32),
                "downbeats": np.array([], dtype=np.float32), "key_root": 0, "key_mode": 1,
                "key_confidence": 0.0, "loudness_lufs": -70.0, "energy": 0.0,
                "vocal_likelihood": 0.0, "sections": []}
    onset_env = librosa.onset.onset_strength(y=y, sr=sr)
    tempo_val = librosa.feature.tempo(onset_envelope=onset_env, sr=sr, aggregate=np.median)
    bpm = float(np.atleast_1d(tempo_val)[0])
    while bpm < 70:
        bpm *= 2
    while bpm > 180:
        bpm /= 2
    _, beat_frames = librosa.beat.beat_track(y=y, sr=sr, onset_envelope=onset_env, units="frames", trim=False)
    beat_times = librosa.frames_to_time(beat_frames, sr=sr).astype(np.float32)
    if beat_times.size >= 8:
        intervals = np.diff(beat_times)
        bpm_conf = float(max(0.0, min(1.0, 1.0 - (np.std(intervals) / (np.mean(intervals) + 1e-9)))))
    else:
        bpm_conf = 0.2
    downbeats = _estimate_downbeats(y, sr, beat_frames)
    chroma = librosa.feature.chroma_stft(y=y, sr=sr)
    key_root, key_mode, key_conf = krumhansl_key(chroma)
    energy = float(np.sqrt(np.mean(y ** 2)))
    loudness = None
    with contextlib.suppress(Exception):
        meter = pyln.Meter(sr)
        loudness = float(meter.integrated_loudness(y.astype(np.float64)))
    if loudness is None or not np.isfinite(loudness):
        loudness = float(20 * np.log10(max(1e-9, energy)))
    vocal_like = _vocal_likelihood(y, sr)
    sections = _estimate_sections(y, sr, beat_times, downbeats)
    return {"bpm": bpm, "bpm_confidence": bpm_conf, "beats": beat_times, "downbeats": downbeats,
            "key_root": int(key_root), "key_mode": int(key_mode), "key_confidence": float(key_conf),
            "loudness_lufs": loudness, "energy": energy, "vocal_likelihood": vocal_like, "sections": sections}


def analyze_file_worker(job: Dict[str, Any]) -> Dict[str, Any]:
    """Process-pool entry point. Decodes + computes features + writes the npz cache.

    Returns a plain, picklable result dict. All heavy CPU work happens here so N
    of these run across cores in parallel; the parent process only does DB writes."""
    try:
        path = Path(job["path"])
        sr = int(job["sr"])
        max_sec = int(job["max_sec"])
        cache_path = Path(job["cache_path"])
        duration = float(job.get("duration") or 0)
        decode_dur = min(duration, max_sec) if duration > 0 else max_sec
        y = decode_audio(path, sr, duration=decode_dur)
        if y.size > sr * max_sec:
            y = y[: sr * max_sec]
        pcm = pcm_sha256(y)  # L0 sound identity, computed from the canonical PCM we already decoded
        feats = compute_pcm_features(y, sr)
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        np.savez_compressed(
            cache_path,
            bpm=np.float32(feats["bpm"]), bpm_confidence=np.float32(feats["bpm_confidence"]),
            key_root=np.int16(feats["key_root"]), key_mode=np.int16(feats["key_mode"]),
            key_confidence=np.float32(feats["key_confidence"]), loudness_lufs=np.float32(feats["loudness_lufs"]),
            energy=np.float32(feats["energy"]), beats=feats["beats"].astype(np.float32),
            downbeats=feats["downbeats"].astype(np.float32),
            sections_json=json.dumps(feats["sections"], ensure_ascii=False),
            vocal_likelihood=np.float32(feats["vocal_likelihood"]),
            pcm_sha=pcm,
        )
        return {"file_id": job["file_id"], "sha256": job.get("sha256"), "pcm_sha": pcm, "ok": True, "features": {
            "bpm": feats["bpm"], "bpm_confidence": feats["bpm_confidence"], "key_root": feats["key_root"],
            "key_mode": feats["key_mode"], "key_confidence": feats["key_confidence"], "loudness_lufs": feats["loudness_lufs"],
            "energy": feats["energy"], "beats": feats["beats"].astype(np.float32).tobytes(),
            "downbeats": feats["downbeats"].astype(np.float32).tobytes(),
            "sections": feats["sections"], "vocal_likelihood": feats["vocal_likelihood"]}}
    except Exception as exc:
        return {"file_id": job.get("file_id"), "ok": False, "error": str(exc)[:500], "path": str(job.get("path"))}


def warmup_dsp() -> None:
    """Pay librosa's numba JIT compilation cost once, up front, on silence.

    Without this the FIRST real analyze or render call blocks ~5-10s while numba
    compiles, with no progress shown, and looks like a freeze."""
    with contextlib.suppress(Exception):
        y = (np.random.default_rng(0).standard_normal(22050) * 0.01).astype(np.float32)
        oe = librosa.onset.onset_strength(y=y, sr=22050)
        librosa.feature.tempo(onset_envelope=oe, sr=22050)
        librosa.beat.beat_track(y=y, sr=22050, onset_envelope=oe, units="frames", trim=False)
        librosa.feature.chroma_stft(y=y, sr=22050)
        librosa.feature.spectral_flatness(S=np.abs(librosa.stft(y)))
        librosa.effects.time_stretch(y[:11025], rate=1.05)
        librosa.effects.pitch_shift(y[:11025], sr=22050, n_steps=1)


