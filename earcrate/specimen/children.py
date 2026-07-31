from __future__ import annotations

"""Score-side compiler for the external ``children_v1`` acceptance specimen.

The score adapter consumes already-custodied evidence: the vector-score extraction,
its exact reconstructed MIDI, printed annotations, the proof receipt, and the
MixScore execution evidence. It does not read or infer from the commercial recording.
That separation is the point of the specimen gate.
"""

import json
from copy import deepcopy
from pathlib import Path
from typing import Any, Mapping, Sequence

from earcrate.midi.codec import midi_read
from earcrate.midi.render import midi_compile_note_spans
from earcrate.mix.model import mixscore_load
from earcrate.music.model import MusicHarmonyFrame, music_make_event

from .model import (
    FORM_GRAPH_SCHEMA_VERSION,
    OBSERVATION_LEDGER_SCHEMA_VERSION,
    PERFORMANCE_PATH_SCHEMA_VERSION,
    SCORE_ANSWER_KEY_SCHEMA_VERSION,
    SpecimenError,
    specimen_bind_artifacts,
    specimen_make_observation,
    specimen_normalize_manifest,
    specimen_read_json,
    specimen_seal_form_graph,
    specimen_seal_observation_ledger,
    specimen_seal_performance_path,
    specimen_seal_score_answer_key,
    specimen_sha256_json,
    specimen_write_json_atomic,
)

EMBEDDED_SPECIMENS: dict[str, str] = {}
CHILDREN_SPECIMEN_ID = "children_v1"
CHILDREN_ADAPTER_VERSION = "1"
STEPS_PER_BEAT = 4
BEATS_PER_MEASURE = 4.0
STEPS_PER_MEASURE = int(STEPS_PER_BEAT * BEATS_PER_MEASURE)


_REQUIRED_SCORE_ARTIFACTS = (
    "score_pdf",
    "score_annotations",
    "score_extraction",
    "score_reconstruction_midi",
    "score_proof_receipt",
)
_REQUIRED_MIX_ARTIFACTS = ("mix_score", "mix_execution_ledger")


def children_repository_root() -> Path:
    here = Path(__file__).resolve()
    if here.name == "children.py" and here.parent.name == "specimen":
        return here.parents[2]
    return here.parent


def children_load_builtin(name: str = CHILDREN_SPECIMEN_ID) -> tuple[dict[str, Any], dict[str, Any]]:
    manifest_name = f"{name}.json"
    annotation_name = f"{name}.annotations.json"
    if manifest_name in EMBEDDED_SPECIMENS and annotation_name in EMBEDDED_SPECIMENS:
        return json.loads(EMBEDDED_SPECIMENS[manifest_name]), json.loads(EMBEDDED_SPECIMENS[annotation_name])
    root = children_repository_root()
    return (
        specimen_read_json(root / "specimens" / manifest_name),
        specimen_read_json(root / "specimens" / annotation_name),
    )


def children_load_bindings(path_or_value: str | Path | Mapping[str, Any]) -> dict[str, str]:
    if isinstance(path_or_value, Mapping):
        raw = dict(path_or_value)
    else:
        path = Path(path_or_value).expanduser().resolve()
        raw = specimen_read_json(path)
        if "bindings" in raw:
            raw = dict(raw["bindings"])
    return {str(key): str(value) for key, value in raw.items() if str(value or "").strip()}


def _validate_annotations(annotations: Mapping[str, Any], *, specimen_id: str, pdf_sha256: str) -> dict[str, Any]:
    if int(annotations.get("schema_version") or 0) != 1:
        raise SpecimenError("unsupported Children annotations schema")
    if str(annotations.get("kind") or "") != "earcrate_children_score_annotations":
        raise SpecimenError("unsupported Children annotations kind")
    if str(annotations.get("specimen_id") or "") != specimen_id:
        raise SpecimenError("Children annotations belong to another specimen")
    if str(annotations.get("source_pdf_sha256") or "") != pdf_sha256:
        raise SpecimenError("Children annotations belong to another score PDF")
    vocabulary = dict(annotations.get("chord_vocabulary") or {})
    if not vocabulary:
        raise SpecimenError("Children annotations require a chord vocabulary")
    symbols = [deepcopy(dict(row)) for row in annotations.get("chord_symbols") or []]
    if not symbols:
        raise SpecimenError("Children annotations require printed chord symbols")
    previous = 0
    for row in sorted(symbols, key=lambda value: int(value.get("printed_measure") or 0)):
        measure = int(row.get("printed_measure") or 0)
        label = str(row.get("label") or "")
        if measure <= previous or label not in vocabulary:
            raise SpecimenError("Children chord symbols must be ordered and use the declared vocabulary")
        previous = measure
        if int(row.get("page") or 0) not in {1, 2, 3, 4}:
            raise SpecimenError("Children chord symbol page must be 1..4")
        bbox = list(row.get("bbox") or [])
        if len(bbox) != 4:
            raise SpecimenError("Children chord symbol requires a four-coordinate bbox")
    markers = [deepcopy(dict(row)) for row in annotations.get("form_markers") or []]
    marker_ids = [str(row.get("marker_id") or "") for row in markers]
    if not all(marker_ids) or len(marker_ids) != len(set(marker_ids)):
        raise SpecimenError("Children form markers require unique nonempty marker_id values")
    required_marker_kinds = {
        "first_ending",
        "second_ending",
        "segno",
        "to_coda",
        "dal_segno_al_coda",
        "coda",
    }
    if not required_marker_kinds.issubset({str(row.get("kind") or "") for row in markers}):
        raise SpecimenError("Children form annotations omit printed navigation evidence")
    return deepcopy(dict(annotations))


