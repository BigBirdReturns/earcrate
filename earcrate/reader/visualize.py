from __future__ import annotations

"""Dependency-free SVG event-atlas visualization from the SongGenome."""

import hashlib
from pathlib import Path
from typing import Any, Mapping
from xml.sax.saxutils import escape


def _reader_event_color(event_id: str, *, foreground: bool = False) -> str:
    if foreground:
        return "#cf4d4d"
    digest = hashlib.sha256(event_id.encode("utf-8")).digest()
    red = 70 + digest[0] % 150
    green = 70 + digest[1] % 150
    blue = 70 + digest[2] % 150
    return f"#{red:02x}{green:02x}{blue:02x}"


def reader_plot_event_atlas(genome: Mapping[str, Any], output_path: str | Path, *, overwrite: bool = False) -> dict[str, Any]:
    destination = Path(output_path).expanduser().resolve()
    if destination.exists() and not overwrite:
        raise FileExistsError(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    layers = ["foreground", "spark", "texture", "harmonic", "floor", "low_end"]
    layer_index = {name: index for index, name in enumerate(layers)}
    events = {str(row["canonical_event_id"]): row for row in genome.get("canonical_events") or []}
    duration = float((genome.get("body") or {}).get("duration_seconds") or 1.0)
    width = 1600
    margin_left = 120
    margin_right = 28
    margin_top = 60
    lane_height = 76
    plot_width = width - margin_left - margin_right
    height = margin_top + lane_height * len(layers) + 48

    def x_at(seconds: float) -> float:
        return margin_left + max(0.0, min(duration, seconds)) / max(duration, 1e-9) * plot_width

    pieces = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="#f7f7f5"/>',
        '<text x="800" y="30" text-anchor="middle" font-family="sans-serif" font-size="22">EarCrate cephalopod reader — canonical event instances</text>',
    ]
    for index, layer in enumerate(layers):
        y = margin_top + index * lane_height
        pieces.append(f'<rect x="{margin_left}" y="{y}" width="{plot_width}" height="{lane_height - 12}" fill="#ffffff" stroke="#dadada"/>')
        pieces.append(
            f'<text x="{margin_left - 12}" y="{y + 35}" text-anchor="end" font-family="sans-serif" font-size="16">{escape(layer)}</text>'
        )
    for beat in (genome.get("time_map") or {}).get("beats") or []:
        x = x_at(float(beat))
        pieces.append(f'<line x1="{x:.3f}" y1="{margin_top}" x2="{x:.3f}" y2="{height - 48}" stroke="#89b4d8" stroke-width="0.4" opacity="0.35"/>')
    for phrase_index, start in enumerate((genome.get("time_map") or {}).get("phrase_starts") or []):
        x = x_at(float(start))
        pieces.append(f'<line x1="{x:.3f}" y1="{margin_top - 12}" x2="{x:.3f}" y2="{height - 48}" stroke="#397fb5" stroke-width="1.5" opacity="0.7"/>')
        pieces.append(f'<text x="{x + 4:.3f}" y="{margin_top - 20}" font-family="sans-serif" font-size="12">P{phrase_index}</text>')
    for instance in genome.get("instances") or []:
        layer = str(instance.get("layer") or "")
        if layer not in layer_index:
            continue
        event = events[str(instance["canonical_event_id"])]
        start = float(instance["start_seconds"])
        end = float(instance["end_seconds"])
        x = x_at(start)
        event_width = max(1.0, x_at(end) - x)
        y = margin_top + layer_index[layer] * lane_height + 7
        recurrent = int(event.get("instance_count") or 1) > 1
        opacity = 0.92 if recurrent else 0.58
        color = _reader_event_color(str(event["canonical_event_id"]), foreground=layer == "foreground")
        pieces.append(
            f'<rect x="{x:.3f}" y="{y}" width="{event_width:.3f}" height="{lane_height - 26}" fill="{color}" fill-opacity="{opacity}" stroke="#202020" stroke-width="0.45"/>'
        )
    for second in range(0, int(duration) + 1, 5):
        x = x_at(float(second))
        pieces.append(f'<text x="{x:.3f}" y="{height - 18}" text-anchor="middle" font-family="sans-serif" font-size="12">{second}s</text>')
    pieces.append("</svg>")
    text = "\n".join(pieces) + "\n"
    destination.write_text(text, encoding="utf-8")
    return {"path": str(destination), "raw_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest()}


__all__ = ["reader_plot_event_atlas"]
