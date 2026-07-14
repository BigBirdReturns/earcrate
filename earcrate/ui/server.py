from earcrate.core.deps import *
from earcrate.core.deps import _dt
from earcrate.app import *
from earcrate.core.util import visible_app_dir
HTML_PAGE = (Path(__file__).resolve().parent / "static" / "index.html").read_text(encoding="utf-8")  # single-file build inlines this


def _resolve_build_stamp() -> str:
    """Package mode: content-hash the source so every code change visibly changes
    the header. Single-file mode: the builder already replaced the sentinel."""
    if BUILD_STAMP != "__BUILD" + "_STAMP__":
        return BUILD_STAMP
    try:
        pkg = Path(__file__).resolve().parent.parent
        h = hashlib.sha256()
        for f in sorted(pkg.rglob("*.py")) + [pkg / "ui" / "static" / "index.html"]:
            h.update(f.read_bytes())
        return h.hexdigest()[:7]
    except Exception:
        return "dev"


HTML_PAGE = HTML_PAGE.replace("__ENGINE_VERSION__", f"{ENGINE_DISPLAY_VERSION} · build {_resolve_build_stamp()}")  # single-sourced from deps.py


# --- Live backend debug log ---------------------------------------------------
# Opt-in via the EARCRATE_DEBUG env var. When set, every HTTP request and its
# outcome (status + elapsed ms) is appended to a single tailable logfile, and any
# handler exception is written with its FULL traceback. This is the backend's
# live "what is it doing / where is it failing" feed: run the app in one window
# and follow the log in another (Debug-EarCrate.cmd wires both up for you).
# Off by default so ordinary runs and the executable gates write no stray file.
#   EARCRATE_DEBUG=1                       -> default location, beside the app
#   EARCRATE_DEBUG=C:\path\earcrate.log    -> that exact file (used by the .cmd)
_DEBUG_OFF = {"", "0", "false", "no", "off"}


