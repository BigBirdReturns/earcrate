from __future__ import annotations

"""Canonical custody objects for cross-organ EarCrate specimens.

This layer coordinates existing organs. It does not parse notation, infer audio,
compose, render MIDI, build racks, or move source transports. It preserves the
identities and lineage required to prove that those organs operated on the same
musical specimen without contaminating one another's evidence branches.
"""

import hashlib
import json
import math
import os
import tempfile
from copy import deepcopy
from pathlib import Path
from typing import Any, Mapping, Sequence

SPECIMEN_MANIFEST_SCHEMA_VERSION = 1
OBSERVATION_LEDGER_SCHEMA_VERSION = 1
FORM_GRAPH_SCHEMA_VERSION = 1
PERFORMANCE_PATH_SCHEMA_VERSION = 1
SCORE_ANSWER_KEY_SCHEMA_VERSION = 1
CONVERGENCE_POLICY_SCHEMA_VERSION = 1
CONVERGENCE_REPORT_SCHEMA_VERSION = 1
BUFFALO_GATE_RECEIPT_SCHEMA_VERSION = 1

BRANCHES = (
    "score",
    "audio",
    "convergence",
    "performance",
    "review",
    "evolution",
)
BRANCH_ALLOWED_ANCESTORS: dict[str, frozenset[str]] = {
    "score": frozenset({"score"}),
    "audio": frozenset({"audio"}),
    "convergence": frozenset({"score", "audio", "convergence"}),
    "performance": frozenset({"score", "audio", "convergence", "performance"}),
    "review": frozenset({"performance", "review"}),
    "evolution": frozenset({"score", "audio", "convergence", "performance", "review", "evolution"}),
}
GATE_STATUSES = {"passed", "blocked", "failed", "not_run"}


class SpecimenError(ValueError):
    """Raised when specimen custody, lineage, or evidence is not provable."""