def _track_names(ledger: Mapping[str, Any]) -> dict[int, str]:
    names: dict[int, str] = {}
    for index, track in enumerate(ledger.get("tracks") or []):
        track_index = int(track.get("track_index", index))
        for event in track.get("events") or []:
            message = event.get("message") or {}
            if str(message.get("type") or "") == "track_name":
                names[track_index] = str(message.get("name") or "")
                break
    return names


def _printed_note_rows(extraction: Mapping[str, Any]) -> list[dict[str, Any]]:
    measure_events = extraction.get("measure_events") or {}
    rows: list[dict[str, Any]] = []
    for occurrence in extraction.get("occurrences") or []:
        order = int(occurrence.get("order_index") or 0)
        measure = int(occurrence.get("measure") or 0)
        occurrence_index = int(occurrence.get("occurrence") or 0)
        start_beat = float(occurrence.get("start_beat") or 0.0)
        measure_value = measure_events.get(str(measure)) or {}
        for staff in ("treble", "bass"):
            for source_index, event in enumerate(measure_value.get(staff) or []):
                if str(event.get("kind") or "") != "note":
                    continue
                beat = float(event.get("beat") or 0.0)
                duration = float(event.get("duration") or 0.0)
                if duration <= 0.0:
                    raise SpecimenError(f"score note has nonpositive duration at printed measure {measure}")
                rows.append(
                    {
                        "order_index": order,
                        "printed_measure": measure,
                        "occurrence": occurrence_index,
                        "staff": staff,
                        "source_event_index": source_index,
                        "pitch": int(event.get("midi")),
                        "pitch_name": str(event.get("pitch") or ""),
                        "performed_beat": start_beat + beat,
                        "performed_step": int(round((start_beat + beat) * STEPS_PER_BEAT)),
                        "duration_steps": max(1, int(round(duration * STEPS_PER_BEAT))),
                        "source_duration_beats": duration,
                    }
                )
    rows.sort(
        key=lambda row: (
            row["performed_step"],
            0 if row["staff"] == "treble" else 1,
            row["pitch"],
            row["source_event_index"],
        )
    )
    return rows


def _midi_note_rows(ledger: Mapping[str, Any]) -> list[dict[str, Any]]:
    compiled = midi_compile_note_spans(ledger)
    names = _track_names(ledger)
    ppq = int(ledger.get("ticks_per_beat") or 0)
    if ppq <= 0:
        raise SpecimenError("exact MIDI ledger has no positive ticks_per_beat")
    rows: list[dict[str, Any]] = []
    for span in compiled.get("note_spans") or []:
        track_index = int(span.get("track_index") or 0)
        track_name = str(span.get("track_name") or names.get(track_index) or "")
        rows.append(
            {
                "midi_event_id": str(span.get("event_id") or ""),
                "track_index": track_index,
                "track_name": track_name,
                "pitch": int(span.get("note") or 0),
                "velocity": int(span.get("velocity") or 0),
                "start_tick": int(span.get("start_tick") or 0),
                "end_tick": int(span.get("end_tick") or 0),
                "performed_step": int(round(int(span.get("start_tick") or 0) / ppq * STEPS_PER_BEAT)),
                "midi_duration_steps": max(
                    1,
                    int(round((int(span.get("end_tick") or 0) - int(span.get("start_tick") or 0)) / ppq * STEPS_PER_BEAT)),
                ),
            }
        )
    rows.sort(
        key=lambda row: (
            row["performed_step"],
            0 if row["track_name"] == "Right Hand" else 1,
            row["pitch"],
            row["midi_event_id"],
        )
    )
    return rows


