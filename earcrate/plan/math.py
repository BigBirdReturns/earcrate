"""Pure composition arithmetic (§5.3 / Lesson #1).

Every formula the composer and the readiness audit share lives here exactly
once. No I/O, no DB, no core state, no persona lookups — just numbers in,
numbers out. The functions reproduce, byte-for-byte, the arithmetic that used
to be inlined in app.py (taste_readiness, choose_taste_deck, propose_mashup,
compose_taste_arrangement, the post-render gate). Do not \"improve\" a constant
here without changing the gate that pins it: these numbers ARE the contract.

Default source_seconds is 11.5 — the fallback used everywhere in app.py as
`float(profile.get(\"source_seconds\") or 11.5)`.
"""
import math

DEFAULT_SOURCE_SECONDS = 11.5
# A 2-minute sketch is the reference length; readiness targets scale off it but
# are clamped so a very short or very long target still asks for a sane crate.
REFERENCE_SECONDS = 120.0
SCALE_MIN = 0.5
SCALE_MAX = 1.2
# Minimum distinct sources any set needs regardless of how short the target is.
MIN_SOURCES = 5
# Per-role base counts at scale 1.0 (target == REFERENCE_SECONDS) and their floors.
FOREGROUND_BASE, FOREGROUND_FLOOR = 12, 4
FLOOR_BASE, FLOOR_FLOOR = 16, 6
BASS_BASE, BASS_FLOOR = 6, 3
SPARK_BASE, SPARK_FLOOR = 12, 5
# Composition floor: never render fewer than this many bars, always a whole
# 4-bar phrase count.
MIN_BARS = 16
BARS_PER_PHRASE = 4


def readiness_scale(target_seconds: float) -> float:
    """clamp(target_seconds / 120, 0.5, 1.2) — the readiness need multiplier."""
    return max(SCALE_MIN, min(SCALE_MAX, float(target_seconds) / REFERENCE_SECONDS))


def sources_needed(target_seconds: float,
                   source_seconds: float = DEFAULT_SOURCE_SECONDS) -> int:
    """Distinct sources a target length demands.

    max(5, ceil(target_seconds / source_seconds)). e.g. a 2-min set at 11.5s of
    usable material per source needs ceil(120/11.5)=11 sources.
    """
    return max(MIN_SOURCES, int(math.ceil(float(target_seconds) / float(source_seconds))))


def readiness_need(target_seconds: float,
                   source_seconds: float = DEFAULT_SOURCE_SECONDS) -> dict:
    """The readiness need{} dict: per-role atom counts + distinct-source count.

    Key insertion order (foreground, floor, bass, spark, sources) is part of the
    contract — the readiness audit iterates need.items() to build its failure
    list in that order.
    """
    scale = readiness_scale(target_seconds)
    return {
        "foreground": max(FOREGROUND_FLOOR, int(math.ceil(FOREGROUND_BASE * scale))),
        "floor": max(FLOOR_FLOOR, int(math.ceil(FLOOR_BASE * scale))),
        "bass": max(BASS_FLOOR, int(math.ceil(BASS_BASE * scale))),
        "spark": max(SPARK_FLOOR, int(math.ceil(SPARK_BASE * scale))),
        "sources": sources_needed(target_seconds, source_seconds),
    }


def bars_exact(target_seconds: float, bpm: float) -> float:
    """Fractional bar count for a target length at a tempo.

    bars = beats / 4; beats = target_seconds * bpm / 60. So
    bars_exact = target_seconds * bpm / 60 / 4 (a 4/4 assumption).
    """
    return float(target_seconds) * float(bpm) / 60.0 / 4.0


def target_bars(target_seconds: float, bpm: float) -> int:
    """Whole-song bar count, snapped to 4-bar phrases with a 16-bar floor.

    Rounds bars_exact to the nearest whole 4-bar phrase, never below 16 bars.
    (Guards the v0.7.4 regression where a stray *4 quadrupled every render.)
    """
    return max(MIN_BARS,
               int(round(bars_exact(target_seconds, bpm) / float(BARS_PER_PHRASE)))
               * BARS_PER_PHRASE)
