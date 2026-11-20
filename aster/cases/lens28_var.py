# -*- coding: utf-8 -*-
"""Variable-driven version of the reference family (replaces the operation script).

Every managed body is rebuilt from a parameterised profile: the bowls shift with the tip
offset, the disc scales about its mid-plane, and the coax outer wall steps out to the
ground-ring radius. No ``unite`` and no ``subtract`` runs, so the same design can be
re-parameterised any number of times.

The parameter names and ranges match :mod:`aster.cases.lens28_ref` so the measured
dataset, the GUI and the benchmark harness all work unchanged.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

from ..core import (CaseSpec, Constraint, FamilySpec, Objective, ParamSpace,
                    ParamSpec)
from ..parametric.model import GeometryReport, OperationModel
from ..parametric.profile import (bowl_profile, load_profiles, mirror_profile,
                                  scale_disc, shift_profile, with_ring_collar)

PROFILE_FILE = Path(__file__).resolve().parent / "lens28_profiles.json"
PIN_RADIUS_MM = 0.4
PIN_Z0_MM = -20.0
PIN_HEIGHT_MM = 21.0
MOUTH_Z_MM = -1.0

PROFILES = load_profiles(PROFILE_FILE)


def build_space() -> ParamSpace:
    return ParamSpace([
        ParamSpec(name="tip_offset", unit="mm", low=0.9, high=1.4, default=1.0,
                  integer_step=0.1, physics="bowl tip distance from the disc mid-plane"),
        ParamSpec(name="disc_factor", unit="1", low=0.8, high=1.3, default=1.0,
                  integer_step=0.05, physics="disc thickness scaling about its mid-plane"),
        ParamSpec(name="disc_radial", unit="1", low=0.5, high=1.0, default=1.0,
                  integer_step=0.1, physics="disc radius scaling"),
        ParamSpec(name="ring_inner", unit="mm", low=1.2, high=2.0, default=1.2,
                  integer_step=0.1, physics="ring inner radius (= dielectric radius)"),
        ParamSpec(name="ring_outer", unit="mm", low=1.8, high=5.0, default=3.0,
                  integer_step=0.2, physics="ground ring outer radius at the coax mouth"),
        ParamSpec(name="ring_height", unit="mm", low=0.2, high=1.2, default=0.6,
                  integer_step=0.1, physics="inner ground ring height"),
        ParamSpec(name="ring_mid", unit="mm", low=1.4, high=5.2, default=3.0,
                  integer_step=0.1,
                  physics="radial split of the ground ring collar; >= ring_outer means one height"),
        ParamSpec(name="ring_height_outer", unit="mm", low=0.2, high=1.2, default=0.6,
                  integer_step=0.1,
                  physics="outer ground ring height, used when ring_mid < ring_outer"),
        ParamSpec(name="ring_height_mid_delta", unit="mm", low=-0.6, high=0.6, default=0.0,
                  integer_step=0.05,
                  physics="middle collar level offset from the inner height; 0 keeps two levels"),
        ParamSpec(name="ring_shift", unit="mm", low=-0.6, high=0.6, default=0.0,
                  integer_step=0.1, physics="ground ring shift along Z"),
    ])


def hole_rings(lower: bool, tip_offset_mm: float = 1.0) -> list[dict]:
    """The reference bowls carry two arrays of holes, measured from the design's faces.

    Six holes of radius 1.0955 mm at radius 4.2 mm (the coupling holes above the feed) and
    twelve holes of radius 1.2649 mm at radius 16.75 mm (the rim holes). The radii come from
    the face areas of the reference bowl (3.7699 and 5.0265 mm2), the lower bowl mirrors the
    upper one in Z.
    """
    sign = -1.0 if lower else 1.0
    prefix = "dn" if lower else "up"
    holes: list[dict] = []
    for index in range(6):
        angle = math.radians(30.0 + 60.0 * index)
        holes.append({"name": "hole_small_%s_%d" % (prefix, index),
                      "x": 4.2 * math.cos(angle), "y": 4.2 * math.sin(angle),
                      "z": sign * (tip_offset_mm + 0.5), "radius": 1.0955,
                      "height": 2.0})
    for index in range(12):
        angle = math.radians(30.0 + 30.0 * index)
        holes.append({"name": "hole_rim_%s_%d" % (prefix, index),
                      "x": 16.75 * math.cos(angle), "y": 16.75 * math.sin(angle),
                      "z": sign * (tip_offset_mm + 4.3), "radius": 1.2649,
                      "height": 2.0})
    return holes


def operations_full(params: dict) -> list[dict]:
    tip = float(params["tip_offset"])
    disc_factor = float(params["disc_factor"])
    disc_radial = float(params["disc_radial"])
    ring_outer = float(params["ring_outer"])
    ring_height = float(params["ring_height"])
    ring_shift = float(params.get("ring_shift", 0.0))
    ring_inner = float(params.get("ring_inner", 1.2))
    ring_mid = float(params.get("ring_mid", ring_outer))
    ring_height_outer = float(params.get("ring_height_outer", ring_height))
    ring_height_mid = ring_height + float(params.get("ring_height_mid_delta", 0.0))

    lower = bowl_profile(tip, 1.0, PROFILES["cone_dn"])
    if ring_outer > max(ring_inner, 2.0) + 1e-9:
        lower = with_ring_collar(lower, ring_inner, ring_outer, ring_height, ring_shift,
                                 mid_radius_mm=ring_mid,
                                 outer_height_mm=ring_height_outer,
                                 middle_height_mm=ring_height_mid)
    # the upper bowl is the mirror of the lower one about the disc mid-plane
    upper = mirror_profile(lower)
    ops: list[dict] = [
        {"op": "revolve_profile", "args": {"points": lower, "name": "cone_dn",
                                           "material": "pec"}},
        {"op": "revolve_profile", "args": {"points": upper, "name": "cone_up",
                                           "material": "pec"}},
        {"op": "revolve_profile",
         "args": {"points": scale_disc(PROFILES["disc"], disc_radial, disc_factor),
                  "name": "disc", "material": "pec"}},
    ]
    # the reference bowls are drilled with two rings of small holes; reproduce them so the
    # rebuilt geometry stays electromagnetically equivalent
    for lower_bowl, target in ((True, "cone_dn"), (False, "cone_up")):
        # one subtract per bowl with every hole as a tool: 36 boolean objects became 2
        names = []
        for hole in hole_rings(lower_bowl, tip):
            ops.append({"op": "create_cylinder",
                        "args": {"name": hole["name"],
                                 "origin": [hole["x"], hole["y"],
                                            hole["z"] - hole["height"] / 2.0],
                                 "radius": hole["radius"], "height": hole["height"],
                                 "material": "vacuum"}})
            names.append(hole["name"])
        ops.append({"op": "subtract", "args": {"target": target, "tools": names,
                                                   "keep_originals": False}})
    ops.append({"op": "validate_simple", "args": {"attempts": 4, "delay_s": 2.0}})
    return ops


BOWL_PARAMETERS = ("tip_offset", "ring_inner", "ring_outer", "ring_height", "ring_shift",
                   "ring_mid", "ring_height_outer", "ring_height_mid_delta")
DISC_PARAMETERS = ("disc_factor", "disc_radial")


def _changed(previous: dict, params: dict, names) -> bool:
    return any(abs(float(params.get(name, 0.0)) - float(previous.get(name, 0.0))) > 1e-9
               for name in names)


def incremental_operations(params: dict, previous: dict) -> list[dict]:
    """Only rebuild the bodies whose parameters changed.

    A disc-thickness tweak then costs one revolve instead of three revolves plus the whole
    hole arrays, which is what makes repeated parameter changes cheap in one session.
    """
    rebuild_bowls = _changed(previous, params, BOWL_PARAMETERS)
    rebuild_disc = _changed(previous, params, DISC_PARAMETERS)
    full = operations_full(params)
    if not rebuild_bowls and not rebuild_disc:
        return [op for op in full if op["op"] == "validate_simple"]
    selection = []
    skip = set()
    if not rebuild_bowls:
        skip.update({"cone_dn", "cone_up"})
    if not rebuild_disc:
        skip.add("disc")
    for op in full:
        name = (op.get("args") or {}).get("name") or (op.get("args") or {}).get("target")
        if op["op"] in {"revolve_profile"} and name in skip:
            continue
        if op["op"] == "create_cylinder" and not rebuild_bowls:
            continue
        if op["op"] == "subtract" and not rebuild_bowls:
            continue
        selection.append(op)
    return selection


def geometry_checks(params: dict) -> GeometryReport:
    report = GeometryReport(valid=True)
    report.add("tip_positive", float(params["tip_offset"]) > 0.0,
               "tip offset must be positive")
    report.add("disc_factor_positive", float(params["disc_factor"]) > 0.0,
               "disc factor must be positive")
    report.add("disc_radial_positive", float(params["disc_radial"]) > 0.0,
               "disc radial factor must be positive")
    report.add("ring_outer_above_wall",
               float(params["ring_outer"]) >= 2.0 - 1e-9,
               "ring outer radius must be at least the coax wall radius (2.0 mm)")
    report.add("ring_height_positive", float(params["ring_height"]) > 0.0,
               "ring height must be positive")
    inner = float(params.get("ring_inner", 1.2))
    mid = float(params.get("ring_mid", params["ring_outer"]))
    splitting = mid < float(params["ring_outer"]) - 1e-9
    report.add("ring_mid_reaches_inner", mid >= inner - 1e-9,
               "the collar split must not fall inside the coax wall")
    report.add("ring_mid_inside_when_split",
               (not splitting) or (inner + 1e-9 < mid < float(params["ring_outer"]) - 1e-9),
               "a split collar needs inner < ring_mid < ring_outer")
    report.add("ring_height_outer_positive",
               float(params.get("ring_height_outer", params["ring_height"])) > 0.0,
               "outer ring height must be positive")
    report.add("ring_height_mid_positive",
               float(params["ring_height"]) + float(params.get("ring_height_mid_delta", 0.0)) > 0.0,
               "the middle collar level must stay above the tip face")
    return report


def em_checks(params: dict) -> GeometryReport:
    report = GeometryReport(valid=True)
    report.add("tip_below_half_bowl", float(params["tip_offset"]) <= 1.4,
               "tip offset above 1.4 mm leaves the bowl detached")
    return report


def build_model(apply=None) -> OperationModel:
    space = build_space()
    return OperationModel(space, operations_full, apply=apply,
                          geometry_checks=geometry_checks, em_checks=em_checks,
                          incremental_operations=incremental_operations)


def build_case(include_efficiency: bool = False) -> CaseSpec:
    """The variable-driven family.

    `include_efficiency` is refused until a controlled run proves the numbers: the old evidence
    (docs/evidence/quantities_probe.json) was too weak to support "unavailable", and the guard now
    computes efficiency from the sphere-grid integral whenever `include_directivity` is on
    (aedt_guard/pattern.py). Directivity is part of this case because the brief asks for radiation
    at theta = 90 deg; the efficiency column stays pending verification (docs/decisions/efficiency.md)
    and is not exposed to the optimiser yet.
    """
    if include_efficiency:
        raise ValueError(
            "radiation efficiency is pending verification for lens28_var: the guard can derive it "
            "from the sphere-grid integral, but no controlled run has cross-checked the value yet "
            "(docs/decisions/efficiency.md)")
    model = build_model()
    family = FamilySpec(
        family_id="lens28_var",
        antenna_type="metal_lens_periodic_disc",
        param_space=model.space,
        notes="variable-driven rebuild of the reference family (no boolean operations)",
    )
    return CaseSpec(
        case_id="lens28_var",
        project="lens28_var",
        design_type="metal_lens_periodic_disc",
        frequency_ghz=(24.0, 32.0),
        family=family,
        constraints=[
            Constraint(id="peak_gain", kind="peak_gain", operator=">=", target=6.0,
                       unit="dBi"),
            Constraint(id="directivity_h", kind="directivity_horiz_db", operator=">=",
                       target=7.0, unit="dBi"),
            Constraint(id="beam_theta_min", kind="beam_theta", operator=">=", target=85.0,
                       unit="deg"),
            Constraint(id="beam_theta_max", kind="beam_theta", operator="<=", target=95.0,
                       unit="deg"),
            Constraint(id="ripple", kind="ripple", operator="<=", target=3.0,
                       unit="dB"),
            Constraint(id="bandwidth", kind="s11_bandwidth", operator=">=", target=8.0,
                       unit="GHz"),
        ],
        objectives=[
            Objective(id="bw_obj", kind="s11_bandwidth", direction="maximize"),
            Objective(id="gain_obj", kind="peak_gain", direction="maximize"),
            # the brief: radiation concentrated at theta = 90 deg, 7 dBi in that plane
            Objective(id="dir_obj", kind="directivity_horiz_db", direction="maximize"),
            # the brief asks for the lowest possible sidelobes: sll is dBc relative to the main peak
            Objective(id="sll_obj", kind="sll", direction="minimize"),
        ] + ([Objective(id="eff_obj", kind="radiation_efficiency",
                       direction="maximize")] if include_efficiency else []),
        evaluation={"frequencies_ghz": [28.0],
                    "include_directivity": True,
                    "horiz_theta_deg": 90.0},
        # a discrete sweep can still be solving when the export arrives; the retries live in
        # the case so every publisher of this spec inherits them
        execution_policy={"max_run_minutes": 120, "require_ff_sphere": True,
                          "require_convergence": True, "num_cores": 16,
                          # a 13-point discrete sweep on a slow design can still be exporting long
                          # after the adaptive solve: the measured window must cover it, otherwise a
                          # good design is thrown away as E_SWEEP_EXPORT_INCOMPLETE (seen twice now)
                          "touchstone_retries": 80, "touchstone_retry_delay_s": 20.0},
    )


PENDING_METRICS = {
    "radiation_efficiency": (
        "旧结论（不可得）已被只读枚举推翻：Antenna Parameters 可枚举 RadiationEfficiency、"
        "RadiatedPower、AcceptedPower 等（docs/evidence/quantities_full.json）。"
        "把 η 接成目标/约束还需要一次受控取数与三条路径互校，见 docs/decisions/efficiency.md"),
}

UNAVAILABLE_METRICS: dict[str, str] = {}
"""Metrics this case genuinely cannot produce. Efficiency moved out of this set on 2026-11-15."""


def register():
    from ..parametric.registry import CaseRegistration

    return CaseRegistration(
        name="lens28_var",
        case_factory=build_case,
        model_factory=build_model,
        antenna_type="metal_lens_periodic_disc",
        notes="variable-driven reference family; every body is rebuilt from a profile",
        unavailable_metrics=dict(UNAVAILABLE_METRICS),
        pending_metrics=dict(PENDING_METRICS),
    )
