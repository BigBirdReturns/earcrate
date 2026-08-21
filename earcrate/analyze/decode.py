from earcrate.core.deps import *
from earcrate.core.deps import _dt
from earcrate.core.wavinfo import *
from fractions import Fraction
import scipy.signal as scipy_signal
def run_cmd(args: List[str], timeout: Optional[int] = None, input_bytes: Optional[bytes] = None) -> subprocess.CompletedProcess:
    return subprocess.run(args, input=input_bytes, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout, check=False)


def ffprobe_json(path: Path) -> Dict[str, Any]:
    args = [
        "ffprobe", "-v", "error", "-show_streams", "-show_format", "-print_format", "json", str(path)
    ]
    cp = run_cmd(args, timeout=20)
    if cp.returncode != 0:
        raise RuntimeError(cp.stderr.decode("utf-8", "replace")[:500])
    return json.loads(cp.stdout.decode("utf-8", "replace") or "{}")


def decode_audio(path: Path, sr: int = DEFAULT_SAMPLE_RATE, start: Optional[float] = None, duration: Optional[float] = None) -> np.ndarray:
    args = ["ffmpeg", "-nostdin", "-v", "error"]
    if start is not None and start > 0:
        args += ["-ss", f"{start:.6f}"]
    args += ["-i", str(path), "-map", "0:a:0", "-vn", "-sn", "-dn"]
    if duration is not None and duration > 0:
        args += ["-t", f"{duration:.6f}"]
    args += ["-f", "f32le", "-ac", "1", "-ar", str(sr), "pipe:1"]
    cp = run_cmd(args, timeout=max(30, int((duration or 180) * 2)))
    if cp.returncode != 0:
        raise RuntimeError(cp.stderr.decode("utf-8", "replace")[:800])
    y = np.frombuffer(cp.stdout, dtype="<f4").astype(np.float32, copy=True)
    if y.size == 0:
        raise RuntimeError("ffmpeg decoded zero samples")
    return np.nan_to_num(y, nan=0.0, posinf=0.0, neginf=0.0)


def decoded_audio_sha256(path: Path, sr: int = DEFAULT_SAMPLE_RATE,
                         duration_hint: float = 0.0) -> str:
    """Hash the complete canonical mono PCM without materializing it in memory.

    Feature analysis may intentionally inspect only a prefix, but full-track stem
    artifacts must never be keyed by that prefix. FFmpeg's hash muxer decodes the
    whole source as mono float32 at the engine sample rate and returns one digest.
    """
    args = [
        "ffmpeg", "-nostdin", "-v", "error", "-i", str(path),
        "-map", "0:a:0", "-vn", "-sn", "-dn", "-ac", "1", "-ar", str(sr),
        "-c:a", "pcm_f32le", "-f", "hash", "-hash", "sha256", "pipe:1",
    ]
    timeout = max(120, int(float(duration_hint or 0.0) * 3.0))
    cp = run_cmd(args, timeout=timeout)
    if cp.returncode != 0:
        raise RuntimeError(cp.stderr.decode("utf-8", "replace")[:800])
    match = re.search(r"SHA256=([0-9a-fA-F]{64})", cp.stdout.decode("ascii", "replace"))
    if not match:
        raise RuntimeError("ffmpeg did not return a full-PCM SHA256")
    return match.group(1).lower()


