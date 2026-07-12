from earcrate.core.deps import *
from earcrate.providers import register
"""EARCRATE v3 §5.4 — retrieval seams.

Three seams, each with a pure, CORRECT default:

  CandidateRetriever  -> FullScanRetriever      (scans the whole catalog)
  EmbeddingProvider   -> NoopEmbeddingProvider  (returns None; never fabricates)
  VectorIndex         -> LinearScanIndex        (brute-force TRUE nearest)

The defaults are deliberately dumb-but-honest: a full scan can never miss a
candidate, a no-op embedder never invents a vector it can't compute, and a
linear scan returns the mathematically true nearest neighbour (not an
approximation). Smarter providers can register alongside without changing the
contract."""

from abc import ABC, abstractmethod


class CandidateRetriever(ABC):
    name = "abstract"

    @abstractmethod
    def retrieve(self, catalog: Any, query: Optional[Any] = None,
                 limit: Optional[int] = None) -> List[Any]:
        raise NotImplementedError


class FullScanRetriever(CandidateRetriever):
    """DEFAULT. Returns every item in the catalog (optionally capped by
    ``limit``). Cannot miss a candidate — the honest baseline."""

    name = "fullscan"

    def retrieve(self, catalog: Any, query: Optional[Any] = None,
                 limit: Optional[int] = None) -> List[Any]:
        items = list(catalog)
        if limit is not None:
            items = items[:int(limit)]
        return items


class EmbeddingProvider(ABC):
    name = "abstract"

    @abstractmethod
    def embed(self, item: Any) -> Optional[Any]:
        raise NotImplementedError


class NoopEmbeddingProvider(EmbeddingProvider):
    """DEFAULT. Returns None — signalling 'no embedding available' rather than
    fabricating a vector. Downstream code must treat None as 'fall back to the
    full scan', never as a zero vector."""

    name = "noop"

    def embed(self, item: Any) -> Optional[Any]:
        return None


class VectorIndex(ABC):
    name = "abstract"

    @abstractmethod
    def add(self, ident: Any, vector: Any) -> None:
        raise NotImplementedError

    @abstractmethod
    def query(self, vector: Any, k: int = 1, metric: str = "cosine") -> List[Any]:
        raise NotImplementedError


class LinearScanIndex(VectorIndex):
    """DEFAULT. Brute-force exact nearest neighbour over every stored vector.
    ``metric`` is 'cosine' (higher similarity = nearer) or 'l2' (smaller
    distance = nearer). Returns ``[(ident, score), ...]`` sorted nearest-first.
    No approximation, no index build — the true answer, always."""

    name = "linear"

    def __init__(self):
        self._ids: List[Any] = []
        self._vecs: List[Any] = []

    def add(self, ident: Any, vector: Any) -> None:
        v = np.asarray(vector, dtype=float).reshape(-1)
        self._ids.append(ident)
        self._vecs.append(v)

    def __len__(self) -> int:
        return len(self._ids)

    def query(self, vector: Any, k: int = 1, metric: str = "cosine") -> List[Any]:
        if not self._ids:
            return []
        q = np.asarray(vector, dtype=float).reshape(-1)
        scored: List[Any] = []
        if metric == "cosine":
            qn = float(np.linalg.norm(q)) or 1.0
            for ident, v in zip(self._ids, self._vecs):
                vn = float(np.linalg.norm(v)) or 1.0
                sim = float(np.dot(q, v) / (qn * vn))
                scored.append((ident, sim))
            # higher cosine = nearer
            scored.sort(key=lambda t: t[1], reverse=True)
        elif metric == "l2":
            for ident, v in zip(self._ids, self._vecs):
                dist = float(np.linalg.norm(q - v))
                scored.append((ident, dist))
            # smaller distance = nearer
            scored.sort(key=lambda t: t[1])
        else:
            raise ValueError("unknown metric %r (want 'cosine' or 'l2')" % (metric,))
        return scored[:max(1, int(k))]


register("retriever", "fullscan", FullScanRetriever, default=True)
register("embedding", "noop", NoopEmbeddingProvider, default=True)
register("vector_index", "linear", LinearScanIndex, default=True)
