"""Bind every selected note to exactly one playable zone, or refuse.

A rack is not a sound. It is a claim that each note in a performance can be produced
by a specific, identified piece of recorded instrument, within a declared amount of
retuning. Coverage that cannot name the zone it used is not coverage.

So this refuses rather than improvises. A note with no zone, a note matched by two
zones that disagree, a note needing more transposition than the rack declares, a
sample whose bytes have changed since the rack was bound — each stops the render. No
General MIDI fallback and no silent substitution: both would answer a different
question than "does this performance work through this instrument".

The instrument is one coherent sampled piano on purpose. Satisfying the pitch range by
assembling fragments from unrelated recordings would introduce an arrangement and
source-selection problem into a lane that exists to test performed interpretation.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from typing import Any, Mapping, Sequence

import numpy as np

NOTE_NAMES = {"c": 0, "d": 2, "e": 4, "f": 5, "g": 7, "a": 9, "b": 11}


class RackError(RuntimeError):
    pass


@dataclass(frozen=True)
class Zone:
    """One playable region: which notes and velocities it covers, and with what."""

    index: int
    sample: str
    lokey: int
    hikey: int
    lovel: int
    hivel: int
    root: int
    release_seconds: float
    velocity_track: float
    trigger: str = "attack"
    controller_triggered: bool = False

    @property
    def is_note_zone(self) -> bool:
        """A note zone sounds when a key goes down.

        Release samples and pedal noise are real parts of the instrument and are not
        candidates for placing a note: an SFZ that carries them will otherwise report
        several zones covering every event, all of them disagreeing, and the binding
        refuses everything for the wrong reason.
        """
        return self.trigger == "attack" and not self.controller_triggered

    def covers(self, pitch: int, velocity: int) -> bool:
        return self.lokey <= pitch <= self.hikey and self.lovel <= velocity <= self.hivel

    def transposition(self, pitch: int) -> int:
        return pitch - self.root

    def as_dict(self) -> dict[str, Any]:
        return {"index": self.index, "sample": self.sample,
                "key_range": [self.lokey, self.hikey],
                "velocity_range": [self.lovel, self.hivel],
                "root_key": self.root,
                "release_seconds": self.release_seconds,
                "velocity_track": self.velocity_track}


def _note_number(token: str) -> int:
    """SFZ keys may be numbers or note names such as `A0` or `D#1`."""
    token = token.strip()
    if re.fullmatch(r"-?\d+", token):
        return int(token)
    match = re.fullmatch(r"([A-Ga-g])([#b]?)(-?\d+)", token)
    if not match:
        raise RackError(f"unreadable key: {token!r}")
    step, accidental, octave = match.groups()
    value = NOTE_NAMES[step.lower()] + (1 if accidental == "#" else -1 if accidental else 0)
    return value + (int(octave) + 1) * 12


def parse_sfz(path: Path) -> tuple[list[Zone], dict[str, Any]]:
    """Read zones from an SFZ, carrying group defaults down into each region."""
    path = Path(path)
    text = path.read_text(encoding="utf-8", errors="replace")
    group: dict[str, str] = {}
    zones: list[Zone] = []

    for raw in text.splitlines():
        line = raw.split("//", 1)[0].strip()
        if not line:
            continue
        opcodes = dict(re.findall(r"(\w+)=([^\s]+(?:\\[^\s]+)*)", line))
        if line.startswith("<group>"):
            group = opcodes
            continue
        if not line.startswith("<region>"):
            continue
        merged = {**group, **opcodes}
        if "sample" not in merged:
            raise RackError(f"a region names no sample: {line[:60]}")
        zones.append(Zone(
            index=len(zones), sample=merged["sample"].replace("\\", "/"),
            lokey=_note_number(merged.get("lokey", "0")),
            hikey=_note_number(merged.get("hikey", "127")),
            lovel=int(merged.get("lovel", 1)), hivel=int(merged.get("hivel", 127)),
            root=_note_number(merged.get("pitch_keycenter", merged.get("lokey", "60"))),
            release_seconds=float(merged.get("ampeg_release", 0.4)),
            velocity_track=float(merged.get("amp_veltrack", 100)) / 100.0,
            trigger=merged.get("trigger", "attack"),
            controller_triggered=any(key.startswith(("on_locc", "on_hicc"))
                                     for key in merged)))

    if not zones:
        raise RackError(f"{path.name} defines no regions")
    note_zones = [zone for zone in zones if zone.is_note_zone]
    if not note_zones:
        raise RackError(f"{path.name} defines no note-triggered regions")
    return zones, {
        "definition": path.name,
        "region_count": len(zones),
        "note_zone_count": len(note_zones),
        "release_zone_count": sum(1 for z in zones if z.trigger == "release"),
        "controller_zone_count": sum(1 for z in zones if z.controller_triggered),
        "note_zones_only_are_bindable": True,
    }


def bind(demand: Mapping[str, Any], zones: Sequence[Zone], *,
         max_transposition: int = 3) -> dict[str, Any]:
    """Match every selected event to exactly one zone, or report why not."""
    bindings: list[dict[str, Any]] = []
    refusals: list[dict[str, Any]] = []

    playable_zones = [zone for zone in zones if zone.is_note_zone]
    for event in demand["selected_event_identities"]:
        pitch, velocity = int(event["pitch"]), int(event["velocity"])
        matches = [zone for zone in playable_zones if zone.covers(pitch, velocity)]

        if not matches:
            refusals.append({**event, "reason": "no zone covers this pitch and velocity"})
            continue

        playable = [zone for zone in matches
                    if abs(zone.transposition(pitch)) <= max_transposition]
        if not playable:
            refusals.append({
                **event, "reason":
                f"every covering zone needs more than {max_transposition} semitones of "
                f"transposition (nearest {min(abs(z.transposition(pitch)) for z in matches)})"})
            continue

        # Ambiguity is only a problem when the candidates would sound different.
        distinct = {(zone.sample, zone.transposition(pitch)) for zone in playable}
        if len(distinct) > 1:
            refusals.append({
                **event, "reason":
                f"{len(distinct)} zones cover this event and disagree: "
                f"{sorted(sample for sample, _ in distinct)[:3]}"})
            continue

        zone = playable[0]
        bindings.append({**event, "zone_index": zone.index, "sample": zone.sample,
                         "transposition_semitones": zone.transposition(pitch),
                         "release_seconds": zone.release_seconds,
                         "velocity_track": zone.velocity_track})

    return {
        "selected_event_count": demand["selected_event_count"],
        "bound_event_count": len(bindings),
        "refused_event_count": len(refusals),
        "all_events_bound": len(bindings) == demand["selected_event_count"],
        "max_transposition_semitones": max_transposition,
        "transposition_histogram": {
            str(value): sum(1 for row in bindings
                            if row["transposition_semitones"] == value)
            for value in sorted({row["transposition_semitones"] for row in bindings})},
        "distinct_samples_used": sorted({row["sample"] for row in bindings}),
        "bindings": bindings,
        "refusals": refusals,
    }


def verify_sources(root: Path, binding: Mapping[str, Any],
                   expected: Mapping[str, str] | None = None) -> dict[str, Any]:
    """Every sample the binding names must exist, and must be what it was when bound."""
    from ...evidence.identity import sha256_file

    observed: dict[str, str] = {}
    missing: list[str] = []
    mutated: list[str] = []

    for sample in binding["distinct_samples_used"]:
        path = Path(root) / sample
        if not path.is_file():
            missing.append(sample)
            continue
        digest = sha256_file(path)
        observed[sample] = digest
        if expected and expected.get(sample) not in (None, digest):
            mutated.append(sample)

    return {"sample_count": len(binding["distinct_samples_used"]),
            "missing": missing, "mutated": mutated,
            "source_identities": observed,
            "sources_intact": not missing and not mutated}