def _match_score_to_midi(
    score_rows: Sequence[Mapping[str, Any]],
    midi_rows: Sequence[Mapping[str, Any]],
) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    if len(score_rows) != len(midi_rows):
        raise SpecimenError(f"score/MIDI note count mismatch: score={len(score_rows)}, MIDI={len(midi_rows)}")
    matches: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for index, (score, midi) in enumerate(zip(score_rows, midi_rows)):
        expected_track = "Right Hand" if str(score["staff"]) == "treble" else "Left Hand"
        failures = []
        if int(score["pitch"]) != int(midi["pitch"]):
            failures.append(f"pitch {score['pitch']} != {midi['pitch']}")
        if int(score["performed_step"]) != int(midi["performed_step"]):
            failures.append(f"step {score['performed_step']} != {midi['performed_step']}")
        if str(midi["track_name"]) != expected_track:
            failures.append(f"track {midi['track_name']!r} != {expected_track!r}")
        if failures:
            raise SpecimenError(f"score/MIDI note mismatch at sorted index {index}: " + "; ".join(failures))
        matches.append((dict(score), dict(midi)))
    return matches


def _edge_kind(source_measure: int, target_measure: int) -> str:
    pair = (int(source_measure), int(target_measure))
    special = {
        (8, 5): "repeat",
        (7, 9): "alternate_ending",
        (17, 10): "repeat",
        (17, 18): "repeat_exit",
        (61, 54): "repeat",
        (59, 62): "alternate_ending",
        (64, 34): "dal_segno",
        (52, 65): "to_coda",
    }
    if pair in special:
        return special[pair]
    if target_measure == source_measure + 1:
        return "sequential"
    return "navigation"


def _build_form(
    *,
    specimen_id: str,
    occurrences: Sequence[Mapping[str, Any]],
    annotations: Mapping[str, Any],
    marker_observation_ids: Mapping[str, str],
) -> tuple[dict[str, Any], dict[str, Any]]:
    markers_by_measure: dict[int, list[str]] = {}
    marker_ids_by_measure: dict[int, list[str]] = {}
    for row in annotations.get("form_markers") or []:
        measure = int(row["printed_measure"])
        markers_by_measure.setdefault(measure, []).append(str(row["kind"]))
        marker_ids_by_measure.setdefault(measure, []).append(str(marker_observation_ids[str(row["marker_id"])]))
    nodes = [
        {
            "node_id": f"measure_{measure:03d}",
            "printed_measure": measure,
            "beats": BEATS_PER_MEASURE,
            "markers": markers_by_measure.get(measure, []),
            "metadata": {
                "marker_observation_ids": marker_ids_by_measure.get(measure, []),
            },
        }
        for measure in range(1, 70)
    ]
    pairs: list[tuple[int, int]] = []
    for index in range(1, len(occurrences)):
        pair = (int(occurrences[index - 1]["measure"]), int(occurrences[index]["measure"]))
        if pair not in pairs:
            pairs.append(pair)
    edges: list[dict[str, Any]] = []
    for source, target in pairs:
        kind = _edge_kind(source, target)
        evidence: list[str] = []
        if (source, target) == (8, 5):
            evidence = marker_ids_by_measure.get(8, [])
        elif (source, target) == (7, 9):
            evidence = marker_ids_by_measure.get(9, [])
        elif (source, target) == (64, 34):
            evidence = [*marker_ids_by_measure.get(64, []), *marker_ids_by_measure.get(34, [])]
        elif (source, target) == (52, 65):
            evidence = [*marker_ids_by_measure.get(52, []), *marker_ids_by_measure.get(65, [])]
        edge_id = f"form_edge_{source:03d}_{target:03d}_{kind}"
        edges.append(
            {
                "edge_id": edge_id,
                "from_node": f"measure_{source:03d}",
                "to_node": f"measure_{target:03d}",
                "edge_kind": kind,
                "priority": 100 if kind in {"dal_segno", "to_coda"} else 10,
                "guard": {},
                "actions": [],
                "evidence_observation_ids": sorted(set(evidence)),
            }
        )
    edges.append(
        {
            "edge_id": "form_edge_069_terminal",
            "from_node": "measure_069",
            "to_node": "",
            "edge_kind": "terminal",
            "priority": 100,
            "guard": {},
            "actions": [{"kind": "stop"}],
            "evidence_observation_ids": [],
        }
    )
    graph = specimen_seal_form_graph(
        {
            "schema_version": FORM_GRAPH_SCHEMA_VERSION,
            "kind": "earcrate_form_graph",
            "specimen_id": specimen_id,
            "entry_node": "measure_001",
            "nodes": nodes,
            "edges": edges,
            "repeat_regions": deepcopy(list(annotations.get("repeat_regions") or [])),
            "metadata": {
                "source": "printed navigation plus score extraction occurrence path",
                "performed_path_is_separate": True,
            },
        }
    )
    edge_by_pair = {
        (int(row["from_node"].split("_")[-1]), int(row["to_node"].split("_")[-1])): str(row["edge_id"])
        for row in graph["edges"]
        if row["to_node"]
    }
    path_rows = []
    for index, raw in enumerate(occurrences):
        measure = int(raw["measure"])
        row = {
            "order_index": int(raw["order_index"]),
            "printed_measure": measure,
            "occurrence": int(raw["occurrence"]),
            "start_beat": float(raw["start_beat"]),
            "beats": BEATS_PER_MEASURE,
            "via_edge_id": "",
        }
        if index:
            previous = int(occurrences[index - 1]["measure"])
            row["via_edge_id"] = edge_by_pair[(previous, measure)]
        path_rows.append(row)
    path = specimen_seal_performance_path(
        {
            "schema_version": PERFORMANCE_PATH_SCHEMA_VERSION,
            "kind": "earcrate_performance_path",
            "specimen_id": specimen_id,
            "form_graph_sha256": graph["form_graph_sha256"],
            "occurrences": path_rows,
            "metadata": deepcopy(dict(annotations.get("performance_path_interpretation") or {})),
        },
        graph,
    )
    return graph, path


