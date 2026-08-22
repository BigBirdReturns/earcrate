"""Canonical musical-key identity for planning and transform feasibility.

Pitch class 0 is C. It is a valid value, not a missing value. Legacy planning
code used ``value or fallback`` in several transform paths, which silently
replaced C with the current deck key because ``0`` is false in Python. A private
Proof-005 acceptance run exposed the consequence: C sources were admitted into
exact-deck capacity at every key and then rejected later by the compatibility
scorer that read their key honestly.

This module installs one shared policy without changing source custody:

* ``key_root == 0`` always means C;
* only ``key_root is None`` inherits a declared fallback key;
* malformed key values fail closed;
* source-pool identity distinguishes C from an unknown key;
* ordinary composition receives in-memory copies whose C value remains 0 but is
  truthy only long enough to survive legacy ``or`` expressions.

The truthy-zero adapter is deliberately an implementation bridge. Serialized
and numeric identity remains the integer 0. It can be removed when the legacy
composer is split into smaller directly editable functions.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
import functools
import hashlib
import json
from typing import Any, Dict, List, Optional

KEY_IDENTITY_POLICY = "key_root_zero_is_C_null_only_fallback_v1"


class KeyIdentityError(ValueError):
    """A key value is missing without authority or cannot be interpreted."""


class _TruthyKeyZero(int):
    """Numeric/serialized zero that survives a legacy ``value or fallback``."""

    def __new__(cls) -> "_TruthyKeyZero":
        return int.__new__(cls, 0)

    def __bool__(self) -> bool:
        return True


_TRUTHY_KEY_ZERO = _TruthyKeyZero()


def canonical_key_root(value: Any, fallback: Optional[int] = None) -> int:
    """Return one pitch class, substituting only for an explicit ``None``.

    ``value`` may be a raw value or a mapping carrying ``key_root``. Empty
    strings and malformed values are not treated as missing because doing so
    would fabricate musical identity.
    """

    raw = value.get("key_root") if isinstance(value, Mapping) else value
    if raw is None:
        if fallback is None:
            raise KeyIdentityError("key_root is unknown and no fallback authority was supplied")
        raw = fallback
    if isinstance(raw, str) and not raw.strip():
        raise KeyIdentityError("empty key_root is invalid; only NULL may inherit a fallback")
    try:
        return int(raw) % 12
    except (TypeError, ValueError, OverflowError) as exc:
        raise KeyIdentityError(f"invalid key_root: {raw!r}") from exc


def normalize_key_item_for_legacy(item: Mapping[str, Any]) -> Dict[str, Any]:
    """Copy an item while keeping C numerically 0 and truthy for legacy code."""

    out = dict(item)
    raw = out.get("key_root")
    if raw is None:
        return out
    key = canonical_key_root(raw)
    out["key_root"] = _TRUTHY_KEY_ZERO if key == 0 else key
    return out


def normalize_key_pool_for_legacy(pool: Sequence[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    return [normalize_key_item_for_legacy(item) for item in pool]


def _explicit_key_item(item: Mapping[str, Any], fallback: int) -> Dict[str, Any]:
    out = dict(item)
    out["key_root"] = canonical_key_root(item, fallback)
    return out


def _corrected_taste_feasible_pool(
    self: Any,
    pool: List[Dict[str, Any]],
    render_bpm: float,
    target_key: int,
    params: Dict[str, Any],
) -> tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Legacy feasibility with one corrected key-identity law."""

    from earcrate.deck.dsp import track_identity
    from earcrate.deck.transform import plan_varispeed_transform
    import earcrate.app as app_module

    stretch_budget = float(params.get("stretch_budget") or 8.0)
    pitch_budget = int(params.get("pitch_shift_budget") or 2)
    role_names = list(getattr(app_module, "EAR_ROLE_ORDER", ()))
    rejected: Dict[str, int] = {str(role): 0 for role in role_names}
    counts: Dict[str, int] = {str(role): 0 for role in role_names}
    out: List[Dict[str, Any]] = []
    sources: set[str] = set()
    invalid_keys = 0

    for item in pool:
        role = str(item.get("role") or item.get("render_role") or "full")
        ear = str(item.get("ear_role") or "")
        try:
            key = canonical_key_root(item, int(target_key) % 12)
            source_bpm = float(item.get("bpm") if item.get("bpm") is not None else render_bpm)
            plan = plan_varispeed_transform(
                role,
                source_bpm,
                float(render_bpm),
                key,
                int(target_key) % 12,
                stretch_budget,
                pitch_budget,
            )
        except (KeyIdentityError, TypeError, ValueError, OverflowError):
            invalid_keys += 1
            rejected[ear] = rejected.get(ear, 0) + 1
            continue
        if plan.get("violation"):
            rejected[ear] = rejected.get(ear, 0) + 1
            continue
        admitted = dict(item)
        admitted["key_root"] = key if item.get("key_root") is not None else None
        admitted["feasible_transform"] = plan
        out.append(admitted)
        counts[ear] = counts.get(ear, 0) + 1
        sources.add(track_identity(admitted))

    have = {
        "foreground": counts.get("VOX_HOOK", 0) + counts.get("VOX_VERSE", 0)
        + counts.get("VOX_SHOUT", 0) + counts.get("RIFF_ID", 0),
        "floor": counts.get("DRUM_BREAK", 0) + counts.get("BED_CHORD", 0)
        + counts.get("RIFF_ID", 0) + counts.get("TEXTURE", 0),
        "bass": counts.get("BASS_RIFF", 0),
        "spark": counts.get("PICKUP_FILL", 0) + counts.get("DROP_HIT", 0)
        + counts.get("TRANSITION_TAIL", 0) + counts.get("TEXTURE", 0)
        + counts.get("VOX_SHOUT", 0),
        "sources": len(sources),
    }
    return out, {
        "render_bpm": round(float(render_bpm), 2),
        "target_key": int(target_key) % 12,
        "role_counts": counts,
        "have": have,
        "rejected_by_role": rejected,
        "invalid_key_count": invalid_keys,
        "pool_size": len(out),
        "source_tracks": len(sources),
        "key_identity_policy": KEY_IDENTITY_POLICY,
    }


