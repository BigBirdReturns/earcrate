from earcrate.core.deps import *
from earcrate.core.deps import _dt
from earcrate.analyze.features import *
from earcrate.analyze.features import _clamp01, _estimate_downbeats, _vocal_likelihood, _estimate_sections
from earcrate.deck.transform import _artifact_cost
from earcrate.tastespec import load_tastespec, tastespec_hash, profile_summary


def ear_crate_file_worker(job: Dict[str, Any]) -> Dict[str, Any]:
    """Measure every loop of ONE file (decode once, DSP per segment) in a worker
    process. Metrics are persona-independent; classification uses the same
    thresholds the serial path used. DB writes stay in the parent."""
    out: Dict[str, Any] = {"path": job["path"], "results": [], "error": None}
    sr = int(job["sample_rate"])
    try:
        y = decode_audio(Path(job["path"]), sr)
    except Exception as exc:
        out["error"] = str(exc)[:300]
        return out
    for lp in job["loops"]:
        try:
            a = max(0, int(float(lp["start_s"]) * sr))
            b = min(y.size, int(float(lp["end_s"]) * sr))
            seg = y[a:b].astype(np.float32, copy=False)
            metrics = EarcrateCore.ear_atom_metrics(None, seg, sr, int(lp["bars"] or 1), float(lp["vocal_likelihood"] or 0.0), str(lp["role"] or "full"))
            ear_role = EarcrateCore.ear_role_from_metrics(None, str(lp["role"] or "full"), int(lp["bars"] or 1), metrics)
            render_role = EAR_TO_RENDER_ROLE.get(ear_role, str(lp["role"] or "full"))
            status = classify_atom_status(ear_role, metrics)
            preview_path = None
            if job.get("write_previews") and status != "rejected" and seg.size > 512:
                preview = apply_edge_fades(normalize_layer_rms(seg.copy(), render_role), sr, True, True, 20)
                preview = integrated_lufs_normalize(preview, sr, -16.0)
                fname = f"{safe_name(str(lp.get('artist') or 'unknown'))}-{safe_name(str(lp.get('title') or 'track'))}-{ear_role}-{str(lp['id'])[:8]}.wav"
                pp = Path(job["preview_dir"]) / fname
                sf.write(str(pp), preview[:min(preview.size, sr * 12)], sr, subtype="PCM_16")
                preview_path = str(pp)
            out["results"].append({"loop_id": lp["id"], "metrics": metrics, "ear_role": ear_role,
                                   "render_role": render_role, "status": status, "preview_path": preview_path})
        except Exception as exc:
            out["results"].append({"loop_id": lp["id"], "error": str(exc)[:300]})
    return out


def classify_atom_status(ear_role: str, metrics: Dict[str, float]) -> str:
    """Persona-facing approval thresholds (unchanged values from the serial pass)."""
    min_score = 0.46
    if ear_role in {"VOX_HOOK", "VOX_VERSE"}:
        min_score = 0.50
    elif ear_role in {"DRUM_BREAK", "BASS_RIFF", "BED_CHORD", "RIFF_ID"}:
        min_score = 0.48
    sc = float(metrics.get("score") or 0.0)
    if sc < 0.30:
        return "rejected"
    return "approved" if sc >= min_score else "candidate"


