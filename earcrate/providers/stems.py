from earcrate.core.deps import *
from earcrate.core.util import sha256_text
from earcrate.providers import register
from earcrate.providers.artifacts import ArtifactStore
"""EARCRATE v3 §5.2 — the StemProvider seam.

Stem separation is a capability core reaches through a seam, never around one.
The DEFAULT is a NO-OP: it reports stems unavailable and never crashes, so a box
with no GPU / no torch behaves correctly (just without stems) instead of
throwing. The in-process Demucs implementation is available for an EarCrate
interpreter that owns torch+demucs+CUDA. ``DemucsProcessStemProvider`` closes the
other legitimate deployment shape: a lean EarCrate environment delegates the
heavy, platform-specific model to a separately proven local Python interpreter.
Both providers materialize content-addressed L3 artifacts with provenance; L3 is
evictable and never source-of-truth.
"""

from abc import ABC, abstractmethod

DEFAULT_ROLES = ("vocals", "drums", "bass", "other")
_PROCESS_SETTINGS_SCHEMA = 1
_PROCESS_PROVIDER_ID = "demucs_process"
_PROCESS_BASE_ROLES = ("vocals", "drums", "bass", "other")
_PROCESS_DERIVED_ROLES = ("no_vocals",)
_PROCESS_PROBE_CODE = r'''
import importlib.metadata as md
import importlib.util
import json
import sys

out = {
    "python_executable": sys.executable,
    "python_version": sys.version.split()[0],
    "torch": False,
    "demucs": False,
    "cuda": False,
    "gpu_name": None,
    "torch_version": None,
    "demucs_version": None,
    "error": None,
}
try:
    import torch
    out["torch"] = True
    out["torch_version"] = str(getattr(torch, "__version__", ""))
    out["cuda"] = bool(torch.cuda.is_available())
    if out["cuda"]:
        out["gpu_name"] = str(torch.cuda.get_device_name(0))
except Exception as exc:
    out["error"] = "torch: %s" % (exc,)
try:
    out["demucs"] = bool(importlib.util.find_spec("demucs") and
                          importlib.util.find_spec("demucs.separate"))
    if out["demucs"]:
        try:
            out["demucs_version"] = md.version("demucs")
        except Exception:
            out["demucs_version"] = "unknown"
except Exception as exc:
    out["error"] = (out["error"] + "; " if out["error"] else "") + "demucs: %s" % (exc,)
out["ready"] = bool(out["torch"] and out["demucs"] and out["cuda"])
print(json.dumps(out, sort_keys=True))
raise SystemExit(0 if out["ready"] else 3)
'''