def _harmony_for_printed_measure(
    annotations: Mapping[str, Any],
) -> dict[int, dict[str, Any]]:
    symbols = sorted(
        [dict(row) for row in annotations.get("chord_symbols") or []],
        key=lambda row: int(row["printed_measure"]),
    )
    vocabulary = dict(annotations["chord_vocabulary"])
    result: dict[int, dict[str, Any]] = {}
    current: dict[str, Any] | None = None
    index = 0
    for measure in range(1, 70):
        while index < len(symbols) and int(symbols[index]["printed_measure"]) == measure:
            current = symbols[index]
            index += 1
        if current is None:
            raise SpecimenError("Children harmony has no label at the first printed measure")
        label = str(current["label"])
        result[measure] = {
            "label": label,
            "printed_symbol_measure": int(current["printed_measure"]),
            "symbol_page": int(current["page"]),
            "symbol_bbox": list(current["bbox"]),
            **deepcopy(dict(vocabulary[label])),
        }
    return result


def _build_harmony_frames(
    performance_path: Mapping[str, Any],
    printed_harmony: Mapping[int, Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    per_measure: list[dict[str, Any]] = []
    for row in performance_path["occurrences"]:
        measure = int(row["printed_measure"])
        harmony = dict(printed_harmony[measure])
        per_measure.append(
            {
                "performed_measure_index": int(row["order_index"]),
                "printed_measure": measure,
                "occurrence": int(row["occurrence"]),
                "start_step": int(round(float(row["start_beat"]) * STEPS_PER_BEAT)),
                "end_step": int(round((float(row["start_beat"]) + float(row["beats"])) * STEPS_PER_BEAT)),
                **harmony,
            }
        )
    frames: list[dict[str, Any]] = []
    for row in per_measure:
        if frames and frames[-1]["label"] == row["label"] and int(frames[-1]["end_step"]) == int(row["start_step"]):
            frames[-1]["end_step"] = int(row["end_step"])
            frames[-1]["performed_measure_indices"].append(int(row["performed_measure_index"]))
            frames[-1]["printed_measures"].append(int(row["printed_measure"]))
            continue
        frame = MusicHarmonyFrame(
            start_step=int(row["start_step"]),
            end_step=int(row["end_step"]),
            root_pc=int(row["root_pc"]),
            pitch_classes=tuple(int(value) for value in row["pitch_classes"]),
            stable_pitch_classes=tuple(int(value) for value in row["stable_pitch_classes"]),
            bass_pitch_classes=tuple(int(value) for value in row["bass_pitch_classes"]),
            label=str(row["label"]),
            function=str(row["function"]),
        ).to_dict()
        frame["performed_measure_indices"] = [int(row["performed_measure_index"])]
        frame["printed_measures"] = [int(row["printed_measure"])]
        frame["source_symbol_measure"] = int(row["printed_symbol_measure"])
        frames.append(frame)
    return frames, per_measure


def _validate_mix_evidence(
    *,
    mix_score_path: Path,
    mix_ledger_path: Path,
    expected: Mapping[str, Any],
) -> dict[str, Any]:
    sealed_score, _ = mixscore_load(mix_score_path)
    ledger = specimen_read_json(mix_ledger_path)
    if int(ledger.get("schema_version") or 0) != 1 or str(ledger.get("kind") or "") != "earcrate_mix_execution_ledger":
        raise SpecimenError("Children mix execution ledger has an unsupported schema")
    selected = int(ledger.get("selected_event_count") or 0)
    executed = int(ledger.get("executed_event_count") or 0)
    refused = int(ledger.get("refused_event_count") or 0)
    if selected != len(sealed_score["events"]):
        raise SpecimenError("Children MixScore event count disagrees with its execution ledger")
    if selected != int(expected["mix_selected_event_count"]):
        raise SpecimenError("Children MixScore selected-event count disagrees with the specimen manifest")
    if executed != int(expected["mix_executed_event_count"]) or refused != int(expected["mix_refused_event_count"]):
        raise SpecimenError("Children MixScore execution counts disagree with the specimen manifest")
    rows = list(ledger.get("events") or [])
    if len(rows) != selected or any(str(row.get("status") or "") != "executed" for row in rows):
        raise SpecimenError("Children MixScore does not account for every selected operation")
    score_ops = [str(row["op"]) for row in sealed_score["events"]]
    ledger_ops = [str(row.get("op") or "") for row in rows]
    if score_ops != ledger_ops:
        raise SpecimenError("Children MixScore operation order disagrees with its execution ledger")
    reconciliation = float(ledger.get("stem_reconciliation_max_abs") or 0.0)
    if reconciliation != float(expected["mix_stem_reconciliation_max_abs"]):
        raise SpecimenError("Children MixScore stem reconciliation disagrees with the specimen manifest")
    return {
        "score_sha256": str(sealed_score["score_sha256"]),
        "selected_event_count": selected,
        "executed_event_count": executed,
        "refused_event_count": refused,
        "operations": sorted(set(score_ops)),
        "stem_reconciliation_max_abs": reconciliation,
        "master_pcm_f32le_sha256": str(ledger.get("master_pcm_f32le_sha256") or ""),
        "stem_pcm_f32le_sha256": deepcopy(dict(ledger.get("stem_pcm_f32le_sha256") or {})),
    }


def children_compile_score_branch(
    *,
    manifest: Mapping[str, Any],
    annotations: Mapping[str, Any],
    bindings: Mapping[str, str | Path],
    output_dir: str | Path,
    repository_root: str | Path | None = None,
    overwrite: bool = False,
) -> dict[str, Any]:
    normalized_manifest = specimen_normalize_manifest(manifest)
    specimen_id = str(normalized_manifest["specimen_id"])
    if specimen_id != CHILDREN_SPECIMEN_ID:
        raise SpecimenError(f"Children adapter refuses another specimen: {specimen_id}")
    destination = Path(output_dir).expanduser().resolve()
    if destination.exists() and any(destination.iterdir()) and not overwrite:
        raise FileExistsError(f"refusing to overwrite nonempty Children score output: {destination}")
    destination.mkdir(parents=True, exist_ok=True)

    bound = specimen_bind_artifacts(
        normalized_manifest,
        bindings,
        repository_root=repository_root or children_repository_root(),
    )
    for artifact_id in [*_REQUIRED_SCORE_ARTIFACTS, *_REQUIRED_MIX_ARTIFACTS]:
        if artifact_id not in bound:
            raise SpecimenError(f"Children score gate requires artifact: {artifact_id}")
    annotation_value = _validate_annotations(
        annotations,
        specimen_id=specimen_id,
        pdf_sha256=str(bound["score_pdf"]["sha256"]),
    )
    if str(bound["score_annotations"]["sha256"]) != str(
        next(row["expected_sha256"] for row in normalized_manifest["artifacts"] if row["artifact_id"] == "score_annotations")
    ):
        raise SpecimenError("Children score annotation identity is not manifest-bound")

    extraction = specimen_read_json(bound["score_extraction"]["path"])
    proof = specimen_read_json(bound["score_proof_receipt"]["path"])
    expected = dict(normalized_manifest["expected"])
    score_meta = dict(extraction.get("score") or {})
    if int(score_meta.get("printed_measure_count") or 0) != int(expected["printed_measure_count"]):
        raise SpecimenError("Children printed-measure count disagrees with manifest")
    if int(score_meta.get("linearized_measure_count") or 0) != int(expected["performed_measure_count"]):
        raise SpecimenError("Children performed-measure count disagrees with manifest")
    if float(score_meta.get("tempo_bpm") or 0.0) != float(expected["tempo_bpm"]):
        raise SpecimenError("Children score tempo disagrees with manifest")
    if int(proof.get("printed_measures") or 0) != int(expected["printed_measure_count"]) or int(proof.get("linearized_measures") or 0) != int(expected["performed_measure_count"]):
        raise SpecimenError("Children proof receipt measure counts disagree with manifest")

    midi_ledger = midi_read(Path(bound["score_reconstruction_midi"]["path"]))
    score_rows = _printed_note_rows(extraction)
    midi_rows = _midi_note_rows(midi_ledger)
    matches = _match_score_to_midi(score_rows, midi_rows)
    if len(matches) != int(expected["midi_note_count"]):
        raise SpecimenError("Children note count disagrees with manifest")
    track_names = sorted({str(row["track_name"]) for row in midi_rows if str(row["track_name"])})
    if track_names != sorted(str(value) for value in expected["midi_instrument_names"]):
        raise SpecimenError(f"Children MIDI instruments disagree with manifest: {track_names}")

    score_inputs = [
        {
            "artifact_id": artifact_id,
            "branch": "score",
            "sha256": str(bound[artifact_id]["sha256"]),
            "ancestor_branches": ["score"],
        }
        for artifact_id in _REQUIRED_SCORE_ARTIFACTS
    ]
    observations: list[dict[str, Any]] = []
    provider = str(annotation_value["provider"]["name"])
    provider_version = str(annotation_value["provider"]["version"])
    observations.append(
        specimen_make_observation(
            specimen_id=specimen_id,
            branch="score",
            kind="tempo",
            address={"page": 1, "scope": "score"},
            value={"bpm": float(annotation_value["tempo"]["bpm"])},
            confidence=float(annotation_value["tempo"]["confidence"]),
            source_artifact_ids=["score_pdf", "score_annotations"],
            provider=provider,
            provider_version=provider_version,
            raw_evidence={"bbox": list(annotation_value["tempo"]["bbox"])},
        )
    )
    observations.append(
        specimen_make_observation(
            specimen_id=specimen_id,
            branch="score",
            kind="meter",
            address={"page": 1, "scope": "score"},
            value={"numerator": 4, "denominator": 4},
            confidence=float(annotation_value["meter"]["confidence"]),
            source_artifact_ids=["score_pdf", "score_annotations"],
            provider=provider,
            provider_version=provider_version,
        )
    )
    observations.append(
        specimen_make_observation(
            specimen_id=specimen_id,
            branch="score",
            kind="key_signature",
            address={"page": 1, "scope": "score"},
            value=deepcopy(dict(annotation_value["key_signature"])),
            confidence=float(annotation_value["key_signature"]["confidence"]),
            source_artifact_ids=["score_pdf", "score_annotations"],
            provider=provider,
            provider_version=provider_version,
        )
    )

    marker_observation_ids: dict[str, str] = {}
    for marker in annotation_value["form_markers"]:
        observation = specimen_make_observation(
            specimen_id=specimen_id,
            branch="score",
            kind="form_instruction",
            address={"page": int(marker["page"]), "printed_measure": int(marker["printed_measure"])},
            value={"marker_id": str(marker["marker_id"]), "kind": str(marker["kind"]), "label": str(marker["label"])},
            confidence=1.0,
            source_artifact_ids=["score_pdf", "score_annotations"],
            provider=provider,
            provider_version=provider_version,
            raw_evidence={"bbox": list(marker["bbox"])},
        )
        observations.append(observation)
        marker_observation_ids[str(marker["marker_id"])] = str(observation["observation_id"])

    printed_harmony = _harmony_for_printed_measure(annotation_value)
    for symbol in annotation_value["chord_symbols"]:
        harmony = printed_harmony[int(symbol["printed_measure"])]
        observations.append(
            specimen_make_observation(
                specimen_id=specimen_id,
                branch="score",
                kind="printed_harmony",
                address={"page": int(symbol["page"]), "printed_measure": int(symbol["printed_measure"])},
                value={
                    "label": str(symbol["label"]),
                    "root_pc": int(harmony["root_pc"]),
                    "pitch_classes": list(harmony["pitch_classes"]),
                    "stable_pitch_classes": list(harmony["stable_pitch_classes"]),
                    "bass_pitch_classes": list(harmony["bass_pitch_classes"]),
                    "function": str(harmony["function"]),
                },
                confidence=float(symbol["confidence"]),
                source_artifact_ids=["score_pdf", "score_annotations"],
                provider=provider,
                provider_version=provider_version,
                raw_evidence={"bbox": list(symbol["bbox"])},
            )
        )

    graph, performance_path = _build_form(
        specimen_id=specimen_id,
        occurrences=list(extraction.get("occurrences") or []),
        annotations=annotation_value,
        marker_observation_ids=marker_observation_ids,
    )
    harmony_frames, performed_harmony = _build_harmony_frames(performance_path, printed_harmony)
    for row in performed_harmony:
        observations.append(
            specimen_make_observation(
                specimen_id=specimen_id,
                branch="score",
                kind="performed_harmony",
                address={
                    "performed_measure_index": int(row["performed_measure_index"]),
                    "printed_measure": int(row["printed_measure"]),
                    "occurrence": int(row["occurrence"]),
                },
                value={
                    "performed_measure_index": int(row["performed_measure_index"]),
                    "start_step": int(row["start_step"]),
                    "end_step": int(row["end_step"]),
                    "label": str(row["label"]),
                    "root_pc": int(row["root_pc"]),
                    "pitch_classes": list(row["pitch_classes"]),
                    "function": str(row["function"]),
                },
                confidence=1.0,
                source_artifact_ids=["score_extraction", "score_annotations"],
                provider="children_form_harmony_expander",
                provider_version=CHILDREN_ADAPTER_VERSION,
                raw_evidence={"source_symbol_measure": int(row["printed_symbol_measure"])},
            )
        )

    note_observations: list[dict[str, Any]] = []
    answer_events: list[dict[str, Any]] = []
    for score_note, midi_note in matches:
        voice_id = "right_hand" if score_note["staff"] == "treble" else "left_hand"
        role = "melody_harmony" if voice_id == "right_hand" else "bass_harmony"
        observation = specimen_make_observation(
            specimen_id=specimen_id,
            branch="score",
            kind="performed_note",
            address={
                "performed_measure_index": int(score_note["order_index"]),
                "printed_measure": int(score_note["printed_measure"]),
                "occurrence": int(score_note["occurrence"]),
                "staff": str(score_note["staff"]),
                "source_event_index": int(score_note["source_event_index"]),
            },
            value={
                "voice_id": voice_id,
                "role": role,
                "pitch": int(score_note["pitch"]),
                "pitch_name": str(score_note["pitch_name"]),
                "performed_step": int(score_note["performed_step"]),
                "duration_steps": int(score_note["duration_steps"]),
                "midi_event_id": str(midi_note["midi_event_id"]),
                "midi_velocity": int(midi_note["velocity"]),
                "midi_duration_steps": int(midi_note["midi_duration_steps"]),
            },
            confidence=1.0,
            source_artifact_ids=["score_extraction", "score_reconstruction_midi"],
            provider="children_vector_score_midi_reconciler",
            provider_version=CHILDREN_ADAPTER_VERSION,
            raw_evidence={
                "start_tick": int(midi_note["start_tick"]),
                "end_tick": int(midi_note["end_tick"]),
            },
        )
        note_observations.append(observation)
        event = music_make_event(
            voice_id=voice_id,
            role=role,
            start_step=int(score_note["performed_step"]),
            duration_steps=int(score_note["duration_steps"]),
            pitch=int(score_note["pitch"]),
            velocity=max(1, int(midi_note["velocity"])),
            function="score_statement",
            operator="authored_score",
            source_ids=[str(observation["observation_id"]), str(midi_note["midi_event_id"])],
            metadata={
                "printed_measure": int(score_note["printed_measure"]),
                "occurrence": int(score_note["occurrence"]),
                "performed_measure_index": int(score_note["order_index"]),
                "midi_gate_duration_steps": int(midi_note["midi_duration_steps"]),
            },
        )
        answer_events.append(event.to_dict())
    observations.extend(note_observations)

    score_ledger = specimen_seal_observation_ledger(
        {
            "schema_version": OBSERVATION_LEDGER_SCHEMA_VERSION,
            "kind": "earcrate_observation_ledger",
            "specimen_id": specimen_id,
            "branch": "score",
            "inputs": score_inputs,
            "observations": observations,
            "metadata": {
                "adapter": "children_vector_score_adapter_v1",
                "audio_branch_consulted": False,
                "commercial_recording_consulted": False,
                "raw_score_reader": str((extraction.get("method") or {}).get("type") or ""),
            },
        }
    )
    answer_key = specimen_seal_score_answer_key(
        {
            "schema_version": SCORE_ANSWER_KEY_SCHEMA_VERSION,
            "kind": "earcrate_score_answer_key",
            "specimen_id": specimen_id,
            "score_ledger_sha256": score_ledger["ledger_sha256"],
            "form_graph_sha256": graph["form_graph_sha256"],
            "performance_path_sha256": performance_path["performance_path_sha256"],
            "midi_semantic_sha256": str(midi_ledger["semantic_sha256"]),
            "steps_per_beat": STEPS_PER_BEAT,
            "tempo_bpm": float(expected["tempo_bpm"]),
            "meter": deepcopy(dict(expected["meter"])),
            "key_signature": deepcopy(dict(expected["key_signature"])),
            "harmony_frames": harmony_frames,
            "events": answer_events,
            "source_counts": {
                "printed_measures": int(expected["printed_measure_count"]),
                "performed_measures": int(expected["performed_measure_count"]),
                "notes": len(answer_events),
                "printed_chord_symbols": len(annotation_value["chord_symbols"]),
                "harmony_frames": len(harmony_frames),
                "form_markers": len(annotation_value["form_markers"]),
            },
            "interpretive_limits": list(annotation_value.get("interpretive_limits") or []),
            "metadata": {
                "authority": "score-derived answer key; cross-modal accepted performance remains pending",
                "music_event_model": "earcrate.music.model.MusicEvent",
                "harmony_frame_model": "earcrate.music.model.MusicHarmonyFrame",
            },
        }
    )

    mix = _validate_mix_evidence(
        mix_score_path=Path(bound["mix_score"]["path"]),
        mix_ledger_path=Path(bound["mix_execution_ledger"]["path"]),
        expected=expected,
    )
    checks = {
        "score_artifact_custody": True,
        "score_midi_note_identity": True,
        "form_graph_path_complete": int(performance_path["performed_measure_count"]) == int(expected["performed_measure_count"]),
        "printed_harmony_canonicalized": len(annotation_value["chord_symbols"]) > 0 and len(harmony_frames) > 0,
        "mixscore_execution_complete": mix["selected_event_count"] == mix["executed_event_count"] and mix["refused_event_count"] == 0,
        "mixscore_stems_reconcile": float(mix["stem_reconciliation_max_abs"]) == 0.0,
        "audio_branch_consulted": False,
    }
    if not all(value for key, value in checks.items() if key != "audio_branch_consulted"):
        raise SpecimenError("Children score branch failed one or more exact checks")
    receipt = {
        "schema_version": 1,
        "kind": "earcrate_children_score_branch_receipt",
        "specimen_id": specimen_id,
        "manifest_sha256": normalized_manifest["manifest_sha256"],
        "score_ledger_sha256": score_ledger["ledger_sha256"],
        "form_graph_sha256": graph["form_graph_sha256"],
        "performance_path_sha256": performance_path["performance_path_sha256"],
        "answer_key_sha256": answer_key["answer_key_sha256"],
        "midi_semantic_sha256": str(midi_ledger["semantic_sha256"]),
        "mixscore": mix,
        "counts": deepcopy(answer_key["source_counts"]),
        "checks": checks,
        "branch_status": "passed",
        "cross_modal_status": "blocked",
        "cross_modal_blockers": [
            "independent reference recording is not bound",
            "audio ObservationLedger is not sealed",
            "score/audio convergence has not run",
        ],
    }
    receipt["receipt_sha256"] = specimen_sha256_json(receipt)

    files = {
        "manifest": destination / "specimen.manifest.bound.json",
        "score_ledger": destination / "score.observation-ledger.json",
        "form_graph": destination / "score.form-graph.json",
        "performance_path": destination / "score.performance-path.json",
        "answer_key": destination / "score.answer-key.json",
        "receipt": destination / "score.branch.receipt.json",
    }
    specimen_write_json_atomic(files["manifest"], normalized_manifest)
    specimen_write_json_atomic(files["score_ledger"], score_ledger)
    specimen_write_json_atomic(files["form_graph"], graph)
    specimen_write_json_atomic(files["performance_path"], performance_path)
    specimen_write_json_atomic(files["answer_key"], answer_key)
    specimen_write_json_atomic(files["receipt"], receipt)
    return {
        "ok": True,
        "complete": True,
        "specimen_id": specimen_id,
        "output_dir": str(destination),
        "files": {key: str(path) for key, path in files.items()},
        "receipt": receipt,
    }


__all__ = [
    "CHILDREN_SPECIMEN_ID",
    "CHILDREN_ADAPTER_VERSION",
    "children_load_builtin",
    "children_load_bindings",
    "children_compile_score_branch",
]
