"""Physical tolerance plans, repeat measurements, and production sample scoring."""
import math
import pytest

from aster.core import ParamSpace, ParamSpec
from aster.robustness import plan_tolerance_study, score_process_yield, score_robustness


@pytest.fixture
def space():
    return ParamSpace([
        ParamSpec("width", "mm", 0.1, 2.0, 1.0, integer_step=0.2),
        ParamSpec("angle", "deg", -3.0, 3.0, 0.0, integer_step=0.5),
    ])


def measured(params, gain, loss=0.5, **fields):
    return {"params": params, "metrics": {"gain": gain, "loss": loss},
            "status": "COMPLETED", **fields}


def test_paired_tooling_offset_and_signed_margins(space):
    config = dict(scenarios={"tool drift": {"width": -50, "angle": 1}},
                  constraints={"gain": (">=", 6.0), "loss": ("<=", 1.0)})
    nominal = space.default()
    tolerances = {"width": {"delta": 50, "unit": "µm"}, "angle": 1}
    first = plan_tolerance_study(space, nominal, tolerances, **config)
    assert [p["params"] for p in first["pending"]] == [nominal, {"width": 0.95, "angle": 1.0}]
    off = first["pending"][1]["params"]
    rows = [measured(nominal, 7, 0.7), measured(off, 5, 0.8)]
    done = plan_tolerance_study(space, nominal, tolerances, measurements=rows, **config)
    assert done["complete"] and not done["pending"]
    assert done["worst_margin"] == pytest.approx({"gain": -1, "loss": 0.2})
    assert done["scenario_pass_fraction"] == 0.5 and done["manufacturing_yield"] is None


def test_corner_coverage_and_rounded_duplicate(space):
    plan = plan_tolerance_study(space, space.default(), {"width": 0.02, "angle": 1}, corners=True)
    assert len(plan["scenarios"]) == 5 and len(plan["pending"]) == 5
    # A physical build increment of 100 um cannot represent ±20 um.
    plan = plan_tolerance_study(space, space.default(), {"width": {"delta": 20, "unit": "um",
                                "resolution": 100}}, measurements=[measured(space.default(), 6)])
    assert not plan["complete"] and plan["pending"] == []
    assert [x["status"] for x in plan["scenarios"]] == ["verified", "quantized", "quantized"]
    assert [x["quantization_error"].get("width", 0.0) for x in plan["scenarios"]] == pytest.approx([0, .02, -.02])


def test_invalid_units_and_unreachable_extreme(space):
    with pytest.raises(ValueError, match="units"):
        plan_tolerance_study(space, space.default(), {"width": {"delta": 1, "unit": "deg"}})
    nominal = dict(space.default(), width=0.1)
    plan = plan_tolerance_study(space, nominal, {"width": 0.05})
    assert plan["scenarios"][1]["outside"] == ["width"]
    assert len(plan["pending"]) == 2 and not plan["complete"]


def test_finite_hard_metrics_and_distinct_repeats(space):
    nominal = space.default()
    conf = dict(constraints={"loss": ("<=", 1.0)}, keys=["gain"], required_repeats=2)
    rows = [measured(nominal, 5, run_id="a"), measured(nominal, 5, run_id="a"),
            measured(nominal, 5, math.nan, run_id="b"), measured(nominal, 5, run_id="cached", cache_hit=True)]
    plan = plan_tolerance_study(space, nominal, {"width": 0.1}, measurements=rows, **conf)
    assert plan["pending"][0]["remaining_repeats"] == 1
    rows.append(measured(nominal, 5, 1.2, run_id="c"))
    plan = plan_tolerance_study(space, nominal, {"width": 0.1}, measurements=rows, **conf)
    assert plan["scenarios"][0]["passed"] is False
    assert plan["worst_margin"]["loss"] == pytest.approx(-0.2)
    assert not score_robustness([{"gain": 8.0}], keys=("gain",),
                                constraints={"loss": ("<=", 1.0)})["worst_verified"]


def test_missing_and_conflicting_independent_process_draws():
    ids = [str(k) for k in range(10)]
    kwargs = dict(process_id="lot", sample_ids=ids, constraints={"gain": (">=", 0.0)},
                  independent_random_sample=True, sample_source="manufactured")
    rows = [measured({}, 1 if i < 5 else -1, sample_id=s, process_id="lot") for i, s in enumerate(ids)]
    result = score_process_yield(rows, **kwargs)
    assert result["manufacturing_yield"] == 0.5
    assert result["wilson_95"] == pytest.approx([0.23659309, 0.76340691])
    assert score_process_yield(rows[:-1], **kwargs)["wilson_95"] is None
    mixed = [dict(rows[0], sample_source="simulated"), *rows[1:]]
    assert score_process_yield(mixed, **kwargs)["manufacturing_yield"] is None
    duplicate = measured({}, -1, sample_id=ids[0], process_id="lot")
    assert score_process_yield(rows + [duplicate], **kwargs)["unresolved"] == ["0"]
    selected = score_process_yield(rows, **{**kwargs, "independent_random_sample": False})
    assert selected["manufacturing_yield"] is None
    modeled = score_process_yield(rows, **{**kwargs, "sample_source": "simulated"})
    assert modeled["modeled_yield"] == 0.5 and modeled["manufacturing_yield"] is None

def test_tolerance_worst_objectives_can_reverse_nominal_ranking(space):
    kwargs = dict(objective_directions={"gain": "maximize", "loss": "minimize"})
    def assess(width, gains, losses):
        nominal = dict(space.default(), width=width)
        pts = plan_tolerance_study(space, nominal, {"width": .05})["pending"]
        history = [measured(point["params"], gain, loss) for point, gain, loss in zip(pts, gains, losses)]
        return plan_tolerance_study(space, nominal, {"width": .05}, measurements=history, **kwargs)
    a = assess(0.5, [10, 4, 7], [1, 4, 2])
    b = assess(1.5, [8, 6, 7], [2, 2, 3])
    assert a["verified_worst_objectives"] == {"gain": 4, "loss": 4}
    assert b["verified_worst_objectives"] == {"gain": 6, "loss": 3}
    partial = assess(0.5, [10], [1])
    assert partial["observed_worst_objectives"] == {"gain": 10, "loss": 1}
    assert partial["verified_worst_objectives"] == {}

def test_scenario_budget_is_checked_before_expanding_corners():
    space = ParamSpace([ParamSpec(f"x{i}", "mm", 0, 2, 1) for i in range(40)])
    widths = dict.fromkeys(space.default(), .1)
    with pytest.raises(ValueError, match="exceed max_scenarios"):
        plan_tolerance_study(space, space.default(), widths, corners=True)
    plan = plan_tolerance_study(space, space.default(), widths)
    assert plan["planned_count"] == 81