class EarcrateCore:
    def __init__(self):
        # The workspace pointer is the ONE app-global breadcrumb (it names the
        # active workspace, so it cannot live inside a workspace). Keep it VISIBLE
        # and portable: a single file next to the app, never a hidden AppData nest.
        # A legacy hidden pointer is adopted on load so nothing breaks on upgrade.
        self.state_dir = visible_app_dir()
        self.pointer_path = self.state_dir / "earcrate_workspace.json"
        self.legacy_pointer_path = app_state_dir() / "config_pointer.json"
        self.config: Optional[Config] = None
        self.db: Optional[sqlite3.Connection] = None
        self.status_lock = threading.Lock()
        self.status: Dict[str, Any] = {"busy": False, "message": "idle", "progress": 0, "last_error": None, "last_render_path": None, "perf_summary": None, "perf_ledger_path": None}
        self.load_config_if_present()

    def set_status(self, message: str, progress: Optional[float] = None, busy: Optional[bool] = None, error: Optional[str] = None) -> None:
        with self.status_lock:
            self.status["message"] = message
            if progress is not None:
                self.status["progress"] = progress
            if busy is not None:
                self.status["busy"] = busy
            if error is not None:
                self.status["last_error"] = error
            elif busy is True and progress is not None and float(progress) <= 0.05:
                # A new run should not inherit a stale red banner from the previous run.
                self.status["last_error"] = None
            elif busy is False and progress is not None and float(progress) >= 1.0:
                # Successful completions clear the last-error line; rejected completions pass error explicitly.
                self.status["last_error"] = None

    def _perf_new_ledger(self, label: str, inputs: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Create a durable runtime ledger for a user-visible run.

        This is deliberately wall-clock instrumentation, not statistical profiling.
        It answers the operational question first: which named stage consumed the
        run. A later profiler can split hot Python functions inside a stage.
        """
        c = self.ensure_config()
        workers_configured = int(getattr(c, "workers", 0) or 0)
        workers_resolved = self._worker_count()
        ledger = {
            "run_id": ulidish(),
            "label": label,
            "engine_version": ENGINE_VERSION,
            "analyzer_version": ANALYZER_VERSION,
            "started_at": now_utc(),
            "started_perf_counter": time.perf_counter(),
            "inputs": inputs or {},
            "resources": {
                "analysis_workers_configured": workers_configured,
                "analysis_workers_resolved": workers_resolved,
                "cpu_count": os.cpu_count(),
            },
            "stages": [],
        }
        self._perf_publish(ledger, ok=None, in_progress=True)
        return ledger

    def _perf_summarize_result(self, result: Any) -> Dict[str, Any]:
        if not isinstance(result, dict):
            return {"type": type(result).__name__}
        keep = {
            "ok", "ready", "analyzed", "parallel", "workers", "analysis_seconds", "cache_hits", "compute_jobs",
            "extracted", "approved", "inserted", "updated", "rejected", "failed", "pool_size", "approved_atoms",
            "edges", "render_bpm", "chosen_bpm", "target_key", "render_path", "drop_count", "seconds", "sections", "layers",
        }
        out: Dict[str, Any] = {}
        for k, v in result.items():
            if k in keep and isinstance(v, (str, int, float, bool, type(None))):
                out[k] = v
        # Common nested summaries without copying huge manifests or graph receipts.
        if isinstance(result.get("readiness"), dict):
            r = result["readiness"]
            out["readiness_ready"] = bool(r.get("ready"))
            out["readiness_failures"] = len(r.get("failures") or [])
        if isinstance(result.get("capacity"), dict):
            out["capacity"] = {k: result["capacity"].get(k) for k in ("sample_events", "distinct_sources", "foreground", "beds") if k in result["capacity"]}
        if isinstance(result.get("counts"), dict):
            out["counts"] = result.get("counts")
        if isinstance(result.get("done"), list):
            out["done_ops"] = len(result.get("done") or [])
        return out

    def _perf_stage(self, ledger: Dict[str, Any], name: str, fn, *args, **kwargs) -> Any:
        t0 = time.perf_counter()
        stage: Dict[str, Any] = {"name": name, "started_at": now_utc()}
        result: Any = None
        try:
            result = fn(*args, **kwargs)
            stage["ok"] = True
            stage["result"] = self._perf_summarize_result(result)
            return result
        except Exception as exc:
            stage["ok"] = False
            stage["error"] = str(exc)
            raise
        finally:
            stage["seconds"] = round(time.perf_counter() - t0, 3)
            ledger.setdefault("stages", []).append(stage)
            self._perf_publish(ledger, ok=None, in_progress=True)

    def _perf_publish(self, ledger: Dict[str, Any], ok: Optional[bool] = None, in_progress: bool = False) -> Dict[str, Any]:
        elapsed = max(0.0, time.perf_counter() - float(ledger.get("started_perf_counter") or time.perf_counter()))
        stages = list(ledger.get("stages") or [])
        top = sorted(stages, key=lambda x: float(x.get("seconds") or 0.0), reverse=True)[:8]
        summary = {
            "run_id": ledger.get("run_id"),
            "label": ledger.get("label"),
            "engine_version": ledger.get("engine_version"),
            "elapsed_seconds": round(elapsed, 3),
            "stage_count": len(stages),
            "top_stages": [{"name": s.get("name"), "seconds": s.get("seconds"), "ok": s.get("ok")} for s in top],
            "in_progress": bool(in_progress),
            "ok": ok,
        }
        public = dict(ledger)
        public.pop("started_perf_counter", None)
        public["elapsed_seconds"] = round(elapsed, 3)
        public["summary"] = summary
        if ok is not None:
            public["ok"] = bool(ok)
            public["finished_at"] = now_utc()
        path_str = None
        try:
            c = self.ensure_config()
            perf_dir = c.agent_root / "perf"
            perf_dir.mkdir(parents=True, exist_ok=True)
            run_path = perf_dir / f"{ledger.get('run_id')}.runtime_ledger.json"
            last_path = perf_dir / "last_run.runtime_ledger.json"
            text = json.dumps(public, ensure_ascii=False, indent=2)
            run_path.write_text(text, encoding="utf-8")
            last_path.write_text(text, encoding="utf-8")
            path_str = str(run_path)
        except Exception:
            path_str = None
        with self.status_lock:
            self.status["perf_summary"] = summary
            if path_str:
                self.status["perf_ledger_path"] = path_str
        return public

    def last_perf(self) -> Dict[str, Any]:
        c = self.ensure_config()
        path = c.agent_root / "perf" / "last_run.runtime_ledger.json"
        if not path.exists():
            return {"ok": False, "error": "no runtime ledger yet"}
        return {"ok": True, "path": str(path), "ledger": json.loads(path.read_text(encoding="utf-8"))}

    def load_config_if_present(self) -> None:
        try:
            if not self.pointer_path.exists() and self.legacy_pointer_path.exists():
                with contextlib.suppress(Exception):
                    self.pointer_path.parent.mkdir(parents=True, exist_ok=True)
                    self.pointer_path.write_text(self.legacy_pointer_path.read_text(encoding="utf-8"), encoding="utf-8")
            if not self.pointer_path.exists():
                return
            data = json.loads(self.pointer_path.read_text(encoding="utf-8"))
            cfg_path = Path(data["config_json"])
            if not cfg_path.exists():
                return
            cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
            self.config = Config(
                master_root=Path(cfg["master_root"]).resolve(),
                working_root=Path(cfg["working_root"]).resolve(),
                stems_root=Path(cfg.get("stems_root") or Path(cfg["working_root"]) / "stems").resolve(),
                playlists_root=Path(cfg.get("playlists_root") or Path(cfg["working_root"]) / "playlists").resolve(),
                agent_root=Path(cfg["agent_root"]).resolve(),
                sample_rate=int(cfg.get("sample_rate", DEFAULT_SAMPLE_RATE)),
                workers=int(cfg.get("workers", 0)),
                seed=int(cfg.get("seed", 1337)),
                analysis_seconds=int(cfg.get("analysis_seconds", DEFAULT_ANALYSIS_SECONDS)),
            )
            self.ensure_layout()
            self.connect_db()
        except Exception:
            self.config = None
            self.db = None

    def configure(self, data: Dict[str, Any]) -> Dict[str, Any]:
        master = Path(data["master_root"]).expanduser().resolve()
        working = Path(data["working_root"]).expanduser().resolve()
        agent = Path(data["agent_root"]).expanduser().resolve()
        playlists = Path(data.get("playlists_root") or working / "playlists").expanduser().resolve()
        stems = Path(data.get("stems_root") or working / "stems").expanduser().resolve()
        if not master.exists() or not master.is_dir():
            raise ValueError("master_root must be an existing directory")
        for a, b, label in [(master, working, "master_root and working_root"), (master, agent, "master_root and agent_root")]:
            try:
                aa = os.path.normcase(str(a))
                bb = os.path.normcase(str(b))
                common = os.path.commonpath([aa, bb])
            except ValueError:
                # Windows raises when roots are on different drives. Different drives cannot contain each other.
                continue
            if common == aa or common == bb:
                raise ValueError(f"{label} must not contain each other")
        self.config = Config(master, working, stems, playlists, agent, int(data.get("sample_rate", DEFAULT_SAMPLE_RATE)), int(data.get("workers", 0)), int(data.get("seed", 1337)), int(data.get("analysis_seconds", DEFAULT_ANALYSIS_SECONDS)))
        self.ensure_layout()
        cfg_path = self.config.agent_root / "config.json"
        cfg_path.write_text(json.dumps(self.config.as_dict(), indent=2), encoding="utf-8")
        self.write_toml_config()
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.pointer_path.write_text(json.dumps({"config_json": str(cfg_path)}, indent=2), encoding="utf-8")
        self.connect_db()
        return {"ok": True, "config": self.config.as_dict()}

    def default_paths(self) -> Dict[str, Any]:
        home = Path.home()
        music = home / "Music"
        if not music.exists():
            music = home
        # Default the workspace to a VISIBLE folder the user can actually find and
        # open, not a hidden AppData nest. Must not sit inside the music folder
        # (INV-1 path separation), so use a sibling under the profile.
        workspace = Path(sibling_workspace(str(music)))
        return {
            "music_folder": str(music),
            "workspace_folder": str(workspace),
            "derived": self.derive_workspace_paths(str(music), str(workspace)),
            "configured": self.config.as_dict() if self.config else None,
        }

    def open_folder(self, path: str) -> Dict[str, Any]:
        """Reveal a folder in the OS file manager. The whole point of the receipts
        is that a human can go look; opening AppData nests by hand is hostile."""
        p = Path(str(path or "")).expanduser()
        if p.is_file():
            p = p.parent
        c = self.config
        roots = [r for r in ([c.master_root, c.working_root, c.agent_root, c.playlists_root, c.stems_root]
                             if c else []) if r]
        if not roots:
            return {"ok": False, "error": "configure a workspace first"}
        # Only reveal inside the configured workspace/library (and their parents so the
        # workspace root itself opens). Never open an arbitrary path from a web request.
        allowed = False
        for r in roots:
            try:
                rp = r.resolve()
                if p.resolve() == rp or p.resolve() in rp.parents or rp in p.resolve().parents or p.resolve() == rp.parent:
                    allowed = True
                    break
            except Exception:
                continue
        if roots and not allowed:
            return {"ok": False, "error": "refusing to open a path outside the workspace/library"}
        if not p.exists():
            return {"ok": False, "error": f"folder does not exist yet: {p}"}
        try:
            if os.name == "nt":
                os.startfile(str(p))  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(p)])
            else:
                subprocess.Popen(["xdg-open", str(p)])
            return {"ok": True, "opened": str(p)}
        except Exception as exc:
            return {"ok": False, "error": f"could not open folder: {exc}", "path": str(p)}

    def derive_workspace_paths(self, music_folder: str, workspace_folder: str) -> Dict[str, str]:
        music = Path(music_folder).expanduser().resolve()
        workspace = Path(workspace_folder).expanduser().resolve()
        return {
            "master_root": str(music),
            "working_root": str(workspace / "work"),
            "agent_root": str(workspace / "agent"),
            "playlists_root": str(workspace / "playlists"),
            "stems_root": str(workspace / "stems"),
        }

    def workspace_candidates(self, music_folder: str = "") -> Dict[str, Any]:
        """Scout ranked workspace candidates with per-candidate receipts.

        Scores locations against the constraints the engine itself imposes:
        path separation from the master root (executor path checks), fsync
        latency (JSONL journals + SQLite), free headroom (analysis .npz cache,
        renders, previews, rollback archives), and sync-client avoidance
        (OneDrive/Dropbox fight fsync and file locks). Read-only probe plus
        one small fsynced temp write per drive; nothing is created.
        """
        music = Path(music_folder).expanduser().resolve() if str(music_folder or "").strip() else None
        sync_markers = {"onedrive", "dropbox", "google drive", "googledrive", "icloud", "iclouddrive", "box", "sync"}
        onedrive_env = [os.environ.get(k) for k in ("OneDrive", "OneDriveConsumer", "OneDriveCommercial")]
        onedrive_roots = [Path(p).resolve() for p in onedrive_env if p]

        def is_sync_managed(p: Path) -> bool:
            parts = {seg.lower() for seg in p.parts}
            if parts & sync_markers:
                return True
            for root in onedrive_roots:
                try:
                    if p == root or root in p.parents or p in root.parents:
                        return True
                except Exception:
                    pass
            return False

        def drive_kind(p: Path) -> str:
            if os.name != "nt":
                return "fixed"
            try:
                import ctypes
                root = str(Path(p.drive + "\\")) if p.drive else str(p.anchor)
                t = ctypes.windll.kernel32.GetDriveTypeW(ctypes.c_wchar_p(root))
                return {2: "removable", 3: "fixed", 4: "network", 5: "cdrom", 6: "ramdisk"}.get(int(t), "unknown")
            except Exception:
                return "unknown"

        def nearest_existing(p: Path) -> Path:
            cur = p
            while not cur.exists() and cur.parent != cur:
                cur = cur.parent
            return cur

        probe_cache: Dict[str, Optional[float]] = {}

        def fsync_probe_ms(p: Path) -> Optional[float]:
            base = nearest_existing(p)
            key = str(base)
            if key in probe_cache:
                return probe_cache[key]
            result: Optional[float] = None
            try:
                import tempfile, time as _t
                payload = b"\0" * 262144
                t0 = _t.perf_counter()
                with tempfile.NamedTemporaryFile(dir=str(base), prefix=".jbgt_probe_", delete=True) as fh:
                    for _ in range(3):
                        fh.write(payload)
                        fh.flush()
                        os.fsync(fh.fileno())
                result = round((_t.perf_counter() - t0) * 1000.0, 1)
            except Exception:
                result = None
            probe_cache[key] = result
            return result

        # Candidate bases: existing configured workspace first (adoption), then
        # per-drive roots, then user-profile locations on the system drive.
        raw: List[Path] = []
        if self.config:
            raw.append(self.config.agent_root.parent)
        # The one no-hunting default: a VISIBLE sibling next to the music folder.
        # No hidden AppData nests, no drive-root / home-root folders. Adoption of
        # old workspaces is handled by the migration tool, not by suggesting new
        # top-level folders here.
        if music is not None:
            raw.append(Path(sibling_workspace(str(music))))
        seen: set = set()
        candidates: List[Dict[str, Any]] = []
        for cand in raw:
            try:
                cand = cand.expanduser().resolve()
            except Exception:
                continue
            key = str(cand).lower()
            if key in seen:
                continue
            seen.add(key)
            reasons: List[str] = []
            hard_reject = False
            if music is not None:
                if cand == music or music in cand.parents:
                    reasons.append("REJECT: inside the music folder; the executor requires separation from master_root")
                    hard_reject = True
                elif cand in music.parents:
                    reasons.append("REJECT: contains the music folder; master_root must not live under the workspace")
                    hard_reject = True
            kind = drive_kind(cand)
            sync_flag = is_sync_managed(cand)
            base = nearest_existing(cand)
            try:
                usage = shutil.disk_usage(str(base))
                free_gb = round(usage.free / 1e9, 1)
            except Exception:
                free_gb = 0.0
            fsync_ms = None if hard_reject else fsync_probe_ms(cand)
            exists_workspace = (cand / "agent" / "config.toml").exists() or (cand / "agent").exists()
            score = 0.0
            score += min(6.0, math.log10(max(1.0, free_gb)) * 3.0)
            if free_gb >= 50:
                reasons.append(f"{free_gb} GB free")
            elif free_gb >= 15:
                reasons.append(f"{free_gb} GB free: workable, watch the analysis cache and renders")
                score -= 0.5
            else:
                reasons.append(f"{free_gb} GB free: too tight for cache + renders + rollback archives")
                score -= 3.0
            if sync_flag:
                score -= 3.0
                reasons.append("sync-managed path (OneDrive/Dropbox class): fights fsync journals and SQLite locks")
            if kind == "removable":
                score -= 2.0
                reasons.append("removable drive: fine for archives, poor for the live DB and cache")
            elif kind == "network":
                score -= 4.0
                reasons.append("network drive: fsync and SQLite over SMB is a known failure mode")
            elif kind == "fixed":
                reasons.append("fixed local drive")
            if fsync_ms is not None:
                if fsync_ms <= 60:
                    score += 1.0
                    reasons.append(f"fsync probe {fsync_ms} ms (3x256KB): fast enough for journals")
                elif fsync_ms <= 200:
                    reasons.append(f"fsync probe {fsync_ms} ms: acceptable")
                else:
                    score -= 1.5
                    reasons.append(f"fsync probe {fsync_ms} ms: slow; journal-heavy stages will drag")
            elif not hard_reject:
                score -= 1.0
                reasons.append("probe location not writable without elevation; verify permissions")
            if exists_workspace:
                score += 2.0
                reasons.append("existing workspace found here (earcrate or legacy Jukebreaker): adopting it preserves DB and analysis cache")
            if music is not None and cand.drive and music.drive and cand.drive.upper() != music.drive.upper():
                score += 0.5
                reasons.append("different drive from the music library: analysis reads and cache writes will not contend")
            if hard_reject:
                score = -100.0
            candidates.append({"path": str(cand), "score": round(score, 2), "free_gb": free_gb, "drive_kind": kind, "sync_managed": sync_flag, "fsync_ms": fsync_ms, "existing_workspace": exists_workspace, "rejected": hard_reject, "reasons": reasons})
        candidates.sort(key=lambda x: x["score"], reverse=True)
        best = next((c for c in candidates if not c["rejected"]), None)
        return {"ok": True, "music_folder": str(music) if music else None, "recommended": (best or {}).get("path"), "candidates": candidates}

    def configure_workspace(self, data: Dict[str, Any]) -> Dict[str, Any]:
        music = str(data.get("music_folder") or data.get("master_root") or "").strip()
        workspace = str(data.get("workspace_folder") or "").strip()
        if not music:
            raise ValueError("music_folder is required")
        if not workspace:
            workspace = sibling_workspace(music)
        paths = self.derive_workspace_paths(music, workspace)
        if data.get("analysis_seconds"):
            paths["analysis_seconds"] = int(data["analysis_seconds"])
        if data.get("workers") is not None:
            paths["workers"] = int(data["workers"])
        return self.configure(paths)

    def write_toml_config(self) -> None:
        assert self.config
        c = self.config
        text = f'''[paths]\nmaster_root = {json.dumps(str(c.master_root))}\nworking_root = {json.dumps(str(c.working_root))}\nstems_root = {json.dumps(str(c.stems_root))}\nplaylists_root = {json.dumps(str(c.playlists_root))}\nagent_root = {json.dumps(str(c.agent_root))}\n\n[analysis]\nsample_rate = {c.sample_rate}\nworkers = {c.workers}\nseed = {c.seed}\nanalysis_seconds = {c.analysis_seconds}\n\n[assist]\nenabled = false\nprovider = "anthropic"\nmodel = ""\n'''
        (c.agent_root / "config.toml").write_text(text, encoding="utf-8")

    # ---- One-time workspace migration (this-iteration cleanup) --------------
    # Simulate -> approve -> execute. Reusable buffalo (DB, analysis cache,
    # renders, manifests, human judgments) move to their NEW homes; anything
    # that does not conform to the current layout is QUARANTINED under legacy/
    # (never deleted); dead breadcrumbs are scrubbed into legacy/_scrubbed/.
    # A later version can fold this into the library engine; for now it is a
    # personal migration tool off the old JukebreakerGT hidden-nest era.
    def _migration_homes(self, workspace: str) -> Dict[str, Path]:
        ws = Path(str(workspace)).expanduser().resolve()
        agent = ws / "agent"
        work = ws / "work"
        return {
            "workspace": ws, "agent": agent, "work": work,
            "db": agent / "earcrate.sqlite",
            "cache_analysis": agent / "cache" / "analysis",
            "cache_transforms": agent / "cache" / "transforms",
            "manifests": agent / "manifests",
            "renders": work / "renders",
            "legacy": ws / "legacy",
            "scrubbed": ws / "legacy" / "_scrubbed",
        }

    def _legacy_source_roots(self, music: str, workspace: str, extra: List[str]) -> List[Path]:
        ws = Path(str(workspace)).expanduser().resolve()
        music_r: Optional[Path] = None
        with contextlib.suppress(Exception):
            music_r = Path(str(music)).expanduser().resolve() if music else None
        home = Path.home()
        cands: List[Path] = [app_state_dir() / "workspace", home / "Jukebreaker", home / "jukebreaker",
                             home / "Earcrate", home / "earcrate"]
        if self.config:
            cands.append(self.config.agent_root.parent)
        for e in (extra or []):
            cands.append(Path(str(e)).expanduser())
        roots: List[Path] = []
        seen: set = set()
        for c in cands:
            try:
                c = c.expanduser().resolve()
            except Exception:
                continue
            if not c.is_dir():
                continue
            if c == ws or ws in c.parents or c in ws.parents:
                continue  # never the target workspace itself
            if music_r is not None and (c == music_r or music_r in c.parents or c in music_r.parents):
                continue  # never the read-only music library
            looks = any((c / p).exists() for p in ("agent/earcrate.sqlite", "agent/jukebreaker.sqlite",
                                                    "earcrate.sqlite", "jukebreaker.sqlite", "agent", "work"))
            if not looks or str(c) in seen:
                continue
            seen.add(str(c))
            roots.append(c)
        return roots

    def _classify_root(self, root: Path, homes: Dict[str, Path], db_taken: List[bool]) -> List[tuple]:
        actions: List[tuple] = []
        db_candidates: List[Path] = []
        for p in sorted(root.rglob("*")):
            if not p.is_file():
                continue
            rel = p.relative_to(root)
            parts = [x.lower() for x in rel.parts]
            name = p.name.lower()
            try:
                sz = p.stat().st_size
            except Exception:
                sz = 0
            if name in ("earcrate.sqlite", "jukebreaker.sqlite"):
                db_candidates.append(p); continue
            if name.endswith((".sqlite-wal", ".sqlite-shm")):
                actions.append(("scrub", p, homes["scrubbed"] / root.name / rel, "sqlite side file (regenerated on open)", sz)); continue
            if name in ("config_pointer.json", "config.json", "config.toml", "janitor_last.json") or name.endswith("probe.tmp"):
                actions.append(("scrub", p, homes["scrubbed"] / root.name / rel, "stale config/breadcrumb (regenerated)", sz)); continue
            if "cache" in parts and "analysis" in parts and name.endswith(".npz"):
                actions.append(("migrate", p, homes["cache_analysis"] / p.name, "analysis cache — kept as-is, no re-scan forced", sz)); continue
            if "cache" in parts and "transforms" in parts:
                actions.append(("migrate", p, homes["cache_transforms"] / p.name, "transform cache", sz)); continue
            if "renders" in parts and name.endswith(".wav"):
                actions.append(("migrate", p, homes["renders"] / p.name, "finished render", sz)); continue
            if "manifests" in parts and name.endswith(".json"):
                actions.append(("migrate", p, homes["manifests"] / p.name, "operation manifest", sz)); continue
            actions.append(("quarantine", p, homes["legacy"] / root.name / rel, "does not conform to the current layout", sz))
        if db_candidates:
            db_candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
            primary = db_candidates[0]
            if db_taken[0]:
                actions.append(("quarantine", primary, homes["legacy"] / root.name / primary.relative_to(root),
                                "another workspace's DB was already adopted; kept for reference", primary.stat().st_size))
            else:
                actions.append(("migrate-db", primary, homes["db"],
                                "reusable library DB — analysis, loops, atoms, and your human judgments", primary.stat().st_size))
                db_taken[0] = True
            for extra_db in db_candidates[1:]:
                actions.append(("quarantine", extra_db, homes["legacy"] / root.name / extra_db.relative_to(root),
                                "older/duplicate DB", extra_db.stat().st_size))
        return actions

    def _migration_actions(self, data: Dict[str, Any]) -> tuple:
        music = str(data.get("music_folder") or (self.config.master_root if self.config else "") or "").strip()
        workspace = str(data.get("workspace_folder") or "").strip() or (sibling_workspace(music) if music else "")
        if not workspace:
            raise ValueError("workspace_folder (or music_folder to derive the sibling) is required")
        homes = self._migration_homes(workspace)
        roots = self._legacy_source_roots(music, workspace, data.get("sources") or [])
        db_taken = [homes["db"].exists()]
        actions: List[tuple] = []
        for r in roots:
            actions += self._classify_root(r, homes, db_taken)
        return music, workspace, homes, roots, actions

    def plan_workspace_migration(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """DRY RUN: exactly what the cleanup will do, with nothing touched yet."""
        music, workspace, homes, roots, actions = self._migration_actions(data)
        buckets: Dict[str, Dict[str, int]] = {}
        total = 0
        for op, src, dst, reason, sz in actions:
            b = buckets.setdefault(op, {"count": 0, "bytes": 0})
            b["count"] += 1; b["bytes"] += int(sz); total += int(sz)
        sig = sha256_text(json_dumps([[op, str(src), str(dst)] for op, src, dst, _, _ in
                                      sorted(actions, key=lambda a: str(a[1]))]))
        preview = [{"op": op, "from": str(src), "to": str(dst), "why": reason, "bytes": int(sz)}
                   for op, src, dst, reason, sz in actions]
        mv = buckets.get("migrate", {}).get("count", 0) + buckets.get("migrate-db", {}).get("count", 0)
        qn = buckets.get("quarantine", {}).get("count", 0)
        sc = buckets.get("scrub", {}).get("count", 0)
        human = (f"From {len(roots)} old workspace(s): migrate {mv} reusable item(s) into "
                 f"{homes['workspace'].name}/, quarantine {qn} non-conforming item(s) under legacy/, "
                 f"scrub {sc} dead breadcrumb(s). Your music library is read-only and never touched. "
                 f"Nothing is deleted — legacy/ keeps everything.")
        return {"ok": True, "dry_run": True, "music_folder": music or None, "source_readonly": True,
                "new_workspace": str(homes["workspace"]), "homes": {k: str(v) for k, v in homes.items()},
                "legacy_sources": [str(r) for r in roots],
                "summary": {**buckets, "total_bytes": total, "sources": len(roots)},
                "signature": sig, "actions": preview, "human": human}

    def apply_workspace_migration(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """EXECUTE an approved plan. Journaled and reversible; nothing deleted."""
        plan = self.plan_workspace_migration(data)
        approved = str(data.get("signature") or "")
        if approved and approved != plan["signature"]:
            return {"ok": False, "error": "the workspace changed since you approved this plan; re-run the preview",
                    "expected_signature": plan["signature"]}
        homes = self._migration_homes(plan["new_workspace"])
        for key in ("agent", "work", "cache_analysis", "cache_transforms", "manifests", "renders", "legacy", "scrubbed"):
            homes[key].mkdir(parents=True, exist_ok=True)
        journal = homes["legacy"] / f"migration-{ulidish()}.jsonl"
        done: Dict[str, int] = {"migrate": 0, "migrate-db": 0, "quarantine": 0, "scrub": 0}
        errors: List[Dict[str, str]] = []
        for a in plan["actions"]:
            op, src, dst = a["op"], Path(a["from"]), Path(a["to"])
            try:
                if not src.exists():
                    continue
                dst.parent.mkdir(parents=True, exist_ok=True)
                if op == "migrate-db":
                    # Fold any WAL into the main file so a single copied .sqlite is
                    # self-consistent; the -wal/-shm side files are regenerable and
                    # are scrubbed separately, never copied onto the new DB.
                    with contextlib.suppress(Exception):
                        _cx = sqlite3.connect(str(src))
                        _cx.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                        _cx.close()
                    shutil.copy2(str(src), str(dst))
                    keep = homes["legacy"] / src.parent.name / src.name
                    keep.parent.mkdir(parents=True, exist_ok=True)
                    if keep.exists():
                        keep = keep.with_name(keep.stem + "__" + ulidish()[:6] + keep.suffix)
                    shutil.move(str(src), str(keep))
                    fsync_append_jsonl(journal, {"op": op, "copied_to": str(dst), "original_moved_to": str(keep), "from": str(src)})
                else:
                    if dst.exists():
                        dst = dst.with_name(dst.stem + "__" + ulidish()[:6] + dst.suffix)
                    shutil.move(str(src), str(dst))
                    fsync_append_jsonl(journal, {"op": op, "moved_to": str(dst), "restore_to": str(src)})
                done[op] = done.get(op, 0) + 1
            except Exception as exc:
                errors.append({"src": str(src), "error": str(exc)[:160]})
        return {"ok": not errors, "applied": done, "errors": errors, "journal": str(journal),
                "new_workspace": plan["new_workspace"],
                "note": ("reusable data moved to new homes; non-conforming quarantined under legacy/; "
                         "dead breadcrumbs in legacy/_scrubbed/; music library untouched; nothing deleted"),
                "next": "point EarCrate at this workspace to use the migrated library"}


    def ensure_config(self) -> Config:
        if not self.config:
            raise RuntimeError("earcrate is not configured yet")
        return self.config

    def ensure_layout(self) -> None:
        c = self.ensure_config()
        for p in [c.working_root, c.working_root / "organized", c.working_root / "renders", c.working_root / "edited", c.stems_root, c.playlists_root, c.agent_root, c.agent_root / "manifests", c.agent_root / "archive", c.agent_root / "cache" / "analysis", c.agent_root / "cache" / "transforms", c.agent_root / "logs"]:
            p.mkdir(parents=True, exist_ok=True)

    def connect_db(self) -> None:
        c = self.ensure_config()
        # v0.7.3 rename: prefer earcrate.sqlite; adopt a legacy jukebreaker.sqlite
        # in place if present so existing workspaces (analysis, loops, atoms)
        # survive the rename without migration.
        _db_path = c.agent_root / "earcrate.sqlite"
        _legacy = c.agent_root / "jukebreaker.sqlite"
        if not _db_path.exists() and _legacy.exists():
            _db_path = _legacy
        self.db = sqlite3.connect(str(_db_path), check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA foreign_keys=ON")
        self.create_schema()
        self.migrate_ear_atoms_per_profile()

    def conn(self) -> sqlite3.Connection:
        if self.db is None:
            self.connect_db()
        assert self.db is not None
        return self.db

    def migrate_ear_atoms_per_profile(self) -> None:
        """v0.7.8: ear_atoms had UNIQUE(loop_id) — one atom per loop GLOBALLY —
        which made personas mutually destructive (building resident B's crate
        overwrote or orphaned resident A's). Rebuild the table with
        UNIQUE(loop_id, taste_profile). Existing rows carry over verbatim."""
        db = self.db
        row = db.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='ear_atoms'").fetchone()
        if not row or "UNIQUE(loop_id, taste_profile)" in (row["sql"] or ""):
            return
        db.executescript("""
            BEGIN;
            CREATE TABLE ear_atoms_v2(
              id TEXT PRIMARY KEY,
              loop_id TEXT REFERENCES loops(id) ON DELETE CASCADE,
              file_id TEXT REFERENCES files(id) ON DELETE CASCADE,
              taste_profile TEXT NOT NULL DEFAULT 'girl_talk_v1',
              ear_role TEXT NOT NULL,
              render_role TEXT NOT NULL,
              start_s REAL NOT NULL, end_s REAL NOT NULL, bars INTEGER NOT NULL,
              bpm REAL, key_root INTEGER,
              score REAL NOT NULL,
              hook_score REAL DEFAULT 0, bed_score REAL DEFAULT 0,
              floor_score REAL DEFAULT 0, bass_score REAL DEFAULT 0, spark_score REAL DEFAULT 0,
              intelligibility REAL DEFAULT 0,
              low_share REAL DEFAULT 0, mid_share REAL DEFAULT 0, high_share REAL DEFAULT 0,
              loopability REAL DEFAULT 0, transient_density REAL DEFAULT 0,
              phrase_position TEXT DEFAULT 'downbeat',
              status TEXT CHECK(status IN ('candidate','approved','rejected')) DEFAULT 'candidate',
              preview_path TEXT,
              metrics_json TEXT NOT NULL DEFAULT '{}',
              created_at TEXT NOT NULL,
              UNIQUE(loop_id, taste_profile)
            );
            INSERT INTO ear_atoms_v2 SELECT * FROM ear_atoms;
            DROP TABLE ear_atoms;
            ALTER TABLE ear_atoms_v2 RENAME TO ear_atoms;
            COMMIT;
        """)

    def create_schema(self) -> None:
        db = self.conn()
        db.executescript(
            """
            CREATE TABLE IF NOT EXISTS files(
              id TEXT PRIMARY KEY,
              path TEXT UNIQUE NOT NULL,
              root TEXT NOT NULL CHECK(root IN ('master','working')),
              size_bytes INTEGER NOT NULL,
              mtime_ns INTEGER NOT NULL,
              sha256 TEXT,
              audio_sha256 TEXT,
              container TEXT, codec TEXT, bitrate_kbps INTEGER,
              sample_rate INTEGER, channels INTEGER, duration_s REAL,
              scanned_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS tags(
              file_id TEXT REFERENCES files(id) ON DELETE CASCADE,
              key TEXT NOT NULL, value TEXT,
              PRIMARY KEY(file_id, key)
            );
            CREATE TABLE IF NOT EXISTS tracks(
              id TEXT PRIMARY KEY,
              file_id TEXT UNIQUE REFERENCES files(id),
              artist TEXT, album_artist TEXT, album TEXT, title TEXT,
              track_no INTEGER, disc_no INTEGER, year INTEGER,
              confidence REAL,
              status TEXT CHECK(status IN ('raw','proposed','approved')) DEFAULT 'raw'
            );
            CREATE TABLE IF NOT EXISTS features(
              file_id TEXT PRIMARY KEY REFERENCES files(id) ON DELETE CASCADE,
              bpm REAL, bpm_confidence REAL,
              key_root INTEGER, key_mode INTEGER, key_confidence REAL,
              loudness_lufs REAL, energy REAL,
              beat_grid BLOB,
              downbeats BLOB,
              sections BLOB,
              vocal_likelihood REAL,
              analyzed_at TEXT, analyzer_version TEXT
            );
            CREATE TABLE IF NOT EXISTS loops(
              id TEXT PRIMARY KEY,
              file_id TEXT REFERENCES files(id),
              start_s REAL NOT NULL, end_s REAL NOT NULL,
              bars INTEGER NOT NULL,
              role TEXT CHECK(role IN ('drum_anchor','bass','harmony','vocal','texture','fx','full')),
              role_confidence REAL,
              score REAL NOT NULL,
              status TEXT CHECK(status IN ('candidate','approved','rejected')) DEFAULT 'candidate',
              stem TEXT CHECK(stem IN ('mix','vocals','drums','bass','other')) DEFAULT 'mix',
              created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS duplicates(
              group_id TEXT NOT NULL, file_id TEXT REFERENCES files(id),
              basis TEXT CHECK(basis IN ('exact_hash','audio_hash','tag_fuzzy')),
              keep_recommended INTEGER DEFAULT 0,
              PRIMARY KEY(group_id, file_id)
            );
            CREATE TABLE IF NOT EXISTS mashups(
              id TEXT PRIMARY KEY, name TEXT, seed INTEGER NOT NULL,
              params_json TEXT NOT NULL,
              arrangement_json TEXT NOT NULL,
              render_path TEXT, created_at TEXT NOT NULL,
              engine_version TEXT,
              arrangement_sha TEXT,
              render_report_path TEXT
            );
            CREATE TABLE IF NOT EXISTS ear_atoms(
              id TEXT PRIMARY KEY,
              loop_id TEXT REFERENCES loops(id) ON DELETE CASCADE,
              file_id TEXT REFERENCES files(id) ON DELETE CASCADE,
              taste_profile TEXT NOT NULL DEFAULT 'girl_talk_v1',
              ear_role TEXT NOT NULL,
              render_role TEXT NOT NULL,
              start_s REAL NOT NULL, end_s REAL NOT NULL, bars INTEGER NOT NULL,
              bpm REAL, key_root INTEGER,
              score REAL NOT NULL,
              hook_score REAL DEFAULT 0, bed_score REAL DEFAULT 0,
              floor_score REAL DEFAULT 0, bass_score REAL DEFAULT 0, spark_score REAL DEFAULT 0,
              intelligibility REAL DEFAULT 0,
              low_share REAL DEFAULT 0, mid_share REAL DEFAULT 0, high_share REAL DEFAULT 0,
              loopability REAL DEFAULT 0, transient_density REAL DEFAULT 0,
              phrase_position TEXT DEFAULT 'downbeat',
              status TEXT CHECK(status IN ('candidate','approved','rejected')) DEFAULT 'candidate',
              preview_path TEXT,
              metrics_json TEXT NOT NULL DEFAULT '{}',
              created_at TEXT NOT NULL,
              UNIQUE(loop_id, taste_profile)
            );
            CREATE TABLE IF NOT EXISTS compatibility_edges(
              id TEXT PRIMARY KEY,
              taste_profile TEXT NOT NULL DEFAULT 'girl_talk_v1',
              left_atom_id TEXT REFERENCES ear_atoms(id) ON DELETE CASCADE,
              right_atom_id TEXT REFERENCES ear_atoms(id) ON DELETE CASCADE,
              relation TEXT NOT NULL,
              score REAL NOT NULL,
              reasons_json TEXT NOT NULL DEFAULT '{}',
              created_at TEXT NOT NULL,
              UNIQUE(taste_profile,left_atom_id,right_atom_id,relation)
            );
            CREATE TABLE IF NOT EXISTS atom_judgments(
              atom_id TEXT REFERENCES ear_atoms(id) ON DELETE CASCADE,
              taste_profile TEXT NOT NULL,
              status TEXT CHECK(status IN ('approved','rejected','candidate')) NOT NULL,
              relabel_role TEXT, favorite INTEGER DEFAULT 0, locked INTEGER DEFAULT 0,
              reason TEXT, updated_at TEXT NOT NULL,
              PRIMARY KEY(atom_id,taste_profile)
            );
            CREATE TABLE IF NOT EXISTS pair_judgments(
              edge_id TEXT REFERENCES compatibility_edges(id) ON DELETE CASCADE,
              taste_profile TEXT NOT NULL,
              status TEXT CHECK(status IN ('approved','rejected','candidate')) NOT NULL,
              reason TEXT, updated_at TEXT NOT NULL,
              PRIMARY KEY(edge_id,taste_profile)
            );
            CREATE TABLE IF NOT EXISTS saved_plans(
              id TEXT PRIMARY KEY, name TEXT NOT NULL, taste_profile TEXT NOT NULL,
              plan_hash TEXT UNIQUE NOT NULL, plan_json TEXT NOT NULL, created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS kv(
              key TEXT PRIMARY KEY,
              value TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_files_sha256 ON files(sha256);
            CREATE INDEX IF NOT EXISTS idx_files_audio_sha256 ON files(audio_sha256);
            CREATE INDEX IF NOT EXISTS idx_loops_file_status ON loops(file_id,status);
            CREATE INDEX IF NOT EXISTS idx_ear_atoms_status_role ON ear_atoms(status,ear_role,taste_profile);
            CREATE INDEX IF NOT EXISTS idx_ear_atoms_loop ON ear_atoms(loop_id);
            CREATE INDEX IF NOT EXISTS idx_edges_profile_relation ON compatibility_edges(taste_profile,relation,score);
            CREATE INDEX IF NOT EXISTS idx_tracks_artist_album ON tracks(artist,album);
            """
        )
        # Additive migration for existing v0.3.x databases.
        for sql in [
            "ALTER TABLE mashups ADD COLUMN engine_version TEXT",
            "ALTER TABLE mashups ADD COLUMN arrangement_sha TEXT",
            "ALTER TABLE mashups ADD COLUMN render_report_path TEXT",
        ]:
            try:
                db.execute(sql)
            except sqlite3.OperationalError:
                pass
        db.commit()

    def kv_get_int(self, key: str, default: int = 0) -> int:
        row = self.conn().execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
        if not row:
            return default
        try:
            return int(row["value"])
        except Exception:
            return default

    def kv_set_int(self, key: str, value: int) -> None:
        self.conn().execute("INSERT OR REPLACE INTO kv(key,value) VALUES(?,?)", (key, str(int(value))))
        self.conn().commit()

    def next_render_seed(self, base_seed: int) -> int:
        counter = self.kv_get_int("render_counter", 0) + 1
        self.kv_set_int("render_counter", counter)
        return int(base_seed) + counter

    def validate_path_in_root(self, path: Path, allowed_root: Path) -> Path:
        rp = path.resolve()
        root = allowed_root.resolve()
        rp_s = os.path.normcase(str(rp))
        root_s = os.path.normcase(str(root))
        try:
            common = os.path.commonpath([rp_s, root_s])
        except ValueError:
            # Windows raises for paths on different drives. That is never inside the allowed root.
            raise ValueError(f"path escape refused: {rp} is not under {root}")
        if common != root_s:
            raise ValueError(f"path escape refused: {rp} is not under {root}")
        return rp

    def validate_not_master(self, path: Path) -> Path:
        c = self.ensure_config()
        rp = path.resolve()
        master = c.master_root.resolve()
        rp_s = os.path.normcase(str(rp))
        master_s = os.path.normcase(str(master))
        try:
            common = os.path.commonpath([rp_s, master_s])
        except ValueError:
            # Different Windows drives cannot be nested, so this is safely outside master_root.
            return rp
        if common == master_s:
            raise ValueError(f"master mutation refused: {rp}")
        return rp

    def doctor(self) -> Dict[str, Any]:
        c = self.ensure_config()
        checks = []
        for tool in ["ffmpeg", "ffprobe"]:
            checks.append({"name": tool, "ok": shutil.which(tool) is not None, "detail": shutil.which(tool) or "missing"})
        for name, p, mode in [("master_root", c.master_root, "read"), ("working_root", c.working_root, "write"), ("agent_root", c.agent_root, "write"), ("playlists_root", c.playlists_root, "write")]:
            ok = p.exists() and p.is_dir()
            detail = str(p)
            if ok and mode == "write":
                try:
                    test = p / f".jb_write_test_{uuid.uuid4().hex}"
                    test.write_text("ok", encoding="utf-8")
                    test.unlink()
                except Exception as exc:
                    ok = False
                    detail += f"; not writable: {exc}"
            checks.append({"name": name, "ok": bool(ok), "detail": detail})
        try:
            row = self.conn().execute("PRAGMA integrity_check").fetchone()
            checks.append({"name": "sqlite_integrity", "ok": row and row[0] == "ok", "detail": row[0] if row else "no row"})
        except Exception as exc:
            checks.append({"name": "sqlite_integrity", "ok": False, "detail": str(exc)})
        jl = c.agent_root / "janitor_last.json"
        if jl.exists():
            try:
                checks.append({"name": "janitor", "ok": True, "detail": json.loads(jl.read_text(encoding="utf-8")).get("summary", "ran")})
            except Exception:
                pass
        return {"ok": all(x["ok"] for x in checks), "checks": checks, "config": c.as_dict()}

    def startup_janitor(self) -> Dict[str, Any]:
        """Launch-time cleanup of everything old versions are known to leave behind.

        Auto-handled (regenerable or salvage-by-copy, never destroys user data):
        stale analysis/transform caches keyed to dead analyzer/engine versions,
        ' (N)' suffix-accretion duplicates in the organized tree (archived, not
        deleted), and legacy workspaces from earlier Jukebreaker/earcrate installs
        — their ingested masters are re-ingested (content-hash deduped) and their
        renders copied to renders/rescued/, after which the receipt marks the old
        folder safe to delete. Deleting the husk stays a human decision."""
        if not self.config:
            return {"ok": False, "reason": "no workspace configured yet"}
        c = self.config
        receipt: Dict[str, Any] = {"ok": True, "ran_at": now_utc()}
        # 1. caches for analyzer/engine versions that no longer exist
        stale_npz = 0
        for f in (c.agent_root / "cache" / "analysis").glob("*.npz"):
            if not f.name.endswith(f"-{ANALYZER_VERSION}.npz"):
                with contextlib.suppress(Exception):
                    f.unlink(); stale_npz += 1
        stale_tf = 0
        for d in (c.agent_root / "cache" / "transforms").glob("*"):
            if d.is_dir() and d.name != ENGINE_VERSION:
                with contextlib.suppress(Exception):
                    shutil.rmtree(d); stale_tf += 1
        receipt["stale_caches_purged"] = stale_npz + stale_tf
        # 2. ' (N)' accretion in the organized tree (pre-v0.7.4 organize bug):
        #    archived under agent/archive/janitor, never deleted
        org = c.working_root / "organized"
        moved = 0
        if org.exists():
            batch_dir = c.agent_root / "archive" / "janitor" / time.strftime("%Y%m%d-%H%M%S")
            for f in list(org.rglob("*")):
                if not f.is_file():
                    continue
                m = re.match(r"^(?P<base>.+?)(?: \(\d+\))+$", f.stem)
                if m and (f.with_name(m.group("base") + f.suffix)).exists():
                    dst = batch_dir / f.relative_to(org)
                    with contextlib.suppress(Exception):
                        dst.parent.mkdir(parents=True, exist_ok=True)
                        shutil.move(str(f), str(dst)); moved += 1
        receipt["duplicate_suffixes_archived"] = moved
        # 3. legacy workspaces from every location old versions used
        home = Path.home()
        candidates = [app_state_dir() / "workspace", home / "Jukebreaker", home / "jukebreaker",
                      home / "earcrate", home / "Earcrate"]
        if os.name == "nt":
            for drive in "CDEFGH":
                candidates += [Path(f"{drive}:\\Jukebreaker"), Path(f"{drive}:\\Earcrate"), Path(f"{drive}:\\earcrate")]
        active = {p.resolve() for p in [c.working_root, c.agent_root, c.working_root.parent, c.agent_root.parent]}
        legacy: List[Dict[str, Any]] = []
        for ws in candidates:
            try:
                if not ws.is_dir() or ws.resolve() in active:
                    continue
                marker = (ws / "agent" / "jukebreaker.sqlite").exists() or (ws / "jukebreaker.sqlite").exists()
                if not marker:
                    continue
                entry: Dict[str, Any] = {"path": str(ws), "salvaged_songs": 0, "rescued_renders": 0}
                ing = ws / "master" / "ingested"
                if ing.is_dir():
                    with contextlib.suppress(Exception):
                        r = self.ingest_sources({"sources": [str(ing)], "apply": True})
                        entry["salvaged_songs"] = int(r.get("planned") or 0)
                rdir = ws / "work" / "renders"
                if rdir.is_dir():
                    rescue = c.working_root / "renders" / "rescued" / safe_name(ws.name)
                    for w in rdir.glob("*.wav"):
                        dst = rescue / w.name
                        if not dst.exists():
                            with contextlib.suppress(Exception):
                                dst.parent.mkdir(parents=True, exist_ok=True)
                                shutil.copy2(w, dst); entry["rescued_renders"] += 1
                entry["safe_to_delete"] = True
                entry["note"] = "songs re-ingested + renders rescued; delete this folder yourself when ready"
                legacy.append(entry)
            except Exception as exc:
                legacy.append({"path": str(ws), "error": str(exc)[:120]})
        receipt["legacy_workspaces"] = legacy
        receipt["summary"] = (f"{stale_npz + stale_tf} stale cache file(s) purged, {moved} duplicate-suffix file(s) archived, "
                              f"{len(legacy)} legacy workspace(s) handled")
        with contextlib.suppress(Exception):
            (c.agent_root / "janitor_last.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8")
        if not self.status.get("busy"):
            self.set_status("janitor: " + receipt["summary"], None, False)
        return receipt

    def scan(self) -> Dict[str, Any]:
        c = self.ensure_config()
        self.set_status("scanning library", 0, True, None)
        db = self.conn()
        paths = [p for p in c.master_root.rglob("*") if p.is_file() and p.suffix.lower() in AUDIO_EXTS]
        total = len(paths)
        scanned = 0
        skipped = 0
        failed = []
        # Phase 1 (serial, cheap): stat-filter to changed/new files only.
        todo = []
        existing_by_path = {}
        for path in paths:
            st = path.stat()
            rp = str(path.resolve())
            existing = db.execute("SELECT id,size_bytes,mtime_ns FROM files WHERE path=?", (rp,)).fetchone()
            if existing and int(existing["size_bytes"]) == st.st_size and int(existing["mtime_ns"]) == st.st_mtime_ns:
                skipped += 1
                continue
            existing_by_path[rp] = existing["id"] if existing else None
            todo.append((path, st))
        # Phase 2 (parallel): ffprobe + tag reads are subprocess/IO bound, so a thread
        # pool overlaps them. 50k-file libraries go from hours to minutes on multi-core.
        def _probe_one(item):
            path, st = item
            try:
                probe = ffprobe_json(path)
                return {"ok": True, "path": path, "st": st, "probe": probe, "tags": self.read_tags(path)}
            except Exception as exc:
                return {"ok": False, "path": path, "error": str(exc)[:500]}
        workers = min(12, max(4, (os.cpu_count() or 2) * 2))
        results = []
        if todo:
            with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
                for i, res in enumerate(ex.map(_probe_one, todo)):
                    results.append(res)
                    if i % 25 == 0:
                        self.set_status(f"scanning {skipped + i + 1}/{total} \u00d7{workers} probes", (skipped + i + 1) / max(1, total), True)
        # Phase 3 (serial): all DB writes in the main thread.
        for idx, res in enumerate(results):
            path = res["path"]
            try:
                if not res["ok"]:
                    raise RuntimeError(res["error"])
                st = res["st"]
                probe = res["probe"]
                fmt = probe.get("format") or {}
                streams = [s for s in probe.get("streams", []) if s.get("codec_type") == "audio"]
                stream = streams[0] if streams else {}
                duration = float(stream.get("duration") or fmt.get("duration") or 0.0)
                bitrate = int((stream.get("bit_rate") or fmt.get("bit_rate") or 0) or 0) // 1000 or None
                fid = existing_by_path.get(str(path.resolve())) or ulidish()
                db.execute(
                    """INSERT INTO files(id,path,root,size_bytes,mtime_ns,container,codec,bitrate_kbps,sample_rate,channels,duration_s,scanned_at)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
                       ON CONFLICT(path) DO UPDATE SET size_bytes=excluded.size_bytes,mtime_ns=excluded.mtime_ns,container=excluded.container,codec=excluded.codec,bitrate_kbps=excluded.bitrate_kbps,sample_rate=excluded.sample_rate,channels=excluded.channels,duration_s=excluded.duration_s,scanned_at=excluded.scanned_at""",
                    (fid, str(path.resolve()), "master", st.st_size, st.st_mtime_ns, fmt.get("format_name"), stream.get("codec_name"), bitrate, int(stream.get("sample_rate") or 0), int(stream.get("channels") or 0), duration, now_utc()),
                )
                db.execute("DELETE FROM tags WHERE file_id=?", (fid,))
                tags = res["tags"]
                for k, v in tags.items():
                    db.execute("INSERT OR REPLACE INTO tags(file_id,key,value) VALUES(?,?,?)", (fid, k, v))
                meta = self.normalized_track_from_tags(path, tags)
                db.execute(
                    """INSERT INTO tracks(id,file_id,artist,album_artist,album,title,track_no,disc_no,year,confidence,status)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?)
                       ON CONFLICT(file_id) DO UPDATE SET artist=excluded.artist,album_artist=excluded.album_artist,album=excluded.album,title=excluded.title,track_no=excluded.track_no,disc_no=excluded.disc_no,year=excluded.year,confidence=excluded.confidence,status=excluded.status""",
                    (ulidish(), fid, meta.get("artist"), meta.get("album_artist"), meta.get("album"), meta.get("title"), meta.get("track_no"), meta.get("disc_no"), meta.get("year"), meta.get("confidence", 0.5), "raw"),
                )
                scanned += 1
            except Exception as exc:
                failed.append({"path": str(path), "error": str(exc)[:500]})
            finally:
                if idx % 50 == 0:
                    db.commit()
                    self.set_status(f"writing {idx+1}/{len(results)}", None, True)
        db.commit()
        self.set_status(f"scan complete: {scanned} updated, {skipped} unchanged, {len(failed)} failed", 1, False)
        return {"ok": True, "total": total, "updated": scanned, "skipped": skipped, "failed": failed[:50]}

    def read_tags(self, path: Path) -> Dict[str, str]:
        out: Dict[str, str] = {}
        try:
            mf = MutagenFile(str(path), easy=True)
            if mf and getattr(mf, "tags", None):
                for k, v in mf.tags.items():
                    if isinstance(v, (list, tuple)):
                        out[str(k).lower()] = "; ".join(str(x) for x in v)
                    else:
                        out[str(k).lower()] = str(v)
        except Exception:
            pass
        return out

    def normalized_track_from_tags(self, path: Path, tags: Dict[str, str]) -> Dict[str, Any]:
        title = tags.get("title") or path.stem
        artist = tags.get("artist") or tags.get("albumartist") or tags.get("album_artist")
        album = tags.get("album")
        album_artist = tags.get("albumartist") or tags.get("album_artist") or artist
        confidence = 0.7 if tags.get("title") else 0.45
        if not artist:
            parts = list(path.relative_to(self.ensure_config().master_root).parts)
            if len(parts) >= 3:
                artist, album = parts[-3], album or parts[-2]
                confidence = max(confidence, 0.55)
            m = re.match(r"(.+?)\s+-\s+(.+)$", path.stem)
            if m:
                artist = artist or m.group(1).strip()
                title = tags.get("title") or m.group(2).strip()
                confidence = max(confidence, 0.6)
        def clean(x: Optional[str]) -> Optional[str]:
            if not x:
                return None
            x = re.sub(r"\s+", " ", str(x)).strip()
            x = re.sub(r"\b(?:Feat|Ft)\.\b", "feat.", x)
            return x
        track_no = None
        raw_track = tags.get("tracknumber") or tags.get("track")
        if raw_track:
            with contextlib.suppress(Exception):
                track_no = int(str(raw_track).split("/")[0])
        year = None
        raw_year = tags.get("date") or tags.get("year")
        if raw_year:
            m = re.search(r"(19\d{2}|20\d{2})", raw_year)
            if m:
                year = int(m.group(1))
        return {"artist": clean(artist), "album_artist": clean(album_artist), "album": clean(album), "title": clean(title), "track_no": track_no, "disc_no": None, "year": year, "confidence": confidence}

    def analysis_seconds(self) -> int:
        """Configurable analysis depth. Defaults to 3 minutes, capped at the hard ceiling."""
        c = self.ensure_config()
        val = int(getattr(c, "analysis_seconds", 0) or DEFAULT_ANALYSIS_SECONDS)
        return max(30, min(MAX_ANALYSIS_SECONDS, val))

    def _worker_count(self) -> int:
        c = self.ensure_config()
        configured = int(getattr(c, "workers", 0) or 0)
        if configured > 0:
            return configured
        return max(1, (os.cpu_count() or 2) - 2)

    def analyze(self, limit: int = 0, force: bool = False) -> Dict[str, Any]:
        t_run = time.perf_counter()
        t_phase = time.perf_counter()
        phase_timings: Dict[str, float] = {}
        c = self.ensure_config()
        self.set_status("analyzing audio", 0, True, None)
        db = self.conn()
        rows = db.execute(
            """SELECT f.* FROM files f LEFT JOIN features ft ON ft.file_id=f.id
               WHERE ft.file_id IS NULL OR ft.analyzer_version!=? OR ?
               ORDER BY f.path LIMIT ?""",
            (ANALYZER_VERSION, 1 if force else 0, limit if limit and limit > 0 else 1000000000),
        ).fetchall()
        total = len(rows)
        phase_timings["select_rows_seconds"] = round(time.perf_counter() - t_phase, 3)
        t_phase = time.perf_counter()
        done = 0
        failed: List[Dict[str, Any]] = []
        max_sec = self.analysis_seconds()
        cache_dir = c.agent_root / "cache" / "analysis"

        # Fast path: anything already cached is loaded in-process (cheap, no DSP).
        jobs: List[Dict[str, Any]] = []
        for row in rows:
            path = Path(row["path"])
            file_sha = row["sha256"] or sha256_file(path)
            if not row["sha256"]:
                db.execute("UPDATE files SET sha256=? WHERE id=?", (file_sha, row["id"]))
            cache_path = cache_dir / f"{file_sha}-{ANALYZER_VERSION}.npz"
            if cache_path.exists():
                try:
                    self._store_from_cache(row["id"], cache_path)
                    done += 1
                    continue
                except Exception:
                    pass  # fall through to recompute
            jobs.append({"file_id": row["id"], "path": str(path), "sha256": file_sha,
                         "sr": c.sample_rate, "max_sec": max_sec, "duration": float(row["duration_s"] or 0),
                         "cache_path": str(cache_path)})
        db.commit()
        phase_timings["cache_load_seconds"] = round(time.perf_counter() - t_phase, 3)
        t_phase = time.perf_counter()
        cache_hits = done
        self.set_status(f"analyzing {done}/{total} (cache) \u2022 {len(jobs)} to compute", done / max(1, total), True)

        workers = min(self._worker_count(), max(1, len(jobs)))
        results: List[Dict[str, Any]] = []
        used_parallel = False
        t_eta = time.perf_counter()
        def _eta(computed: int) -> str:
            if computed <= 0:
                return ""
            left = (time.perf_counter() - t_eta) / computed * (len(jobs) - computed)
            return f" \u00b7 ~{int(left // 60)}m{int(left % 60):02d}s left"
        if jobs and workers > 1:
            try:
                mp = __import__("multiprocessing")
                # fork on Unix: workers inherit the already-imported module, so no
                # re-import and no risk of a child re-running server startup. spawn on
                # Windows (fork unavailable there). Serial fallback below covers any failure.
                method = "fork" if ("fork" in mp.get_all_start_methods() and os.name != "nt") else "spawn"
                ctx = mp.get_context(method)
                with concurrent.futures.ProcessPoolExecutor(max_workers=workers, mp_context=ctx) as ex:
                    futs = {ex.submit(analyze_file_worker, job): job for job in jobs}
                    for i, fut in enumerate(concurrent.futures.as_completed(futs)):
                        results.append(fut.result())
                        self.set_status(f"analyzing {done + i + 1}/{total} \u00d7{workers} cores{_eta(i + 1)}", (done + i + 1) / max(1, total), True)
                used_parallel = True
            except Exception as exc:
                # Serial fallback: any pool/spawn failure must not break analysis.
                self.set_status(f"parallel pool unavailable ({str(exc)[:60]}); using single core", None, True)
                results = []
        if jobs and not used_parallel:
            for i, job in enumerate(jobs):
                results.append(analyze_file_worker(job))
                self.set_status(f"analyzing {done + i + 1}/{total} (1 core){_eta(i + 1)}", (done + i + 1) / max(1, total), True)
        phase_timings["compute_seconds"] = round(time.perf_counter() - t_phase, 3)
        t_phase = time.perf_counter()

        # Parent process does all DB writes from returned features.
        for r in results:
            if r.get("ok"):
                f = r["features"]
                self.store_features(
                    r["file_id"], f["bpm"], f["bpm_confidence"], f["key_root"], f["key_mode"], f["key_confidence"],
                    f["loudness_lufs"], f["energy"], np.frombuffer(f["beats"], dtype=np.float32),
                    np.frombuffer(f["downbeats"], dtype=np.float32), f["sections"], f["vocal_likelihood"])
                done += 1
            else:
                failed.append({"path": r.get("path"), "error": r.get("error")})
        db.commit()
        phase_timings["db_write_seconds"] = round(time.perf_counter() - t_phase, 3)
        phase_timings["total_seconds"] = round(time.perf_counter() - t_run, 3)
        mode = f"{workers} cores" if used_parallel else "1 core"
        self.set_status(f"analysis complete: {done} analyzed, {len(failed)} failed ({mode})", 1, False)
        return {"ok": True, "analyzed": done, "failed": failed[:50], "parallel": used_parallel, "workers": workers, "analysis_seconds": max_sec, "cache_hits": cache_hits, "compute_jobs": len(jobs), "phase_timings": phase_timings}

    def _store_from_cache(self, file_id: str, cache_path: Path) -> None:
        data = np.load(cache_path, allow_pickle=False)
        sections = json.loads(str(data["sections_json"]))
        self.store_features(file_id, float(data["bpm"]), float(data["bpm_confidence"]), int(data["key_root"]),
                            int(data["key_mode"]), float(data["key_confidence"]), float(data["loudness_lufs"]),
                            float(data["energy"]), data["beats"], data["downbeats"], sections, float(data["vocal_likelihood"]))

    def analyze_one(self, row: sqlite3.Row) -> None:
        c = self.ensure_config()
        db = self.conn()
        path = Path(row["path"])
        file_sha = row["sha256"] or sha256_file(path)
        if not row["sha256"]:
            db.execute("UPDATE files SET sha256=? WHERE id=?", (file_sha, row["id"]))
        cache_path = c.agent_root / "cache" / "analysis" / f"{file_sha}-{ANALYZER_VERSION}.npz"
        if cache_path.exists():
            data = np.load(cache_path, allow_pickle=False)
            sections = json.loads(str(data["sections_json"]))
            self.store_features(row["id"], float(data["bpm"]), float(data["bpm_confidence"]), int(data["key_root"]), int(data["key_mode"]), float(data["key_confidence"]), float(data["loudness_lufs"]), float(data["energy"]), data["beats"], data["downbeats"], sections, float(data["vocal_likelihood"]))
            return
        duration = float(row["duration_s"] or 0)
        max_sec = self.analysis_seconds()
        decode_dur = min(duration, max_sec) if duration > 0 else max_sec
        y = decode_audio(path, c.sample_rate, duration=decode_dur)
        if y.size > c.sample_rate * max_sec:
            y = y[: c.sample_rate * max_sec]
        # avoid pathological silence
        if float(np.max(np.abs(y))) < 1e-5:
            bpm, bpm_conf, beats, downbeats = 120.0, 0.0, np.array([], dtype=np.float32), np.array([], dtype=np.float32)
            key_root, key_mode, key_conf = 0, 1, 0.0
            loudness, energy, vocal_like = -70.0, 0.0, 0.0
            sections = []
        else:
            onset_env = librosa.onset.onset_strength(y=y, sr=c.sample_rate)
            tempo_val = librosa.feature.tempo(onset_envelope=onset_env, sr=c.sample_rate, aggregate=np.median)
            bpm = float(np.atleast_1d(tempo_val)[0])
            while bpm < 70:
                bpm *= 2
            while bpm > 180:
                bpm /= 2
            tempo2, beat_frames = librosa.beat.beat_track(y=y, sr=c.sample_rate, onset_envelope=onset_env, units="frames", trim=False)
            beat_times = librosa.frames_to_time(beat_frames, sr=c.sample_rate).astype(np.float32)
            if beat_times.size >= 8:
                intervals = np.diff(beat_times)
                bpm_conf = float(max(0.0, min(1.0, 1.0 - (np.std(intervals) / (np.mean(intervals) + 1e-9)))))
            else:
                bpm_conf = 0.2
            downbeats = self.estimate_downbeats(y, c.sample_rate, beat_frames)
            chroma = librosa.feature.chroma_stft(y=y, sr=c.sample_rate)
            key_root, key_mode, key_conf = krumhansl_key(chroma)
            energy = float(np.sqrt(np.mean(y ** 2)))
            with contextlib.suppress(Exception):
                meter = pyln.Meter(c.sample_rate)
                loudness = float(meter.integrated_loudness(y.astype(np.float64)))
            if "loudness" not in locals() or not np.isfinite(loudness):
                loudness = float(20 * np.log10(max(1e-9, energy)))
            vocal_like = self.vocal_likelihood(y, c.sample_rate)
            sections = self.estimate_sections(y, c.sample_rate, beat_times, downbeats)
            beats = beat_times
        np.savez_compressed(
            cache_path,
            bpm=np.float32(bpm), bpm_confidence=np.float32(bpm_conf), key_root=np.int16(key_root), key_mode=np.int16(key_mode), key_confidence=np.float32(key_conf), loudness_lufs=np.float32(loudness), energy=np.float32(energy), beats=beats.astype(np.float32), downbeats=downbeats.astype(np.float32), sections_json=json.dumps(sections, ensure_ascii=False), vocal_likelihood=np.float32(vocal_like)
        )
        self.store_features(row["id"], bpm, bpm_conf, key_root, key_mode, key_conf, loudness, energy, beats, downbeats, sections, vocal_like)

    def estimate_downbeats(self, y: np.ndarray, sr: int, beat_frames: np.ndarray) -> np.ndarray:
        return _estimate_downbeats(y, sr, beat_frames)

    def vocal_likelihood(self, y: np.ndarray, sr: int) -> float:
        return _vocal_likelihood(y, sr)

    def estimate_sections(self, y: np.ndarray, sr: int, beats: np.ndarray, downbeats: np.ndarray) -> List[Dict[str, Any]]:
        return _estimate_sections(y, sr, beats, downbeats)

    def store_features(self, file_id: str, bpm: float, bpm_conf: float, key_root: int, key_mode: int, key_conf: float, loudness: float, energy: float, beats: np.ndarray, downbeats: np.ndarray, sections: List[Dict[str, Any]], vocal_like: float) -> None:
        self.conn().execute(
            """INSERT INTO features(file_id,bpm,bpm_confidence,key_root,key_mode,key_confidence,loudness_lufs,energy,beat_grid,downbeats,sections,vocal_likelihood,analyzed_at,analyzer_version)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(file_id) DO UPDATE SET bpm=excluded.bpm,bpm_confidence=excluded.bpm_confidence,key_root=excluded.key_root,key_mode=excluded.key_mode,key_confidence=excluded.key_confidence,loudness_lufs=excluded.loudness_lufs,energy=excluded.energy,beat_grid=excluded.beat_grid,downbeats=excluded.downbeats,sections=excluded.sections,vocal_likelihood=excluded.vocal_likelihood,analyzed_at=excluded.analyzed_at,analyzer_version=excluded.analyzer_version""",
            (file_id, bpm, bpm_conf, key_root, key_mode, key_conf, loudness, energy, array_to_blob(beats.astype(np.float32)), array_to_blob(downbeats.astype(np.float32)), json.dumps(sections, ensure_ascii=False).encode("utf-8"), vocal_like, now_utc(), ANALYZER_VERSION),
        )

    def extract_loops(self, limit: int = 0, auto_approve: bool = False, force: bool = False) -> Dict[str, Any]:
        c = self.ensure_config()
        self.set_status("extracting loop candidates", 0, True, None)
        db = self.conn()
        # v0.7.3: when not forcing, exclude files that already have loops at the
        # SQL layer. The per-row COUNT(*) skip below still guards correctness, but
        # this stops each incremental pass from walking the entire files table and
        # probing every already-extracted row (the flat cost behind the miss-loop
        # ear_crate/extract stages on large libraries).
        if force:
            rows = db.execute(
                """SELECT f.*, ft.bpm, ft.beat_grid, ft.downbeats, ft.sections, ft.vocal_likelihood
                   FROM files f JOIN features ft ON ft.file_id=f.id
                   ORDER BY f.path LIMIT ?""",
                (limit if limit and limit > 0 else 1000000000,),
            ).fetchall()
        else:
            rows = db.execute(
                """SELECT f.*, ft.bpm, ft.beat_grid, ft.downbeats, ft.sections, ft.vocal_likelihood
                   FROM files f JOIN features ft ON ft.file_id=f.id
                   WHERE f.id NOT IN (SELECT DISTINCT file_id FROM loops)
                   ORDER BY f.path LIMIT ?""",
                (limit if limit and limit > 0 else 1000000000,),
            ).fetchall()
        total_inserted = 0
        failed = []
        for idx, row in enumerate(rows):
            try:
                if force:
                    db.execute("DELETE FROM loops WHERE file_id=? AND stem='mix'", (row["id"],))
                existing = db.execute("SELECT COUNT(*) FROM loops WHERE file_id=?", (row["id"],)).fetchone()[0]
                if existing and not force:
                    continue
                inserted = self.extract_loops_one(row, auto_approve=auto_approve)
                total_inserted += inserted
            except Exception as exc:
                failed.append({"path": row["path"], "error": str(exc)[:500]})
            finally:
                if idx % 2 == 0:
                    db.commit()
                self.set_status(f"extracting loops {idx+1}/{len(rows)}", (idx + 1) / max(1, len(rows)), True)
        db.commit()
        self.set_status(f"loop extraction complete: {total_inserted} candidates, {len(failed)} failed", 1, False)
        return {"ok": True, "inserted": total_inserted, "failed": failed[:50]}

    def extract_loops_one(self, row: sqlite3.Row, auto_approve: bool = True) -> int:
        c = self.ensure_config()
        db = self.conn()
        beats = blob_to_array(row["beat_grid"])
        downbeats = blob_to_array(row["downbeats"])
        if downbeats.size < 2 or beats.size < 16:
            return 0
        duration = float(row["duration_s"] or 0)
        path = Path(row["path"])
        # Decode once for candidate scoring. Cap to analysis length.
        y = decode_audio(path, c.sample_rate, duration=min(duration or MAX_ANALYSIS_SECONDS, MAX_ANALYSIS_SECONDS))
        max_time = y.size / c.sample_rate
        candidates: List[Dict[str, Any]] = []
        for bars in [8, 4, 2, 1]:
            step = max(1, bars)
            for start in downbeats[::step]:
                end = self.loop_end_from_beats(float(start), bars, beats)
                if end is None or end > max_time or end - start < 0.5:
                    continue
                seg = y[int(start * c.sample_rate) : int(end * c.sample_rate)]
                if seg.size < c.sample_rate // 2:
                    continue
                score, role, role_conf = self.score_loop(seg, c.sample_rate, bars, float(row["vocal_likelihood"] or 0.0))
                if score <= 0:
                    continue
                candidates.append({"start": float(start), "end": float(end), "bars": bars, "score": score, "role": role, "role_confidence": role_conf})
        candidates.sort(key=lambda x: (x["score"], x["bars"]), reverse=True)
        selected: List[Dict[str, Any]] = []
        for cand in candidates:
            if len(selected) >= 12:
                break
            overlap_bad = False
            for s in selected:
                inter = max(0.0, min(cand["end"], s["end"]) - max(cand["start"], s["start"]))
                smaller = min(cand["end"] - cand["start"], s["end"] - s["start"])
                if smaller > 0 and inter / smaller > 0.5:
                    overlap_bad = True
                    break
            if not overlap_bad:
                selected.append(cand)
        # Try to preserve at least two roles if candidates contain them.
        roles = {x["role"] for x in selected}
        if len(roles) < 2:
            for cand in candidates:
                if cand["role"] not in roles and all(not (max(0.0, min(cand["end"], s["end"]) - max(cand["start"], s["start"])) / max(0.001, min(cand["end"] - cand["start"], s["end"] - s["start"])) > 0.5) for s in selected):
                    if len(selected) >= 12:
                        selected[-1] = cand
                    else:
                        selected.append(cand)
                    break
        status = "approved" if auto_approve else "candidate"
        for cand in selected[:12]:
            db.execute(
                "INSERT INTO loops(id,file_id,start_s,end_s,bars,role,role_confidence,score,status,stem,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (ulidish(), row["id"], cand["start"], cand["end"], cand["bars"], cand["role"], cand["role_confidence"], cand["score"], status, "mix", now_utc()),
            )
        return len(selected[:12])

    def loop_end_from_beats(self, start: float, bars: int, beats: np.ndarray) -> Optional[float]:
        idx = int(np.searchsorted(beats, start - 0.01, side="left"))
        target = idx + bars * 4
        if target < beats.size:
            return float(beats[target])
        return None

    def score_loop(self, seg: np.ndarray, sr: int, bars: int, track_vocal_like: float) -> Tuple[float, str, float]:
        """Score one downbeat-aligned loop candidate.

        v0.2.8 is calibrated against a dense collage reference rather than a plain
        loop-sequencer target. The practical change is that the highest score is no
        longer automatically the longest clean loop. Four-bar and two-bar hook
        material can outrank eight-bar beds when it has midband salience, onset
        movement, and enough energy to read as recognizable.
        """
        if float(np.max(np.abs(seg))) < 1e-5:
            return 0.0, "full", 0.0
        win = min(seg.size // 4, sr // 2)
        if win < 512:
            return 0.0, "full", 0.0

        h1 = np.abs(np.fft.rfft(seg[:win]))
        h2 = np.abs(np.fft.rfft(seg[-win:]))
        dist = np.linalg.norm(h1 / (np.linalg.norm(h1) + 1e-9) - h2 / (np.linalg.norm(h2) + 1e-9))
        loopability = float(max(0.0, min(1.0, 1.0 - dist / 1.55)))

        onset = librosa.onset.onset_detect(y=seg, sr=sr, units="time", backtrack=False)
        onset_density = float(onset.size / max(0.25, seg.size / sr))
        if onset.size >= 3:
            intervals = np.diff(onset)
            rhythmic_stability = float(max(0.0, min(1.0, 1.0 - np.std(intervals) / (np.mean(intervals) + 1e-9))))
        else:
            rhythmic_stability = 0.32

        S = np.abs(librosa.stft(seg, n_fft=2048, hop_length=1024))
        freqs = librosa.fft_frequencies(sr=sr, n_fft=2048)
        full_total = float(np.sum(S) + 1e-9)
        vocal_band = float(np.sum(S[(freqs >= 300) & (freqs <= 3400), :]) / full_total)
        bass_band = float(np.sum(S[freqs <= 200, :]) / full_total)
        high_band = float(np.sum(S[freqs >= 5000, :]) / full_total)
        centroid = float(np.mean(librosa.feature.spectral_centroid(S=S, sr=sr)))
        flat_mean = float(np.mean(librosa.feature.spectral_flatness(S=S)))
        rms = float(np.sqrt(np.mean(np.square(seg)))) if seg.size else 0.0
        peak = float(np.max(np.abs(seg))) + 1e-9
        crest = float(peak / max(rms, 1e-9))
        energy_score = float(max(0.0, min(1.0, rms / 0.13)))

        percussive_ratio = float(max(0.0, min(1.0, 0.60 * min(1.0, onset_density / 5.8) + 0.40 * min(1.0, flat_mean / 0.075))))
        drum_score = percussive_ratio if (percussive_ratio >= 0.58 and vocal_band < 0.58) else 0.0

        mid_salience = max(0.0, min(1.0, (vocal_band - 0.34) / 0.24))
        movement = max(0.0, min(1.0, onset_density / 4.2))
        not_subheavy = max(0.0, min(1.0, 1.0 - max(0.0, bass_band - 0.22) / 0.20))
        vocal_score = max(0.0, min(1.0, 0.46 * mid_salience + 0.22 * movement + 0.18 * energy_score + 0.14 * track_vocal_like))
        vocal_score *= not_subheavy

        bass_score = max(0.0, min(1.0, (bass_band - 0.10) / 0.22)) * max(0.25, 1.0 - high_band)
        harmony_score = max(0.0, min(1.0, 0.55 * (1.0 - percussive_ratio) + 0.30 * mid_salience + 0.15 * (1.0 - bass_score)))
        texture_score = max(0.0, min(1.0, 0.42 * min(1.0, centroid / 5200.0) + 0.35 * high_band / 0.30 + 0.23 * flat_mean / 0.06))

        role_scores = {
            "drum_anchor": drum_score,
            "bass": bass_score,
            "vocal": vocal_score,
            "harmony": harmony_score,
            "texture": texture_score,
            "full": 0.50 + 0.10 * energy_score,
        }
        role = max(role_scores, key=role_scores.get)
        role_conf = float(role_scores[role])
        if role == "drum_anchor" and role_conf < 0.58:
            role = "full"
            role_conf = max(role_conf, 0.55)
        elif role != "drum_anchor" and role_conf < 0.62:
            role = "full"
            role_conf = max(role_conf, 0.55)

        length_bonus = {8: 0.88, 4: 1.00, 2: 0.72, 1: 0.42}.get(bars, 0.35)
        hook_pop = max(0.0, min(1.0, 0.38 * vocal_score + 0.27 * movement + 0.22 * energy_score + 0.13 * min(1.0, crest / 5.0)))
        spectral_distinctness = max(role_conf, hook_pop if role in ("vocal", "full", "texture") else role_conf)
        score = 0.28 * loopability + 0.22 * rhythmic_stability + 0.22 * spectral_distinctness + 0.18 * length_bonus + 0.10 * hook_pop
        return float(score), role, role_conf

    def list_tracks(self, limit: int = 500) -> Dict[str, Any]:
        rows = self.conn().execute(
            """SELECT f.id file_id,f.path,f.duration_s,f.codec,f.sample_rate,t.artist,t.album,t.title,ft.bpm,ft.key_root,ft.key_mode,ft.vocal_likelihood,
                      (SELECT COUNT(*) FROM loops l WHERE l.file_id=f.id) loop_count
               FROM files f LEFT JOIN tracks t ON t.file_id=f.id LEFT JOIN features ft ON ft.file_id=f.id
               ORDER BY f.path LIMIT ?""",
            (limit,),
        ).fetchall()
        return {"items": [dict(r) for r in rows]}

    def list_loops(self, status: str = "", limit: int = 1000) -> Dict[str, Any]:
        db = self.conn()
        where = "WHERE l.status=?" if status else ""
        args = (status, limit) if status else (limit,)
        rows = db.execute(
            f"""SELECT l.*, f.path, t.artist, t.title FROM loops l JOIN files f ON f.id=l.file_id LEFT JOIN tracks t ON t.file_id=f.id {where} ORDER BY l.score DESC LIMIT ?""",
            args,
        ).fetchall()
        counts_rows = db.execute("SELECT status, COUNT(*) AS n FROM loops GROUP BY status").fetchall()
        counts = {"candidate": 0, "approved": 0, "rejected": 0, "total": 0}
        for r in counts_rows:
            counts[str(r["status"])] = int(r["n"])
            counts["total"] += int(r["n"])
        return {"items": [dict(r) for r in rows], "counts": counts}

    def set_loop_status(self, loop_id: str, status: str) -> Dict[str, Any]:
        if status not in {"candidate", "approved", "rejected"}:
            raise ValueError("invalid loop status")
        self.conn().execute("UPDATE loops SET status=? WHERE id=?", (status, loop_id))
        self.conn().commit()
        return {"ok": True}

    def bulk_loop_status(self, status: str, from_status: str = "candidate") -> Dict[str, Any]:
        if status not in {"candidate", "approved", "rejected"}:
            raise ValueError("invalid loop status")
        if from_status not in {"", "candidate", "approved", "rejected"}:
            raise ValueError("invalid source status")
        if status == "approved":
            raise ValueError("bulk approval is disabled; use quota approval so the hot pool stays bounded and role-balanced")
        db = self.conn()
        if from_status:
            cur = db.execute("UPDATE loops SET status=? WHERE status=?", (status, from_status))
        else:
            cur = db.execute("UPDATE loops SET status=?", (status,))
        db.commit()
        return {"ok": True, "updated": cur.rowcount, "status": status, "from_status": from_status or "all"}

    def propose_playlist(self, name: str, query: str, target_minutes: int = 60) -> Dict[str, Any]:
        c = self.ensure_config()
        db = self.conn()
        q = query.lower()
        clauses = []
        params: List[Any] = []
        bpm_match = re.search(r"(\d{2,3})\s*[-–]\s*(\d{2,3})\s*bpm", q)
        if bpm_match:
            clauses.append("ft.bpm BETWEEN ? AND ?")
            params += [float(bpm_match.group(1)), float(bpm_match.group(2))]
        if "instrumental" in q or "low vocal" in q:
            clauses.append("ft.vocal_likelihood < ?")
            params.append(0.35)
        if "vocal" in q and "low vocal" not in q:
            clauses.append("ft.vocal_likelihood >= ?")
            params.append(0.35)
        where = "WHERE " + " AND ".join(clauses) if clauses else ""
        rows = db.execute(
            f"""SELECT f.id,f.path,f.duration_s FROM files f LEFT JOIN features ft ON ft.file_id=f.id {where}
                ORDER BY COALESCE(ft.energy,0) DESC, f.path""",
            params,
        ).fetchall()
        entries = []
        total = 0.0
        for r in rows:
            entries.append(r["id"])
            total += float(r["duration_s"] or 180)
            if total >= target_minutes * 60:
                break
        manifest = self.write_manifest("librarian", c.seed, f"Create playlist {name}", [{"op_id": ulidish(), "type": "create_playlist", "args": {"name": name, "entries": entries, "format": "m3u8"}, "preconditions": {}}])
        return {"ok": True, "manifest": manifest, "entries": len(entries)}


    def outcome_params(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Translate plain-language outcome controls into the low-level arranger knobs."""
        preset = str(data.get("preset") or "party_cutup")
        presets = {
            "clean_blend": {"chaos": 38, "key_strictness": 82, "pitch_shift_budget": 1, "stretch_budget": 7, "genre_whiplash": 25, "vocal_density": 45, "anchor_stability": 88, "recognizability_bias": 55},
            "hook_ride": {"chaos": 58, "key_strictness": 72, "pitch_shift_budget": 2, "stretch_budget": 10, "genre_whiplash": 48, "vocal_density": 88, "anchor_stability": 78, "recognizability_bias": 86},
            "party_cutup": {"chaos": 72, "key_strictness": 64, "pitch_shift_budget": 2, "stretch_budget": 8, "genre_whiplash": 74, "vocal_density": 78, "anchor_stability": 72, "recognizability_bias": 84},
            "max_chaos": {"chaos": 86, "key_strictness": 54, "pitch_shift_budget": 2, "stretch_budget": 9, "genre_whiplash": 90, "vocal_density": 80, "anchor_stability": 44, "recognizability_bias": 88},
        }
        out = dict(presets.get(preset, presets["party_cutup"]))

        edit_speed = str(data.get("edit_speed") or "fast")
        if edit_speed == "steady":
            out["chaos"] = min(out["chaos"], 45)
        elif edit_speed == "active":
            out["chaos"] = max(52, min(out["chaos"], 68))
        elif edit_speed == "hypercut":
            out["chaos"] = max(out["chaos"], 84)

        hooks = str(data.get("hooks") or "hooky")
        if hooks == "sparse":
            out["vocal_density"] = 35; out["recognizability_bias"] = max(50, out["recognizability_bias"] - 15)
        elif hooks == "balanced":
            out["vocal_density"] = max(58, min(out["vocal_density"], 72))
        elif hooks == "hooky":
            out["vocal_density"] = max(out["vocal_density"], 84); out["recognizability_bias"] = max(out["recognizability_bias"], 82)

        jump = str(data.get("genre_jump") or "whiplash")
        if jump == "smooth":
            out["genre_whiplash"] = 22; out["key_strictness"] = max(out["key_strictness"], 78)
        elif jump == "mixed":
            out["genre_whiplash"] = max(45, min(out["genre_whiplash"], 62))
        elif jump == "whiplash":
            out["genre_whiplash"] = max(out["genre_whiplash"], 82)

        safety = str(data.get("safety") or "loose")
        if safety == "tight":
            out["key_strictness"] = max(out["key_strictness"], 82); out["pitch_shift_budget"] = min(out["pitch_shift_budget"], 1); out["stretch_budget"] = min(out["stretch_budget"], 7)
        elif safety == "loose":
            out["key_strictness"] = max(58, min(out["key_strictness"], 72)); out["pitch_shift_budget"] = min(max(out["pitch_shift_budget"], 2), 2); out["stretch_budget"] = min(max(out["stretch_budget"], 7), 8.5)
        elif safety == "wild":
            out["key_strictness"] = min(out["key_strictness"], 54); out["pitch_shift_budget"] = 2; out["stretch_budget"] = 9

        backbone = str(data.get("backbone") or "survive")
        if backbone == "locked":
            out["anchor_stability"] = 92
        elif backbone == "survive":
            out["anchor_stability"] = max(72, min(out["anchor_stability"], 84))
        elif backbone == "restless":
            out["anchor_stability"] = min(out["anchor_stability"], 48)

        default_candidates = 12
        params = {
            "name": str(data.get("name") or "EarCrate Sketch"),
            "target_seconds": int(data.get("target_seconds") or 180),
            "bpm": float(data.get("bpm") or 0),
            "drama": int(data.get("drama") or (82 if preset in ("party_cutup", "max_chaos") else 58)),
            "candidate_count": int(data.get("candidate_count") or data.get("arrangement_candidates") or default_candidates),
            "max_aux_decks": int(data.get("max_aux_decks") or 3),
            "quality_mode": str(data.get("quality_mode") or "stable_deck"),
            "strict_world_roles": bool(data.get("strict_world_roles", True)),
            "post_render_gate": bool(data.get("post_render_gate", True)),
            "lookahead_seconds": int(data.get("lookahead_seconds") or 90),
            **out,
        }
        if data.get("seed") not in (None, "", 0, "0"):
            params["seed"] = int(data.get("seed"))
        return params

    def one_click_mix(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Cold-start jam path. The only composer is the TasteSpec engine; the
        legacy two-world arranger was removed in the v2 cut."""
        return self.one_click_taste_mix(data)

    def auto_approve_quota(self, max_loops: int = 60) -> Dict[str, Any]:
        """Approve a balanced hot pool instead of bulk-approving the landfill."""
        db = self.conn()
        db.execute("UPDATE loops SET status='candidate' WHERE status='approved'")
        rows = [dict(r) for r in db.execute("""SELECT l.*, ft.vocal_likelihood
                                      FROM loops l JOIN files f ON f.id=l.file_id
                                      LEFT JOIN features ft ON ft.file_id=f.id
                                      WHERE l.status='candidate'
                                      ORDER BY l.score DESC""").fetchall()]
        chosen: Dict[str, Dict[str, Any]] = {}
        by_track: Dict[str, int] = {}
        role_min = {"drum_anchor": 4, "bass": 3, "harmony": 4, "full": 4}
        unsat = []
        def eligible(r: Dict[str, Any]) -> bool:
            role = r.get("role")
            if role == "vocal" and float(r.get("vocal_likelihood") or 0) < 0.65:
                return False
            if role == "drum_anchor" and float(r.get("role_confidence") or 0) < 0.55:
                return False
            return True
        for role, minimum in role_min.items():
            candidates = [r for r in rows if eligible(r) and (r.get("role") == role or (role == "harmony" and r.get("role") in ("harmony", "full")))]
            for r in candidates[:minimum]:
                chosen[r["id"]] = r
            if len(candidates) < minimum:
                unsat.append({"role": role, "needed": minimum, "available": len(candidates)})
        vocals = [r for r in rows if eligible(r) and r.get("role") == "vocal"]
        for r in vocals[:8]:
            chosen[r["id"]] = r
        for r in rows:
            if len(chosen) >= max_loops:
                break
            if not eligible(r):
                continue
            if r["id"] in chosen:
                continue
            cnt = by_track.get(r["file_id"], 0)
            if cnt >= 2:
                continue
            chosen[r["id"]] = r
            by_track[r["file_id"]] = cnt + 1
        ids = list(chosen.keys())[:max_loops]
        if ids:
            db.executemany("UPDATE loops SET status='approved' WHERE id=?", [(x,) for x in ids])
        db.commit()
        awaiting = db.execute("SELECT COUNT(*) n FROM loops WHERE status='candidate'").fetchone()["n"]
        return {"ok": True, "approved": len(ids), "awaiting_review": int(awaiting), "unsatisfied_role_minimums": unsat, "message": f"{len(ids)} loops auto-approved by quota; {awaiting} candidates awaiting review. Reviewed pools sound better."}

    def approved_loop_pool(self) -> List[Dict[str, Any]]:
        rows = self.conn().execute(
            """SELECT l.*, f.path, f.duration_s, t.artist,t.album,t.title,ft.bpm,ft.key_root,ft.key_mode,ft.energy,ft.vocal_likelihood,
                      (SELECT value FROM tags WHERE file_id=f.id AND key='genre' LIMIT 1) genre,
                      t.year
               FROM loops l JOIN files f ON f.id=l.file_id LEFT JOIN tracks t ON t.file_id=f.id LEFT JOIN features ft ON ft.file_id=f.id
               WHERE l.status='approved'
               ORDER BY l.score DESC"""
        ).fetchall()
        return [dict(r) for r in rows]


    def ear_role_from_metrics(self, base_role: str, bars: int, metrics: Dict[str, float]) -> str:
        base = str(base_role or "full")
        hook = float(metrics.get("hook_score") or 0.0)
        bed = float(metrics.get("bed_score") or 0.0)
        floor = float(metrics.get("floor_score") or 0.0)
        bass = float(metrics.get("bass_score") or 0.0)
        spark = float(metrics.get("spark_score") or 0.0)
        intelligibility = float(metrics.get("intelligibility") or 0.0)
        if base == "vocal" or (intelligibility >= 0.62 and hook >= 0.50):
            if bars <= 2 or spark >= 0.70:
                return "VOX_SHOUT"
            return "VOX_HOOK" if hook >= 0.58 else "VOX_VERSE"
        if base == "drum_anchor" or (floor >= 0.66 and bass < 0.48):
            return "DRUM_BREAK"
        if base == "bass" or bass >= 0.62:
            return "BASS_RIFF"
        if base in {"harmony", "full"}:
            if hook >= 0.62 and bed < 0.64:
                return "RIFF_ID"
            return "BED_CHORD" if bed >= 0.48 else "TEXTURE"
        if base in {"texture", "fx"}:
            if bars <= 1 or spark >= 0.72:
                return "DROP_HIT" if spark >= 0.82 else "PICKUP_FILL"
            return "TRANSITION_TAIL" if metrics.get("loopability", 0.0) >= 0.52 else "TEXTURE"
        if spark >= 0.78 and bars <= 2:
            return "PICKUP_FILL"
        if bed >= max(hook, floor, bass, spark):
            return "BED_CHORD"
        return "TEXTURE"

    def ear_atom_metrics(self, seg: np.ndarray, sr: int, bars: int, track_vocal_like: float, base_role: str = "full") -> Dict[str, float]:
        if seg.size < 512 or float(np.max(np.abs(seg))) < 1e-6:
            return {"score": 0.0, "hook_score": 0.0, "bed_score": 0.0, "floor_score": 0.0, "bass_score": 0.0, "spark_score": 0.0, "intelligibility": 0.0, "low_share": 0.0, "mid_share": 0.0, "high_share": 0.0, "loopability": 0.0, "transient_density": 0.0}
        seg = seg.astype(np.float32, copy=False)
        rms = rms_value(seg)
        peak = float(np.max(np.abs(seg))) + 1e-9
        crest = float(peak / max(rms, 1e-9))
        win = min(seg.size // 4, sr // 2)
        if win >= 512:
            h1 = np.abs(np.fft.rfft(seg[:win]))
            h2 = np.abs(np.fft.rfft(seg[-win:]))
            dist = np.linalg.norm(h1 / (np.linalg.norm(h1) + 1e-9) - h2 / (np.linalg.norm(h2) + 1e-9))
            loopability = float(max(0.0, min(1.0, 1.0 - dist / 1.55)))
        else:
            loopability = 0.0
        onset = librosa.onset.onset_detect(y=seg, sr=sr, units="time", backtrack=False)
        onset_density = float(onset.size / max(0.25, seg.size / sr))
        transient_density = float(min(1.0, onset_density / 7.0))
        S = np.abs(librosa.stft(seg, n_fft=2048, hop_length=1024)) ** 2
        freqs = librosa.fft_frequencies(sr=sr, n_fft=2048)
        total = float(np.sum(S) + 1e-12)
        sub_share = float(np.sum(S[freqs < 90]) / total)
        low_share = float(np.sum(S[freqs < 200]) / total)
        lowmid_share = float(np.sum(S[(freqs >= 200) & (freqs < 520)]) / total)
        mid_share = float(np.sum(S[(freqs >= 520) & (freqs <= 3400)]) / total)
        presence_share = float(np.sum(S[(freqs >= 1800) & (freqs <= 6500)]) / total)
        high_share = float(np.sum(S[freqs > 3400]) / total)
        flat = float(np.mean(librosa.feature.spectral_flatness(S=np.sqrt(S))))
        harmonic, percussive = librosa.effects.hpss(seg)
        hp_total = float(np.sum(np.abs(harmonic)) + np.sum(np.abs(percussive)) + 1e-9)
        harmonic_ratio = float(np.sum(np.abs(harmonic)) / hp_total)
        percussive_ratio = float(np.sum(np.abs(percussive)) / hp_total)
        energy_score = float(min(1.0, rms / 0.11))
        intelligibility = float(max(0.0, min(1.0, 0.40 * min(1.0, mid_share / 0.42) + 0.28 * min(1.0, presence_share / 0.24) + 0.22 * float(track_vocal_like or 0.0) + 0.10 * (1.0 - min(1.0, low_share / 0.42)))))
        hook_score = float(max(0.0, min(1.0, 0.32 * intelligibility + 0.20 * min(1.0, presence_share / 0.22) + 0.16 * transient_density + 0.16 * energy_score + 0.10 * min(1.0, crest / 5.5) + 0.06 * (1.0 if bars in (2, 4) else 0.4))))
        floor_score = float(max(0.0, min(1.0, 0.34 * percussive_ratio + 0.24 * transient_density + 0.18 * loopability + 0.14 * energy_score + 0.10 * (1.0 - min(1.0, mid_share / 0.75)))))
        bass_score = float(max(0.0, min(1.0, 0.48 * min(1.0, low_share / 0.34) + 0.22 * min(1.0, sub_share / 0.18) + 0.16 * loopability + 0.14 * energy_score)))
        bed_score = float(max(0.0, min(1.0, 0.32 * harmonic_ratio + 0.24 * loopability + 0.18 * min(1.0, (lowmid_share + mid_share) / 0.62) + 0.14 * energy_score + 0.12 * (1.0 - min(1.0, intelligibility / 0.88)))))
        spark_score = float(max(0.0, min(1.0, 0.26 * min(1.0, crest / 6.0) + 0.22 * transient_density + 0.18 * min(1.0, high_share / 0.28) + 0.16 * energy_score + 0.18 * (1.0 if bars <= 2 else 0.35))))
        base_bias = {"vocal": hook_score, "drum_anchor": floor_score, "bass": bass_score, "harmony": bed_score, "texture": spark_score, "fx": spark_score, "full": max(bed_score, hook_score * 0.9)}.get(str(base_role or "full"), 0.0)
        score = float(max(hook_score, floor_score, bass_score, bed_score, spark_score, base_bias) * 0.72 + loopability * 0.16 + energy_score * 0.12)
        if rms < 1e-4:
            score *= 0.1
        return {"score": score, "hook_score": hook_score, "bed_score": bed_score, "floor_score": floor_score, "bass_score": bass_score, "spark_score": spark_score, "intelligibility": intelligibility, "low_share": low_share, "mid_share": mid_share, "high_share": high_share, "loopability": loopability, "transient_density": transient_density, "rms": rms, "crest": crest, "harmonic_ratio": harmonic_ratio, "percussive_ratio": percussive_ratio, "flatness": flat}

    def build_ear_crate(self, limit: int = 0, force: bool = False, taste_profile: str = "girl_talk_v1", write_previews: bool = False) -> Dict[str, Any]:
        """Turn loop candidates into deterministic, auditionable phrase atoms.

        The arranger is no longer allowed to discover taste from raw file slices at
        render time. This pass classifies each loop into a narrow ear role and
        approves only fragments that have enough salience to be useful material.
        """
        c = self.ensure_config()
        db = self.conn()
        self.set_status("TasteSpec: building ear crate", 0.0, True, None)
        _t_crate = time.perf_counter()
        # force now means RE-MEASURE IN PLACE, never delete: atom identity is
        # stable, so pair judgments and locked human calls survive every rebuild.
        _incr = "" if force else "AND l.id NOT IN (SELECT loop_id FROM ear_atoms WHERE taste_profile=?)"
        _args = ([taste_profile] if not force else []) + [limit if limit and limit > 0 else 1000000000]
        rows = db.execute(
            f"""SELECT * FROM (
                 SELECT l.*, f.path, ft.bpm, ft.key_root, ft.vocal_likelihood, t.artist, t.title
                 FROM loops l JOIN files f ON f.id=l.file_id
                 LEFT JOIN features ft ON ft.file_id=f.id
                 LEFT JOIN tracks t ON t.file_id=f.id
                 WHERE l.status!='rejected' {_incr}
                 ORDER BY l.score DESC LIMIT ?
               ) ORDER BY path""",
            tuple(_args),
        ).fetchall()
        preview_dir = c.working_root / "ear_crate" / "previews"
        if write_previews:
            preview_dir.mkdir(parents=True, exist_ok=True)
        inserted = 0; updated = 0; rejected = 0; adopted = 0; failed: List[Dict[str, str]] = []
        counts: Dict[str, int] = {r: 0 for r in EAR_ROLE_ORDER}
        locked_ids = {row["atom_id"] for row in db.execute(
            "SELECT atom_id FROM atom_judgments WHERE taste_profile=? AND locked=1", (taste_profile,)).fetchall()}

        def _upsert_atom(lr, metrics: Dict[str, Any], ear_role: str, render_role: str, status: str, preview_path):
            nonlocal inserted, updated, rejected
            existing = db.execute("SELECT id, ear_role, status FROM ear_atoms WHERE loop_id=? AND taste_profile=?",
                                  (lr["id"], taste_profile)).fetchone()
            aid = existing["id"] if existing else ("atm_" + sha256_text(f"{lr['id']}|{taste_profile}")[:20])
            if existing and existing["id"] in locked_ids:
                # a human locked this call; re-measurement must not overturn it
                ear_role, status = existing["ear_role"], existing["status"]
            if status == "rejected":
                rejected += 1
            values = (aid, lr["id"], lr["file_id"], taste_profile, ear_role, render_role,
                      float(lr["start_s"]), float(lr["end_s"]), int(lr["bars"] or 1),
                      float(lr["bpm"] or 0.0), int(lr["key_root"] or 0),
                      float(metrics.get("score") or 0.0), float(metrics.get("hook_score") or 0.0),
                      float(metrics.get("bed_score") or 0.0), float(metrics.get("floor_score") or 0.0),
                      float(metrics.get("bass_score") or 0.0), float(metrics.get("spark_score") or 0.0),
                      float(metrics.get("intelligibility") or 0.0), float(metrics.get("low_share") or 0.0),
                      float(metrics.get("mid_share") or 0.0), float(metrics.get("high_share") or 0.0),
                      float(metrics.get("loopability") or 0.0), float(metrics.get("transient_density") or 0.0),
                      "downbeat", status, preview_path, json.dumps(metrics, ensure_ascii=False), now_utc())
            db.execute(
                """INSERT INTO ear_atoms(id,loop_id,file_id,taste_profile,ear_role,render_role,start_s,end_s,bars,bpm,key_root,score,hook_score,bed_score,floor_score,bass_score,spark_score,intelligibility,low_share,mid_share,high_share,loopability,transient_density,phrase_position,status,preview_path,metrics_json,created_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(loop_id,taste_profile) DO UPDATE SET ear_role=excluded.ear_role,render_role=excluded.render_role,score=excluded.score,hook_score=excluded.hook_score,bed_score=excluded.bed_score,floor_score=excluded.floor_score,bass_score=excluded.bass_score,spark_score=excluded.spark_score,intelligibility=excluded.intelligibility,low_share=excluded.low_share,mid_share=excluded.mid_share,high_share=excluded.high_share,loopability=excluded.loopability,transient_density=excluded.transient_density,status=excluded.status,preview_path=excluded.preview_path,metrics_json=excluded.metrics_json,created_at=excluded.created_at""",
                values)
            if existing:
                updated += 1
            else:
                inserted += 1
            if status == "approved":
                counts[ear_role] = counts.get(ear_role, 0) + 1

        # ---- Phase A: ADOPT — metrics are persona-independent, so any other
        # resident's measurement of the same loop is reused verbatim. A second
        # resident auditions a big library in seconds, not hours. ----
        need_dsp: List[Any] = []
        for idx, r in enumerate(rows):
            donor = db.execute(
                "SELECT metrics_json, ear_role, render_role, preview_path FROM ear_atoms WHERE loop_id=? AND taste_profile!=? LIMIT 1",
                (r["id"], taste_profile)).fetchone()
            metrics = None
            if donor:
                try:
                    metrics = json.loads(donor["metrics_json"] or "{}") or None
                except Exception:
                    metrics = None
            if metrics:
                status = classify_atom_status(str(donor["ear_role"]), metrics)
                _upsert_atom(r, metrics, str(donor["ear_role"]), str(donor["render_role"]), status, donor["preview_path"])
                adopted += 1
                if idx % 64 == 0:
                    db.commit()
                    self.set_status(f"TasteSpec: adopting measured atoms {idx+1}/{len(rows)}", (idx + 1) / max(1, len(rows)) * 0.2, True)
            else:
                need_dsp.append(r)
        db.commit()

        # ---- Phase B: MEASURE — decode each file once and fan the DSP across
        # cores (the same ProcessPool discipline analyze uses). ----
        by_path: Dict[str, List[Any]] = {}
        for r in need_dsp:
            by_path.setdefault(str(r["path"]), []).append(r)
        jobs = [{"path": path, "sample_rate": c.sample_rate, "write_previews": bool(write_previews),
                 "preview_dir": str(preview_dir),
                 "loops": [{"id": r["id"], "start_s": float(r["start_s"]), "end_s": float(r["end_s"]),
                            "bars": int(r["bars"] or 1), "role": str(r["role"] or "full"),
                            "vocal_likelihood": float(r["vocal_likelihood"] or 0.0),
                            "artist": r["artist"], "title": r["title"]} for r in lst]}
                for path, lst in by_path.items()]
        row_by_loop = {r["id"]: r for r in need_dsp}
        workers = self._worker_count()
        done_files = 0
        _t_dsp = time.perf_counter()

        def _consume(res: Dict[str, Any]):
            nonlocal done_files
            done_files += 1
            if res.get("error"):
                failed.append({"loop_id": "file:" + str(res.get("path")), "error": str(res["error"])[:300]})
            for item in res.get("results") or []:
                lr = row_by_loop.get(item.get("loop_id"))
                if lr is None:
                    continue
                if item.get("error"):
                    failed.append({"loop_id": str(item.get("loop_id")), "error": str(item["error"])[:300]})
                    continue
                _upsert_atom(lr, item["metrics"], item["ear_role"], item["render_role"], item["status"], item.get("preview_path"))
            db.commit()
            _left = (time.perf_counter() - _t_dsp) / max(1, done_files) * (len(jobs) - done_files)
            self.set_status(f"TasteSpec: ear-crating file {done_files}/{len(jobs)} \u00d7{workers} cores \u00b7 ~{int(_left // 60)}m{int(_left % 60):02d}s left",
                            0.2 + 0.8 * done_files / max(1, len(jobs)), True)

        used_parallel = False
        if jobs and workers > 1:
            try:
                mp = __import__("multiprocessing")
                method = "fork" if ("fork" in mp.get_all_start_methods() and os.name != "nt") else "spawn"
                ctx = mp.get_context(method)
                with concurrent.futures.ProcessPoolExecutor(max_workers=workers, mp_context=ctx) as ex:
                    for fut in concurrent.futures.as_completed({ex.submit(ear_crate_file_worker, job): job for job in jobs}):
                        _consume(fut.result())
                used_parallel = True
            except Exception as exc:
                self.set_status(f"ear-crate pool unavailable ({str(exc)[:60]}); using single core", None, True)
                done_files = 0
        if jobs and not used_parallel:
            for job in jobs:
                _consume(ear_crate_file_worker(job))
        db.commit()
        approved = db.execute("SELECT COUNT(*) n FROM ear_atoms WHERE taste_profile=? AND status='approved'", (taste_profile,)).fetchone()["n"]
        self.set_status(f"TasteSpec ear crate complete: {approved} approved atoms", 1.0, False)
        return {"ok": True, "taste_profile": taste_profile, "scanned_loops": len(rows), "inserted": inserted, "updated": updated, "adopted": adopted, "parallel_files": len(jobs), "approved": int(approved), "role_counts": counts, "rejected": rejected, "failed": failed[:50]}

    def list_ear_atoms(self, status: str = "approved", taste_profile: str = "girl_talk_v1", limit: int = 500) -> Dict[str, Any]:
        where = "WHERE a.taste_profile=?"
        args: List[Any] = [taste_profile]
        if status:
            where += " AND a.status=?"; args.append(status)
        args.append(limit)
        rows = self.conn().execute(
            f"""SELECT a.*, f.path, t.artist, t.title
                   FROM ear_atoms a JOIN files f ON f.id=a.file_id LEFT JOIN tracks t ON t.file_id=f.id
                   {where} ORDER BY a.score DESC LIMIT ?""",
            args,
        ).fetchall()
        counts_rows = self.conn().execute("SELECT ear_role,status,COUNT(*) n FROM ear_atoms WHERE taste_profile=? GROUP BY ear_role,status", (taste_profile,)).fetchall()
        counts: Dict[str, Dict[str, int]] = {}
        for r in counts_rows:
            counts.setdefault(str(r["ear_role"]), {})[str(r["status"])] = int(r["n"])
        return {"ok": True, "items": [dict(r) for r in rows], "counts": counts, "taste_profile": taste_profile}

    def approved_atom_pool(self, taste_profile: str = "girl_talk_v1") -> List[Dict[str, Any]]:
        rows = self.conn().execute(
            """SELECT a.id atom_id,a.preview_path,a.ear_role,a.render_role,a.score atom_score,a.hook_score,a.bed_score,a.floor_score,a.bass_score,a.spark_score,a.intelligibility,a.low_share,a.mid_share,a.high_share,a.loopability,a.transient_density,
                      l.*, f.path, f.duration_s, t.artist,t.album,t.title,ft.bpm,ft.key_root,ft.key_mode,ft.energy,ft.vocal_likelihood,
                      (SELECT value FROM tags WHERE file_id=f.id AND key='genre' LIMIT 1) genre,
                      t.year
               FROM ear_atoms a JOIN loops l ON l.id=a.loop_id JOIN files f ON f.id=a.file_id
               LEFT JOIN tracks t ON t.file_id=f.id LEFT JOIN features ft ON ft.file_id=f.id
               WHERE a.taste_profile=? AND a.status='approved'
               ORDER BY a.score DESC""",
            (taste_profile,),
        ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["id"] = d.get("loop_id") or d.get("id")
            d["role"] = d.get("render_role") or d.get("role") or "full"
            d["score"] = float(d.get("atom_score") or d.get("score") or 0.0)
            d["source_track_key"] = track_identity(d)
            d["dry_quality_score"] = max(float(d.get("dry_quality_score") or 0.0), float(d.get("atom_score") or 0.0) * 0.65)
            d["dry_high3000_share"] = float(d.get("high_share") or 0.0)
            d["dry_low200_share"] = float(d.get("low_share") or 0.0)
            d["dry_quality_veto"] = False
            out.append(d)
        return out

    def rank_crate(self, taste_profile: str = "girl_talk_v1", limit: int = 0) -> Dict[str, Any]:
        """Rank the approved ear crate by the persona's own selection priorities
        (recognizable hooks first, clean role material, danceable, deck-feasible,
        contrast bonus). The curation surface: which of YOUR loops the artist would
        actually reach for, and why — every entry carries its five sub-scores."""
        pool = self.approved_atom_pool(taste_profile)
        profile = TASTE_PROFILES.get(taste_profile, TASTE_PROFILES["girl_talk_v1"])
        islands = build_bpm_lattice(pool, None)[:4] if pool else []
        out = rank_material(pool, tempo_islands=islands, profile=profile)
        if limit and limit > 0:
            out["ranked"] = out["ranked"][:limit]
        out["ok"] = True
        out["taste_profile"] = taste_profile
        return out

    def taste_readiness(self, taste_profile: str = "girl_talk_v1", target_seconds: float = 120.0) -> Dict[str, Any]:
        pool = self.approved_atom_pool(taste_profile)
        profile = TASTE_PROFILES.get(taste_profile, TASTE_PROFILES["girl_talk_v1"])
        by_role: Dict[str, int] = {r: 0 for r in EAR_ROLE_ORDER}
        source_keys = set()
        for x in pool:
            by_role[str(x.get("ear_role") or "")] = by_role.get(str(x.get("ear_role") or ""), 0) + 1
            source_keys.add(track_identity(x))
        scale = max(0.5, min(1.2, float(target_seconds) / 120.0))
        need = {
            "foreground": max(4, int(math.ceil(12 * scale))),
            "floor": max(6, int(math.ceil(16 * scale))),
            "bass": max(3, int(math.ceil(6 * scale))),
            "spark": max(5, int(math.ceil(12 * scale))),
            "sources": max(5, int(math.ceil(float(target_seconds) / float(profile.get("source_seconds") or 11.5))))
        }
        have = {
            "foreground": by_role.get("VOX_HOOK",0)+by_role.get("VOX_VERSE",0)+by_role.get("VOX_SHOUT",0)+by_role.get("RIFF_ID",0),
            "floor": by_role.get("DRUM_BREAK",0)+by_role.get("BED_CHORD",0)+by_role.get("RIFF_ID",0)+by_role.get("TEXTURE",0),
            "bass": by_role.get("BASS_RIFF",0),
            "spark": by_role.get("PICKUP_FILL",0)+by_role.get("DROP_HIT",0)+by_role.get("TRANSITION_TAIL",0)+by_role.get("TEXTURE",0)+by_role.get("VOX_SHOUT",0),
            "sources": len(source_keys),
        }
        failures = []
        for k, n in need.items():
            if have.get(k, 0) < n:
                failures.append(f"{k} atoms short: have {have.get(k,0)}, need {n}")
        # Endless-set math: each approved atom supplies ~2 sample-events before it
        # reads as a rerun; a source contributes at most ~3 fresh foreground moments.
        atom_event_capacity = min(len(pool) * 2, len(source_keys) * 3)
        endless = endless_sustain(atom_event_capacity, len(source_keys), profile)
        return {"ok": True, "ready": not failures, "taste_profile": taste_profile, "profile": profile, "have": have, "need": need, "role_counts": by_role, "source_tracks": len(source_keys), "pool_size": len(pool), "failures": failures, "endless": endless}

    def atom_edge_score(self, left: Dict[str, Any], right: Dict[str, Any], relation: str, render_bpm: float, target_key: int, stretch_budget: float, pitch_budget: int) -> Tuple[float, Dict[str, Any]]:
        def key_of(x: Dict[str, Any]) -> int:
            try: return int(x.get("key_root")) % 12
            except Exception: return target_key
        def bpm_of(x: Dict[str, Any]) -> float:
            try: return float(x.get("bpm") or render_bpm)
            except Exception: return render_bpm
        lk = key_of(left); rk = key_of(right)
        lrel = harmonic_relation_name(lk, target_key)
        rrel = harmonic_relation_name(rk, target_key)
        harmonic = 1.0 if lk == rk or lrel in {"same_key","dominant","subdominant","relative_or_parallel"} or rrel in {"same_key","dominant","subdominant","relative_or_parallel"} else 0.42
        try:
            lt = plan_varispeed_transform(str(left.get("role") or "full"), bpm_of(left), render_bpm, lk, target_key, stretch_budget, pitch_budget)
            rt = plan_varispeed_transform(str(right.get("role") or "full"), bpm_of(right), render_bpm, rk, target_key, stretch_budget, pitch_budget)
            if lt.get("violation") or rt.get("violation"):
                return 0.0, {"reason": "transform_violation", "left": lt.get("violation"), "right": rt.get("violation")}
            transform = 1.0 - min(1.0, (float(lt.get("varispeed_pct") or 0.0)+float(rt.get("varispeed_pct") or 0.0))/24.0 + (abs(float(lt.get("residual_pitch_shift") or 0.0))+abs(float(rt.get("residual_pitch_shift") or 0.0)))/5.0)
        except Exception as exc:
            return 0.0, {"reason": f"transform_error:{exc}"}
        same_source = track_identity(left) == track_identity(right)
        low_conflict = min(float(left.get("low_share") or 0.0), float(right.get("low_share") or 0.0))
        mid_mask = min(float(left.get("mid_share") or 0.0), float(right.get("mid_share") or 0.0))
        if relation == "vocal_over_bed":
            intelligible = max(float(left.get("intelligibility") or 0.0), float(right.get("intelligibility") or 0.0))
            bedness = max(float(left.get("bed_score") or 0.0), float(right.get("bed_score") or 0.0), float(left.get("floor_score") or 0.0), float(right.get("floor_score") or 0.0))
            score = 0.28*harmonic + 0.22*transform + 0.22*intelligible + 0.16*bedness + 0.12*(1.0-min(1.0, low_conflict/0.24 + mid_mask/0.95))
        elif relation == "bass_over_drums":
            score = 0.30*harmonic + 0.25*transform + 0.22*max(float(left.get("bass_score") or 0.0), float(right.get("bass_score") or 0.0)) + 0.16*max(float(left.get("floor_score") or 0.0), float(right.get("floor_score") or 0.0)) + 0.07*(1.0-min(1.0, low_conflict/0.32))
        else:
            score = 0.30*harmonic + 0.30*transform + 0.20*max(float(left.get("spark_score") or 0.0), float(right.get("spark_score") or 0.0)) + 0.20*(1.0-min(1.0, mid_mask/1.10))
        if same_source:
            score -= 0.28
        reasons = {"harmonic": round(harmonic,3), "transform": round(transform,3), "same_source": same_source, "low_conflict": round(low_conflict,3), "mid_mask": round(mid_mask,3)}
        return float(max(0.0, min(1.0, score))), reasons

    def build_compatibility_graph(self, taste_profile: str = "girl_talk_v1", target_seconds: float = 120.0, bpm: float = 0.0, limit_per_side: int = 120) -> Dict[str, Any]:
        db = self.conn()
        pool = self.approved_atom_pool(taste_profile)
        if not pool:
            return {"ok": False, "error": "no approved ear atoms"}
        params = {"taste_profile": taste_profile, "target_seconds": target_seconds, "bpm": bpm or 0.0, "stretch_budget": 8.0, "pitch_shift_budget": 2}
        deck = self.choose_taste_deck(pool, params)
        pool = list(deck.get("pool") or pool)
        target_key = int(deck.get("target_key") or self.choose_target_key_for_pool(pool))
        render_bpm = float(deck.get("render_bpm") or bpm or 120.0)
        stretch_budget = 8.0; pitch_budget = 2
        foreground = [x for x in pool if x.get("ear_role") in {"VOX_HOOK","VOX_VERSE","VOX_SHOUT","RIFF_ID"}][:limit_per_side]
        beds = [x for x in pool if x.get("ear_role") in {"DRUM_BREAK","BED_CHORD","RIFF_ID","TEXTURE"}][:limit_per_side]
        basses = [x for x in pool if x.get("ear_role") == "BASS_RIFF"][:limit_per_side]
        sparks = [x for x in pool if x.get("ear_role") in {"PICKUP_FILL","DROP_HIT","TRANSITION_TAIL","TEXTURE","VOX_SHOUT"}][:limit_per_side]
        # Durable edge identity: the id is a hash of (profile,left,right,relation),
        # so rebuilding the graph updates scores IN PLACE and human pair_judgments
        # (keyed by edge id, ON DELETE CASCADE) survive every regraph. The old
        # delete-all + random ids erased judgments on rebuild — the exact
        # "automated rescoring erasing judgments" the constitution forbids.
        db.execute("DELETE FROM compatibility_edges WHERE taste_profile=? AND id NOT LIKE 'edg_%'", (taste_profile,))
        made = 0
        for relation, lefts, rights in [("vocal_over_bed", foreground, beds), ("bass_over_drums", basses, beds), ("spark_into_phrase", sparks, beds+foreground[:40])]:
            scored = []
            for a in lefts:
                for b in rights:
                    if a.get("atom_id") == b.get("atom_id"):
                        continue
                    sc, reasons = self.atom_edge_score(a, b, relation, render_bpm, target_key, stretch_budget, pitch_budget)
                    if sc >= float(TASTE_PROFILES.get(taste_profile, {}).get("min_edge_score", 0.54)):
                        scored.append((sc, a, b, reasons))
            scored.sort(reverse=True, key=lambda x: x[0])
            for sc, a, b, reasons in scored[:max(80, int(target_seconds))]:
                eid = "edg_" + sha256_text(f"{taste_profile}|{a.get('atom_id')}|{b.get('atom_id')}|{relation}")[:20]
                db.execute("""INSERT INTO compatibility_edges(id,taste_profile,left_atom_id,right_atom_id,relation,score,reasons_json,created_at)
                              VALUES(?,?,?,?,?,?,?,?)
                              ON CONFLICT(id) DO UPDATE SET score=excluded.score, reasons_json=excluded.reasons_json, created_at=excluded.created_at""",
                           (eid, taste_profile, a.get("atom_id"), b.get("atom_id"), relation, float(sc), json.dumps(reasons, ensure_ascii=False), now_utc()))
                made += 1
        db.commit()
        return {"ok": True, "taste_profile": taste_profile, "edges": made, "render_bpm": render_bpm, "target_key": target_key, "foreground": len(foreground), "beds": len(beds), "basses": len(basses), "sparks": len(sparks), "feasibility": deck.get("diagnostics")}

    def taste_feasible_pool(self, pool: List[Dict[str, Any]], render_bpm: float, target_key: int, params: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
        """Filter the crate to atoms that can actually be played at the chosen deck.

        v0.6.0 counted inventory before proving transform feasibility, so the
        composer could select a floor or foreground atom and then silently drop it
        when add_layer() discovered the BPM/key plan was illegal. This is the root
        of the 0.17 coverage failure. The composer now sees only playable atoms.
        """
        stretch_budget = float(params.get("stretch_budget") or 8.0)
        pitch_budget = int(params.get("pitch_shift_budget") or 2)
        out: List[Dict[str, Any]] = []
        rejected: Dict[str, int] = {r: 0 for r in EAR_ROLE_ORDER}
        counts: Dict[str, int] = {r: 0 for r in EAR_ROLE_ORDER}
        sources: set = set()
        for item in pool:
            role = str(item.get("role") or item.get("render_role") or "full")
            key = int(item.get("key_root") or target_key) % 12
            plan = plan_varispeed_transform(role, float(item.get("bpm") or render_bpm), render_bpm, key, target_key, stretch_budget, pitch_budget)
            ear = str(item.get("ear_role") or "")
            if plan.get("violation"):
                rejected[ear] = rejected.get(ear, 0) + 1
                continue
            d = dict(item)
            d["feasible_transform"] = plan
            out.append(d)
            counts[ear] = counts.get(ear, 0) + 1
            sources.add(track_identity(d))
        have = {
            "foreground": counts.get("VOX_HOOK",0)+counts.get("VOX_VERSE",0)+counts.get("VOX_SHOUT",0)+counts.get("RIFF_ID",0),
            "floor": counts.get("DRUM_BREAK",0)+counts.get("BED_CHORD",0)+counts.get("RIFF_ID",0)+counts.get("TEXTURE",0),
            "bass": counts.get("BASS_RIFF",0),
            "spark": counts.get("PICKUP_FILL",0)+counts.get("DROP_HIT",0)+counts.get("TRANSITION_TAIL",0)+counts.get("TEXTURE",0)+counts.get("VOX_SHOUT",0),
            "sources": len(sources),
        }
        return out, {"render_bpm": round(float(render_bpm), 2), "target_key": int(target_key), "role_counts": counts, "have": have, "rejected_by_role": rejected, "pool_size": len(out), "source_tracks": len(sources)}

    def choose_taste_deck(self, pool: List[Dict[str, Any]], params: Dict[str, Any]) -> Dict[str, Any]:
        """Choose the BPM/key that maximizes playable TasteSpec material.

        The BPM field is treated as a taste hint, not as a theater-producing hard
        pin. A hard pin can be added later as a separate explicit control; the
        default one-click path must choose the strongest tempo island in the crate.
        """
        user_bpm = float(params.get("bpm") or 0.0) or None
        profile = TASTE_PROFILES.get(str(params.get("taste_profile") or "girl_talk_v1"), TASTE_PROFILES["girl_talk_v1"])
        target_seconds = float(params.get("target_seconds") or 120.0)
        needed_sources = max(5, int(math.ceil(target_seconds / float(profile.get("source_seconds") or 11.5))))
        keys = sorted({int(x.get("key_root") or 0) % 12 for x in pool}) or [0]
        weighted_keys = []
        for k in keys:
            weight = sum(float(x.get("score") or 0.0) for x in pool if int(x.get("key_root") or 0) % 12 == k)
            weighted_keys.append((weight, k))
        weighted_keys.sort(reverse=True)
        key_candidates = [k for _, k in weighted_keys[:8]]
        for k in range(12):
            if k not in key_candidates:
                key_candidates.append(k)
        bpm_lattice = build_bpm_lattice(pool, user_bpm)
        # Native tempo islands matter more than the UI hint; test the top transform
        # lattice for each likely key instead of inheriting one brittle target.
        candidate_rows: List[Tuple[float, List[Dict[str, Any]], Dict[str, Any]]] = []
        for key in key_candidates:
            lat = score_bpm_lattice(pool, user_bpm, key, float(params.get("stretch_budget") or 8.0), int(params.get("pitch_shift_budget") or 2))
            for bpm_row in lat.get("lattice", [])[:14]:
                bpm = float(bpm_row.get("bpm") or 0.0)
                feasible, diag = self.taste_feasible_pool(pool, bpm, key, params)
                have = diag.get("have") or {}
                role_floor = min(1.0, float(have.get("floor",0)) / 16.0)
                role_fg = min(1.0, float(have.get("foreground",0)) / 12.0)
                role_bass = min(1.0, float(have.get("bass",0)) / 4.0)
                role_spark = min(1.0, float(have.get("spark",0)) / 8.0)
                src = min(1.0, float(have.get("sources",0)) / max(1.0, float(needed_sources)))
                user_bonus = 0.10 if user_bpm and abs(bpm - user_bpm) / max(1.0, user_bpm) <= 0.035 else 0.0
                score = 4.0*role_floor + 4.0*role_fg + 2.0*role_bass + 1.5*role_spark + 3.0*src + user_bonus - float(bpm_row.get("plan_score") or 0.0) * 0.20
                diag["deck_score"] = round(float(score), 4)
                diag["needed_sources"] = needed_sources
                diag["bpm_lattice_row"] = bpm_row
                candidate_rows.append((score, feasible, diag))
        if not candidate_rows:
            key = self.choose_target_key_for_pool(pool)
            feasible, diag = self.taste_feasible_pool(pool, float(user_bpm or 120.0), key, params)
            return {"pool": feasible, "render_bpm": float(user_bpm or 120.0), "target_key": key, "lattice": {"best": diag, "lattice": []}, "diagnostics": diag}
        candidate_rows.sort(reverse=True, key=lambda t: t[0])
        # Source contract at deck selection (v0.6.4): a deck that cannot supply
        # needed_sources must not win on role richness alone.
        satisfying = [row for row in candidate_rows if int((row[2].get("have") or {}).get("sources", 0)) >= needed_sources]
        if satisfying:
            candidate_rows = satisfying
        score, feasible, diag = candidate_rows[0]
        return {"pool": feasible, "render_bpm": float(diag["render_bpm"]), "target_key": int(diag["target_key"]), "lattice": {"best": diag, "lattice": [r[2] for r in candidate_rows[:12]]}, "diagnostics": diag}

    def propose_taste_mashup(self, params: Dict[str, Any]) -> Dict[str, Any]:
        c = self.ensure_config()
        taste_profile = str(params.get("taste_profile") or "girl_talk_v1")
        target_seconds = float(params.get("target_seconds") or 120)
        readiness = self.taste_readiness(taste_profile, target_seconds)
        if not readiness.get("ready"):
            raise RuntimeError("TasteSpec crate is not ready: " + "; ".join(readiness.get("failures") or []))
        pool = self.approved_atom_pool(taste_profile)
        name = safe_name(str(params.get("name") or "EarCrate Set"), "EarCrate Set")
        explicit_seed = params.get("seed") not in (None, "", 0, "0")
        seed = int(params.get("seed")) if explicit_seed else self.next_render_seed(c.seed)
        params = dict(params)
        params.update({"seed": seed, "taste_profile": taste_profile, "quality_mode": "stable_deck", "post_render_gate": True, "mix_mode": "tastespec_graph"})
        arrangement = self.compose_taste_arrangement(pool, params, seed)
        preflight = self.arrangement_preflight_gate(arrangement)
        taste_gate = self.taste_arrangement_gate(arrangement)
        arrangement["candidate_search"] = {"count": 1, "selected_seed": seed, "selected_score": self.score_arrangement(arrangement), "selected_preflight": preflight, "taste_gate": taste_gate, "render_policy": "TasteSpec graph compiler: render only after crate, compatibility, and style-contract gates"}
        if not preflight.get("passed") or not taste_gate.get("passed"):
            failures = (preflight.get("failures") or []) + (taste_gate.get("failures") or [])
            raise RuntimeError("TasteSpec pre-render gate refused theater: " + "; ".join(failures))
        arr_sha = arrangement_sha(arrangement)
        mashup_id = ulidish()
        render_name = f"{safe_name(name)}-{ENGINE_VERSION}-{arr_sha[:8]}-{seed}.wav"
        dst = c.working_root / "renders" / render_name
        self.conn().execute("INSERT INTO mashups(id,name,seed,params_json,arrangement_json,render_path,created_at,engine_version,arrangement_sha) VALUES(?,?,?,?,?,?,?,?,?)", (mashup_id, name, seed, json.dumps(params, ensure_ascii=False), json.dumps(arrangement, ensure_ascii=False), str(dst), now_utc(), ENGINE_VERSION, arr_sha))
        self.conn().commit()
        op = {"op_id": ulidish(), "type": "render_mashup", "args": {"mashup_id": mashup_id, "dst": str(dst)}, "preconditions": {"dst_absent": True}}
        manifest = self.write_manifest("tastespec", seed, f"Render TasteSpec mashup '{name}'", [op])
        return {"ok": True, "mashup_id": mashup_id, "manifest": manifest, "arrangement": arrangement, "dst": str(dst), "engine_version": ENGINE_VERSION, "arrangement_sha": arr_sha,
            "tastespec": arrangement.get("tastespec") or profile_summary(str((arrangement.get("params") or {}).get("taste_profile") or "girl_talk_v1")), "readiness": readiness}

    def compose_taste_arrangement(self, pool: List[Dict[str, Any]], params: Dict[str, Any], seed: int) -> Dict[str, Any]:
        rng = random.Random(seed)
        target_seconds = float(params.get("target_seconds") or 120)
        user_bpm = float(params.get("bpm") or 0.0) or None
        deck = self.choose_taste_deck(pool, params)
        pool = list(deck.get("pool") or [])
        profile_data = load_tastespec(str(params.get("taste_profile") or "girl_talk_v1"))
        profile0 = TASTE_PROFILES.get(str(params.get("taste_profile") or "girl_talk_v1"), TASTE_PROFILES["girl_talk_v1"])
        need_sources0 = max(5, int(math.ceil(target_seconds / float(profile0.get("source_seconds") or 11.5))))
        deck_sources = int(((deck.get("diagnostics") or {}).get("have") or {}).get("sources", 0))
        if deck_sources and deck_sources < need_sources0:
            diag = deck.get("diagnostics") or {}
            raise RuntimeError(
                f"TasteSpec deck infeasible: best deck ({diag.get('render_bpm')} BPM, key {diag.get('target_key')}) keeps {deck_sources}/{need_sources0} distinct playable sources; the crate needs more sources that survive transform at a common tempo")
        target_key = int(deck.get("target_key") or self.choose_target_key_for_pool(pool))
        lattice = dict(deck.get("lattice") or {})
        render_bpm = float(deck.get("render_bpm") or user_bpm or 120.0)
        if not pool:
            # Preserve an inspectable failure instead of rendering emptiness.
            pool = self.approved_atom_pool(str(params.get("taste_profile") or "girl_talk_v1"))
        # bars = beats/4; target_seconds*bpm/60 is beats, /4 is bars already. The old
        # code then multiplied by 4 again, quadrupling every render (a 2-min target
        # became ~8 min). Round to the nearest whole 4-bar phrase instead.
        bars_exact = target_seconds * render_bpm / 60.0 / 4.0
        total_bars = max(16, int(round(bars_exact / 4.0)) * 4)
        section_bars = 4
        sections = []
        foreground = [x for x in pool if x.get("ear_role") in {"VOX_HOOK","VOX_VERSE","VOX_SHOUT","RIFF_ID"}]
        floors = [x for x in pool if x.get("ear_role") in {"DRUM_BREAK","BED_CHORD","RIFF_ID","TEXTURE"}]
        basses = [x for x in pool if x.get("ear_role") == "BASS_RIFF"]
        sparks = [x for x in pool if x.get("ear_role") in {"PICKUP_FILL","DROP_HIT","TRANSITION_TAIL","TEXTURE","VOX_SHOUT"}]
        for xs in (foreground, floors, basses, sparks):
            xs.sort(key=lambda x: (float(x.get("score") or 0.0), float(x.get("hook_score") or 0.0), str(x.get("id"))), reverse=True)
        recent_sources: List[str] = []
        recent_loop_ids: List[str] = []
        prev_sec: Optional[Dict[str, Any]] = None
        bar = 0
        idx = 0
        pitch_budget = int(params.get("pitch_shift_budget") or 2)
        stretch_budget = float(params.get("stretch_budget") or 8.0)
        key_strictness = int(params.get("key_strictness") or 72)
        graph_receipts: List[Dict[str, Any]] = []
        # Curation loop closure: human judgments steer composition. Rejected pairs
        # are vetoes the composer must obey; approved pairs and favorited atoms get
        # a deterministic boost. Loaded once per composition; absent DB (synthetic
        # test cores) means no judgments, not an error.
        pair_verdicts: Dict[Tuple[str, str], str] = {}
        favorite_atoms: set = set()
        _profile_name = str(params.get("taste_profile") or "girl_talk_v1")
        try:
            _db = self.conn()
            for _r in _db.execute("""SELECT e.left_atom_id la, e.right_atom_id ra, pj.status st
                                     FROM pair_judgments pj JOIN compatibility_edges e ON e.id=pj.edge_id
                                     WHERE pj.taste_profile=?""", (_profile_name,)).fetchall():
                pair_verdicts[(str(_r["la"]), str(_r["ra"]))] = str(_r["st"])
            for _r in _db.execute("SELECT atom_id FROM atom_judgments WHERE taste_profile=? AND favorite=1", (_profile_name,)).fetchall():
                favorite_atoms.add(str(_r["atom_id"]))
        except Exception:
            pass
        source_use: Dict[str, int] = {}
        profile = TASTE_PROFILES.get(str(params.get("taste_profile") or "girl_talk_v1"), TASTE_PROFILES["girl_talk_v1"])
        need_sources = max(5, int(math.ceil(target_seconds / float(profile.get("source_seconds") or 11.5))))
        max_events_per_source = max(2, int(math.ceil((total_bars / 4.0) / max(1, need_sources))) + 1)
        def source_of(x: Dict[str, Any]) -> str:
            return track_identity(x)
        def playable(x: Dict[str, Any], role: Optional[str] = None) -> Optional[Dict[str, Any]]:
            r = str(role or x.get("role") or x.get("render_role") or "full")
            key = int(x.get("key_root") or target_key) % 12
            tf = plan_varispeed_transform(r, float(x.get("bpm") or render_bpm), render_bpm, key, target_key, stretch_budget, pitch_budget)
            return None if tf.get("violation") else tf
        def pick(cands: List[Dict[str, Any]], prefer_against: Optional[Dict[str, Any]] = None, relation: str = "vocal_over_bed", role: Optional[str] = None) -> Optional[Dict[str, Any]]:
            scored = []
            known_sources = set(source_use)
            for x in cands:
                if playable(x, role) is None:
                    continue
                verdict = None
                if prefer_against is not None and pair_verdicts:
                    _k = (str(x.get("atom_id")), str(prefer_against.get("atom_id")))
                    verdict = pair_verdicts.get(_k) or pair_verdicts.get((_k[1], _k[0]))
                    if verdict == "rejected":
                        continue  # a human said this pairing is bad; the composer obeys
                src = source_of(x)
                penalty = 0.0
                if str(x.get("id")) in recent_loop_ids[-12:]:
                    penalty += 0.55
                if source_use.get(src, 0) >= max_events_per_source and len(known_sources) < need_sources:
                    penalty += 1.25
                elif recent_sources.count(src) >= 2:
                    penalty += 0.40
                if prefer_against is not None:
                    edge, reasons = self.atom_edge_score(x, prefer_against, relation, render_bpm, target_key, stretch_budget, pitch_budget)
                else:
                    edge, reasons = 0.64, {"reason": "solo_pick"}
                if edge <= 0.0:
                    continue
                novelty = 0.34 if src not in source_use else (0.12 if src not in recent_sources[-8:] else -0.16)
                if str(x.get("atom_id")) in favorite_atoms:
                    novelty += 0.15  # human favorite
                if verdict == "approved":
                    novelty += 0.25  # human-approved pairing
                    reasons = dict(reasons); reasons["human_verdict"] = "approved"
                balance = -0.18 * source_use.get(src, 0)
                jitter = rng.random() * 0.01
                scored.append((float(x.get("score") or 0.0) * 0.44 + edge * 0.38 + novelty + balance - penalty + jitter, x, edge, reasons))
            if not scored:
                return None
            # Hard rotation (v0.6.4): while the turnover target is unmet, unused
            # sources win outright whenever a playable one scored.
            if len(known_sources) < need_sources:
                fresh = [t for t in scored if source_of(t[1]) not in source_use]
                if fresh:
                    scored = fresh
            scored.sort(reverse=True, key=lambda t: t[0])
            chosen = scored[0]
            graph_receipts.append({"relation": relation, "score": round(float(chosen[2]),3), "reasons": chosen[3], "left": chosen[1].get("atom_id"), "right": prefer_against.get("atom_id") if prefer_against else None})
            return chosen[1]
        def add_layer(layers: List[Dict[str, Any]], item: Dict[str, Any], role: str, gain_db: float, off: int, blen: int) -> bool:
            loop_key = int(item.get("key_root") or target_key) % 12
            tf = playable(item, role)
            if tf is None:
                return False
            layers.append({"loop_id": item["id"], "atom_id": item.get("atom_id"), "ear_role": item.get("ear_role"), "role": role, "pitch_shift": float(tf.get("synthetic_pitch_shift") or 0.0), "bar_offset": int(off), "bar_len": int(blen), "gain_db": gain_db, "world": "taste", "source_track_key": source_of(item), "dry_high3000_share": float(item.get("high_share") or 0.0), "dry_quality_score": float(item.get("score") or 0.0), "transform_mode": tf.get("transform_mode") or tf.get("mode"), "source_bpm_raw": tf.get("source_bpm_raw"), "source_bpm_folded": tf.get("source_bpm_folded"), "tempo_octave_multiplier": tf.get("tempo_octave_multiplier"), "speed_ratio": tf.get("speed_ratio"), "varispeed_pct": tf.get("varispeed_pct"), "natural_pitch_shift": tf.get("natural_pitch_shift"), "desired_key_shift": tf.get("desired_key_shift"), "residual_pitch_shift": tf.get("residual_pitch_shift"), "artifact_risk": tf.get("artifact_risk")})
            recent_loop_ids.append(str(item["id"]))
            recent_sources.append(source_of(item))
            source_use[source_of(item)] = source_use.get(source_of(item), 0) + 1
            del recent_loop_ids[:-32]
            del recent_sources[:-32]
            return True
        while bar < total_bars:
            bars = min(section_bars, total_bars - bar)
            sec_type = "drop" if idx % 4 == 0 and idx > 0 else ("build" if idx % 4 == 3 else "sustain")
            floor = pick(floors, None, "floor") or (floors[idx % len(floors)] if floors else None)
            fg = pick(foreground, floor, "vocal_over_bed", role="vocal") if foreground and floor else (pick(foreground, None, "foreground", role="vocal") if foreground else None)
            bass = None
            if basses and floor and str(floor.get("ear_role")) != "BASS_RIFF" and float(floor.get("low_share") or 0.0) < 0.34:
                bass = pick(basses, floor, "bass_over_drums", role="bass")
            spark = pick(sparks, floor or fg, "spark_into_phrase") if sparks and (idx % 2 == 1 or sec_type == "drop") else None
            layers: List[Dict[str, Any]] = []
            if floor:
                add_layer(layers, floor, str(floor.get("role") or "harmony"), -8.5 if sec_type != "drop" else -7.0, 0, bars)
            if bass and not any(x.get("role") == "bass" for x in layers):
                add_layer(layers, bass, "bass", -7.5, 0, bars)
            if fg:
                fg_len = min(bars, 4 if str(fg.get("ear_role")) != "VOX_SHOUT" else 1)
                fg_off = 0 if idx == 0 else (0 if fg_len >= bars else rng.choice([0, max(0,bars-fg_len)]))
                add_layer(layers, fg, "vocal" if str(fg.get("ear_role")) in {"VOX_HOOK","VOX_VERSE","VOX_SHOUT"} else str(fg.get("role") or "harmony"), -6.5 if str(fg.get("ear_role")) in {"VOX_HOOK","VOX_VERSE","VOX_SHOUT"} else -10.5, fg_off, fg_len)
            if spark:
                slen = 1 if bars <= 4 else 2
                add_layer(layers, spark, str(spark.get("role") or "texture"), -16.0, max(0, bars - slen), slen)
            if not any(x.get("role") in {"drum_anchor","bass","harmony","full"} for x in layers) and floors:
                for cand in floors:
                    if add_layer(layers, cand, str(cand.get("role") or "harmony"), -8.5, 0, bars):
                        break
            if idx == 0 and not any(x.get("role") == "vocal" or x.get("ear_role") in {"VOX_HOOK","VOX_VERSE","VOX_SHOUT","RIFF_ID"} for x in layers) and foreground:
                intro_fg = pick(foreground, floor, "vocal_over_bed", role="vocal") or foreground[0]
                add_layer(layers, intro_fg, "vocal" if str(intro_fg.get("ear_role")) in {"VOX_HOOK","VOX_VERSE","VOX_SHOUT"} else str(intro_fg.get("role") or "harmony"), -6.5, 0, min(bars, 4))
            transition = self.plan_transition(prev_sec, sec_type, int(prev_sec.get("target_key") or target_key) if prev_sec else None, target_key, bar, bars, layers, int(params.get("chaos") or 72), int(params.get("drama") or 82), rng)
            sec = {"bar_start": bar, "bars": bars, "type": sec_type, "energy_level": 0.9 if sec_type == "drop" else 0.7, "target_key": target_key, "transition_in": transition, "layers": layers}
            sections.append(sec)
            prev_sec = sec
            bar += bars
            idx += 1
        return {"bpm": render_bpm, "target_key": target_key, "seed": seed, "params": params, "engine": ENGINE_VERSION, "dj_compiler": {"version": "v0.7.3", "contract": "TasteSpec: ear-crated phrase atoms + deterministic compatibility graph + runtime ledger + turnover contract + keyless percussion + style gates; no fallback render is allowed"}, "bpm_lattice": {"target_bpm": user_bpm, "chosen_bpm": round(render_bpm,2), "chosen_by": "taste_feasibility" if lattice.get("best") else ("user_hint" if user_bpm else "taste_lattice_min_cost"), "best": lattice.get("best"), "candidates": lattice.get("lattice", [])[:8]}, "world_model": {"mode": "tastespec_graph", "taste_profile": params.get("taste_profile"), "rule": "floor rail + foreground rail + spark rail from approved EarAtoms"}, "tastespec": {"id": profile_data["id"], "version": profile_data["version"], "hash": profile_data["hash"]}, "taste_ledger": {"profile": params.get("taste_profile"), "graph_receipts": graph_receipts[:200], "source_contract": TASTE_PROFILES.get(str(params.get("taste_profile") or "girl_talk_v1"))}, "sections": sections}

    def taste_arrangement_gate(self, arrangement: Dict[str, Any]) -> Dict[str, Any]:
        params = arrangement.get("params") or {}
        profile = TASTE_PROFILES.get(str(params.get("taste_profile") or "girl_talk_v1"), TASTE_PROFILES["girl_talk_v1"])
        bpm = float(arrangement.get("bpm") or 120.0)
        sections = list(arrangement.get("sections") or [])
        failures: List[str] = []
        warnings: List[str] = []
        total_bars = sum(int(s.get("bars") or 0) for s in sections)
        floor_bars = 0; fg_bars = 0; first_fg_bar: Optional[int] = None
        source_bars: Dict[str, int] = {}
        for s in sections:
            start = int(s.get("bar_start") or 0)
            for ly in s.get("layers") or []:
                blen = int(ly.get("bar_len") or s.get("bars") or 0)
                role = str(ly.get("role") or "")
                ear = str(ly.get("ear_role") or "")
                src = str(ly.get("source_track_key") or ly.get("loop_id"))
                source_bars[src] = source_bars.get(src, 0) + blen
                if role in {"drum_anchor","bass","harmony","full"} or ear in {"DRUM_BREAK","BASS_RIFF","BED_CHORD","RIFF_ID"}:
                    floor_bars += blen
                if role == "vocal" or ear in {"VOX_HOOK","VOX_VERSE","VOX_SHOUT","RIFF_ID"}:
                    fg_bars += blen
                    first_fg_bar = start if first_fg_bar is None else min(first_fg_bar, start)
        floor_cov = floor_bars / max(1, total_bars)
        fg_cov = fg_bars / max(1, total_bars)
        source_count = len(source_bars)
        target_seconds = float(params.get("target_seconds") or (total_bars * 4 * 60.0 / bpm))
        need_sources = max(5, int(math.ceil(target_seconds / float(profile.get("source_seconds") or 11.5))))
        first_fg_s = None if first_fg_bar is None else first_fg_bar * 4 * 60.0 / bpm
        max_source_run_s = max(source_bars.values()) * 4 * 60.0 / bpm if source_bars else 0.0
        if floor_cov < float(profile.get("floor_coverage") or 0.70):
            failures.append(f"floor rail coverage too low ({floor_cov:.2f})")
        if fg_cov < float(profile.get("foreground_coverage") or 0.50):
            failures.append(f"foreground rail coverage too low ({fg_cov:.2f})")
        if first_fg_s is None:
            failures.append("no recognizable foreground rail")
        elif first_fg_s > float(profile.get("first_foreground_s") or 8.0):
            failures.append(f"first foreground arrives too late ({first_fg_s:.2f}s)")
        if source_count < need_sources:
            failures.append(f"source identity turnover too low ({source_count}/{need_sources})")
        if max_source_run_s > float(profile.get("max_source_run_s") or 16.0) * 1.8:
            warnings.append(f"one source dominates {max_source_run_s:.1f}s of planned bars")
        return {"passed": not failures, "failures": failures, "warnings": warnings, "metrics": {"floor_coverage": round(floor_cov,3), "foreground_coverage": round(fg_cov,3), "source_tracks": source_count, "needed_sources": need_sources, "first_foreground_s": None if first_fg_s is None else round(first_fg_s,2), "max_source_run_s": round(max_source_run_s,2)}}

    def _taste_harvest_projection(self, readiness, analyzed_count, total_files, report_all=False):
        """Project per-axis atom yield to the full library. Axes whose projected
        full-library yield (linear, 1.35x generosity) still misses the contract
        are returned; report_all returns a yield line for every failing axis."""
        have = readiness.get("have") or {}
        need = readiness.get("need") or {}
        remaining = max(0, int(total_files) - int(analyzed_count))
        out = []
        for axis, n in need.items():
            h = float(have.get(axis, 0))
            if h >= float(n):
                continue
            rate = h / max(1, analyzed_count)
            projected = h + rate * remaining
            line = f"{axis}: have {int(h)}, need {n}, yield {rate:.3f}/track over {analyzed_count} tracks, projected {int(projected)} at {total_files} tracks"
            if report_all:
                out.append(line)
            elif projected * 1.35 < float(n):
                out.append(line)
        return out

    def one_click_taste_mix(self, data: Dict[str, Any]) -> Dict[str, Any]:
        c = self.ensure_config()
        taste_profile = str(data.get("taste_profile") or "girl_talk_v1")
        track_budget = int(data.get("track_budget") or 240)
        force_loops = bool(data.get("force_loops", False))
        target_seconds = float(self.outcome_params(data).get("target_seconds") or data.get("target_seconds") or 120)
        ledger = self._perf_new_ledger("one_click_taste_mix", {
            "taste_profile": taste_profile,
            "track_budget": track_budget,
            "force_loops": force_loops,
            "target_seconds": target_seconds,
            "preset": str(data.get("preset") or ""),
            "bpm_hint": float(data.get("bpm") or 0.0),
        })
        try:
            self.set_status("TasteSpec: doctor", 0.02, True, None)
            doctor = self._perf_stage(ledger, "doctor", self.doctor)
            if not doctor.get("ok"):
                raise RuntimeError("Doctor failed; fix setup before TasteSpec compile")

            self.set_status("TasteSpec: scanning library", 0.06, True, None)
            scan_result = self._perf_stage(ledger, "scan_library", self.scan)

            # v0.7.2 fail-fast harvest (ported from v0.6.4): readiness is monotone
            # per-role counts, checkable per batch. Stop the moment the contract is
            # satisfied; refuse early with yield projection when it cannot be.
            batch = max(16, int(data.get("harvest_batch") or 96))
            exhaustive = bool(data.get("exhaustive", False))
            total_files = int(scan_result.get("total") or 0)
            analyze_result = {"ok": True, "analyzed": 0, "failed": []}
            loop_result = {"ok": True, "inserted": 0, "failed": []}
            crate_result = {"ok": True}
            harvest_log = []
            first_pass = True
            batch_idx = 0
            readiness = self._perf_stage(ledger, "taste_readiness_initial", self.taste_readiness, taste_profile, target_seconds)
            while not readiness.get("ready"):
                batch_idx += 1
                analyzed_count = int(self.conn().execute("SELECT COUNT(*) n FROM features WHERE analyzer_version=?", (ANALYZER_VERSION,)).fetchone()["n"])
                self.set_status(f"TasteSpec: harvesting batch {batch_idx} ({analyzed_count}/{total_files} tracks in)", min(0.70, 0.10 + 0.55 * (analyzed_count / max(1, total_files))), True, None)
                step = self._perf_stage(ledger, f"harvest_b{batch_idx}_analyze", self.analyze, limit=batch, force=False)
                analyze_result["analyzed"] = int(analyze_result.get("analyzed") or 0) + int(step.get("analyzed") or 0)
                analyze_result["failed"] = (analyze_result.get("failed") or []) + list(step.get("failed") or [])
                newly = int(step.get("analyzed") or 0)
                lstep = self._perf_stage(ledger, f"harvest_b{batch_idx}_extract_loops", self.extract_loops, limit=0, auto_approve=False, force=(force_loops and first_pass))
                loop_result["inserted"] = int(loop_result.get("inserted") or 0) + int(lstep.get("inserted") or 0)
                crate_result = self._perf_stage(ledger, f"harvest_b{batch_idx}_ear_crate", self.build_ear_crate, limit=0, force=(force_loops and first_pass), taste_profile=taste_profile, write_previews=False)
                first_pass = False
                readiness = self._perf_stage(ledger, f"harvest_b{batch_idx}_readiness", self.taste_readiness, taste_profile, target_seconds)
                analyzed_count = int(self.conn().execute("SELECT COUNT(*) n FROM features WHERE analyzer_version=?", (ANALYZER_VERSION,)).fetchone()["n"])
                harvest_log.append({"batch": batch_idx, "tracks_analyzed": analyzed_count, "have": dict(readiness.get("have") or {}), "need": dict(readiness.get("need") or {}), "ready": bool(readiness.get("ready"))})
                if readiness.get("ready"):
                    break
                if newly == 0:
                    break  # library exhausted; the verdict below is honest
                if not exhaustive and analyzed_count >= max(80, 3 * batch) and total_files > analyzed_count:
                    hopeless = self._taste_harvest_projection(readiness, analyzed_count, total_files)
                    if hopeless:
                        msg = f"TasteSpec crate refused early after {analyzed_count}/{total_files} tracks; projected full-library yield cannot satisfy the contract: " + "; ".join(hopeless)
                        with self.status_lock:
                            self.status["last_render_path"] = None
                        final_perf = self._perf_publish(ledger, ok=False, in_progress=False)
                        self.set_status(msg, 1, False, msg)
                        return {"ok": False, "error": msg, "early_refusal": True, "harvest": harvest_log, "scan": scan_result, "analyze": analyze_result, "loops": loop_result, "crate": crate_result, "readiness": readiness, "perf": final_perf.get("summary")}
            if not readiness.get("ready"):
                analyzed_count = int(self.conn().execute("SELECT COUNT(*) n FROM features WHERE analyzer_version=?", (ANALYZER_VERSION,)).fetchone()["n"])
                yields = self._taste_harvest_projection(readiness, analyzed_count, max(total_files, analyzed_count), report_all=True)
                msg = "TasteSpec crate refused theater: " + "; ".join(readiness.get("failures") or []) + ((" | yields: " + "; ".join(yields)) if yields else "")
                with self.status_lock:
                    self.status["last_render_path"] = None
                final_perf = self._perf_publish(ledger, ok=False, in_progress=False)
                self.set_status(msg, 1, False, msg)
                return {"ok": False, "error": msg, "harvest": harvest_log, "scan": scan_result, "analyze": analyze_result, "loops": loop_result, "crate": crate_result, "readiness": readiness, "perf": final_perf.get("summary")}

            self.set_status("TasteSpec: building compatibility graph", 0.76, True, None)
            graph = self._perf_stage(ledger, "build_compatibility_graph", self.build_compatibility_graph, taste_profile, target_seconds, float(data.get("bpm") or 0.0))
            params = self.outcome_params(data)
            params.update({"taste_profile": taste_profile, "name": str(data.get("name") or "EarCrate Set"), "target_seconds": int(target_seconds), "quality_mode": "stable_deck", "post_render_gate": True})
            # Station steering: crowd 🔥/🧊 receipts bias the compile intent.
            _bias = self.station_bias()
            for _k in ("chaos", "vocal_density", "drama"):
                if _bias.get(_k):
                    params[_k] = max(0, min(100, int(params.get(_k, 70)) + int(_bias[_k])))
            if any(_bias.get(k) for k in ("chaos", "vocal_density", "drama")):
                params["station_bias"] = {k: _bias.get(k, 0) for k in ("chaos", "vocal_density", "drama")}
            self.set_status("TasteSpec: composing deterministic rail plan", 0.84, True, None)
            try:
                proposal = self._perf_stage(ledger, "compose_and_gate_taste_plan", self.propose_taste_mashup, params)
            except RuntimeError as exc:
                proposal = None
                last_exc = exc
                miss_idx = 0
                while proposal is None:
                    miss_idx += 1
                    self.set_status(f"TasteSpec: harvesting more after gate miss (pass {miss_idx})", 0.86, True, None)
                    step = self._perf_stage(ledger, f"miss{miss_idx}_analyze", self.analyze, limit=batch, force=False)
                    analyze_result["analyzed"] = int(analyze_result.get("analyzed") or 0) + int(step.get("analyzed") or 0)
                    lstep = self._perf_stage(ledger, f"miss{miss_idx}_extract_loops", self.extract_loops, limit=0, auto_approve=False, force=False)
                    loop_result["inserted"] = int(loop_result.get("inserted") or 0) + int(lstep.get("inserted") or 0)
                    crate_result = self._perf_stage(ledger, f"miss{miss_idx}_ear_crate", self.build_ear_crate, limit=0, force=False, taste_profile=taste_profile, write_previews=False)
                    try:
                        proposal = self._perf_stage(ledger, f"miss{miss_idx}_compose_and_gate", self.propose_taste_mashup, params)
                    except RuntimeError as exc2:
                        last_exc = exc2
                        if int(step.get("analyzed") or 0) == 0:
                            raise last_exc
            self.set_status("TasteSpec: rendering accepted plan", 0.90, True, None)
            executed = self._perf_stage(ledger, "execute_manifest_render", self.execute_manifest, proposal["manifest"], apply=True)
            render_path = None
            rejected = []
            for item in executed.get("done", []):
                if item.get("type") == "render_mashup" and item.get("path"):
                    render_path = item.get("path")
                elif item.get("type") == "render_rejected":
                    rejected.append(item)
            if render_path:
                with self.status_lock:
                    self.status["last_render_path"] = render_path
                final_perf = self._perf_publish(ledger, ok=True, in_progress=False)
                self.set_status("TasteSpec one-click complete; accepted render loaded", 1.0, False)
                return {"ok": True, "render_path": render_path, "scan": scan_result, "analyze": analyze_result, "loops": loop_result, "crate": crate_result, "readiness": readiness, "graph": graph, "proposal": {"manifest": proposal["manifest"], "dst": proposal.get("dst")}, "execute": executed, "harvest": harvest_log, "perf": final_perf.get("summary")}
            msg = "TasteSpec render rejected after accepted plan; inspect rejected report"
            with self.status_lock:
                self.status["last_render_path"] = None
            final_perf = self._perf_publish(ledger, ok=False, in_progress=False)
            self.set_status(msg, 1, False, msg)
            return {"ok": False, "error": msg, "rejected": rejected, "scan": scan_result, "analyze": analyze_result, "loops": loop_result, "crate": crate_result, "readiness": readiness, "graph": graph, "proposal": proposal, "execute": executed, "perf": final_perf.get("summary")}
        except Exception:
            self._perf_publish(ledger, ok=False, in_progress=False)
            raise

    def preflight(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Render-free readiness audit: answers 'will this plan work?' in seconds.

        Runs the BPM lattice and crate-readiness audit over the approved pool so
        the UI can show usable-loops-per-role, native-BPM windows, transform tiers,
        source dominance, and a recommended speed BEFORE spending a full render.
        """
        params = self.outcome_params(data) if data.get("preset") else dict(data)
        pool = self.approved_loop_pool()
        if not pool:
            return {"ok": False, "pool_size": 0, "ready": False,
                    "warnings": ["No approved loops. Analyze, extract, then approve a hot pool by quota."]}
        target_key = self.choose_target_key_for_pool(pool)
        user_bpm = float(params.get("bpm") or 0) or None
        stretch_budget = float(params.get("stretch_budget") or 0) or None
        pitch_budget = float(params.get("pitch_shift_budget") or 0) or None
        track_seconds = float(params.get("target_seconds") or params.get("seconds") or 120)
        audit = crate_readiness_audit(pool, user_bpm, target_key, stretch_budget, pitch_budget, track_seconds)
        audit["ok"] = True
        audit["target_key"] = target_key
        audit["user_bpm"] = user_bpm
        return audit

    def arrangement_preflight_gate(self, arrangement: Dict[str, Any]) -> Dict[str, Any]:
        """Cheap non-audio veto before any full WAV render is attempted.

        The structural contract is now explicit: a full-length sketch must have
        enough planned layer coverage to become a song. Spectral quality alone is
        not allowed to bless a one-layer tail at the end of a two-minute file.
        """
        score = self.score_arrangement(arrangement)
        sections = list(arrangement.get("sections") or [])
        layers = [ly for sec in sections for ly in sec.get("layers", [])]
        avg_high = float(np.mean([float(ly.get("dry_high3000_share") or 0.0) for ly in layers])) if layers else 0.0
        avg_quality = float(np.mean([float(ly.get("dry_quality_score") or 0.0) for ly in layers])) if layers else 0.0
        failures: List[str] = []
        warnings: List[str] = []
        if score.get("veto"):
            # Name only violated conditions with values (v0.6.4); the old reporter
            # listed every truthy metric and named bystanders on every veto.
            causes = []
            if int(score.get("transform_violations") or 0):
                causes.append(f"transform_violations={int(score.get('transform_violations') or 0)}")
            if int(score.get("role_leaks") or 0):
                causes.append(f"role_leaks={int(score.get('role_leaks') or 0)}")
            if float(score.get("predicted_silence_ratio") or 0.0) > 0.10:
                causes.append(f"predicted_silence_ratio={float(score.get('predicted_silence_ratio') or 0.0):.3f}>0.10")
            if int(score.get("max_source_reuse") or 0) > 12:
                causes.append(f"max_source_reuse={int(score.get('max_source_reuse') or 0)}>12")
            if float(score.get("source_diversity") or 1.0) < 0.16:
                causes.append(f"source_diversity={float(score.get('source_diversity') or 0.0):.3f}<0.16")
            _cov = float(score.get("covered_bar_ratio") or 0.0)
            _lev = int(score.get("layer_events") or 0)
            _flb = score.get("first_layer_bar")
            _ms = max(1, int(score.get("music_sections") or 1))
            _ems = int(score.get("empty_music_sections") or 0)
            if _cov < 0.62 or _lev < 6 or _flb is None or (_flb is not None and int(_flb) > 4) or (_ems / _ms > 0.25):
                causes.append(f"structural_empty(covered={_cov:.2f}, layer_events={_lev}, first_layer_bar={_flb}, empty_sections={_ems}/{_ms})")
            failures.append("arrangement score veto: " + (", ".join(causes) if causes else "unattributed; inspect candidate_search.selected_score"))
        covered = float(score.get("covered_bar_ratio") or 0.0)
        layer_events = int(score.get("layer_events") or 0)
        first_layer_bar = score.get("first_layer_bar")
        music_sections = max(1, int(score.get("music_sections") or 1))
        empty_music = int(score.get("empty_music_sections") or 0)
        if covered < 0.62:
            failures.append(f"preflight layer coverage too low ({covered:.2f}); would render mostly empty timeline")
        elif covered < 0.76:
            warnings.append(f"preflight layer coverage marginal ({covered:.2f})")
        if layer_events < 6:
            failures.append(f"preflight layer event count too low ({layer_events}); no song body")
        if empty_music / music_sections > 0.25:
            failures.append("preflight has too many non-cut sections with no layers")
        if first_layer_bar is None:
            failures.append("preflight found no planned audio layers")
        elif float(first_layer_bar) > 4.0:
            failures.append(f"preflight first layer starts too late at bar {float(first_layer_bar):.1f}")
        if avg_high < 0.016:
            failures.append("preflight high-frequency share too low; likely cave/muffle")
        elif avg_high < 0.026:
            warnings.append("preflight high-frequency share low; choose brighter material or keep presence repair")
        if avg_quality < 0.18:
            failures.append("preflight loop quality too low")
        elif avg_quality < 0.30:
            warnings.append("preflight loop quality marginal")
        return {"passed": not failures, "failures": failures, "warnings": warnings, "score": score, "avg_high3000_share": avg_high, "avg_dry_quality_score": avg_quality}

    def propose_mashup(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Back-compat entry: routes every proposal through the TasteSpec composer.
        The legacy two-world arranger this used to dispatch to no longer exists."""
        params = dict(params)
        params.setdefault("taste_profile", "girl_talk_v1")
        return self.propose_taste_mashup(params)

    def score_arrangement(self, arrangement: Dict[str, Any]) -> Dict[str, Any]:
        """Cheap arrangement-only scorer so many candidate plans can compete before audio render."""
        sections = list(arrangement.get("sections") or [])
        layers = [ly for sec in sections for ly in sec.get("layers", [])]
        track_keys = {str(ly.get("source_track_key") or ly.get("loop_id")) for ly in layers}
        worlds = [str(ly.get("world") or "") for ly in layers]
        keys = [int(sec.get("target_key") or 0) % 12 for sec in sections]
        transitions = [sec.get("transition_in") or {} for sec in sections]
        named = sum(1 for t in transitions if t.get("type") in {"beatmatch_blend", "bass_swap", "hook_blend_over_bed", "acapella_bridge", "impact_drop", "hard_cut_pickup"})
        false_blend = sum(1 for t in transitions if t.get("type") == "bass_swap" and t.get("prev_bass_owner") and t.get("prev_bass_owner") == t.get("next_bass_owner"))
        dynamic = sum(1 for sec in sections if sec.get("type") in {"cut", "breakdown", "drop"})
        cut_bars = sum(int(sec.get("bars") or 0) for sec in sections if sec.get("type") == "cut")
        hard_air = sum(1 for t in transitions if t.get("type") == "hard_cut_to_air")
        duration_bars = sum(int(sec.get("bars") or 0) for sec in sections)
        predicted_silence = cut_bars / max(1, duration_bars)
        # Count voice/bed by the layer's actual role, not only the legacy two-world
        # "world" tag. The TasteSpec composer tags every layer world="taste" and
        # marks vocals via role/ear_role, so the old worlds.count("voice") read 0 on
        # every TasteSpec render — blinding the vocal_density intent-match and the
        # voice-missing veto. Recognize both vocabularies.
        _VOX_EAR = {"VOX_HOOK", "VOX_VERSE", "VOX_SHOUT"}
        _BED_ROLE = {"drum_anchor", "bass", "harmony", "full"}
        _BED_EAR = {"DRUM_BREAK", "BED_CHORD", "RIFF_ID", "TEXTURE", "BASS_RIFF"}
        voice = sum(1 for ly in layers if str(ly.get("world")) == "voice"
                    or str(ly.get("role")) == "vocal" or str(ly.get("ear_role")) in _VOX_EAR)
        bed = sum(1 for ly in layers if str(ly.get("world")) == "bed"
                  or str(ly.get("role")) in _BED_ROLE or str(ly.get("ear_role")) in _BED_EAR)
        music_sections = [sec for sec in sections if str(sec.get("type") or "") != "cut"]
        empty_music_sections = sum(1 for sec in music_sections if not sec.get("layers"))
        covered_bars_set = set()
        first_layer_bar: Optional[int] = None
        weighted_layer_bars = 0.0
        for sec in sections:
            sec_start = int(sec.get("bar_start") or 0)
            sec_bars = int(sec.get("bars") or 0)
            for ly in sec.get("layers", []) or []:
                off = max(0, int(ly.get("bar_offset") or 0))
                blen = int(ly.get("bar_len") or sec_bars or 0)
                blen = max(0, min(sec_bars - off if sec_bars else blen, blen))
                if blen <= 0:
                    continue
                abs_start = sec_start + off
                first_layer_bar = abs_start if first_layer_bar is None else min(first_layer_bar, abs_start)
                weighted_layer_bars += float(blen)
                for b in range(abs_start, abs_start + blen):
                    covered_bars_set.add(b)
        covered_bar_ratio = len(covered_bars_set) / max(1, duration_bars)
        avg_layer_depth = weighted_layer_bars / max(1, duration_bars)
        source_diversity = len(track_keys) / max(1, len(layers))
        pitch_diversity = len(set(keys))
        params = arrangement.get("params") or {}
        transform_violations = 0
        role_leaks = 0
        max_stretch_seen = 0.0
        max_pitch_seen = 0.0
        by_source: Dict[str, int] = {}
        for ly in layers:
            role = str(ly.get("role") or "full")
            ps = float(ly.get("residual_pitch_shift", ly.get("pitch_shift") or 0.0) or 0.0)
            stretch_pct = float(ly.get("varispeed_pct") or 0.0)
            max_stretch_seen = max(max_stretch_seen, stretch_pct)
            max_pitch_seen = max(max_pitch_seen, abs(ps))
            if drydeck_transform_violation(role, ps, stretch_pct):
                transform_violations += 1
            k = str(ly.get("source_track_key") or ly.get("loop_id"))
            by_source[k] = by_source.get(k, 0) + 1
        max_source_reuse = max(by_source.values()) if by_source else 0
        tail_density = sum(int(t.get("xfade_beats") or 0) for t in transitions if t.get("type") not in {"start", "hard_cut_to_air", "impact_drop"}) / max(1, len(transitions))

        n_sec = max(1, len(sections))
        avg_bars = duration_bars / n_sec
        realized_chaos = _clamp01((8.0 - avg_bars) / 6.0)
        realized_drama = _clamp01((dynamic / n_sec) * 2.5)
        realized_whiplash = _clamp01(source_diversity * 1.6)
        realized_vocal = _clamp01(voice / max(1, len(layers)))
        t_chaos = _clamp01(float(params.get("chaos", 55)) / 100.0)
        t_drama = _clamp01(float(params.get("drama", 70)) / 100.0)
        t_whip = _clamp01(float(params.get("genre_whiplash", 55)) / 100.0)
        t_vocal = _clamp01(float(params.get("vocal_density", 70)) / 100.0)
        drama_air_allow = 0.02 + 0.06 * t_drama

        total = 0.0
        total += 4.0 * min(1.0, source_diversity * 2.0)
        total += 0.2 * named
        total += 3.0 if (voice and bed) else 0.0
        total += 8.0 * (1.0 - abs(t_chaos - realized_chaos))
        total += 7.0 * (1.0 - abs(t_drama - realized_drama))
        total += 6.0 * (1.0 - abs(t_whip - realized_whiplash))
        total += 5.0 * (1.0 - abs(t_vocal - realized_vocal))
        # Make a song body a first-class objective, not an accidental byproduct.
        total += 12.0 * min(1.0, covered_bar_ratio)
        total += 5.0 * min(1.0, avg_layer_depth / 1.5)
        total -= 26.0 * max(0.0, 0.62 - covered_bar_ratio)
        total -= 4.0 * max(0, 6 - len(layers))
        total -= 6.0 if (first_layer_bar is None or first_layer_bar > 4) else 0.0
        total -= 14.0 * transform_violations
        total -= 12.0 * role_leaks
        total -= 3.0 * false_blend
        total -= 24.0 * max(0.0, predicted_silence - drama_air_allow)
        total -= 2.8 * max(0, max_source_reuse - 6)
        total -= 1.0 * max(0.0, tail_density - 3.0)
        total -= 2.5 * max(0, hard_air - 1)
        total -= 10.0 * max(0.0, 0.20 - source_diversity)
        total -= 3.0 * empty_music_sections
        structural_empty = covered_bar_ratio < 0.62 or len(layers) < 6 or first_layer_bar is None or first_layer_bar > 4 or (music_sections and empty_music_sections / max(1, len(music_sections)) > 0.25)
        veto = bool(transform_violations or role_leaks or predicted_silence > 0.10 or max_source_reuse > 12 or source_diversity < 0.16 or structural_empty)
        return {"total": round(float(total), 4), "veto": veto, "transform_violations": transform_violations, "role_leaks": role_leaks, "predicted_silence_ratio": round(float(predicted_silence), 4), "hard_air_transitions": int(hard_air), "max_stretch_pct": round(float(max_stretch_seen), 3), "max_abs_residual_pitch_shift": round(float(max_pitch_seen), 3), "max_source_reuse": int(max_source_reuse), "source_tracks": len(track_keys), "source_diversity": round(float(source_diversity), 4), "pitch_centers": pitch_diversity, "named_transitions": named, "dynamic_sections": dynamic, "false_blends": false_blend, "voice_layers": voice, "bed_layers": bed, "duration_bars": duration_bars, "layer_events": len(layers), "covered_bar_ratio": round(float(covered_bar_ratio), 4), "avg_layer_depth": round(float(avg_layer_depth), 4), "music_sections": len(music_sections), "empty_music_sections": int(empty_music_sections), "first_layer_bar": first_layer_bar, "realized_chaos": round(realized_chaos, 3), "realized_drama": round(realized_drama, 3), "realized_whiplash": round(realized_whiplash, 3), "realized_vocal": round(realized_vocal, 3), "intent_targets": {"chaos": round(t_chaos, 3), "drama": round(t_drama, 3), "whiplash": round(t_whip, 3), "vocal": round(t_vocal, 3)}}

    def choose_target_key_for_pool(self, pool: List[Dict[str, Any]]) -> int:
        counts: Dict[int, float] = {}
        for x in pool:
            try:
                k = int(x.get("key_root")) % 12
            except Exception:
                continue
            counts[k] = counts.get(k, 0.0) + float(x.get("score") or 0.5)
        if not counts:
            return 0
        return max(counts, key=counts.get)

    def plan_transition(self, prev_sec: Optional[Dict[str, Any]], sec_type: str, prev_key: Optional[int], next_key: int, bar_start: int, bars: int, layers: List[Dict[str, Any]], chaos: int, drama: int, rng: random.Random) -> Dict[str, Any]:
        """Compile basic DJ transition grammar into the arrangement report."""
        if prev_sec is None:
            return {"type": "start", "xfade_beats": 0, "curve": "none", "phrase_boundary": "downbeat", "harmonic_relation": "start", "bass_policy": "none"}
        prev_type = str(prev_sec.get("type") or "sustain")
        relation = harmonic_relation_name(prev_key, next_key)
        prev_layers = prev_sec.get("layers") or []
        prev_bass = next((x.get("loop_id") for x in prev_layers if x.get("role") in ("bass", "drum_anchor", "full")), None)
        next_bass = next((x.get("loop_id") for x in layers if x.get("role") in ("bass", "drum_anchor", "full")), None)
        phrase = "16_bar" if bar_start % 16 == 0 else ("8_bar" if bar_start % 8 == 0 else ("4_bar" if bar_start % 4 == 0 else "pickup"))
        if sec_type == "cut":
            typ, beats, curve, bass_policy = "hard_cut_to_air", 0, "none", "clear_floor"
        elif prev_type in ("cut", "breakdown") and sec_type == "drop":
            typ, beats, curve, bass_policy = "impact_drop", 0, "none", "incoming_on_downbeat"
        elif sec_type == "breakdown":
            typ, beats, curve, bass_policy = "acapella_bridge", 2 if drama >= 60 else 4, "s_curve", "strip_low_end"
        elif prev_bass and next_bass and prev_bass != next_bass:
            typ, beats, curve, bass_policy = "bass_swap", 8 if chaos < 80 else 4, "equal_power", "one_low_owner"
        elif prev_bass and next_bass and prev_bass == next_bass:
            # Same floor owner is not a bass handoff; it is a hook/texture blend riding over a preserved bed.
            typ, beats, curve, bass_policy = "hook_blend_over_bed", 4, "equal_power", "preserve_floor"
        elif relation in ("same_key", "dominant", "subdominant", "relative_or_parallel"):
            typ, beats, curve, bass_policy = "beatmatch_blend", 8 if chaos < 76 else 4, "equal_power", "one_low_owner"
        else:
            typ, beats, curve, bass_policy = "hard_cut_pickup", 1, "s_curve", "incoming_on_downbeat"
        return {"type": typ, "xfade_beats": int(beats), "curve": curve, "phrase_boundary": phrase, "harmonic_relation": relation, "bass_policy": bass_policy, "low_cutoff_hz": 170, "prev_bass_owner": prev_bass, "next_bass_owner": next_bass}

    def write_manifest(self, author: str, seed: int, summary: str, operations: List[Dict[str, Any]]) -> str:
        c = self.ensure_config()
        mid = ulidish()
        manifest = {"manifest_id": mid, "created_at": now_utc(), "author": author, "seed": seed, "summary": summary, "operations": operations}
        name = safe_name(summary, "manifest")[:60]
        path = c.agent_root / "manifests" / f"{_dt.datetime.now().strftime('%Y%m%d')}-{name}-{mid[:8]}.json"
        path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        return str(path)

    def list_manifests(self) -> Dict[str, Any]:
        c = self.ensure_config()
        items = []
        for p in sorted((c.agent_root / "manifests").glob("*.json"), key=lambda x: x.stat().st_mtime, reverse=True):
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                items.append({"path": str(p), "manifest_id": data.get("manifest_id"), "summary": data.get("summary"), "created_at": data.get("created_at"), "operations": len(data.get("operations") or [])})
            except Exception:
                items.append({"path": str(p), "manifest_id": None, "summary": "unreadable", "created_at": None, "operations": 0})
        return {"items": items[:200]}


    def seed_demo_renders(self, count: int = 8, bars: int = 8, bpm: int = 100) -> Dict[str, Any]:
        """Warm-up demo: synthesize a few listenable chord+kick loops into renders/
        so a brand-new workspace can PLAY immediately (Endless has material) while
        the real library compiles. Clearly a demo — no real music, made locally."""
        c = self.ensure_config()
        renders = c.working_root / "renders"
        renders.mkdir(parents=True, exist_ok=True)
        sr = 44100
        beat = 60.0 / float(bpm or 100)
        A = 220.0
        keys = {k: A * 2 ** (semi / 12.0) for k, semi in {"A": 0, "C": 3, "D": 5, "E": 7, "F": 8, "G": 10}.items()}
        progs = [["A","C","D","E"],["C","G","A","F"],["D","A","E","C"],["E","C","G","D"],
                 ["F","C","D","A"],["G","D","E","C"],["A","E","F","C"],["C","D","A","G"],
                 ["D","F","G","A"],["E","A","C","D"]]
        def _loop(name, roots):
            dur = beat * 4 * bars
            t = np.arange(int(sr * dur)) / sr
            y = np.zeros_like(t)
            seg = len(t) // len(roots)
            for i, r in enumerate(roots):
                a = i * seg; b = (i + 1) * seg if i < len(roots) - 1 else len(t)
                tt = t[a:b] - t[a]
                env = np.minimum(1.0, np.minimum(tt / 0.05, (tt[-1] - tt) / 0.15 + 1)) if len(tt) else tt
                chord = (np.sin(2*np.pi*r*tt) + 0.6*np.sin(2*np.pi*r*1.26*tt) + 0.5*np.sin(2*np.pi*r*1.5*tt)) / 2.1
                y[a:b] += chord * env * 0.5
            for bt in range(int(dur / beat)):
                k0 = int(bt * beat * sr); kl = int(0.09 * sr); kt = np.arange(kl) / sr
                y[k0:k0+kl] += np.sin(2*np.pi*(120*np.exp(-kt*18)+45)*kt) * np.exp(-kt*32) * 0.7
            y = np.tanh(y * 1.1) * 0.85
            st = np.stack([y, y], axis=1).astype(np.float32)
            sf.write(str(renders / (name + ".wav")), st, sr)
            (renders / (name + ".render_report.json")).write_text(json.dumps(
                {"engine_version": ENGINE_VERSION, "quality_gate": {"passed": True},
                 "render_timestamp": now_utc(), "demo": True,
                 "note": "synthesized warm-up demo (no real music) — Book a set to compile your library"}),
                encoding="utf-8")
            return str(renders / (name + ".wav"))
        n = max(1, min(len(progs), int(count)))
        made = [_loop("demo_%02d_%s" % (i + 1, "".join(progs[i])), [keys[k] for k in progs[i]]) for i in range(n)]
        return {"ok": True, "seeded": len(made), "dir": str(renders),
                "note": "demo warm-up renders written; press Endless to play them continuously"}


    # ---- Deep clean: look at each file's audio GRAPH, not its tags ----------
    # Separates real songs from static junk by decoding and measuring the sound
    # itself. It does NOT judge genre: spoken word, classical, lo-fi, and Elvis
    # all pass. Only silence, broadband static/noise, non-decodable/corrupt
    # files, and empty fragments are flagged. Also finds empty and art-only
    # folders. Dry-run / assessment only; nothing is moved here.
    def assess_track_audio(self, path, sr: int = 22050, probe: float = 45.0) -> Dict[str, Any]:
        from earcrate.analyze.decode import decode_audio
        try:
            y = decode_audio(Path(path), sr=sr, start=0.0, duration=probe)
        except Exception as exc:
            return {"real": False, "reason": "does not decode (corrupt or not really audio)", "detail": str(exc)[:80]}
        y = np.asarray(y, dtype=np.float32)
        if y.size < sr:  # under ~1 second of samples
            return {"real": False, "reason": "empty / under 1s fragment", "seconds": round(float(y.size) / sr, 2)}
        rms = float(np.sqrt(np.mean(y ** 2)))
        frames = librosa.feature.rms(y=y)[0]
        silent_frac = float(np.mean(frames < 1e-3)) if frames.size else 1.0
        S = np.abs(librosa.stft(y))
        flat = float(np.mean(librosa.feature.spectral_flatness(S=S)[0])) if S.size else 1.0
        if rms < 1e-3 or silent_frac > 0.97:
            return {"real": False, "reason": "silent", "rms": round(rms, 5), "silent_frac": round(silent_frac, 3)}
        if flat > 0.5:
            return {"real": False, "reason": "broadband static / noise (no tonal or rhythmic structure)",
                    "flatness": round(flat, 3)}
        return {"real": True, "reason": "real audio", "seconds": round(float(y.size) / sr, 1),
                "rms": round(rms, 5), "flatness": round(flat, 3)}

    def deep_clean_scan(self, data: Dict[str, Any]) -> Dict[str, Any]:
        c = self.ensure_config()
        root = Path(str(data.get("root") or c.master_root)).expanduser().resolve()
        if not root.is_dir():
            return {"ok": False, "error": f"not a folder: {root}"}
        limit = int(data.get("limit") or 0)
        image_exts = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp"}
        sidecar_exts = {".nfo", ".txt", ".m3u", ".m3u8", ".cue", ".log", ".ini", ".db", ".url", ".sfv", ".pls"}
        all_files = [p for p in root.rglob("*") if p.is_file()]
        audio = [p for p in all_files if p.suffix.lower() in AUDIO_EXTS]
        images = [p for p in all_files if p.suffix.lower() in image_exts]
        sidecars = [p for p in all_files if p.suffix.lower() in sidecar_exts]
        checked = audio[:limit] if limit > 0 else audio
        real, junk = 0, []
        for p in checked:
            verdict = self.assess_track_audio(p)
            if verdict.get("real"):
                real += 1
            else:
                junk.append({"path": str(p), "reason": verdict.get("reason"), **{k: verdict[k] for k in ("flatness", "rms", "detail", "seconds") if k in verdict}})
        # folder-level junk: empty folders, and folders that hold files but no audio anywhere below
        empty_folders, art_only_folders = [], []
        for d in [p for p in root.rglob("*") if p.is_dir()]:
            try:
                entries = list(d.iterdir())
            except Exception:
                continue
            files_below = [f for f in d.rglob("*") if f.is_file()]
            if not files_below and not any(x.is_dir() for x in entries):
                empty_folders.append(str(d)); continue
            has_audio_below = any(f.suffix.lower() in AUDIO_EXTS for f in files_below)
            if files_below and not has_audio_below and not any((sub / "x").exists() for sub in []):
                # a leaf-ish folder with files but zero audio anywhere under it = art/nfo clutter
                if not any(x.is_dir() for x in entries):
                    art_only_folders.append(str(d))
        sig = sha256_text(json_dumps(sorted([j["path"] for j in junk] + empty_folders + art_only_folders)))
        return {"ok": True, "dry_run": True, "root": str(root),
                "audio_files": len(audio), "checked": len(checked),
                "real_songs": real, "junk_count": len(junk),
                "image_files": len(images), "sidecar_files": len(sidecars),
                "empty_folders": empty_folders[:50], "empty_folder_count": len(empty_folders),
                "art_only_folders": art_only_folders[:50], "art_only_folder_count": len(art_only_folders),
                "junk": junk[:200], "signature": sig,
                "human": (f"Listened to {len(checked)} of {len(audio)} audio file(s): {real} real, "
                          f"{len(junk)} static/junk. Plus {len(empty_folders)} empty and "
                          f"{len(art_only_folders)} art-only folder(s), {len(images)} loose image(s). "
                          f"Nothing moved — this is the assessment.")}


    def list_renders(self) -> Dict[str, Any]:
        c = self.ensure_config()
        render_dir = c.working_root / "renders"
        render_dir.mkdir(parents=True, exist_ok=True)
        exts = {".wav", ".flac", ".mp3", ".m4a", ".ogg", ".opus"}
        items = []
        for p in sorted([x for x in render_dir.iterdir() if x.is_file() and x.suffix.lower() in exts], key=lambda x: x.stat().st_mtime, reverse=True):
            rp = p.resolve()
            self.validate_path_in_root(rp, render_dir)
            st = rp.stat()
            report_path = rp.with_suffix(".render_report.json")
            engine = None
            gate_passed = None
            if report_path.exists():
                try:
                    report = json.loads(report_path.read_text(encoding="utf-8"))
                    engine = report.get("engine_version")
                    gate = report.get("quality_gate") or {}
                    if gate:
                        gate_passed = bool(gate.get("passed"))
                except Exception:
                    pass
            items.append({"path": str(rp), "name": rp.name, "size_bytes": st.st_size, "mtime": _dt.datetime.fromtimestamp(st.st_mtime).isoformat(timespec="seconds"), "engine_version": engine, "quality_gate_passed": gate_passed, "current_engine": engine == ENGINE_VERSION})
        return {"items": items[:50], "current_engine": ENGINE_VERSION}

    def validate_rollback_src(self, path: Path) -> Path:
        c = self.ensure_config()
        rp = self.validate_not_master(path)
        allowed_roots = [
            c.working_root / "renders",
            c.playlists_root,
            c.agent_root / "rejected_renders",
        ]
        last_error: Optional[Exception] = None
        for root in allowed_roots:
            try:
                return self.validate_path_in_root(rp, root)
            except ValueError as exc:
                last_error = exc
        raise ValueError(f"rollback source outside permitted output roots: {rp}") from last_error

    def prevalidate_manifest(self, manifest_path: str) -> Dict[str, Any]:
        c = self.ensure_config()
        p = Path(manifest_path).expanduser().resolve()
        self.validate_path_in_root(p, c.agent_root / "manifests")
        manifest = json.loads(p.read_text(encoding="utf-8"))
        manifest_sha = hashlib.sha256(p.read_bytes()).hexdigest()
        ops = manifest.get("operations") or []
        plan: List[Dict[str, Any]] = []
        for idx, op in enumerate(ops):
            op_type = op.get("type")
            if op_type not in VALID_OPS:
                raise ValueError(f"executor rejects op type {op_type!r}")
            args = op.get("args") or {}
            if op_type == "render_mashup":
                dst = self.validate_not_master(Path(args["dst"])).resolve()
                self.validate_path_in_root(dst, c.working_root / "renders")
                if (op.get("preconditions") or {}).get("dst_absent") and dst.exists():
                    raise ValueError(f"destination exists: {dst}")
                row = self.conn().execute("SELECT id, arrangement_sha FROM mashups WHERE id=?", (args.get("mashup_id"),)).fetchone()
                if not row:
                    raise RuntimeError(f"mashup not found: {args.get('mashup_id')}")
                plan.append({
                    "index": idx,
                    "op_id": op.get("op_id"),
                    "type": op_type,
                    "mashup_id": args.get("mashup_id"),
                    "would_write": [str(dst), str(dst.with_suffix(".render_report.json"))],
                    "rollback_inverse": {"type": "archive_move", "src": str(dst), "reason": "rollback render output"},
                    "preconditions": op.get("preconditions") or {},
                })
            elif op_type == "create_playlist":
                name = safe_name(args.get("name") or "playlist") + ".m3u8"
                dst = (c.playlists_root / name).resolve()
                self.validate_not_master(dst)
                self.validate_path_in_root(dst, c.playlists_root)
                plan.append({
                    "index": idx,
                    "op_id": op.get("op_id"),
                    "type": op_type,
                    "would_write": [str(dst)],
                    "rollback_inverse": {"type": "archive_move", "src": str(dst), "reason": "rollback playlist output"},
                    "preconditions": op.get("preconditions") or {},
                })
            elif op_type == "ingest_copy":
                src = Path(args["src"]).expanduser().resolve()
                dst = Path(args["dst"]).expanduser().resolve()
                if not src.is_file():
                    raise ValueError(f"ingest source missing: {src}")
                self.validate_path_in_root(dst, c.master_root / "ingested")
                if dst.exists():
                    raise ValueError(f"destination exists: {dst}")
                plan.append({"index": idx, "op_id": op.get("op_id"), "type": op_type, "would_write": [str(dst)], "rollback_inverse": {"type": "archive_move", "src": str(dst), "reason": "rollback ingested copy"}, "preconditions": op.get("preconditions") or {}})
            elif op_type == "organize_copy":
                src = Path(args["src"]).expanduser().resolve()
                dst = Path(args["dst"]).expanduser().resolve()
                if not src.is_file():
                    raise ValueError(f"organize source missing: {src}")
                self.validate_not_master(dst)
                self.validate_path_in_root(dst, c.working_root / "organized")
                if dst.exists():
                    raise ValueError(f"destination exists: {dst}")
                plan.append({"index": idx, "op_id": op.get("op_id"), "type": op_type, "would_write": [str(dst)], "rollback_inverse": {"type": "archive_move", "src": str(dst), "reason": "rollback organized copy"}, "preconditions": op.get("preconditions") or {}})
        return {"path": str(p), "manifest": manifest, "manifest_sha256": manifest_sha, "operations": ops, "plan": plan}

    def execute_manifest(self, manifest_path: str, apply: bool = False) -> Dict[str, Any]:
        c = self.ensure_config()
        prepared = self.prevalidate_manifest(manifest_path)
        manifest = prepared["manifest"]
        manifest_sha = prepared["manifest_sha256"]
        ops = prepared["operations"]
        plan = prepared["plan"]
        if not apply:
            return {
                "ok": True,
                "dry_run": True,
                "apply_required": True,
                "message": "dry run only; pass apply=true or --apply to write outputs",
                "manifest_id": manifest.get("manifest_id"),
                "manifest_sha256": manifest_sha,
                "would_execute": len(plan),
                "plan": plan,
            }
        done = []
        self.set_status("executing manifest", 0, True, None)
        for idx, op in enumerate(ops):
            rec_base = {"ulid": ulidish(), "ts": now_utc(), "manifest_id": manifest.get("manifest_id"), "manifest_sha256": manifest_sha, "op_id": op.get("op_id"), "type": op.get("type")}
            try:
                if op["type"] == "render_mashup":
                    dst = Path(op["args"]["dst"]).resolve()
                    inverse = {**rec_base, "inverse": {"type": "archive_move", "src": str(dst), "reason": "rollback render output"}}
                    fsync_append_jsonl(c.agent_root / "rollback.jsonl", inverse)
                    out = self.render_mashup(op["args"]["mashup_id"], dst)
                    done.append(out)
                elif op["type"] == "create_playlist":
                    out = self.execute_create_playlist(op)
                    inverse = {**rec_base, "inverse": {"type": "archive_move", "src": out["path"], "reason": "rollback playlist output"}}
                    fsync_append_jsonl(c.agent_root / "rollback.jsonl", inverse)
                    done.append(out)
                elif op["type"] == "ingest_copy":
                    out = self.execute_ingest_copy(op)
                    inverse = {**rec_base, "inverse": {"type": "archive_move", "src": out["path"], "reason": "rollback ingested copy"}}
                    fsync_append_jsonl(c.agent_root / "rollback.jsonl", inverse)
                    done.append(out)
                elif op["type"] == "organize_copy":
                    out = self.execute_organize_copy(op)
                    inverse = {**rec_base, "inverse": {"type": "archive_move", "src": out["path"], "reason": "rollback organized copy"}}
                    fsync_append_jsonl(c.agent_root / "rollback.jsonl", inverse)
                    done.append(out)
                fsync_append_jsonl(c.agent_root / "operations.jsonl", {**rec_base, "status": "done", "apply": True})
            except Exception as exc:
                fsync_append_jsonl(c.agent_root / "operations.jsonl", {**rec_base, "status": "failed", "error": str(exc), "apply": True})
                self.set_status(f"manifest failed: {exc}", idx / max(1, len(ops)), False, str(exc))
                raise
            finally:
                self.set_status(f"executed {idx+1}/{len(ops)}", (idx + 1) / max(1, len(ops)), True)
        last_render = None
        for item in done:
            if isinstance(item, dict) and item.get("type") == "render_mashup" and item.get("path"):
                last_render = item.get("path")
        with self.status_lock:
            if last_render:
                self.status["last_render_path"] = last_render
        self.set_status("manifest executed", 1, False)
        return {"ok": True, "dry_run": False, "manifest_id": manifest.get("manifest_id"), "done": done, "plan": plan}

    def rollback_outputs(self, manifest_id: str = "", limit: int = 0, apply: bool = False) -> Dict[str, Any]:
        c = self.ensure_config()
        log_path = c.agent_root / "rollback.jsonl"
        if not log_path.exists():
            return {"ok": True, "dry_run": not apply, "plan": [], "done": [], "skipped": ["rollback log not found"]}
        records: List[Dict[str, Any]] = []
        for line in log_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
            except Exception:
                continue
            if manifest_id and str(rec.get("manifest_id") or "") != manifest_id:
                continue
            inv = rec.get("inverse") or {}
            if inv.get("type") != "archive_move" or not inv.get("src"):
                continue
            records.append(rec)
        records = list(reversed(records))
        if limit and limit > 0:
            records = records[:limit]
        archive_root = (c.agent_root / "archive" / "rollback").resolve()
        archive_root.mkdir(parents=True, exist_ok=True)
        seen: set[str] = set()
        plan: List[Dict[str, Any]] = []
        done: List[Dict[str, Any]] = []
        skipped: List[Dict[str, Any]] = []
        for rec in records:
            inv = rec.get("inverse") or {}
            src = self.validate_rollback_src(Path(inv["src"]))
            src_key = os.path.normcase(str(src))
            if src_key in seen:
                skipped.append({"src": str(src), "reason": "duplicate rollback source"})
                continue
            seen.add(src_key)
            stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
            archive_dst = archive_root / f"{stamp}-{ulidish()[:8]}-{safe_name(src.name, 'artifact')}"
            item = {
                "src": str(src),
                "archive_dst": str(archive_dst),
                "manifest_id": rec.get("manifest_id"),
                "op_id": rec.get("op_id"),
                "type": rec.get("type"),
                "reason": inv.get("reason") or "rollback output",
                "exists": src.exists(),
            }
            sidecars: List[Dict[str, str]] = []
            if src.suffix.lower() == ".wav":
                report = src.with_suffix(".render_report.json")
                if report.exists():
                    sidecars.append({"src": str(report), "archive_dst": str(archive_dst.with_suffix(".render_report.json"))})
            if sidecars:
                item["sidecars"] = sidecars
            plan.append(item)
            if not apply:
                continue
            if not src.exists():
                skipped.append({**item, "reason": "source already absent"})
                continue
            archive_dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(archive_dst))
            moved = {**item, "archive_dst": str(archive_dst), "moved": True}
            moved_sidecars = []
            for sc in sidecars:
                sc_src = Path(sc["src"]).resolve()
                sc_dst = Path(sc["archive_dst"]).resolve()
                if sc_src.exists():
                    shutil.move(str(sc_src), str(sc_dst))
                    moved_sidecars.append({"src": str(sc_src), "archive_dst": str(sc_dst), "moved": True})
            if moved_sidecars:
                moved["sidecars"] = moved_sidecars
            done.append(moved)
            fsync_append_jsonl(c.agent_root / "rollback_applied.jsonl", {"ulid": ulidish(), "ts": now_utc(), "source_record": rec, "archive_dst": str(archive_dst), "sidecars": moved_sidecars})
        return {"ok": True, "dry_run": not apply, "apply_required": not apply, "planned": len(plan), "moved": len(done), "plan": plan, "done": done, "skipped": skipped}

    def execute_create_playlist(self, op: Dict[str, Any]) -> Dict[str, Any]:
        c = self.ensure_config()
        name = safe_name(op["args"].get("name") or "playlist") + ".m3u8"
        dst = (c.playlists_root / name).resolve()
        self.validate_path_in_root(dst, c.playlists_root)
        self.validate_not_master(dst)
        rows = []
        for fid in op["args"].get("entries") or []:
            r = self.conn().execute("SELECT path FROM files WHERE id=?", (fid,)).fetchone()
            if r:
                try:
                    rows.append(os.path.relpath(r["path"], c.playlists_root))
                except ValueError:
                    # Windows cannot relativize between drives. Absolute paths are valid in M3U8.
                    rows.append(str(Path(r["path"]).resolve()))
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text("#EXTM3U\n" + "\n".join(rows) + "\n", encoding="utf-8")
        return {"type": "create_playlist", "path": str(dst), "entries": len(rows)}

    def render_mashup(self, mashup_id: str, dst: Path) -> Dict[str, Any]:
        c = self.ensure_config()
        db = self.conn()
        row = db.execute("SELECT * FROM mashups WHERE id=?", (mashup_id,)).fetchone()
        if not row:
            raise RuntimeError("mashup not found")
        arrangement = json.loads(row["arrangement_json"])
        arr_sha = arrangement_sha(arrangement)
        bpm = float(arrangement["bpm"])
        sr = c.sample_rate
        sections = list(arrangement.get("sections") or [])
        loop_ids = sorted({layer["loop_id"] for sec in sections for layer in sec.get("layers", [])})
        loops: Dict[str, Dict[str, Any]] = {}
        for lid in loop_ids:
            r = db.execute("""SELECT l.*, f.path, ft.bpm FROM loops l JOIN files f ON f.id=l.file_id LEFT JOIN features ft ON ft.file_id=f.id WHERE l.id=?""", (lid,)).fetchone()
            if r:
                loops[lid] = dict(r)
        total_bars = 0
        for sec in sections:
            total_bars = max(total_bars, int(sec["bar_start"]) + int(sec["bars"]))
        total_len = int(math.ceil(total_bars * 4 * 60.0 / bpm * sr))
        mix = np.zeros(total_len, dtype=np.float32)
        audio_cache: Dict[str, np.ndarray] = {}
        transform_cache: Dict[str, np.ndarray] = {}
        transform_cache_dir = c.agent_root / "cache" / "transforms" / ENGINE_VERSION
        transform_cache_dir.mkdir(parents=True, exist_ok=True)
        max_tail_decks = max(1, min(6, int((arrangement.get("params") or {}).get("max_aux_decks") or 3)))
        report: Dict[str, Any] = {"engine_version": ENGINE_VERSION, "arrangement_sha": arr_sha, "seed": arrangement.get("seed"), "bpm": bpm, "render_timestamp": now_utc(), "dj_compiler": arrangement.get("dj_compiler") or {}, "world_model": arrangement.get("world_model") or {}, "tastespec": arrangement.get("tastespec") or profile_summary(str((arrangement.get("params") or {}).get("taste_profile") or "girl_talk_v1")), "candidate_search": arrangement.get("candidate_search") or {}, "deck_model": {"version": "v0.5.17", "model": "varispeed_lattice_dry_multideck_tail_overlay", "max_aux_decks": max_tail_decks, "rule": "incoming downbeat stays on grid; only dry, role-approved outgoing decks overhang into the transition window"}, "transform_cache": {"hits": 0, "misses": 0, "disk_hits": 0}, "quality_gate": {}, "layers": [], "transitions": [], "drops": [], "drop_count": 0}

        def transition_xfade_samples(sec_obj: Dict[str, Any], sec_len_samples: int) -> int:
            transition = dict(sec_obj.get("transition_in") or {})
            xfade_beats = float(transition.get("xfade_beats") or 0)
            typ = str(transition.get("type") or "")
            if typ in ("", "start", "impact_drop", "hard_cut_to_air", "hard_cut_pickup", "bed_ride"):
                return 0
            n = int(round(xfade_beats * 60.0 / bpm * sr))
            return max(0, min(n, max(0, sec_len_samples // 2)))

        def blend_decks(out_seg: np.ndarray, in_seg: np.ndarray, transition: Dict[str, Any], xfade_len: int) -> np.ndarray:
            n = min(int(xfade_len), out_seg.size, in_seg.size)
            if n <= 0:
                return in_seg[:0].astype(np.float32)
            out_seg = out_seg[:n].astype(np.float32, copy=False)
            in_seg = in_seg[:n].astype(np.float32, copy=False)
            curve = str(transition.get("curve") or "equal_power")
            bass_policy = str(transition.get("bass_policy") or "one_low_owner")
            cutoff = float(transition.get("low_cutoff_hz") or 170.0)
            if bass_policy in ("one_low_owner", "strip_low_end"):
                blended = dj_bass_swap_blend(out_seg, in_seg, sr, cutoff, curve)
                if bass_policy == "strip_low_end":
                    lo, hi = fft_low_high_split(blended, sr, cutoff)
                    blended = hi + lo * 0.25
            else:
                inc, outc = dj_fade_curves(n, curve)
                blended = out_seg * outc + in_seg * inc
            peak = float(np.max(np.abs(blended))) if blended.size else 0.0
            if peak > 0.96:
                blended = blended * (0.96 / peak)
            return blended.astype(np.float32)

        def select_tail_decks(transition: Dict[str, Any], tails: List[Dict[str, Any]], xfade_len: int) -> List[Dict[str, Any]]:
            """Dry-deck tail budget. Multideck is available, but the default mix is not a cave."""
            if xfade_len <= 32:
                return []
            alive = [d for d in tails if d.get("audio") is not None and int(d.get("samples") or 0) >= xfade_len]
            typ = str(transition.get("type") or "")
            wanted: List[str]
            limit: int
            if typ == "bass_swap":
                wanted, limit = ["low", "rhythm"], 2
            elif typ == "hook_blend_over_bed":
                wanted, limit = ["rhythm", "low"], 2
            elif typ == "acapella_bridge":
                wanted, limit = ["voice", "texture"], 2
            elif typ == "beatmatch_blend":
                wanted, limit = ["rhythm", "low", "texture"], 3
            else:
                wanted, limit = ["rhythm", "low"], 2
            chosen: List[Dict[str, Any]] = []
            for group in wanted:
                for d in alive:
                    if d in chosen:
                        continue
                    if str(d.get("deck_group") or d.get("deck") or "") == group:
                        chosen.append(d)
                        break
                if len(chosen) >= limit:
                    break
            if not chosen and alive:
                chosen = alive[:1]
            return chosen[:min(limit, max_tail_decks)]

        def transformed_loop_clip(lid: str, raw_clip: np.ndarray, info: Dict[str, Any], ps: float, target_loop_len: int, stretch_pct: float, rate: float, role_name: str) -> np.ndarray:
            veto = drydeck_transform_violation(role_name, float(ps), float(stretch_pct))
            if veto:
                raise RuntimeError(veto)
            cache_key = sha256_text(json_dumps({"loop_id": lid, "start_s": info.get("start_s"), "end_s": info.get("end_s"), "target_len": target_loop_len, "pitch_shift": round(float(ps), 4), "role": role_name, "sr": sr, "engine": ENGINE_VERSION}))
            if cache_key in transform_cache:
                report["transform_cache"]["hits"] += 1
                return transform_cache[cache_key].copy()
            cache_path = transform_cache_dir / f"{cache_key}.npy"
            if cache_path.exists():
                try:
                    arr = np.load(cache_path, allow_pickle=False).astype(np.float32)
                    if arr.size == target_loop_len:
                        transform_cache[cache_key] = arr
                        report["transform_cache"]["hits"] += 1
                        report["transform_cache"]["disk_hits"] += 1
                        return arr.copy()
                except Exception:
                    pass
            report["transform_cache"]["misses"] += 1
            clip2 = raw_clip.astype(np.float32, copy=True)
            dry_mode = str((arrangement.get("params") or {}).get("quality_mode") or "") in {"dry_deck", "stable_deck"}
            if dry_mode:
                # Fast dry DJ transform: prefer deterministic varispeed/resample for the
                # tiny corrections allowed by the budgets. This is faster and avoids the
                # watery cave artifacts caused by phase-vocoder stretching.
                clip2 = resample_or_fit(clip2, target_loop_len).astype(np.float32)
                report.setdefault("transform_policy", "varispeed_first_resample_then_small_residual_pitch")
            elif abs(float(rate) - 1.0) <= 0.015:
                clip2 = resample_or_fit(clip2, target_loop_len).astype(np.float32)
            else:
                try:
                    clip2 = librosa.effects.time_stretch(clip2, rate=rate).astype(np.float32)
                except Exception as exc:
                    raise RuntimeError(f"time_stretch failed: {exc}")
            diff = abs(clip2.size - target_loop_len) / max(1, target_loop_len)
            if diff > 0.005:
                raise RuntimeError(f"post-stretch length mismatch {diff:.4f}")
            if clip2.size != target_loop_len:
                clip2 = resample_or_fit(clip2, target_loop_len)
            if abs(float(ps)) > 1e-4:
                try:
                    clip2 = librosa.effects.pitch_shift(clip2, sr=sr, n_steps=float(ps)).astype(np.float32)
                except Exception as exc:
                    raise RuntimeError(f"pitch_shift failed: {exc}")
                diff = abs(clip2.size - target_loop_len) / max(1, target_loop_len)
                if diff > 0.005:
                    raise RuntimeError(f"post-pitch length mismatch {diff:.4f}")
                if clip2.size != target_loop_len:
                    clip2 = resample_or_fit(clip2, target_loop_len)
            try:
                np.save(cache_path, clip2.astype(np.float32), allow_pickle=False)
            except Exception:
                pass
            transform_cache[cache_key] = clip2.astype(np.float32)
            return clip2.astype(np.float32, copy=True)

        def render_section_deck(sidx: int, sec: Dict[str, Any], tail_len: int) -> Tuple[np.ndarray, List[Dict[str, Any]]]:
            sec_len = int(round(int(sec["bars"]) * 4 * 60.0 / bpm * sr))
            deck_len = sec_len + max(0, int(tail_len))
            vocal_present = any(layer.get("role") == "vocal" for layer in sec.get("layers", []))
            section_has_bass = any(layer.get("role") == "bass" for layer in sec.get("layers", []))
            section_deck = np.zeros(deck_len, dtype=np.float32)
            tail_parts: Dict[str, np.ndarray] = {}
            for layer in sec.get("layers", []):
                lid = layer.get("loop_id")
                info = loops.get(lid)
                drop_base = {"section_index": sidx, "loop_id": lid, "role": layer.get("role")}
                if not info:
                    report["drops"].append({**drop_base, "reason": "loop metadata missing"}); continue
                try:
                    path = info["path"]
                    if path not in audio_cache:
                        audio_cache[path] = decode_audio(Path(path), sr)
                    source = audio_cache[path]
                    a = max(0, int(float(info["start_s"]) * sr))
                    b = min(source.size, int(float(info["end_s"]) * sr))
                    clip = source[a:b].astype(np.float32, copy=True)
                    if clip.size < 256:
                        report["drops"].append({**drop_base, "reason": "clip too short"}); continue
                    source_loop_bars = max(1, int(info["bars"] or 1))
                    target_loop_len = max(512, int(round(source_loop_bars * 4 * 60.0 / bpm * sr)))
                    source_bpm = float(info.get("bpm") or bpm)
                    stretch_pct = float(layer.get("varispeed_pct", abs(source_bpm - bpm) / max(1e-9, bpm) * 100.0) or 0.0)
                    stretch_budget = float((arrangement.get("params") or {}).get("stretch_budget") or 12.0)
                    rate = clip.size / target_loop_len
                    ps = float(layer.get("residual_pitch_shift", layer.get("pitch_shift") or 0.0) or 0.0)
                    role_name = str(layer.get("role") or info.get("role") or "full")
                    veto = drydeck_transform_violation(role_name, ps, stretch_pct)
                    if veto:
                        report["drops"].append({**drop_base, "reason": veto, "stretch_pct": stretch_pct, "pitch_shift": round(float(ps), 4)}); continue
                    try:
                        clip = transformed_loop_clip(str(lid), clip, info, ps, target_loop_len, stretch_pct, rate, role_name)
                    except Exception as exc:
                        report["drops"].append({**drop_base, "reason": str(exc)}); continue

                    layer_bar_offset = max(0, int(layer.get("bar_offset") or 0))
                    requested_bar_len = layer.get("bar_len")
                    active_bars = int(sec["bars"]) if requested_bar_len is None else max(1, int(requested_bar_len))
                    if layer_bar_offset >= int(sec["bars"]):
                        report["drops"].append({**drop_base, "reason": "bar_offset outside section"}); continue
                    active_bars = min(active_bars, int(sec["bars"]) - layer_bar_offset)
                    active_start = int(round(layer_bar_offset * 4 * 60.0 / bpm * sr))
                    active_len = max(512, int(round(active_bars * 4 * 60.0 / bpm * sr)))
                    reaches_section_end = active_start + active_len >= sec_len - 8
                    tail_participates = bool(tail_len > 32 and reaches_section_end)
                    render_len = active_len + (tail_len if tail_participates else 0)
                    clip = tile_with_crossfade(clip, render_len, sr)
                    clip = simple_fft_filter(clip, sr, role_name, vocal_present, section_has_bass)
                    clip = normalize_layer_rms(clip, role_name)
                    clip = tame_short_overlay(clip, sr, role_name, active_bars)

                    fade_in = True; fade_out = not tail_participates
                    if active_start == 0 and sidx > 0:
                        prev = sections[sidx - 1]
                        for pl in prev.get("layers", []):
                            if pl.get("loop_id") == layer.get("loop_id"):
                                poff = max(0, int(pl.get("bar_offset") or 0)); plen = int(pl.get("bar_len") or prev.get("bars") or 1)
                                if poff + plen >= int(prev.get("bars") or 1):
                                    fade_in = False; break
                    if (not tail_participates) and active_start + active_len >= sec_len - 8 and sidx + 1 < len(sections):
                        nxt = sections[sidx + 1]
                        for nl in nxt.get("layers", []):
                            if nl.get("loop_id") == layer.get("loop_id") and int(nl.get("bar_offset") or 0) == 0:
                                fade_out = False; break
                    clip = apply_edge_fades(clip, sr, fade_in=fade_in, fade_out=fade_out, fade_ms=14)
                    layer_gain_db = cap_overlay_gain_db(float(layer.get("gain_db", -8.0)), role_name, active_bars)
                    gain = 10 ** (layer_gain_db / 20.0)
                    active_end = min(deck_len, active_start + clip.size)
                    if active_end > active_start:
                        rendered = clip[: active_end - active_start] * gain
                        section_deck[active_start:active_end] += rendered
                        if tail_participates:
                            tail_start = max(0, sec_len - active_start)
                            tail_audio = clip[tail_start:tail_start + tail_len] * gain
                            if tail_audio.size > 32:
                                group = deck_group_for_role(role_name)
                                if group not in tail_parts:
                                    tail_parts[group] = np.zeros(tail_len, dtype=np.float32)
                                n_tail = min(tail_len, tail_audio.size)
                                tail_parts[group][:n_tail] += tail_audio[:n_tail].astype(np.float32)
                        report["layers"].append({**drop_base, "stretch_rate": rate, "stretch_pct": stretch_pct, "pitch_shift": round(float(ps), 4), "gain_db": layer_gain_db, "bar_offset": layer_bar_offset, "bar_len": active_bars, "deck": f"deck_{sidx % 4}", "deck_group": deck_group_for_role(role_name), "world": layer.get("world"), "source_track_key": layer.get("source_track_key"), "dry_high3000_share": layer.get("dry_high3000_share"), "dry_quality_score": layer.get("dry_quality_score"), "tail_participates": tail_participates, "transform_mode": layer.get("transform_mode"), "speed_ratio": layer.get("speed_ratio"), "varispeed_pct": layer.get("varispeed_pct"), "natural_pitch_shift": layer.get("natural_pitch_shift"), "desired_key_shift": layer.get("desired_key_shift"), "residual_pitch_shift": layer.get("residual_pitch_shift"), "artifact_risk": layer.get("artifact_risk")})
                except Exception as exc:
                    report["drops"].append({**drop_base, "reason": f"unexpected render error: {exc}"})
                    continue
            tail_decks_out = []
            for group, audio in tail_parts.items():
                if audio.size > 32 and float(np.max(np.abs(audio))) > 1e-7:
                    tail_decks_out.append({"deck_group": group, "audio": audio.astype(np.float32), "samples": int(audio.size)})
            if not tail_decks_out and tail_len > 32:
                summed_tail = section_deck[sec_len:sec_len + tail_len].astype(np.float32)
                if summed_tail.size > 32 and float(np.max(np.abs(summed_tail))) > 1e-7:
                    tail_decks_out.append({"deck_group": "mixed", "audio": summed_tail, "samples": int(summed_tail.size)})
            return section_deck[:sec_len].astype(np.float32), tail_decks_out

        tail_decks: List[Dict[str, Any]] = []
        for sidx, sec in enumerate(sections):
            sec_start = int(round(int(sec["bar_start"]) * 4 * 60.0 / bpm * sr))
            sec_len = int(round(int(sec["bars"]) * 4 * 60.0 / bpm * sr))
            next_tail_len = 0
            if sidx + 1 < len(sections):
                next_sec_len = int(round(int(sections[sidx + 1]["bars"]) * 4 * 60.0 / bpm * sr))
                next_tail_len = transition_xfade_samples(sections[sidx + 1], next_sec_len)
            section_mix, outgoing_tails = render_section_deck(sidx, sec, next_tail_len)
            transition = dict(sec.get("transition_in") or {})
            xfade_len = transition_xfade_samples(sec, sec_len)
            xfade_len = min(xfade_len, section_mix.size)
            usable_tails = select_tail_decks(transition, tail_decks, xfade_len)
            applied_transition = {**transition, "section_index": sidx, "xfade_samples": int(xfade_len), "applied": False, "deck_model": "varispeed_lattice_dry_multideck_tail_overlay", "overlap_side": "tail", "incoming_downbeat_error_ms": None, "outgoing_energy_zero_before_boundary": None, "tail_deck_count": len(usable_tails)}
            if xfade_len > 32 and usable_tails:
                outgoing = np.zeros(xfade_len, dtype=np.float32)
                for deck in usable_tails:
                    outgoing[:xfade_len] += deck["audio"][:xfade_len]
                in_seg = section_mix[:xfade_len].copy()
                blended = blend_decks(outgoing, in_seg, transition, xfade_len)
                end_blend = min(total_len, sec_start + blended.size)
                if end_blend > sec_start:
                    mix[sec_start:end_blend] += blended[: end_blend - sec_start]
                rest = section_mix[xfade_len:]
                rest_start = sec_start + xfade_len
                rest_end = min(total_len, rest_start + rest.size)
                if rest_end > rest_start:
                    mix[rest_start:rest_end] += rest[: rest_end - rest_start]
                # Measure, do not assert. In the overlay model the incoming section audio
                # is placed with sample index 0 at sec_start (the phrase-grid downbeat), and
                # the outgoing tail overhangs BEFORE it. The intended downbeat sample is
                # sec_start; the actual placed sample is sec_start too, so the true error is
                # the clamp difference (nonzero only if sec_start was pushed by total_len).
                intended_downbeat = int(round(int(sec["bar_start"]) * 4 * 60.0 / bpm * sr))
                actual_downbeat = sec_start
                downbeat_err_ms = round(abs(actual_downbeat - intended_downbeat) / sr * 1000.0, 3)
                # Measure whether the outgoing tail actually reached zero energy by the boundary.
                boundary_tail = outgoing[-min(64, outgoing.size):] if outgoing.size else np.zeros(1, dtype=np.float32)
                outgoing_zero = bool(float(np.max(np.abs(boundary_tail))) < 1e-4)
                applied_transition.update({"applied": True, "tail_deck_count": len(usable_tails), "incoming_downbeat_error_ms": downbeat_err_ms, "transition_window_start_sample": sec_start, "transition_window_end_sample": end_blend, "source_tail_sections": [int(d.get("section_index")) for d in usable_tails], "source_tail_decks": [str(d.get("deck_group") or d.get("deck")) for d in usable_tails], "outgoing_energy_zero_before_boundary": outgoing_zero})
                tail_decks = []
            else:
                end = min(total_len, sec_start + sec_len)
                if end > sec_start:
                    mix[sec_start:end] += section_mix[: end - sec_start]
                if str(transition.get("type") or "") == "hard_cut_to_air":
                    applied_transition["outgoing_energy_zero_before_boundary"] = True
            if outgoing_tails and next_tail_len > 32:
                for td in outgoing_tails:
                    tail_decks.append({"section_index": sidx, "deck": f"deck_{sidx % 4}", "deck_group": td.get("deck_group"), "audio": td.get("audio").astype(np.float32), "samples": int(td.get("samples") or 0), "starts_at_sample": int(sec_start + sec_len)})
                tail_decks = tail_decks[-max_tail_decks:]
            else:
                if next_tail_len <= 32:
                    tail_decks = []
            report["transitions"].append(applied_transition)
            self.set_status(f"rendering section {sidx+1}/{len(sections)}", (sidx + 1) / max(1, len(sections)), True)
        # One final full-mix limiter/trim only. Do not flatten section dynamics.
        if str((arrangement.get("params") or {}).get("quality_mode") or "") in {"dry_deck", "stable_deck"}:
            mix = stable_presence_restore(mix, sr)
        mix = integrated_lufs_normalize(mix, sr, -14.0)
        target_seconds = float((arrangement.get("params") or {}).get("target_seconds") or (total_bars * 4 * 60.0 / bpm))
        report["quality_gate"] = drydeck_quality_gate(drydeck_metrics(mix, sr), target_seconds)
        dst = dst.resolve()
        self.validate_path_in_root(dst, c.working_root / "renders")
        self.validate_not_master(dst)
        dst.parent.mkdir(parents=True, exist_ok=True)
        sf.write(str(dst), mix, sr, subtype="PCM_24")
        report["drop_count"] = len(report["drops"])
        report_path = dst.with_suffix(".render_report.json")
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        write_wav_info_chunk(dst, {"engine_version": ENGINE_VERSION, "arrangement_sha": arr_sha, "seed": arrangement.get("seed"), "params_sha": sha256_text(json_dumps(arrangement.get("params") or {})), "analyzer_version": ANALYZER_VERSION, "render_timestamp": report["render_timestamp"]})
        if bool((arrangement.get("params") or {}).get("post_render_gate", True)) and target_seconds >= 60 and not report.get("quality_gate", {}).get("passed", True):
            # Durable rule: keep receipts, quarantine the artifact, and return a structured rejection
            # so the one-click path can automatically try another plan without loading bad audio.
            reject_dir = (c.agent_root / "rejected_renders" / ENGINE_VERSION).resolve()
            reject_dir.mkdir(parents=True, exist_ok=True)
            q_dst = reject_dir / dst.name
            q_report = reject_dir / report_path.name
            try:
                shutil.move(str(dst), str(q_dst))
            except Exception:
                q_dst = dst
            try:
                shutil.move(str(report_path), str(q_report))
            except Exception:
                q_report = report_path
            db.execute("UPDATE mashups SET render_path=NULL, engine_version=?, arrangement_sha=?, render_report_path=? WHERE id=?", (ENGINE_VERSION, arr_sha, str(q_report), mashup_id))
            db.commit()
            return {"type": "render_rejected", "path": str(q_dst), "report": str(q_report), "quality_gate": report.get("quality_gate"), "drop_count": report["drop_count"], "engine_version": ENGINE_VERSION, "arrangement_sha": arr_sha, "seconds": round(mix.size / sr, 3), "sections": len(sections), "layers": sum(len(s.get("layers", [])) for s in sections), "presented": False}
        db.execute("UPDATE mashups SET render_path=?, engine_version=?, arrangement_sha=?, render_report_path=? WHERE id=?", (str(dst), ENGINE_VERSION, arr_sha, str(report_path), mashup_id))
        db.commit()
        return {"type": "render_mashup", "path": str(dst), "report": str(report_path), "drop_count": report["drop_count"], "engine_version": ENGINE_VERSION, "arrangement_sha": arr_sha, "seconds": round(mix.size / sr, 3), "sections": len(sections), "layers": sum(len(s.get("layers", [])) for s in sections), "presented": True}


    def propose_plan(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Compose an arrangement for the timeline surface WITHOUT writing a
        manifest, mashup row, or WAV. Pure planning: same composer, same
        judgments, same receipts — a plan you can look at, save, and only then
        decide to render."""
        taste_profile = str(params.get("taste_profile") or "girl_talk_v1")
        pool = self.approved_atom_pool(taste_profile)
        if not pool:
            return {"ok": False, "error": "ear crate is empty — run Analyze, Extract Loops, and Ear Crate first"}
        p = {"taste_profile": taste_profile,
             "target_seconds": float(params.get("target_seconds") or 120),
             "bpm": float(params.get("bpm") or 0.0),
             "stretch_budget": float(params.get("stretch_budget") or 8.0),
             "pitch_shift_budget": int(params.get("pitch_shift_budget") or 2)}
        seed = int(params.get("seed") or 0) or self.next_render_seed(self.ensure_config().seed)
        try:
            arrangement = self.compose_taste_arrangement(pool, p, seed)
        except RuntimeError as exc:
            return {"ok": False, "error": str(exc)}
        score = self.score_arrangement(arrangement)
        gate = self.taste_arrangement_gate(arrangement)
        return {"ok": True, "arrangement": arrangement, "score": score, "taste_gate": gate, "seed": seed}

    def list_plans(self) -> Dict[str, Any]:
        rows = self.conn().execute("SELECT id,name,taste_profile,plan_hash,created_at FROM saved_plans ORDER BY created_at DESC LIMIT 100").fetchall()
        return {"ok": True, "items": [dict(r) for r in rows]}

    def residents(self) -> Dict[str, Any]:
        """One card per persona: live readiness, endless receipt, and what the
        crate is missing — the honest 'can this resident play tonight' answer."""
        items = []
        for pid in sorted(TASTE_PROFILES):
            flat = TASTE_PROFILES[pid]
            entry: Dict[str, Any] = {
                "id": pid, "name": flat.get("name") or pid, "contract": flat.get("contract") or "",
                "version": flat.get("tastespec_version"), "hash": flat.get("tastespec_hash"),
                "density": {"sources_per_minute": flat.get("sources_per_minute"),
                            "min_layers": flat.get("min_layers"), "max_layers": flat.get("max_layers")},
            }
            try:
                r = self.taste_readiness(pid, 120.0)
                have, need = r.get("have") or {}, r.get("need") or {}
                ratios = [min(1.0, float(have.get(k, 0)) / max(1, float(need[k]))) for k in need] or [0.0]
                entry["readiness_pct"] = int(round(100 * sum(ratios) / len(ratios)))
                entry["ready"] = bool(r.get("ready"))
                entry["endless"] = r.get("endless")
                if int(r.get("pool_size") or 0) == 0:
                    entry["wants"] = ["hasn't auditioned your library yet \u2014 Book a set ear-crates it automatically (first time on a big library takes a while; watch the bottom bar for the count + ETA)"]
                    entry["never_auditioned"] = True
                else:
                    entry["wants"] = list(r.get("failures") or [])
            except Exception as exc:
                entry["error"] = str(exc)[:200]
            items.append(entry)
        return {"ok": True, "items": items}

    def sessions_list(self) -> Dict[str, Any]:
        """Every render AND every refusal, with receipts. A refusal is a session
        too — it tells you what to feed the crate."""
        c = self.ensure_config()
        items: List[Dict[str, Any]] = []
        for x in (self.list_renders().get("items") or []):
            passed = x.get("quality_gate_passed")
            items.append({"kind": "render", "name": x.get("name"), "path": x.get("path"),
                          "mtime": x.get("mtime"), "passed": (passed is not False),
                          "meta": f"{(x.get('size_bytes') or 0)/1048576:.1f} MB · {x.get('engine_version') or '?'}"
                                  + ("" if x.get("current_engine") else " · old engine"),
                          "receipts": "quality gate " + ("passed" if passed else ("FAILED" if passed is False else "unrecorded"))})
        rej_root = c.agent_root / "rejected_renders"
        if rej_root.exists():
            reports = sorted(rej_root.rglob("*.render_report.json"), key=lambda f: f.stat().st_mtime, reverse=True)[:20]
            for f in reports:
                why = "refused by post-render gate"
                try:
                    rep = json.loads(f.read_text(encoding="utf-8"))
                    gate = rep.get("quality_gate") or {}
                    fails = gate.get("failures") or [k for k, v in gate.items() if v is False]
                    if fails:
                        why = "refused: " + "; ".join(str(x) for x in fails[:4])
                except Exception:
                    pass
                items.append({"kind": "refusal", "name": f.stem.replace(".render_report", ""),
                              "path": str(f), "passed": False,
                              "mtime": _dt.datetime.fromtimestamp(f.stat().st_mtime).isoformat(timespec="seconds"),
                              "meta": "quarantined render", "receipts": why})
        items.sort(key=lambda x: str(x.get("mtime") or ""), reverse=True)
        return {"ok": True, "items": items[:60]}

    def station_feedback(self, signal: str) -> Dict[str, Any]:
        """Crowd controls with real consequences: 🔥/🧊 nudge the NEXT compile's
        intent (persisted bias, clamped), ⏭ is logged. Every press is a taste
        receipt in a durable journal — steering, not theater."""
        signal = str(signal or "").lower()
        if signal not in {"fire", "ice", "skip"}:
            return {"ok": False, "error": "signal must be fire, ice, or skip"}
        c = self.ensure_config()
        db = self.conn()
        row = db.execute("SELECT value FROM kv WHERE key='station_bias'").fetchone()
        bias = json.loads(row["value"]) if row else {"chaos": 0, "vocal_density": 0, "drama": 0, "receipts": 0}
        if signal == "fire":
            bias["chaos"] = max(-20, min(20, bias.get("chaos", 0) + 5))
            bias["vocal_density"] = max(-20, min(20, bias.get("vocal_density", 0) + 5))
        elif signal == "ice":
            bias["chaos"] = max(-20, min(20, bias.get("chaos", 0) - 6))
            bias["drama"] = max(-20, min(20, bias.get("drama", 0) - 5))
        bias["receipts"] = int(bias.get("receipts", 0)) + 1
        db.execute("INSERT OR REPLACE INTO kv(key,value) VALUES('station_bias',?)", (json.dumps(bias),))
        db.commit()
        fsync_append_jsonl(c.agent_root / "journals" / "station_feedback.jsonl",
                           {"at": now_utc(), "signal": signal, "bias": bias})
        return {"ok": True, "receipt": bias["receipts"], "bias": bias,
                "applies": "next compile" if signal != "skip" else "logged only"}

    def station_bias(self) -> Dict[str, int]:
        try:
            row = self.conn().execute("SELECT value FROM kv WHERE key='station_bias'").fetchone()
            return json.loads(row["value"]) if row else {}
        except Exception:
            return {}

    def taste_profile_receipt(self, taste_profile: str = "girl_talk_v1") -> Dict[str, Any]:
        return profile_summary(taste_profile)

    def set_atom_judgment(self, atom_id: str, taste_profile: str, status: str, relabel_role: str = "", favorite: bool = False, locked: bool = False, reason: str = "") -> Dict[str, Any]:
        if status not in {"approved", "rejected", "candidate"}:
            raise ValueError("atom judgment status must be approved, rejected, or candidate")
        db = self.conn()
        row = db.execute("SELECT id FROM ear_atoms WHERE id=? AND taste_profile=?", (atom_id, taste_profile)).fetchone()
        if not row:
            raise ValueError("atom not found for TasteSpec profile")
        db.execute("""INSERT INTO atom_judgments(atom_id,taste_profile,status,relabel_role,favorite,locked,reason,updated_at)
                      VALUES(?,?,?,?,?,?,?,?)
                      ON CONFLICT(atom_id,taste_profile) DO UPDATE SET status=excluded.status,relabel_role=excluded.relabel_role,favorite=excluded.favorite,locked=excluded.locked,reason=excluded.reason,updated_at=excluded.updated_at""",
                   (atom_id, taste_profile, status, relabel_role or None, 1 if favorite else 0, 1 if locked else 0, reason, now_utc()))
        db.execute("UPDATE ear_atoms SET status=?, ear_role=COALESCE(NULLIF(?,''), ear_role) WHERE id=? AND taste_profile=?", (status, relabel_role, atom_id, taste_profile))
        db.commit()
        return {"ok": True, "atom_id": atom_id, "taste_profile": taste_profile, "status": status}

    def compatible_pairs_for_atom(self, atom_id: str, taste_profile: str = "girl_talk_v1", limit: int = 40) -> Dict[str, Any]:
        rows = self.conn().execute("""SELECT e.*, pj.status judgment_status, pj.reason judgment_reason,
                   la.ear_role left_role, ra.ear_role right_role, rf.path right_path, rt.artist right_artist, rt.title right_title
                 FROM compatibility_edges e
                 JOIN ear_atoms la ON la.id=e.left_atom_id JOIN ear_atoms ra ON ra.id=e.right_atom_id
                 JOIN files rf ON rf.id=ra.file_id LEFT JOIN tracks rt ON rt.file_id=rf.id
                 LEFT JOIN pair_judgments pj ON pj.edge_id=e.id AND pj.taste_profile=e.taste_profile
                 WHERE e.taste_profile=? AND (e.left_atom_id=? OR e.right_atom_id=?)
                 ORDER BY e.score DESC LIMIT ?""", (taste_profile, atom_id, atom_id, limit)).fetchall()
        items=[]
        for r in rows:
            d=dict(r); d["reasons"] = json.loads(d.pop("reasons_json") or "{}"); items.append(d)
        return {"ok": True, "taste_profile": taste_profile, "atom_id": atom_id, "items": items}

    def set_pair_judgment(self, edge_id: str, taste_profile: str, status: str, reason: str = "") -> Dict[str, Any]:
        if status not in {"approved", "rejected", "candidate"}:
            raise ValueError("pair judgment status must be approved, rejected, or candidate")
        row = self.conn().execute("SELECT id FROM compatibility_edges WHERE id=? AND taste_profile=?", (edge_id, taste_profile)).fetchone()
        if not row:
            raise ValueError("compatibility edge not found for TasteSpec profile")
        self.conn().execute("""INSERT INTO pair_judgments(edge_id,taste_profile,status,reason,updated_at) VALUES(?,?,?,?,?)
                             ON CONFLICT(edge_id,taste_profile) DO UPDATE SET status=excluded.status,reason=excluded.reason,updated_at=excluded.updated_at""", (edge_id, taste_profile, status, reason, now_utc()))
        self.conn().commit()
        return {"ok": True, "edge_id": edge_id, "taste_profile": taste_profile, "status": status}

    def save_plan(self, name: str, plan: Dict[str, Any], taste_profile: str = "girl_talk_v1") -> Dict[str, Any]:
        prof = load_tastespec(taste_profile)
        plan = dict(plan)
        plan["tastespec"] = {"id": prof["id"], "version": prof["version"], "hash": prof["hash"]}
        ph = arrangement_sha(plan)
        self.conn().execute("INSERT OR REPLACE INTO saved_plans(id,name,taste_profile,plan_hash,plan_json,created_at) VALUES(COALESCE((SELECT id FROM saved_plans WHERE plan_hash=?),?),?,?,?,?,?)", (ph, ulidish(), name, taste_profile, ph, json.dumps(plan, ensure_ascii=False), now_utc()))
        self.conn().commit()
        out_dir = self.ensure_config().working_root / "plans"; out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / f"{safe_name(name)}-{ph[:8]}.plan.json"; path.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
        return {"ok": True, "plan_hash": ph, "path": str(path), "tastespec": plan["tastespec"]}

    def load_plan(self, plan_hash: str) -> Dict[str, Any]:
        row = self.conn().execute("SELECT * FROM saved_plans WHERE plan_hash=?", (plan_hash,)).fetchone()
        if not row: raise ValueError("saved plan not found")
        return {"ok": True, "plan": json.loads(row["plan_json"]), "name": row["name"], "taste_profile": row["taste_profile"], "plan_hash": row["plan_hash"]}

    def judge_render(self, render_path: str, ref_path: Optional[str] = None) -> Dict[str, Any]:
        render_metrics = judge_audio_file(Path(render_path), ref_path=Path(ref_path) if ref_path else None)
        return render_metrics

    def run_background(self, fn, *args, **kwargs) -> Dict[str, Any]:
        with self.status_lock:
            if self.status.get("busy"):
                raise RuntimeError("EarCrate is already busy")
            self.status.update({"busy": True, "message": "starting", "progress": 0, "last_error": None})
        def target():
            try:
                fn(*args, **kwargs)
            except Exception as exc:
                traceback.print_exc()
                self.set_status(f"error: {exc}", busy=False, error=str(exc))
        threading.Thread(target=target, daemon=True).start()
        return {"ok": True, "started": True}




# --- librarian attachment (rebuild plan: orchestrator stays thin; features live in modules) ---
from earcrate.librarian.ingest import ingest_sources, organize_and_retag, execute_ingest_copy, execute_organize_copy
EarcrateCore.ingest_sources = ingest_sources
EarcrateCore.organize_and_retag = organize_and_retag
from earcrate.librarian.ingest import reorganize_source, rollback_reorganize, _prune_empty_dirs
EarcrateCore.reorganize_source = reorganize_source
EarcrateCore.rollback_reorganize = rollback_reorganize
EarcrateCore._prune_empty_dirs = _prune_empty_dirs
EarcrateCore.execute_ingest_copy = execute_ingest_copy
EarcrateCore.execute_organize_copy = execute_organize_copy
