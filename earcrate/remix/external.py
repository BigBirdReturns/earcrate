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

import math
from statistics import median
from typing import Any, Dict, List, Optional, Tuple

# Ear-roles that count as a usable bed under an external vocal. A remix needs a
# structural floor (drums/chords/riff) at minimum; bass and sparks are enrichment.
_FLOOR_ROLES = ("DRUM_BREAK", "BED_CHORD", "RIFF_ID", "TEXTURE")

# Musical band an anchor tempo must land in. Octave candidates outside this are
# discarded; the vocal-plausible sub-band [60,120] is where sung material lives
# (an acapella that reads >120 is almost always a doubled estimate).
_TEMPO_BAND = (60.0, 180.0)
_VOCAL_BAND = (60.0, 120.0)


def _octave_candidates(v: float) -> List[float]:
    """Octave-shifted tempo candidates for ``v`` that fall in the musical band.

    Tempo estimators on acapellas famously octave-error (double or halve the true
    pulse). We enumerate v, v/2, v*2, v/4, v*4, keep the ones inside [60,180], and
    let the caller pick — anchoring the vocal's OWN octave, never retuning it to a
    foreign tempo. De-duplicated, order-stable, and never empty (raw v is the floor)."""
    seen: List[float] = []
    for factor in (1.0, 0.5, 2.0, 0.25, 4.0):
        cand = round(v * factor, 6)
        if _TEMPO_BAND[0] <= cand <= _TEMPO_BAND[1] and not any(abs(cand - s) < 1e-6 for s in seen):
            seen.append(cand)
    return seen or [round(v, 6)]


def _dominant_bed_key(bed_keys: List[Tuple[int, float]]) -> Optional[int]:
    """Score-weighted dominant key root across the library bed (deterministic).

    Ties break to the lowest root so identical input always yields identical output."""
    agg: Dict[int, float] = {}
    for root, weight in bed_keys:
        r = int(root) % 12
        agg[r] = agg.get(r, 0.0) + max(0.0, float(weight))
    if not agg:
        return None
    return max(sorted(agg.keys()), key=lambda k: agg[k])


def remix_anchor(feats: Dict[str, Any],
                 bed_tempos: Optional[List[float]] = None,
                 bed_keys: Optional[List[Tuple[int, float]]] = None) -> Dict[str, Any]:
    """Read the render anchor (tempo + key) off the target vocal, disambiguating the
    worst-case failure modes of tempo/key estimation on a bare acapella.

    ``compute_pcm_features`` folds bpm into a band but is blind to OCTAVE errors: a
    76 BPM acapella routinely reads as a confident 152 (a clean 2x), and the whole bed
    then conforms to double-time. Likewise a key pinned at ~0.2 confidence is a guess we
    must not transpose a whole library bed to. So:

      * BPM: enumerate the vocal's octave candidates in [60,180]. With ``bed_tempos``,
        lock to the candidate closest (log-distance) to the bed's MEDIAN — the library
        material pulls a doubled vocal back down to where real loops live. Without a bed,
        prefer the vocal-plausible band [60,120]; halve a >130 read (V/2 >= 60).
      * KEY: if ``key_confidence < 0.30`` the key is a guess — never hard-pin it. Given
        ``bed_keys`` ((root, weight) per bed atom), adopt the score-weighted dominant bed
        key (let the bed's natural key win). Otherwise keep the vocal key, low-confidence.

    Backward compatible: ``remix_anchor(feats)`` with no hints still returns every original
    key. An ``anchor_source`` receipt records what drove each choice for inspection."""
    raw_bpm = float(feats.get("bpm") or 0.0)
    if not (40.0 <= raw_bpm <= 260.0):
        raw_bpm = 120.0

    candidates = _octave_candidates(raw_bpm)
    bpm = raw_bpm
    bpm_from = "vocal"
    tempos = [float(t) for t in (bed_tempos or []) if t and float(t) > 0.0]
    if tempos:
        med = float(median(tempos))
        target = math.log2(med) if med > 0 else math.log2(max(1e-6, raw_bpm))
        bpm = min(candidates, key=lambda c: abs(math.log2(c) - target))
        bpm_from = "bed_matched" if abs(bpm - raw_bpm) > 1e-6 else "vocal"
    else:
        in_band = [c for c in candidates if _VOCAL_BAND[0] <= c <= _VOCAL_BAND[1]]
        if _VOCAL_BAND[0] <= raw_bpm <= _VOCAL_BAND[1]:
            bpm = raw_bpm
        elif in_band:
            bpm = min(in_band, key=lambda c: abs(math.log2(c) - math.log2(raw_bpm)))
        elif raw_bpm > 130.0 and (raw_bpm / 2.0) >= _VOCAL_BAND[0]:
            bpm = raw_bpm / 2.0
        bpm_from = "halved" if bpm < raw_bpm - 1e-6 else "vocal"

    key_root = int(feats.get("key_root") or 0) % 12
    key_mode = int(feats.get("key_mode") if feats.get("key_mode") is not None else 1)
    key_conf = float(feats.get("key_confidence") or 0.0)
    key_from = "vocal"
    if key_conf < 0.30:
        # The vocal's key is a guess: do NOT hard-pin it (a whole library bed would be
        # transposed to a coin-flip). Prefer the bed's own dominant key when we have one.
        dom = _dominant_bed_key(bed_keys) if bed_keys else None
        if dom is not None:
            key_root = dom
            key_from = "bed_dominant"

    return {
        "bpm": round(bpm, 3),
        "key_root": key_root,
        "key_mode": key_mode,
        "key_confidence": key_conf,
        "bpm_confidence": float(feats.get("bpm_confidence") or 0.0),
        "vocal_likelihood": float(feats.get("vocal_likelihood") or 0.0),
        "anchor_source": {
            "bpm_raw": round(raw_bpm, 3),
            "bpm_from": bpm_from,
            "key_from": key_from,
            "key_conf": key_conf,
        },
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