def _sha256_local_file(path: Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _clean_process_environment() -> Dict[str, str]:
    """Run the heavy interpreter outside EarCrate's active virtual environment.

    A child Python inherits ordinary machine state, CUDA visibility, model caches,
    PATH, and FFmpeg, but never EarCrate's PYTHONHOME/PYTHONPATH/VIRTUAL_ENV. Those
    three variables are exactly how a lean venv can contaminate an otherwise
    capable system interpreter and recreate the Noop-era environment split.
    """
    env = dict(os.environ)
    for name in ("PYTHONHOME", "PYTHONPATH", "VIRTUAL_ENV"):
        env.pop(name, None)
    env["PYTHONUTF8"] = "1"
    return env


def _default_process_runner(argv: List[str], *, env: Dict[str, str],
                            timeout: float):
    return subprocess.run(
        argv,
        check=False,
        capture_output=True,
        text=True,
        env=env,
        timeout=timeout,
    )


def probe_demucs_process_python(
    python_path: Any,
    expected_sha256: str = "",
    *,
    runner: Optional[Any] = None,
    timeout_seconds: float = 120.0,
) -> Dict[str, Any]:
    """Prove a local interpreter owns torch, Demucs, CUDA, and one GPU.

    The interpreter itself is content-bound before and after the probe. A probe
    receipt is capability evidence only; a real separation and cache hit are
    still required before a repair may select this provider.
    """
    path = Path(str(python_path)).expanduser().resolve()
    result: Dict[str, Any] = {
        "python_path": str(path),
        "python_sha256": None,
        "expected_python_sha256": str(expected_sha256 or ""),
        "ready": False,
    }
    if not path.is_file():
        result["error"] = "configured Python interpreter is not a regular file"
        return result
    if path.is_symlink():
        result["error"] = "configured Python interpreter may not be a symlink"
        return result
    try:
        before = _sha256_local_file(path)
    except OSError as exc:
        result["error"] = "could not hash configured Python interpreter: %s" % (exc,)
        return result
    result["python_sha256"] = before
    if expected_sha256 and before.lower() != str(expected_sha256).lower():
        result["error"] = "configured Python interpreter hash does not match its receipt"
        return result
    run = runner or _default_process_runner
    try:
        completed = run(
            [str(path), "-c", _PROCESS_PROBE_CODE],
            env=_clean_process_environment(),
            timeout=float(timeout_seconds),
        )
    except Exception as exc:
        result["error"] = "external Python capability probe failed: %s" % (exc,)
        return result
    raw = str(getattr(completed, "stdout", "") or "").strip().splitlines()
    try:
        payload = json.loads(raw[-1]) if raw else {}
    except Exception:
        payload = {}
    if isinstance(payload, dict):
        result.update(payload)
    result["probe_returncode"] = int(getattr(completed, "returncode", 1) or 0)
    if not payload:
        stderr = str(getattr(completed, "stderr", "") or "")
        result["error"] = "external Python probe returned no JSON: " + stderr[-800:]
    try:
        after = _sha256_local_file(path)
    except OSError as exc:
        result["error"] = "could not re-hash configured Python interpreter: %s" % (exc,)
        result["ready"] = False
        return result
    result["python_sha256_after"] = after
    if after != before:
        result["error"] = "configured Python interpreter changed during capability probe"
        result["ready"] = False
        return result
    result["ready"] = bool(
        result.get("probe_returncode") == 0
        and result.get("torch")
        and result.get("demucs")
        and result.get("cuda")
        and result.get("ready")
    )
    if not result["ready"] and not result.get("error"):
        result["error"] = "interpreter does not expose torch+demucs on CUDA"
    return result


def stem_capability() -> Dict[str, bool]:
    """HONEST in-process capability probe for the original Demucs provider.

    This deliberately remains an in-process fact. A separately configured
    ``demucs_process`` provider proves itself through its own interpreter-bound
    receipt and real separation; it is not laundered into this legacy boolean.
    """
    def _importable(mod: str) -> bool:
        try:
            __import__(mod)
            return True
        except Exception:
            return False
    torch_ok = _importable("torch")
    demucs_ok = _importable("demucs") and _importable("demucs.separate")
    cuda_ok = False
    if torch_ok:
        try:
            import torch  # noqa: F401
            cuda_ok = bool(torch.cuda.is_available())
        except Exception:
            cuda_ok = False
    return {
        "torch": bool(torch_ok),
        "demucs": bool(demucs_ok),
        "cuda": bool(cuda_ok),
        "ready": bool(torch_ok and demucs_ok and cuda_ok),
    }


class StemProvider(ABC):
    """Separate an audio file into role stems.

    ``separate(pcm_sha, audio_path, roles)`` returns a dict with at least
    ``available`` (bool), ``provider`` (str), ``pcm_sha`` (str) and ``stems``
    (role -> artifact-key or path). When a provider cannot run it returns
    ``available=False`` with a ``reason`` rather than silently yielding the mix.
    """

    name = "abstract"

    @abstractmethod
    def separate(self, pcm_sha: str, audio_path: str,
                 roles: Optional[Any] = None) -> Dict[str, Any]:
        raise NotImplementedError


class NoopStemProvider(StemProvider):
    """DEFAULT. Reports stems unavailable; touches no heavy deps; never crashes."""

    name = "noop"

    def separate(self, pcm_sha: str, audio_path: str,
                 roles: Optional[Any] = None) -> Dict[str, Any]:
        return {
            "available": False,
            "provider": "noop",
            "pcm_sha": str(pcm_sha),
            "stems": {},
            "reason": "stem separation is not configured on this box "
                      "(default NoopStemProvider); no stems produced",
        }


class DemucsStemProvider(StemProvider):
    """Torch/Demucs-backed separation inside EarCrate's own interpreter."""

    name = "demucs"

    def __init__(self, store: Optional[ArtifactStore] = None,
                 model_version: str = "htdemucs",
                 tier: str = "ephemeral"):
        self.store = store if store is not None else ArtifactStore()
        self.model_version = str(model_version)
        self.tier = tier

    def separate(self, pcm_sha: str, audio_path: str,
                 roles: Optional[Any] = None) -> Dict[str, Any]:
        role_list = list(roles) if roles else list(DEFAULT_ROLES)
        keys = {role: self._artifact_key(str(pcm_sha), role) for role in role_list}
        if role_list and all(self.store.get(k) is not None for k in keys.values()):
            return {
                "available": True,
                "provider": "demucs",
                "pcm_sha": str(pcm_sha),
                "model_version": self.model_version,
                "tier": self.tier,
                "evictable": True,
                "cached": True,
                "stems": dict(keys),
            }
        produced = self._run_demucs(str(audio_path), role_list)
        stems: Dict[str, str] = {}
        for role in role_list:
            wav_bytes = produced.get(role)
            if wav_bytes is None:
                continue
            key = keys[role]
            self.store.put(
                key, wav_bytes, tier=self.tier,
                source_identity=str(pcm_sha), provider="demucs",
                version=self.model_version,
                extra={"role": role, "audio_path": str(audio_path)},
            )
            stems[role] = key
        return {
            "available": True,
            "provider": "demucs",
            "pcm_sha": str(pcm_sha),
            "model_version": self.model_version,
            "tier": self.tier,
            "evictable": True,
            "cached": False,
            "stems": stems,
        }

    def _artifact_key(self, pcm_sha: str, role: str) -> str:
        return "stem_" + sha256_text("|".join(
            [str(pcm_sha), "demucs", self.model_version, str(role)]))

    def _run_demucs(self, audio_path: str, roles: List[str]) -> Dict[str, bytes]:  # pragma: no cover
        try:
            import io as _io
            import torch  # noqa: F401
            import demucs.separate  # noqa: F401
            from demucs.pretrained import get_model
            from demucs.apply import apply_model
            from demucs.audio import AudioFile
            import soundfile as _sf
            import numpy as _np
        except Exception as exc:
            raise RuntimeError(
                "Demucs stems need torch+demucs on a CUDA box; neither is "
                "importable here (%s). Install torch+demucs and run on GPU, or "
                "use the default NoopStemProvider." % (exc,)
            ) from None
        device = "cuda" if torch.cuda.is_available() else "cpu"
        model = get_model(self.model_version)
        model.to(device)
        model.eval()
        wav = AudioFile(str(audio_path)).read(
            streams=0, samplerate=model.samplerate, channels=model.audio_channels)
        ref = wav.mean(0)
        wav = (wav - ref.mean()) / (ref.std() + 1e-8)
        with torch.no_grad():
            sources = apply_model(model, wav[None].to(device), device=device)[0]
        sources = sources * ref.std() + ref.mean()
        names = list(getattr(model, "sources", []))
        out: Dict[str, bytes] = {}
        for role in roles:
            if role not in names:
                continue
            arr = sources[names.index(role)].cpu().numpy().T
            buf = _io.BytesIO()
            _sf.write(buf, arr.astype(_np.float32), int(model.samplerate), format="WAV")
            out[role] = buf.getvalue()
        return out


class DemucsProcessStemProvider(StemProvider):
    """Demucs in a separately proven local Python, never through the network.

    EarCrate's active interpreter remains lean. A private settings receipt under
    ``agent_root/stem_provider.json`` binds the heavy interpreter by SHA-256 and
    pins the Demucs recipe. Every call revalidates that interpreter and source
    container around the subprocess. Missing settings, a changed interpreter, a
    failed capability probe, an incomplete stem set, or malformed WAV output is a
    refusal. The source mix is never returned as a substitute.
    """

    name = _PROCESS_PROVIDER_ID

    def __init__(
        self,
        store: Optional[ArtifactStore] = None,
        settings_path: Optional[Any] = None,
        tier: str = "ephemeral",
        runner: Optional[Any] = None,
        probe_runner: Optional[Any] = None,
    ):
        self.store = store if store is not None else ArtifactStore()
        self._settings_path_override = Path(settings_path).expanduser().resolve() if settings_path else None
        self.tier = str(tier)
        self._runner = runner or _default_process_runner
        self._probe_runner = probe_runner or self._runner
        self._settings_cache: Optional[Dict[str, Any]] = None
        self._probe_cache: Optional[Dict[str, Any]] = None

    def _settings_path(self) -> Path:
        if self._settings_path_override is not None:
            return self._settings_path_override
        explicit = str(os.environ.get("EARCRATE_DEMUCS_PROCESS_CONFIG") or "").strip()
        if explicit:
            return Path(explicit).expanduser().resolve()
        l3 = str(os.environ.get("EARCRATE_L3_ROOT") or "").strip()
        if not l3:
            raise RuntimeError(
                "demucs_process requires a configured EarCrate workspace "
                "(EARCRATE_L3_ROOT is absent)"
            )
        l3_path = Path(l3).expanduser().resolve()
        if len(l3_path.parents) < 2:
            raise RuntimeError("EARCRATE_L3_ROOT cannot resolve an agent root")
        agent_root = l3_path.parent.parent
        path = (agent_root / "stem_provider.json").resolve()
        try:
            path.relative_to(agent_root)
        except ValueError:
            raise RuntimeError("stem-provider settings escaped the EarCrate agent root")
        return path

    def _settings(self) -> Dict[str, Any]:
        if self._settings_cache is not None:
            return dict(self._settings_cache)
        path = self._settings_path()
        if not path.is_file():
            raise RuntimeError(
                "demucs_process is selected but its private stem_provider.json receipt is missing"
            )
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise RuntimeError("could not read stem-provider settings: %s" % (exc,))
        if not isinstance(value, dict):
            raise RuntimeError("stem-provider settings must be a JSON object")
        if int(value.get("schema_version") or 0) != _PROCESS_SETTINGS_SCHEMA:
            raise RuntimeError("unsupported stem-provider settings schema")
        if str(value.get("provider_id") or "") != _PROCESS_PROVIDER_ID:
            raise RuntimeError("stem-provider settings name the wrong provider")
        python_path = Path(str(value.get("python_path") or "")).expanduser().resolve()
        python_sha = str(value.get("python_sha256") or "").lower()
        if not python_sha or not re.fullmatch(r"[0-9a-f]{64}", python_sha):
            raise RuntimeError("stem-provider settings carry no valid interpreter SHA-256")
        if not python_path.is_file() or python_path.is_symlink():
            raise RuntimeError("stem-provider interpreter is missing, not a file, or a symlink")
        actual = _sha256_local_file(python_path)
        if actual.lower() != python_sha:
            raise RuntimeError("stem-provider interpreter changed since its receipt")
        model = str(value.get("model") or "htdemucs")
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", model):
            raise RuntimeError("stem-provider model name contains unsafe characters")
        recipe = {
            "model": model,
            "device": "cuda",
            "shifts": int(value.get("shifts") if value.get("shifts") is not None else 0),
            "overlap": float(value.get("overlap") if value.get("overlap") is not None else 0.10),
            "segment_seconds": float(value.get("segment_seconds") if value.get("segment_seconds") is not None else 6.0),
        }
        if recipe["shifts"] < 0 or recipe["shifts"] > 8:
            raise RuntimeError("stem-provider shifts are outside the supported range")
        if not 0.0 <= recipe["overlap"] < 1.0:
            raise RuntimeError("stem-provider overlap is outside [0, 1)")
        if not 1.0 <= recipe["segment_seconds"] <= 7.8:
            raise RuntimeError("stem-provider segment_seconds is outside [1.0, 7.8]")
        identity_body = {
            "provider_id": _PROCESS_PROVIDER_ID,
            "python_sha256": python_sha,
            "python_version": value.get("python_version"),
            "torch_version": value.get("torch_version"),
            "demucs_version": value.get("demucs_version"),
            "gpu_name": value.get("gpu_name"),
            "recipe": recipe,
        }
        normalized = dict(value)
        normalized.update({
            "python_path": str(python_path),
            "python_sha256": python_sha,
            "recipe": recipe,
            "settings_identity": sha256_text(json.dumps(
                identity_body, sort_keys=True, separators=(",", ":"), ensure_ascii=False
            )),
            "settings_path": str(path),
        })
        self._settings_cache = normalized
        return dict(normalized)

    def capability(self) -> Dict[str, Any]:
        if self._probe_cache is not None:
            return dict(self._probe_cache)
        settings = self._settings()
        result = probe_demucs_process_python(
            settings["python_path"],
            settings["python_sha256"],
            runner=self._probe_runner,
            timeout_seconds=float(settings.get("probe_timeout_seconds") or 120.0),
        )
        required_gpu = str(settings.get("gpu_name") or "").strip()
        if required_gpu and result.get("gpu_name") != required_gpu:
            result["ready"] = False
            result["error"] = "configured GPU identity changed since provider proof"
        for field in ("python_version", "torch_version", "demucs_version"):
            wanted = str(settings.get(field) or "").strip()
            if wanted and str(result.get(field) or "") != wanted:
                result["ready"] = False
                result["error"] = "%s changed since provider proof" % field
        result["provider"] = self.name
        result["settings_identity"] = settings["settings_identity"]
        self._probe_cache = result
        return dict(result)

    def _artifact_key(self, pcm_sha: str, role: str, settings: Dict[str, Any]) -> str:
        return "stem_" + sha256_text("|".join([
            str(pcm_sha), self.name, str(settings["settings_identity"]),
            str(settings["recipe"]["model"]), str(role),
        ]))

    def _artifact_exists(self, key: str) -> bool:
        has = getattr(self.store, "has", None)
        if callable(has):
            return bool(has(key))
        paths = getattr(self.store, "_paths", None)
        if callable(paths):
            binary, meta = paths(key)
            return Path(binary).is_file() and Path(meta).is_file()
        return self.store.get(key) is not None

    @staticmethod
    def _validate_wav_bytes(data: bytes, role: str) -> Dict[str, Any]:
        if not isinstance(data, (bytes, bytearray)) or len(data) <= 44:
            raise RuntimeError("Demucs produced an empty %s stem" % role)
        raw = bytes(data)
        if raw[:4] not in (b"RIFF", b"RF64") or raw[8:12] != b"WAVE":
            raise RuntimeError("Demucs produced a malformed %s WAV" % role)
        import io as _io
        try:
            info = sf.info(_io.BytesIO(raw))
        except Exception as exc:
            raise RuntimeError("Demucs %s WAV is not decodable: %s" % (role, exc))
        if int(info.frames or 0) <= 0 or int(info.samplerate or 0) <= 0:
            raise RuntimeError("Demucs %s WAV contains no audio frames" % role)
        return {
            "bytes": len(raw),
            "frames": int(info.frames),
            "sample_rate": int(info.samplerate),
            "channels": int(info.channels),
            "sha256": hashlib.sha256(raw).hexdigest(),
        }

    def _mix_no_vocals(self, refs: Dict[str, str], settings: Dict[str, Any],
                       pcm_sha: str) -> str:
        import io as _io
        parts = []
        sample_rate = None
        channels = None
        for role in ("drums", "bass", "other"):
            held = self.store.get(refs[role])
            if not held or not held.get("data"):
                raise RuntimeError("cannot derive no_vocals: %s artifact is absent" % role)
            audio, sr = sf.read(_io.BytesIO(held["data"]), dtype="float32", always_2d=True)
            if sample_rate is None:
                sample_rate = int(sr)
                channels = int(audio.shape[1])
            if int(sr) != sample_rate or int(audio.shape[1]) != channels:
                raise RuntimeError("cannot derive no_vocals from mismatched stem formats")
            parts.append(audio.astype(np.float32, copy=False))
        length = max(part.shape[0] for part in parts)
        mix = np.zeros((length, int(channels or 2)), dtype=np.float32)
        for part in parts:
            mix[:part.shape[0], :part.shape[1]] += part
        peak = float(np.max(np.abs(mix))) if mix.size else 0.0
        applied_gain = 1.0
        if peak > 0.999:
            applied_gain = 0.999 / peak
            mix *= applied_gain
        buffer = _io.BytesIO()
        sf.write(buffer, mix, int(sample_rate or 44100), format="WAV", subtype="PCM_24")
        data = buffer.getvalue()
        self._validate_wav_bytes(data, "no_vocals")
        key = self._artifact_key(pcm_sha, "no_vocals", settings)
        self.store.put(
            key,
            data,
            tier=self.tier,
            source_identity=str(pcm_sha),
            provider=self.name,
            version=str(settings["settings_identity"]),
            extra={
                "role": "no_vocals",
                "derived_from": [refs[r] for r in ("drums", "bass", "other")],
                "peak_safety_gain": applied_gain,
                "recipe": settings["recipe"],
            },
        )
        return key

    def separate(self, pcm_sha: str, audio_path: str,
                 roles: Optional[Any] = None) -> Dict[str, Any]:
        requested = list(roles) if roles else list(DEFAULT_ROLES)
        if not requested or not all(isinstance(role, str) and role for role in requested):
            return {
                "available": False, "provider": self.name, "pcm_sha": str(pcm_sha),
                "stems": {}, "reason": "requested stem roles are empty or malformed",
            }
        requested = list(dict.fromkeys(requested))
        allowed = set(_PROCESS_BASE_ROLES) | set(_PROCESS_DERIVED_ROLES)
        unknown = [role for role in requested if role not in allowed]
        if unknown:
            return {
                "available": False, "provider": self.name, "pcm_sha": str(pcm_sha),
                "stems": {}, "reason": "unsupported stem role(s): " + ", ".join(unknown),
            }
        source = Path(str(audio_path)).expanduser().resolve()
        if not source.is_file():
            return {
                "available": False, "provider": self.name, "pcm_sha": str(pcm_sha),
                "stems": {}, "reason": "source audio is missing or not a regular file",
            }
        try:
            settings = self._settings()
            capability = self.capability()
        except Exception as exc:
            return {
                "available": False, "provider": self.name, "pcm_sha": str(pcm_sha),
                "stems": {}, "reason": str(exc),
            }
        if not capability.get("ready"):
            return {
                "available": False, "provider": self.name, "pcm_sha": str(pcm_sha),
                "stems": {}, "reason": str(capability.get("error") or
                                             "external Demucs capability is not ready"),
                "capability": capability,
            }
        keys = {role: self._artifact_key(str(pcm_sha), role, settings)
                for role in _PROCESS_BASE_ROLES}
        requested_keys = dict(keys)
        requested_keys["no_vocals"] = self._artifact_key(str(pcm_sha), "no_vocals", settings)
        if ("no_vocals" in requested
                and not self._artifact_exists(requested_keys["no_vocals"])
                and all(self._artifact_exists(keys[role])
                        for role in ("drums", "bass", "other"))):
            requested_keys["no_vocals"] = self._mix_no_vocals(
                keys, settings, str(pcm_sha)
            )
        if all(self._artifact_exists(requested_keys[role]) for role in requested):
            return {
                "available": True, "provider": self.name, "pcm_sha": str(pcm_sha),
                "model_version": settings["recipe"]["model"],
                "settings_identity": settings["settings_identity"],
                "python_sha256": settings["python_sha256"],
                "tier": self.tier, "evictable": True, "cached": True,
                "stems": {role: requested_keys[role] for role in requested},
                "capability": capability,
            }
        source_before = _sha256_local_file(source)
        python_before = _sha256_local_file(Path(settings["python_path"]))
        if python_before != settings["python_sha256"]:
            return {
                "available": False, "provider": self.name, "pcm_sha": str(pcm_sha),
                "stems": {}, "reason": "external Python changed before separation",
            }
        work_root = Path(getattr(self.store, "root", tempfile.gettempdir())).resolve()
        work_root.mkdir(parents=True, exist_ok=True)
        work = Path(tempfile.mkdtemp(prefix="demucs-process-", dir=str(work_root)))
        output = work / "out"
        output.mkdir(parents=True, exist_ok=True)
        recipe = settings["recipe"]
        argv = [
            str(settings["python_path"]), "-m", "demucs.separate",
            "-n", str(recipe["model"]),
            "-d", "cuda",
            "--shifts", str(recipe["shifts"]),
            "--overlap", ("%.6f" % recipe["overlap"]),
            "--segment", ("%.6f" % recipe["segment_seconds"]),
            "-o", str(output),
            str(source),
        ]
        try:
            completed = self._runner(
                argv,
                env=_clean_process_environment(),
                timeout=float(settings.get("separation_timeout_seconds") or 7200.0),
            )
            if int(getattr(completed, "returncode", 1) or 0) != 0:
                stderr = str(getattr(completed, "stderr", "") or "")
                return {
                    "available": False, "provider": self.name, "pcm_sha": str(pcm_sha),
                    "stems": {}, "reason": "external Demucs failed: " + stderr[-1800:],
                    "command_recipe": recipe,
                }
            produced: Dict[str, bytes] = {}
            measurements: Dict[str, Dict[str, Any]] = {}
            for role in _PROCESS_BASE_ROLES:
                matches = [path for path in output.rglob(role + ".wav") if path.is_file()]
                if len(matches) != 1:
                    return {
                        "available": False, "provider": self.name, "pcm_sha": str(pcm_sha),
                        "stems": {}, "reason": "external Demucs produced %d %s WAVs; expected one" %
                                                (len(matches), role),
                    }
                data = matches[0].read_bytes()
                measurements[role] = self._validate_wav_bytes(data, role)
                produced[role] = data
            if _sha256_local_file(source) != source_before:
                return {
                    "available": False, "provider": self.name, "pcm_sha": str(pcm_sha),
                    "stems": {}, "reason": "source audio changed during separation",
                }
            if _sha256_local_file(Path(settings["python_path"])) != python_before:
                return {
                    "available": False, "provider": self.name, "pcm_sha": str(pcm_sha),
                    "stems": {}, "reason": "external Python changed during separation",
                }
            for role, data in produced.items():
                self.store.put(
                    keys[role],
                    data,
                    tier=self.tier,
                    source_identity=str(pcm_sha),
                    provider=self.name,
                    version=str(settings["settings_identity"]),
                    extra={
                        "role": role,
                        "source_file_sha256": source_before,
                        "python_sha256": settings["python_sha256"],
                        "python_version": capability.get("python_version"),
                        "torch_version": capability.get("torch_version"),
                        "demucs_version": capability.get("demucs_version"),
                        "gpu_name": capability.get("gpu_name"),
                        "recipe": recipe,
                        "wav": measurements[role],
                    },
                )
            if "no_vocals" in requested:
                requested_keys["no_vocals"] = self._mix_no_vocals(
                    keys, settings, str(pcm_sha)
                )
        except Exception as exc:
            return {
                "available": False, "provider": self.name, "pcm_sha": str(pcm_sha),
                "stems": {}, "reason": "external Demucs execution error: %s" % (exc,),
            }
        finally:
            shutil.rmtree(work, ignore_errors=True)
        missing = [role for role in requested if not self._artifact_exists(requested_keys[role])]
        if missing:
            return {
                "available": False, "provider": self.name, "pcm_sha": str(pcm_sha),
                "stems": {}, "reason": "provider failed to materialize: " + ", ".join(missing),
            }
        return {
            "available": True, "provider": self.name, "pcm_sha": str(pcm_sha),
            "model_version": recipe["model"],
            "settings_identity": settings["settings_identity"],
            "python_sha256": settings["python_sha256"],
            "tier": self.tier, "evictable": True, "cached": False,
            "stems": {role: requested_keys[role] for role in requested},
            "capability": capability,
        }


register("stems", "noop", NoopStemProvider, default=True)
register("stems", "demucs", DemucsStemProvider)
register("stems", _PROCESS_PROVIDER_ID, DemucsProcessStemProvider)