class _DebugLog:
    """Thread-safe append-only log. ThreadingHTTPServer serves requests
    concurrently, so writes are serialized under a lock. Resolved once at import;
    when disabled every write is a cheap early return."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._path: Optional[Path] = None
        raw = str(os.environ.get("EARCRATE_DEBUG") or "").strip()
        self.on = raw.lower() not in _DEBUG_OFF
        if self.on:
            try:
                # A path-like value is used verbatim (so the app and the tailing
                # window agree on ONE file); a bare flag picks the default spot.
                if "/" in raw or "\\" in raw or raw.lower().endswith(".log"):
                    self._path = Path(raw).expanduser()
                else:
                    self._path = visible_app_dir() / "earcrate_debug.log"
                self._path.parent.mkdir(parents=True, exist_ok=True)
            except Exception:
                self.on = False
                self._path = None

    def path(self) -> Optional[Path]:
        return self._path

    def write(self, line: str) -> None:
        if not self.on or self._path is None:
            return
        stamp = _dt.datetime.now().strftime("%H:%M:%S.%f")[:-3]
        try:
            with self._lock, self._path.open("a", encoding="utf-8") as f:
                f.write(f"{stamp}  {line}\n")
                f.flush()
        except Exception:
            pass


DEBUG_LOG = _DebugLog()


class JBHandler(BaseHTTPRequestHandler):
    core: EarcrateCore = None  # type: ignore
    token: str = ""
    _status: int = 0

    def log_message(self, fmt: str, *args: Any) -> None:
        return

    def _safe_path(self) -> str:
        # Never let the per-session token land in a logfile the user may share.
        return re.sub(r"([?&]token=)[^&]*", r"\1<redacted>", self.path or "")

    def _send(self, status: int, body: bytes, content_type: str = "application/json") -> None:
        self._status = status
        try:
            self.send_response(status)
            self.send_header("Content-Type", content_type + "; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError, OSError):
            return

    def _json(self, status: int, data: Any) -> None:
        self._send(status, json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8"), "application/json")

    def _send_file(self, path: Path, content_type: str) -> None:
        size = path.stat().st_size
        range_header = self.headers.get("Range") or ""
        start, end = 0, size - 1
        status = 200
        if range_header.startswith("bytes="):
            spec = range_header.split("=", 1)[1].split(",", 1)[0].strip()
            if "-" in spec:
                a, b = spec.split("-", 1)
                if a:
                    start = max(0, int(a))
                if b:
                    end = min(size - 1, int(b))
                status = 206
        if start > end or start >= size:
            self.send_response(416)
            self.send_header("Content-Range", f"bytes */{size}")
            self.end_headers()
            return
        self._status = status
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(end - start + 1))
        if status == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        with path.open("rb") as f:
            f.seek(start)
            remaining = end - start + 1
            while remaining > 0:
                chunk = f.read(min(1024 * 1024, remaining))
                if not chunk:
                    break
                try:
                    self.wfile.write(chunk)
                except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError, OSError):
                    return
                remaining -= len(chunk)

    def _check_token(self) -> bool:
        if self.path.startswith("/") and not self.path.startswith("/api"):
            return True
        if self.headers.get("X-JB-Token") == self.token:
            return True
        q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        return (q.get("token") or [""])[0] == self.token

    def do_GET(self) -> None:
        _t0 = time.perf_counter()
        self._status = 0
        try:
            parsed = urllib.parse.urlparse(self.path)
            if parsed.path == "/":
                self._send(200, HTML_PAGE.encode("utf-8"), "text/html")
                return
            if not self._check_token():
                self._json(403, {"error": "bad token"})
                return
            if parsed.path == "/api/status":
                self._json(200, self.core.status_snapshot())
                return
            if parsed.path == "/api/perf":
                self._json(200, self.core.last_perf())
                return
            if parsed.path == "/api/defaults":
                self._json(200, self.core.default_paths())
                return
            if parsed.path == "/api/tracks":
                self._json(200, self.core.list_tracks())
                return
            if parsed.path == "/api/loops":
                q = urllib.parse.parse_qs(parsed.query)
                self._json(200, self.core.list_loops((q.get("status") or [""])[0]))
                return
            if parsed.path == "/api/ear_atoms":
                q = urllib.parse.parse_qs(parsed.query)
                self._json(200, self.core.list_ear_atoms((q.get("status") or ["approved"])[0], (q.get("taste_profile") or ["girl_talk_v1"])[0]))
                return
            if parsed.path == "/api/residents":
                self._json(200, self.core.residents())
                return
            if parsed.path == "/api/sessions":
                self._json(200, self.core.sessions_list())
                return
            if parsed.path == "/api/timeline/list":
                self._json(200, self.core.list_plans())
                return
            if parsed.path == "/api/taste/profile":
                q = urllib.parse.parse_qs(parsed.query)
                self._json(200, self.core.taste_profile_receipt((q.get("taste_profile") or ["girl_talk_v1"])[0]))
                return
            if parsed.path == "/api/taste/pairs":
                q = urllib.parse.parse_qs(parsed.query)
                self._json(200, self.core.compatible_pairs_for_atom((q.get("atom_id") or [""])[0], (q.get("taste_profile") or ["girl_talk_v1"])[0], int((q.get("limit") or ["40"])[0])))
                return
            if parsed.path == "/api/manifests":
                self._json(200, self.core.list_manifests())
                return
            if parsed.path == "/api/renders":
                self._json(200, self.core.list_renders())
                return
            if parsed.path == "/api/identify/journals":
                self._json(200, self.core.identify_journals())
                return
            if parsed.path == "/api/capabilities":
                self._json(200, self.core.machine_capabilities())
                return
            if parsed.path == "/api/audio":
                q = urllib.parse.parse_qs(parsed.query)
                raw = (q.get("path") or [""])[0]
                audio_path = Path(raw).expanduser().resolve()
                c = self.core.ensure_config()
                # Read-only playback is allowed from renders, atom previews, and the
                # source library itself (auditioning IS the product). Writes never
                # happen here; path containment still enforced.
                for _root in (c.working_root, c.master_root):
                    try:
                        self.core.validate_path_in_root(audio_path, _root)
                        break
                    except Exception:
                        continue
                else:
                    self._json(403, {"error": "audio path outside workspace/library"})
                    return
                if not audio_path.exists() or not audio_path.is_file():
                    self._json(404, {"error": "audio file not found"})
                    return
                ctype = mimetypes.guess_type(str(audio_path))[0] or "audio/wav"
                self._send_file(audio_path, ctype)
                return
            self._json(404, {"error": "not found"})
        except Exception as exc:
            if DEBUG_LOG.on:
                DEBUG_LOG.write("ERROR  GET  " + self._safe_path() + "\n" + traceback.format_exc().rstrip())
            self._json(500, {"error": str(exc), "trace": traceback.format_exc()})
        finally:
            if DEBUG_LOG.on:
                DEBUG_LOG.write(f"GET   {self._status or '---'}  {int((time.perf_counter() - _t0) * 1000)}ms  {self._safe_path()}")

    def do_POST(self) -> None:
        _t0 = time.perf_counter()
        self._status = 0
        try:
            if not self._check_token():
                self._json(403, {"error": "bad token"})
                return
            n = int(self.headers.get("Content-Length") or 0)
            data = json.loads(self.rfile.read(n).decode("utf-8") or "{}") if n else {}
            path = urllib.parse.urlparse(self.path).path
            if path == "/api/config":
                self._json(200, self.core.configure(data)); return
            if path == "/api/ingest":
                self._json(200, self.core.ingest_sources(data)); return
            if path == "/api/organize":
                self._json(200, self.core.organize_and_retag(data)); return
            if path == "/api/rank":
                self._json(200, self.core.rank_crate(str(data.get("taste_profile") or "girl_talk_v1"), int(data.get("limit") or 40))); return
            if path == "/api/open_folder":
                self._json(200, self.core.open_folder(str(data.get("path") or ""))); return
            if path == "/api/config_workspace":
                self._json(200, self.core.configure_workspace(data)); return
            if path == "/api/relocate_workspace":
                self._json(200, self.core.relocate_workspace(data)); return
            if path == "/api/capabilities":
                self._json(200, self.core.machine_capabilities()); return
            if path == "/api/workspace_candidates":
                self._json(200, self.core.workspace_candidates(str(data.get("music_folder") or ""))); return
            if path == "/api/browse_dir":
                self._json(200, choose_directory_dialog(str(data.get("current") or ""))); return
            if path == "/api/doctor":
                self._json(200, self.core.doctor()); return
            if path == "/api/scan_bg":
                self._json(200, self.core.run_background(self.core.scan)); return
            if path == "/api/analyze_bg":
                self._json(200, self.core.run_background(self.core.analyze, int(data.get("limit") or 0))); return
            if path == "/api/extract_loops_bg":
                self._json(200, self.core.run_background(self.core.extract_loops, int(data.get("limit") or 0), bool(data.get("auto_approve", True)), bool(data.get("force", False)))); return
            if path == "/api/loop_status":
                self._json(200, self.core.set_loop_status(str(data["loop_id"]), str(data["status"]))); return
            if path == "/api/loops/bulk_status":
                self._json(200, self.core.bulk_loop_status(str(data["status"]), str(data.get("from_status") or "candidate"))); return
            if path == "/api/loops/auto_approve_quota":
                self._json(200, self.core.auto_approve_quota(int(data.get("max_loops") or 60))); return
            if path == "/api/ear_crate/build":
                self._json(200, self.core.build_ear_crate(int(data.get("limit") or 0), bool(data.get("force", False)), str(data.get("taste_profile") or "girl_talk_v1"), bool(data.get("write_previews", False)))); return
            if path == "/api/ear_atoms/judgment":
                self._json(200, self.core.set_atom_judgment(str(data["atom_id"]), str(data.get("taste_profile") or "girl_talk_v1"), str(data["status"]), str(data.get("relabel_role") or ""), bool(data.get("favorite", False)), bool(data.get("locked", False)), str(data.get("reason") or ""))); return
            if path == "/api/taste/pair_judgment":
                self._json(200, self.core.set_pair_judgment(str(data["edge_id"]), str(data.get("taste_profile") or "girl_talk_v1"), str(data["status"]), str(data.get("reason") or ""))); return
            if path == "/api/station/feedback":
                self._json(200, self.core.station_feedback(str(data.get("signal") or ""))); return
            if path == "/api/timeline/propose":
                self._json(200, self.core.propose_plan(data)); return
            if path == "/api/timeline/save":
                self._json(200, self.core.save_plan(str(data.get("name") or "plan"), data["plan"], str(data.get("taste_profile") or "girl_talk_v1"))); return
            if path == "/api/timeline/load":
                self._json(200, self.core.load_plan(str(data["plan_hash"]))); return
            if path == "/api/study/reference":
                self._json(200, self.core.study_reference(str(data.get("path") or ""), str(data.get("taste_profile") or "girl_talk_v1"))); return
            if path == "/api/stems/warm":
                self._json(200, self.core.run_background(self.core.warm_stems, str(data.get("taste_profile") or "girl_talk_v1"), int(data.get("max_items") or 0))); return
            if path == "/api/stems/warm_status":
                self._json(200, self.core.stem_warm_status(str(data.get("taste_profile") or "girl_talk_v1"))); return
            if path == "/api/taste/readiness":
                self._json(200, self.core.taste_readiness(str(data.get("taste_profile") or "girl_talk_v1"), float(data.get("target_seconds") or 120))); return
            if path == "/api/taste/graph":
                self._json(200, self.core.build_compatibility_graph(str(data.get("taste_profile") or "girl_talk_v1"), float(data.get("target_seconds") or 120), float(data.get("bpm") or 0.0))); return
            if path == "/api/mashup/propose":
                self._json(200, self.core.propose_mashup(data)); return
            if path == "/api/one_click_bg":
                self._json(200, self.core.run_background(self.core.one_click_mix, data)); return
            if path == "/api/one_click":
                self._json(200, self.core.one_click_mix(data)); return
            if path == "/api/render_plan":
                self._json(200, self.core.run_background(self.core.render_plan, data)); return
            if path == "/api/bakeoff":
                # plan_only is a fast, synchronous A/B/C preview (compose+gate, no
                # WAV); a full bake-off renders one WAV per persona in the background.
                if bool(data.get("plan_only")):
                    self._json(200, self.core.bakeoff(data)); return
                self._json(200, self.core.run_background(self.core.bakeoff, data)); return
            if path == "/api/reorganize/plan":
                self._json(200, self.core.reorganize_source({**data, "apply": False})); return
            if path == "/api/reorganize/apply":
                self._json(200, self.core.reorganize_source({**data, "apply": True})); return
            if path == "/api/reorganize/rollback":
                self._json(200, self.core.rollback_reorganize(data)); return
            if path == "/api/identify/apply":
                # Dry-run (apply:false) must be SYNCHRONOUS so the UI can read the
                # signature it has to echo back; the real apply is backgrounded.
                if not bool(data.get("apply", False)):
                    self._json(200, self.core.apply_identities(data)); return
                self._json(200, self.core.run_background(self.core.apply_identities, data)); return
            if path == "/api/identify/rollback":
                self._json(200, self.core.rollback_identities(data)); return
            if path == "/api/identify":
                self._json(200, self.core.run_background(self.core.identify_tracks, data)); return
            if path == "/api/deepclean/scan":
                self._json(200, self.core.run_background(self.core.deep_clean_scan, data)); return
            if path == "/api/demo/seed":
                self._json(200, self.core.seed_demo_renders(int(data.get("count") or 8))); return
            if path == "/api/migrate/plan":
                self._json(200, self.core.plan_workspace_migration(data)); return
            if path == "/api/migrate/apply":
                self._json(200, self.core.run_background(self.core.apply_workspace_migration, data)); return
            if path == "/api/preflight":
                self._json(200, self.core.preflight(data)); return
            if path == "/api/playlist/propose":
                self._json(200, self.core.propose_playlist(str(data.get("name") or "playlist"), str(data.get("query") or ""), int(data.get("target_minutes") or 60))); return
            if path == "/api/manifest/execute":
                self._json(200, self.core.execute_manifest(str(data["path"]), apply=bool(data.get("apply", False)))); return
            if path == "/api/manifest/execute_bg":
                if not bool(data.get("apply", False)):
                    self._json(200, self.core.execute_manifest(str(data["path"]), apply=False)); return
                self._json(200, self.core.run_background(self.core.execute_manifest, str(data["path"]), True)); return
            if path == "/api/rollback":
                self._json(200, self.core.rollback_outputs(str(data.get("manifest_id") or ""), int(data.get("limit") or 0), apply=bool(data.get("apply", False)))); return
            if path == "/api/judge":
                self._json(200, self.core.judge_render(str(data.get("path") or ""), str(data.get("ref") or "") or None)); return
            self._json(404, {"error": "not found"})
        except Exception as exc:
            if DEBUG_LOG.on:
                try:
                    body_preview = json.dumps(locals().get("data") or {}, ensure_ascii=False)[:800]
                except Exception:
                    body_preview = "<unreadable body>"
                DEBUG_LOG.write("ERROR  POST " + urllib.parse.urlparse(self.path).path
                                + "  body=" + body_preview + "\n" + traceback.format_exc().rstrip())
            self._json(500, {"error": str(exc), "trace": traceback.format_exc()})
        finally:
            if DEBUG_LOG.on:
                DEBUG_LOG.write(f"POST  {self._status or '---'}  {int((time.perf_counter() - _t0) * 1000)}ms  {urllib.parse.urlparse(self.path).path}")


def serve(open_browser: bool = True, port: int = 0) -> None:
    core = EarcrateCore()
    token = base64.urlsafe_b64encode(os.urandom(24)).decode("ascii").rstrip("=")
    JBHandler.core = core
    JBHandler.token = token
    server = ThreadingHTTPServer(("127.0.0.1", port), JBHandler)
    host, actual_port = server.server_address
    url = f"http://127.0.0.1:{actual_port}/?token={urllib.parse.quote(token)}"
    print(f"earcrate is running on {url}")
    if DEBUG_LOG.on and DEBUG_LOG.path() is not None:
        print(f"[debug] backend log: {DEBUG_LOG.path()}")
        print(f'[debug] live-tail:   Get-Content -Path "{DEBUG_LOG.path()}" -Wait -Tail 50   (PowerShell)')
    # Warm librosa's numba JIT off the request path so the first analyze/render
    # does not block ~5-10s on compilation and look like a freeze.
    threading.Thread(target=warmup_dsp, daemon=True).start()

    def _janitor():
        try:
            core.startup_janitor()
        except Exception:
            pass
    # Launch-time cleanup of old-version leftovers (caches, ' (N)' dupes, legacy
    # workspaces). Additive/archival only; receipt at agent/janitor_last.json.
    threading.Timer(2.0, lambda: threading.Thread(target=_janitor, daemon=True).start()).start()
    if open_browser:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


