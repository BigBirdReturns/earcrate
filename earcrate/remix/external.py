"""External-target remix — the one mode that doesn't wait on owning more records.

Every other path composes ENTIRELY from library material: the engine picks a deck
(tempo + key) that maximizes what the crate can play, then rails a vocal, bed, and
sparks out of approved atoms. This inverts that. You drop a fresh, out-of-library
vocal (an acapella, a fresh take, a foreign hook); the engine ANCHORS the render to
that vocal's own tempo and key and rebuilds a bed UNDER it, in a remix persona's
style, from the library. The target is the boss — the bed conforms to it, never the
reverse — so the dropped vocal is never time-stretched or pitch-shifted into mud.

This is what the 22 remix personas were built for (Branchez, Pretty Lights, Dilla,
...): "ONE foreground element over a bed rebuilt in a producer's style."

Anchor inversion is the whole idea:
  * normal path  -> choose_taste_deck searches for the tempo/key with the most
                    playable material; the bed and vocal both bend to that deck.
  * external path -> the deck is PINNED to the target vocal's (bpm, key); only the
                    library bed bends. `remix_anchor` reads the pin off the target's
                    analyzed features; `external_remix_feasibility` then answers the
                    honest question — does the crate have enough bed that survives
                    transform to THAT anchor in THIS persona's budget?

Everything here is pure (features in, plan out) so the composition/gate logic is
gate-tested without decoding audio; the EarcrateCore method wires analysis + render.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

# Ear-roles that count as a usable bed under an external vocal. A remix needs a
# structural floor (drums/chords/riff) at minimum; bass and sparks are enrichment.
_FLOOR_ROLES = ("DRUM_BREAK", "BED_CHORD", "RIFF_ID", "TEXTURE")


def remix_anchor(feats: Dict[str, Any]) -> Dict[str, Any]:
    """Read the render anchor (tempo + key) off the target vocal's analyzed features.

    ``compute_pcm_features`` already folds bpm into [70, 180]; we only guard against a
    zero/garbage tempo (a near-silent or arrhythmic acapella) by falling back to a
    neutral 120 so the bed still has a grid to lock to. Returns the pin the deck is
    forced to — NOT a search space."""
    bpm = float(feats.get("bpm") or 0.0)
    if not (40.0 <= bpm <= 260.0):
        bpm = 120.0
    key_root = int(feats.get("key_root") or 0) % 12
    key_mode = int(feats.get("key_mode") if feats.get("key_mode") is not None else 1)
    return {
        "bpm": round(bpm, 3),
        "key_root": key_root,
        "key_mode": key_mode,
        "key_confidence": float(feats.get("key_confidence") or 0.0),
        "bpm_confidence": float(feats.get("bpm_confidence") or 0.0),
        "vocal_likelihood": float(feats.get("vocal_likelihood") or 0.0),
    }


def external_foreground_atom(title: str, anchor: Dict[str, Any], duration_s: float,
                             pcm_sha: str, path: str) -> Dict[str, Any]:
    """Build the pinned foreground 'atom' that represents the dropped vocal.

    Shaped exactly like a library VOX atom so the existing composer rails it as the
    foreground with no special-casing — but it carries an ``external_ref`` (path +
    identity + duration) that the renderer follows to the file on disk instead of the
    loop cache, and ``is_external`` so the composer holds it for the WHOLE track
    (a dropped vocal is one continuous performance, not a rotating sample). Its bpm/key
    ARE the anchor, so its own transform is identity and it renders undegraded.

    Deterministic: identical inputs -> identical atom (gate-stable)."""
    ident = "external::" + (pcm_sha[:16] if pcm_sha else "unknown")
    return {
        "id": ident,
        "atom_id": ident,
        "ear_role": "VOX_HOOK",
        "role": "vocal",
        "render_role": "vocal",
        "key_root": int(anchor["key_root"]) % 12,
        "key_mode": int(anchor.get("key_mode") or 1),
        "bpm": float(anchor["bpm"]),
        "score": 1.0,
        "hook_score": 1.0,
        # Band shares are cosmetic here (layer receipts + bass-gating). A vocal is
        # presence-forward and low-light: keep low_share under the 0.34 bass gate so
        # the composer still lets a library bass in under the acapella.
        "high_share": 0.30,
        "low_share": 0.08,
        "artist": "external",
        "title": str(title or "target"),
        "duration_s": float(duration_s or 0.0),
        "is_external": True,
        "external_ref": {"path": str(path), "pcm_sha": str(pcm_sha or ""),
                         "duration_s": float(duration_s or 0.0)},
    }


def external_vocal_window(abs_bar_start: int, bars: int, render_bpm: float,
                          duration_s: float) -> Optional[Tuple[float, float]]:
    """Map a section (absolute bar position + length) onto the vocal's OWN timeline.

    The dropped vocal is a single continuous take, so it plays front-to-back across the
    arrangement: section starting at absolute bar B draws the slice of the vocal that
    lines up with B at the anchor tempo. Returns (start_s, len_s), clamped so a section
    that runs past the end of the vocal gets only the remaining audio; returns None once
    the vocal is fully spent (the bed carries on instrumental past the last word)."""
    spb = 4.0 * 60.0 / max(1e-6, float(render_bpm))  # seconds per bar
    start_s = float(abs_bar_start) * spb
    dur = float(duration_s or 0.0)
    if dur <= 0.0:
        # Unknown length: trust the grid, let the renderer clamp to the real file.
        return (start_s, float(bars) * spb)
    if start_s >= dur - 1e-3:
        return None
    len_s = min(float(bars) * spb, dur - start_s)
    if len_s <= 1e-3:
        return None
    return (round(start_s, 4), round(len_s, 4))


def external_remix_feasibility(diag: Dict[str, Any], needed_sources: int) -> Dict[str, Any]:
    """Honest buildability verdict for an external remix at the pinned anchor.

    ``diag`` is the ``taste_feasible_pool`` diagnostics AT THE ANCHOR (library bed only,
    the external vocal excluded). A remix is buildable when the crate can supply, after
    transform to the target's own tempo/key: a structural floor, and enough distinct bed
    sources to avoid a one-loop drone. Bass/spark are reported but never block — a bed
    can be drums+chords with no standalone bassline. Returns {buildable, reasons, have}."""
    have = dict(diag.get("have") or {})
    floor = int(have.get("floor", 0))
    bass = int(have.get("bass", 0))
    spark = int(have.get("spark", 0))
    sources = int(have.get("sources", 0))
    min_sources = max(2, int(needed_sources or 0))
    reasons: List[str] = []
    buildable = True
    if floor < 1:
        buildable = False
        reasons.append("no structural bed (drums/chords/riff) survives transform to the target's tempo & key")
    if sources < min_sources:
        buildable = False
        reasons.append(f"only {sources} distinct bed source(s) at the anchor; need >= {min_sources} to avoid a single-loop drone")
    if buildable:
        note = f"bed OK: {floor} floor, {bass} bass, {spark} spark across {sources} sources at {diag.get('render_bpm')} BPM key {diag.get('target_key')}"
        if bass < 1:
            note += " (no standalone bassline — floor carries the low end)"
        reasons.append(note)
    return {"buildable": buildable, "reasons": reasons,
            "have": {"floor": floor, "bass": bass, "spark": spark, "sources": sources,
                     "needed_sources": min_sources}}
