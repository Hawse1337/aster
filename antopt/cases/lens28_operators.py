"""Structure operators for the handed 28 GHz lens (module D, case plugin).

The first operator scales the radiating aperture. It is declarative: the
modelling step is described here and executed through the guarded broker, so the
same description works for a script-built model today and a variable-driven
model later.

Measured motivation (2026-11-10): nine measured points peak at ~6.63 dBi while
the handover asks for ~7 dBi; the step-ring parameters cannot close that gap, so
the search has to change the aperture itself.
"""
from __future__ import annotations

from typing import Any

from ..core import ParamSpec, SpecError
from ..topology.operators import StructureOperator

APERTURE_SCALE = ParamSpec(
    name="aperture_scale", unit="", low=0.92, high=1.12, default=1.0,
    integer_step=0.01,
    physics="scales the radiating aperture; larger aperture raises the peak "
            "directivity at the same frequency, at the cost of the input match",
)

APERTURE_SHIFT = ParamSpec(
    name="aperture_z", unit="mm", low=-2.0, high=2.0, default=0.0,
    integer_step=0.1,
    physics="moves the aperture along the axis; changes the distance to the feed "
            "cone and therefore the illumination taper",
)


def _legality(values: dict[str, Any]) -> tuple[bool, str]:
    scale = float(values["aperture_scale"])
    if not (0.92 <= scale <= 1.12):
        return False, f"aperture_scale {scale} outside [0.92, 1.12]"
    return True, ""


def _operations(params: dict[str, Any]) -> list[dict[str, Any]]:
    scale = float(params["aperture_scale"])
    shift = float(params.get("aperture_z", 0.0))
    return [
        {"op": "scale_objects", "args": {"targets": ["disc", "disc2"],
                                         "factor": scale}},
        {"op": "shift_objects", "args": {"targets": ["disc", "disc2"],
                                         "delta": [0.0, 0.0, shift]}},
        {"op": "validate_simple", "args": {"attempts": 6, "delay_s": 2.0}},
    ]


def aperture_operator() -> StructureOperator:
    return StructureOperator(
        operator_id="aperture_scale",
        name="scale and shift the radiating aperture",
        antenna_types=("metal_lens_periodic_disc",),
        changes="scale the periodic disc aperture by a factor and shift it along Z",
        mechanism="directivity rises with the electrical aperture size; the "
                  "illumination taper changes with the feed distance",
        risked_metrics=("s11", "ripple"),
        manufacturing_rules={"max_scale": 1.12, "min_scale": 0.92},
        rollback="drop the proposal and keep the previous family",
        extra_params=(APERTURE_SCALE, APERTURE_SHIFT),
        legality=_legality,
    )


def operator_operations(operator_id: str, values: dict[str, Any]) -> list[dict[str, Any]]:
    if operator_id != "aperture_scale":
        raise SpecError(f"unknown lens28 operator: {operator_id}")
    return _operations(values)
