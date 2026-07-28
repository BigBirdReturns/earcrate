from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace(path: Path, old: str, new: str, label: str) -> None:
    text = path.read_text(encoding="utf-8")
    if text.count(old) != 1:
        raise SystemExit(f"{label} patch point missing or ambiguous: {text.count(old)}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


# Evidence tiers and a branch that cannot be mistaken for blind audio.
model = ROOT / "earcrate" / "specimen" / "model.py"
replace(
    model,
    'BUFFALO_GATE_RECEIPT_SCHEMA_VERSION = 1\n\nBRANCHES = (\n    "score",\n    "audio",\n',
    'BUFFALO_GATE_RECEIPT_SCHEMA_VERSION = 1\n\nEVIDENCE_TIERS = {\n    "unspecified",\n    "authoritative_score",\n    "community_symbolic_witness",\n    "blind_audio_inference",\n    "cross_modal_accepted",\n    "performance_realization",\n}\n\nBRANCHES = (\n    "score",\n    "symbolic",\n    "audio",\n',
    "model evidence tiers",
)
replace(
    model,
    '''BRANCH_ALLOWED_ANCESTORS: dict[str, frozenset[str]] = {
    "score": frozenset({"score"}),
    "audio": frozenset({"audio"}),
    "convergence": frozenset({"score", "audio", "convergence"}),
    "performance": frozenset({"score", "audio", "convergence", "performance"}),
    "review": frozenset({"performance", "review"}),
    "evolution": frozenset({"score", "audio", "convergence", "performance", "review", "evolution"}),
}
''',
    '''BRANCH_ALLOWED_ANCESTORS: dict[str, frozenset[str]] = {
    "score": frozenset({"score"}),
    "symbolic": frozenset({"symbolic"}),
    "audio": frozenset({"audio"}),
    "convergence": frozenset({"score", "symbolic", "audio", "convergence"}),
    "performance": frozenset({"score", "symbolic", "audio", "convergence", "performance"}),
    "review": frozenset({"performance", "review"}),
    "evolution": frozenset({"score", "symbolic", "audio", "convergence", "performance", "review", "evolution"}),
}
''',
    "model branch ancestry",
)
replace(
    model,
    '''    out = {
        "schema_version": SPECIMEN_MANIFEST_SCHEMA_VERSION,
        "kind": "earcrate_specimen_manifest",
        "specimen_id": specimen_id,
        "title": _text(manifest.get("title"), "title"),
        "credited_artist": str(manifest.get("credited_artist") or ""),
        "credited_composer": str(manifest.get("credited_composer") or ""),
        "rights": deepcopy(dict(manifest.get("rights") or {})),
        "artifacts": sorted(artifacts, key=lambda row: row["artifact_id"]),
        "expected": deepcopy(dict(manifest.get("expected") or {})),
        "metadata": deepcopy(dict(manifest.get("metadata") or {})),
    }
''',
    '''    metadata = deepcopy(dict(manifest.get("metadata") or {}))
    evidence_tier = str(manifest.get("evidence_tier") or metadata.get("evidence_tier") or "unspecified").strip()
    if evidence_tier not in EVIDENCE_TIERS:
        raise SpecimenError(f"evidence_tier must be one of {sorted(EVIDENCE_TIERS)}")
    metadata["evidence_tier"] = evidence_tier
    out = {
        "schema_version": SPECIMEN_MANIFEST_SCHEMA_VERSION,
        "kind": "earcrate_specimen_manifest",
        "specimen_id": specimen_id,
        "title": _text(manifest.get("title"), "title"),
        "credited_artist": str(manifest.get("credited_artist") or ""),
        "credited_composer": str(manifest.get("credited_composer") or ""),
        "evidence_tier": evidence_tier,
        "rights": deepcopy(dict(manifest.get("rights") or {})),
        "artifacts": sorted(artifacts, key=lambda row: row["artifact_id"]),
        "expected": deepcopy(dict(manifest.get("expected") or {})),
        "metadata": metadata,
    }
''',
    "manifest evidence tier",
)

# The standalone artifact must embed the authority that package mode actually calls.
builder = ROOT / "build" / "make_singlefile.py"
replace(
    builder,
    'SPECIMEN_FILES = ["model.py", "convergence.py", "children.py", "continuation.py", "gate.py", "cli.py", "__init__.py"]',
    'SPECIMEN_FILES = ["model.py", "convergence.py", "community.py", "children.py", "continuation.py", "continuation_dense.py", "flim.py", "gate.py", "cli.py", "__init__.py"]',
    "single-file specimen modules",
)

# A legal flag alone is insufficient: the gate must enforce the dense identity contract.
gate = ROOT / "earcrate" / "specimen" / "gate.py"
replace(
    gate,
    '''    continuation = continuation_receipt
    continuation_ok = bool(continuation and continuation.get("legal") is True and continuation.get("negative_control_refused") is True)
''',
    '''    continuation = continuation_receipt
    continuation_midi = dict((continuation or {}).get("midi") or {})
    continuation_novelty = dict((continuation or {}).get("novelty") or {})
    continuation_ok = bool(
        continuation
        and continuation.get("legal") is True
        and continuation.get("negative_control_refused") is True
        and continuation.get("rhythmic_identity_passed") is True
        and int(continuation.get("open_obligation_count") or -1) == 0
        and int(continuation_midi.get("selected_event_count") or 0) > 0
        and int(continuation_midi.get("selected_event_count") or 0) == int(continuation_midi.get("executed_event_count") or -1)
        and int(continuation_midi.get("refused_event_count") or 0) == 0
        and continuation_novelty.get("literal_copy_detected") is False
        and continuation_novelty.get("pitch_sequence_changed") is True
        and continuation_novelty.get("harmony_sequence_changed") is True
    )
''',
    "strict adjacent-move organ",
)

# Preserve the community witness as an organ without promoting it to hearing.
heritage = ROOT / "earcrate" / "music" / "heritage.py"
needle = '''    {
        "organ": "cross_organ_specimen_gate",
        "sources": ["earcrate.specimen", "content-addressed acceptance specimens", "cross-modal convergence reports"],
'''
insert = '''    {
        "organ": "community_symbolic_witnesses",
        "sources": ["public community notation", "instrument-part witnesses", "pattern recipes", "catalog identity"],
        "destination": "explicit intermediate evidence tier feeding editable PerformanceScores and adjacent-move tests",
        "disposition": "preserve",
        "reason": "community symbolic evidence can prove reconstruction, composition, and transport while remaining categorically distinct from blind recording inference",
    },
''' + needle
replace(heritage, needle, insert, "heritage community witness")

# Give the synthetic Children fixture a real final-eight-bar two-hand rhythm contract.
test = ROOT / "tests" / "test_buffalo_specimen.py"
replace(
    test,
    '''def _midi(path: Path) -> None:
    midi = mido.MidiFile(type=1, ticks_per_beat=PPQ)
    conductor = mido.MidiTrack()
    conductor.append(mido.MetaMessage("set_tempo", tempo=round(60_000_000 / 130), time=0))
    conductor.append(mido.MetaMessage("time_signature", numerator=4, denominator=4, time=0))
    midi.tracks.append(conductor)
    for name, channel, note in (("Right Hand", 0, 77), ("Left Hand", 1, 49)):
        track = mido.MidiTrack()
        track.append(mido.MetaMessage("track_name", name=name, time=0))
        track.append(mido.Message("note_on", channel=channel, note=note, velocity=88, time=0))
        track.append(mido.Message("note_off", channel=channel, note=note, velocity=0, time=PPQ))
        midi.tracks.append(track)
    midi.save(path)
''',
    '''def _midi(path: Path, occurrences: list[dict], per_measure: dict[str, dict]) -> None:
    midi = mido.MidiFile(type=1, ticks_per_beat=PPQ)
    conductor = mido.MidiTrack()
    conductor.append(mido.MetaMessage("set_tempo", tempo=round(60_000_000 / 130), time=0))
    conductor.append(mido.MetaMessage("time_signature", numerator=4, denominator=4, time=0))
    midi.tracks.append(conductor)
    for name, channel, staff in (("Right Hand", 0, "treble"), ("Left Hand", 1, "bass")):
        absolute = []
        for occurrence in occurrences:
            measure = per_measure[str(int(occurrence["measure"]))]
            base = int(round(float(occurrence["start_beat"]) * PPQ))
            for event in measure[staff]:
                start = base + int(round(float(event["beat"]) * PPQ))
                duration = max(1, int(round(float(event["duration"]) * PPQ)))
                note = int(event["midi"])
                absolute.append((start, 1, mido.Message("note_on", channel=channel, note=note, velocity=88, time=0)))
                absolute.append((start + duration, 0, mido.Message("note_off", channel=channel, note=note, velocity=0, time=0)))
        absolute.sort(key=lambda row: (row[0], row[1], str(row[2])))
        track = mido.MidiTrack()
        track.append(mido.MetaMessage("track_name", name=name, time=0))
        previous = 0
        for tick, _priority, message in absolute:
            track.append(message.copy(time=int(tick) - previous))
            previous = int(tick)
        track.append(mido.MetaMessage("end_of_track", time=0))
        midi.tracks.append(track)
    midi.save(path)
''',
    "synthetic MIDI expansion",
)
replace(
    test,
    '''    per_measure["1"]["bass"] = [
        {"kind": "note", "midi": 49, "pitch": "Db3", "beat": 0.0, "duration": 1.0}
    ]
    occurrence_counts: dict[int, int] = {}
''',
    '''    per_measure["1"]["bass"] = [
        {"kind": "note", "midi": 49, "pitch": "Db3", "beat": 0.0, "duration": 1.0}
    ]
    tail_pitches = {
        50: (77, 49),
        51: (80, 49),
        52: (82, 49),
        65: (77, 49),
        66: (75, 49),
        67: (73, 49),
        68: (72, 49),
        69: (77, 41),
    }
    for measure, (treble_pitch, bass_pitch) in tail_pitches.items():
        per_measure[str(measure)]["treble"] = [
            {"kind": "note", "midi": treble_pitch, "pitch": f"midi-{treble_pitch}", "beat": 0.0, "duration": 1.0}
        ]
        per_measure[str(measure)]["bass"] = [
            {"kind": "note", "midi": bass_pitch, "pitch": f"midi-{bass_pitch}", "beat": 0.0, "duration": 1.0}
        ]
    occurrence_counts: dict[int, int] = {}
''',
    "synthetic rhythm tail",
)
replace(
    test,
    '''    extraction = {
''',
    '''    linear_counts = {
        staff: sum(len(per_measure[str(int(row["measure"]))][staff]) for row in occurrences)
        for staff in ("treble", "bass")
    }
    total_note_count = sum(linear_counts.values())
    extraction = {
''',
    "synthetic linear counts",
)
replace(
    test,
    '        "linear_note_counts": {"treble": 1, "bass": 1},',
    '        "linear_note_counts": linear_counts,',
    "synthetic extraction counts",
)
replace(test, '    _midi(midi_path)', '    _midi(midi_path, occurrences, per_measure)', "synthetic MIDI call")
replace(test, '        "midi": {"note_count": 2, "instrument_count": 2},', '        "midi": {"note_count": total_note_count, "instrument_count": 2},', "synthetic proof count")
replace(test, '            "midi_note_count": 2,', '            "midi_note_count": total_note_count,', "synthetic manifest count")
replace(test, '    assert receipt["counts"]["notes"] == 2', '    assert receipt["counts"]["notes"] == 24', "synthetic assertion")
replace(
    test,
    '    assert payload["specimen_ids"] == ["children_v1"]',
    '    assert payload["specimen_ids"] == ["children_v1", "flim_bad_plus_v1"]',
    "single-file specimen IDs",
)
replace(
    test,
    '    assert rows["cross_organ_specimen_gate"]["disposition"] == "preserve"',
    '    assert rows["cross_organ_specimen_gate"]["disposition"] == "preserve"\n    assert rows["community_symbolic_witnesses"]["disposition"] == "preserve"',
    "heritage witness assertion",
)

# Focused CI must execute the new tier and the dense continuation.
workflow = ROOT / ".github" / "workflows" / "song-reader.yml"
replace(
    workflow,
    '      - "tests/test_children_continuation.py"\n',
    '      - "tests/test_children_continuation.py"\n      - "tests/test_flim_community_witness.py"\n',
    "workflow path",
)
replace(
    workflow,
    '          tests/test_children_continuation.py\n',
    '          tests/test_children_continuation.py\n          tests/test_flim_community_witness.py\n',
    "workflow command",
)

# Children is the authoritative-score control in the ladder.
children_manifest = ROOT / "specimens" / "children_v1.json"
value = json.loads(children_manifest.read_text(encoding="utf-8"))
value["evidence_tier"] = "authoritative_score"
children_manifest.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")

# Extend branch enums and require the normalized evidence tier.
branches = ["score", "symbolic", "audio", "convergence", "performance", "review", "evolution"]
tiers = ["unspecified", "authoritative_score", "community_symbolic_witness", "blind_audio_inference", "cross_modal_accepted", "performance_realization"]
manifest_schema_path = ROOT / "schemas" / "earcrate_specimen_manifest_v1.schema.json"
manifest_schema = json.loads(manifest_schema_path.read_text(encoding="utf-8"))
manifest_schema["properties"]["artifacts"]["items"]["properties"]["branch"]["enum"] = branches
manifest_schema["properties"]["evidence_tier"] = {"enum": tiers}
if "evidence_tier" not in manifest_schema["required"]:
    manifest_schema["required"].insert(4, "evidence_tier")
manifest_schema_path.write_text(json.dumps(manifest_schema, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")

observation_schema_path = ROOT / "schemas" / "earcrate_observation_ledger_v1.schema.json"
observation_schema = json.loads(observation_schema_path.read_text(encoding="utf-8"))


def extend_branch_enums(node):
    if isinstance(node, dict):
        if isinstance(node.get("enum"), list) and set(node["enum"]) == set(branches) - {"symbolic"}:
            node["enum"] = branches
        for item in node.values():
            extend_branch_enums(item)
    elif isinstance(node, list):
        for item in node:
            extend_branch_enums(item)


extend_branch_enums(observation_schema)
observation_schema_path.write_text(json.dumps(observation_schema, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")

adjacent_schema_path = ROOT / "schemas" / "earcrate_children_adjacent_move_receipt_v1.schema.json"
adjacent_schema = json.loads(adjacent_schema_path.read_text(encoding="utf-8"))
for field in ("rhythmic_identity_passed", "rhythmic_obligation"):
    if field not in adjacent_schema["required"]:
        adjacent_schema["required"].insert(adjacent_schema["required"].index("novelty"), field)
adjacent_schema["properties"]["rhythmic_identity_passed"] = {"const": True}
adjacent_schema["properties"]["rhythmic_obligation"] = {
    "type": "object",
    "required": ["rhythmic_identity_passed", "source_event_count", "continuation_event_count", "duration_multiset_preserved"],
    "properties": {
        "rhythmic_identity_passed": {"const": True},
        "source_event_count": {"type": "integer", "minimum": 1},
        "continuation_event_count": {"type": "integer", "minimum": 1},
        "duration_multiset_preserved": {"const": True},
    },
    "additionalProperties": True,
}
novelty = adjacent_schema["properties"]["novelty"]
for field in ("pitch_sequence_changed", "harmony_sequence_changed"):
    if field not in novelty["required"]:
        novelty["required"].append(field)
    novelty["properties"][field] = {"const": True}
adjacent_schema_path.write_text(json.dumps(adjacent_schema, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")

# Bootstrap files are not part of the reviewable result.
Path(__file__).unlink()
workflow_path = ROOT / ".github" / "workflows" / "apply-flim-community-witness.yml"
workflow_path.unlink(missing_ok=True)
