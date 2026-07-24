from __future__ import annotations

"""Recurrence arm: canonical audible cells and exact repeated instances."""

import math
from typing import Any, Mapping

import librosa
import numpy as np

from earcrate.reader.model import reader_observation_id, reader_sha256_json


class _ReaderUnionFind:
    def __init__(self, count: int):
        self.parent = list(range(count))

    def find(self, value: int) -> int:
        while self.parent[value] != value:
            self.parent[value] = self.parent[self.parent[value]]
            value = self.parent[value]
        return value

    def union(self, left: int, right: int) -> None:
        left = self.find(left)
        right = self.find(right)
        if left != right:
            self.parent[right] = left


def _reader_recurrence_unit(row: np.ndarray) -> np.ndarray:
    return row / (float(np.linalg.norm(row)) + 1e-12)


def _reader_cell_embedding(segment: np.ndarray, sample_rate: int) -> np.ndarray:
    mono = segment.mean(axis=1)
    if mono.size < 256 or float(np.max(np.abs(mono))) < 1e-8:
        return np.zeros(83, dtype=np.float64)
    n_fft = min(1024, 2 ** int(np.floor(np.log2(max(256, mono.size)))))
    hop = max(64, n_fft // 4)
    mel = librosa.feature.melspectrogram(y=mono, sr=sample_rate, n_fft=n_fft, hop_length=hop, n_mels=32, power=2)
    decibels = librosa.power_to_db(mel + 1e-12, ref=np.max)
    chroma = librosa.feature.chroma_stft(y=mono, sr=sample_rate, n_fft=n_fft, hop_length=hop).mean(axis=1)
    onset = librosa.onset.onset_strength(y=mono, sr=sample_rate, n_fft=n_fft, hop_length=hop)
    rms = librosa.feature.rms(y=mono, frame_length=n_fft, hop_length=hop)[0]
    centroid = librosa.feature.spectral_centroid(y=mono, sr=sample_rate, n_fft=n_fft, hop_length=hop)
    value = np.r_[
        decibels.mean(axis=1),
        decibels.std(axis=1),
        chroma,
        [
            float(rms.mean()),
            float(rms.std()),
            float(rms.max()) if rms.size else 0.0,
            float(onset.mean()) if onset.size else 0.0,
            float(onset.max()) if onset.size else 0.0,
            float(centroid.mean() / sample_rate),
            float(np.max(np.abs(mono))),
        ],
    ]
    return _reader_recurrence_unit(np.nan_to_num(value).astype(np.float64))


def reader_recurrence_arm(
    layers: Mapping[str, np.ndarray],
    pulse: Mapping[str, Any],
    sample_rate: int,
    body: Mapping[str, Any],
    persona: Mapping[str, Any],
) -> dict[str, Any]:
    beats = np.asarray(pulse["beats"], dtype=np.float64)
    duration = float(body["duration_seconds"])
    boundaries = list(float(value) for value in beats)
    if duration - boundaries[-1] > 0.15:
        boundaries.append(duration)
    phrase_beats = int(pulse["phrase_beats"])
    cells = []
    observations = []
    body_sha = str(body["body_sha256"])
    for layer_name in sorted(layers):
        audio = layers[layer_name]
        for beat_index in range(len(boundaries) - 1):
            start_seconds = boundaries[beat_index]
            end_seconds = boundaries[beat_index + 1]
            if end_seconds - start_seconds < 0.08:
                continue
            start_frame = max(0, int(round(start_seconds * sample_rate)))
            end_frame = min(int(body["frames"]), int(round(end_seconds * sample_rate)))
            segment = audio[start_frame:end_frame]
            rms = float(np.sqrt(np.mean(segment**2) + 1e-12))
            payload = {
                "layer": layer_name,
                "beat_index": int(beat_index),
                "phrase_index": int(beat_index // phrase_beats),
                "phase_index": int(beat_index % phrase_beats),
                "rms": rms,
            }
            observation_id = reader_observation_id(
                body_sha,
                "recurrence",
                "beat_aligned_layer_cell",
                start_frame,
                end_frame,
                payload,
            )
            observations.append(
                {
                    "observation_id": observation_id,
                    "body_sha256": body_sha,
                    "arm": "recurrence",
                    "kind": "beat_aligned_layer_cell",
                    "start_frame": start_frame,
                    "end_frame": end_frame,
                    "payload": payload,
                    "confidence": 1.0,
                }
            )
            cells.append(
                {
                    "cell_index": len(cells),
                    "observation_id": observation_id,
                    **payload,
                    "start_seconds": start_seconds,
                    "end_seconds": end_seconds,
                    "start_frame": start_frame,
                    "end_frame": end_frame,
                    "embedding": _reader_cell_embedding(segment, sample_rate),
                }
            )

    policy = persona.get("recurrence") or {}
    phase_prior = float(policy.get("same_phase_prior") or 0.08)
    same_phase_threshold = float(policy.get("same_phase_minimum_cosine") or 0.80)
    cross_phase_threshold = float(policy.get("cross_phase_minimum_cosine") or 0.93)
    medoid_threshold = float(policy.get("minimum_cluster_medoid_cosine") or 0.76)
    clusters: list[list[int]] = []
    for layer_name in sorted(layers):
        indices = [index for index, cell in enumerate(cells) if cell["layer"] == layer_name]
        union = _ReaderUnionFind(len(indices))
        similarity = np.eye(len(indices), dtype=np.float64)
        for left in range(len(indices)):
            for right in range(left + 1, len(indices)):
                first = cells[indices[left]]
                second = cells[indices[right]]
                raw = float(np.dot(first["embedding"], second["embedding"]))
                similarity[left, right] = similarity[right, left] = raw + (
                    phase_prior if first["phase_index"] == second["phase_index"] else 0.0
                )
        for left in range(len(indices)):
            order = np.argsort(similarity[left])[::-1]
            for right in order:
                if right == left:
                    continue
                first = cells[indices[left]]
                second = cells[indices[right]]
                if first["phrase_index"] == second["phrase_index"]:
                    continue
                raw = float(np.dot(first["embedding"], second["embedding"]))
                threshold = same_phase_threshold if first["phase_index"] == second["phase_index"] else cross_phase_threshold
                if raw < threshold:
                    continue
                reciprocal_left = set(np.argsort(similarity[left])[-4:])
                reciprocal_right = set(np.argsort(similarity[right])[-4:])
                if right in reciprocal_left and left in reciprocal_right:
                    union.union(left, right)
                break
        groups: dict[int, list[int]] = {}
        for local_index in range(len(indices)):
            groups.setdefault(union.find(local_index), []).append(indices[local_index])
        for members in groups.values():
            embeddings = np.stack([cells[index]["embedding"] for index in members])
            pairwise = embeddings @ embeddings.T
            medoid = members[int(np.argmax(pairwise.mean(axis=1)))]
            accepted = [
                index for index in members if float(np.dot(cells[index]["embedding"], cells[medoid]["embedding"])) >= medoid_threshold
            ]
            rejected = [index for index in members if index not in accepted]
            if accepted:
                clusters.append(accepted)
            clusters.extend([[index] for index in rejected])
    clusters.sort(key=lambda values: (str(cells[values[0]]["layer"]), min(float(cells[index]["start_seconds"]) for index in values)))

    canonical_events = []
    instances = []
    recurrence_edges = []
    execution_map = []
    for cluster_index, members in enumerate(clusters):
        embeddings = np.stack([cells[index]["embedding"] for index in members])
        pairwise = embeddings @ embeddings.T
        medoid = members[int(np.argmax(pairwise.mean(axis=1)))]
        event_payload = {
            "layer": cells[medoid]["layer"],
            "member_observation_ids": sorted(cells[index]["observation_id"] for index in members),
        }
        event_id = "event_" + reader_sha256_json(event_payload)[:20]
        canonical_events.append(
            {
                "canonical_event_id": event_id,
                "kind": "recurrent_cell" if len(members) > 1 else "unique_cell",
                "layer": cells[medoid]["layer"],
                "instance_count": len(members),
                "prototype_observation_id": cells[medoid]["observation_id"],
                "prototype_start_frame": int(cells[medoid]["start_frame"]),
                "prototype_end_frame": int(cells[medoid]["end_frame"]),
                "phase_indices": sorted(set(int(cells[index]["phase_index"]) for index in members)),
            }
        )
        for index in members:
            cell = cells[index]
            instance_id = event_id + ":" + cell["observation_id"][-12:]
            alternatives = [value for value in members if value != index]
            if alternatives:
                execution_prototype = max(
                    alternatives,
                    key=lambda value: float(np.dot(cell["embedding"], cells[value]["embedding"])),
                )
                execution_source = cells[execution_prototype]
                same_time_source_used = False
            else:
                execution_prototype = index
                execution_source = cell
                same_time_source_used = True
            instances.append(
                {
                    "instance_id": instance_id,
                    "canonical_event_id": event_id,
                    "layer": cell["layer"],
                    "start_frame": int(cell["start_frame"]),
                    "end_frame": int(cell["end_frame"]),
                    "start_seconds": float(cell["start_seconds"]),
                    "end_seconds": float(cell["end_seconds"]),
                    "beat_index": int(cell["beat_index"]),
                    "phrase_index": int(cell["phrase_index"]),
                    "phase_index": int(cell["phase_index"]),
                    "observation_ids": [cell["observation_id"]],
                    "rms": float(cell["rms"]),
                    "execution_prototype_observation_id": execution_source["observation_id"],
                    "execution_prototype_start_frame": int(execution_source["start_frame"]),
                    "execution_prototype_end_frame": int(execution_source["end_frame"]),
                    "same_time_source_used": same_time_source_used,
                }
            )
            execution_map.append(
                {
                    "instance_id": instance_id,
                    "target_cell_index": int(index),
                    "prototype_cell_index": int(execution_prototype),
                    "audible": bool(alternatives),
                }
            )
            if execution_prototype != index:
                recurrence_edges.append(
                    {
                        "from_observation_id": execution_source["observation_id"],
                        "to_observation_id": cell["observation_id"],
                        "canonical_event_id": event_id,
                        "cosine_similarity": float(np.dot(cell["embedding"], execution_source["embedding"])),
                    }
                )
    return {
        "arm": "recurrence",
        "cells": cells,
        "clusters": clusters,
        "canonical_events": canonical_events,
        "instances": instances,
        "recurrence_edges": recurrence_edges,
        "execution_map": execution_map,
        "observations": observations,
        "diagnostics": {
            "cell_count": len(cells),
            "canonical_event_count": len(canonical_events),
            "recurrent_event_count": sum(int(row["instance_count"]) > 1 for row in canonical_events),
            "recurrent_instance_count": sum(int(row["instance_count"]) for row in canonical_events if int(row["instance_count"]) > 1),
            "same_time_audible_instance_count": sum(
                bool(row["same_time_source_used"]) and len([value for value in canonical_events if value["canonical_event_id"] == row["canonical_event_id"] and int(value["instance_count"]) > 1]) > 0
                for row in instances
            ),
        },
    }


__all__ = ["reader_recurrence_arm"]
