from __future__ import annotations

"""Language-neutral, content-addressed objects for EarCrate Floor."""

import hashlib
import json
import math
import os
import tempfile
from copy import deepcopy
from fractions import Fraction
from pathlib import Path
from typing import Any, Mapping, Sequence

VERSION = 1
PROTOCOL = "stdio-json-v1"
K_MANIFEST = "earcrate_floor_provider_manifest"
K_REQUEST = "earcrate_floor_provider_request"
K_RESULT = "earcrate_floor_provider_result"
K_TIME_MAP = "earcrate_floor_time_map"
K_PHRASE = "earcrate_floor_phrase_contract"
K_RIGHTS = "earcrate_floor_rights_envelope"
K_REVIEW = "earcrate_floor_review_patch"
K_RECEIPT = "earcrate_floor_invocation_receipt"
K_POLICY = "earcrate_floor_evaluation_policy"
K_EVALUATION = "earcrate_floor_evaluation_ledger"
K_TOURNAMENT = "earcrate_floor_tournament_report"
K_CRATE = "earcrate_floor_crate"

BRANCHES = ("score", "symbolic", "audio", "convergence", "performance", "review", "evolution")
TIERS = (
    "unspecified", "authoritative_score", "community_symbolic_witness",
    "blind_audio_inference", "cross_modal_accepted", "performance_realization",
    "human_review", "campaign_evidence",
)
ANCESTORS = {
    "score": {"score"}, "symbolic": {"symbolic"}, "audio": {"audio"},
    "convergence": {"score", "symbolic", "audio", "convergence"},
    "performance": {"score", "symbolic", "audio", "convergence", "performance"},
    "review": {"performance", "review"},
    "evolution": set(BRANCHES),
}
BRANCH_TIERS = {
    "score": {"unspecified", "authoritative_score"},
    "symbolic": {"unspecified", "community_symbolic_witness"},
    "audio": {"unspecified", "blind_audio_inference"},
    "convergence": {"unspecified", "cross_modal_accepted"},
    "performance": {"unspecified", "performance_realization"},
    "review": {"unspecified", "human_review"},
    "evolution": {"unspecified", "campaign_evidence"},
}
OUTPUT_KINDS = ("observation", "candidate", "measurement", "refusal", "derived_artifact", "review_patch")
FORBIDDEN_AUTHORITY = {
    "song_genome", "performance_score", "mix_score", "accepted_score",
    "accepted_performance", "canonical_state", "selected_provider",
    "selected_winner", "tournament_winner", "applied_review_patch",
    "legal_determination", "rights_cleared", "whole_organism_passed",
    "buffalo_gate_passed",
}


class FloorError(ValueError):
    pass


def jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise FloorError("non-finite number in Floor object")
        return value
    if isinstance(value, Mapping):
        return {str(k): jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [jsonable(v) for v in value]
    if isinstance(value, (set, frozenset)):
        return sorted((jsonable(v) for v in value), key=lambda v: canonical_bytes(v))
    if isinstance(value, Path):
        return str(value)
    if hasattr(value, "to_dict"):
        return jsonable(value.to_dict())
    if hasattr(value, "item"):
        return jsonable(value.item())
    return str(value)


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(jsonable(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def sha_json(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def sha_file(path: str | Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def read_json(path: str | Path) -> dict[str, Any]:
    source = Path(path).expanduser().resolve()
    try:
        value = json.loads(source.read_text(encoding="utf-8"))
    except Exception as exc:
        raise FloorError(f"cannot read Floor JSON {source}: {exc}") from exc
    if not isinstance(value, dict):
        raise FloorError("Floor JSON must contain an object")
    return value


def write_json(path: str | Path, value: Mapping[str, Any]) -> Path:
    target = Path(path).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(jsonable(dict(value)), ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n"
    fd, temp_name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=str(target.parent))
    temp = Path(temp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(text); f.flush(); os.fsync(f.fileno())
        os.replace(temp, target)
    finally:
        if temp.exists(): temp.unlink()
    return target


def _text(value: Any, field: str) -> str:
    result = str(value or "").strip()
    if not result: raise FloorError(f"{field} must be nonempty")
    return result


def _sha(value: Any, field: str, optional: bool = False) -> str | None:
    result = str(value or "").strip().lower()
    if optional and not result: return None
    if len(result) != 64 or any(c not in "0123456789abcdef" for c in result):
        raise FloorError(f"{field} must be a lowercase SHA-256")
    return result


def _int(value: Any, field: str, positive: bool = False) -> int:
    try: result = int(value)
    except Exception as exc: raise FloorError(f"{field} must be an integer") from exc
    if result < 0 or (positive and result == 0): raise FloorError(f"{field} must be {'positive' if positive else 'nonnegative'}")
    return result


def _float(value: Any, field: str) -> float:
    try: result = float(value)
    except Exception as exc: raise FloorError(f"{field} must be numeric") from exc
    if not math.isfinite(result): raise FloorError(f"{field} must be finite")
    return result


def rational(value: Any, field: str = "time") -> str:
    try:
        number = value if isinstance(value, Fraction) else Fraction(str(value))
    except Exception as exc:
        raise FloorError(f"{field} must be an exact rational") from exc
    return str(number.numerator) if number.denominator == 1 else f"{number.numerator}/{number.denominator}"


def evidence(branch: Any, tier: Any, ancestors: Sequence[Any] = ()) -> tuple[str, str, list[str]]:
    b = _text(branch, "evidence branch").lower()
    t = str(tier or "unspecified").strip().lower()
    if b not in BRANCHES: raise FloorError(f"unsupported evidence branch: {b}")
    if t not in TIERS or t not in BRANCH_TIERS[b]: raise FloorError(f"tier {t!r} cannot be claimed by branch {b!r}")
    rows = sorted({str(v).lower() for v in (ancestors or [b])} | {b})
    if any(v not in BRANCHES for v in rows): raise FloorError("unknown ancestor branch")
    bad = sorted(set(rows) - ANCESTORS[b])
    if bad: raise FloorError(f"{b} evidence is tainted by forbidden ancestors: {bad}")
    return b, t, rows


def _scan_authority(value: Any, path: str = "payload") -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            normalized = str(key).strip().lower().replace("-", "_").replace(" ", "_")
            harmless = item is False or item is None or item == "" or item == 0
            if normalized in FORBIDDEN_AUTHORITY and not harmless:
                raise FloorError(f"provider output claims forbidden authority at {path}/{key}")
            _scan_authority(item, f"{path}/{key}")
    elif isinstance(value, (list, tuple)):
        for i, item in enumerate(value): _scan_authority(item, f"{path}/{i}")


def artifact_ref(raw: Mapping[str, Any], *, identity: bool = True, relative: bool = False, defaults: Mapping[str, Any] | None = None) -> dict[str, Any]:
    row = {**dict(defaults or {}), **dict(raw)}
    b, t, a = evidence(row.get("branch", "audio"), row.get("tier", "unspecified"), row.get("ancestor_branches") or [])
    path = str(row.get("path") or "")
    if path and not relative: path = str(Path(path).expanduser().resolve())
    return {
        "artifact_id": _text(row.get("artifact_id"), "artifact_id"),
        "sha256": _sha(row.get("sha256"), "artifact sha256", optional=not identity),
        "size_bytes": _int(row.get("size_bytes", 0), "artifact size_bytes"),
        "media_type": _text(row.get("media_type", "application/octet-stream"), "artifact media_type"),
        "role": str(row.get("role") or ""), "branch": b, "tier": t,
        "ancestor_branches": a, "path": path, "uri": str(row.get("uri") or ""),
        "metadata": deepcopy(dict(row.get("metadata") or {})),
    }


def semantic_artifact(row: Mapping[str, Any]) -> dict[str, Any]:
    value = deepcopy(dict(row)); value.pop("path", None); value.pop("uri", None); return value


def seal_manifest(value: Mapping[str, Any]) -> dict[str, Any]:
    raw = deepcopy(dict(value))
    if int(raw.get("schema_version", 0)) != VERSION or raw.get("kind") != K_MANIFEST: raise FloorError("unsupported provider manifest")
    entry = dict(raw.get("entrypoint") or {}); protocol = str(entry.get("protocol") or PROTOCOL); argv = [str(v) for v in entry.get("argv") or []]
    if protocol not in {PROTOCOL, "python-in-process-v1"}: raise FloorError("unsupported entrypoint protocol")
    if protocol == PROTOCOL and not argv: raise FloorError("stdio provider requires argv")
    runtime = dict(raw.get("runtime") or {}); det = str(runtime.get("determinism") or "unknown")
    if det not in {"deterministic", "seeded", "best_effort", "unknown"}: raise FloorError("unsupported determinism declaration")
    may_emit = sorted({_text(v, "may_emit") for v in (dict(raw.get("authority") or {}).get("may_emit") or OUTPUT_KINDS)})
    unknown = sorted(set(may_emit) - set(OUTPUT_KINDS))
    if unknown: raise FloorError(f"unsupported output kinds: {unknown}")
    ev = dict(raw.get("evidence") or {})
    accepted_branches = sorted({_text(v, "accepted branch").lower() for v in ev.get("accepted_branches") or BRANCHES})
    accepted_tiers = sorted({str(v).lower() for v in ev.get("accepted_tiers") or TIERS})
    if any(v not in BRANCHES for v in accepted_branches) or any(v not in TIERS for v in accepted_tiers): raise FloorError("invalid evidence declaration")
    supply = dict(raw.get("supply_chain") or {})
    models = []
    for item in supply.get("model_artifacts") or []:
        models.append({"artifact_id": _text(item.get("artifact_id"), "model artifact_id"), "sha256": _sha(item.get("sha256"), "model sha256"), "size_bytes": _int(item.get("size_bytes", 0), "model size"), "license_expression": str(item.get("license_expression") or "NOASSERTION"), "source_uri": str(item.get("source_uri") or "")})
    out = {
        "schema_version": VERSION, "kind": K_MANIFEST,
        "provider_id": _text(raw.get("provider_id"), "provider_id"), "provider_version": _text(raw.get("provider_version"), "provider_version"),
        "display_name": str(raw.get("display_name") or raw.get("provider_id") or ""), "description": str(raw.get("description") or ""),
        "capabilities": sorted({_text(v, "capability") for v in raw.get("capabilities") or []}),
        "entrypoint": {"protocol": protocol, "argv": argv, "working_directory": str(entry.get("working_directory") or "manifest_dir")},
        "runtime": {"language": str(runtime.get("language") or "unknown"), "requires_network": bool(runtime.get("requires_network", False)), "determinism": det, "timeout_seconds": _int(runtime.get("timeout_seconds", 300), "timeout", True), "max_stdout_bytes": _int(runtime.get("max_stdout_bytes", 8<<20), "stdout limit", True), "max_stderr_bytes": _int(runtime.get("max_stderr_bytes", 8<<20), "stderr limit", True), "max_artifact_bytes": _int(runtime.get("max_artifact_bytes", 2<<30), "artifact limit", True)},
        "evidence": {"accepted_branches": accepted_branches, "accepted_tiers": accepted_tiers},
        "authority": {"may_emit": may_emit, "may_not_emit": sorted(FORBIDDEN_AUTHORITY | set(dict(raw.get("authority") or {}).get("may_not_emit") or [])), "canonical_authority": False, "may_apply_review_patches": False, "may_select_tournament_winner": False},
        "supply_chain": {"license_expression": str(supply.get("license_expression") or "NOASSERTION"), "source_uri": str(supply.get("source_uri") or ""), "source_revision": str(supply.get("source_revision") or ""), "executable_sha256": _sha(supply.get("executable_sha256"), "executable sha256", True), "model_artifacts": sorted(models, key=lambda r:r["artifact_id"]), "signatures": deepcopy(list(supply.get("signatures") or []))},
        "metadata": deepcopy(dict(raw.get("metadata") or {})),
    }
    if not out["capabilities"]: raise FloorError("provider requires capabilities")
    out["manifest_sha256"] = sha_json(out)
    if raw.get("manifest_sha256") and raw["manifest_sha256"] != out["manifest_sha256"]: raise FloorError("manifest_sha256 mismatch")
    return out


def seal_request(value: Mapping[str, Any]) -> dict[str, Any]:
    raw = deepcopy(dict(value))
    if int(raw.get("schema_version", 0)) != VERSION or raw.get("kind") != K_REQUEST: raise FloorError("unsupported provider request")
    ev = dict(raw.get("evidence") or {}); b,t,a = evidence(ev.get("branch","audio"), ev.get("tier","unspecified"), ev.get("ancestor_branches") or [])
    inputs = [artifact_ref(v) for v in raw.get("inputs") or []]
    if not inputs or len({v["artifact_id"] for v in inputs}) != len(inputs): raise FloorError("request requires unique input artifacts")
    for item in inputs:
        if set(item["ancestor_branches"]) - ANCESTORS[b]: raise FloorError("request input ancestry is forbidden")
        if t == "blind_audio_inference" and item["branch"] != "audio": raise FloorError("blind audio request accepts audio inputs only")
    network = dict(raw.get("network_policy") or {}); ap = dict(raw.get("artifact_policy") or {})
    out = {"schema_version":VERSION,"kind":K_REQUEST,"capability":_text(raw.get("capability"),"capability"),"evidence":{"branch":b,"tier":t,"ancestor_branches":a,"prohibited_inputs":sorted({str(v) for v in ev.get("prohibited_inputs") or []})},"inputs":sorted(inputs,key=lambda r:r["artifact_id"]),"parameters":deepcopy(dict(raw.get("parameters") or {})),"seed":int(raw.get("seed",0)),"network_policy":{"allowed":bool(network.get("allowed",False)),"declared_hosts":sorted({str(v) for v in network.get("declared_hosts") or []})},"artifact_policy":{"output_dir":str(ap.get("output_dir") or ""),"max_total_bytes":_int(ap.get("max_total_bytes",2<<30),"max_total_bytes",True),"allow_source_media_copy":bool(ap.get("allow_source_media_copy",False))},"metadata":deepcopy(dict(raw.get("metadata") or {}))}
    out["request_sha256"] = sha_json(out)
    semantic = deepcopy(out); semantic["inputs"]=[semantic_artifact(v) for v in semantic["inputs"]]; semantic["artifact_policy"].pop("output_dir",None)
    out["request_semantic_sha256"] = sha_json(semantic)
    if raw.get("request_sha256") and raw["request_sha256"] != out["request_sha256"]: raise FloorError("request_sha256 mismatch")
    if raw.get("request_semantic_sha256") and raw["request_semantic_sha256"] != out["request_semantic_sha256"]: raise FloorError("request semantic hash mismatch")
    return out


def seal_result(value: Mapping[str, Any], manifest: Mapping[str, Any], request: Mapping[str, Any]) -> dict[str, Any]:
    raw=deepcopy(dict(value)); m=seal_manifest(manifest); q=seal_request(request)
    if int(raw.get("schema_version",VERSION))!=VERSION or raw.get("kind",K_RESULT)!=K_RESULT: raise FloorError("unsupported provider result")
    for field, expected in (("provider_id",m["provider_id"]),("provider_version",m["provider_version"]),("manifest_sha256",m["manifest_sha256"]),("request_sha256",q["request_sha256"]),("request_semantic_sha256",q["request_semantic_sha256"])):
        if str(raw.get(field) or expected)!=expected: raise FloorError(f"result {field} mismatch")
    status=str(raw.get("status") or "ok")
    if status not in {"ok","refused","failed"}: raise FloorError("invalid result status")
    outputs=[]
    for i,item in enumerate(raw.get("outputs") or []):
        kind=_text(item.get("output_kind"),"output_kind")
        if kind not in OUTPUT_KINDS or kind not in m["authority"]["may_emit"]: raise FloorError("output kind not permitted")
        b,t,a=evidence(item.get("branch",q["evidence"]["branch"]),item.get("tier",q["evidence"]["tier"]),item.get("ancestor_branches") or q["evidence"]["ancestor_branches"])
        if (b,t)!=(q["evidence"]["branch"],q["evidence"]["tier"]): raise FloorError("provider cannot relabel evidence")
        confidence=_float(item.get("confidence",1),"confidence")
        if not 0<=confidence<=1: raise FloorError("confidence outside [0,1]")
        payload=jsonable(item.get("payload")); _scan_authority(payload,f"outputs/{i}")
        if kind=="review_patch": seal_review(payload)
        outputs.append({"output_id":_text(item.get("output_id") or f"output_{i:04d}","output_id"),"output_kind":kind,"branch":b,"tier":t,"ancestor_branches":a,"confidence":confidence,"evidence_refs":sorted({str(v) for v in item.get("evidence_refs") or []}),"payload":payload,"metadata":deepcopy(dict(item.get("metadata") or {}))})
    if status=="ok" and not outputs: raise FloorError("successful result requires outputs")
    defaults={"branch":q["evidence"]["branch"],"tier":q["evidence"]["tier"],"ancestor_branches":q["evidence"]["ancestor_branches"]}
    artifacts=[artifact_ref(v,relative=True,defaults=defaults) for v in raw.get("artifacts") or []]
    if len({v["artifact_id"] for v in artifacts})!=len(artifacts): raise FloorError("duplicate result artifact_id")
    for item in artifacts:
        if item["path"] and (Path(item["path"]).is_absolute() or ".." in Path(item["path"]).parts): raise FloorError("result artifact path must be contained and relative")
    diagnostics=deepcopy(dict(raw.get("diagnostics") or {})); metadata=deepcopy(dict(raw.get("metadata") or {})); _scan_authority(diagnostics,"diagnostics"); _scan_authority(metadata,"metadata")
    out={"schema_version":VERSION,"kind":K_RESULT,"provider_id":m["provider_id"],"provider_version":m["provider_version"],"manifest_sha256":m["manifest_sha256"],"request_sha256":q["request_sha256"],"request_semantic_sha256":q["request_semantic_sha256"],"status":status,"outputs":sorted(outputs,key=lambda r:r["output_id"]),"artifacts":sorted(artifacts,key=lambda r:r["artifact_id"]),"diagnostics":diagnostics,"metadata":metadata,"canonical_authority":False}
    out["result_sha256"]=sha_json(out); semantic=deepcopy(out); semantic["artifacts"]=[semantic_artifact(v) for v in semantic["artifacts"]]; semantic.pop("diagnostics",None); out["result_semantic_sha256"]=sha_json(semantic)
    return out


def seal_time_map(value: Mapping[str,Any])->dict[str,Any]:
    raw=deepcopy(dict(value))
    if raw.get("kind")!=K_TIME_MAP or int(raw.get("schema_version",0))!=VERSION: raise FloorError("unsupported time map")
    rows=[]; previous=None
    for i,item in enumerate(raw.get("segments") or []):
        ts,te=rational(item.get("target_start")),rational(item.get("target_end")); ss,se=rational(item.get("source_start")),rational(item.get("source_end")); op=_text(item.get("operation"),"operation")
        if op not in {"continuous","jump","loop","retrigger","reverse","hold"}: raise FloorError("unsupported time operation")
        if Fraction(te)<=Fraction(ts) or (previous is not None and Fraction(ts)<previous): raise FloorError("overlapping or empty target interval")
        previous=Fraction(te); rows.append({"segment_id":_text(item.get("segment_id") or f"segment_{i:04d}","segment_id"),"target_start":ts,"target_end":te,"source_start":ss,"source_end":se,"operation":op,"loop_start":rational(item.get("loop_start",ss)),"loop_end":rational(item.get("loop_end",se)),"metadata":deepcopy(dict(item.get("metadata") or {}))})
    if not rows: raise FloorError("time map requires segments")
    out={"schema_version":VERSION,"kind":K_TIME_MAP,"source_artifact_id":_text(raw.get("source_artifact_id"),"source_artifact_id"),"target_domain":_text(raw.get("target_domain") or "performance_beats","target_domain"),"source_domain":_text(raw.get("source_domain") or "source_seconds","source_domain"),"segments":rows,"metadata":deepcopy(dict(raw.get("metadata") or {}))}; out["time_map_sha256"]=sha_json(out); return out


def seal_rights(value: Mapping[str,Any])->dict[str,Any]:
    raw=deepcopy(dict(value));
    if raw.get("kind",K_RIGHTS)!=K_RIGHTS or int(raw.get("schema_version",VERSION))!=VERSION: raise FloorError("unsupported rights envelope")
    assertions=[]
    for i,item in enumerate(raw.get("assertions") or []): assertions.append({"assertion_id":_text(item.get("assertion_id") or f"assertion_{i:04d}","assertion_id"),"predicate":_text(item.get("predicate"),"predicate"),"value":jsonable(item.get("value")),"evidence_refs":sorted({str(v) for v in item.get("evidence_refs") or []}),"asserted_by":str(item.get("asserted_by") or "unknown")})
    out={"schema_version":VERSION,"kind":K_RIGHTS,"asset_id":_text(raw.get("asset_id"),"asset_id"),"license_expression":str(raw.get("license_expression") or "NOASSERTION"),"policy":str(raw.get("policy") or "unknown"),"commercial_use":str(raw.get("commercial_use") or "unknown"),"attribution_required":str(raw.get("attribution_required") or "unknown"),"jurisdictions":sorted({str(v) for v in raw.get("jurisdictions") or []}),"purposes":sorted({str(v) for v in raw.get("purposes") or []}),"source_uri":str(raw.get("source_uri") or ""),"assertions":sorted(assertions,key=lambda r:r["assertion_id"]),"evidence_refs":sorted({str(v) for v in raw.get("evidence_refs") or []}),"legal_determination":False,"metadata":deepcopy(dict(raw.get("metadata") or {}))}; out["rights_sha256"]=sha_json(out); return out


def seal_phrase(value: Mapping[str,Any])->dict[str,Any]:
    raw=deepcopy(dict(value));
    if raw.get("kind")!=K_PHRASE or int(raw.get("schema_version",0))!=VERSION: raise FloorError("unsupported phrase contract")
    window=dict(raw.get("target_window") or {}); start,end=rational(window.get("start")),rational(window.get("end"));
    if Fraction(end)<=Fraction(start): raise FloorError("empty phrase window")
    meter=dict(raw.get("meter") or {}); transform=dict(raw.get("transform_policy") or {});
    def rng(name, default):
        values=list(transform.get(name) or default)
        if len(values)!=2: raise FloorError(f"{name} requires [min,max]")
        result=[_float(v,name) for v in values]
        if result[0]>result[1]: raise FloorError(f"invalid {name} range")
        return result
    constraints=[]
    for level in ("hard","soft"):
        for i,item in enumerate(raw.get(f"{level}_constraints") or []): constraints.append((level,{"constraint_id":_text(item.get("constraint_id"),"constraint_id"),"kind":_text(item.get("kind"),"constraint kind"),"operator":_text(item.get("operator"),"constraint operator"),"value":jsonable(item.get("value")),"unit":str(item.get("unit") or ""),"reason":str(item.get("reason") or ""),"evidence_refs":sorted({str(v) for v in item.get("evidence_refs") or []})}))
    ids=[row[1]["constraint_id"] for row in constraints]
    if len(ids)!=len(set(ids)): raise FloorError("duplicate phrase constraint_id")
    rights=seal_rights(raw.get("rights") or {"schema_version":1,"kind":K_RIGHTS,"asset_id":"unbound_phrase"})
    out={"schema_version":VERSION,"kind":K_PHRASE,"role":_text(raw.get("role"),"role"),"target_window":{"start":start,"end":end,"unit":str(window.get("unit") or "beats")},"meter":{"numerator":_int(meter.get("numerator",4),"meter numerator",True),"denominator":_int(meter.get("denominator",4),"meter denominator",True)},"transform_policy":{"tempo_ratio":rng("tempo_ratio",[1,1]),"transpose_semitones":rng("transpose_semitones",[0,0]),"gain_db":rng("gain_db",[0,0]),"reverse_allowed":bool(transform.get("reverse_allowed",False)),"loop_allowed":bool(transform.get("loop_allowed",False)),"slice_allowed":bool(transform.get("slice_allowed",True)),"keylock_required":bool(transform.get("keylock_required",False))},"hard_constraints":sorted([r for level,r in constraints if level=="hard"],key=lambda r:r["constraint_id"]),"soft_constraints":sorted([r for level,r in constraints if level=="soft"],key=lambda r:r["constraint_id"]),"identity_obligations":deepcopy(list(raw.get("identity_obligations") or [])),"future_obligations":deepcopy(list(raw.get("future_obligations") or [])),"evidence_refs":sorted({str(v) for v in raw.get("evidence_refs") or []}),"rights":rights,"metadata":deepcopy(dict(raw.get("metadata") or {}))}; out["contract_sha256"]=sha_json(out); out["contract_id"]=f"phrase_{out['contract_sha256'][:24]}"; return out


def seal_review(value: Mapping[str,Any])->dict[str,Any]:
    raw=deepcopy(dict(value));
    if raw.get("kind",K_REVIEW)!=K_REVIEW or int(raw.get("schema_version",VERSION))!=VERSION: raise FloorError("unsupported review patch")
    if bool(raw.get("applied",False)): raise FloorError("review patches must arrive unapplied")
    op=str(raw.get("operation") or "annotate")
    if op not in {"add","replace","remove","annotate","rerank"}: raise FloorError("unsupported review operation")
    pointer=str(raw.get("json_pointer") or "")
    if pointer and not pointer.startswith("/"): raise FloorError("invalid JSON pointer")
    out={"schema_version":VERSION,"kind":K_REVIEW,"target_revision_sha256":_sha(raw.get("target_revision_sha256"),"target revision"),"operation":op,"target_object_id":_text(raw.get("target_object_id"),"target_object_id"),"json_pointer":pointer,"value":jsonable(raw.get("value")),"reason":_text(raw.get("reason"),"reason"),"evidence_refs":sorted({str(v) for v in raw.get("evidence_refs") or []}),"invalidation_hints":sorted({str(v) for v in raw.get("invalidation_hints") or []}),"proposed_by":str(raw.get("proposed_by") or "unknown"),"applied":False,"metadata":deepcopy(dict(raw.get("metadata") or {}))}; out["patch_sha256"]=sha_json(out); out["patch_id"]=f"review_{out['patch_sha256'][:24]}"; return out


def seal_receipt(value: Mapping[str,Any])->dict[str,Any]:
    raw=deepcopy(dict(value));
    if raw.get("kind",K_RECEIPT)!=K_RECEIPT or int(raw.get("schema_version",VERSION))!=VERSION: raise FloorError("unsupported invocation receipt")
    checks=deepcopy(dict(raw.get("checks") or {})); required={"input_identities_verified","result_schema_verified","artifact_paths_contained","artifact_identities_verified","authority_boundary_verified"}
    if required-set(checks): raise FloorError("invocation receipt omits required checks")
    out={"schema_version":VERSION,"kind":K_RECEIPT,"provider_id":_text(raw.get("provider_id"),"provider_id"),"provider_version":_text(raw.get("provider_version"),"provider_version"),"manifest_sha256":_sha(raw.get("manifest_sha256"),"manifest sha"),"request_sha256":_sha(raw.get("request_sha256"),"request sha"),"request_semantic_sha256":_sha(raw.get("request_semantic_sha256"),"request semantic sha"),"result_sha256":_sha(raw.get("result_sha256"),"result sha"),"result_semantic_sha256":_sha(raw.get("result_semantic_sha256"),"result semantic sha"),"executable_sha256":_sha(raw.get("executable_sha256"),"executable sha",True),"argv":[str(v) for v in raw.get("argv") or []],"returncode":int(raw.get("returncode",0)),"stdout_sha256":_sha(raw.get("stdout_sha256"),"stdout sha"),"stderr_sha256":_sha(raw.get("stderr_sha256"),"stderr sha"),"input_artifacts":deepcopy(list(raw.get("input_artifacts") or [])),"output_artifacts":deepcopy(list(raw.get("output_artifacts") or [])),"repeatability":deepcopy(dict(raw.get("repeatability") or {})),"network_policy":deepcopy(dict(raw.get("network_policy") or {})),"checks":checks,"complete":bool(raw.get("complete",False)),"canonical_authority":False,"metadata":deepcopy(dict(raw.get("metadata") or {}))}; semantic=deepcopy(out); semantic["metadata"].pop("duration_seconds",None); out["receipt_semantic_sha256"]=sha_json(semantic); out["receipt_sha256"]=sha_json(out); return out


def seal_policy(value: Mapping[str,Any])->dict[str,Any]:
    raw=deepcopy(dict(value));
    if raw.get("kind",K_POLICY)!=K_POLICY or int(raw.get("schema_version",VERSION))!=VERSION: raise FloorError("unsupported evaluation policy")
    gates=[{"gate_id":_text(v.get("gate_id") or f"gate_{i}","gate_id"),"metric":_text(v.get("metric"),"metric"),"operator":_text(v.get("operator"),"operator"),"value":_float(v.get("value"),"gate value")} for i,v in enumerate(raw.get("hard_gates") or [])]
    stages=[]
    for i,stage in enumerate(raw.get("objective_stages") or []):
        metrics=[]
        for metric in stage.get("metrics") or []:
            direction=str(metric.get("direction") or "max")
            if direction not in {"max","min"}: raise FloorError("metric direction must be max/min")
            metrics.append({"metric":_text(metric.get("metric"),"metric"),"weight":_float(metric.get("weight",1),"weight"),"direction":direction})
        if not metrics: raise FloorError("objective stage requires metrics")
        stages.append({"stage_id":_text(stage.get("stage_id") or f"stage_{i}","stage_id"),"metrics":metrics})
    if not stages: raise FloorError("policy requires objective stages")
    out={"schema_version":VERSION,"kind":K_POLICY,"policy_id":_text(raw.get("policy_id"),"policy_id"),"require_independent_evaluator":bool(raw.get("require_independent_evaluator",True)),"hard_gates":gates,"objective_stages":stages,"metadata":deepcopy(dict(raw.get("metadata") or {}))}; out["policy_sha256"]=sha_json(out); return out


def _compare(actual:float,op:str,expected:float)->bool:
    return {"gte":actual>=expected,"gt":actual>expected,"lte":actual<=expected,"lt":actual<expected,"eq":actual==expected,"ne":actual!=expected}.get(op,False)


def build_evaluation(policy:Mapping[str,Any],provider_id:str,provider_version:str,result_semantic_sha256:str,evaluator_id:str,metrics:Mapping[str,Any])->dict[str,Any]:
    p=seal_policy(policy)
    if p["require_independent_evaluator"] and provider_id==evaluator_id: raise FloorError("evaluator must be independent")
    values={str(k):_float(v,f"metric {k}") for k,v in metrics.items()}; gates=[]
    for g in p["hard_gates"]:
        actual=values.get(g["metric"]); gates.append({**g,"actual":actual,"passed":actual is not None and _compare(actual,g["operator"],g["value"])})
    stages=[]; vector=[]
    for stage in p["objective_stages"]:
        complete=True; score=0.0; terms=[]
        for m in stage["metrics"]:
            value=values.get(m["metric"]); complete &= value is not None; contribution=None if value is None else (value if m["direction"]=="max" else -value)*m["weight"]
            if contribution is not None: score+=contribution
            terms.append({**m,"value":value,"contribution":contribution})
        score=round(score,12) if complete else None; vector.append(score); stages.append({"stage_id":stage["stage_id"],"complete":complete,"score":score,"terms":terms})
    out={"schema_version":VERSION,"kind":K_EVALUATION,"policy_sha256":p["policy_sha256"],"provider_id":_text(provider_id,"provider_id"),"provider_version":_text(provider_version,"provider_version"),"result_semantic_sha256":_sha(result_semantic_sha256,"result sha"),"evaluator_id":_text(evaluator_id,"evaluator_id"),"independent_evaluator":provider_id!=evaluator_id,"metrics":dict(sorted(values.items())),"hard_gates":gates,"hard_gates_passed":all(v["passed"] for v in gates),"objective_stages":stages,"rank_vector":vector,"canonical_authority":False}; out["evaluation_sha256"]=sha_json(out); return out


def build_tournament(policy:Mapping[str,Any],evaluations:Sequence[Mapping[str,Any]])->dict[str,Any]:
    p=seal_policy(policy); rows=[deepcopy(dict(v)) for v in evaluations]
    if not rows: raise FloorError("tournament requires evaluations")
    for row in rows:
        if row.get("kind")!=K_EVALUATION or row.get("policy_sha256")!=p["policy_sha256"]: raise FloorError("incompatible evaluation")
        if p["require_independent_evaluator"] and not row.get("independent_evaluator"): raise FloorError("self evaluation in tournament")
    eligible=[v for v in rows if v.get("hard_gates_passed") and all(x is not None for x in v.get("rank_vector") or [])]
    eligible.sort(key=lambda v:(tuple(-float(x) for x in v["rank_vector"]),str(v["provider_id"]),str(v["result_semantic_sha256"])))
    ranked=[{"rank":i+1,"provider_id":v["provider_id"],"provider_version":v["provider_version"],"result_semantic_sha256":v["result_semantic_sha256"],"evaluation_sha256":v["evaluation_sha256"],"rank_vector":v["rank_vector"]} for i,v in enumerate(eligible)]
    out={"schema_version":VERSION,"kind":K_TOURNAMENT,"policy_sha256":p["policy_sha256"],"evaluation_count":len(rows),"eligible_count":len(ranked),"ranked":ranked,"benchmark_winner":ranked[0] if ranked else None,"winner_scope":"benchmark_winner_only","canonical_authority":False,"selection_applied":False}; out["tournament_sha256"]=sha_json(out); return out


def schema_bundle()->dict[str,dict[str,Any]]:
    sha={"type":"string","pattern":"^[0-9a-f]{64}$"}
    def schema(name,kind,required,props): return {"$schema":"https://json-schema.org/draft/2020-12/schema","$id":f"https://earcrate.local/schema/{name}","title":name,"type":"object","required":["schema_version","kind",*required],"properties":{"schema_version":{"const":1},"kind":{"const":kind},**props},"additionalProperties":True}
    return {
        "earcrate_floor_provider_manifest_v1.schema.json":schema("provider manifest",K_MANIFEST,["provider_id","provider_version","capabilities","manifest_sha256"],{"provider_id":{"type":"string"},"provider_version":{"type":"string"},"capabilities":{"type":"array","minItems":1},"manifest_sha256":sha}),
        "earcrate_floor_provider_request_v1.schema.json":schema("provider request",K_REQUEST,["capability","evidence","inputs","request_sha256","request_semantic_sha256"],{"capability":{"type":"string"},"evidence":{"type":"object"},"inputs":{"type":"array","minItems":1},"request_sha256":sha,"request_semantic_sha256":sha}),
        "earcrate_floor_provider_result_v1.schema.json":schema("provider result",K_RESULT,["provider_id","status","outputs","canonical_authority","result_sha256","result_semantic_sha256"],{"provider_id":{"type":"string"},"status":{"enum":["ok","refused","failed"]},"outputs":{"type":"array"},"canonical_authority":{"const":False},"result_sha256":sha,"result_semantic_sha256":sha}),
        "earcrate_floor_time_map_v1.schema.json":schema("time map",K_TIME_MAP,["segments","time_map_sha256"],{"segments":{"type":"array","minItems":1},"time_map_sha256":sha}),
        "earcrate_floor_phrase_contract_v1.schema.json":schema("phrase contract",K_PHRASE,["role","target_window","rights","contract_sha256","contract_id"],{"role":{"type":"string"},"target_window":{"type":"object"},"rights":{"type":"object"},"contract_sha256":sha,"contract_id":{"type":"string"}}),
        "earcrate_floor_rights_envelope_v1.schema.json":schema("rights envelope",K_RIGHTS,["asset_id","legal_determination","rights_sha256"],{"asset_id":{"type":"string"},"legal_determination":{"const":False},"rights_sha256":sha}),
        "earcrate_floor_review_patch_v1.schema.json":schema("review patch",K_REVIEW,["target_revision_sha256","operation","applied","patch_sha256","patch_id"],{"target_revision_sha256":sha,"operation":{"enum":["add","replace","remove","annotate","rerank"]},"applied":{"const":False},"patch_sha256":sha,"patch_id":{"type":"string"}}),
        "earcrate_floor_invocation_receipt_v1.schema.json":schema("invocation receipt",K_RECEIPT,["manifest_sha256","request_sha256","result_sha256","checks","complete","canonical_authority","receipt_sha256"],{"manifest_sha256":sha,"request_sha256":sha,"result_sha256":sha,"checks":{"type":"object"},"complete":{"type":"boolean"},"canonical_authority":{"const":False},"receipt_sha256":sha}),
        "earcrate_floor_evaluation_policy_v1.schema.json":schema("evaluation policy",K_POLICY,["policy_id","objective_stages","policy_sha256"],{"policy_id":{"type":"string"},"objective_stages":{"type":"array","minItems":1},"policy_sha256":sha}),
        "earcrate_floor_evaluation_ledger_v1.schema.json":schema("evaluation ledger",K_EVALUATION,["provider_id","evaluator_id","independent_evaluator","evaluation_sha256"],{"provider_id":{"type":"string"},"evaluator_id":{"type":"string"},"independent_evaluator":{"type":"boolean"},"evaluation_sha256":sha}),
        "earcrate_floor_tournament_report_v1.schema.json":schema("tournament report",K_TOURNAMENT,["winner_scope","canonical_authority","selection_applied","tournament_sha256"],{"winner_scope":{"const":"benchmark_winner_only"},"canonical_authority":{"const":False},"selection_applied":{"const":False},"tournament_sha256":sha}),
        "earcrate_floor_crate_v1.schema.json":schema("floor crate",K_CRATE,["files","source_media_copied","mapping_status","crate_sha256"],{"files":{"type":"array"},"source_media_copied":{"const":False},"mapping_status":{"const":"informative_not_certified"},"crate_sha256":sha}),
    }


def capability()->dict[str,Any]:
    out={"schema_version":1,"kind":"earcrate_floor_capability","ready":True,"protocol":PROTOCOL,"provider_output_kinds":list(OUTPUT_KINDS),"evidence_branches":list(BRANCHES),"evidence_tiers":list(TIERS),"canonical_authority":False,"subprocess_boundary":{"stdin":"one sealed request JSON object","stdout":"one provider result JSON object","shell":False,"artifact_directory":"FLOOR_ARTIFACT_DIR","network_declaration_checked":True,"os_network_sandbox_enforced":False},"schemas":sorted(schema_bundle()),"requires_network":False,"requires_cloud":False}; out["capability_sha256"]=sha_json(out); return out


# Compatibility names used by the public package and tests.
floor_jsonable=jsonable; floor_canonical_json_bytes=canonical_bytes; floor_sha256_json=sha_json; floor_sha256_file=sha_file; floor_read_json=read_json; floor_write_json_atomic=write_json
floor_seal_provider_manifest=seal_manifest; floor_load_provider_manifest=lambda p: seal_manifest(read_json(p)); floor_seal_provider_request=seal_request; floor_seal_provider_result=lambda v,manifest,request: seal_result(v,manifest,request)
floor_seal_time_map=seal_time_map; floor_seal_rights_envelope=seal_rights; floor_seal_phrase_contract=seal_phrase; floor_seal_review_patch=seal_review; floor_seal_invocation_receipt=seal_receipt; floor_seal_evaluation_policy=seal_policy
floor_build_evaluation_ledger=lambda *,policy,provider_id,provider_version,result_semantic_sha256,evaluator_id,metrics,evidence_refs=(): build_evaluation(policy,provider_id,provider_version,result_semantic_sha256,evaluator_id,metrics)
floor_build_tournament_report=lambda *,policy,evaluations: build_tournament(policy,evaluations)
floor_schema_bundle=schema_bundle; floor_capability=capability

__all__=[name for name in globals() if name.startswith("floor_") or name in {"FloorError","VERSION","PROTOCOL","K_MANIFEST","K_REQUEST","K_RESULT","K_TIME_MAP","K_PHRASE","K_RIGHTS","K_REVIEW","K_RECEIPT","K_POLICY","K_EVALUATION","K_TOURNAMENT","K_CRATE","BRANCHES","TIERS","OUTPUT_KINDS"}]
