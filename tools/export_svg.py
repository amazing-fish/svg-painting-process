"""Export one vector-only SVG frame from a generated painting-data.json.

This exports a static frame, not an animated SVG. The HTML player remains the
animation player. Usage:
    python tools/export_svg.py --data run/qa/painting-data.json --output frame.svg
    python tools/export_svg.py --data run/qa/painting-data.json --output half.svg --time-ms 1500
"""
from __future__ import annotations

import argparse
import json
import math
import re
from pathlib import Path
import xml.etree.ElementTree as ET

SVG_NS = "http://www.w3.org/2000/svg"
ET.register_namespace("", SVG_NS)
_HEX_COLOR = re.compile(r"#[0-9a-fA-F]{3}(?:[0-9a-fA-F]{3})?\Z")


def _finite(value: object, label: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{label} must be a finite number")
    try:
        result = float(value)
    except (ValueError, TypeError) as exc:
        raise ValueError(f"{label} must be a finite number") from exc
    if not math.isfinite(result):
        raise ValueError(f"{label} must be a finite number")
    return result


def _number(value: float) -> str:
    return format(value, ".17g")


def _points(value: object, label: str) -> list[tuple[float, float]]:
    if not isinstance(value, list) or len(value) < 2:
        raise ValueError(f"{label} needs at least two coordinate pairs")
    points = []
    for pair in value:
        if not isinstance(pair, (list, tuple)) or len(pair) != 2:
            raise ValueError(f"{label} must contain coordinate pairs")
        points.append((_finite(pair[0], label), _finite(pair[1], label)))
    return points


def partial_ribbon_d(stroke: dict, progress: float) -> str:
    """Use the player's arc-length prefix geometry without revealing its tail."""
    p = min(1.0, max(0.0, _finite(progress, "progress")))
    if p <= 0:
        return ""
    if p >= 1:
        return stroke["d"]
    points = _points(stroke.get("points"), "points")
    left = _points(stroke.get("left"), "left")
    right = _points(stroke.get("right"), "right")
    if len(left) != len(points) or len(right) != len(points):
        raise ValueError("points, left and right must have the same length")

    supplied = stroke.get("lengths")
    lengths = None
    if isinstance(supplied, list) and len(supplied) == len(points):
        try:
            values = [_finite(value, "lengths") for value in supplied]
            if all(b >= a for a, b in zip(values, values[1:])) and values[-1] > values[0]:
                lengths = [value - values[0] for value in values]
        except ValueError:
            pass
    if lengths is None:
        lengths = [0.0]
        for a, b in zip(points, points[1:]):
            lengths.append(lengths[-1] + math.hypot(b[0] - a[0], b[1] - a[1]))
    # A stationary dab advances the boundary samples in their recorded order.
    if lengths[-1] == 0:
        lengths = list(range(len(points)))

    distance = p * lengths[-1]
    cut = 1
    while cut < len(lengths) - 1 and lengths[cut] < distance:
        cut += 1
    fraction = (distance - lengths[cut - 1]) / (lengths[cut] - lengths[cut - 1] or 1)

    def interpolate(edge: list[tuple[float, float]]) -> tuple[float, float]:
        a, b = edge[cut - 1], edge[cut]
        return (a[0] + (b[0] - a[0]) * fraction, a[1] + (b[1] - a[1]) * fraction)

    prefix_left = left[:cut] + [interpolate(left)]
    prefix_right = right[:cut] + [interpolate(right)]
    vertices = prefix_left + list(reversed(prefix_right))
    return "M" + " L".join(f"{_number(x)},{_number(y)}" for x, y in vertices) + " Z"


def render_svg(data: dict, time_ms: float | None = None) -> str:
    """Render the completed stroke prefix and, at most, one growing stroke."""
    if not isinstance(data, dict):
        raise ValueError("painting data must be an object")
    width = _finite(data.get("width"), "width")
    height = _finite(data.get("height"), "height")
    if width <= 0 or height <= 0:
        raise ValueError("width and height must be positive")
    strokes = data.get("strokes")
    if not isinstance(strokes, list):
        raise ValueError("strokes must be a list")
    timeline = []
    previous_end = 0.0
    for index, stroke in enumerate(strokes):
        if not isinstance(stroke, dict):
            raise ValueError(f"stroke {index} must be an object")
        start = _finite(stroke.get("start"), f"stroke {index} start")
        duration = _finite(stroke.get("duration"), f"stroke {index} duration")
        if start < previous_end or duration <= 0:
            raise ValueError("strokes must have positive durations and a serial, non-overlapping timeline")
        end = start + duration
        if not math.isfinite(end):
            raise ValueError("stroke end must be finite")
        timeline.append((start, end, duration))
        previous_end = end
    total = _finite(data.get("totalDuration", previous_end), "totalDuration")
    if total < previous_end:
        raise ValueError("totalDuration cannot precede the last stroke end")
    current = total if time_ms is None else min(total, max(0.0, _finite(time_ms, "time_ms")))

    root = ET.Element(f"{{{SVG_NS}}}svg", {
        "width": _number(width), "height": _number(height),
        "viewBox": f"0 0 {_number(width)} {_number(height)}",
        "data-time-ms": _number(current),
        "role": "img",
    })
    ET.SubElement(root, f"{{{SVG_NS}}}title").text = "Painting process — static SVG frame"
    ET.SubElement(root, f"{{{SVG_NS}}}rect", {
        "width": _number(width), "height": _number(height), "fill": "#ffffff",
    })
    group = ET.SubElement(root, f"{{{SVG_NS}}}g", {"stroke": "none"})
    for index, (stroke, (start, end, duration)) in enumerate(zip(strokes, timeline)):
        if start >= current and end > current:
            break
        complete = end <= current
        d = stroke.get("d") if complete else partial_ribbon_d(stroke, (current - start) / duration)
        if not isinstance(d, str) or not d:
            raise ValueError(f"stroke {index} needs a non-empty SVG path")
        color = stroke.get("color")
        if not isinstance(color, str) or not _HEX_COLOR.fullmatch(color):
            raise ValueError(f"stroke {index} color must be #RGB or #RRGGBB")
        opacity = _finite(stroke.get("opacity", 1), f"stroke {index} opacity")
        if not 0 <= opacity <= 1:
            raise ValueError(f"stroke {index} opacity must be between 0 and 1")
        ET.SubElement(group, f"{{{SVG_NS}}}path", {
            "d": d, "fill": color, "opacity": _number(opacity),
            "data-index": str(index), "data-state": "completed" if complete else "drawing",
        })
        if not complete:
            break
    return ET.tostring(root, encoding="utf-8", xml_declaration=True).decode("utf-8") + "\n"


def export_svg(data: dict, output_path: str | Path, time_ms: float | None = None) -> Path:
    """Write a standalone static frame and return its output path."""
    svg = render_svg(data, time_ms)
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(svg, encoding="utf-8")
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", required=True, type=Path, help="Generated painting-data.json")
    parser.add_argument("--output", required=True, type=Path, help="Static vector SVG output")
    parser.add_argument("--time-ms", type=float, help="Original stroke time in milliseconds; default is the final frame")
    args = parser.parse_args()
    try:
        data = json.loads(args.data.read_text(encoding="utf-8"))
        export_svg(data, args.output, args.time_ms)
    except (ValueError, OSError) as exc:
        parser.error(str(exc))
    print(str(args.output.resolve()))


if __name__ == "__main__":
    main()
