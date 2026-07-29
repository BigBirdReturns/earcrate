from __future__ import annotations

"""Canonical objects for the EarCrate Open Music Evidence Floor.

The Floor is deliberately smaller than a DAW, model runtime, or musical planner. It
standardizes custody, evidence tier, provider authority, source/performance time,
phrase substitutability, review proposals, evaluation, and portable receipts. A
provider may measure or propose. It may not silently become musical authority.
"""

import hashlib
import json
import math
import os
import re
import tempfile
from copy import deepcopy
from fractions import Fraction
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

FLOOR_PROTOCOL_VERSION = 1
FLOOR_SCHEMA_VERSION = 1

FLOOR_EVIDENCE_BRANCHES = (
    "score",
    "symbolic",
    "audio",
    "convergence",
    "performance",
    "review",
    "evolution",
)
FLOOR_EVIDENCE_TIERS = (
    "unspecified",
    "authoritative_score",
    "community_symbolic_witness",
    "blind_audio_inference",
    "cross_modal_accepted",
    "performance_realization",
    "human_review",
    "campaign_evidence",
)
FLOOR_EMISSION_KINDS = (
    "observation",
    "candidate",
    "measurement",
    "refusal",
    "derived_artifact",
    "review_patch",
)
FLOOR_RESULT_STATUSES = ("success", "refused", "error")
FLOOR_NETWORK_POLICIES = ("forbidden", "declared", "required")
FLOOR_DETERMINISM_LEVELS = ("unknown", "best_effort", "repeatable", "bit_exact")
FLOOR_TIME_MODES = ("continuous", "jump", "loop", "retrigger", "reverse", "hold")
FLOOR_RIGHTS_STATUSES = (
    "unknown",
    "asserted",
    "user_verified",
    "provider_verified",
    "externally_certified",
)

FLOOR_DEFAULT_FORBIDDEN_AUTHORITY = (
    "SongGenome",
    "PerformanceScore",
    "MixScore",
    "accepted_score",
    "accepted_revision",
    "canonical_state",
    "canonical_song",
    "selected_winner",
    "tournament_winner",
    "applied_review_patch",
    "legal_determination",
    "rights_cleared",
    "whole_organism_passed",
    "buffalo_gate_passed",
)

