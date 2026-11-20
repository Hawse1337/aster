# -*- coding: utf-8 -*-
"""10 GHz dielectric lens omnidirectional antenna case (Lens2G4 scaled).

This is the case registration for the dielectric-lens project.  The AEDT
project is ``LensOmni_10GHz_trunc.aedt`` (truncated biconical center + coax).
The parametric model is intentionally declared as an operation list for now;
the real executor will apply the operations through the guard/PyAEDT.
"""
from __future__ import annotations

from ..core import (CaseSpec, Constraint, FamilySpec, Objective, ParamSpace,
                    ParamSpec)
from ..parametric.model import OperationModel

PROJECT_PATH = ""  # configure a reference project before running this case
DESIGN_NAME = "Trunc_10GHz"

H_RIM = ParamSpec(
    name="H_RIM", unit="mm", low=0.15, high=1.56, default=0.3,
    integer_step=0.05,
    physics="half-thickness of the dielectric lens rim; thinner edge reduces "
            "phase delay at the aperture edge and changes the elevation sidelobes",
)
R_KNEE = ParamSpec(
    name="R_KNEE", unit="mm", low=18.0, high=24.0, default=20.4,
    integer_step=0.4,
    physics="radius where the inner smooth cone ends and the stepped outer "
            "section begins; controls the inner aperture phase",
)
H_KNEE = ParamSpec(
    name="H_KNEE", unit="mm", low=5.0, high=7.0, default=6.0,
    integer_step=0.25,
    physics="half-thickness at the knee radius; sets the slope of the inner "
            "taper and the feed-to-lens phase",
)


def build_space() -> ParamSpace:
    return ParamSpace([H_RIM, R_KNEE, H_KNEE])


def build_case() -> CaseSpec:
    space = build_space()
    family = FamilySpec(
        family_id="lens2g4_10g_v1",
        antenna_type="dielectric_lens_omni",
        param_space=space,
        builder="antopt.cases.lens2g4_10g:build_model",
        notes="10 GHz scaled Lens2G4 dielectric disc; truncated biconical center; "
              "5-18 GHz target; thin-rim variant",
    )
    return CaseSpec(
        case_id="lens2g4_10g",
        project="lens2g4_10g",
        design_type="dielectric_lens_omni",
        frequency_ghz=(5.0, 18.0),
        family=family,
        constraints=[
            Constraint(id="s11", kind="s11", operator="<=", target=-10.0,
                       unit="dB", band_ghz=(5.0, 18.0), violation_scale=3.0),
            Constraint(id="peak_gain", kind="peak_gain", operator=">=",
                       target=5.0, unit="dBi", violation_scale=2.0),
            Constraint(id="ripple", kind="ripple", operator="<=", target=3.0,
                       unit="dB", violation_scale=1.0),
        ],
        objectives=[
            Objective(id="gain_obj", kind="peak_gain", direction="maximize"),
            Objective(id="s11_obj", kind="s11", direction="minimize"),
        ],
        evaluation={
            "frequencies_ghz": [5.0, 8.0, 10.0, 12.0, 15.0, 18.0],
            "max_frequency_points": 6,
            "theta_cut_phi_deg": [0, 90],
            "horiz_theta_deg": 90,
            "main_lobe_exclusion_deg": 10,
            "realized_gain_mode": "gain_plus_mismatch",
        },
        execution_policy={
            "max_run_minutes": 90,
            "require_ff_sphere": True,
            "require_convergence": True,
            "num_cores": 16,
            "num_tasks": 1,
            "max_total_cores": 16,
            "enforce_cpu_budget": False,
        },
        project_path=PROJECT_PATH,
        setup_name="Setup1",
        notes=(
            "Target: 5-18 GHz, |S11| <= -10 dB; peak gain >= 5 dBi; azimuth "
            "ripple <= 3 dB; axial (theta near 0/180) suppressed. Feed is SMA "
            "(N-type is limited to about 11 GHz and was dropped). Every run must "
            "produce a Discrete 5-18 GHz S11 curve and a 3D realised-gain pattern."
        ),
    )


def _operations(params: dict) -> list[dict]:
    return [
        {"op": "set_param", "name": "H_RIM", "value": float(params["H_RIM"])},
        {"op": "set_param", "name": "R_KNEE", "value": float(params["R_KNEE"])},
        {"op": "set_param", "name": "H_KNEE", "value": float(params["H_KNEE"])},
        {"op": "rebuild_lens_profile"},
        {"op": "rebuild_biconical_and_coax"},
        {"op": "validate_simple"},
    ]


def build_model() -> OperationModel:
    return OperationModel(build_space(), _operations)


def register():
    from ..parametric.registry import CaseRegistration

    return CaseRegistration(
        name="lens2g4_10g",
        case_factory=build_case,
        model_factory=build_model,
        antenna_type="dielectric_lens_omni",
        notes="10 GHz dielectric lens / truncated biconical, 5-18 GHz",
    )
