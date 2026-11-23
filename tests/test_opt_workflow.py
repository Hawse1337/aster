# -*- coding: utf-8 -*-
"""Tests for the standard optimisation workflow contract and stage gates."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from aster.approved_builder import ApprovedBuilderConfig
from aster.core import CaseSpec, Constraint, FamilySpec, Objective, ParamSpace, ParamSpec
from aster.opt_workflow import (
    OptimizationPlan,
    WorkflowGateError,
    WorkflowRunner,
    load_plan,
    save_plan,
)


def _case(project: Path) -> CaseSpec:
    space = ParamSpace([ParamSpec(name="x", unit="mm", low=0, high=10, default=5)])
    return CaseSpec(
        case_id="workflow-test",
        project="workflow-test",
        design_type="test",
        frequency_ghz=(5.0, 8.0),
        family=FamilySpec(
            family_id="workflow-family",
            antenna_type="test",
            param_space=space,
            builder="test",
        ),
        constraints=[
            Constraint(
                id="s11", kind="s11", operator="<=", target=-10.0,
                unit="dB", band_ghz=(5.0, 8.0), violation_scale=1.0,
            )
        ],
        objectives=[Objective(id="o", kind="s11", direction="minimize")],
        evaluation={"frequencies_ghz": [5.0, 6.0, 7.0, 8.0]},
        project_path=str(project),
    )


def _config(tmp_path: Path, project: Path) -> ApprovedBuilderConfig:
    return ApprovedBuilderConfig(
        campaign_id="workflow-campaign",
        campaign_dir=str(tmp_path / "campaign"),
        builder_id="demo",
        project_path=str(project),
        outdir_root=str(tmp_path / "runs"),
        port=12345,
        design="D",
    )


class _FakeBridge:
    def run_approved_builder(self, **kwargs):
        outdir = Path(kwargs["outdir"])
        outdir.mkdir(parents=True, exist_ok=True)
        touchstone = outdir / "result.s1p"
        touchstone.write_text(
            "# GHZ S DB R 50\n" + "\n".join("%s -15 0" % f for f in (5.0, 6.0, 7.0, 8.0)),
            encoding="utf-8",
        )
        return {"artifacts": [str(touchstone)], "run_id": "r1"}


def test_plan_round_trip_and_case_validation(tmp_path: Path):
    project = tmp_path / "base.aedt"
    project.write_text("{}", encoding="utf-8")
    case = _case(project)
    plan = OptimizationPlan(
        case_id=case.case_id,
        route="structural_search",
        parameter_space={"x": {"low": 0, "high": 10, "step": 5}},
        objectives=[{"kind": "s11", "direction": "minimize"}],
    )
    path = save_plan(tmp_path / "plan.json", plan)
    loaded = load_plan(path, case=case)
    assert loaded.case_id == case.case_id
    bad = OptimizationPlan(case_id=case.case_id, parameter_space={"wrong": {}})
    with pytest.raises(Exception, match="parameter_space"):
        bad.validate(case)


def test_screening_requires_baseline_and_records_gate(tmp_path: Path):
    project = tmp_path / "base.aedt"
    project.write_text("{}", encoding="utf-8")
    case = _case(project)
    plan = OptimizationPlan(
        case_id=case.case_id,
        parameter_space={"x": {"low": 0, "high": 10, "step": 5}},
        objectives=[{"kind": "s11", "direction": "minimize"}],
    )
    runner = WorkflowRunner(case, _config(tmp_path, project), plan, bridge=_FakeBridge())
    with pytest.raises(WorkflowGateError):
        runner.run_screening(candidates=[{"x": 5.0}])
    runner.record_baseline("baseline-run", {"project": str(project)})
    artifact = runner.run_screening(candidates=[{"x": 5.0}])
    assert Path(artifact["manifest"]).is_file()
    state = json.loads(Path(runner.state_path).read_text(encoding="utf-8"))
    assert state["stage"] == "screening"
    gate = runner.gate()
    assert gate["checks"]["baseline"] is True
    assert gate["checks"]["screening"] is True
    assert "search" in state["plan"]["required_artifacts"]


def test_grid_screening_produces_deterministic_points(tmp_path: Path):
    project = tmp_path / "base.aedt"
    project.write_text("{}", encoding="utf-8")
    case = _case(project)
    plan = OptimizationPlan(
        case_id=case.case_id,
        strategy={"screening": "grid", "optimizer": "none", "verification": "full_model"},
        parameter_space={"x": {"low": 0, "high": 10, "step": 5}},
        objectives=[{"kind": "s11", "direction": "minimize"}],
    )
    runner = WorkflowRunner(case, _config(tmp_path, project), plan, bridge=_FakeBridge())
    runner.record_baseline("baseline-run")
    artifact = runner.run_screening()
    assert [point["x"] for point in artifact["candidates"]] == [0.0, 5.0, 10.0]
