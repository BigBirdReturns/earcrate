from __future__ import annotations

import threading
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Mapping

from earcrate.midi.model import midi_sha256_json

LIVE_ACTIVITY_SCHEMA_VERSION = 1
LIVE_ACTIVITY_KIND = "earcrate_live_activity_receipt"
LIVE_ACTIVITY_DOMAINS = (
    "offline_compile",
    "control",
    "phrase_render",
    "cpu_execution",
    "audio_callback",
    "unspecified",
)
LIVE_ACTIVITY_OPERATIONS = (
    "planning",
    "library_search",
    "sample_decode",
    "binding",
    "pattern_scan",
    "material_scan",
    "cpu_command",
)
_CALLBACK_FORBIDDEN_OPERATIONS = frozenset(
    {"planning", "library_search", "sample_decode", "binding", "pattern_scan", "material_scan"}
)


class LiveCallbackPurityError(RuntimeError):
    """Raised when a forbidden operation reaches the audio callback domain."""


@dataclass(frozen=True)
class _LiveActivityContext:
    recorder: "LiveActivityRecorder | None"
    domain: str


_context = threading.local()
_UNSPECIFIED_CONTEXT = _LiveActivityContext(None, "unspecified")


def _current_context() -> _LiveActivityContext:
    value = getattr(_context, "value", None)
    return value if isinstance(value, _LiveActivityContext) else _UNSPECIFIED_CONTEXT


def live_activity_context(
    recorder: "LiveActivityRecorder | None",
    domain: str,
) -> _LiveActivityContext:
    normalized = str(domain or "unspecified")
    if normalized not in LIVE_ACTIVITY_DOMAINS:
        raise ValueError(f"unknown live activity domain: {normalized}")
    return _LiveActivityContext(recorder, normalized)


def live_activity_swap(context: _LiveActivityContext) -> _LiveActivityContext:
    """Install a preallocated context and return the previous context."""
    previous = _current_context()
    _context.value = context
    return previous


def live_activity_push(
    recorder: "LiveActivityRecorder | None",
    domain: str,
) -> _LiveActivityContext:
    return live_activity_swap(live_activity_context(recorder, domain))


def live_activity_pop(previous: _LiveActivityContext) -> None:
    _context.value = previous


class live_activity_scope:
    """Thread-local activity scope for control and render threads."""

    def __init__(self, recorder: "LiveActivityRecorder | None", domain: str):
        self.recorder = recorder
        self.domain = domain
        self.previous: _LiveActivityContext | None = None

    def __enter__(self) -> "LiveActivityRecorder | None":
        self.previous = live_activity_push(self.recorder, self.domain)
        return self.recorder

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        assert self.previous is not None
        live_activity_pop(self.previous)