def specimen_jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise SpecimenError("specimen objects cannot contain non-finite numbers")
        return value
    if isinstance(value, Mapping):
        return {str(key): specimen_jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [specimen_jsonable(item) for item in value]
    if hasattr(value, "to_dict"):
        return specimen_jsonable(value.to_dict())
    if hasattr(value, "item"):
        return specimen_jsonable(value.item())
    return str(value)


def specimen_canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        specimen_jsonable(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def specimen_sha256_json(value: Any) -> str:
    return hashlib.sha256(specimen_canonical_json_bytes(value)).hexdigest()


def specimen_sha256_file(path: str | Path) -> str:
    source = Path(path)
    digest = hashlib.sha256()
    with source.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def specimen_read_json(path: str | Path) -> dict[str, Any]:
    source = Path(path).expanduser().resolve()
    try:
        value = json.loads(source.read_text(encoding="utf-8"))
    except Exception as exc:
        raise SpecimenError(f"cannot read specimen JSON {source}: {exc}") from exc
    if not isinstance(value, dict):
        raise SpecimenError(f"specimen JSON must contain an object: {source}")
    return value


def specimen_write_json_atomic(path: str | Path, value: Mapping[str, Any]) -> Path:
    target = Path(path).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(
        specimen_jsonable(dict(value)),
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
        allow_nan=False,
    ) + "\n"
    descriptor, raw_temp = tempfile.mkstemp(
        prefix=f".{target.name}.", suffix=".tmp", dir=str(target.parent)
    )
    temp = Path(raw_temp)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, target)
    finally:
        if temp.exists():
            temp.unlink()
    return target


def _text(value: Any, field: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise SpecimenError(f"{field} must be nonempty")
    return text


def _sha256(value: Any, field: str, *, optional: bool = False) -> str | None:
    text = str(value or "").strip().lower()
    if optional and not text:
        return None
    if len(text) != 64 or any(character not in "0123456789abcdef" for character in text):
        raise SpecimenError(f"{field} must be a lowercase SHA-256")
    return text


def _branch(value: Any, field: str = "branch") -> str:
    branch = _text(value, field).lower()
    if branch not in BRANCHES:
        raise SpecimenError(f"{field} must be one of {list(BRANCHES)}")
    return branch


def specimen_normalize_manifest(manifest: Mapping[str, Any]) -> dict[str, Any]:
    if int(manifest.get("schema_version") or 0) != SPECIMEN_MANIFEST_SCHEMA_VERSION:
        raise SpecimenError("unsupported specimen manifest schema")
    if str(manifest.get("kind") or "") != "earcrate_specimen_manifest":
        raise SpecimenError("unsupported specimen manifest kind")
    specimen_id = _text(manifest.get("specimen_id"), "specimen_id")
    artifacts: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, raw in enumerate(manifest.get("artifacts") or []):
        if not isinstance(raw, Mapping):
            raise SpecimenError(f"manifest artifact {index} is not an object")
        artifact_id = _text(raw.get("artifact_id"), f"artifact {index} artifact_id")
        if artifact_id in seen:
            raise SpecimenError(f"duplicate artifact_id: {artifact_id}")
        seen.add(artifact_id)
        status = str(raw.get("status") or "bound").strip().lower()
        if status not in {"bound", "unbound", "optional"}:
            raise SpecimenError(f"artifact {artifact_id} has unsupported status {status}")
        expected = _sha256(
            raw.get("expected_sha256"),
            f"artifact {artifact_id} expected_sha256",
            optional=status != "bound",
        )
        if status == "bound" and expected is None:
            raise SpecimenError(f"bound artifact {artifact_id} requires expected_sha256")
        artifacts.append(
            {
                "artifact_id": artifact_id,
                "branch": _branch(raw.get("branch"), f"artifact {artifact_id} branch"),
                "media_kind": _text(raw.get("media_kind"), f"artifact {artifact_id} media_kind"),
                "status": status,
                "required_for": sorted({str(value) for value in raw.get("required_for") or []}),
                "expected_sha256": expected,
                "path_hint": str(raw.get("path_hint") or ""),
                "repository_managed": bool(raw.get("repository_managed", False)),
                "metadata": deepcopy(dict(raw.get("metadata") or {})),
            }
        )
    if not artifacts:
        raise SpecimenError("specimen manifest requires artifacts")
    out = {
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
    out["manifest_sha256"] = specimen_sha256_json(out)
    return out


def specimen_validate_manifest(manifest: Mapping[str, Any]) -> None:
    normalized = specimen_normalize_manifest(manifest)
    supplied = str(manifest.get("manifest_sha256") or "")
    if supplied and supplied != normalized["manifest_sha256"]:
        raise SpecimenError("manifest_sha256 does not match specimen manifest")


def specimen_artifact_map(manifest: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    normalized = specimen_normalize_manifest(manifest)
    return {str(row["artifact_id"]): row for row in normalized["artifacts"]}


def specimen_bind_artifacts(
    manifest: Mapping[str, Any],
    bindings: Mapping[str, str | Path],
    *,
    repository_root: str | Path | None = None,
) -> dict[str, dict[str, Any]]:
    """Verify local artifacts against immutable manifest identities.

    Unbound and optional artifacts may remain absent. Every bound artifact must resolve
    either through ``bindings`` or, when explicitly repository-managed, its path hint.
    """

    normalized = specimen_normalize_manifest(manifest)
    root = Path(repository_root).expanduser().resolve() if repository_root else None
    resolved: dict[str, dict[str, Any]] = {}
    for row in normalized["artifacts"]:
        artifact_id = str(row["artifact_id"])
        raw_path = bindings.get(artifact_id)
        if raw_path in {None, ""} and row["repository_managed"] and root is not None:
            raw_path = root / str(row["path_hint"])
        if raw_path in {None, ""}:
            if row["status"] == "bound":
                raise SpecimenError(f"bound specimen artifact is not mapped: {artifact_id}")
            continue
        path = Path(raw_path).expanduser().resolve()
        if not path.is_file():
            raise SpecimenError(f"specimen artifact does not exist: {artifact_id}: {path}")
        actual = specimen_sha256_file(path)
        expected = row.get("expected_sha256")
        if expected and actual != expected:
            raise SpecimenError(
                f"specimen artifact identity changed: {artifact_id}: expected {expected}, found {actual}"
            )
        resolved[artifact_id] = {
            "artifact_id": artifact_id,
            "branch": str(row["branch"]),
            "media_kind": str(row["media_kind"]),
            "path": str(path),
            "sha256": actual,
            "ancestor_branches": [str(row["branch"])],
            "manifest_status": str(row["status"]),
        }
    return resolved


def specimen_make_observation(
    *,
    specimen_id: str,
    branch: str,
    kind: str,
    address: Mapping[str, Any],
    value: Any,
    confidence: float,
    source_artifact_ids: Sequence[str],
    provider: str,
    provider_version: str,
    raw_evidence: Mapping[str, Any] | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    branch = _branch(branch)
    number = float(confidence)
    if not math.isfinite(number) or not 0.0 <= number <= 1.0:
        raise SpecimenError("observation confidence must be in [0,1]")
    payload = {
        "specimen_id": _text(specimen_id, "specimen_id"),
        "branch": branch,
        "kind": _text(kind, "observation kind"),
        "address": deepcopy(dict(address)),
        "value": specimen_jsonable(value),
        "confidence": number,
        "source_artifact_ids": sorted({str(value) for value in source_artifact_ids}),
        "provider": _text(provider, "observation provider"),
        "provider_version": _text(provider_version, "observation provider_version"),
        "raw_evidence": deepcopy(dict(raw_evidence or {})),
        "metadata": deepcopy(dict(metadata or {})),
    }
    payload["observation_id"] = "observation_" + specimen_sha256_json(payload)[:24]
    return payload


def specimen_validate_branch_isolation(ledger: Mapping[str, Any]) -> None:
    branch = _branch(ledger.get("branch"), "ledger branch")
    allowed = BRANCH_ALLOWED_ANCESTORS[branch]
    bad: set[str] = set()
    for raw in ledger.get("inputs") or []:
        direct = _branch(raw.get("branch"), "ledger input branch")
        ancestors = {str(value) for value in raw.get("ancestor_branches") or [direct]}
        ancestors.add(direct)
        bad.update(ancestor for ancestor in ancestors if ancestor not in allowed)
    if bad:
        raise SpecimenError(
            f"{branch} branch lineage is tainted by forbidden ancestor branches: {sorted(bad)}"
        )
    declared = set(str(value) for value in ledger.get("allowed_ancestor_branches") or allowed)
    if declared != set(allowed):
        raise SpecimenError(
            f"{branch} branch allowed_ancestor_branches must be {sorted(allowed)}"
        )


def specimen_seal_observation_ledger(ledger: Mapping[str, Any]) -> dict[str, Any]:
    if int(ledger.get("schema_version") or OBSERVATION_LEDGER_SCHEMA_VERSION) != OBSERVATION_LEDGER_SCHEMA_VERSION:
        raise SpecimenError("unsupported observation ledger schema")
    if str(ledger.get("kind") or "earcrate_observation_ledger") != "earcrate_observation_ledger":
        raise SpecimenError("unsupported observation ledger kind")
    specimen_id = _text(ledger.get("specimen_id"), "ledger specimen_id")
    branch = _branch(ledger.get("branch"), "ledger branch")
    inputs: list[dict[str, Any]] = []
    for index, raw in enumerate(ledger.get("inputs") or []):
        if not isinstance(raw, Mapping):
            raise SpecimenError(f"ledger input {index} is not an object")
        direct = _branch(raw.get("branch"), f"ledger input {index} branch")
        inputs.append(
            {
                "artifact_id": _text(raw.get("artifact_id"), f"ledger input {index} artifact_id"),
                "branch": direct,
                "sha256": _sha256(raw.get("sha256"), f"ledger input {index} sha256"),
                "ancestor_branches": sorted({str(value) for value in raw.get("ancestor_branches") or [direct]}),
                "metadata": deepcopy(dict(raw.get("metadata") or {})),
            }
        )
    observations: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, raw in enumerate(ledger.get("observations") or []):
        if not isinstance(raw, Mapping):
            raise SpecimenError(f"observation {index} is not an object")
        row = deepcopy(dict(raw))
        observation_id = _text(row.get("observation_id"), f"observation {index} observation_id")
        if observation_id in seen:
            raise SpecimenError(f"duplicate observation_id: {observation_id}")
        seen.add(observation_id)
        if str(row.get("specimen_id") or "") != specimen_id:
            raise SpecimenError(f"observation {observation_id} belongs to another specimen")
        if _branch(row.get("branch"), f"observation {observation_id} branch") != branch:
            raise SpecimenError(f"observation {observation_id} belongs to another branch")
        confidence = float(row.get("confidence", -1.0))
        if not math.isfinite(confidence) or not 0.0 <= confidence <= 1.0:
            raise SpecimenError(f"observation {observation_id} confidence must be in [0,1]")
        observations.append(row)
    out = {
        "schema_version": OBSERVATION_LEDGER_SCHEMA_VERSION,
        "kind": "earcrate_observation_ledger",
        "specimen_id": specimen_id,
        "branch": branch,
        "allowed_ancestor_branches": sorted(BRANCH_ALLOWED_ANCESTORS[branch]),
        "inputs": sorted(inputs, key=lambda row: (row["artifact_id"], row["sha256"])),
        "observations": sorted(observations, key=lambda row: row["observation_id"]),
        "metadata": deepcopy(dict(ledger.get("metadata") or {})),
    }
    specimen_validate_branch_isolation(out)
    out["ledger_sha256"] = specimen_sha256_json(out)
    return out


def specimen_validate_observation_ledger(ledger: Mapping[str, Any]) -> None:
    sealed = specimen_seal_observation_ledger(ledger)
    supplied = str(ledger.get("ledger_sha256") or "")
    if supplied and supplied != sealed["ledger_sha256"]:
        raise SpecimenError("ledger_sha256 does not match observation ledger")


def specimen_seal_form_graph(graph: Mapping[str, Any]) -> dict[str, Any]:
    if int(graph.get("schema_version") or FORM_GRAPH_SCHEMA_VERSION) != FORM_GRAPH_SCHEMA_VERSION:
        raise SpecimenError("unsupported form graph schema")
    if str(graph.get("kind") or "earcrate_form_graph") != "earcrate_form_graph":
        raise SpecimenError("unsupported form graph kind")
    specimen_id = _text(graph.get("specimen_id"), "form graph specimen_id")
    nodes: list[dict[str, Any]] = []
    node_ids: set[str] = set()
    for index, raw in enumerate(graph.get("nodes") or []):
        node_id = _text(raw.get("node_id"), f"form node {index} node_id")
        if node_id in node_ids:
            raise SpecimenError(f"duplicate form node_id: {node_id}")
        node_ids.add(node_id)
        row = {
            "node_id": node_id,
            "printed_measure": int(raw.get("printed_measure") or 0),
            "beats": float(raw.get("beats") or 0.0),
            "markers": sorted({str(value) for value in raw.get("markers") or []}),
            "metadata": deepcopy(dict(raw.get("metadata") or {})),
        }
        if row["printed_measure"] <= 0 or row["beats"] <= 0.0:
            raise SpecimenError("form graph nodes require positive printed measures and beats")
        nodes.append(row)
    if not nodes:
        raise SpecimenError("form graph requires nodes")
    edges: list[dict[str, Any]] = []
    edge_ids: set[str] = set()
    for index, raw in enumerate(graph.get("edges") or []):
        edge_id = _text(raw.get("edge_id"), f"form edge {index} edge_id")
        if edge_id in edge_ids:
            raise SpecimenError(f"duplicate form edge_id: {edge_id}")
        edge_ids.add(edge_id)
        source = _text(raw.get("from_node"), f"form edge {edge_id} from_node")
        target = str(raw.get("to_node") or "")
        if source not in node_ids or (target and target not in node_ids):
            raise SpecimenError(f"form edge {edge_id} references an unknown node")
        edges.append(
            {
                "edge_id": edge_id,
                "from_node": source,
                "to_node": target,
                "edge_kind": _text(raw.get("edge_kind"), f"form edge {edge_id} edge_kind"),
                "priority": int(raw.get("priority") or 0),
                "guard": deepcopy(dict(raw.get("guard") or {})),
                "actions": deepcopy(list(raw.get("actions") or [])),
                "evidence_observation_ids": sorted({str(value) for value in raw.get("evidence_observation_ids") or []}),
            }
        )
    if not edges:
        raise SpecimenError("form graph requires edges")
    out = {
        "schema_version": FORM_GRAPH_SCHEMA_VERSION,
        "kind": "earcrate_form_graph",
        "specimen_id": specimen_id,
        "entry_node": _text(graph.get("entry_node"), "form graph entry_node"),
        "nodes": sorted(nodes, key=lambda row: (row["printed_measure"], row["node_id"])),
        "edges": sorted(edges, key=lambda row: (row["from_node"], -row["priority"], row["edge_id"])),
        "repeat_regions": deepcopy(list(graph.get("repeat_regions") or [])),
        "metadata": deepcopy(dict(graph.get("metadata") or {})),
    }
    if out["entry_node"] not in node_ids:
        raise SpecimenError("form graph entry_node is unknown")
    out["form_graph_sha256"] = specimen_sha256_json(out)
    return out


def specimen_validate_form_graph(graph: Mapping[str, Any]) -> None:
    sealed = specimen_seal_form_graph(graph)
    supplied = str(graph.get("form_graph_sha256") or "")
    if supplied and supplied != sealed["form_graph_sha256"]:
        raise SpecimenError("form_graph_sha256 does not match form graph")


def specimen_seal_performance_path(path: Mapping[str, Any], graph: Mapping[str, Any] | None = None) -> dict[str, Any]:
    if int(path.get("schema_version") or PERFORMANCE_PATH_SCHEMA_VERSION) != PERFORMANCE_PATH_SCHEMA_VERSION:
        raise SpecimenError("unsupported performance path schema")
    if str(path.get("kind") or "earcrate_performance_path") != "earcrate_performance_path":
        raise SpecimenError("unsupported performance path kind")
    rows: list[dict[str, Any]] = []
    expected_index = 0
    expected_start = 0.0
    per_measure: dict[int, int] = {}
    for raw in path.get("occurrences") or []:
        if int(raw.get("order_index", -1)) != expected_index:
            raise SpecimenError("performance path order_index must be contiguous")
        measure = int(raw.get("printed_measure") or raw.get("measure") or 0)
        occurrence = int(raw.get("occurrence") or 0)
        expected_occurrence = per_measure.get(measure, 0) + 1
        if measure <= 0 or occurrence != expected_occurrence:
            raise SpecimenError(f"measure {measure} occurrence must be {expected_occurrence}")
        start_beat = float(raw.get("start_beat") or 0.0)
        beats = float(raw.get("beats") or 4.0)
        if abs(start_beat - expected_start) > 1e-9 or beats <= 0.0:
            raise SpecimenError("performance path beat positions must be contiguous")
        rows.append(
            {
                "order_index": expected_index,
                "printed_measure": measure,
                "occurrence": occurrence,
                "start_beat": start_beat,
                "beats": beats,
                "via_edge_id": str(raw.get("via_edge_id") or ""),
            }
        )
        per_measure[measure] = occurrence
        expected_index += 1
        expected_start += beats
    if not rows:
        raise SpecimenError("performance path requires occurrences")
    out = {
        "schema_version": PERFORMANCE_PATH_SCHEMA_VERSION,
        "kind": "earcrate_performance_path",
        "specimen_id": _text(path.get("specimen_id"), "performance path specimen_id"),
        "form_graph_sha256": _sha256(path.get("form_graph_sha256"), "performance path form_graph_sha256"),
        "occurrences": rows,
        "printed_measure_count": len({row["printed_measure"] for row in rows}),
        "performed_measure_count": len(rows),
        "total_beats": expected_start,
        "metadata": deepcopy(dict(path.get("metadata") or {})),
    }
    if graph is not None:
        sealed_graph = specimen_seal_form_graph(graph)
        if out["form_graph_sha256"] != sealed_graph["form_graph_sha256"]:
            raise SpecimenError("performance path names another form graph")
        edges = {
            (str(row["from_node"]), str(row["to_node"])): str(row["edge_id"])
            for row in sealed_graph["edges"]
        }
        for index in range(1, len(rows)):
            source = f"measure_{rows[index - 1]['printed_measure']:03d}"
            target = f"measure_{rows[index]['printed_measure']:03d}"
            edge_id = edges.get((source, target))
            if edge_id is None:
                raise SpecimenError(f"performance path traverses undeclared form edge {source}->{target}")
            if rows[index]["via_edge_id"] and rows[index]["via_edge_id"] != edge_id:
                raise SpecimenError(f"performance path edge receipt disagrees at occurrence {index}")
            rows[index]["via_edge_id"] = edge_id
    out["performance_path_sha256"] = specimen_sha256_json(out)
    return out


def specimen_validate_performance_path(path: Mapping[str, Any], graph: Mapping[str, Any] | None = None) -> None:
    sealed = specimen_seal_performance_path(path, graph)
    supplied = str(path.get("performance_path_sha256") or "")
    if supplied and supplied != sealed["performance_path_sha256"]:
        raise SpecimenError("performance_path_sha256 does not match performance path")


def specimen_seal_score_answer_key(answer: Mapping[str, Any]) -> dict[str, Any]:
    if int(answer.get("schema_version") or SCORE_ANSWER_KEY_SCHEMA_VERSION) != SCORE_ANSWER_KEY_SCHEMA_VERSION:
        raise SpecimenError("unsupported score answer-key schema")
    if str(answer.get("kind") or "earcrate_score_answer_key") != "earcrate_score_answer_key":
        raise SpecimenError("unsupported score answer-key kind")
    harmony_frames = [deepcopy(dict(row)) for row in answer.get("harmony_frames") or []]
    events = [deepcopy(dict(row)) for row in answer.get("events") or []]
    event_ids = [str(row.get("event_id") or "") for row in events]
    if not all(event_ids) or len(event_ids) != len(set(event_ids)):
        raise SpecimenError("score answer key events require unique nonempty event_id values")
    previous_end = 0
    for frame in harmony_frames:
        start = int(frame.get("start_step") or 0)
        end = int(frame.get("end_step") or 0)
        if start != previous_end or end <= start:
            raise SpecimenError("harmony frames must be contiguous and positive")
        previous_end = end
    if not harmony_frames:
        raise SpecimenError("score answer key requires harmony frames")
    out = {
        "schema_version": SCORE_ANSWER_KEY_SCHEMA_VERSION,
        "kind": "earcrate_score_answer_key",
        "specimen_id": _text(answer.get("specimen_id"), "answer key specimen_id"),
        "score_ledger_sha256": _sha256(answer.get("score_ledger_sha256"), "answer key score_ledger_sha256"),
        "form_graph_sha256": _sha256(answer.get("form_graph_sha256"), "answer key form_graph_sha256"),
        "performance_path_sha256": _sha256(answer.get("performance_path_sha256"), "answer key performance_path_sha256"),
        "midi_semantic_sha256": _sha256(answer.get("midi_semantic_sha256"), "answer key midi_semantic_sha256"),
        "steps_per_beat": int(answer.get("steps_per_beat") or 0),
        "tempo_bpm": float(answer.get("tempo_bpm") or 0.0),
        "meter": deepcopy(dict(answer.get("meter") or {})),
        "key_signature": deepcopy(dict(answer.get("key_signature") or {})),
        "harmony_frames": harmony_frames,
        "events": sorted(events, key=lambda row: (int(row.get("start_step") or 0), str(row.get("voice_id") or ""), str(row["event_id"]))),
        "source_counts": deepcopy(dict(answer.get("source_counts") or {})),
        "interpretive_limits": sorted({str(value) for value in answer.get("interpretive_limits") or []}),
        "metadata": deepcopy(dict(answer.get("metadata") or {})),
    }
    if out["steps_per_beat"] <= 0 or out["tempo_bpm"] <= 0.0:
        raise SpecimenError("answer key requires positive steps_per_beat and tempo_bpm")
    out["answer_key_sha256"] = specimen_sha256_json(out)
    return out


def specimen_validate_score_answer_key(answer: Mapping[str, Any]) -> None:
    sealed = specimen_seal_score_answer_key(answer)
    supplied = str(answer.get("answer_key_sha256") or "")
    if supplied and supplied != sealed["answer_key_sha256"]:
        raise SpecimenError("answer_key_sha256 does not match score answer key")


def specimen_observations_by_kind(ledger: Mapping[str, Any], kind: str) -> list[dict[str, Any]]:
    specimen_validate_observation_ledger(ledger)
    return [deepcopy(dict(row)) for row in ledger.get("observations") or [] if str(row.get("kind") or "") == str(kind)]


def specimen_default_convergence_policy() -> dict[str, Any]:
    policy = {
        "schema_version": CONVERGENCE_POLICY_SCHEMA_VERSION,
        "kind": "earcrate_convergence_policy",
        "policy_id": "children_buffalo_v1",
        "tempo_abs_error_bpm_max": 1.0,
        "meter_exact": True,
        "key_root_exact": True,
        "key_mode_exact": True,
        "note_pitch_recall_min": 0.80,
        "note_onset_mae_steps_max": 1.0,
        "harmony_root_recall_min": 0.75,
        "required_metrics": [
            "tempo",
            "meter",
            "key",
            "note_pitch_recall",
            "note_onset_mae_steps",
            "harmony_root_recall",
        ],
    }
    policy["policy_sha256"] = specimen_sha256_json(policy)
    return policy


def specimen_normalize_convergence_policy(policy: Mapping[str, Any] | None = None) -> dict[str, Any]:
    raw = deepcopy(dict(policy or specimen_default_convergence_policy()))
    if int(raw.get("schema_version") or 0) != CONVERGENCE_POLICY_SCHEMA_VERSION:
        raise SpecimenError("unsupported convergence policy schema")
    if str(raw.get("kind") or "") != "earcrate_convergence_policy":
        raise SpecimenError("unsupported convergence policy kind")
    raw.pop("policy_sha256", None)
    raw["policy_id"] = _text(raw.get("policy_id"), "convergence policy_id")
    for field in (
        "tempo_abs_error_bpm_max",
        "note_pitch_recall_min",
        "note_onset_mae_steps_max",
        "harmony_root_recall_min",
    ):
        value = float(raw.get(field))
        if not math.isfinite(value) or value < 0.0:
            raise SpecimenError(f"convergence policy {field} must be nonnegative")
        raw[field] = value
    raw["meter_exact"] = bool(raw.get("meter_exact", True))
    raw["key_root_exact"] = bool(raw.get("key_root_exact", True))
    raw["key_mode_exact"] = bool(raw.get("key_mode_exact", True))
    raw["required_metrics"] = sorted({str(value) for value in raw.get("required_metrics") or []})
    raw["policy_sha256"] = specimen_sha256_json(raw)
    return raw


def specimen_seal_gate_receipt(receipt: Mapping[str, Any]) -> dict[str, Any]:
    if int(receipt.get("schema_version") or BUFFALO_GATE_RECEIPT_SCHEMA_VERSION) != BUFFALO_GATE_RECEIPT_SCHEMA_VERSION:
        raise SpecimenError("unsupported Buffalo Gate receipt schema")
    if str(receipt.get("kind") or "earcrate_buffalo_gate_receipt") != "earcrate_buffalo_gate_receipt":
        raise SpecimenError("unsupported Buffalo Gate receipt kind")
    organs: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, raw in enumerate(receipt.get("organs") or []):
        organ_id = _text(raw.get("organ_id"), f"organ {index} organ_id")
        if organ_id in seen:
            raise SpecimenError(f"duplicate Buffalo Gate organ: {organ_id}")
        seen.add(organ_id)
        status = str(raw.get("status") or "not_run")
        if status not in GATE_STATUSES:
            raise SpecimenError(f"organ {organ_id} has unsupported status {status}")
        organs.append(
            {
                "organ_id": organ_id,
                "status": status,
                "required": bool(raw.get("required", True)),
                "artifact_sha256s": sorted({str(value) for value in raw.get("artifact_sha256s") or []}),
                "checks": deepcopy(dict(raw.get("checks") or {})),
                "blockers": sorted({str(value) for value in raw.get("blockers") or []}),
                "failures": sorted({str(value) for value in raw.get("failures") or []}),
                "metadata": deepcopy(dict(raw.get("metadata") or {})),
            }
        )
    required = [row for row in organs if row["required"]]
    overall = "passed"
    if any(row["status"] == "failed" for row in required):
        overall = "failed"
    elif any(row["status"] in {"blocked", "not_run"} for row in required):
        overall = "blocked"
    out = {
        "schema_version": BUFFALO_GATE_RECEIPT_SCHEMA_VERSION,
        "kind": "earcrate_buffalo_gate_receipt",
        "specimen_id": _text(receipt.get("specimen_id"), "Buffalo Gate specimen_id"),
        "manifest_sha256": _sha256(receipt.get("manifest_sha256"), "Buffalo Gate manifest_sha256"),
        "overall_status": overall,
        "buffalo_gate_passed": overall == "passed",
        "organs": sorted(organs, key=lambda row: row["organ_id"]),
        "required_organ_count": len(required),
        "passed_required_organ_count": sum(row["status"] == "passed" for row in required),
        "metadata": deepcopy(dict(receipt.get("metadata") or {})),
    }
    out["receipt_sha256"] = specimen_sha256_json(out)
    return out


def specimen_validate_gate_receipt(receipt: Mapping[str, Any]) -> None:
    sealed = specimen_seal_gate_receipt(receipt)
    supplied = str(receipt.get("receipt_sha256") or "")
    if supplied and supplied != sealed["receipt_sha256"]:
        raise SpecimenError("receipt_sha256 does not match Buffalo Gate receipt")
