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
        try:
            import torch  # noqa: F401
            import demucs  # noqa: F401
            import demucs.separate  # noqa: F401
        except Exception as exc:  # pragma: no cover - exercised only off a CUDA box
            raise RuntimeError(
                "Demucs stems need torch+demucs on a CUDA box; neither is "
                "importable here (%s). Install torch+demucs and run on GPU, or "
                "use the default NoopStemProvider." % (exc,)
            ) from None
        # --- hardware path (torch present) -------------------------------
        # Real separation runs Demucs, then each produced stem is written to L3
        # with provenance so it can be traced and evicted. This branch never
        # runs in the CI/no-torch environment; the materialization seam below is
        # the load-bearing contract we pin, keyed to make it deterministic.
        role_list = list(roles) if roles else list(DEFAULT_ROLES)
        produced = self._run_demucs(audio_path, role_list)  # -> {role: bytes}
        stems: Dict[str, str] = {}
        for role, wav_bytes in produced.items():
            key = self._artifact_key(pcm_sha, role)
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
            "stems": stems,
        }

    def _artifact_key(self, pcm_sha: str, role: str) -> str:
        # Deterministic L3 key: same sound + model + role -> same artifact.
        return "stem_" + sha256_text("|".join(
            [str(pcm_sha), "demucs", self.model_version, str(role)]))

    def _run_demucs(self, audio_path: str, roles: List[str]) -> Dict[str, bytes]:  # pragma: no cover
        # Placeholder for the actual Demucs invocation (needs torch+CUDA). Kept
        # separate so the materialization/provenance seam above is testable by
        # injecting a fake producer in a GPU harness.
        raise RuntimeError("Demucs runtime not available in this environment")


register("stems", "noop", NoopStemProvider, default=True)
register("stems", "demucs", DemucsStemProvider)
