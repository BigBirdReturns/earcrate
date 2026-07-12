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
                 model_version: str = "htdemucs_v4",
                 tier: str = "ephemeral"):
        self.store = store if store is not None else ArtifactStore()
        self.model_version = str(model_version)
        self.tier = tier

    def separate(self, pcm_sha: str, audio_path: str,
                 roles: Optional[Any] = None) -> Dict[str, Any]:
        role_list = list(roles) if roles else list(DEFAULT_ROLES)
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
                "model_version": self.model_version,
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
        # Deterministic L3 key: same sound + model + role -> same artifact.
        return "stem_" + sha256_text("|".join(
            [str(pcm_sha), "demucs", self.model_version, str(role)]))

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
            arr = sources[names.index(role)].cpu().numpy().T  # (samples, channels)
            buf = _io.BytesIO()
            _sf.write(buf, arr.astype(_np.float32), int(model.samplerate), format="WAV")
            out[role] = buf.getvalue()
        return out


register("stems", "noop", NoopStemProvider, default=True)
register("stems", "demucs", DemucsStemProvider)
