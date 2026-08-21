"""Anchor-based transition planning (Step 1 of the wizard-tier sequence).

The old model chose a transition by putting two timestamps on a beat grid:
``beats[idx + bars*4]`` with the length picked first from a fixed ``[8,4,2,1]``
ladder. This module replaces that with the correct object:

    ANCHORS  ->  TECHNIQUE TEMPLATE  ->  technique-DERIVED duration + a scored plan

Duration is an OUTCOME of the technique and the surrounding structure, never a
number chosen up front. A hard cut needs one beat; a long blend needs 16-32 bars
of compatible material; a bass swap's load-bearing instant is the low-end
handoff, not the fade start.

DETERMINISM: pure functions over an already-computed analysis dict (no audio, no
clock, no RNG). Same input -> byte-identical candidates. Everything consumes the
signals the analyzer ALREADY stores (downbeats, sections, bpm/key confidence,
recurrence/hook curve); nothing here re-decodes audio.

HONEST SCOPE. This is the foundation, not the whole system:
  * BUILT here (uses existing analysis): anchor extraction from downbeats +
    section boundaries + hook recurrence; a technique library with real
    preconditions; technique-derived duration; a multi-factor, technique-
    conditioned score; and the uncertainty rule -- a low-confidence beat grid
    downgrades a fragile long blend to a robust cut/echo (abstention), never the
    reverse.
  * DEFERRED to Step 2 (needs NEW per-beat features): kick/snare/bass/vocal
    ACTIVITY curves, local chord distributions, groove/microtiming. Techniques
    that require them (a true stem-level bass swap, a spectrally-safe double
    drop, phrase-aware vocal handling) declare that need in their preconditions
    and DISABLE themselves with a reason instead of pretending. Their control
    curves (stem/eq/effect envelopes) belong to the render layer and are left
    unset here.
  * DEFERRED to Steps 3-4: rendered-preview scoring of the summed signal, and
    control curves learned from aligned expert mixes.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

# Rounding for every emitted score so accumulation order never leaks FP noise
# into a plan comparison or a determinism gate.
_NDIGITS = 6


def _r(x: float) -> float:
    return round(float(x), _NDIGITS)


def _clamp01(x: float) -> float:
    return 0.0 if x < 0.0 else (1.0 if x > 1.0 else float(x))


# --------------------------------------------------------------------------- #
# Anchors: musically meaningful in/out points, NOT every beat.                #
# --------------------------------------------------------------------------- #
# Grid/section anchors are always available. The activity anchors (drums_in,
# bass_exit, vocal_start/end, drop) require Step-2 per-beat features; they appear
# ONLY when a beat_state is supplied, which is exactly what gates the stem-level
# techniques on until the features exist.
ENTRY_KINDS = ("clean_in", "drop_in", "section_in", "drums_in", "vocal_start", "drop")
EXIT_KINDS = ("clean_out", "outro_in", "break_out", "cadence", "bass_exit", "vocal_end", "drop")


@dataclass(frozen=True)
class Anchor:
    """A candidate in/out point with the evidence that justifies using it."""
    side: str                 # "entry" (incoming B) or "exit" (outgoing A)
    kind: str
    beat_index: int
    time_sec: float
    strength: float           # how salient the event is, [0,1]
    confidence: float         # how much we trust the grid/label here, [0,1]
    safe_lead_bars: float     # clean bars available BEFORE this anchor
    safe_tail_bars: float     # clean bars available AFTER this anchor

    def as_dict(self) -> Dict[str, Any]:
        return {
            "side": self.side, "kind": self.kind, "beat_index": self.beat_index,
            "time_sec": _r(self.time_sec), "strength": _r(self.strength),
            "confidence": _r(self.confidence),
            "safe_lead_bars": _r(self.safe_lead_bars),
            "safe_tail_bars": _r(self.safe_tail_bars),
        }


def _grid(analysis: Dict[str, Any]) -> Tuple[List[float], List[float], float, float]:
    beats = [float(b) for b in (analysis.get("beats") or [])]
    downbeats = [float(d) for d in (analysis.get("downbeats") or [])]
    bpm = float(analysis.get("bpm") or 0.0)
    bpm_conf = _clamp01(float(analysis.get("bpm_confidence") or 0.0))
    return beats, downbeats, bpm, bpm_conf


def _nearest_beat_index(beats: List[float], t: float) -> int:
    if not beats:
        return 0
    # deterministic nearest (ties -> lower index)
    best_i, best_d = 0, abs(beats[0] - t)
    for i, b in enumerate(beats):
        d = abs(b - t)
        if d < best_d:
            best_i, best_d = i, d
    return best_i


def track_anchors(analysis: Dict[str, Any],
                  beat_state: Optional[Dict[str, Any]] = None) -> Dict[str, List[Anchor]]:
    """Derive entry (incoming) and exit (outgoing) anchors for one track from its
    stored analysis. Deterministic and audio-free.

    Sources used:
      * downbeats            -> clean_in / clean_out (grid-aligned starts/ends)
      * sections[label,...]  -> section_in / outro_in / break_out / drop_in
      * bpm_confidence       -> per-anchor confidence
      * recurrence hook curve (optional) -> boosts strength of anchors near a hook
      * beat_state (Step 2, optional) -> drums_in / bass_exit / vocal_start /
        vocal_end / drop, the activity-derived anchors the stem techniques need

    A track with no downbeats yields no anchors (honest: we cannot place a
    grid-aligned transition without a grid)."""
    beats, downbeats, bpm, bpm_conf = _grid(analysis)
    if not downbeats or bpm <= 0:
        return {"entry": [], "exit": []}
    sections = analysis.get("sections") or []
    duration = float(analysis.get("duration_s") or (beats[-1] if beats else 0.0))
    bars_total = max(1.0, len(downbeats))
    # Optional hook curve: [(start_s, end_s, recur), ...] or parallel arrays.
    hook_spans = _hook_spans(analysis)

    def hook_boost(t: float) -> float:
        for a, b, r in hook_spans:
            if a <= t <= b:
                return 0.25 * _clamp01(r)
        return 0.0

    entry: List[Anchor] = []
    exit_: List[Anchor] = []

    # --- clean grid anchors: every downbeat is a usable in/out point, strength
    #     scaled by how phrase-aligned it is (16 > 8 > 4-bar boundary).
    for k, t in enumerate(downbeats):
        bi = _nearest_beat_index(beats, t)
        phrase = 1.0 if k % 16 == 0 else (0.8 if k % 8 == 0 else (0.6 if k % 4 == 0 else 0.4))
        strength = _clamp01(phrase + hook_boost(t))
        lead = float(k)
        tail = float(max(0.0, bars_total - k))
        entry.append(Anchor("entry", "clean_in", bi, t, strength, bpm_conf, lead, tail))
        exit_.append(Anchor("exit", "clean_out", bi, t, strength, bpm_conf, lead, tail))

    # --- section-boundary anchors carry FUNCTIONAL meaning (intro/outro/chorus).
    for sec in sections:
        try:
            start = float(sec.get("start"))
        except (TypeError, ValueError):
            continue
        label = str(sec.get("label") or "")
        bi = _nearest_beat_index(beats, start)
        # bars before/after this boundary within the track
        lead = start / max(1e-9, duration) * bars_total
        tail = max(0.0, bars_total - lead)
        conf = bpm_conf  # section labels inherit grid trust (no separate score yet)
        if label == "intro":
            # end-of-intro is a strong clean entry for the INCOMING track
            entry.append(Anchor("entry", "section_in", bi, start, 0.7, conf, lead, tail))
        elif label == "outro":
            exit_.append(Anchor("exit", "outro_in", bi, start, 0.9, conf, lead, tail))
        elif label == "chorus":
            # a chorus onset is a drop-like high-impact entry AND a strong exit
            entry.append(Anchor("entry", "drop_in", bi, start, _clamp01(0.85 + hook_boost(start)), conf, lead, tail))
            exit_.append(Anchor("exit", "cadence", bi, start, 0.7, conf, lead, tail))
        elif label == "verse":
            exit_.append(Anchor("exit", "break_out", bi, start, 0.5, conf, lead, tail))

    if beat_state:
        a_entry, a_exit = _activity_anchors(beat_state, beats, bars_total, bpm_conf)
        entry.extend(a_entry)
        exit_.extend(a_exit)

    entry.sort(key=lambda a: (a.beat_index, a.kind))
    exit_.sort(key=lambda a: (a.beat_index, a.kind))
    return {"entry": entry, "exit": exit_}


# Activity thresholds for calling a role "present" / "absent" at a beat.
_HI, _LO = 0.5, 0.25


def _activity_anchors(beat_state: Dict[str, Any], beats: List[float],
                      bars_total: float, conf: float) -> Tuple[List[Anchor], List[Anchor]]:
    """The Step-2 anchors: entrances/exits defined by what the MUSIC does, not just
    the grid. A clean drum entrance (kick up, bass still down) is the correct place
    to start a bass swap; a bass departure is where the outgoing low end should
    leave; vocal edges are sentence boundaries; a novelty spike is a drop."""
    act = beat_state.get("activity") or {}
    nov = beat_state.get("novelty") or []
    kick = act.get("kick") or []
    bass = act.get("bass") or []
    vocal = act.get("vocal") or []
    n = len(kick)

    def t_of(i: int) -> float:
        return float(beats[i]) if i < len(beats) else (float(beats[-1]) if beats else 0.0)

    def lead(i: int) -> float:
        return i / 4.0

    def tail(i: int) -> float:
        return max(0.0, bars_total - i / 4.0)

    entry: List[Anchor] = []
    exit_: List[Anchor] = []
    for i in range(1, n):
        # drums_in: a downbeat where incoming drums play but bass does not -- the
        # clean-drum region a bass swap rides in on (not necessarily a fresh
        # entrance; any bass-free drum bar works).
        if i % 4 == 0 and kick[i] >= _HI and bass[i] <= _LO:
            entry.append(Anchor("entry", "drums_in", i, t_of(i), kick[i], conf, lead(i), tail(i)))
        if bass[i - 1] >= _HI and bass[i] <= _LO:
            exit_.append(Anchor("exit", "bass_exit", i, t_of(i), bass[i - 1], conf, lead(i), tail(i)))
        if i < len(vocal) and vocal[i] >= _HI and vocal[i - 1] < _LO:
            entry.append(Anchor("entry", "vocal_start", i, t_of(i), vocal[i], conf, lead(i), tail(i)))
        if i < len(vocal) and vocal[i - 1] >= _HI and vocal[i] < _LO:
            exit_.append(Anchor("exit", "vocal_end", i, t_of(i), vocal[i - 1], conf, lead(i), tail(i)))
    for i in range(len(nov)):
        if nov[i] >= 0.85:
            entry.append(Anchor("entry", "drop", i, t_of(i), nov[i], conf, lead(i), tail(i)))
            exit_.append(Anchor("exit", "drop", i, t_of(i), nov[i], conf, lead(i), tail(i)))
    return entry, exit_


# Role-collision weights per technique: which simultaneous roles are RISKY. Two
# bass lines are almost always bad; two kicks flam; two leads/vocals clash; two
# percussion layers are often fine. hard_cut has no overlap so no collisions.
_COLLISION_W: Dict[str, Dict[str, float]] = {
    "long_blend": {"bass": 1.0, "vocal": 0.8, "lead": 0.5, "kick": 0.3, "snare": 0.1, "hat": 0.0},
    "bass_swap": {"bass": 1.0, "vocal": 0.5, "lead": 0.4, "kick": 0.2, "snare": 0.1, "hat": 0.0},
    "double_drop": {"bass": 1.0, "kick": 0.9, "lead": 0.8, "vocal": 0.6, "snare": 0.3, "hat": 0.1},
    "echo_out": {"bass": 0.3, "vocal": 0.3, "lead": 0.2, "kick": 0.1, "snare": 0.0, "hat": 0.0},
    "hard_cut": {},
}


def _role_collision(a_state: Optional[Dict[str, Any]], b_state: Optional[Dict[str, Any]],
                    a_exit_beat: int, b_entry_beat: int, dur_bars: int,
                    tech_name: str) -> Tuple[float, Dict[str, float]]:
    """C_s(c): per-role co-activity across the overlap, weighted by how risky that
    role's collision is for THIS technique. Returns (normalized penalty [0,1],
    per-role co-activity). The outgoing track's tail continues from its exit; the
    incoming head plays from its entry; they are compared beat-for-beat."""
    w = _COLLISION_W.get(tech_name, {})
    if not a_state or not b_state or dur_bars <= 0 or not w:
        return 0.0, {}
    aA = a_state.get("activity") or {}
    aB = b_state.get("activity") or {}
    K = dur_bars * 4
    per: Dict[str, float] = {}
    for role in w:
        ra = aA.get(role) or []
        rb = aB.get(role) or []
        vals = []
        for k in range(K):
            ia, ib = a_exit_beat + k, b_entry_beat + k
            if ia < len(ra) and ib < len(rb):
                vals.append(float(ra[ia]) * float(rb[ib]))
        if vals:
            per[role] = sum(vals) / len(vals)
    tw = sum(w.values()) or 1.0
    penalty = sum(w[r] * c for r, c in per.items()) / tw
    return _clamp01(penalty), {r: _r(c) for r, c in per.items()}


def _hook_spans(analysis: Dict[str, Any]) -> List[Tuple[float, float, float]]:
    hooks = analysis.get("hook_spans") or analysis.get("recurrence")
    out: List[Tuple[float, float, float]] = []
    if isinstance(hooks, dict):
        cs = hooks.get("col_start") or []
        ce = hooks.get("col_end") or []
        rc = hooks.get("recur") or []
        for a, b, r in zip(cs, ce, rc):
            out.append((float(a), float(b), _clamp01(float(r))))
    elif isinstance(hooks, list):
        for item in hooks:
            try:
                out.append((float(item[0]), float(item[1]), _clamp01(float(item[2]))))
            except (TypeError, ValueError, IndexError):
                continue
    return out


# --------------------------------------------------------------------------- #
# Technique templates: each decides its OWN preconditions + duration.         #
# --------------------------------------------------------------------------- #
@dataclass
class TransitionTemplate:
    name: str
    entry_kinds: Tuple[str, ...]
    exit_kinds: Tuple[str, ...]
    min_grid_conf: float          # meter/grid trust required (blend strict, cut lenient)
    max_tempo_warp: float         # allowed |bpm ratio - 1| (0.0 = only same tempo, 1.0 = any)
    needs_stem_activity: bool     # requires Step-2 per-beat activity -> disabled until then
    # score weights (benefits) and risk weights (penalties), technique-conditioned
    w: Dict[str, float]
    r: Dict[str, float]


# The starter library. needs_stem_activity techniques honestly disable until the
# per-beat activity features (Step 2) exist -- they cannot be done correctly from
# scalar vocal_likelihood / track-level key alone.
TECHNIQUES: Tuple[TransitionTemplate, ...] = (
    TransitionTemplate(
        "hard_cut", entry_kinds=("drop_in", "clean_in", "section_in"),
        exit_kinds=("cadence", "clean_out", "break_out", "outro_in"),
        min_grid_conf=0.0, max_tempo_warp=1.0, needs_stem_activity=False,
        w={"phrase": 0.4, "impact": 0.6, "harmonic": 0.0, "energy": 0.3},
        r={"grid": 0.0, "collision": 0.0}),
    TransitionTemplate(
        "echo_out", entry_kinds=("clean_in", "drop_in", "section_in"),
        exit_kinds=("cadence", "break_out", "outro_in", "clean_out"),
        min_grid_conf=0.25, max_tempo_warp=0.5, needs_stem_activity=False,
        w={"phrase": 0.5, "impact": 0.2, "harmonic": 0.2, "energy": 0.3},
        r={"grid": 0.3, "collision": 0.1}),
    TransitionTemplate(
        "long_blend", entry_kinds=("clean_in", "section_in"),
        exit_kinds=("clean_out", "outro_in"),
        min_grid_conf=0.6, max_tempo_warp=0.06, needs_stem_activity=False,
        w={"phrase": 0.8, "impact": 0.1, "harmonic": 0.8, "energy": 0.5},
        r={"grid": 0.7, "collision": 0.6}),
    TransitionTemplate(
        "bass_swap", entry_kinds=("drums_in",),
        exit_kinds=("bass_exit", "break_out"),
        min_grid_conf=0.6, max_tempo_warp=0.06, needs_stem_activity=True,
        w={"phrase": 0.7, "impact": 0.3, "harmonic": 0.5, "energy": 0.4},
        r={"grid": 0.6, "collision": 0.9}),
    TransitionTemplate(
        "double_drop", entry_kinds=("drop",),
        exit_kinds=("drop", "cadence"),
        min_grid_conf=0.75, max_tempo_warp=0.06, needs_stem_activity=True,
        w={"phrase": 0.9, "impact": 1.0, "harmonic": 0.6, "energy": 0.8},
        r={"grid": 0.8, "collision": 1.0}),
)


@dataclass
class TransitionPlan:
    """A scored, technique-derived transition. Control curves (stem/eq/effect
    envelopes) are the render layer's job (Step 3) and are intentionally left out
    here; this is the STRUCTURAL plan a renderer or the existing plan_transition
    grammar can execute."""
    outgoing: str
    incoming: str
    technique: str
    a_exit_beat: int
    b_entry_beat: int
    duration_bars: int
    scores: Dict[str, float]
    total_score: float
    confidence: float
    predicted_failure_modes: List[str] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "outgoing": self.outgoing, "incoming": self.incoming,
            "technique": self.technique, "a_exit_beat": self.a_exit_beat,
            "b_entry_beat": self.b_entry_beat, "duration_bars": self.duration_bars,
            "scores": {k: _r(v) for k, v in self.scores.items()},
            "total_score": _r(self.total_score), "confidence": _r(self.confidence),
            "predicted_failure_modes": list(self.predicted_failure_modes),
        }


def _tempo_warp(a_bpm: float, b_bpm: float) -> float:
    if a_bpm <= 0 or b_bpm <= 0:
        return 1.0
    # fold octave errors (half/double tempo) before measuring the warp
    ratio = b_bpm / a_bpm
    while ratio > 1.5:
        ratio /= 2.0
    while ratio < 0.67:
        ratio *= 2.0
    return abs(ratio - 1.0)


def _harmonic_compat(a: Dict[str, Any], b: Dict[str, Any]) -> float:
    """Coarse track-level harmonic compatibility in [0,1] from key root/mode.
    Deliberately coarse: local chord-distribution harmony is Step 2. Same key = 1;
    perfect fifth / relative = high; tritone = low."""
    try:
        ar, br = int(a.get("key_root")), int(b.get("key_root"))
        am, bm = int(a.get("key_mode")), int(b.get("key_mode"))
    except (TypeError, ValueError):
        return 0.5  # unknown key -> neutral, do not reward or punish
    interval = (br - ar) % 12
    circle = {0: 1.0, 7: 0.85, 5: 0.85, 9: 0.7, 3: 0.7, 2: 0.55, 10: 0.55}
    base = circle.get(interval, 0.3 if interval == 6 else 0.45)
    if am != bm:
        base *= 0.9
    return _clamp01(base)


def derive_duration(tech: TransitionTemplate, a_exit: Anchor, b_entry: Anchor,
                    grid_conf: float) -> int:
    """Duration is an OUTCOME of technique + structure, not a pre-chosen number.

      hard_cut    -> 0 bars (continuity from expectation/impact, one downbeat)
      echo_out    -> a short tail (2-4 bars), shorter when the grid is shaky
      long_blend  -> a phrase-multiple (16 or 32 bars) bounded by the clean
                     material actually available on BOTH sides
      bass_swap   -> lead-in bars until the low-end handoff (needs Step-2 stems)
      double_drop -> the alignment window around the paired drops
    """
    if tech.name == "hard_cut":
        return 0
    if tech.name == "echo_out":
        return 4 if grid_conf >= 0.5 else 2
    # Overlapping techniques play BOTH tracks forward from their anchors, so the
    # available material is the outgoing TAIL after its exit and the incoming tail
    # after its entry -- not the outgoing lead-in (that has already played).
    avail = max(0.0, min(a_exit.safe_tail_bars, b_entry.safe_tail_bars))
    if tech.name == "long_blend":
        want = 32.0 if (grid_conf >= 0.8 and avail >= 32.0) else 16.0
        return int(min(want, avail))
    if tech.name == "bass_swap":
        return int(min(8.0, avail))
    if tech.name == "double_drop":
        return int(min(8.0, avail))
    return int(max(1.0, min(8.0, avail)))


def _score_plan(tech: TransitionTemplate, a: Dict[str, Any], b: Dict[str, Any],
                a_exit: Anchor, b_entry: Anchor, set_state: Dict[str, Any],
                grid_conf: float, dur_bars: int = 0,
                a_state: Optional[Dict[str, Any]] = None,
                b_state: Optional[Dict[str, Any]] = None) -> Tuple[Dict[str, float], float, List[str]]:
    warp = _tempo_warp(float(a.get("bpm") or 0), float(b.get("bpm") or 0))
    harmonic = _harmonic_compat(a, b)
    phrase = 0.5 * (a_exit.strength + b_entry.strength)
    impact = b_entry.strength if b_entry.kind == "drop_in" else 0.35
    # energy trajectory: does the incoming section move energy the way the set wants?
    want = str(set_state.get("energy_intent") or "maintain")
    b_energy = _clamp01(float(b.get("energy") or 0.0))
    a_energy = _clamp01(float(a.get("energy") or 0.0))
    delta = b_energy - a_energy
    if want == "lift":
        energy = _clamp01(0.5 + delta)
    elif want == "release":
        energy = _clamp01(0.5 - delta)
    else:
        energy = _clamp01(1.0 - abs(delta))
    benefits = (tech.w["phrase"] * phrase + tech.w["impact"] * impact
                + tech.w["harmonic"] * harmonic + tech.w["energy"] * energy)
    # risks: shaky grid, and a tempo warp beyond what the technique tolerates
    grid_risk = tech.r["grid"] * (1.0 - grid_conf)
    warp_over = max(0.0, warp - tech.max_tempo_warp)
    warp_risk = tech.r["collision"] * min(1.0, warp_over / max(1e-6, tech.max_tempo_warp or 1.0))
    # role collision C_s(c): needs per-beat activity (Step 2). 0 without it.
    role_penalty, per_role = _role_collision(a_state, b_state, a_exit.beat_index,
                                             b_entry.beat_index, dur_bars, tech.name)
    role_risk = tech.r["collision"] * role_penalty
    total = benefits - grid_risk - warp_risk - role_risk
    failures: List[str] = []
    if grid_conf < 0.5:
        failures.append("low beat-grid confidence (%.2f)" % grid_conf)
    if warp > tech.max_tempo_warp:
        failures.append("tempo warp %.1f%% exceeds %s limit %.1f%%"
                        % (warp * 100, tech.name, tech.max_tempo_warp * 100))
    if per_role.get("bass", 0.0) >= 0.4:
        failures.append("bass-vs-bass collision %.2f across the overlap" % per_role["bass"])
    if per_role.get("vocal", 0.0) >= 0.4:
        failures.append("two lead vocals overlap %.2f" % per_role["vocal"])
    scores = {
        "phrase": phrase, "impact": impact, "harmonic": harmonic,
        "energy": energy, "grid_risk": grid_risk, "warp_risk": warp_risk,
        "role_collision": role_risk,
    }
    return scores, total, failures


def _preconditions(tech: TransitionTemplate, a: Dict[str, Any], b: Dict[str, Any],
                   a_exit: Anchor, b_entry: Anchor, grid_conf: float,
                   have_state: bool = False) -> Tuple[bool, List[str]]:
    reasons: List[str] = []
    if tech.needs_stem_activity and not have_state:
        reasons.append("%s needs per-beat stem activity (Step 2); disabled" % tech.name)
        return False, reasons
    # Overlapping techniques need enough clean material AFTER both anchors to run.
    _MIN_TAIL = {"long_blend": 8.0, "bass_swap": 4.0, "double_drop": 4.0, "echo_out": 2.0}
    need = _MIN_TAIL.get(tech.name)
    if need is not None:
        avail = min(a_exit.safe_tail_bars, b_entry.safe_tail_bars)
        if avail < need:
            reasons.append("%s needs >=%d bars after both anchors, have %.1f"
                           % (tech.name, int(need), avail))
    if a_exit.kind not in tech.exit_kinds:
        reasons.append("exit anchor %r not valid for %s" % (a_exit.kind, tech.name))
    if b_entry.kind not in tech.entry_kinds:
        reasons.append("entry anchor %r not valid for %s" % (b_entry.kind, tech.name))
    if grid_conf < tech.min_grid_conf:
        reasons.append("grid confidence %.2f below %s minimum %.2f"
                       % (grid_conf, tech.name, tech.min_grid_conf))
    warp = _tempo_warp(float(a.get("bpm") or 0), float(b.get("bpm") or 0))
    if warp > tech.max_tempo_warp:
        reasons.append("tempo warp %.1f%% over %s limit" % (warp * 100, tech.name))
    return (not reasons), reasons


def generate_transition_candidates(a: Dict[str, Any], b: Dict[str, Any],
                                   set_state: Optional[Dict[str, Any]] = None,
                                   top_k: int = 8,
                                   a_state: Optional[Dict[str, Any]] = None,
                                   b_state: Optional[Dict[str, Any]] = None) -> List[TransitionPlan]:
    """All viable transition plans from outgoing ``a`` to incoming ``b``, scored
    and ranked. For every technique x valid exit-anchor x valid entry-anchor we
    instantiate a plan, drop it if it fails the technique's HARD preconditions,
    otherwise derive its duration and score it. Deterministic order (score desc,
    then technique/beat for stable ties).

    ``a_state``/``b_state`` are the optional Step-2 beat states. WITHOUT them the
    stem techniques (bass_swap, double_drop) generate nothing (their anchors don't
    exist) and role collisions are unknown -- honest degradation. WITH them, the
    activity anchors appear, the stem techniques become reachable, and each plan is
    penalized for bass/vocal/lead collisions across its actual overlap.

    The UNCERTAINTY RULE is structural, not cosmetic: a low-confidence grid fails
    the ``min_grid_conf`` precondition of long_blend/double_drop, so those simply
    are not generated -- what survives is the robust cut / echo. The planner
    therefore abstains from a fragile blend instead of attempting it."""
    set_state = set_state or {}
    a_anchors = track_anchors(a, a_state)["exit"]
    b_anchors = track_anchors(b, b_state)["entry"]
    grid_conf = min(_clamp01(float(a.get("bpm_confidence") or 0.0)),
                    _clamp01(float(b.get("bpm_confidence") or 0.0)))
    a_id = str(a.get("id") or a.get("file_id") or "A")
    b_id = str(b.get("id") or b.get("file_id") or "B")
    have_state = bool(a_state and b_state)
    plans: List[TransitionPlan] = []
    for tech in TECHNIQUES:
        # Stem techniques stay disabled until BOTH beat states are supplied.
        if tech.needs_stem_activity and not have_state:
            continue
        for a_exit in a_anchors:
            if a_exit.kind not in tech.exit_kinds:
                continue
            for b_entry in b_anchors:
                if b_entry.kind not in tech.entry_kinds:
                    continue
                ok, _reasons = _preconditions(tech, a, b, a_exit, b_entry, grid_conf, have_state)
                if not ok:
                    continue
                dur = derive_duration(tech, a_exit, b_entry, grid_conf)
                scores, total, failures = _score_plan(
                    tech, a, b, a_exit, b_entry, set_state, grid_conf, dur, a_state, b_state)
                plans.append(TransitionPlan(
                    outgoing=a_id, incoming=b_id, technique=tech.name,
                    a_exit_beat=a_exit.beat_index, b_entry_beat=b_entry.beat_index,
                    duration_bars=dur, scores=scores, total_score=total,
                    confidence=_r(grid_conf), predicted_failure_modes=failures))
    plans.sort(key=lambda p: (-p.total_score, p.technique, p.a_exit_beat, p.b_entry_beat))
    return plans[:max(1, top_k)] if plans else []


def best_transition(a: Dict[str, Any], b: Dict[str, Any],
                    set_state: Optional[Dict[str, Any]] = None,
                    a_state: Optional[Dict[str, Any]] = None,
                    b_state: Optional[Dict[str, Any]] = None) -> Optional[TransitionPlan]:
    """The single highest-scoring viable plan, or None when NOTHING is viable
    (e.g. no grid on either track) -- the caller should then hold/skip rather than
    force an unmusical transition."""
    cands = generate_transition_candidates(a, b, set_state, top_k=1,
                                           a_state=a_state, b_state=b_state)
    return cands[0] if cands else None
