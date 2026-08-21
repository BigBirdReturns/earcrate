from earcrate.core.deps import *
from earcrate.deck.harmony import krumhansl_key
"""Step 2 of the transition sequence: per-beat MUSICAL STATE, not one label/track.

The anchor+technique foundation (earcrate/plan/transitions.py) had to disable the
powerful techniques (real bass swap, spectrally-safe double drop, phrase-aware
vocal handling) because it only had scalar signals -- one key, one vocal_likelihood
per track. This module produces the beat-synchronous features those techniques and
the role-collision model need:

  * ROLE ACTIVITY per beat -- kick / snare / hat / bass / vocal / lead -- from
    band-limited spectral flux (transient roles) and sustained band energy (bass),
    normalized per-track so they are comparable and bounded.
  * GROOVE -- onset histogram over the 16th-note bar grid, swing ratio,
    syncopation, and half/double-time probability -- because matching BPM and
    phase does NOT match groove.
  * LOCAL HARMONY -- windowed key + tonal confidence -- because one key per track
    is too coarse: tracks modulate and go atonal under percussion.
  * NOVELTY -- beat-synced spectral flux -- so event COLLISIONS (a vocal pickup on
    an unrelated fill) can be distinguished from deliberate alignment.

DETERMINISM: fixed n_fft/hop, no clock, no RNG -- same audio -> byte-identical
features (rounded to _NDIGITS). Everything is derived from ONE STFT plus the beat
grid the analyzer already computes; this never re-runs beat tracking.

VERIFIED IN-CLOUD: this is librosa/numpy/scipy (no GPU, no demucs), so its
behaviour is gate-checked on synthetic signals here -- a kick loop reads high kick
/ low vocal activity, a pure tone reads high tonal_confidence, a swung pattern
reads swing != straight. The DESKTOP runs it on real audio; nothing here needs a
4060.
"""

_NDIGITS = 6
_N_FFT = 2048
_HOP = 512

# Frequency bands (Hz) for role attribution. Deliberately coarse and fixed so the
# result is reproducible; these are PROXIES for stems, not a substitute for real
# separation, but they are enough to tell a kick transient from a sustained bass
# from a vocal from a hat.
_BANDS = {
    "kick": (40.0, 120.0),      # low transient
    "bass": (60.0, 250.0),      # sustained low
    "snare": (150.0, 400.0),    # body of the backbeat
    "vocal": (300.0, 3000.0),   # sung/spoken range
    "lead": (1500.0, 5000.0),   # melodic presence
    "hat": (8000.0, 16000.0),   # high transient
}


def _clamp01(x):
    return 0.0 if x < 0.0 else (1.0 if x > 1.0 else float(x))


def _r(x):
    return round(float(x), _NDIGITS)


def _norm01(arr):
    """Per-song relative normalization to [0,1] (max -> 1). Deterministic; an
    all-zero track stays all-zero rather than dividing by zero."""
    a = np.asarray(arr, dtype=np.float64)
    peak = float(np.max(a)) if a.size else 0.0
    if peak <= 1e-12:
        return np.zeros_like(a)
    return np.clip(a / peak, 0.0, 1.0)


def _beat_frames(beats, sr, n_frames):
    """Frame index of each beat time (hop grid), clamped into range."""
    if beats is None or len(beats) == 0:
        return np.zeros(0, dtype=np.int64)
    t = np.asarray(beats, dtype=np.float64)
    fr = np.round(t * sr / _HOP).astype(np.int64)
    return np.clip(fr, 0, max(0, n_frames - 1))


def _aggregate_per_beat(curve, bframes, reducer="mean"):
    """Reduce a per-frame curve to one value per beat interval [beat_i, beat_i+1)."""
    out = []
    n = len(bframes)
    for i in range(n - 1):
        f0, f1 = int(bframes[i]), int(bframes[i + 1])
        if f1 <= f0:
            f1 = min(f0 + 1, len(curve))
        seg = curve[f0:f1]
        if seg.size == 0:
            out.append(0.0)
        elif reducer == "max":
            out.append(float(np.max(seg)))
        else:
            out.append(float(np.mean(seg)))
    return np.asarray(out, dtype=np.float64)


# Transient roles score by their SHARE of broadband onset flux (a hit); sustained
# roles score by their SHARE of per-frame spectral energy (a held sound). Using a
# SHARE (not a per-track max) is what makes a pure 220 Hz tone read as bass, not as
# a phantom kick: a near-silent band contributes a near-zero share instead of being
# normalized up into spurious activity.
_TRANSIENT_ROLES = ("kick", "snare", "hat")
_SUSTAINED_ROLES = ("bass", "vocal", "lead")


