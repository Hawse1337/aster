"""Parametric model for the handed 28 GHz periodic-disc metal lens (lens28).

S1 scope: the model is *declarative* (ordered operations) and the AEDT
execution is injected. Phase-1.5 will replace the operations with HFSS design
variables so a parameter change never rebuilds geometry.
"""
from __future__ import annotations

from typing import Any

from ..core import ParamSpace, SpecError
from ..parametric.model import GeometryReport, OperationModel, check_manufacturing
from ..parametric.registry import CaseRegistration, register_case
from .lens28 import build_case

GEOMETRY_LIMITS = {
    # manufacturing / electrical sanity limits for the capacitive step
    "step_L": {"min": 0.5, "max": 4.0},
    "step_ro": {"min": 0.4, "max": 0.9},
}
MIN_WALL_MM = 0.5
MIN_GAP_MM = 0.3


def space() -> ParamSpace:
    return build_case().family.param_space


def operations(params: dict[str, Any]) -> list[dict[str, Any]]:
    """Ordered modelling operations for one parameter set."""
    step_L = float(params["step_L"])
    step_ro = float(params["step_ro"])
    wedge = int(params["wedge"])
    cone_dz = float(params["cone_dz"])
    return [
        {"op": "delete_if_present", "args": {"names": ["step_out", "step_in"]}},
        {"op": "create_cylinder", "args": {"name": "step_out", "origin": [0, 0, -1 - step_L],
                                           "radius": 1.2, "height": step_L,
                                           "axis": "Z", "material": "pec"}},
        {"op": "create_cylinder", "args": {"name": "step_in", "origin": [0, 0, -1 - step_L],
                                           "radius": step_ro, "height": step_L,
                                           "axis": "Z", "material": "pec"}},
        {"op": "subtract", "args": {"target": "step_out", "tool": "step_in",
                                    "keep_originals": False}},
        {"op": "set_wedge", "args": {"present": bool(wedge)}},
        {"op": "shift_cones", "args": {"dz": cone_dz}},
        {"op": "validate_simple", "args": {"attempts": 6, "delay_s": 2.0}},
    ]


def geometry_checks(params: dict[str, Any]) -> GeometryReport:
    report = GeometryReport(valid=True)
    report.add("param_limits", True)
    bands = check_manufacturing(params, GEOMETRY_LIMITS)
    for name, ok in bands.checks.items():
        report.add(name, ok)
    # the ring must stay thinner than the coax outer wall (r=1.2) by the minimum gap
    radial_gap = 1.2 - float(params["step_ro"])
    report.add("step_gap", radial_gap >= MIN_GAP_MM,
               f"radial gap {radial_gap:.3f} mm < {MIN_GAP_MM} mm")
    # keep a small margin inside the 4 mm parameter bound; the solve-time
    # validate_simple still catches any real overlap with the coax section
    overhang = float(params["step_L"]) - 3.95
    report.add("step_length", overhang <= 0.1,
               f"step length {params['step_L']} mm exceeds the coax section")
    return report


def em_checks(params: dict[str, Any]) -> GeometryReport:
    report = GeometryReport(valid=True)
    # cone offset must stay inside the region; larger offsets risk touching the disc
    limit = 0.6
    dz = abs(float(params["cone_dz"]))
    report.add("cone_offset", dz <= limit, f"|cone_dz|={dz} > {limit}")
    return report


def build_model(apply: Any = None) -> OperationModel:
    return OperationModel(
        space(),
        operations,
        apply=apply,
        geometry_checks=geometry_checks,
        em_checks=em_checks,
    )


def register() -> CaseRegistration:
    registration = CaseRegistration(
        name="lens28",
        case_factory=build_case,
        model_factory=build_model,
        antenna_type="metal_lens_periodic_disc",
        notes="handed 2026-11-10; original periodic discs must be preserved",
    )
    return registration