def _source_identity(item: Mapping[str, Any]) -> str:
    explicit = item.get("source_track_key") or item.get("source_id")
    if explicit not in (None, ""):
        return str(explicit)
    try:
        from earcrate.deck.dsp import track_identity

        return str(track_identity(dict(item)))
    except Exception:
        artist = str(item.get("artist") or "").strip().lower()
        title = str(item.get("title") or "").strip().lower()
        if artist or title:
            return f"{artist}::{title}"
        return hashlib.sha1(str(item.get("path") or "").encode("utf-8", "replace")).hexdigest()[:12]


def _atom_identity(item: Mapping[str, Any]) -> str:
    return str(item.get("atom_id") or item.get("id") or item.get("loop_id") or "")


def corrected_source_pool_projection(
    pool: Sequence[Mapping[str, Any]], excluded_ids: Iterable[str] = ()
) -> List[Dict[str, Any]]:
    """Content projection that does not collapse unknown key into C."""

    excluded = {str(value) for value in excluded_ids}
    rows: List[Dict[str, Any]] = []
    for item in pool:
        source_id = _source_identity(item)
        atom_id = _atom_identity(item)
        if source_id in excluded or atom_id in excluded:
            continue
        raw_key = item.get("key_root")
        key_value = None if raw_key is None else canonical_key_root(raw_key)
        rows.append(
            {
                "source_id": source_id,
                "atom_id": atom_id,
                "loop_id": str(item.get("id") or item.get("loop_id") or ""),
                "source_audio_sha256": str(
                    item.get("source_audio_sha256") or item.get("audio_sha256") or ""
                ),
                "ear_role": str(item.get("ear_role") or ""),
                "render_role": str(item.get("render_role") or item.get("role") or ""),
                "bpm": float(item.get("bpm") or 0.0).hex(),
                "key_root": key_value,
                "bars": int(item.get("bars") or 0),
                "start_s": float(item.get("start_s") or 0.0).hex(),
                "end_s": float(item.get("end_s") or 0.0).hex(),
            }
        )
    rows.sort(key=lambda row: (row["source_id"], row["atom_id"], row["loop_id"]))
    return rows


