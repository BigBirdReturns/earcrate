from earcrate.core.deps import *
from earcrate.core.util import sha256_text
from earcrate.providers import register
from earcrate.providers.artifacts import ArtifactStore
"""EARCRATE v3 §5.2 — the StemProvider seam.

Stem separation is a capability core reaches through a seam, never around one.
The DEFAULT is a NO-OP: it reports stems unavailable and never crashes, so a box
with no GPU / no torch behaves correctly (just without stems) instead of
throwing. The Demucs implementation GUARDS its heavy imports: importing this
module never touches torch, and constructing a DemucsStemProvider is free; only
CALLING ``separate`` on a box without torch+demucs raises a clear, actionable
error. Real separations are materialized into an L3 ArtifactStore with
provenance (pcm_sha, \"demucs\", model_version) and a retention tier — evictable,
never a source of truth. Core is NEVER source-of-truth for stems."""

from abc import ABC, abstractmethod

DEFAULT_ROLES = ("vocals", "drums", "bass", "other")

# Loading htdemucs + moving it to the GPU costs seconds. Reloading it on EVERY
# separate() call left the GPU mostly idle between per-file loads (the ~30%-util
# symptom): a single render needs ~40 separations = ~40 model loads. Cache the
# resident model per (model_version, device) so it loads ONCE and every later
# separation reuses it. Lock-guarded because the HTTP server is threaded.
_MODEL_CACHE: Dict[str, Any] = {}
_MODEL_LOCK = threading.Lock()