def beat_activity(y, sr, beats):
    """Per-beat role activity in [0,1], as a SHARE of that beat's energy.

    Transient roles (kick/snare/hat) = share of broadband positive flux (onset
    energy) in their band, reduced by max over the beat. Sustained roles
    (bass/vocal/lead) = share of spectral energy in their band, reduced by mean;
    vocal and lead are additionally weighted by tonalness (1 - spectral flatness)
    so a noise burst does not read as voice. Returns {role: [value per beat]}."""
    y = np.nan_to_num(np.asarray(y, dtype=np.float32))
    beats = np.asarray(beats, dtype=np.float64)
    if y.size < _N_FFT or beats.size < 2:
        return {r: [] for r in _BANDS}
    S = np.abs(librosa.stft(y, n_fft=_N_FFT, hop_length=_HOP)) ** 2
    freqs = librosa.fft_frequencies(sr=sr, n_fft=_N_FFT)
    n_frames = S.shape[1]
    bframes = _beat_frames(beats, sr, n_frames)
    flat = librosa.feature.spectral_flatness(S=np.sqrt(S))[0]
    tonal = np.clip(1.0 - flat, 0.0, 1.0)
    total_energy = S.sum(axis=0) + 1e-9
    # Loudness anchor: a band ONSET is "real" only when its flux is large relative
    # to the track's average energy. Dividing by a share of flux instead lets a
    # near-silent (steady) signal produce a phantom onset, because 0/0 -> noise.
    energy_scale = float(np.mean(total_energy)) + 1e-9
    out = {}
    for role, (lo, hi) in _BANDS.items():
        mask = (freqs >= lo) & (freqs < hi)
        band = S[mask, :].sum(axis=0) if mask.any() else np.zeros(n_frames)
        if role in _TRANSIENT_ROLES:
            band_flux = np.maximum(0.0, np.diff(band, prepend=band[:1]))
            mag = np.clip(band_flux / energy_scale, 0.0, 1.0)  # absolute onset strength
            per_beat = _aggregate_per_beat(mag, bframes, "max")
        else:  # sustained: share of held energy, tonally weighted for voice/lead
            share = np.clip(band / total_energy, 0.0, 1.0)
            if role in ("vocal", "lead"):
                share = share * tonal
            per_beat = _aggregate_per_beat(share, bframes, "mean")
        out[role] = [_r(_clamp01(v)) for v in per_beat]
    return out


def beat_novelty(y, sr, beats):
    """Per-beat spectral-flux novelty, per-track normalized -- how much the sound
    CHANGES at each beat (fills, entrances, drops)."""
    y = np.nan_to_num(np.asarray(y, dtype=np.float32))
    beats = np.asarray(beats, dtype=np.float64)
    if y.size < _N_FFT or beats.size < 2:
        return []
    onset = librosa.onset.onset_strength(y=y, sr=sr, hop_length=_HOP)
    bframes = _beat_frames(beats, sr, onset.shape[0])
    per_beat = _aggregate_per_beat(onset, bframes, "max")
    return [_r(v) for v in _norm01(per_beat)]


