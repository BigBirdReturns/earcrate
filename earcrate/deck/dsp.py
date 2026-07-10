from earcrate.core.deps import *
from earcrate.core.deps import _dt
from earcrate.analyze.decode import *
def equal_power_fade(length: int) -> Tuple[np.ndarray, np.ndarray]:
    if length <= 0:
        return np.array([], dtype=np.float32), np.array([], dtype=np.float32)
    theta = np.linspace(0, math.pi / 2, length, dtype=np.float32)
    return np.sin(theta), np.cos(theta)


def dj_fade_curves(length: int, curve: str = "equal_power") -> Tuple[np.ndarray, np.ndarray]:
    """Return incoming/outgoing DJ fade curves.

    Equal-power is the default because a linear crossfade audibly dips in the
    middle when two similarly loud signals overlap. S-curve is kept for more
    cut-like transitions where the center should hold both records briefly.
    """
    if length <= 0:
        z = np.array([], dtype=np.float32)
        return z, z
    x = np.linspace(0.0, 1.0, length, dtype=np.float32)
    if curve == "s_curve":
        inc = np.sin((math.pi / 2.0) * x) ** 2
        out = np.cos((math.pi / 2.0) * x) ** 2
    elif curve == "linear":
        inc = x
        out = 1.0 - x
    else:
        inc = np.sin((math.pi / 2.0) * x)
        out = np.cos((math.pi / 2.0) * x)
    return inc.astype(np.float32), out.astype(np.float32)


def fft_low_high_split(y: np.ndarray, sr: int, cutoff_hz: float = 170.0) -> Tuple[np.ndarray, np.ndarray]:
    """Split a short transition segment into low and high bands deterministically."""
    y = np.asarray(y, dtype=np.float32)
    if y.size < 32:
        z = np.zeros_like(y, dtype=np.float32)
        return z, y.copy()
    n = int(2 ** math.ceil(math.log2(max(32, y.size))))
    spec = np.fft.rfft(y, n=n)
    freqs = np.fft.rfftfreq(n, 1.0 / sr)
    # Smoothish logistic crossover avoids the nastiest brick-wall zippering.
    width = max(18.0, cutoff_hz * 0.18)
    lo_mask = 1.0 / (1.0 + np.exp(np.clip((freqs - cutoff_hz) / width, -60.0, 60.0)))
    low = np.fft.irfft(spec * lo_mask, n=n)[: y.size].astype(np.float32)
    return low, (y - low).astype(np.float32)


def dj_bass_swap_blend(old_seg: np.ndarray, in_seg: np.ndarray, sr: int, cutoff_hz: float, curve: str) -> np.ndarray:
    """Blend mids/highs across the transition, but transfer sub/kick ownership late."""
    n = min(old_seg.size, in_seg.size)
    if n <= 0:
        return old_seg.astype(np.float32)
    old_seg = old_seg[:n].astype(np.float32, copy=False)
    in_seg = in_seg[:n].astype(np.float32, copy=False)
    inc, out = dj_fade_curves(n, curve)
    old_lo, old_hi = fft_low_high_split(old_seg, sr, cutoff_hz)
    in_lo, in_hi = fft_low_high_split(in_seg, sr, cutoff_hz)
    x = np.linspace(0.0, 1.0, n, dtype=np.float32)
    # DJs normally do not run two basslines at full weight. Keep outgoing low
    # authority until the last quarter, then hand the floor to the incoming deck.
    lo_in = np.clip((x - 0.72) / 0.28, 0.0, 1.0)
    lo_out = np.clip((0.88 - x) / 0.28, 0.0, 1.0)
    return (old_hi * out + in_hi * inc + old_lo * lo_out + in_lo * lo_in).astype(np.float32)




def track_identity(item: Dict[str, Any]) -> str:
    """Stable-ish source identity for diversity scoring and two-world receipts."""
    artist = safe_name(str(item.get("artist") or ""), "unknown").lower()
    title = safe_name(str(item.get("title") or ""), "untitled").lower()
    path = str(item.get("path") or "").lower()
    if artist or title:
        return f"{artist}::{title}"
    return hashlib.sha1(path.encode("utf-8", "replace")).hexdigest()[:12]


def item_text_blob(item: Dict[str, Any]) -> str:
    parts = [item.get(k) for k in ("artist", "album", "title", "genre", "path", "year")]
    return " ".join(str(x or "") for x in parts).lower()


def world_query_match(item: Dict[str, Any], query: str) -> bool:
    q = str(query or "").strip().lower()
    if not q:
        return False
    blob = item_text_blob(item)
    terms = [t for t in re.split(r"[,;|]+|\s+", q) if t]
    return all(t in blob for t in terms)


def role_world_guess(item: Dict[str, Any]) -> str:
    role = str(item.get("role") or "full")
    vocal = float(item.get("vocal_likelihood") or 0.0)
    if role == "vocal" or vocal >= 0.65:
        return "voice"
    if role in ("drum_anchor", "bass", "harmony", "texture", "fx", "full"):
        return "bed"
    return "neutral"