def decode_audio_with_full_sha(path: Path, sr: int = DEFAULT_SAMPLE_RATE,
                               keep_seconds: Optional[float] = None,
                               duration_hint: float = 0.0) -> Tuple[np.ndarray, str]:
    """ONE canonical full-track decode that yields both the feature PCM and the
    full-track identity hash — replacing the analyze path's double decode.

    Analysis used to decode a bounded prefix for features and then run a second,
    complete ffmpeg pass (the hash muxer) just to compute ``pcm_sha``. Empirically
    verified: SHA256 over the decoder's own f32le byte stream is byte-identical to
    the hash-muxer digest for the same canonical decode (mono f32le at ``sr``,
    stream 0:a:0), so the second decode is pure waste. This streams a single full
    decode, hashing every byte as it arrives and retaining only the first
    ``keep_seconds`` of samples in memory for feature analysis.

    The digest is computed over the RAW decoder bytes (before nan_to_num), exactly
    like the hash muxer, so every existing ``pcm_sha`` — and the box's banked L3
    stem cache keyed by it — stays valid with zero invalidation."""
    args = [
        "ffmpeg", "-nostdin", "-v", "error", "-i", str(path),
        "-map", "0:a:0", "-vn", "-sn", "-dn",
        "-f", "f32le", "-ac", "1", "-ar", str(sr), "pipe:1",
    ]
    keep_bytes = None
    if keep_seconds is not None and keep_seconds > 0:
        keep_bytes = int(keep_seconds * sr) * 4
    deadline = time.monotonic() + max(120, int(float(duration_hint or 0.0) * 3.0))
    proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    stderr_chunks: List[bytes] = []
    t = threading.Thread(target=lambda: stderr_chunks.append(proc.stderr.read()), daemon=True)
    t.start()
    hasher = hashlib.sha256()
    prefix = bytearray()
    total = 0
    try:
        while True:
            chunk = proc.stdout.read(1 << 20)
            if not chunk:
                break
            hasher.update(chunk)
            total += len(chunk)
            if keep_bytes is None or len(prefix) < keep_bytes:
                room = len(chunk) if keep_bytes is None else keep_bytes - len(prefix)
                prefix += chunk[:room]
            if time.monotonic() > deadline:
                proc.kill()
                raise RuntimeError("ffmpeg decode timed out")
        rc = proc.wait(timeout=30)
    finally:
        with contextlib.suppress(Exception):
            proc.kill()
    t.join(timeout=5)
    if rc != 0:
        err = (stderr_chunks[0] if stderr_chunks else b"").decode("utf-8", "replace")
        raise RuntimeError(err[:800])
    if total == 0:
        raise RuntimeError("ffmpeg decoded zero samples")
    # Whole f32 samples only: a truncated trailing sample must not reach numpy.
    usable = len(prefix) - (len(prefix) % 4)
    y = np.frombuffer(bytes(prefix[:usable]), dtype="<f4").astype(np.float32, copy=True)
    return np.nan_to_num(y, nan=0.0, posinf=0.0, neginf=0.0), hasher.hexdigest()


def resample_or_fit(y: np.ndarray, target_len: int) -> np.ndarray:
    """Resample to an exact sample count with a REAL anti-aliased resampler.

    This sits in the render hot path (every varispeed clip goes through it), and it
    used to be np.interp — linear interpolation, i.e. a first-order-hold low-pass
    that shaved the top octave off EVERY layer before mixing. That was a primary
    cause of the 'presence-dark' renders the calibrated gate kept rejecting
    (high3000_share 0.02-0.07 vs the real-Girl-Talk 0.31 target): the mix was
    darkened at the source, and no downstream EQ could restore what was aliased
    away. Polyphase FIR (scipy, already a hard dep) preserves the top end;
    deterministic; exact length by construction of the final trim/pad."""
    if target_len <= 0:
        return np.zeros(0, dtype=np.float32)
    if y.size == 0:
        return np.zeros(target_len, dtype=np.float32)
    if y.size == target_len:
        return y.astype(np.float32, copy=False)
    if y.size < 32 or target_len < 32:
        # Too short for an FIR resampler's taps; linear is fine at this scale.
        x_old = np.linspace(0.0, 1.0, num=y.size, endpoint=False)
        x_new = np.linspace(0.0, 1.0, num=target_len, endpoint=False)
        return np.interp(x_new, x_old, y).astype(np.float32)
    frac = Fraction(int(target_len), int(y.size)).limit_denominator(1000)
    out = scipy_signal.resample_poly(y.astype(np.float64), frac.numerator, frac.denominator).astype(np.float32)
    if out.size < target_len:
        out = np.concatenate([out, np.zeros(target_len - out.size, dtype=np.float32)])
    return out[:target_len]


