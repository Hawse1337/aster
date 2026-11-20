"""Structural consistency checks for modelled changes (S1/S5 safety).

A geometry can be *valid* (no overlap) and still be electrically meaningless:
scaling a structure can move its metal outside the radiation boundary or away
from the port plane, and the solver will happily return numbers. These checks
compare bounding boxes before a solve so such a state is rejected early.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ConsistencyReport:
    ok: bool = True
    checks: dict[str, bool] = field(default_factory=dict)
    messages: list[str] = field(default_factory=list)

    def add(self, name: str, passed: bool, message: str = "") -> "ConsistencyReport":
        self.checks[name] = bool(passed)
        if not passed:
            self.ok = False
            if message:
                self.messages.append(f"{name}: {message}")
        return self


def _box(value: Any) -> tuple[list[float], list[float]]:
    if isinstance(value, dict):
        minimum = value.get("min")
        maximum = value.get("max")
    else:
        minimum, maximum = value[0], value[1]
    return [float(v) for v in minimum], [float(v) for v in maximum]


def check_inside(inner: dict[str, Any], outer: dict[str, Any], *,
                 name: str = "inside", margin_mm: float = 0.0) -> bool:
    low_i, high_i = _box(inner)
    low_o, high_o = _box(outer)
    return all(
        low_i[axis] >= low_o[axis] + margin_mm and high_i[axis] <= high_o[axis] - margin_mm
        for axis in range(3)
    )


def check_structure_consistency(
    objects: dict[str, dict[str, Any]],
    *,
    structure: tuple[str, ...] = ("disc", "disc2"),
    region: str = "rad_region",
    port: str = "port_disc",
    port_tolerance_mm: float = 0.05,
    baseline_port: dict[str, Any] | None = None,
    margin_mm: float = 0.0,
) -> ConsistencyReport:
    """Check that the structure stays inside the region and the port did not move."""
    report = ConsistencyReport()
    missing = [name for name in (*structure, region, port) if name not in objects]
    if missing:
        report.add("objects_present", False, f"missing objects: {missing}")
        return report
    report.add("objects_present", True)

    region_box = objects[region]
    for name in structure:
        report.add(
            f"{name}_inside_region",
            check_inside(objects[name], region_box, margin_mm=margin_mm),
            f"{name} extends outside {region}",
        )

    if baseline_port is not None:
        port_low, port_high = _box(objects[port])
        base_low, base_high = _box(baseline_port)
        moved = max(abs(a - b) for a, b in zip(port_low + port_high, base_low + base_high))
        report.add("port_unchanged", moved <= port_tolerance_mm,
                   f"port moved by {moved:.3f} mm")
    return report


def boxes_from_snippet_result(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Normalise the {name: {size,min,max}} payload produced by the AEDT probe."""
    objects: dict[str, dict[str, Any]] = {}
    for name, info in (payload or {}).items():
        if not isinstance(info, dict) or "min" not in info or "max" not in info:
            continue
        objects[str(name)] = {"min": [float(v) for v in info["min"]],
                              "max": [float(v) for v in info["max"]]}
    return objects
