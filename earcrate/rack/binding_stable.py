from __future__ import annotations

from typing import Any, Mapping, Sequence

_binding_module = None
import earcrate.rack.binding as _binding_module

_original_rack_compile_binding = (
    _binding_module.rack_compile_binding
    if _binding_module is not None
    else rack_compile_binding
)


def rack_compile_binding(
    ledger: Mapping[str, Any],
    racks: Sequence[Mapping[str, Any]],
    *,
    assignments: Mapping[str, str] | None = None,
    pitch_bend_range_semitones: float = 2.0,
) -> dict[str, Any]:
    """Canonicalize rack order before compatibility receipts and selection."""
    ordered = sorted(
        [dict(rack) for rack in racks],
        key=lambda rack: (
            str(rack.get("rack_sha256") or ""),
            str(rack.get("rack_id") or ""),
        ),
    )
    return _original_rack_compile_binding(
        ledger,
        ordered,
        assignments=assignments,
        pitch_bend_range_semitones=pitch_bend_range_semitones,
    )


if _binding_module is not None:
    _binding_module.rack_compile_binding = rack_compile_binding
    rack_load_binding = _binding_module.rack_load_binding
    rack_load_many = _binding_module.rack_load_many
    rack_validate_binding = _binding_module.rack_validate_binding