def corrected_source_pool_identity(
    pool: Sequence[Mapping[str, Any]], excluded_ids: Iterable[str] = ()
) -> str:
    body = json.dumps(
        corrected_source_pool_projection(pool, excluded_ids),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _strict_transform_for_slot(
    item: Mapping[str, Any],
    slot_role: str,
    render_bpm: float,
    target_key: int,
    params: Mapping[str, Any],
) -> Optional[Dict[str, Any]]:
    """Exact-pool transform planning under the same key law as feasibility."""

    from earcrate.deck.transform import plan_varispeed_transform

    try:
        source_bpm = float(item.get("bpm") if item.get("bpm") is not None else render_bpm)
        source_key = canonical_key_root(item, int(target_key) % 12)
        stretch_budget = float(params.get("stretch_budget") or 8.0)
        pitch_budget = int(params.get("pitch_shift_budget") or 2)
        plan = plan_varispeed_transform(
            str(slot_role or "full"),
            source_bpm,
            float(render_bpm),
            source_key,
            int(target_key) % 12,
            stretch_budget,
            pitch_budget,
        )
    except (KeyIdentityError, TypeError, ValueError, OverflowError):
        return None
    if plan.get("violation"):
        return None
    return dict(plan)


def install_key_identity(core_class: Any) -> Any:
    """Install one key policy across feasibility, composition, edges, and identity."""

    if getattr(core_class, "_key_identity_installed", False):
        return core_class

    original_feasible = core_class.taste_feasible_pool

    @functools.wraps(original_feasible)
    def feasible(
        self: Any,
        pool: List[Dict[str, Any]],
        render_bpm: float,
        target_key: int,
        params: Dict[str, Any],
    ) -> tuple[List[Dict[str, Any]], Dict[str, Any]]:
        return _corrected_taste_feasible_pool(self, pool, render_bpm, target_key, params)

    original_compose = core_class.compose_taste_arrangement

    @functools.wraps(original_compose)
    def compose(
        self: Any, pool: List[Dict[str, Any]], params: Dict[str, Any], seed: int
    ) -> Dict[str, Any]:
        return original_compose(self, normalize_key_pool_for_legacy(pool), params, seed)

    original_edge = core_class.atom_edge_score

    @functools.wraps(original_edge)
    def edge(
        self: Any,
        left: Dict[str, Any],
        right: Dict[str, Any],
        relation: str,
        render_bpm: float,
        target_key: int,
        stretch_budget: float,
        pitch_budget: int,
    ) -> Any:
        return original_edge(
            self,
            _explicit_key_item(left, int(target_key) % 12),
            _explicit_key_item(right, int(target_key) % 12),
            relation,
            render_bpm,
            target_key,
            stretch_budget,
            pitch_budget,
        )

    core_class._pre_key_identity_taste_feasible_pool = original_feasible
    core_class._pre_key_identity_compose_taste_arrangement = original_compose
    core_class._pre_key_identity_atom_edge_score = original_edge
    core_class.taste_feasible_pool = feasible
    core_class.compose_taste_arrangement = compose
    core_class.atom_edge_score = edge

    # Patch the already-imported exact-island modules so pool identity, the
    # public script, and slot transforms all share the same rule.
    import earcrate.plan as plan_package
    import earcrate.plan.islands as islands_module
    import earcrate.plan.source_rotation as rotation_module

    islands_module.source_pool_projection = corrected_source_pool_projection
    islands_module.source_pool_identity = corrected_source_pool_identity
    plan_package.source_pool_identity = corrected_source_pool_identity
    rotation_module._transform_for_slot = _strict_transform_for_slot

    core_class._key_identity_installed = True
    core_class._key_identity_policy = KEY_IDENTITY_POLICY
    return core_class


__all__ = [
    "KEY_IDENTITY_POLICY",
    "KeyIdentityError",
    "canonical_key_root",
    "corrected_source_pool_identity",
    "corrected_source_pool_projection",
    "install_key_identity",
    "normalize_key_item_for_legacy",
    "normalize_key_pool_for_legacy",
]
