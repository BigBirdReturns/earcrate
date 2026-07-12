from earcrate.core.deps import *
"""EARCRATE v3 §3 — provider registry.

Core reaches capability THROUGH a seam, never around it. A *kind* names a
capability (``stems``, ``retriever``, ``embedding``, ``vector_index``,
``artifacts``); each kind has one or more registered *factories* keyed by name,
exactly one of which is the DEFAULT. ``get(kind)`` with no name hands back a
freshly constructed instance of the DEFAULT, so callers that don't care which
implementation they get still go through the seam. Pure python — no torch, no
network, no heavy deps at import time."""

# kind -> {name: factory(callable -> instance)}
_REGISTRY: Dict[str, Dict[str, Any]] = {}
# kind -> default name
_DEFAULTS: Dict[str, str] = {}


def register(kind: str, name: str, factory: Any, default: bool = False) -> None:
    """Register ``factory`` (a zero-arg callable, typically a class) under
    ``(kind, name)``. The first registration for a kind becomes the default;
    pass ``default=True`` to force it."""
    if not callable(factory):
        raise TypeError("provider factory must be callable")
    kinds = _REGISTRY.setdefault(kind, {})
    kinds[name] = factory
    if default or kind not in _DEFAULTS:
        _DEFAULTS[kind] = name


def get(kind: str, name: Optional[str] = None) -> Any:
    """Return a fresh instance of the named provider, or the DEFAULT when
    ``name`` is None. Raises KeyError for an unknown kind/name — a seam with no
    registered default is a bug, not a silent None."""
    kinds = _REGISTRY.get(kind)
    if not kinds:
        raise KeyError("no providers registered for kind %r" % (kind,))
    if name is None:
        name = _DEFAULTS.get(kind)
    if name is None or name not in kinds:
        raise KeyError("no provider %r for kind %r" % (name, kind))
    return kinds[name]()


def registered(kind: str) -> List[str]:
    """Sorted names registered for a kind (empty list if none)."""
    return sorted(_REGISTRY.get(kind, {}).keys())


def default_name(kind: str) -> Optional[str]:
    """Name of the current default provider for a kind, or None."""
    return _DEFAULTS.get(kind)


# Re-export the concrete classes so callers can `from earcrate.providers import
# NoopStemProvider`. These lines are column-0 `from earcrate.` imports: the
# single-file builder STRIPS them, and the classes are already in the shared
# namespace by concatenation order (artifacts -> stems -> retrieval come after
# this file in build ORDER). In package mode they run here and, as a side
# effect, execute each module's module-level register() calls.
from earcrate.providers.artifacts import ArtifactStore
from earcrate.providers.stems import StemProvider, NoopStemProvider, DemucsStemProvider
from earcrate.providers.retrieval import CandidateRetriever, FullScanRetriever, EmbeddingProvider, NoopEmbeddingProvider, VectorIndex, LinearScanIndex
