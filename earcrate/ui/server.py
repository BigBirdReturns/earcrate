from earcrate.core.deps import *
from earcrate.core.deps import _dt
from earcrate.app import *
HTML_PAGE = (Path(__file__).resolve().parent / "static" / "index.html").read_text(encoding="utf-8")  # single-file build inlines this
HTML_PAGE = HTML_PAGE.replace("__ENGINE_VERSION__", ENGINE_DISPLAY_VERSION)  # version is single-sourced from deps.py
class JBHandler(BaseHTTPRequestHandler):
    core: EarcrateCore = None  # type: ignore
    token: str = ""

    def log_message(self, fmt: str, *args: Any) -> None:
        return

    def _send(self, status: int, body: bytes, content_type: str = "application/json") -> None:
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
        try:
            parsed = urllib.parse.urlparse(self.path)
            if parsed.path == "/":
                self._send(200, HTML_PAGE.encode("utf-8"), "text/html")
                return
            if not self._check_token():
                self._json(403, {"error": "bad token"})
                return
            if parsed.path == "/api/status":
                with self.core.status_lock:
                    self._json(200, dict(self.core.status))
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
            if parsed.path == "/api/manifests":
                self._json(200, self.core.list_manifests())
                return
            if parsed.path == "/api/renders":
                self._json(200, self.core.list_renders())
                return
            if parsed.path == "/api/audio":
                q = urllib.parse.parse_qs(parsed.query)
                raw = (q.get("path") or [""])[0]
                audio_path = Path(raw).expanduser().resolve()
                c = self.core.ensure_config()
                self.core.validate_path_in_root(audio_path, c.working_root / "renders")
                if not audio_path.exists() or not audio_path.is_file():
                    self._json(404, {"error": "audio file not found"})
                    return
                ctype = mimetypes.guess_type(str(audio_path))[0] or "audio/wav"
                self._send_file(audio_path, ctype)
                return
            self._json(404, {"error": "not found"})
        except Exception as exc:
            self._json(500, {"error": str(exc), "trace": traceback.format_exc()})

    def do_POST(self) -> None:
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
            if path == "/api/config_workspace":
                self._json(200, self.core.configure_workspace(data)); return
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
            if path == "/api/continuum/compile":
                self._json(200, self.core.propose_continuum(data)); return
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
            self._json(500, {"error": str(exc), "trace": traceback.format_exc()})


def serve(open_browser: bool = True, port: int = 0) -> None:
    core = EarcrateCore()
    token = base64.urlsafe_b64encode(os.urandom(24)).decode("ascii").rstrip("=")
    JBHandler.core = core
    JBHandler.token = token
    server = ThreadingHTTPServer(("127.0.0.1", port), JBHandler)
    host, actual_port = server.server_address
    url = f"http://127.0.0.1:{actual_port}/?token={urllib.parse.quote(token)}"
    print(f"earcrate is running on {url}")
    # Warm librosa's numba JIT off the request path so the first analyze/render
    # does not block ~5-10s on compilation and look like a freeze.
    threading.Thread(target=warmup_dsp, daemon=True).start()
    if open_browser:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


