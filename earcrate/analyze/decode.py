from earcrate.core.deps import *
from earcrate.core.deps import _dt
from earcrate.core.wavinfo import *
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
    args += ["-i", str(path)]
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


def resample_or_fit(y: np.ndarray, target_len: int) -> np.ndarray:
    if target_len <= 0:
        return np.zeros(0, dtype=np.float32)
    if y.size == 0:
        return np.zeros(target_len, dtype=np.float32)
    if y.size == target_len:
        return y.astype(np.float32, copy=False)
    x_old = np.linspace(0.0, 1.0, num=y.size, endpoint=False)
    x_new = np.linspace(0.0, 1.0, num=target_len, endpoint=False)
    return np.interp(x_new, x_old, y).astype(np.float32)


