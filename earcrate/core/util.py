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


def visible_app_dir() -> Path:
    """A VISIBLE, portable home for the single app-global pointer file — never a
    hidden AppData/dotfolder nest. Order: EARCRATE_HOME override, then the
    directory that HOLDS the earcrate package (portable, next to START_HERE),
    then the user profile as a last resort. The pointer is one small file; it
    does not create a cluttered top-level folder.

    The anchor must be IDENTICAL across every entry point so that
    `python -m earcrate`, the `earcrate` console script, and the frozen
    single-file build all resolve the SAME workspace pointer. The previous
    implementation anchored to ``sys.modules['__main__'].__file__``: that is the
    package ``__main__.py`` under ``-m`` but the console-script wrapper in a
    ``bin/`` dir under the CLI, so the two entry points wrote the pointer to
    DIFFERENT files and could not see each other's workspace. It also fell back
    to the current working directory, making the pointer cwd-dependent. Both
    of those are non-deterministic and are the bug this function fixes.

    Instead we anchor to the location of THIS module — every entry point imports
    it, so the result does not depend on which launcher started the process, nor
    on the cwd."""
    env = os.environ.get("EARCRATE_HOME")
    if env:
        return Path(env).expanduser()
    cand: Optional[Path] = None
    try:
        here = Path(__file__).resolve()
        # Normal package layout: .../<root>/earcrate/core/util.py, where <root>
        # is the portable folder next to START_HERE. In the concatenated
        # single-file build this module IS the running script (name != util.py),
        # so we sit beside that single file instead.
        if here.name == "util.py" and here.parent.name == "core" and len(here.parents) >= 3:
            cand = here.parents[2]
        else:
            cand = here.parent
    except Exception:
        cand = None
    if cand is None:
        cand = Path.home()
    try:
        cand.mkdir(parents=True, exist_ok=True)
        probe = cand / "earcrate_write_probe.tmp"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return cand
    except Exception:
        return Path.home()


def pointer_search_dirs() -> List[Path]:
    """Every directory the workspace pointer may legitimately live in, in
    priority order. Writing still goes to visible_app_dir(); READING must scan
    all of these, because the pointer's location depends on how the process was
    started (`python dist/earcrate.py` anchors on the dist file; a driver script
    that imports the package anchors on the SCRIPT — which is how a standalone
    script once silently resolved a stale legacy AppData workspace instead of
    the configured one)."""
    env = os.environ.get("EARCRATE_HOME")
    if env:
        # An explicit EARCRATE_HOME is an override, not a hint: it is the ONLY
        # place the pointer may live. No fallback scan — falling through to
        # other locations would let a stray pointer elsewhere hijack a
        # deliberately sandboxed instance (the gates depend on this contract).
        return [Path(env).expanduser()]
    dirs: List[Path] = []
    main = sys.modules.get("__main__")
    mf = getattr(main, "__file__", None) if main is not None else None
    if mf:
        with contextlib.suppress(Exception):
            dirs.append(Path(mf).resolve().parent)
    with contextlib.suppress(Exception):
        dirs.append(Path.cwd())
    # The directory of the code itself: repo root in package mode, dist/ in a
    # single-file build. This is what makes an importing driver script agree
    # with the CLI entry point run from the same tree.
    with contextlib.suppress(Exception):
        here = Path(__file__).resolve()
        if here.parent.name == "core" and here.parent.parent.name == "earcrate":
            dirs.append(here.parents[2])
        else:
            dirs.append(here.parent)
    dirs.append(Path.home())
    out: List[Path] = []
    seen = set()
    for d in dirs:
        key = os.path.normcase(str(d))
        if key not in seen:
            seen.add(key)
            out.append(d)
    return out


def sibling_workspace(music_folder: str) -> str:
    """Derive the default workspace as a VISIBLE SIBLING next to the music folder
    (INV-1 forbids it living inside the music folder). Name is derived from the
    music folder, never hardcoded. e.g. '.../The Sample Factory' ->
    '.../The Sample Factory — EarCrate'."""
    music = Path(str(music_folder or "")).expanduser().resolve()
    base = safe_name(music.name, "Library")
    return str(music.parent / f"{base} — EarCrate")


