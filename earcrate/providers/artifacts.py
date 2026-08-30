from earcrate.core.deps import *
from earcrate.core.util import now_utc, json_dumps, visible_app_dir
from earcrate.providers import register
"""EARCRATE v3 §5.3 (L3) — the ArtifactStore.

L3 is derived, reconstructible, EVICTABLE cache: stem separations, rendered
mixdowns, anything expensive to compute but never a source of truth. This is a
local tiered directory store. Every artifact carries provenance
(``source_identity``, ``provider``, ``version``) so a materialized blob can
always be traced back to what produced it, and lives in one of three retention
tiers:

    ephemeral  — first to go under pressure (scratch, single-session)
    warm       — kept while there is room (recent, likely-reused)
    pinned     — NEVER evicted by budget (user-important, contract-load-bearing)

``evict(budget)`` sheds bytes until the store fits ``budget``, dropping every
ephemeral artifact (oldest first) before touching a single warm one, and NEVER
removing a pinned artifact even if that leaves the store over budget. Core is
never source-of-truth for anything in here."""

TIERS = ("ephemeral", "warm", "pinned")
# eviction order: ephemeral first, then warm; pinned is never in this list.
_EVICT_ORDER = ("ephemeral", "warm")


class ArtifactStore:
    def __init__(self, root: Optional[Any] = None):
        if root is None:
            # A workspace that has been configured exports EARCRATE_L3_ROOT
            # (core.configure sets it to <agent_root>/cache/L3). Defaulting to it
            # makes BOTH the provider's store and the renderer's get("artifacts")
            # resolve to the SAME on-disk root, so a materialized stem key
            # actually resolves. With NO workspace configured we fall back to a
            # VISIBLE, app-adjacent cache dir (never a temp dir) so stems land
            # somewhere the user can actually find — EARCRATE_CACHE_ROOT overrides.
            env_root = os.environ.get("EARCRATE_L3_ROOT")
            if env_root:
                root = Path(env_root)
            else:
                cache_root = os.environ.get("EARCRATE_CACHE_ROOT")
                base = Path(cache_root).expanduser() if cache_root else (visible_app_dir() / "cache")
                root = base / "L3"
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _paths(self, key: str):
        safe = hashlib.sha256(str(key).encode("utf-8")).hexdigest()
        return self.root / (safe + ".bin"), self.root / (safe + ".meta.json")

    def put(self, key: str, data: bytes, tier: str = "warm",
            source_identity: str = "", provider: str = "", version: str = "",
            extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Materialize ``data`` under ``key`` in ``tier`` with provenance.
        Provenance (source_identity, provider, version) is mandatory shape — an
        artifact with no traceable origin is a bug, so blanks are stored but the
        keys always exist."""
        if tier not in TIERS:
            raise ValueError("unknown retention tier %r (want one of %r)" % (tier, TIERS))
        if not isinstance(data, (bytes, bytearray)):
            raise TypeError("artifact data must be bytes")
        data = bytes(data)
        bin_path, meta_path = self._paths(key)
        meta = {
            "key": str(key),
            "tier": tier,
            "bytes": len(data),
            "source_identity": str(source_identity),
            "provider": str(provider),
            "version": str(version),
            "created": now_utc(),
        }
        if extra:
            meta["extra"] = extra
        bin_path.write_bytes(data)
        meta_path.write_text(json_dumps(meta), encoding="utf-8")
        return dict(meta)

    def has(self, key: str) -> bool:
        """Existence check WITHOUT reading the blob. Callers used ``get(key) is
        not None`` to probe for cached stems — which read the entire ~48 MB WAV
        (and its meta) off disk per probe. A warm-status sweep over a big library
        did gigabytes of IO to answer a yes/no question."""
        bin_path, meta_path = self._paths(key)
        return bin_path.exists() and meta_path.exists()

    def get(self, key: str) -> Optional[Dict[str, Any]]:
        """Return ``{\"data\": bytes, \"meta\": {...}}`` or None if absent."""
        bin_path, meta_path = self._paths(key)
        if not bin_path.exists() or not meta_path.exists():
            return None
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        return {"data": bin_path.read_bytes(), "meta": meta}

    def _entries(self) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for meta_path in self.root.glob("*.meta.json"):
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
            except Exception:
                continue
            bin_path = meta_path.with_name(meta_path.name[:-len(".meta.json")] + ".bin")
            meta["_bin"] = bin_path
            meta["_meta"] = meta_path
            out.append(meta)
        return out

    def total_bytes(self) -> int:
        return sum(int(e.get("bytes", 0)) for e in self._entries())

    def _drop(self, entry: Dict[str, Any]) -> None:
        for p in (entry.get("_bin"), entry.get("_meta")):
            try:
                if p is not None and Path(p).exists():
                    Path(p).unlink()
            except Exception:
                pass

    def evict(self, budget: int) -> List[str]:
        """Shed artifacts until total bytes <= ``budget``. Evicts ALL ephemeral
        (oldest first) before ANY warm, and NEVER a pinned artifact. Returns the
        keys evicted, in the order dropped. If pinned bytes alone exceed the
        budget the store stays over budget — pinned is inviolable."""
        evicted: List[str] = []
        entries = self._entries()
        total = sum(int(e.get("bytes", 0)) for e in entries)
        if total <= budget:
            return evicted
        for tier in _EVICT_ORDER:
            tier_entries = [e for e in entries if e.get("tier") == tier]
            # oldest first (created asc); stable tiebreak on key for determinism.
            tier_entries.sort(key=lambda e: (str(e.get("created", "")), str(e.get("key", ""))))
            for e in tier_entries:
                if total <= budget:
                    return evicted
                self._drop(e)
                total -= int(e.get("bytes", 0))
                evicted.append(str(e.get("key", "")))
            if total <= budget:
                return evicted
        return evicted


register("artifacts", "local", ArtifactStore, default=True)