def stem_capability() -> Dict[str, bool]:
    """HONEST capability probe for the stem path. Reports whether the heavy deps
    are importable and whether CUDA is actually usable. On the shipped default box
    (no torch, no demucs) every flag is False and ``ready`` is False — the feature
    is OFF and UNVERIFIED until a real GPU box reports ``ready`` True AND a Demucs
    run has been receipted. This function NEVER raises and NEVER claims readiness
    it cannot prove."""
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
    ``available=False`` with a ``reason`` rather than raising — unavailability
    is a normal state, not an error."""

    name = "abstract"

    @abstractmethod
    def separate(self, pcm_sha: str, audio_path: str,
                 roles: Optional[Any] = None) -> Dict[str, Any]:
        raise NotImplementedError

    def has_stems(self, pcm_sha: str, roles: Optional[Any] = None) -> bool:
        """Whether the requested stems are ALREADY materialized in the cache,
        WITHOUT running (or triggering) a separation. The background warmer and
        the warm-status probe use this to skip already-cached sources and report
        progress without touching the GPU. Default False: a provider that cannot
        separate has nothing cached."""
        return False


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
    """Torch/Demucs-backed separation. GUARDED: the heavy imports live inside
    ``separate`` so importing this module and constructing the provider never
    require torch. Calling ``separate`` without torch+demucs raises a clear
    RuntimeError (never a bare ImportError). Output is materialized to L3 with
    provenance and a retention tier."""

    name = "demucs"

    def __init__(self, store: Optional[ArtifactStore] = None,
                 model_version: str = "htdemucs",
                 tier: str = "ephemeral"):
        self.store = store if store is not None else ArtifactStore()
        self.model_version = str(model_version)
        self.tier = tier

    def _effective_model_version(self) -> str:
        """The model actually used for separation. EARCRATE_DEMUCS_MODEL lets a box
        opt into a lighter/faster released model to cut the cold, separation-bound
        render; unset it returns ``self.model_version`` (htdemucs) so nothing changes.
        The L3 artifact key, the stored provenance version and the resident-model
        cache all resolve through THIS, so a swapped model gets its own cache entries
        and honest provenance instead of colliding with / mislabeling htdemucs."""
        return os.environ.get("EARCRATE_DEMUCS_MODEL") or self.model_version

    def separate(self, pcm_sha: str, audio_path: str,
                 roles: Optional[Any] = None) -> Dict[str, Any]:
        role_list = list(roles) if roles else list(DEFAULT_ROLES)
        model_version = self._effective_model_version()
        keys = {role: self._artifact_key(str(pcm_sha), role) for role in role_list}
        # --- CACHE-BEFORE-SEPARATE ---------------------------------------
        # A separation is expensive (GPU) and content-addressed by pcm_sha. If
        # every requested role is ALREADY materialized in the shared L3 store we
        # return those artifacts WITHOUT touching torch or running Demucs — a
        # cache hit needs no GPU. This is what makes a produced stem reusable
        # across renders instead of recomputed every time.
        if role_list and all(self.store.get(k) is not None for k in keys.values()):
            return {
                "available": True,
                "provider": "demucs",
                "pcm_sha": str(pcm_sha),
                "model_version": model_version,
                "tier": self.tier,
                "evictable": True,
                "cached": True,
                "stems": dict(keys),
            }
        # --- MISS: run the real separation -------------------------------
        # _run_demucs GUARDS the torch/demucs import and raises a clear
        # RuntimeError (never a bare ImportError) on a box without them. On a real
        # CUDA box it returns {role: wav_bytes}; each produced stem is then
        # written to L3 with provenance so it can be traced and evicted. This
        # branch never runs end-to-end in the CI/no-torch environment.
        produced = self._run_demucs(str(audio_path), role_list)  # -> {role: bytes}
        stems: Dict[str, str] = {}
        for role in role_list:
            wav_bytes = produced.get(role)
            if wav_bytes is None:
                continue
            key = keys[role]
            self.store.put(
                key, wav_bytes, tier=self.tier,
                source_identity=str(pcm_sha), provider="demucs",
                version=model_version,
                extra={"role": role, "audio_path": str(audio_path)},
            )
            stems[role] = key
        return {
            "available": True,
            "provider": "demucs",
            "pcm_sha": str(pcm_sha),
            "model_version": model_version,
            "tier": self.tier,
            "evictable": True,
            "cached": False,
            "stems": stems,
        }

    def has_stems(self, pcm_sha: str, roles: Optional[Any] = None) -> bool:
        """True iff EVERY requested role is already materialized in L3 for this
        pcm_sha under the effective model — a pure cache lookup, no torch, no GPU,
        no separation. This is exactly the CACHE-BEFORE-SEPARATE predicate, lifted
        out so the warmer can ask 'is this source already warm?' cheaply."""
        role_list = list(roles) if roles else list(DEFAULT_ROLES)
        if not role_list:
            return False
        return all(self.store.get(self._artifact_key(str(pcm_sha), r)) is not None
                   for r in role_list)

    def _artifact_key(self, pcm_sha: str, role: str) -> str:
        # Deterministic L3 key: same sound + model + role -> same artifact.
        return "stem_" + sha256_text("|".join(
            [str(pcm_sha), "demucs", self._effective_model_version(), str(role)]))

    def _run_demucs(self, audio_path: str, roles: List[str]) -> Dict[str, bytes]:  # pragma: no cover
        """REAL Demucs invocation. GUARDED: the torch/demucs imports live here so
        importing this module and hitting the cache path never require torch. On a
        box without them this raises a CLEAR RuntimeError naming torch+demucs — a
        bare ImportError must never leak. Returns {role: wav_bytes} (16-bit PCM
        WAV) for the requested roles.

        UNVERIFIED: this body has NOT been executed anywhere in this milestone —
        there is no torch, no demucs and no CUDA device in this environment. It is
        the real-hardware contract, pending a 4060 GPU receipt. Do NOT read its
        presence as proof the GPU separation works."""
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
        # MODEL-SELECTION knob: the COLD render is separation-bound (~6s/miss), and
        # htdemucs is the heaviest of the released models. EARCRATE_DEMUCS_MODEL lets
        # a box opt into a LIGHTER/faster model (e.g. "htdemucs_ft" quality vs a
        # cheaper "mdx_extra_q") WITHOUT touching the default: unset -> self.model_version
        # (htdemucs), byte-for-byte today's behavior. The resident-model cache and the
        # L3 artifact key / provenance version (see _effective_model_version) both key
        # off the ACTUAL model name, so a swapped model never silently reuses another
        # model's weights or mislabels an artifact.
        model_name = self._effective_model_version()
        cache_key = "%s|%s" % (model_name, device)
        with _MODEL_LOCK:
            model = _MODEL_CACHE.get(cache_key)
            if model is None:
                model = get_model(model_name)
                model.to(device)
                model.eval()
                _MODEL_CACHE[cache_key] = model  # resident: reused across every separation
        wav = AudioFile(str(audio_path)).read(
            streams=0, samplerate=model.samplerate, channels=model.audio_channels)
        ref = wav.mean(0)
        wav = (wav - ref.mean()) / (ref.std() + 1e-8)
        with torch.no_grad():
            sources = apply_model(model, wav[None].to(device), device=device)[0]
        sources = sources * ref.std() + ref.mean()
        names = list(getattr(model, "sources", []))
        out: Dict[str, bytes] = {}

        def _emit(arr) -> bytes:
            buf = _io.BytesIO()
            _sf.write(buf, arr.astype(_np.float32), int(model.samplerate), format="WAV")
            return buf.getvalue()

        def _vocals():
            # (samples, channels) for the isolated vocal source.
            return sources[names.index("vocals")].cpu().numpy().T

        def _instrumental():
            # "no_vocals" is the INSTRUMENTAL tape track: the sum of every non-vocal
            # source (drums+bass+other) — the clean bed a foreign acapella rides over,
            # equivalent to demucs --two-stems=vocals.
            idxs = [i for i, n in enumerate(names) if n != "vocals"]
            if not idxs:
                return None
            inst = sources[idxs[0]]
            for i in idxs[1:]:
                inst = inst + sources[i]
            return inst.cpu().numpy().T

        # TWO-STEMS FAST PATH: when the renderer only asks for the vocal and/or the
        # instrumental (the roles the mashup layers actually consume), behave like
        # demucs --two-stems=vocals — emit ONLY those two stems instead of decoding
        # and WAV-encoding all four sources then discarding drums/bass/other. Both
        # come from the one separation and are cached independently in L3. Byte-
        # identical to the 4-stem path for these roles; it just skips the wasted
        # per-stem encode work on the cold miss.
        role_set = set(roles)
        if role_set and role_set <= {"vocals", "no_vocals"}:
            if "vocals" in role_set and "vocals" in names:
                out["vocals"] = _emit(_vocals())
            if "no_vocals" in role_set:
                inst = _instrumental()
                if inst is not None:
                    out["no_vocals"] = _emit(inst)
            return out

        # FULL 4-stem path: emit every requested individual source, plus the summed
        # instrumental when "no_vocals" is also requested alongside real stems.
        for role in roles:
            if role not in names:
                continue
            arr = sources[names.index(role)].cpu().numpy().T  # (samples, channels)
            out[role] = _emit(arr)
        if "no_vocals" in roles:
            inst = _instrumental()
            if inst is not None:
                out["no_vocals"] = _emit(inst)
        return out


register("stems", "noop", NoopStemProvider, default=True)
register("stems", "demucs", DemucsStemProvider)
