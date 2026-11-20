"""Case configuration: 28 GHz all-metal periodic-disc metal lens (lens28).

This is the first real benchmark case handed over on 2026-11-10. The project
under ``PROJECT_PATH`` is a *copy* used for evaluation; the original design
(periodic corrugated disc + second disc + cones + flange) must be preserved —
no topology replacement is allowed for this case.

Status of the model (see the handover): the geometry is still built
imperatively by scripts, so this case declares the parameters that act as the
optimisation axes but the full variable-driven parametric model is Phase 1 work.
Until that exists, ``build_model`` documents the exact operations a builder
must perform; the executor applies them through ``run_python_code``.
"""
from __future__ import annotations

from ..core import CaseSpec, Constraint, FamilySpec, Objective, ParamSpace, ParamSpec

BASE_PROJECT = ""  # configure a reference project before running this case
HANDED_PROJECT = ""  # configure a reference project before running this case
DESIGN_NAME = "B0_f24_thin"

# Independent variables currently exposed by the legacy evaluator. Phase 1 will
# move these into HFSS design variables so the geometry updates in place.
STEP_LENGTH = ParamSpec(
    name="step_L", unit="mm", low=0.5, high=4.0, default=2.0, integer_step=0.1,
    physics="capacitive step height inside the coax; raises series capacitance to "
            "pull the high-frequency roll-off of the input match back into band",
    manufacturing={"min_gap_mm": 0.5},
)
STEP_RADIUS = ParamSpec(
    name="step_ro", unit="mm", low=0.4, high=0.9, default=0.8, integer_step=0.05,
    physics="outer radius of the capacitive ring; sets the step impedance ratio",
    manufacturing={"min_gap_mm": 0.3},
)
WEDGE = ParamSpec(
    name="wedge", unit="", low=0, high=1, default=1, kind="choice", choices=(0, 1),
    physics="thin PEC wedge under the disc (0=removed, 1=present); affects the "
            "aperture illumination and the horizontal-plane ripple",
)
CONE_DZ = ParamSpec(
    name="cone_dz", unit="mm", low=-0.6, high=0.6, default=0.0, integer_step=0.05,
    physics="vertical offset of the feed cone; trades input match against the "
            "leakage distribution on the periodic disc",
)


def build_case() -> CaseSpec:
    space = ParamSpace([STEP_LENGTH, STEP_RADIUS, WEDGE, CONE_DZ])
    family = FamilySpec(
        family_id="lens28_disc_v1",
        antenna_type="metal_lens_periodic_disc",
        param_space=space,
        builder="aster.cases.lens28:build_model",
        notes="original two discs + periodic corrugation must be preserved",
    )
    return CaseSpec(
        case_id="lens28",
        project="lens28",
        design_type="metal_lens_periodic_disc",
        frequency_ghz=(24.0, 30.0),
        family=family,
        constraints=[
            Constraint(id="s11", kind="s11", operator="<=", target=-10.0,
                       unit="dB", band_ghz=(24.0, 30.0), violation_scale=3.0),
            # The handover asks for ~7 dBi PEAK realised gain; theta=90 is the
            # omnidirectional level (measured ~0 dBi on every candidate) and is
            # covered by the ripple gate instead of an absolute level gate.
            Constraint(id="peak_gain", kind="peak_gain", operator=">=",
                       target=6.0, unit="dBi", violation_scale=2.0),
            Constraint(id="ripple", kind="ripple", operator="<=", target=3.0,
                       unit="dB", violation_scale=1.0),
        ],
        objectives=[
            Objective(id="gain_obj", kind="peak_gain", direction="maximize"),
            Objective(id="s11_obj", kind="s11", direction="minimize"),
        ],
        evaluation={
            # the handed model only has a 28 GHz far-field setup; multi-frequency
            # far fields are a Phase-1 modelling upgrade (see notes below)
            "frequencies_ghz": [28.0],
            "max_frequency_points": 7,
            "theta_cut_phi_deg": [0, 90],
            "horiz_theta_deg": 90,
            "main_lobe_exclusion_deg": 10,
            "realized_gain_mode": "gain_plus_mismatch",
        },
        execution_policy={
            "max_run_minutes": 35,
            "require_ff_sphere": True,
            "require_convergence": True,
            "num_cores": 16,
            "num_tasks": 1,
            "max_total_cores": 16,
            "enforce_cpu_budget": False,
        },
        project_path=HANDED_PROJECT,
        setup_name="B0_Swp",
        notes=(
            "Original design goals: 24-30 GHz continuous S11 <= -10 dB at 50 ohm, "
            "theta=90 horizontal omnidirectional with ripple <= 3 dB, peak realised "
            "gain ~7 dBi. The effective-bandwidth constraint is enabled once the "
            "model exports far fields at several frequencies; until then the s11 "
            "band plus the 28 GHz pattern gates are the acceptance criteria. "
            "Baseline B0_F13: 24.2-28.65 GHz band, 6.59 dB @theta=106, theta=90 ~0 dB, "
            "ripple 1.51 dB."
        ),
    )


def build_model(candidate_params: dict) -> list[str]:
    """Return the ordered modelling operations for one candidate.

    The list is intentionally declarative so the same description can drive a
    script-based builder now and a variable-only builder after Phase 1:
    ``step_L``/``step_ro`` rebuild the capacitive ring, ``wedge`` toggles the
    wedge object, ``cone_dz`` shifts the feed cones.
    """
    step_L = float(candidate_params["step_L"])
    step_ro = float(candidate_params["step_ro"])
    wedge = int(candidate_params["wedge"])
    cone_dz = float(candidate_params["cone_dz"])
    return [
        "delete step_out, step_in if present",
        f"create step_out cylinder r=1.2 h={step_L:.3f} at z=-1-{step_L:.3f}",
        f"create step_in cylinder r={step_ro:.3f} h={step_L:.3f} at the same z",
        "subtract step_in from step_out (keep_originals=False)",
        f"set wedge present={bool(wedge)}",
        f"move cone_up/cone_dn by dz={cone_dz:.3f}",
        "validate_simple (retry up to 6x, 2 s apart)",
    ]


