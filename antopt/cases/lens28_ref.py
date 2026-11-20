"""Reference lens family (LensFinal f24g05) with two searchable structure knobs.

The measured baseline is the user-supplied LensFinal design: S11 -16.65 dB at
28 GHz, 1.0 GHz of contiguous <=-10 dB bandwidth (27-28 GHz), peak gain 6.22 dBi.
A tip-offset scan showed that moving the bowls outward destroys the match while
raising the gain, so bandwidth has to be pursued through other dimensions:

* ``tip_offset``        axial distance from the disc mid-plane to each bowl tip
                        (reference value 1.0 mm, the matching optimum),
* ``disc_factor``       thickness multiplier of the main disc about its centre
                        (reference thickness 5.4 mm).

The family base project must already contain the discrete band sweep used by the
campaign runner; ``scripts/measure_band_discrete.py`` builds such a copy.
"""
from __future__ import annotations

from ..core import CaseSpec, Constraint, FamilySpec, Objective, ParamSpace, ParamSpec
from ..parametric.model import GeometryReport, OperationModel
from ..parametric.registry import CaseRegistration


def build_case() -> CaseSpec:
    tip = ParamSpec(
        name="tip_offset", unit="mm", low=0.9, high=1.4, default=1.0, integer_step=0.1,
        physics="bowl tip distance from the disc mid-plane; 1.0 mm is the measured "
                "matching optimum, outward motion trades match for gain",
    )
    disc = ParamSpec(
        name="disc_factor", unit="1", low=0.8, high=1.3, default=1.0, integer_step=0.05,
        physics="scale factor on the main disc thickness about its centre; the disc "
                "straddles the coax mouth and sets the cavity height",
    )
    radial = ParamSpec(
        name="disc_radial", unit="1", low=0.5, high=1.0, default=1.0, integer_step=0.1,
        physics="radial scale of the main disc (rim radius 70 mm * factor); shrinking "
                "the aperture changes the guided path and is the structural knob for "
                "bandwidth that the tip/thickness scans could not provide",
    )
    ring_inner = ParamSpec(
        name="ring_inner", unit="mm", low=1.2, high=2.0, default=1.2, integer_step=0.1,
        physics="inner radius of the PEC ring bridging the coax outer conductor and "
                "the lower bowl; 1.2 mm matches the coax dielectric radius",
    )
    ring_outer = ParamSpec(
        name="ring_outer", unit="mm", low=1.8, high=5.0, default=3.0, integer_step=0.2,
        physics="outer radius of the ground ring; the measured 3.0 mm ring moved the "
                "passband to 25-27 GHz, so this radius sets the resonance position",
    )
    ring_height = ParamSpec(
        name="ring_height", unit="mm", low=0.2, high=1.2, default=0.6, integer_step=0.1,
        physics="axial thickness of the ground ring; it straddles the coax mouth by "
                "half of this value on each side",
    )
    # one authoritative parameter list: the model space (kept in sync by
    # construction so the case can never miss a searchable parameter)
    space = build_model().space
    family = FamilySpec(
        family_id="lens28_ref_v1",
        antenna_type="metal_lens_periodic_disc",
        param_space=space,
        builder="antopt.cases.lens28_ref:build_model",
        notes="reference design f24g05 measured today; only tip offset and disc "
              "thickness are searchable, everything else is the proven structure",
    )
    return CaseSpec(
        case_id="lens28_ref",
        project="lens28_ref",
        design_type="metal_lens_periodic_disc",
        frequency_ghz=(24.0, 30.0),
        family=family,
        constraints=[
            # the full 24-30 GHz band is not reachable in this topology (12 measured
            # directions excluded it); the agreed goal is now "maximise bandwidth
            # with gain >= 6 dBi", gated on a 3 GHz contiguous match
            Constraint(id="peak_gain", kind="peak_gain", operator=">=", target=6.0,
                       unit="dBi", violation_scale=2.0),
            Constraint(id="ripple", kind="ripple", operator="<=", target=3.0, unit="dB",
                       violation_scale=1.0),
            Constraint(id="bandwidth", kind="s11_bandwidth", operator=">=", target=3.0,
                       unit="GHz", violation_scale=1.0),
        ],
        objectives=[
            Objective(id="gain_obj", kind="peak_gain", direction="maximize"),
            Objective(id="bw_obj", kind="s11_bandwidth", direction="maximize"),
        ],
        evaluation={"frequencies_ghz": [28.0], "max_frequency_points": 7,
                    "theta_cut_phi_deg": [0, 90], "horiz_theta_deg": 90,
                    "main_lobe_exclusion_deg": 10,
                    "realized_gain_mode": "gain_plus_mismatch"},
        execution_policy={"max_run_minutes": 50, "require_ff_sphere": True,
                          "require_convergence": True, "num_cores": 16,
                          "touchstone_retries": 40, "touchstone_retry_delay_s": 15.0,
                          "num_tasks": 1, "max_total_cores": 16,
                          "enforce_cpu_budget": False,
                          "touchstone_retries": 40,
                          "touchstone_retry_delay_s": 15.0},
        setup_name="P4",
        notes="baseline: S11 -16.65 dB @28, bandwidth 1.0 GHz, peak 6.22 dBi, "
              "ripple 1.72 dB (run 20261110T101202425058)",
    )


