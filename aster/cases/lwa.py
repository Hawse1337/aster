"""Second antenna plugin: periodic leaky-wave antenna (LWA) — generality check.

This case exists to prove the core is antenna-agnostic: it only uses the public
contracts (CaseSpec / ParametricModel / knowledge helpers). Its geometry builder
is declarative like the lens case, so a real project can be wired later without
touching the core.

Knowledge implemented here (validated by unit tests, not by HFSS yet):

* spatial-harmonic phase constant:  beta_n = beta_0 + 2*pi*n/p
* main-beam direction:              theta = asin(beta_n / k0)
* open-stopband heuristic:          gain dip near broadside together with a
                                    reflection peak around the same frequency
"""
from __future__ import annotations

import math
from typing import Any

from ..core import CaseSpec, Constraint, FamilySpec, Objective, ParamSpace, ParamSpec
from ..parametric.model import GeometryReport, OperationModel, check_manufacturing
from ..parametric.registry import CaseRegistration

WAVE_WIDTH = ParamSpec(name="a_mm", unit="mm", low=18.0, high=26.0, default=22.86,
                       integer_step=0.1,
                       physics="broad-wall width; sets the dominant-mode cut-off")
WAVE_HEIGHT = ParamSpec(name="b_mm", unit="mm", low=6.0, high=12.0, default=10.16,
                        integer_step=0.1,
                        physics="narrow-wall height; affects the wave impedance")
PERIOD = ParamSpec(name="p_mm", unit="mm", low=6.0, high=16.0, default=10.0,
                   integer_step=0.1,
                   physics="period of the radiating perturbation; shifts beta_n")
UNIT_HEIGHT = ParamSpec(name="h_mm", unit="mm", low=0.5, high=4.0, default=2.0,
                        integer_step=0.05,
                        physics="perturbation height; controls the leakage rate alpha")
GAP = ParamSpec(name="g_mm", unit="mm", low=0.2, high=2.0, default=0.5,
                integer_step=0.05,
                physics="slot/gap width; couples to the guided mode")
PERIODS = ParamSpec(name="n_units", unit="", low=8, high=40, default=20, kind="int",
                    physics="number of periods; effective aperture and residual power")
GRADIENT_A1 = ParamSpec(name="a1", unit="mm", low=-1.5, high=1.5, default=0.0,
                        integer_step=0.05,
                        physics="linear term of the additive height law "
                                "h_n = h_mm + a1*u + a2*u^2 (u normalised along the array)")
GRADIENT_A2 = ParamSpec(name="a2", unit="mm", low=-1.5, high=1.5, default=0.0,
                        integer_step=0.05,
                        physics="quadratic term of the additive height law; shapes the "
                                "aperture illumination without adding dimensions")

MIN_WALL_MM = 0.5
MIN_GAP_MM = 0.3


def space() -> ParamSpace:
    return ParamSpace([WAVE_WIDTH, WAVE_HEIGHT, PERIOD, UNIT_HEIGHT, GAP, PERIODS,
                       GRADIENT_A1, GRADIENT_A2])


def build_case(base_frequency_ghz: float = 28.0) -> CaseSpec:
    family = FamilySpec(
        family_id="lwa_periodic_v1",
        antenna_type="leaky_wave",
        param_space=space(),
        builder="aster.cases.lwa:build_model",
        notes="periodic perturbation on a rectangular guide; -1 spatial harmonic",
    )
    return CaseSpec(
        case_id="lwa",
        project="lwa",
        design_type="periodic_leaky_wave",
        frequency_ghz=(base_frequency_ghz - 3.0, base_frequency_ghz + 3.0),
        family=family,
        constraints=[
            Constraint(id="s11", kind="s11", operator="<=", target=-10.0, unit="dB",
                       band_ghz=(base_frequency_ghz - 3.0, base_frequency_ghz + 3.0),
                       violation_scale=3.0),
            Constraint(id="gain_h", kind="realized_gain_h", operator=">=", target=8.0,
                       unit="dBi", violation_scale=2.0),
            Constraint(id="ripple", kind="ripple", operator="<=", target=3.0,
                       unit="dB", violation_scale=1.0),
        ],
        objectives=[
            Objective(id="gain_obj", kind="realized_gain_h", direction="maximize"),
            Objective(id="s11_obj", kind="s11", direction="minimize"),
        ],
        evaluation={"frequencies_ghz": [25.0, 26.5, 28.0, 29.5, 31.0],
                    "max_frequency_points": 7, "horiz_theta_deg": 90},
        execution_policy={"max_run_minutes": 30, "require_convergence": True,
                          "num_cores": 8, "max_total_cores": 16},
        setup_name="Setup1",
        notes="second plugin: proves the core works without a metal lens",
    )


# ------------------------------------------------------------------ knowledge
C_MM_GHZ = 299.792458  # speed of light in mm*GHz


def k0_mm_inv(frequency_ghz: float) -> float:
    return 2.0 * math.pi * float(frequency_ghz) / C_MM_GHZ


def beta0_te10(frequency_ghz: float, width_mm: float) -> float:
    """Propagation constant of the dominant TE10 mode in an empty guide."""
    if width_mm <= 0:
        raise ValueError("guide width must be positive")
    k0 = k0_mm_inv(frequency_ghz)
    kc = math.pi / float(width_mm)
    inside = k0 * k0 - kc * kc
    return math.sqrt(inside) if inside > 0 else 0.0


def beta_n(beta0: float, period_mm: float, n: int) -> float:
    """beta_n = beta_0 + 2*pi*n/p (spatial harmonics of a periodic guide)."""
    if period_mm <= 0:
        raise ValueError("period must be positive")
    return beta0 + 2.0 * math.pi * n / period_mm