_PROVIDER_ID_RE = re.compile(r"^[a-z0-9](?:[a-z0-9._-]{0,126}[a-z0-9])?$")
_CAPABILITY_RE = re.compile(r"^[a-z0-9](?:[a-z0-9._/-]{0,126}[a-z0-9])?$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class FloorError(ValueError):
    """Raised when a Floor object cannot support its declared contract."""


class FloorProtocolError(RuntimeError):
    """Raised when provider execution violates the Floor wire protocol."""


def floor_jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise FloorError("Floor objects cannot contain non-finite numbers")
        return value
    if isinstance(value, Fraction):
        return floor_fraction(value)
    if isinstance(value, Mapping):
        return {str(key): floor_jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [floor_jsonable(item) for item in value]
    if isinstance(value, (set, frozenset)):
        return sorted((floor_jsonable(item) for item in value), key=lambda item: json.dumps(item, sort_keys=True))
    if isinstance(value, Path):
        return str(value)
    if hasattr(value, "to_dict"):
        return floor_jsonable(value.to_dict())
    if hasattr(value, "item"):
        return floor_jsonable(value.item())
    return str(value)


def floor_canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        floor_jsonable(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def floor_sha256_json(value: Any) -> str:
    return hashlib.sha256(floor_canonical_json_bytes(value)).hexdigest()


def floor_sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(bytes(value)).hexdigest()


def floor_sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def floor_read_json(path: str | Path) -> dict[str, Any]:
    source = Path(path).expanduser().resolve()
    try:
        value = json.loads(source.read_text(encoding="utf-8"))
    except Exception as exc:
        raise FloorError(f"cannot read Floor JSON {source}: {exc}") from exc
    if not isinstance(value, dict):
        raise FloorError(f"Floor JSON must contain an object: {source}")
    return value


def floor_write_json_atomic(path: str | Path, value: Mapping[str, Any]) -> Path:
    target = Path(path).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(
        floor_jsonable(dict(value)),
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
        allow_nan=False,
    ) + "\n"
    descriptor, raw_temp = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=str(target.parent))
    temp = Path(raw_temp)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, target)
    finally:
        temp.unlink(missing_ok=True)
    return target


def _floor_text(value: Any, field: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise FloorError(f"{field} must be nonempty")
    return text


def _floor_sha(value: Any, field: str, *, optional: bool = False) -> str | None:
    text = str(value or "").strip().lower()
    if optional and not text:
        return None
    if not _SHA256_RE.fullmatch(text):
        raise FloorError(f"{field} must be a lowercase SHA-256")
    return text


def _floor_positive_int(value: Any, field: str, *, allow_zero: bool = False) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise FloorError(f"{field} must be an integer") from exc
    if number < 0 or (number == 0 and not allow_zero):
        qualifier = "nonnegative" if allow_zero else "positive"
        raise FloorError(f"{field} must be {qualifier}")
    return number


def _floor_number(value: Any, field: str, *, minimum: float | None = None, maximum: float | None = None) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise FloorError(f"{field} must be numeric") from exc
    if not math.isfinite(number):
        raise FloorError(f"{field} must be finite")
    if minimum is not None and number < minimum:
        raise FloorError(f"{field} must be >= {minimum}")
    if maximum is not None and number > maximum:
        raise FloorError(f"{field} must be <= {maximum}")
    return number


def floor_fraction(value: Any, field: str = "time") -> str:
    """Canonicalize a rational timeline value as an integer or ``n/d`` string."""
    try:
        if isinstance(value, Fraction):
            fraction = value
        elif isinstance(value, int):
            fraction = Fraction(value, 1)
        elif isinstance(value, float):
            if not math.isfinite(value):
                raise ValueError("non-finite")
            fraction = Fraction(str(value))
        else:
            text = str(value).strip()
            if not text:
                raise ValueError("empty")
            fraction = Fraction(text)
    except Exception as exc:
        raise FloorError(f"{field} must be a rational number") from exc
    if fraction.denominator <= 0:
        raise FloorError(f"{field} denominator must be positive")
    if fraction.denominator == 1:
        return str(fraction.numerator)
    return f"{fraction.numerator}/{fraction.denominator}"


def floor_fraction_value(value: Any, field: str = "time") -> Fraction:
    return Fraction(floor_fraction(value, field))


def _floor_without_hash(value: Mapping[str, Any], field: str) -> dict[str, Any]:
    out = deepcopy(dict(value))
    out.pop(field, None)
    return out


def _floor_check_supplied_hash(raw: Mapping[str, Any], sealed: Mapping[str, Any], field: str, label: str) -> None:
    supplied = str(raw.get(field) or "")
    if supplied and supplied != str(sealed.get(field) or ""):
        raise FloorError(f"{field} does not match {label}")


def _floor_semantic_artifact(raw: Mapping[str, Any], *, require_path: bool = False) -> dict[str, Any]:
    artifact_id = _floor_text(raw.get("artifact_id"), "artifact_id")
    sha256 = _floor_sha(raw.get("sha256"), f"artifact {artifact_id} sha256")
    size = _floor_positive_int(raw.get("size_bytes", 0), f"artifact {artifact_id} size_bytes", allow_zero=True)
    media_kind = _floor_text(raw.get("media_kind"), f"artifact {artifact_id} media_kind")
    branch = str(raw.get("branch") or "").strip()
    if branch and branch not in FLOOR_EVIDENCE_BRANCHES:
        raise FloorError(f"artifact {artifact_id} branch is not recognized")
    path = str(raw.get("path") or "")
    uri = str(raw.get("uri") or "")
    if require_path and not path:
        raise FloorError(f"artifact {artifact_id} requires a local path")
    return {
        "artifact_id": artifact_id,
        "sha256": sha256,
        "size_bytes": size,
        "media_kind": media_kind,
        "role": str(raw.get("role") or ""),
        "branch": branch,
        "ancestor_branches": sorted({str(item) for item in raw.get("ancestor_branches") or ([branch] if branch else [])}),
        "path": path,
        "uri": uri,
        "metadata": deepcopy(dict(raw.get("metadata") or {})),
    }


def floor_artifact_semantic_identity(raw: Mapping[str, Any]) -> dict[str, Any]:
    artifact = _floor_semantic_artifact(raw)
    return {
        key: value
        for key, value in artifact.items()
        if key not in {"path", "uri"}
    }


def _floor_result_semantic_identity(raw: Mapping[str, Any]) -> dict[str, Any]:
    value = deepcopy(dict(raw))
    value.pop("result_sha256", None)
    value.pop("semantic_result_sha256", None)
    for artifact in value.get("artifacts") or []:
        if isinstance(artifact, dict):
            artifact.pop("path", None)
            artifact.pop("uri", None)
    return value


def floor_validate_authority_payload(value: Any, *, path: str = "$", forbidden: Sequence[str] = FLOOR_DEFAULT_FORBIDDEN_AUTHORITY) -> None:
    """Refuse provider payloads that try to claim canonical or applied authority."""
    forbidden_lower = {str(item).lower() for item in forbidden}
    if isinstance(value, Mapping):
        for key, item in value.items():
            key_text = str(key)
            if key_text.lower() in forbidden_lower:
                raise FloorError(f"provider payload claims forbidden authority at {path}.{key_text}")
            if key_text.lower() in {"accepted", "canonical", "selected", "applied", "legally_cleared"} and bool(item):
                raise FloorError(f"provider payload sets authority-bearing flag at {path}.{key_text}")
            floor_validate_authority_payload(item, path=f"{path}.{key_text}", forbidden=forbidden)
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            floor_validate_authority_payload(item, path=f"{path}[{index}]", forbidden=forbidden)
    elif isinstance(value, str) and value.lower() in forbidden_lower:
        raise FloorError(f"provider payload names forbidden authority at {path}")


def floor_seal_time_map(value: Mapping[str, Any]) -> dict[str, Any]:
    raw = deepcopy(dict(value))
    if int(raw.get("schema_version") or FLOOR_SCHEMA_VERSION) != FLOOR_SCHEMA_VERSION:
        raise FloorError("unsupported TimeMap schema")
    if str(raw.get("kind") or "earcrate_floor_time_map") != "earcrate_floor_time_map":
        raise FloorError("unsupported TimeMap kind")
    segments: list[dict[str, Any]] = []
    previous_target_end: Fraction | None = None
    for index, row_raw in enumerate(raw.get("segments") or []):
        if not isinstance(row_raw, Mapping):
            raise FloorError(f"TimeMap segment {index} must be an object")
        row = dict(row_raw)
        mode = str(row.get("mode") or "continuous")
        if mode not in FLOOR_TIME_MODES:
            raise FloorError(f"TimeMap segment {index} has unsupported mode {mode!r}")
        target_start = floor_fraction_value(row.get("target_start"), f"segment {index} target_start")
        target_end = floor_fraction_value(row.get("target_end"), f"segment {index} target_end")
        source_start = floor_fraction_value(row.get("source_start"), f"segment {index} source_start")
        source_end = floor_fraction_value(row.get("source_end"), f"segment {index} source_end")
        if target_end <= target_start:
            raise FloorError(f"TimeMap segment {index} target interval must be positive")
        if mode == "reverse":
            if source_end >= source_start:
                raise FloorError(f"TimeMap reverse segment {index} must move source time backward")
        elif mode == "hold":
            if source_end != source_start:
                raise FloorError(f"TimeMap hold segment {index} must keep source time fixed")
        elif source_end <= source_start:
            raise FloorError(f"TimeMap segment {index} source interval must be positive")
        if previous_target_end is not None and target_start < previous_target_end:
            raise FloorError("TimeMap target segments may not overlap")
        previous_target_end = target_end
        segments.append(
            {
                "segment_id": str(row.get("segment_id") or f"segment_{index:04d}"),
                "target_start": floor_fraction(target_start),
                "target_end": floor_fraction(target_end),
                "source_artifact_id": _floor_text(row.get("source_artifact_id"), f"segment {index} source_artifact_id"),
                "source_start": floor_fraction(source_start),
                "source_end": floor_fraction(source_end),
                "mode": mode,
                "rate": floor_fraction(row.get("rate", 1), f"segment {index} rate"),
                "metadata": deepcopy(dict(row.get("metadata") or {})),
            }
        )
    if not segments:
        raise FloorError("TimeMap requires at least one segment")
    out = {
        "schema_version": FLOOR_SCHEMA_VERSION,
        "kind": "earcrate_floor_time_map",
        "time_unit": str(raw.get("time_unit") or "beat"),
        "segments": segments,
        "metadata": deepcopy(dict(raw.get("metadata") or {})),
    }
    out["time_map_sha256"] = floor_sha256_json(out)
    _floor_check_supplied_hash(raw, out, "time_map_sha256", "TimeMap")
    return out


def floor_seal_rights_envelope(value: Mapping[str, Any]) -> dict[str, Any]:
    raw = deepcopy(dict(value))
    status = str(raw.get("assertion_status") or "unknown")
    if status not in FLOOR_RIGHTS_STATUSES:
        raise FloorError(f"unsupported rights assertion status {status!r}")
    source_sha = _floor_sha(raw.get("source_artifact_sha256"), "rights source_artifact_sha256", optional=True)
    out = {
        "schema_version": FLOOR_SCHEMA_VERSION,
        "kind": "earcrate_floor_rights_envelope",
        "source_artifact_sha256": source_sha,
        "assertion_status": status,
        "license_expression": str(raw.get("license_expression") or "NOASSERTION"),
        "policy_uri": str(raw.get("policy_uri") or ""),
        "allowed_uses": sorted({str(item) for item in raw.get("allowed_uses") or []}),
        "prohibited_uses": sorted({str(item) for item in raw.get("prohibited_uses") or []}),
        "attribution": deepcopy(list(raw.get("attribution") or [])),
        "evidence_refs": sorted({str(item) for item in raw.get("evidence_refs") or []}),
        "jurisdiction": str(raw.get("jurisdiction") or ""),
        "expires_at": str(raw.get("expires_at") or ""),
        "provider_may_not_decide_legality": True,
        "metadata": deepcopy(dict(raw.get("metadata") or {})),
    }
    out["rights_envelope_sha256"] = floor_sha256_json(out)
    _floor_check_supplied_hash(raw, out, "rights_envelope_sha256", "rights envelope")
    return out


def floor_seal_phrase_contract(value: Mapping[str, Any]) -> dict[str, Any]:
    raw = deepcopy(dict(value))
    meter = dict(raw.get("meter") or {})
    numerator = _floor_positive_int(meter.get("numerator"), "PhraseContract meter numerator")
    denominator = _floor_positive_int(meter.get("denominator"), "PhraseContract meter denominator")
    start_beat = floor_fraction(raw.get("start_beat", 0), "PhraseContract start_beat")
    length_beats = floor_fraction(raw.get("length_beats"), "PhraseContract length_beats")
    if floor_fraction_value(length_beats) <= 0:
        raise FloorError("PhraseContract length_beats must be positive")
    transforms = dict(raw.get("transforms") or {})
    allowed_ops = sorted({str(item) for item in transforms.get("allowed_operations") or []})
    out = {
        "schema_version": FLOOR_SCHEMA_VERSION,
        "kind": "earcrate_floor_phrase_contract",
        "role": _floor_text(raw.get("role"), "PhraseContract role"),
        "start_beat": start_beat,
        "length_beats": length_beats,
        "meter": {"numerator": numerator, "denominator": denominator},
        "entry_grammar": deepcopy(dict(raw.get("entry_grammar") or {})),
        "exit_grammar": deepcopy(dict(raw.get("exit_grammar") or {})),
        "transforms": {
            "allowed_operations": allowed_ops,
            "tempo_ratio": deepcopy(dict(transforms.get("tempo_ratio") or {})),
            "transpose_semitones": deepcopy(dict(transforms.get("transpose_semitones") or {})),
            "gain_db": deepcopy(dict(transforms.get("gain_db") or {})),
            "reverse_allowed": bool(transforms.get("reverse_allowed", False)),
            "loop_allowed": bool(transforms.get("loop_allowed", True)),
            "slice_allowed": bool(transforms.get("slice_allowed", True)),
            "metadata": deepcopy(dict(transforms.get("metadata") or {})),
        },
        "hard_constraints": deepcopy(dict(raw.get("hard_constraints") or {})),
        "soft_objectives": deepcopy(list(raw.get("soft_objectives") or [])),
        "identity_obligations": deepcopy(list(raw.get("identity_obligations") or [])),
        "future_obligations": deepcopy(list(raw.get("future_obligations") or [])),
        "evidence_refs": sorted({str(item) for item in raw.get("evidence_refs") or []}),
        "rights": floor_seal_rights_envelope(raw.get("rights") or {}),
        "metadata": deepcopy(dict(raw.get("metadata") or {})),
    }
    if not out["identity_obligations"]:
        raise FloorError("PhraseContract requires at least one identity obligation")
    out["phrase_contract_sha256"] = floor_sha256_json(out)
    out["contract_id"] = str(raw.get("contract_id") or "phrase_contract_" + out["phrase_contract_sha256"][:24])
    # Include the stable public ID in the final identity.
    payload = _floor_without_hash(out, "phrase_contract_sha256")
    out["phrase_contract_sha256"] = floor_sha256_json(payload)
    _floor_check_supplied_hash(raw, out, "phrase_contract_sha256", "PhraseContract")
    return out


def floor_seal_review_patch(value: Mapping[str, Any]) -> dict[str, Any]:
    raw = deepcopy(dict(value))
    if raw.get("applied") not in {None, False}:
        raise FloorError("provider ReviewPatch proposals must remain unapplied")
    operations = []
    for index, item in enumerate(raw.get("operations") or []):
        if not isinstance(item, Mapping):
            raise FloorError(f"ReviewPatch operation {index} must be an object")
        op = str(item.get("op") or "")
        if op not in {"add", "remove", "replace", "move", "copy", "test"}:
            raise FloorError(f"ReviewPatch operation {index} has unsupported op {op!r}")
        path = _floor_text(item.get("path"), f"ReviewPatch operation {index} path")
        row = {"op": op, "path": path}
        if "from" in item:
            row["from"] = str(item.get("from") or "")
        if "value" in item:
            row["value"] = floor_jsonable(item.get("value"))
        operations.append(row)
    if not operations:
        raise FloorError("ReviewPatch requires at least one operation")
    out = {
        "schema_version": FLOOR_SCHEMA_VERSION,
        "kind": "earcrate_floor_review_patch",
        "target_revision_sha256": _floor_sha(raw.get("target_revision_sha256"), "ReviewPatch target_revision_sha256"),
        "target_object": _floor_text(raw.get("target_object"), "ReviewPatch target_object"),
        "operations": operations,
        "reason": _floor_text(raw.get("reason"), "ReviewPatch reason"),
        "evidence_refs": sorted({str(item) for item in raw.get("evidence_refs") or []}),
        "invalidation_hints": sorted({str(item) for item in raw.get("invalidation_hints") or []}),
        "proposed_by": deepcopy(dict(raw.get("proposed_by") or {})),
        "applied": False,
        "metadata": deepcopy(dict(raw.get("metadata") or {})),
    }
    out["review_patch_sha256"] = floor_sha256_json(out)
    out["patch_id"] = str(raw.get("patch_id") or "review_patch_" + out["review_patch_sha256"][:24])
    payload = _floor_without_hash(out, "review_patch_sha256")
    out["review_patch_sha256"] = floor_sha256_json(payload)
    _floor_check_supplied_hash(raw, out, "review_patch_sha256", "ReviewPatch")
    return out


def _floor_normalize_capability(raw: Mapping[str, Any], index: int) -> dict[str, Any]:
    name = _floor_text(raw.get("capability"), f"capability {index}")
    if not _CAPABILITY_RE.fullmatch(name):
        raise FloorError(f"capability {name!r} contains unsupported characters")
    branches = sorted({str(item) for item in raw.get("evidence_branches") or []})
    tiers = sorted({str(item) for item in raw.get("evidence_tiers") or []})
    if not branches or any(item not in FLOOR_EVIDENCE_BRANCHES for item in branches):
        raise FloorError(f"capability {name} requires recognized evidence branches")
    if not tiers or any(item not in FLOOR_EVIDENCE_TIERS for item in tiers):
        raise FloorError(f"capability {name} requires recognized evidence tiers")
    result_kinds = sorted({str(item) for item in raw.get("result_kinds") or []})
    if not result_kinds or any(item not in FLOOR_EMISSION_KINDS for item in result_kinds):
        raise FloorError(f"capability {name} requires recognized result kinds")
    network = str(raw.get("network_policy") or "forbidden")
    if network not in FLOOR_NETWORK_POLICIES:
        raise FloorError(f"capability {name} has unsupported network policy")
    determinism = str(raw.get("determinism") or "unknown")
    if determinism not in FLOOR_DETERMINISM_LEVELS:
        raise FloorError(f"capability {name} has unsupported determinism level")
    return {
        "capability": name,
        "input_media_kinds": sorted({str(item) for item in raw.get("input_media_kinds") or ["*/*"]}),
        "result_kinds": result_kinds,
        "evidence_branches": branches,
        "evidence_tiers": tiers,
        "network_policy": network,
        "determinism": determinism,
        "max_runtime_seconds": _floor_positive_int(raw.get("max_runtime_seconds", 300), f"capability {name} max_runtime_seconds"),
        "max_output_bytes": _floor_positive_int(raw.get("max_output_bytes", 1 << 30), f"capability {name} max_output_bytes"),
        "parameter_schema": deepcopy(dict(raw.get("parameter_schema") or {})),
        "metadata": deepcopy(dict(raw.get("metadata") or {})),
    }


def floor_seal_provider_manifest(value: Mapping[str, Any]) -> dict[str, Any]:
    raw = deepcopy(dict(value))
    if int(raw.get("schema_version") or 0) != FLOOR_SCHEMA_VERSION:
        raise FloorError("unsupported provider manifest schema")
    if str(raw.get("kind") or "") != "earcrate_floor_provider_manifest":
        raise FloorError("unsupported provider manifest kind")
    provider_id = _floor_text(raw.get("provider_id"), "provider_id").lower()
    if not _PROVIDER_ID_RE.fullmatch(provider_id):
        raise FloorError("provider_id must be a portable lowercase identifier")
    provider_version = _floor_text(raw.get("provider_version"), "provider_version")
    protocol = dict(raw.get("protocol") or {})
    if str(protocol.get("name") or "") != "earcrate-floor-stdio-json":
        raise FloorError("provider manifest must use the earcrate-floor-stdio-json protocol")
    if int(protocol.get("version") or 0) != FLOOR_PROTOCOL_VERSION:
        raise FloorError("provider protocol version is unsupported")
    entrypoint = dict(raw.get("entrypoint") or {})
    argv = entrypoint.get("argv")
    if not isinstance(argv, list) or not argv or not all(isinstance(item, str) and item for item in argv):
        raise FloorError("provider entrypoint.argv must be a nonempty argv array")
    if any("\x00" in item for item in argv):
        raise FloorError("provider entrypoint.argv may not contain NUL")
    capabilities = [_floor_normalize_capability(item, index) for index, item in enumerate(raw.get("capabilities") or [])]
    if not capabilities:
        raise FloorError("provider manifest requires capabilities")
    names = [item["capability"] for item in capabilities]
    if len(names) != len(set(names)):
        raise FloorError("provider manifest has duplicate capabilities")
    authority = dict(raw.get("authority") or {})
    may_emit = sorted({str(item) for item in authority.get("may_emit") or FLOOR_EMISSION_KINDS})
    if any(item not in FLOOR_EMISSION_KINDS for item in may_emit):
        raise FloorError("provider authority may_emit contains unsupported result kinds")
    may_not_emit = sorted({*FLOOR_DEFAULT_FORBIDDEN_AUTHORITY, *(str(item) for item in authority.get("may_not_emit") or [])})
    supply_chain = dict(raw.get("supply_chain") or {})
    out = {
        "schema_version": FLOOR_SCHEMA_VERSION,
        "kind": "earcrate_floor_provider_manifest",
        "provider_id": provider_id,
        "provider_version": provider_version,
        "display_name": str(raw.get("display_name") or provider_id),
        "description": str(raw.get("description") or ""),
        "protocol": {"name": "earcrate-floor-stdio-json", "version": FLOOR_PROTOCOL_VERSION},
        "entrypoint": {
            "argv": list(argv),
            "working_directory": str(entrypoint.get("working_directory") or "${FLOOR_MANIFEST_DIR}"),
            "environment": deepcopy(dict(entrypoint.get("environment") or {})),
        },
        "capabilities": sorted(capabilities, key=lambda item: item["capability"]),
        "authority": {
            "may_emit": may_emit,
            "may_not_emit": may_not_emit,
            "canonical_write_access": False,
            "review_patch_apply_access": False,
            "legal_decision_access": False,
        },
        "supply_chain": {
            "license_expression": str(supply_chain.get("license_expression") or "NOASSERTION"),
            "source_uri": str(supply_chain.get("source_uri") or ""),
            "artifact_sha256": _floor_sha(supply_chain.get("artifact_sha256"), "supply_chain artifact_sha256", optional=True),
            "model_identities": deepcopy(list(supply_chain.get("model_identities") or [])),
            "signatures": deepcopy(list(supply_chain.get("signatures") or [])),
            "metadata": deepcopy(dict(supply_chain.get("metadata") or {})),
        },
        "metadata": deepcopy(dict(raw.get("metadata") or {})),
    }
    out["manifest_sha256"] = floor_sha256_json(out)
    _floor_check_supplied_hash(raw, out, "manifest_sha256", "provider manifest")
    return out


def floor_capability_for_manifest(manifest: Mapping[str, Any], capability: str) -> dict[str, Any]:
    sealed = floor_seal_provider_manifest(manifest)
    for row in sealed["capabilities"]:
        if row["capability"] == str(capability):
            return deepcopy(row)
    raise FloorError(f"provider {sealed['provider_id']} has no capability {capability!r}")


def _floor_request_semantic_payload(value: Mapping[str, Any]) -> dict[str, Any]:
    payload = deepcopy(dict(value))
    payload.pop("request_sha256", None)
    payload.pop("request_id", None)
    payload["inputs"] = [floor_artifact_semantic_identity(item) for item in payload.get("inputs") or []]
    return payload


def floor_seal_provider_request(value: Mapping[str, Any]) -> dict[str, Any]:
    raw = deepcopy(dict(value))
    if int(raw.get("schema_version") or 0) != FLOOR_SCHEMA_VERSION:
        raise FloorError("unsupported provider request schema")
    if str(raw.get("kind") or "") != "earcrate_floor_provider_request":
        raise FloorError("unsupported provider request kind")
    capability = _floor_text(raw.get("capability"), "request capability")
    if not _CAPABILITY_RE.fullmatch(capability):
        raise FloorError("request capability contains unsupported characters")
    branch = str(raw.get("evidence_branch") or "")
    tier = str(raw.get("evidence_tier") or "")
    if branch not in FLOOR_EVIDENCE_BRANCHES:
        raise FloorError("request evidence_branch is unsupported")
    if tier not in FLOOR_EVIDENCE_TIERS:
        raise FloorError("request evidence_tier is unsupported")
    inputs = [_floor_semantic_artifact(item) for item in raw.get("inputs") or []]
    if not inputs:
        raise FloorError("provider request requires at least one input artifact")
    ids = [item["artifact_id"] for item in inputs]
    if len(ids) != len(set(ids)):
        raise FloorError("provider request has duplicate artifact_id values")
    for item in inputs:
        ancestors = set(item["ancestor_branches"])
        if item["branch"] and item["branch"] not in ancestors:
            raise FloorError(f"input {item['artifact_id']} omits its direct branch from ancestry")
        if branch == "audio" and any(ancestor != "audio" for ancestor in ancestors):
            raise FloorError("audio provider request is tainted by non-audio ancestry")
    allowed_result_kinds = sorted({str(item) for item in raw.get("allowed_result_kinds") or FLOOR_EMISSION_KINDS})
    if any(item not in FLOOR_EMISSION_KINDS for item in allowed_result_kinds):
        raise FloorError("request allowed_result_kinds contains an unsupported kind")
    network_policy = str(raw.get("network_policy") or "forbidden")
    if network_policy not in FLOOR_NETWORK_POLICIES:
        raise FloorError("request network_policy is unsupported")
    limits = dict(raw.get("limits") or {})
    out = {
        "schema_version": FLOOR_SCHEMA_VERSION,
        "kind": "earcrate_floor_provider_request",
        "capability": capability,
        "evidence_branch": branch,
        "evidence_tier": tier,
        "inputs": inputs,
        "parameters": deepcopy(dict(raw.get("parameters") or {})),
        "allowed_result_kinds": allowed_result_kinds,
        "forbidden_authority_claims": sorted({*FLOOR_DEFAULT_FORBIDDEN_AUTHORITY, *(str(item) for item in raw.get("forbidden_authority_claims") or [])}),
        "network_policy": network_policy,
        "limits": {
            "runtime_seconds": _floor_positive_int(limits.get("runtime_seconds", 300), "request runtime_seconds"),
            "stdout_bytes": _floor_positive_int(limits.get("stdout_bytes", 8 << 20), "request stdout_bytes"),
            "stderr_bytes": _floor_positive_int(limits.get("stderr_bytes", 8 << 20), "request stderr_bytes"),
            "artifact_bytes": _floor_positive_int(limits.get("artifact_bytes", 1 << 30), "request artifact_bytes"),
            "artifact_count": _floor_positive_int(limits.get("artifact_count", 1024), "request artifact_count"),
        },
        "context": deepcopy(dict(raw.get("context") or {})),
        "metadata": deepcopy(dict(raw.get("metadata") or {})),
    }
    semantic = _floor_request_semantic_payload(out)
    out["request_sha256"] = floor_sha256_json(semantic)
    out["request_id"] = str(raw.get("request_id") or "floor_request_" + out["request_sha256"][:24])
    # Public ID is not part of the semantic request identity.
    _floor_check_supplied_hash(raw, out, "request_sha256", "provider request")
    return out


def floor_request_semantic_identity(value: Mapping[str, Any]) -> str:
    return floor_seal_provider_request(value)["request_sha256"]


def _floor_seal_emission(
    raw: Mapping[str, Any],
    *,
    index: int,
    request: Mapping[str, Any],
    manifest: Mapping[str, Any],
) -> dict[str, Any]:
    kind = str(raw.get("kind") or "")
    if kind not in FLOOR_EMISSION_KINDS:
        raise FloorError(f"provider emission {index} has unsupported kind {kind!r}")
    if kind not in request["allowed_result_kinds"]:
        raise FloorError(f"provider emission {index} kind {kind!r} is not allowed by the request")
    if kind not in manifest["authority"]["may_emit"]:
        raise FloorError(f"provider manifest does not authorize emission kind {kind!r}")
    payload = floor_jsonable(raw.get("payload"))
    floor_validate_authority_payload(payload, forbidden=request["forbidden_authority_claims"])
    if kind == "review_patch":
        if not isinstance(payload, Mapping):
            raise FloorError("review_patch emission payload must be an object")
        payload = floor_seal_review_patch(payload)
    if isinstance(payload, Mapping) and str(payload.get("kind") or "") == "earcrate_floor_time_map":
        payload = floor_seal_time_map(payload)
    if isinstance(payload, Mapping) and str(payload.get("kind") or "") == "earcrate_floor_phrase_contract":
        payload = floor_seal_phrase_contract(payload)
    confidence = raw.get("confidence")
    confidence_value = None if confidence is None else _floor_number(confidence, f"emission {index} confidence", minimum=0.0, maximum=1.0)
    out = {
        "kind": kind,
        "subject": str(raw.get("subject") or ""),
        "payload": payload,
        "evidence_refs": sorted({str(item) for item in raw.get("evidence_refs") or []}),
        "confidence": confidence_value,
        "metadata": deepcopy(dict(raw.get("metadata") or {})),
    }
    if kind not in {"refusal", "measurement"} and not out["evidence_refs"]:
        raise FloorError(f"provider emission {index} requires evidence_refs")
    out["emission_id"] = str(raw.get("emission_id") or f"floor_emission_{floor_sha256_json(out)[:24]}")
    return out


def floor_seal_provider_result(
    value: Mapping[str, Any],
    *,
    request: Mapping[str, Any],
    manifest: Mapping[str, Any],
) -> dict[str, Any]:
    raw = deepcopy(dict(value))
    sealed_request = floor_seal_provider_request(request)
    sealed_manifest = floor_seal_provider_manifest(manifest)
    if int(raw.get("schema_version") or FLOOR_SCHEMA_VERSION) != FLOOR_SCHEMA_VERSION:
        raise FloorError("unsupported provider result schema")
    if str(raw.get("kind") or "earcrate_floor_provider_result") != "earcrate_floor_provider_result":
        raise FloorError("unsupported provider result kind")
    if str(raw.get("request_sha256") or "") != sealed_request["request_sha256"]:
        raise FloorError("provider result belongs to another request")
    if str(raw.get("provider_manifest_sha256") or "") != sealed_manifest["manifest_sha256"]:
        raise FloorError("provider result names another provider manifest")
    if str(raw.get("provider_id") or "") != sealed_manifest["provider_id"]:
        raise FloorError("provider result provider_id disagrees with manifest")
    if str(raw.get("provider_version") or "") != sealed_manifest["provider_version"]:
        raise FloorError("provider result provider_version disagrees with manifest")
    status = str(raw.get("status") or "")
    if status not in FLOOR_RESULT_STATUSES:
        raise FloorError("provider result status is unsupported")
    emissions = [
        _floor_seal_emission(item, index=index, request=sealed_request, manifest=sealed_manifest)
        for index, item in enumerate(raw.get("emissions") or [])
    ]
    artifacts = [_floor_semantic_artifact(item) for item in raw.get("artifacts") or []]
    ids = [item["artifact_id"] for item in artifacts]
    if len(ids) != len(set(ids)):
        raise FloorError("provider result has duplicate artifact_id values")
    refusals = []
    for index, item in enumerate(raw.get("refusals") or []):
        if not isinstance(item, Mapping):
            raise FloorError(f"provider refusal {index} must be an object")
        refusals.append(
            {
                "code": _floor_text(item.get("code"), f"provider refusal {index} code"),
                "message": _floor_text(item.get("message"), f"provider refusal {index} message"),
                "details": deepcopy(dict(item.get("details") or {})),
            }
        )
    if status == "success" and not emissions and not artifacts:
        raise FloorError("successful provider result must emit evidence or artifacts")
    if status == "refused" and not refusals:
        raise FloorError("refused provider result requires at least one refusal")
    out = {
        "schema_version": FLOOR_SCHEMA_VERSION,
        "kind": "earcrate_floor_provider_result",
        "request_sha256": sealed_request["request_sha256"],
        "provider_manifest_sha256": sealed_manifest["manifest_sha256"],
        "provider_id": sealed_manifest["provider_id"],
        "provider_version": sealed_manifest["provider_version"],
        "status": status,
        "emissions": emissions,
        "artifacts": artifacts,
        "refusals": refusals,
        "metrics": deepcopy(dict(raw.get("metrics") or {})),
        "metadata": deepcopy(dict(raw.get("metadata") or {})),
    }
    floor_validate_authority_payload(out["metrics"], forbidden=sealed_request["forbidden_authority_claims"])
    out["semantic_result_sha256"] = floor_sha256_json(_floor_result_semantic_identity(out))
    out["result_sha256"] = floor_sha256_json(out)
    _floor_check_supplied_hash(raw, out, "semantic_result_sha256", "provider semantic result")
    _floor_check_supplied_hash(raw, out, "result_sha256", "provider result")
    return out


def floor_seal_invocation_receipt(value: Mapping[str, Any]) -> dict[str, Any]:
    raw = deepcopy(dict(value))
    if int(raw.get("schema_version") or FLOOR_SCHEMA_VERSION) != FLOOR_SCHEMA_VERSION:
        raise FloorError("unsupported invocation receipt schema")
    if str(raw.get("kind") or "earcrate_floor_invocation_receipt") != "earcrate_floor_invocation_receipt":
        raise FloorError("unsupported invocation receipt kind")
    out = {
        "schema_version": FLOOR_SCHEMA_VERSION,
        "kind": "earcrate_floor_invocation_receipt",
        "provider_id": _floor_text(raw.get("provider_id"), "receipt provider_id"),
        "provider_version": _floor_text(raw.get("provider_version"), "receipt provider_version"),
        "provider_manifest_sha256": _floor_sha(raw.get("provider_manifest_sha256"), "receipt provider_manifest_sha256"),
        "request_sha256": _floor_sha(raw.get("request_sha256"), "receipt request_sha256"),
        "result_sha256": _floor_sha(raw.get("result_sha256"), "receipt result_sha256", optional=True),
        "semantic_result_sha256": _floor_sha(raw.get("semantic_result_sha256"), "receipt semantic_result_sha256", optional=True),
        "argv": [str(item) for item in raw.get("argv") or []],
        "working_directory": str(raw.get("working_directory") or ""),
        "executable": deepcopy(dict(raw.get("executable") or {})),
        "input_custody": deepcopy(list(raw.get("input_custody") or [])),
        "output_custody": deepcopy(list(raw.get("output_custody") or [])),
        "stdout": deepcopy(dict(raw.get("stdout") or {})),
        "stderr": deepcopy(dict(raw.get("stderr") or {})),
        "process": deepcopy(dict(raw.get("process") or {})),
        "network": {
            "declared_policy": str((raw.get("network") or {}).get("declared_policy") or "forbidden"),
            "host_enforcement": str((raw.get("network") or {}).get("host_enforcement") or "declaration_only"),
            "os_sandbox_proved": bool((raw.get("network") or {}).get("os_sandbox_proved", False)),
        },
        "resource_limits": deepcopy(dict(raw.get("resource_limits") or {})),
        "complete": bool(raw.get("complete", False)),
        "refusals": deepcopy(list(raw.get("refusals") or [])),
        "metadata": deepcopy(dict(raw.get("metadata") or {})),
    }
    if out["network"]["os_sandbox_proved"]:
        raise FloorError("reference Floor host cannot claim an OS network sandbox")
    out["receipt_sha256"] = floor_sha256_json(out)
    _floor_check_supplied_hash(raw, out, "receipt_sha256", "invocation receipt")
    return out


def floor_seal_evaluation_policy(value: Mapping[str, Any]) -> dict[str, Any]:
    raw = deepcopy(dict(value))
    hard_gates = []
    for index, row in enumerate(raw.get("hard_gates") or []):
        if not isinstance(row, Mapping):
            raise FloorError(f"evaluation hard gate {index} must be an object")
        hard_gates.append(
            {
                "metric": _floor_text(row.get("metric"), f"evaluation hard gate {index} metric"),
                "operator": str(row.get("operator") or "gte"),
                "value": floor_jsonable(row.get("value")),
            }
        )
    stages = []
    for index, row in enumerate(raw.get("lexicographic_stages") or []):
        if not isinstance(row, Mapping):
            raise FloorError(f"evaluation stage {index} must be an object")
        weights = {str(key): _floor_number(value, f"stage {index} weight {key}") for key, value in dict(row.get("weights") or {}).items()}
        if not weights:
            raise FloorError(f"evaluation stage {index} requires metric weights")
        stages.append({"stage": _floor_text(row.get("stage"), f"evaluation stage {index} name"), "weights": weights})
    if not stages:
        raise FloorError("evaluation policy requires lexicographic stages")
    out = {
        "schema_version": FLOOR_SCHEMA_VERSION,
        "kind": "earcrate_floor_evaluation_policy",
        "policy_id": str(raw.get("policy_id") or ""),
        "hard_gates": hard_gates,
        "lexicographic_stages": stages,
        "higher_is_better": sorted({str(item) for item in raw.get("higher_is_better") or []}),
        "lower_is_better": sorted({str(item) for item in raw.get("lower_is_better") or []}),
        "metadata": deepcopy(dict(raw.get("metadata") or {})),
    }
    out["policy_sha256"] = floor_sha256_json(out)
    if not out["policy_id"]:
        out["policy_id"] = "floor_policy_" + out["policy_sha256"][:24]
        out["policy_sha256"] = floor_sha256_json(_floor_without_hash(out, "policy_sha256"))
    _floor_check_supplied_hash(raw, out, "policy_sha256", "evaluation policy")
    return out


def floor_seal_evaluation_ledger(value: Mapping[str, Any]) -> dict[str, Any]:
    raw = deepcopy(dict(value))
    evaluator = deepcopy(dict(raw.get("evaluator") or {}))
    evaluator_id = _floor_text(evaluator.get("evaluator_id"), "evaluation evaluator_id")
    provider_id = _floor_text(raw.get("provider_id"), "evaluation provider_id")
    if evaluator_id == provider_id:
        raise FloorError("provider quality must be evaluated by an independent evaluator identity")
    metrics = {}
    for key, item in dict(raw.get("metrics") or {}).items():
        metrics[str(key)] = _floor_number(item, f"evaluation metric {key}")
    if not metrics:
        raise FloorError("evaluation ledger requires metrics")
    out = {
        "schema_version": FLOOR_SCHEMA_VERSION,
        "kind": "earcrate_floor_evaluation_ledger",
        "provider_id": provider_id,
        "provider_manifest_sha256": _floor_sha(raw.get("provider_manifest_sha256"), "evaluation provider_manifest_sha256"),
        "request_sha256": _floor_sha(raw.get("request_sha256"), "evaluation request_sha256"),
        "result_sha256": _floor_sha(raw.get("result_sha256"), "evaluation result_sha256"),
        "evaluator": {
            "evaluator_id": evaluator_id,
            "version": str(evaluator.get("version") or ""),
            "manifest_sha256": _floor_sha(evaluator.get("manifest_sha256"), "evaluation evaluator manifest_sha256", optional=True),
        },
        "fixture_sha256": _floor_sha(raw.get("fixture_sha256"), "evaluation fixture_sha256", optional=True),
        "metrics": metrics,
        "hard_gate_evidence": deepcopy(dict(raw.get("hard_gate_evidence") or {})),
        "notes": deepcopy(list(raw.get("notes") or [])),
        "metadata": deepcopy(dict(raw.get("metadata") or {})),
    }
    out["evaluation_sha256"] = floor_sha256_json(out)
    _floor_check_supplied_hash(raw, out, "evaluation_sha256", "evaluation ledger")
    return out


def floor_seal_conformance_report(value: Mapping[str, Any]) -> dict[str, Any]:
    raw = deepcopy(dict(value))
    if int(raw.get("schema_version") or FLOOR_SCHEMA_VERSION) != FLOOR_SCHEMA_VERSION:
        raise FloorError("unsupported conformance report schema")
    if str(raw.get("kind") or "earcrate_floor_conformance_report") != "earcrate_floor_conformance_report":
        raise FloorError("unsupported conformance report kind")
    requested = _floor_positive_int(raw.get("requested_runs"), "conformance requested_runs")
    completed = _floor_positive_int(raw.get("completed_runs", 0), "conformance completed_runs", allow_zero=True)
    if completed > requested:
        raise FloorError("conformance completed_runs cannot exceed requested_runs")
    runs = deepcopy(list(raw.get("runs") or []))
    failures = deepcopy(list(raw.get("failures") or []))
    if len(runs) != completed:
        raise FloorError("conformance run count disagrees with completed_runs")
    out = {
        "schema_version": FLOOR_SCHEMA_VERSION,
        "kind": "earcrate_floor_conformance_report",
        "requested_runs": requested,
        "completed_runs": completed,
        "runs": runs,
        "failures": failures,
        "checks": deepcopy(dict(raw.get("checks") or {})),
        "complete": bool(raw.get("complete", False)),
        "quality_claimed": bool(raw.get("quality_claimed", False)),
        "selection_authority": bool(raw.get("selection_authority", False)),
    }
    if out["quality_claimed"] or out["selection_authority"]:
        raise FloorError("protocol conformance may not claim quality or selection authority")
    out["conformance_sha256"] = floor_sha256_json(out)
    _floor_check_supplied_hash(raw, out, "conformance_sha256", "conformance report")
    return out


def floor_seal_tournament_report(value: Mapping[str, Any]) -> dict[str, Any]:
    raw = deepcopy(dict(value))
    if int(raw.get("schema_version") or FLOOR_SCHEMA_VERSION) != FLOOR_SCHEMA_VERSION:
        raise FloorError("unsupported tournament report schema")
    if str(raw.get("kind") or "earcrate_floor_tournament_report") != "earcrate_floor_tournament_report":
        raise FloorError("unsupported tournament report kind")
    competitors = deepcopy(list(raw.get("competitors") or []))
    if not competitors:
        raise FloorError("tournament report requires competitors")
    provider_ids = [str(row.get("provider_id") or "") for row in competitors]
    if not all(provider_ids) or len(provider_ids) != len(set(provider_ids)):
        raise FloorError("tournament competitors require unique provider IDs")
    out = {
        "schema_version": FLOOR_SCHEMA_VERSION,
        "kind": "earcrate_floor_tournament_report",
        "policy_sha256": _floor_sha(raw.get("policy_sha256"), "tournament policy_sha256"),
        "request_sha256": _floor_sha(raw.get("request_sha256"), "tournament request_sha256"),
        "competitors": competitors,
        "winner": deepcopy(raw.get("winner")),
        "winner_semantics": str(raw.get("winner_semantics") or ""),
        "canonical_authority": bool(raw.get("canonical_authority", False)),
        "selection_requires_earcrate_adjudication": bool(raw.get("selection_requires_earcrate_adjudication", True)),
        "quality_is_distinct_from_protocol_conformance": bool(raw.get("quality_is_distinct_from_protocol_conformance", True)),
    }
    if out["canonical_authority"]:
        raise FloorError("tournament winner may not claim canonical authority")
    if not out["selection_requires_earcrate_adjudication"]:
        raise FloorError("tournament report must retain EarCrate adjudication")
    out["tournament_sha256"] = floor_sha256_json(out)
    _floor_check_supplied_hash(raw, out, "tournament_sha256", "tournament report")
    return out


def floor_seal_floor_crate(value: Mapping[str, Any]) -> dict[str, Any]:
    raw = deepcopy(dict(value))
    if int(raw.get("schema_version") or FLOOR_SCHEMA_VERSION) != FLOOR_SCHEMA_VERSION:
        raise FloorError("unsupported Floor crate schema")
    if str(raw.get("kind") or "earcrate_floor_crate") != "earcrate_floor_crate":
        raise FloorError("unsupported Floor crate kind")
    files = deepcopy(list(raw.get("files") or []))
    paths = [str(row.get("path") or "") for row in files]
    if not files or not all(paths) or len(paths) != len(set(paths)):
        raise FloorError("Floor crate requires unique nonempty file paths")
    for index, row in enumerate(files):
        _floor_sha(row.get("sha256"), f"Floor crate file {index} sha256")
        _floor_positive_int(row.get("size_bytes", 0), f"Floor crate file {index} size_bytes", allow_zero=True)
    out = {
        "schema_version": FLOOR_SCHEMA_VERSION,
        "kind": "earcrate_floor_crate",
        "provider_manifest_sha256": _floor_sha(raw.get("provider_manifest_sha256"), "crate provider_manifest_sha256"),
        "request_sha256": _floor_sha(raw.get("request_sha256"), "crate request_sha256"),
        "result_sha256": _floor_sha(raw.get("result_sha256"), "crate result_sha256"),
        "invocation_receipt_sha256": _floor_sha(raw.get("invocation_receipt_sha256"), "crate invocation_receipt_sha256"),
        "files": files,
        "source_media_copied": bool(raw.get("source_media_copied", False)),
        "derived_artifacts_copied": deepcopy(list(raw.get("derived_artifacts_copied") or [])),
        "standards_mappings": deepcopy(list(raw.get("standards_mappings") or [])),
        "standards_certification_claimed": bool(raw.get("standards_certification_claimed", False)),
    }
    if out["source_media_copied"]:
        raise FloorError("Floor crate v1 may not claim source media was copied")
    if out["standards_certification_claimed"]:
        raise FloorError("Floor crate mappings may not claim standards certification")
    out["crate_sha256"] = floor_sha256_json(out)
    _floor_check_supplied_hash(raw, out, "crate_sha256", "Floor crate")
    return out


def floor_object_kind(value: Mapping[str, Any]) -> str:
    return str(value.get("kind") or "")


def floor_verify_object(value: Mapping[str, Any]) -> dict[str, Any]:
    """Validate any normative Floor object and return its sealed form."""
    kind = floor_object_kind(value)
    if kind == "earcrate_floor_provider_manifest":
        return floor_seal_provider_manifest(value)
    if kind == "earcrate_floor_provider_request":
        return floor_seal_provider_request(value)
    if kind == "earcrate_floor_time_map":
        return floor_seal_time_map(value)
    if kind == "earcrate_floor_phrase_contract":
        return floor_seal_phrase_contract(value)
    if kind == "earcrate_floor_rights_envelope":
        return floor_seal_rights_envelope(value)
    if kind == "earcrate_floor_review_patch":
        return floor_seal_review_patch(value)
    if kind == "earcrate_floor_invocation_receipt":
        return floor_seal_invocation_receipt(value)
    if kind == "earcrate_floor_evaluation_policy":
        return floor_seal_evaluation_policy(value)
    if kind == "earcrate_floor_evaluation_ledger":
        return floor_seal_evaluation_ledger(value)
    if kind == "earcrate_floor_conformance_report":
        return floor_seal_conformance_report(value)
    if kind == "earcrate_floor_tournament_report":
        return floor_seal_tournament_report(value)
    if kind == "earcrate_floor_crate":
        return floor_seal_floor_crate(value)
    if kind == "earcrate_floor_provider_result":
        raise FloorError("ProviderResult verification requires its ProviderRequest and ProviderManifest")
    raise FloorError(f"unsupported Floor object kind: {kind!r}")


__all__ = [name for name in globals() if name.startswith("floor_") or name.startswith("FLOOR_") or name in {"FloorError", "FloorProtocolError"}]