def deck_group_for_role(role: str) -> str:
    role = str(role or "full")
    if role == "bass":
        return "low"
    if role in ("drum_anchor", "full"):
        return "rhythm"
    if role == "vocal":
        return "voice"
    return "texture"

def harmonic_relation_name(prev_key: Optional[int], next_key: Optional[int]) -> str:
    if prev_key is None or next_key is None:
        return "unknown"
    d = (int(next_key) - int(prev_key)) % 12
    if d == 0:
        return "same_key"
    if d in (7,):
        return "dominant"
    if d in (5,):
        return "subdominant"
    if d in (3, 4, 8, 9):
        return "relative_or_parallel"
    if d in (1, 11):
        return "chromatic_gear_shift"
    return "tension"


def tile_with_crossfade(y: np.ndarray, target_len: int, sr: int = DEFAULT_SAMPLE_RATE, fade_ms: int = 10) -> np.ndarray:
    if target_len <= 0:
        return np.zeros(0, dtype=np.float32)
    if y.size == 0:
        return np.zeros(target_len, dtype=np.float32)
    fade = max(1, min(int(sr * fade_ms / 1000), max(1, y.size // 8)))
    if y.size >= target_len:
        out = y[:target_len].copy()
        f_in, f_out = equal_power_fade(min(fade, target_len // 2))
        if f_in.size:
            out[:f_in.size] *= f_in
            out[-f_out.size:] *= f_out
        return out.astype(np.float32)
    chunks = []
    total = 0
    base = y.astype(np.float32, copy=False)
    while total < target_len + fade:
        chunks.append(base.copy())
        total += base.size - fade if chunks[:-1] else base.size
        if len(chunks) > 10000:
            break
    out = chunks[0]
    f_in, f_out = equal_power_fade(fade)
    for ch in chunks[1:]:
        merged = np.empty(out.size + ch.size - fade, dtype=np.float32)
        merged[: out.size - fade] = out[: out.size - fade]
        merged[out.size - fade : out.size] = out[-fade:] * f_out + ch[:fade] * f_in
        merged[out.size :] = ch[fade:]
        out = merged
        if out.size >= target_len:
            break
    return out[:target_len].astype(np.float32)


def simple_fft_filter(y: np.ndarray, sr: int, role: str, vocal_present: bool, section_has_bass: bool = False) -> np.ndarray:
    """Deterministic role carving with a single low-end owner per section.

    v0.4.0 implements Addendum A6: bass owns the low band when present; if no
    bass role is present, the drum/full anchor is allowed to keep the low end.
    """
    if y.size < 32:
        return y.astype(np.float32)
    n = int(2 ** math.ceil(math.log2(y.size)))
    spec = np.fft.rfft(y, n=n)
    freqs = np.fft.rfftfreq(n, 1 / sr)
    gain = np.ones_like(freqs, dtype=np.float32)
    if role == "bass":
        gain[freqs > 320] *= 0.55
        gain[freqs < 32] *= 0.35
    elif role == "drum_anchor":
        # Do not high-pass the anchor. It keeps authority when no bass owner exists.
        if section_has_bass:
            gain[(freqs >= 50) & (freqs <= 180)] *= 0.85
        if vocal_present:
            gain[(freqs >= 2000) & (freqs <= 5000)] *= 0.78
    elif role == "vocal":
        if section_has_bass:
            gain[freqs < 120] *= 0.18
        gain[freqs > 9500] *= 0.75
    elif role in ("harmony", "texture", "fx", "full"):
        if section_has_bass:
            gain[freqs < 180] *= 0.25
        if vocal_present and role in ("harmony", "texture", "full"):
            gain[(freqs >= 300) & (freqs <= 3400)] *= 0.78
    filtered = np.fft.irfft(spec * gain, n=n)[: y.size]
    return filtered.astype(np.float32)

def integrated_lufs_normalize(y: np.ndarray, sr: int, target_lufs: float = -14.0) -> np.ndarray:
    """Final full-mix loudness trim. Quiet sections remain quiet.

    Addendum A3 deletes per-section limiting and forbids upward compression of
    quiet sections. This function applies one full-mix gain trim and one final
    peak ceiling only.
    """
    if y.size < sr // 2:
        return y.astype(np.float32)
    y = np.nan_to_num(y.astype(np.float32), nan=0.0, posinf=0.0, neginf=0.0)
    try:
        meter = pyln.Meter(sr)
        loudness = meter.integrated_loudness(y.astype(np.float64))
        if np.isfinite(loudness):
            gain_db = target_lufs - float(loudness)
            # Trim down freely; allow only tiny upward makeup so breakdowns/cuts stay real.
            gain_db = min(gain_db, 1.5)
            y = (y.astype(np.float64) * (10 ** (gain_db / 20.0))).astype(np.float32)
    except Exception:
        pass
    peak = float(np.max(np.abs(y))) if y.size else 0.0
    if peak > 0.891:
        y = y * (0.891 / peak)
    return np.nan_to_num(y.astype(np.float32), nan=0.0, posinf=0.0, neginf=0.0)

def rms_value(y: np.ndarray) -> float:
    if y.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(np.square(y.astype(np.float32))) + 1e-12))


def normalize_layer_rms(y: np.ndarray, role: str) -> np.ndarray:
    """Bring clips into a role-sized gain window before arrangement gain.

    v0.2.8 does not trust source-track mastering. A hot brickwalled vocal and a
    quiet old indie drum loop should enter the summing bus at comparable role
    energy before their musical gain is applied. This is intentionally simple and
    deterministic, so a fixed seed still renders byte-identical arrangements.
    """
    if y.size == 0:
        return y.astype(np.float32)
    targets = {
        "drum_anchor": 0.095,
        "bass": 0.090,
        "vocal": 0.105,
        "harmony": 0.060,
        "texture": 0.050,
        "fx": 0.045,
        "full": 0.075,
    }
    target = float(targets.get(role, 0.065))
    current = rms_value(y)
    if current <= 1e-7:
        return y.astype(np.float32)
    scale = max(0.20, min(3.00, target / current))
    y = y.astype(np.float32) * scale
    peak = float(np.max(np.abs(y))) if y.size else 0.0
    if peak > 0.98:
        y *= 0.98 / peak
    return y.astype(np.float32)


def apply_edge_fades(y: np.ndarray, sr: int, fade_in: bool = True, fade_out: bool = True, fade_ms: int = 70) -> np.ndarray:
    """Apply entrance/exit fades without forcing every section boundary to zero."""
    if y.size <= 8:
        return y.astype(np.float32)
    fade = min(int(sr * fade_ms / 1000), y.size // 3)
    if fade <= 1:
        return y.astype(np.float32)
    f_in, f_out = equal_power_fade(fade)
    out = y.astype(np.float32, copy=True)
    if fade_in:
        out[:fade] *= f_in
    if fade_out:
        out[-fade:] *= f_out
    return out


def soft_limit_bus(y: np.ndarray, peak_ceiling: float = 0.891) -> np.ndarray:
    """Deterministic soft bus guard before final LUFS normalization."""
    if y.size == 0:
        return y.astype(np.float32)
    y = np.nan_to_num(y.astype(np.float32), nan=0.0, posinf=0.0, neginf=0.0)
    # Soft knee catches section piles without flattening everything like a hard clip.
    drive = 1.15
    y = np.tanh(y * drive) / np.tanh(drive)
    peak = float(np.max(np.abs(y)))
    if peak > peak_ceiling:
        y *= peak_ceiling / peak
    return y.astype(np.float32)


def tame_short_overlay(y: np.ndarray, sr: int, role: str, active_bars: int) -> np.ndarray:
    """Make ticks behave like ornaments, not foreground section changes.

    The early GT renderer normalized every layer by role and then applied a gain.
    A one-bar texture or full-mix stab could therefore arrive with the same local
    loudness as a real bed. v0.3.1 treats short non-vocal overlays as ticks: lower
    RMS target, hard peak cap, and a curved attack/release envelope.
    """
    if y.size == 0 or active_bars > 2:
        return y.astype(np.float32)
    role = str(role or "full")
    if role in ("drum_anchor", "bass", "harmony") and active_bars >= 2:
        return y.astype(np.float32)
    out = y.astype(np.float32, copy=True)
    # Vocal hooks may stay readable; texture/full/fx ticks must not dominate.
    if role == "vocal":
        target_rms = 0.060 if active_bars <= 1 else 0.075
        peak_cap = 0.55 if active_bars <= 1 else 0.62
    elif role in ("texture", "fx", "full"):
        target_rms = 0.024 if active_bars <= 1 else 0.034
        peak_cap = 0.24 if active_bars <= 1 else 0.32
    else:
        target_rms = 0.040 if active_bars <= 1 else 0.052
        peak_cap = 0.40 if active_bars <= 1 else 0.48
    current = rms_value(out)
    if current > 1e-8:
        out *= min(1.0, target_rms / current)
    peak = float(np.max(np.abs(out))) if out.size else 0.0
    if peak > peak_cap:
        out *= peak_cap / peak
    # Shape the tick so it does not click or arrive as a brick.
    fade = min(max(int(0.018 * sr), 64), out.size // 5)
    if fade > 1:
        attack = np.sin(np.linspace(0, np.pi / 2, fade, dtype=np.float32)) ** 2
        release = attack[::-1]
        out[:fade] *= attack
        out[-fade:] *= release
    return out.astype(np.float32)


def cap_overlay_gain_db(gain_db: float, role: str, active_bars: int) -> float:
    """Ceiling for short overlays after arrangement gain selection."""
    role = str(role or "full")
    g = float(gain_db)
    if active_bars <= 1:
        if role == "vocal":
            return min(g, -10.5)
        if role in ("texture", "fx", "full"):
            return min(g, -20.0)
        return min(g, -15.5)
    if active_bars == 2 and role in ("texture", "fx", "full"):
        return min(g, -17.5)
    if role == "bass":
        return min(g, -5.5)
    return g




def pitch_distance(a: int, b: int) -> int:
    d = (b - a) % 12
    if d > 6:
        d -= 12
    return int(d)