def beam_angle_deg(beta: float, frequency_ghz: float) -> float:
    """theta = asin(beta_n / k0); NaN when the harmonic does not radiate."""
    k0 = k0_mm_inv(frequency_ghz)
    ratio = beta / k0 if k0 else float("nan")
    if not math.isfinite(ratio) or abs(ratio) > 1.0:
        return float("nan")
    return math.degrees(math.asin(ratio))


def radiating_harmonic(width_mm: float, period_mm: float, frequency_ghz: float,
                       n: int = -1) -> tuple[bool, float, float]:
    """Is the n-th harmonic radiating, and where does it point?"""
    beta0 = beta0_te10(frequency_ghz, width_mm)
    beta = beta_n(beta0, period_mm, n)
    angle = beam_angle_deg(beta, frequency_ghz)
    return math.isfinite(angle), beta, angle


def open_stopband_suspected(gain_curve: dict[float, float],
                            reflection_curve: dict[float, float],
                            threshold_db: float = 1.5) -> dict[str, Any]:
    """Heuristic: gain dip with a simultaneous reflection rise near broadside."""
    freqs = sorted(set(gain_curve) & set(reflection_curve))
    findings = []
    for index in range(1, len(freqs) - 1):
        freq = freqs[index]
        dip = gain_curve[freq] - 0.5 * (gain_curve[freqs[index - 1]] + gain_curve[freqs[index + 1]])
        rise = reflection_curve[freq] - 0.5 * (
            reflection_curve[freqs[index - 1]] + reflection_curve[freqs[index + 1]]
        )
        if dip < -threshold_db and rise > threshold_db:
            findings.append({"frequency_ghz": freq, "gain_dip_db": round(dip, 3),
                             "reflection_rise_db": round(rise, 3)})
    return {"suspected": bool(findings), "points": findings}


# -------------------------------------------------------------------- builder
def operations(params: dict[str, Any]) -> list[dict[str, Any]]:
    n_units = int(params["n_units"])
    ops: list[dict[str, Any]] = [
        {"op": "delete_if_present", "args": {"names": ["lwa_units"]}},
        {"op": "create_box", "args": {"name": "lwa_guide",
                                      "origin": [0, 0, 0],
                                      "sizes": [float(params["a_mm"]), float(params["b_mm"]),
                                                float(params["p_mm"]) * n_units],
                                      "material": "vacuum"}},
        {"op": "create_tapered_units", "args": {"count": n_units,
                                                "pitch": float(params["p_mm"]),
                                                "h0": float(params["h_mm"]),
                                                "a1": float(params["a1"]),
                                                "a2": float(params["a2"]),
                                                "mode": "additive",
                                                "gap": float(params["g_mm"]),
                                                "width": float(params["a_mm"]),
                                                "depth": float(params["g_mm"]),
                                                "base_y": float(params["b_mm"])}},
        {"op": "validate_simple", "args": {"attempts": 6, "delay_s": 2.0}},
    ]
    return ops


def unit_heights(params: dict[str, Any]) -> list[float]:
    """The N rib heights from the three-parameter law h_n = h0 + a1*u + a2*u^2.

    ``u`` runs from 0 to 1 across the array, so twenty independent heights collapse into the
    three numbers the optimiser actually searches (GPT's task book, item 10).
    """
    count = int(params["n_units"])
    h0 = float(params["h_mm"])
    a1 = float(params.get("a1", 0.0))
    a2 = float(params.get("a2", 0.0))
    heights: list[float] = []
    for index in range(max(1, count)):
        u = 0.0 if count <= 1 else index / float(count - 1)
        heights.append(h0 + a1 * u + a2 * u * u)
    return heights


def geometry_checks(params: dict[str, Any]) -> GeometryReport:
    report = GeometryReport(valid=True)
    band = check_manufacturing(params, {
        "a_mm": {"min": 18.0, "max": 26.0},
        "b_mm": {"min": 6.0, "max": 12.0},
        "h_mm": {"min": MIN_WALL_MM, "max": 4.0},
        "g_mm": {"min": MIN_GAP_MM, "max": 2.0},
    })
    for name, ok in band.checks.items():
        report.add(name, ok)
    period = float(params["p_mm"])
    height = float(params["h_mm"])
    report.add("unit_fits_period", height < period, f"h_mm={height} >= p_mm={period}")
    heights = unit_heights(params)
    if heights:
        lowest = min(heights)
        tallest = max(heights)
        report.add("taper_stays_printable", lowest >= MIN_WALL_MM,
                   f"lowest rib {lowest:.3f} mm below the {MIN_WALL_MM} mm wall minimum")
        report.add("taper_stays_below_period", tallest < period,
                   f"tallest rib {tallest:.3f} mm reaches the period {period:.3f} mm")
    return report


def em_checks(params: dict[str, Any], band_low_ghz: float = 24.0) -> GeometryReport:
    report = GeometryReport(valid=True)
    try:
        ok, _beta, angle = radiating_harmonic(float(params["a_mm"]), float(params["p_mm"]),
                                              band_low_ghz)
        report.add("radiating_harmonic", ok,
                   f"-1 harmonic does not radiate at {band_low_ghz} GHz with p="
                   f"{params['p_mm']} mm (angle={angle})")
    except ValueError as exc:
        report.add("radiating_harmonic", False, str(exc))
    return report


def build_model(apply: Any = None) -> OperationModel:
    return OperationModel(space(), operations, apply=apply,
                          geometry_checks=geometry_checks, em_checks=em_checks)


def register() -> CaseRegistration:
    return CaseRegistration(
        name="lwa",
        case_factory=build_case,
        model_factory=build_model,
        antenna_type="leaky_wave",
        notes="second plugin (generality proof); wire a real project in S7",
    )

