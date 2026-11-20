"""Case configuration for the user-corrected bowl structure (lens28_userfix).

Differences from ``lens28`` (the handed model):

* the redundant lower disc (``disc2``) and the thin plate (``wedge``) are gone;
* both bowls are identical (one is the mirror of the other) with 18 holes;
* the main disc sits between the bowls with an adjustable clearance, which is
  now a real optimisation parameter (``bowl_gap``).

The parameter space therefore has four axes: ``step_L``, ``step_ro``,
``bowl_gap`` and ``cone_dz``.
"""
from __future__ import annotations

from ..core import CaseSpec, Constraint, FamilySpec, Objective, ParamSpace, ParamSpec
from ..parametric.model import GeometryReport, OperationModel
from ..parametric.registry import CaseRegistration
from .lens28 import STEP_LENGTH, STEP_RADIUS, BASE_PROJECT, HANDED_PROJECT

DESIGN_NAME = "B0_f24_thin"

BOWL_GAP = ParamSpec(
    name="tip_offset", unit="mm", low=0.5, high=2.5, default=1.0, integer_step=0.1,
    physics="axial distance from the main disc mid-plane to each bowl tip plane "
            "(reference design f24g05 uses 1.0 mm); the disc straddles the coax "
            "mouth and both bowls stay symmetric around it",
    manufacturing={"min_gap_mm": 0.5},
)
CONE_DZ = ParamSpec(
    name="cone_dz", unit="mm", low=-0.6, high=0.6, default=0.0, integer_step=0.05,
    physics="common axial offset of both bowls; moves the pair together instead "
            "of changing their separation",
)

GEOMETRY_LIMITS = {"step_L": {"min": 0.5, "max": 4.0},
                   "step_ro": {"min": 0.4, "max": 0.9},
                   "tip_offset": {"min": 0.5, "max": 2.5}}


def build_case() -> CaseSpec:
    space = ParamSpace([STEP_LENGTH, STEP_RADIUS, BOWL_GAP, CONE_DZ])
    family = FamilySpec(
        family_id="lens28_userfix_v1",
        antenna_type="metal_lens_periodic_disc",
        param_space=space,
        builder="aster.cases.lens28_userfix:build_model",
        notes="user-corrected structure: disc2 and wedge removed, bowls identical "
              "with 18 holes, disc centred with an adjustable gap",
    )
    return CaseSpec(
        case_id="lens28_userfix",
        project="lens28_userfix",
        design_type="metal_lens_periodic_disc",
        frequency_ghz=(24.0, 30.0),
        family=family,
        constraints=[
            Constraint(id="s11", kind="s11", operator="<=", target=-10.0, unit="dB",
                       band_ghz=(24.0, 30.0), violation_scale=3.0),
            Constraint(id="peak_gain", kind="peak_gain", operator=">=", target=6.0,
                       unit="dBi", violation_scale=2.0),
            Constraint(id="ripple", kind="ripple", operator="<=", target=3.0, unit="dB",
                       violation_scale=1.0),
            # measured on the user-built prototype: gain is fine, bandwidth is
            # the bottleneck (~1 GHz), so bandwidth becomes a hard requirement
            Constraint(id="bandwidth", kind="s11_bandwidth", operator=">=", target=3.0,
                       unit="GHz", violation_scale=2.0),
        ],
        objectives=[
            Objective(id="gain_obj", kind="peak_gain", direction="maximize"),
            Objective(id="bw_obj", kind="s11_bandwidth", direction="maximize"),
        ],
        evaluation={"frequencies_ghz": [28.0], "max_frequency_points": 7,
                    "theta_cut_phi_deg": [0, 90], "horiz_theta_deg": 90,
                    "main_lobe_exclusion_deg": 10,
                    "realized_gain_mode": "gain_plus_mismatch"},
        execution_policy={"max_run_minutes": 30, "require_ff_sphere": True,
                          "require_convergence": True, "num_cores": 16,
                          "num_tasks": 1, "max_total_cores": 16,
                          "enforce_cpu_budget": False},
        project_path=HANDED_PROJECT,
        setup_name="FF_Swp",
        notes="same electrical goal as lens28: s11 <= -10 dB over the band, peak "
              "realised gain ~6 dBi or more, horizontal ripple <= 3 dB",
    )


def operations(params: dict) -> list[dict]:
    step_L = float(params["step_L"])
    step_ro = float(params["step_ro"])
    tip_offset = float(params["tip_offset"])
    cone_dz = float(params["cone_dz"])
    return [
        {"op": "delete_if_present", "args": {"names": ["step_out", "step_in"]}},
        {"op": "create_cylinder", "args": {"name": "step_out", "origin": [0, 0, -1 - step_L],
                                           "radius": 1.2, "height": step_L, "axis": "Z",
                                           "material": "pec"}},
        {"op": "create_cylinder", "args": {"name": "step_in", "origin": [0, 0, -1 - step_L],
                                           "radius": step_ro, "height": step_L, "axis": "Z",
                                           "material": "pec"}},
        {"op": "subtract", "args": {"target": "step_out", "tool": "step_in",
                                    "keep_originals": False}},
        {"op": "gap_bowls", "args": {"tip_offset_mm": tip_offset, "disc_offset_mm": 1.0}},
        {"op": "shift_objects", "args": {"targets": ["cone_up", "cone_dn"],
                                         "delta": [0.0, 0.0, cone_dz]}},
        {"op": "validate_simple", "args": {"attempts": 6, "delay_s": 2.0}},
    ]


def geometry_checks(params: dict) -> GeometryReport:
    from .lens28_builder import check_manufacturing

    report = GeometryReport(valid=True)
    band = check_manufacturing(params, GEOMETRY_LIMITS)
    for name, ok in band.checks.items():
        report.add(name, ok)
    radial_gap = 1.2 - float(params["step_ro"])
    report.add("step_gap", radial_gap >= 0.3, f"radial gap {radial_gap:.3f} mm < 0.3 mm")
    report.add("step_length", float(params["step_L"]) <= 3.95,
               "step length exceeds the coax section")
    return report


def em_checks(params: dict) -> GeometryReport:
    report = GeometryReport(valid=True)
    report.add("tip_offset_positive", float(params["tip_offset"]) > 0.0, "bowl tip offset must be > 0")
    return report


def build_model(apply=None) -> OperationModel:
    return OperationModel(ParamSpace([STEP_LENGTH, STEP_RADIUS, BOWL_GAP, CONE_DZ]),
                          operations, apply=apply,
                          geometry_checks=geometry_checks, em_checks=em_checks)


def register() -> CaseRegistration:
    return CaseRegistration(
        name="lens28_userfix",
        case_factory=build_case,
        model_factory=build_model,
        antenna_type="metal_lens_periodic_disc",
        notes="user-corrected geometry; bowl_gap is an optimisation parameter",
    )




