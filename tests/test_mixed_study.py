"""Exercise measured-history ingestion using analytic test observations, without AEDT."""
from aster.core import ParamSpace, ParamSpec
from aster.design_study import DesignStudy
from aster.parametric.model import OperationModel
from aster.synthetic import SyntheticProblem, build_case


def test_choice_and_integer_survive_recommendation_from_completed_history():
    problem = SyntheticProblem()
    case = build_case(problem)
    space = ParamSpace([*case.family.param_space,
                        ParamSpec("feed", "", 0, 1, "probe", kind="choice", choices=("probe", "slot")),
                        ParamSpec("elements", "", 2, 6, 3, kind="int")])
    case.family.param_space = space
    model = OperationModel(space, lambda params: [])
    study = DesignStudy(case, model, {
        "case_id": case.case_id, "profile_id": "mixed-test", "revision": 1, "recipe_id": "fixture-v1",
        "band": {"band_ghz": [24, 25], "max_gap_ghz": 1}, "objectives": ["peak_gain", "band_margin_db"],
        "cost_seconds": {"geometry": 10},
    })
    observations = [
        ("high-gain", .75, .77, "slot", 6), ("compromise", .60, .70, "probe", 4),
        ("best-match", .49, .85, "slot", 3), ("dominated", .53, .12, "probe", 5),
        ("low-gain", .10, .00, "slot", 2), ("bad-match", .89, .25, "probe", 3),
    ]
    rows = []
    for rid, x1, x2, feed, elements in observations:
        params = {"x1": x1, "x2": x2, "feed": feed, "elements": elements}
        metrics = problem.metrics(params)
        rows.append({"run_id": rid, "case": case.case_id, "recipe_id": "fixture-v1",
                     "source": "MEASURED", "status": "COMPLETED", "params": params,
                     "metrics": metrics, "total_seconds": 9,
                     "s11_points": [[24, metrics["s11"]], [25, metrics["s11"]]]})
    result = study.plan(rows, budget_seconds=30, count=2, seed=17)
    assert result["trained_on"] == len(rows)
    assert set(result["nominal_front"]) == {"high-gain", "compromise", "best-match"}
    assert {r["run_id"] for r in result["assessments"] if not r["sampled_pass"]} == {
        "low-gain", "bad-match"}
    assert len(result["actions"]) == 2
    for action in result["actions"]:
        assert action["kind"] == "geometry"
        params = action["params"]
        assert params["feed"] in ("probe", "slot") and type(params["elements"]) is int
        assert space.validate(params) == params and all(params != r["params"] for r in rows)
    assert result["actions"][0]["params"] != result["actions"][1]["params"]