def operations_full(params: dict) -> list[dict]:
    tip = float(params["tip_offset"])
    factor = float(params["disc_factor"])
    radial = float(params.get("disc_radial", 1.0))
    ring_inner = float(params.get("ring_inner", 1.2))
    ring_outer = float(params.get("ring_outer", 3.0))
    ring_height = float(params.get("ring_height", 0.6))
    ring_shift = float(params.get("ring_shift", 0.0))
    ring2_outer = float(params.get("ring2_outer", 0.0))
    ring2_height = float(params.get("ring2_height", 0.4))
    ring2_shift = float(params.get("ring2_shift", -0.4))
    feed_radius = float(params.get("feed_radius", 0.4))
    feed_len = float(params.get("feed_len", 1.6))
    feed_z0 = float(params.get("feed_z0", -2.8))
    ops = [
        {"op": "scale_xy_about_axis", "args": {"name": "disc", "factor": radial}},
        {"op": "scale_z_about_centre", "args": {"name": "disc", "factor": factor}},
        {"op": "gap_bowls", "args": {"tip_offset_mm": tip, "disc_offset_mm": 1.0}},
        {"op": "add_step_ring", "args": {"inner_radius": ring_inner,
                                         "outer_radius": ring_outer,
                                         "height": ring_height,
                                         "shift": ring_shift,
                                         "target": "cone_dn"}},
    ]
    if ring2_outer > 0:
        ops.append({"op": "add_step_ring",
                    "args": {"inner_radius": ring_inner,
                             "outer_radius": ring2_outer,
                             "height": ring2_height,
                             "shift": ring2_shift,
                             "tag": "r2",
                             "target": "cone_dn"}})
    if feed_radius >= 0.45:
        ops.append({"op": "add_inner_sleeve",
                    "args": {"radius": feed_radius, "z0": feed_z0,
                             "length": feed_len}})
    ops.append({"op": "validate_simple", "args": {"attempts": 4, "delay_s": 2.0}})
    return ops


RING_PARAMETERS = ("ring_inner", "ring_outer", "ring_height", "ring_shift",
                   "ring2_outer", "ring2_height", "ring2_shift")


def incremental_operations(params: dict, previous: dict) -> list[dict]:
    """Operations for an in-place parameter update.

    `add_step_ring` unites the ring into the lower cone, which absorbs that cone and
    makes a second apply fail. When every ring parameter is unchanged the ring is
    already in place, so the destructive operation is dropped and the remaining moves
    (disc scaling, bowl repositioning) can be applied repeatedly.
    """
    operations = operations_full(params)
    same_ring = all(abs(float(params.get(name, 0.0)) - float(previous.get(name, 0.0))) < 1e-9
                    for name in RING_PARAMETERS)
    if same_ring:
        operations = [op for op in operations if op["op"] != "add_step_ring"]
    return operations


def geometry_checks(params: dict) -> GeometryReport:
    report = GeometryReport(valid=True)
    report.add("tip_offset_positive", float(params["tip_offset"]) > 0.0,
               "tip offset must be positive")
    report.add("disc_factor_positive", float(params["disc_factor"]) > 0.0,
               "disc factor must be positive")
    thickness = 5.4 * float(params["disc_factor"])
    report.add("disc_thickness", thickness <= 8.0,
               "scaled disc thickness %.2f mm exceeds 8 mm" % thickness)
    radial = float(params.get("disc_radial", 1.0))
    report.add("disc_rim", 70.0 * radial >= 20.0,
               "scaled disc rim %.1f mm is smaller than the bowl rim" % (70.0 * radial))
    return report


def em_checks(params: dict) -> GeometryReport:
    report = GeometryReport(valid=True)
    report.add("disc_straddles_mouth", 5.4 * float(params["disc_factor"]) >= 1.2,
               "disc must stay thicker than the coax mouth offset")
    return report


def build_model(apply=None) -> OperationModel:
    space = ParamSpace([
        ParamSpec(name="tip_offset", unit="mm", low=0.9, high=1.4, default=1.0,
                  integer_step=0.1),
        ParamSpec(name="disc_factor", unit="1", low=0.8, high=1.3, default=1.0,
                  integer_step=0.05),
        ParamSpec(name="disc_radial", unit="1", low=0.5, high=1.0, default=1.0,
                  integer_step=0.1),
        ParamSpec(name="ring_inner", unit="mm", low=1.2, high=2.0, default=1.2,
                  integer_step=0.1),
        ParamSpec(name="ring_outer", unit="mm", low=1.8, high=5.0, default=3.0,
                  integer_step=0.2),
        ParamSpec(name="ring_height", unit="mm", low=0.2, high=1.2, default=0.6,
                  integer_step=0.1),
        ParamSpec(name="ring_shift", unit="mm", low=-0.6, high=0.6, default=0.0,
                  integer_step=0.1),
        ParamSpec(name="ring2_outer", unit="mm", low=0.0, high=5.0, default=0.0,
                  integer_step=0.2),
        ParamSpec(name="ring2_height", unit="mm", low=0.2, high=1.0, default=0.4,
                  integer_step=0.1),
        ParamSpec(name="ring2_shift", unit="mm", low=-0.8, high=0.2, default=-0.4,
                  integer_step=0.1),
        ParamSpec(name="feed_radius", unit="mm", low=0.4, high=1.0, default=0.4,
                  integer_step=0.05),
        ParamSpec(name="feed_len", unit="mm", low=0.6, high=3.0, default=1.6,
                  integer_step=0.2),
        ParamSpec(name="feed_z0", unit="mm", low=-6.0, high=-1.2, default=-2.8,
                  integer_step=0.2),
    ])
    return OperationModel(space, operations_full, apply=apply,
                          geometry_checks=geometry_checks, em_checks=em_checks,
                          incremental_operations=incremental_operations)


def register() -> CaseRegistration:
    return CaseRegistration(
        name="lens28_ref",
        case_factory=build_case,
        model_factory=build_model,
        antenna_type="metal_lens_periodic_disc",
        notes="reference family with tip_offset and disc thickness as parameters",
    )
