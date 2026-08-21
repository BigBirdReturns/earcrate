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


