"""Patch 2: MaterialRegion -- variable-length regions, not fixed-bar loops.

The kernel bottleneck the review identified: extract_loops_one enumerates only
[8,4,2,1] bars, starts at downbeats[::bars], keeps <=12 regions, and ends at
idx+bars*4. Any phrase, pickup, fill, outro, drop, or transition tail not exposed
by that generator is invisible to every later stage. This module replaces the
loop-with-one-role unit with a MaterialRegion whose:

  * START comes from a musically meaningful anchor (phrase boundary, clean drum
    entrance, bass exit, vocal-phrase edge, drop, section boundary), NOT a fixed
    stride;
  * END is proposed INDEPENDENTLY of the start (grid phrases of 1/2/4/8/16/32
    bars, the enclosing section boundary, a vocal-phrase end, or the natural tail
    to the end of the track);
  * ROLE is a set of CAPABILITIES with probabilities (vocal_foreground=0.85,
    rhythmic_bed=0.2, transition_tail=0.55), not one permanent label; and
  * confidence/salience/loopability are measured, so downstream pruning is by
    function, not by a stride.

The old fixed-bar extractor is preserved behind ``baseline=True`` (for the
frozen baseline_v1 comparison the review asks for). ``loop_end_from_beats``-style
exact-grid construction survives as ONE low-level end constructor here, no longer
the whole searchable universe.

DETERMINISM: pure functions over an analysis dict (+ optional Step-2 beat_state).
Same input -> byte-identical regions. Reuses the gated anchor detector in
earcrate.plan.transitions and the per-beat features in earcrate.analyze.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from earcrate.plan.transitions import track_anchors, Anchor

_NDIGITS = 6
# grid phrase lengths proposed for every start (bars). NOT a stride -- every
# qualified anchor gets all of these ends, then we prune.
_PHRASE_BARS = (1.0, 2.0, 4.0, 8.0, 16.0, 32.0)
_KINDS = ("one_shot", "pickup", "phrase", "loopable_bed", "section", "natural_tail")


def _r(x: float) -> float:
    return round(float(x), _NDIGITS)


def _clamp01(x: float) -> float:
    return 0.0 if x < 0.0 else (1.0 if x > 1.0 else float(x))


@dataclass(frozen=True)
class MaterialRegion:
    source_id: str
    start_beat: int
    end_beat: int
    start_time_s: float
    end_time_s: float
    bars: float
    kind: str
    start_kind: str
    end_kind: str
    role_probabilities: Dict[str, float]
    section_role: str
    phrase_boundary_confidence: float
    loopability: float
    salience: float
    analysis_confidence: float

    def as_dict(self) -> Dict[str, Any]:
        return {
            "source_id": self.source_id, "start_beat": self.start_beat,
            "end_beat": self.end_beat, "start_time_s": _r(self.start_time_s),
            "end_time_s": _r(self.end_time_s), "bars": _r(self.bars), "kind": self.kind,
            "start_kind": self.start_kind, "end_kind": self.end_kind,
            "role_probabilities": {k: _r(v) for k, v in self.role_probabilities.items()},
            "section_role": self.section_role,
            "phrase_boundary_confidence": _r(self.phrase_boundary_confidence),
            "loopability": _r(self.loopability), "salience": _r(self.salience),
            "analysis_confidence": _r(self.analysis_confidence),
        }


def _beats(analysis: Dict[str, Any]) -> List[float]:
    return [float(b) for b in (analysis.get("beats") or [])]


def _bars_total(analysis: Dict[str, Any]) -> float:
    return max(1.0, float(len(analysis.get("downbeats") or [])))


def _section_at(analysis: Dict[str, Any], t: float) -> str:
    for sec in analysis.get("sections") or []:
        try:
            if float(sec.get("start")) <= t < float(sec.get("end")):
                return str(sec.get("label") or "")
        except (TypeError, ValueError):
            continue
    return ""


def _mean_over(curve: List[float], b0: int, b1: int) -> float:
    if not curve:
        return 0.0
    seg = curve[max(0, b0):max(b0 + 1, b1)]
    return float(sum(seg) / len(seg)) if seg else 0.0


def _std_over(curve: List[float], b0: int, b1: int) -> float:
    seg = curve[max(0, b0):max(b0 + 1, b1)] if curve else []
    if len(seg) < 2:
        return 0.0
    m = sum(seg) / len(seg)
    return (sum((x - m) ** 2 for x in seg) / len(seg)) ** 0.5


def _role_capabilities(beat_state: Optional[Dict[str, Any]], b0: int, b1: int,
                       near_tail: bool) -> Tuple[Dict[str, float], float, float]:
    """Region role CAPABILITIES + (loopability, salience) from per-beat activity.
    Without beat_state, capabilities are unknown (empty) but the region is still a
    valid grid region (honest degradation)."""
    if not beat_state:
        return {}, 0.0, 0.0
    act = beat_state.get("activity") or {}
    nov = beat_state.get("novelty") or []
    vocal = _mean_over(act.get("vocal") or [], b0, b1)
    bass = _mean_over(act.get("bass") or [], b0, b1)
    lead = _mean_over(act.get("lead") or [], b0, b1)
    kick = _mean_over(act.get("kick") or [], b0, b1)
    snare = _mean_over(act.get("snare") or [], b0, b1)
    hat = _mean_over(act.get("hat") or [], b0, b1)
    percussive = _clamp01((kick + snare + hat) / 3.0)
    salience = _clamp01(_mean_over(nov, b0, b1))
    # steady (low novelty variance) + percussive -> loopable bed
    loopability = _clamp01((1.0 - min(1.0, _std_over(nov, b0, b1) * 2.0)) * (0.5 + 0.5 * percussive))
    caps = {
        "vocal_foreground": _clamp01(vocal),
        "bass_anchor": _clamp01(bass),
        "lead_foreground": _clamp01(lead),
        "rhythmic_bed": _clamp01(0.5 * percussive + 0.5 * loopability),
        # a low-novelty, low-vocal region near the track end makes a good tail
        "transition_tail": _clamp01((0.6 if near_tail else 0.3) * (1.0 - salience) * (1.0 - vocal)),
    }
    return caps, loopability, salience


def _classify(bars: float, end_kind: str, loopability: float,
              caps: Dict[str, float]) -> str:
    if end_kind == "natural_tail":
        return "natural_tail"
    if end_kind == "section":
        return "section"
    if bars < 1.0:
        return "one_shot"
    if bars <= 1.0 and end_kind == "pickup":
        return "pickup"
    if loopability >= 0.6 and caps.get("rhythmic_bed", 0.0) >= 0.5:
        return "loopable_bed"
    return "phrase"


def propose_regions(analysis: Dict[str, Any],
                    beat_state: Optional[Dict[str, Any]] = None,
                    baseline: bool = False,
                    per_start_cap: int = 6) -> List[MaterialRegion]:
    """Enumerate candidate MaterialRegions for one track.

    ``baseline=True`` reproduces the old fixed-bar extractor (starts at
    downbeats[::bars] for bars in 8,4,2,1) for the frozen baseline comparison.
    Otherwise: every qualified START anchor gets INDEPENDENT end proposals (grid
    phrases, the enclosing section end, and the natural tail), each classified and
    scored, then pruned per start by salience+confidence. Deterministic order."""
    beats = _beats(analysis)
    downbeats = [float(d) for d in (analysis.get("downbeats") or [])]
    if not beats or not downbeats:
        return []
    bpm = float(analysis.get("bpm") or 0.0)
    if bpm <= 0:
        return []
    bpm_conf = _clamp01(float(analysis.get("bpm_confidence") or 0.0))
    bars_total = _bars_total(analysis)
    src = str(analysis.get("id") or analysis.get("file_id") or "src")
    n_beats = len(beats)

    def t_of(bi: int) -> float:
        return float(beats[bi]) if 0 <= bi < n_beats else (float(beats[-1]) if beats else 0.0)

    if baseline:
        return _baseline_regions(analysis, beats, downbeats, src, bpm_conf)

    anchors = track_anchors(analysis, beat_state)["entry"]
    regions: List[MaterialRegion] = []
    for a in anchors:
        s = a.beat_index
        ends: List[Tuple[int, str]] = []
        # (1) grid phrases of several lengths, independent of the start
        for nb in _PHRASE_BARS:
            e = s + int(nb * 4)
            if e < n_beats and e > s:
                ends.append((e, "grid"))
        # (2) the enclosing/next section boundary
        for sec in analysis.get("sections") or []:
            try:
                se = float(sec.get("end"))
            except (TypeError, ValueError):
                continue
            eb = _nearest(beats, se)
            if eb > s + 1:
                ends.append((eb, "section"))
                break
        # (3) the natural tail to the end of the track
        if n_beats - 1 > s + 3:
            ends.append((n_beats - 1, "natural_tail"))
        seen = set()
        cand: List[MaterialRegion] = []
        for e, ek in ends:
            if e in seen:
                continue
            seen.add(e)
            bars = (e - s) / 4.0
            if bars <= 0:
                continue
            near_tail = (bars_total - e / 4.0) <= 2.0
            caps, loop, sal = _role_capabilities(beat_state, s, e, near_tail)
            kind = _classify(bars, ek if ek != "grid" else "phrase", loop, caps)
            conf = _clamp01(bpm_conf * (0.5 + 0.5 * a.strength))
            cand.append(MaterialRegion(
                source_id=src, start_beat=s, end_beat=e,
                start_time_s=t_of(s), end_time_s=t_of(e), bars=bars, kind=kind,
                start_kind=a.kind, end_kind=ek,
                role_probabilities=caps, section_role=_section_at(analysis, t_of(s)),
                phrase_boundary_confidence=a.strength, loopability=loop,
                salience=sal, analysis_confidence=conf))
        # prune per start: keep the most salient/confident ends
        cand.sort(key=lambda r: (-(r.salience + r.analysis_confidence), r.end_beat))
        regions.extend(cand[:max(1, per_start_cap)])
    regions.sort(key=lambda r: (r.start_beat, r.end_beat, r.kind))
    return regions


def _nearest(beats: List[float], t: float) -> int:
    if not beats:
        return 0
    best_i, best_d = 0, abs(beats[0] - t)
    for i, b in enumerate(beats):
        d = abs(b - t)
        if d < best_d:
            best_i, best_d = i, d
    return best_i


def _baseline_regions(analysis: Dict[str, Any], beats: List[float],
                      downbeats: List[float], src: str, bpm_conf: float) -> List[MaterialRegion]:
    """The frozen baseline_v1 generator: the exact [8,4,2,1] / downbeats[::bars] /
    idx+bars*4 behavior, wrapped as MaterialRegions so the new path and the old
    one are directly comparable on candidate recall."""
    n_beats = len(beats)
    out: List[MaterialRegion] = []
    seen = set()
    for bars in (8, 4, 2, 1):
        step = max(1, bars)
        for k in range(0, len(downbeats), step):
            db_t = downbeats[k]
            s = _nearest(beats, db_t)
            e = s + bars * 4
            if e >= n_beats or e <= s:
                continue
            key = (s, e)
            if key in seen:
                continue
            seen.add(key)
            out.append(MaterialRegion(
                source_id=src, start_beat=s, end_beat=e,
                start_time_s=float(beats[s]), end_time_s=float(beats[e]),
                bars=float(bars), kind="phrase", start_kind="clean_in",
                end_kind="grid", role_probabilities={}, section_role="",
                phrase_boundary_confidence=1.0 if k % 16 == 0 else 0.5,
                loopability=0.0, salience=0.0, analysis_confidence=bpm_conf))
    out.sort(key=lambda r: (r.start_beat, r.end_beat))
    return out
