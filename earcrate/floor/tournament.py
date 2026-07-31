from __future__ import annotations

"""Independent provider evaluation and lexicographic tournaments."""

from copy import deepcopy
from typing import Any, Mapping, Sequence

from .model import (
    FloorError,
    floor_seal_evaluation_ledger,
    floor_seal_evaluation_policy,
    floor_seal_tournament_report,
    floor_sha256_json,
)


def _floor_gate_compare(actual: Any, operator: str, expected: Any) -> bool:
    op = str(operator)
    if op == "gte":
        return float(actual) >= float(expected)
    if op == "gt":
        return float(actual) > float(expected)
    if op == "lte":
        return float(actual) <= float(expected)
    if op == "lt":
        return float(actual) < float(expected)
    if op == "eq":
        return actual == expected
    if op == "ne":
        return actual != expected
    if op == "true":
        return bool(actual) is True
    if op == "false":
        return bool(actual) is False
    raise FloorError(f"unsupported evaluation gate operator {operator!r}")


def _floor_stage_score(metrics: Mapping[str, float], weights: Mapping[str, float], *, lower_is_better: set[str]) -> float:
    score = 0.0
    for metric, weight in sorted(weights.items()):
        if metric not in metrics:
            raise FloorError(f"evaluation is missing metric {metric!r}")
        direction = -1.0 if metric in lower_is_better else 1.0
        score += direction * float(weight) * float(metrics[metric])
    return round(score, 12)


def floor_run_tournament(
    policy_value: Mapping[str, Any],
    evaluation_values: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    policy = floor_seal_evaluation_policy(policy_value)
    evaluations = [floor_seal_evaluation_ledger(value) for value in evaluation_values]
    if not evaluations:
        raise FloorError("provider tournament requires evaluation ledgers")
    request_hashes = {row["request_sha256"] for row in evaluations}
    if len(request_hashes) != 1:
        raise FloorError("provider tournament evaluations must refer to one request")
    provider_ids = [row["provider_id"] for row in evaluations]
    if len(provider_ids) != len(set(provider_ids)):
        raise FloorError("provider tournament has duplicate provider_id entries")

    lower = set(policy["lower_is_better"])
    competitors: list[dict[str, Any]] = []
    for evaluation in evaluations:
        gate_rows = []
        hard_pass = True
        for gate in policy["hard_gates"]:
            metric = gate["metric"]
            if metric in evaluation["metrics"]:
                actual = evaluation["metrics"][metric]
            else:
                actual = evaluation["hard_gate_evidence"].get(metric)
            passed = _floor_gate_compare(actual, gate["operator"], gate["value"])
            hard_pass = hard_pass and passed
            gate_rows.append({**gate, "actual": actual, "passed": passed})
        stage_vector = []
        if hard_pass:
            for stage in policy["lexicographic_stages"]:
                stage_vector.append(
                    {
                        "stage": stage["stage"],
                        "score": _floor_stage_score(evaluation["metrics"], stage["weights"], lower_is_better=lower),
                    }
                )
        competitors.append(
            {
                "provider_id": evaluation["provider_id"],
                "provider_manifest_sha256": evaluation["provider_manifest_sha256"],
                "result_sha256": evaluation["result_sha256"],
                "evaluation_sha256": evaluation["evaluation_sha256"],
                "evaluator_id": evaluation["evaluator"]["evaluator_id"],
                "hard_gates_passed": hard_pass,
                "hard_gates": gate_rows,
                "lexicographic_vector": stage_vector,
                "metrics": deepcopy(evaluation["metrics"]),
            }
        )

    eligible = [row for row in competitors if row["hard_gates_passed"]]
    if not eligible:
        winner = None
    else:
        def key(row: Mapping[str, Any]) -> tuple[Any, ...]:
            scores = tuple(float(stage["score"]) for stage in row["lexicographic_vector"])
            # Stable deterministic identity tie-breaker; higher scores win.
            return (*scores, str(row["provider_manifest_sha256"]))

        winner = max(eligible, key=key)
    report = {
        "schema_version": 1,
        "kind": "earcrate_floor_tournament_report",
        "policy_sha256": policy["policy_sha256"],
        "request_sha256": next(iter(request_hashes)),
        "competitors": sorted(competitors, key=lambda row: row["provider_id"]),
        "winner": None if winner is None else {
            "provider_id": winner["provider_id"],
            "provider_manifest_sha256": winner["provider_manifest_sha256"],
            "result_sha256": winner["result_sha256"],
            "lexicographic_vector": deepcopy(winner["lexicographic_vector"]),
        },
        "winner_semantics": "benchmark winner under this sealed policy and fixture only",
        "canonical_authority": False,
        "selection_requires_earcrate_adjudication": True,
        "quality_is_distinct_from_protocol_conformance": True,
    }
    return floor_seal_tournament_report(report)


__all__ = ["floor_run_tournament"]
