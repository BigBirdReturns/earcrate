"""The mastering contract, not a mastering aesthetic.

A1-07's master is one solved linear gain with no limiter, EQ, multiband, resampling or
dither. That was the right chain for that track and it is *not* the shared default. A
future track may legitimately need a limiter, or an EQ move, or a resample to a
delivery rate, and a framework that forbids those globally would force the next lane
to work around it -- which is how a framework starts losing to the music.

So the framework verifies a contract:

* the executed stages equal the declared stages, in exact order;
* nothing undeclared ran;
* the tools that ran are the tools the plan named, by identity;
* determinism satisfies the policy the plan declared;
* the signal gates were **measured**, not copied from the targets;
* the declared refusal conditions are executable, not decorative;
* the output binds stable authority.

The legacy A1-07 chain is expressed in this contract as a conformance fixture, so the
framework is tested against a real lane and not only against what it was designed for.
That fixture lives in the test suite rather than here: a shared module naming a track
is the first step toward a shared module branching on one. A1-07's writer is not moved
or rewritten either -- it lives inside the audio-affecting file set, and touching it
would move the digest identifying the code that produced the accepted master.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from ..evidence.identity import canonical_json_bytes, sha256_bytes

# Every stage the framework can express. Presence here is not permission: a stage may
# only run if a plan declares it.
KNOWN_STAGES = ("linear_gain", "equalization", "compression", "limiting", "multiband",
                "resampling", "dither", "trim", "fade", "channel_map")

STOCHASTIC_STAGES = frozenset({"dither"})

DETERMINISM_POLICIES = ("bit_exact_across_executions", "pcm_exact_container_may_differ",
                        "not_required")


class MasteringContractError(RuntimeError):
    pass


@dataclass(frozen=True)
class Stage:
    name: str
    parameters: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.name not in KNOWN_STAGES:
            raise MasteringContractError(
                f"unknown stage {self.name!r}; extend KNOWN_STAGES deliberately")

    def as_dict(self) -> dict[str, Any]:
        return {"name": self.name, "parameters": dict(self.parameters)}


@dataclass(frozen=True)
class SignalTarget:
    """A declared constraint and how it is checked. `tolerance` is part of the claim."""

    metric: str
    comparison: str  # "<=", ">=", "==", "within"
    value: float
    tolerance: float = 0.0

    def satisfied_by(self, measured: float) -> bool:
        if self.comparison == "<=":
            return measured <= self.value + self.tolerance
        if self.comparison == ">=":
            return measured >= self.value - self.tolerance
        if self.comparison in ("==", "within"):
            return abs(measured - self.value) <= self.tolerance
        raise MasteringContractError(f"unknown comparison {self.comparison!r}")

    def as_dict(self) -> dict[str, Any]:
        return {"metric": self.metric, "comparison": self.comparison,
                "value": self.value, "tolerance": self.tolerance}


@dataclass(frozen=True)
class MasteringPlan:
    """What a master is allowed to be, declared before it is cut."""

    track_id: str
    source_authority: Mapping[str, Any]
    stages: tuple[Stage, ...]
    allowed_tools: Mapping[str, str]           # tool name -> binary/checkpoint identity
    sample_format: Mapping[str, Any]
    dither_allowed: bool
    determinism_policy: str
    signal_targets: tuple[SignalTarget, ...]
    refusal_conditions: tuple[str, ...]
    section_invariants: Mapping[str, Any] = field(default_factory=dict)
    output_identity_requirements: tuple[str, ...] = ("canonical_pcm_sha256",)

    def __post_init__(self) -> None:
        if self.determinism_policy not in DETERMINISM_POLICIES:
            raise MasteringContractError(
                f"unknown determinism policy {self.determinism_policy!r}")
        declared = {stage.name for stage in self.stages}
        if not self.dither_allowed and "dither" in declared:
            raise MasteringContractError(
                "the plan declares a dither stage while forbidding dither")
        if self.determinism_policy == "bit_exact_across_executions" and \
                declared & STOCHASTIC_STAGES:
            raise MasteringContractError(
                f"{sorted(declared & STOCHASTIC_STAGES)} is stochastic and cannot satisfy "
                "bit-exact reproduction; choose one or the other deliberately")
        if not self.refusal_conditions:
            raise MasteringContractError(
                "a plan with no refusal conditions cannot fail closed; declare what stops it")
        if not self.allowed_tools:
            raise MasteringContractError("a plan must name the tools it permits, by identity")

    @property
    def stage_names(self) -> tuple[str, ...]:
        return tuple(stage.name for stage in self.stages)

    def as_dict(self) -> dict[str, Any]:
        return {
            "track_id": self.track_id,
            "source_authority": dict(self.source_authority),
            "stages": [stage.as_dict() for stage in self.stages],
            "allowed_tools": dict(self.allowed_tools),
            "sample_format": dict(self.sample_format),
            "dither_allowed": self.dither_allowed,
            "determinism_policy": self.determinism_policy,
            "signal_targets": [target.as_dict() for target in self.signal_targets],
            "refusal_conditions": list(self.refusal_conditions),
            "section_invariants": dict(self.section_invariants),
            "output_identity_requirements": list(self.output_identity_requirements),
        }

    def plan_sha256(self) -> str:
        return sha256_bytes(canonical_json_bytes(self.as_dict()))


def validate_execution(plan: MasteringPlan, executed: Mapping[str, Any]) -> list[str]:
    """Findings: exactly how an execution failed to be the master that was declared."""
    problems: list[str] = []

    ran: Sequence[Mapping[str, Any]] = executed.get("stages") or ()
    ran_names = tuple(str(row.get("name")) for row in ran)
    if ran_names != plan.stage_names:
        problems.append(
            f"executed stages {list(ran_names)} do not match declared "
            f"{list(plan.stage_names)} in content or order")
    for declared_stage, actual in zip(plan.stages, ran):
        for key, value in declared_stage.parameters.items():
            if (actual.get("parameters") or {}).get(key) != value:
                problems.append(
                    f"stage {declared_stage.name}: {key} ran as "
                    f"{(actual.get('parameters') or {}).get(key)!r}, declared {value!r}")

    for tool, identity in (executed.get("tools") or {}).items():
        if tool not in plan.allowed_tools:
            problems.append(f"undeclared tool {tool!r} took part in the master")
        elif plan.allowed_tools[tool] != identity:
            problems.append(
                f"tool {tool!r} ran as {identity!r}, plan requires {plan.allowed_tools[tool]!r}")

    if not plan.dither_allowed and executed.get("dither_applied"):
        problems.append("dither was applied under a plan that forbids it")

    determinism = executed.get("determinism") or {}
    if plan.determinism_policy == "bit_exact_across_executions":
        if int(determinism.get("executions") or 0) < 2:
            problems.append("bit-exact reproduction claimed from fewer than two executions")
        if not determinism.get("canonical_pcm_equal"):
            problems.append("the executions disagree on canonical PCM")
        if not determinism.get("container_equal"):
            problems.append("the executions disagree on container bytes")
    elif plan.determinism_policy == "pcm_exact_container_may_differ":
        if not determinism.get("canonical_pcm_equal"):
            problems.append("the executions disagree on canonical PCM")

    # Measured, not copied. A report that restates the targets without saying what
    # measured them is not evidence, however plausible its numbers look.
    measurement = executed.get("measurement") or {}
    measured = measurement.get("values") or {}
    if not measurement.get("measured_by"):
        problems.append("signal gates carry no measuring tool identity; they were not measured")
    for target in plan.signal_targets:
        if target.metric not in measured:
            problems.append(f"signal target {target.metric} was never measured")
        elif not target.satisfied_by(float(measured[target.metric])):
            problems.append(
                f"{target.metric}={measured[target.metric]} misses {target.comparison} "
                f"{target.value} (tolerance {target.tolerance})")

    exercised = set(executed.get("refusals_exercised") or ())
    unexercised = [row for row in plan.refusal_conditions if row not in exercised]
    if unexercised:
        problems.append(
            f"declared refusal condition(s) never exercised: {unexercised}. A refusal that "
            "has never fired is a comment.")

    for name in plan.output_identity_requirements:
        if not (executed.get("output") or {}).get(name):
            problems.append(f"the output does not carry {name}")
    if not (executed.get("output") or {}).get("authority_sha256"):
        problems.append("the output binds no stable authority identity")

    return problems
