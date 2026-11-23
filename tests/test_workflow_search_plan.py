"""Normal numerical workflow paths using the upstream offline antenna fixture."""
import json
from pathlib import Path

import pytest

from aster.cases import lens2g4_10g
from aster.core import ParamSpace, ParamSpec
from aster.doe import plan_doe
from aster.executor import SyntheticExecutor
from aster.opt_workflow import OptimizationPlan, WorkflowError, WorkflowRunner
from aster.optimizer.nsga2 import run_nsga2
from aster.optimizer.nsga3 import run_nsga3
from aster.synthetic import SyntheticProblem, build_case
from aster.workflow_space import effective_case
from test_opt_workflow import _case, _config, _FakeBridge


def test_example_plan_applies_the_declared_lattice_and_preserves_registered_limits():
    raw = json.loads((Path(__file__).parents[1] / "examples" / "optimization_workflow_plan.json").read_bytes())
    original = lens2g4_10g.build_case()
    plan = OptimizationPlan.from_dict(raw).validate(original)
    compiled = effective_case(original, plan)
    assert [o.kind for o in compiled.objectives] == ["peak_gain", "s11"]
    assert compiled.constraints == original.constraints and compiled is not original
    for point in plan_doe(compiled.family.param_space, compiled.case_id, 10, seed=7).points:
        for name, value in point.items():
            spec = plan.parameter_space[name]
            assert spec["low"] <= value <= spec["high"]
            rank = (value - spec["low"]) / spec["step"]
            assert rank == pytest.approx(round(rank))


def test_plan_subsets_reorders_and_tightens_the_effective_case():
    original = build_case()
    spec = OptimizationPlan(case_id=original.case_id, parameter_space={"x1": {"low": .23, "high": .37, "step": .04}},
        objectives=[{"kind": "s11", "direction": "maximize"}],
        constraints=[{"kind": "s11", "target": -13}])
    case = effective_case(original, spec)
    assert [(o.kind,o.direction) for o in case.objectives] == [("s11","maximize")]
    assert [(c.kind,c.target) for c in case.constraints] == [("s11",-13),("peak_gain",5)]
    points = plan_doe(case.family.param_space, case.case_id, 11, seed=4).points
    assert points and all(p["x1"] in (.23,.27,.31,.35) for p in points)
    assert original.family.param_space["x1"].low == 0 and original.constraints[0].target == -10
    with pytest.raises(ValueError, match="step grid"):
        case.family.param_space.validate({"x1": .3, "x2": .5})


@pytest.mark.parametrize("changes", [
    {"parameter_space": {"x1": {"low": -2}}}, {"parameter_space": {"x1": {"step": 0}}},
    {"constraints": [{"kind": "s11", "target": -5}]}, {"constraints": [{"kind": "s11", "operator": ">="}]},
    {"objectives": [{"kind": "peak_gain", "direction": "nonsense"}]},
    {"strategy": {"screening": "lhs", "optimizer": "grid"}}, {"budget": {"screening_candidates": False}},
])
def test_invalid_briefs_fail_before_execution(changes):
    case = build_case()
    with pytest.raises(WorkflowError):
        OptimizationPlan(case_id=case.case_id, **changes).validate(case)


@pytest.mark.parametrize("optimizer", [run_nsga2, run_nsga3])
def test_initial_screened_geometry_is_re_evaluated_within_budget(optimizer):
    problem = SyntheticProblem()
    case = build_case(problem)
    class Recorder(SyntheticExecutor):
        def evaluate(self, candidate):
            calls.append(dict(candidate.params))
            return super().evaluate(candidate)
    calls = []
    seed = {"x1": .6, "x2": .7}
    result = optimizer(case, Recorder(case, problem), seed=9, pop_size=6, generations=2,
                       budget=5, initial_params=[seed])
    assert calls[0] == seed and result["evaluations"] == len(calls) == 5


def _runner(tmp_path, **kw):
    project = tmp_path / "base.aedt"
    project.write_text("{}", encoding="utf-8")
    case = _case(project)
    runner = WorkflowRunner(case, _config(tmp_path, project), OptimizationPlan(case_id=case.case_id, **kw),
                            bridge=_FakeBridge())
    runner.record_baseline("baseline-run", {"project": str(project)})
    return runner