class LiveActivityRecorder:
    """Thread-safe measured counters for actual live-runtime operations."""

    def __init__(self, *, event_capacity: int = 256):
        if int(event_capacity) < 0:
            raise ValueError("live activity event capacity cannot be negative")
        self.event_capacity = int(event_capacity)
        self._lock = threading.Lock()
        self._counts = {
            domain: {operation: 0 for operation in LIVE_ACTIVITY_OPERATIONS}
            for domain in LIVE_ACTIVITY_DOMAINS
        }
        self._events: list[dict[str, Any] | None] = [None] * self.event_capacity
        self._event_write_count = 0
        self._callback_violation_count = 0

    def record(
        self,
        operation: str,
        *,
        units: int = 1,
        detail: Mapping[str, Any] | None = None,
        domain: str | None = None,
    ) -> None:
        normalized_operation = str(operation)
        if normalized_operation not in LIVE_ACTIVITY_OPERATIONS:
            raise ValueError(f"unknown live activity operation: {normalized_operation}")
        amount = int(units)
        if amount <= 0:
            raise ValueError("live activity units must be positive")
        context = _current_context()
        normalized_domain = str(domain or context.domain or "unspecified")
        if normalized_domain not in LIVE_ACTIVITY_DOMAINS:
            raise ValueError(f"unknown live activity domain: {normalized_domain}")
        if normalized_domain == "audio_callback" and normalized_operation in _CALLBACK_FORBIDDEN_OPERATIONS:
            with self._lock:
                self._counts[normalized_domain][normalized_operation] += amount
                self._callback_violation_count += amount
                self._record_event_locked(
                    normalized_domain,
                    normalized_operation,
                    amount,
                    detail,
                    forbidden=True,
                )
            raise LiveCallbackPurityError(
                f"forbidden {normalized_operation} operation reached the audio callback"
            )
        with self._lock:
            self._counts[normalized_domain][normalized_operation] += amount
            self._record_event_locked(
                normalized_domain,
                normalized_operation,
                amount,
                detail,
                forbidden=False,
            )

    def _record_event_locked(
        self,
        domain: str,
        operation: str,
        units: int,
        detail: Mapping[str, Any] | None,
        *,
        forbidden: bool,
    ) -> None:
        if self.event_capacity <= 0:
            return
        index = self._event_write_count % self.event_capacity
        self._events[index] = {
            "ordinal": self._event_write_count,
            "thread_id": threading.get_ident(),
            "domain": domain,
            "operation": operation,
            "units": units,
            "forbidden": bool(forbidden),
            "detail": deepcopy(dict(detail or {})),
        }
        self._event_write_count += 1

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            counts = deepcopy(self._counts)
            total_events = self._event_write_count
            retained_count = min(total_events, self.event_capacity)
            start = max(0, total_events - retained_count)
            retained = []
            for ordinal in range(start, total_events):
                event = self._events[ordinal % self.event_capacity]
                if event is not None and int(event["ordinal"]) == ordinal:
                    retained.append(deepcopy(event))
            callback_violations = self._callback_violation_count
        totals = {
            operation: sum(int(counts[domain][operation]) for domain in LIVE_ACTIVITY_DOMAINS)
            for operation in LIVE_ACTIVITY_OPERATIONS
        }
        receipt = {
            "schema_version": LIVE_ACTIVITY_SCHEMA_VERSION,
            "kind": LIVE_ACTIVITY_KIND,
            "domains": counts,
            "totals": totals,
            "event_capacity": self.event_capacity,
            "observed_event_count": total_events,
            "retained_event_count": len(retained),
            "callback_violation_count": callback_violations,
            "events": retained,
        }
        receipt["activity_sha256"] = midi_sha256_json(receipt)
        return receipt


def live_record_activity(
    operation: str,
    *,
    units: int = 1,
    detail: Mapping[str, Any] | None = None,
) -> None:
    context = _current_context()
    if context.recorder is None:
        return
    context.recorder.record(operation, units=units, detail=detail, domain=context.domain)


def live_activity_delta(before: Mapping[str, Any], after: Mapping[str, Any]) -> dict[str, Any]:
    domains = {}
    for domain in LIVE_ACTIVITY_DOMAINS:
        domains[domain] = {}
        for operation in LIVE_ACTIVITY_OPERATIONS:
            domains[domain][operation] = (
                int((after.get("domains") or {}).get(domain, {}).get(operation, 0))
                - int((before.get("domains") or {}).get(domain, {}).get(operation, 0))
            )
            if domains[domain][operation] < 0:
                raise ValueError("live activity counters moved backwards")
    totals = {
        operation: sum(domains[domain][operation] for domain in LIVE_ACTIVITY_DOMAINS)
        for operation in LIVE_ACTIVITY_OPERATIONS
    }
    delta = {
        "schema_version": LIVE_ACTIVITY_SCHEMA_VERSION,
        "kind": "earcrate_live_activity_delta",
        "domains": domains,
        "totals": totals,
        "observed_event_count": (
            int(after.get("observed_event_count") or 0)
            - int(before.get("observed_event_count") or 0)
        ),
        "callback_violation_count": (
            int(after.get("callback_violation_count") or 0)
            - int(before.get("callback_violation_count") or 0)
        ),
    }
    delta["activity_delta_sha256"] = midi_sha256_json(delta)
    return delta
