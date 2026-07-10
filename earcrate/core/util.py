from earcrate.core.deps import *
from earcrate.core.deps import _dt
def now_utc() -> str:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def ulidish() -> str:
    # Sortable enough for local journals without adding a dependency. Format intentionally ULID-like.
    millis = int(time.time() * 1000)
    return f"{millis:012x}{uuid.uuid4().hex[:14]}".upper()


def safe_name(s: str, fallback: str = "untitled") -> str:
    s = re.sub(r"[\\/:*?\"<>|\r\n\t]+", "_", s or "")
    s = re.sub(r"\s+", " ", s).strip(" ._")
    return s[:120] or fallback


def app_state_dir() -> Path:
    if os.name == "nt":
        root = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA") or str(Path.home())
        return Path(root) / APP_NAME
    return Path.home() / ".local" / "share" / APP_NAME


def choose_directory_dialog(current: str = "") -> Dict[str, Any]:
    """Open a local OS folder picker for the browser UI.

    The HTTP UI runs on localhost, so this executes in the Python process on the
    user's machine. If Tk is unavailable, the API returns an explicit recoverable
    error and the user can paste a path manually.
    """
    try:
        import tkinter as tk
        from tkinter import filedialog
    except Exception as exc:
        return {"ok": False, "path": "", "error": f"folder picker unavailable: {exc}"}
    initial = str(current or "").strip()
    if initial:
        try:
            initial_path = Path(initial).expanduser()
            if not initial_path.exists():
                initial_path = initial_path.parent
            initial = str(initial_path)
        except Exception:
            initial = str(Path.home())
    else:
        initial = str(Path.home())
    try:
        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        selected = filedialog.askdirectory(initialdir=initial, title="Choose folder")
        root.destroy()
        return {"ok": True, "path": selected or ""}
    except Exception as exc:
        return {"ok": False, "path": "", "error": f"folder picker failed: {exc}"}


def sha256_file(path: Path, chunk: int = 1024 * 1024) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def json_dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def arrangement_sha(arrangement: Dict[str, Any]) -> str:
    return sha256_text(json_dumps(arrangement))


def path_to_audio_url(path: str) -> str:
    return "/audio?path=" + urllib.parse.quote(path)


def fsync_append_jsonl(path: Path, record: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json_dumps(record) + "\n"
    with path.open("a", encoding="utf-8") as f:
        f.write(line)
        f.flush()
        os.fsync(f.fileno())


def array_to_blob(arr: np.ndarray) -> bytes:
    bio = io.BytesIO()
    np.save(bio, np.asarray(arr), allow_pickle=False)
    return bio.getvalue()


def blob_to_array(blob: Optional[bytes], dtype=np.float32) -> np.ndarray:
    if not blob:
        return np.array([], dtype=dtype)
    bio = io.BytesIO(blob)
    return np.load(bio, allow_pickle=False)