@pytest.mark.parametrize("budget,points,expected", [(0, None, "zero"), (1, [{"x":2},{"x":3}], "exceed")])
def test_no_screening_submission_on_zero_or_excess_candidates(tmp_path, budget, points, expected):
    runner = _runner(tmp_path, budget={"screening_candidates": budget})
    with pytest.raises(WorkflowError, match=expected):
        runner.run_screening(candidates=points)
    assert not (Path(runner.config.campaign_dir) / "screening").exists()


def test_screened_points_connect_to_search_and_zero_budget_has_no_calls(tmp_path):
    runner = _runner(tmp_path, budget={"screening_candidates": 1, "optimizer_evaluations": 0})
    screened = runner.run_screening(candidates=[{"x": 4}])
    assert screened["search_seeds"] == [{"x": 4.}]
    with pytest.raises(WorkflowError, match="zero"):
        runner.run_search()
    assert not (Path(runner.config.campaign_dir) / "search").exists()


def test_workflow_passes_screened_geometry_and_actual_bounds_to_search(tmp_path):
    runner = _runner(tmp_path, budget={"screening_candidates": 1, "optimizer_evaluations": 3},
                     parameter_space={"x": {"low": 2.25, "high": 3.1, "step": .2}})
    screened = runner.run_screening(candidates=[{"x": 2.65}])
    search = runner.run_search(pop_size=4, generations=2, seed=5)
    entries = json.loads(Path(search["archive"]).read_bytes())["entries"]
    assert entries[0]["params"] == screened["search_seeds"][0] == {"x": 2.65}
    assert 0 < search["evaluations"] == len(entries) <= 3
    assert all(e["params"]["x"] in (2.25, 2.45, 2.65, 2.85, 3.05) for e in entries)


def test_absent_optimizer_has_no_in_flight_receipt(tmp_path, monkeypatch):
    runner = _runner(tmp_path, budget={"screening_candidates": 1, "optimizer_evaluations": 2})
    runner.run_screening(candidates=[{"x": 4}])
    def missing_backend(name):
        raise ModuleNotFoundError(name)
    monkeypatch.setattr("aster.opt_workflow.import_module", missing_backend)
    with pytest.raises(WorkflowError, match="pymoo"):
        runner.run_search()
    saved = json.loads(runner.state_path.read_bytes())
    assert not saved.get("in_flight") and "search" not in saved["artifacts"]


def test_mixed_registered_space_stays_canonical_across_grid_lhs_and_nsga(tmp_path):
    problem = SyntheticProblem()
    case = build_case(problem)
    case.family.param_space = ParamSpace([*case.family.param_space,
        ParamSpec("feed", "", 0, 1, "probe", kind="choice", choices=("probe", "edge", "slot")),
        ParamSpec("elements", "", 2, 10, 4, kind="int")])
    plan = OptimizationPlan(case_id=case.case_id, parameter_space={
        "feed": {"choices": ["edge", "slot"]}, "elements": {"low": 3, "high": 8, "step": 2},
        "x1": {"low": .23, "high": .43, "step": .04}})
    project = tmp_path / "antenna.aedt"
    project.write_bytes(b"offline fixture")
    runner = WorkflowRunner(case, _config(tmp_path, project), plan, bridge=_FakeBridge())
    compiled = runner.case
    grid = runner._grid_points(max_candidates=18)
    assert {p["x1"] for p in grid} >= {.23, .43}
    assert {p["feed"] for p in grid} == {"edge", "slot"}
    lhs = plan_doe(compiled.family.param_space, case.case_id, 12, seed=3)
    result = run_nsga2(compiled, SyntheticExecutor(compiled, problem), pop_size=6, generations=3,
                       budget=10, seed=3, initial_params=lhs.points[:1])
    points = [*grid, *lhs.points, *(entry.params for entry in result["entries"])]
    assert result["entries"][0].params == lhs.points[0]
    assert set(p["feed"] for p in points) == {"edge", "slot"}
    assert {p["elements"] for p in points} == {3,5,7}
    assert all(type(p["elements"]) is int and compiled.family.param_space.validate(p) == p for p in points)