def groove_descriptor(y, sr, beats, downbeats):
    """Groove that a BPM/phase match cannot capture.

    onset_histogram : 16 bins over the bar (16th-note grid), the rhythmic
                      fingerprint; deterministic, sums to 1 (or all-zero).
    swing_ratio     : centre of onset mass within each beat's two 8th-note halves
                      (0.5 straight; >0.5 late/swung).
    syncopation     : share of onset energy landing OFF the beat vs on it.
    halftime_prob / doubletime_prob : onset-autocorrelation support at 2x / 0.5x
                      the beat period (a track can imply half/double feel).
    """
    y = np.nan_to_num(np.asarray(y, dtype=np.float32))
    beats = np.asarray(beats, dtype=np.float64)
    empty = {"onset_histogram": [0.0] * 16, "swing_ratio": 0.5, "syncopation": 0.0,
             "halftime_prob": 0.0, "doubletime_prob": 0.0}
    if y.size < _N_FFT or beats.size < 3:
        return empty
    onset = librosa.onset.onset_strength(y=y, sr=sr, hop_length=_HOP)
    times = librosa.frames_to_time(np.arange(onset.shape[0]), sr=sr, hop_length=_HOP)
    db = np.asarray(downbeats, dtype=np.float64)
    # bar span: consecutive downbeats, else 4 beats
    if db.size >= 2:
        bar_starts = db
    else:
        bar_starts = beats[::4]
    hist = np.zeros(16, dtype=np.float64)
    for i in range(len(bar_starts) - 1):
        a, b = bar_starts[i], bar_starts[i + 1]
        if b - a <= 1e-6:
            continue
        m = (times >= a) & (times < b)
        if not m.any():
            continue
        phase = (times[m] - a) / (b - a)  # 0..1 across the bar
        binned = np.clip((phase * 16).astype(int), 0, 15)
        for bi, w in zip(binned, onset[m]):
            hist[bi] += float(w)
    total = float(hist.sum())
    hist_n = (hist / total) if total > 1e-9 else hist
    # on-beat bins are 0,4,8,12 (the quarter notes); the rest are off.
    on = float(hist_n[[0, 4, 8, 12]].sum())
    syncopation = _clamp01(1.0 - on)
    # swing: mass in the 2nd 8th of each beat (bins 2-3, 6-7, 10-11, 14-15) vs 1st.
    first8 = float(hist_n[[0, 1, 4, 5, 8, 9, 12, 13]].sum())
    second8 = float(hist_n[[2, 3, 6, 7, 10, 11, 14, 15]].sum())
    swing = 0.5 if (first8 + second8) < 1e-9 else _clamp01(second8 / (first8 + second8))
    # half/double feel via onset autocorrelation at the beat lag and its 2x/0.5x.
    ht, dt = _tempo_feel(onset, beats, sr)
    return {"onset_histogram": [_r(v) for v in hist_n], "swing_ratio": _r(swing),
            "syncopation": _r(syncopation), "halftime_prob": _r(ht),
            "doubletime_prob": _r(dt)}


def _tempo_feel(onset, beats, sr):
    beats = np.asarray(beats, dtype=np.float64)
    if beats.size < 3 or onset.size < 8:
        return 0.0, 0.0
    beat_period_s = float(np.median(np.diff(beats)))
    if beat_period_s <= 0:
        return 0.0, 0.0
    lag = beat_period_s * sr / _HOP
    o = onset - float(np.mean(onset))
    ac = np.correlate(o, o, mode="full")[o.size - 1:]
    if ac[0] <= 1e-9:
        return 0.0, 0.0
    ac = ac / ac[0]

    def at(l):
        li = int(round(l))
        return float(ac[li]) if 0 < li < ac.size else 0.0
    base = max(1e-6, at(lag))
    half = _clamp01(at(lag * 2.0) / base)     # strong support at 2x beat -> halftime feel
    doub = _clamp01(at(lag * 0.5) / base)     # support at 0.5x beat -> doubletime feel
    return half, doub


def local_harmony(y, sr, beats, window_s=6.0, hop_s=3.0):
    """Windowed key + tonal confidence -- one key per track is too coarse.

    Returns a list of {start_s, key_root, key_mode, tonal_confidence}. Confidence
    is the Krumhansl correlation strength: low under drums/atonal passages, high
    under a clear tonal centre -- which lets a transition avoid exposing harmony
    exactly where the track has none."""
    y = np.nan_to_num(np.asarray(y, dtype=np.float32))
    if y.size < sr:
        return []
    win = int(window_s * sr)
    hop = max(1, int(hop_s * sr))
    out = []
    for start in range(0, max(1, y.size - sr), hop):
        seg = y[start:start + win]
        if seg.size < sr:
            break
        try:
            chroma = librosa.feature.chroma_cqt(y=seg, sr=sr)
        except Exception:
            chroma = librosa.feature.chroma_stft(y=seg, sr=sr)
        v = np.mean(chroma, axis=1)
        root, mode, conf = krumhansl_key(chroma)
        out.append({"start_s": _r(start / sr), "key_root": int(root),
                    "key_mode": int(mode), "tonal_confidence": _r(_clamp01(conf))})
    return out


def beat_state_features(y, sr, beats, downbeats):
    """Assemble the beat-synchronous state for one track: role activity, novelty,
    groove, and local harmony. Deterministic and JSON-serializable -- suitable to
    store per file OR compute on demand for the two tracks in a transition."""
    activity = beat_activity(y, sr, beats)
    novelty = beat_novelty(y, sr, beats)
    groove = groove_descriptor(y, sr, beats, downbeats)
    harmony = local_harmony(y, sr, beats)
    n_beats = max(0, len(np.asarray(beats)) - 1)
    return {
        "n_beats": int(n_beats),
        "activity": activity,       # {role: [per-beat]}
        "novelty": novelty,         # [per-beat]
        "groove": groove,           # dict
        "local_harmony": harmony,   # [ {start_s, key_root, key_mode, tonal_confidence} ]
        "roles": list(_BANDS.keys()),
    }