def _normalize_initial_dir(current: str) -> str:
    initial = str(current or "").strip()
    if initial:
        try:
            initial_path = Path(initial).expanduser()
            if not initial_path.exists():
                initial_path = initial_path.parent
            return str(initial_path)
        except Exception:
            return str(Path.home())
    return str(Path.home())


def choose_directory_dialog(current: str = "") -> Dict[str, Any]:
    """Open a local OS folder picker for the browser UI.

    The HTTP UI runs on localhost, so this executes in the Python process on the
    user's machine. On Windows the native FolderBrowserDialog is used via
    PowerShell with a TopMost owner — a Tk dialog spawned from an HTTP worker
    thread routinely opens BEHIND the browser and looks like a dead button.
    Tk remains the cross-platform fallback; if neither works the API returns an
    explicit recoverable error and the user can paste a path manually.
    """
    initial = _normalize_initial_dir(current)
    if os.name == "nt":
        script = (
            "Add-Type -AssemblyName System.Windows.Forms; "
            "$f = New-Object System.Windows.Forms.FolderBrowserDialog; "
            "$f.Description = 'Choose folder'; $f.ShowNewFolderButton = $true; "
            f"$f.SelectedPath = {json.dumps(initial)}; "
            "$owner = New-Object System.Windows.Forms.Form -Property @{TopMost=$true; WindowState='Minimized'; ShowInTaskbar=$false}; "
            "if ($f.ShowDialog($owner) -eq 'OK') { [Console]::Out.Write($f.SelectedPath) }; "
            "$owner.Dispose()"
        )
        try:
            r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                               capture_output=True, text=True, timeout=300)
            if r.returncode == 0:
                return {"ok": True, "path": (r.stdout or "").strip()}
        except Exception:
            pass  # fall through to Tk
    try:
        import tkinter as tk
        from tkinter import filedialog
    except Exception as exc:
        return {"ok": False, "path": "", "error": f"folder picker unavailable: {exc}; paste the path into the box instead"}
    try:
        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        root.update()
        selected = filedialog.askdirectory(initialdir=initial, title="Choose folder", parent=root)
        root.destroy()
        return {"ok": True, "path": selected or ""}
    except Exception as exc:
        return {"ok": False, "path": "", "error": f"folder picker failed: {exc}; paste the path into the box instead"}


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


def pcm_sha256(y) -> str:
    """L0 SOUND identity: SHA256 of the decoded canonical PCM — codec-independent
    (the sound, not the file bytes). The cheap analyze pass deposits this on
    files.audio_sha256; L3 stems (Demucs) are content-addressed by it
    (artifact key = SHA256(pcm_sha ‖ "demucs" ‖ model ‖ role)), so a sound is
    separated ONCE and reused/dedup'd across duplicate files. Same decoded PCM ->
    same id, by construction. This is the link the cheap laptop scan hands to the
    expensive GPU scan (v3 §2 L0)."""
    arr = np.ascontiguousarray(np.asarray(y, dtype=np.float32))
    return hashlib.sha256(arr.tobytes()).hexdigest()


def segment_id(source_identity: str, analyzer_version: str, start_sample: int,
               end_sample: int, role: str, stem: str = "mix") -> str:
    """Deterministic content identity for a loop/segment — EARCRATE v3 §2 keystone.

    segment_id = SHA256( source_identity ‖ analyzer_version ‖ start_sample
                         ‖ end_sample ‖ role ‖ stem )

    Same sound + analyzer + window + role always yields the SAME id, so
    re-extraction is an UPSERT instead of a delete+reinsert. Human judgments
    keyed (transitively) off this id therefore survive a force rebuild by
    construction — the Lesson #7 cascade cannot recur. `source_identity` is the
    file id here: loops are per-file in this layer, and sound-level dedup across
    duplicate files is L1 work (rebuild plan §5.3), not this keystone."""
    payload = "‖".join([str(source_identity), str(analyzer_version),
                             str(int(start_sample)), str(int(end_sample)),
                             str(role), str(stem)])
    return "seg_" + sha256_text(payload)


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


