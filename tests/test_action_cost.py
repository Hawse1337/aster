import pytest

from aster.action_cost import fit_action_scorer
from aster.core import ParamSpec, ParamSpace


SPACE = ParamSpace([ParamSpec("length", "mm", 1, 4, 2),
                    ParamSpec("material", "", 0, 1, "A", kind="choice", choices=("A", "B"))])
POINT = SPACE.default()


def row(i, status, seconds, **kw):
    return {"run_id": str(i), "params": POINT, "case": "example", "recipe_id": "r",
            "source": "MEASURED", "status": status, "total_seconds": seconds, **kw}


def fit(rows, **kw):
    return fit_action_scorer(rows, SPACE, case_id="example", recipe_id="r", **kw)


def test_exact_action_before_full_solve_and_no_cross_category():
    rows = [row(1, "COMPLETED", 100), row(2, "COMPLETED", 99, action_kind="coverage", action_seconds=5),
            row(3, "COMPLETED", 5, recipe_id="other"), row(4, "COMPLETED", 5, source="PREDICTED")]
    scorer = fit(rows)
    assert scorer.estimate("interaction", POINT)["estimated_seconds"] >= 100
    assert 5 <= scorer.estimate("coverage", POINT)["estimated_seconds"] < 7
    assert scorer.estimate("frequency", POINT)["estimated_seconds"] is None
    assert scorer.estimate("geometry", dict(POINT, material="B"))["risk_basis"] == "unknown"


def test_censoring_is_a_lower_bound_and_infrastructure_is_not_geometry():
    rows = [row(1, "TIMEOUT", 150, action_kind="geometry"),
            row(2, "FAILED", 3, error_code="LICENSE", action_kind="geometry")]
    scorer = fit(rows)
    info = scorer.estimate("geometry", POINT)
    assert info["estimated_seconds"] is None and info["cost"]["lower_bound_seconds"] == 150
    assert info["success_factor"] == 1 and info["risk_basis"] == "unknown"
    assert info["risk"]["censored_samples"] == info["risk"]["environment_samples"] == 1
    assert fit(rows, cost_seconds={"geometry": 60}).estimate("geometry", POINT)["estimated_seconds"] == 150
    rows.append(row(3, "FAILED", 4, error_code="E_GEOMETRY", action_kind="geometry"))
    assert fit(rows).estimate("geometry", POINT)["success_factor"] == pytest.approx(1/3)


def test_missing_cost_and_measurement_provenance():
    rows = [row(1, "COMPLETED", 100, source=""), row(2, "COMPLETED", 100, cache_hit=True),
            row(3, "COMPLETED", 10, learning_contract={"case_id":"other", "identity":"r"})]
    info = fit(rows).estimate("tolerance", POINT)
    assert info["cost_basis"] == info["risk_basis"] == "unknown"
    assert info["estimated_seconds"] is None and info["success_factor"] == 1
