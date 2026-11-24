# -*- coding: utf-8 -*-
"""Tests for the optional guard-backed solve+export adapter (no AEDT)."""

from __future__ import annotations

from pathlib import Path

from aster.approved_builder import ApprovedBuilderConfig
from aster.core import CaseSpec, Constraint, FamilySpec, Objective, ParamSpace, ParamSpec
from aster.solve_adapter import GuardedSolveConfig, GuardedSolveExecutor


def _case(project: Path) -> CaseSpec:
    space = ParamSpace([ParamSpec(name="x", unit="mm", low=0, high=10, default=5)])
    return CaseSpec(
        case_id="solve-adapter-test",
        project="solve-adapter-test",
        design_type="test",
        frequency_ghz=(5.0, 8.0),
        family=FamilySpec(
            family_id="solve-family",
            antenna_type="test",
            param_space=space,
            builder="test",
        ),
        constraints=[Constraint(
            id="s11", kind="s11", operator="<=", target=-10.0,
            unit="dB", band_ghz=(5.0, 8.0), violation_scale=1.0,
        )],
        objectives=[Objective(id="s11_obj", kind="s11", direction="minimize")],
        evaluation={"frequencies_ghz": [5.0, 6.0, 7.0, 8.0]},
        project_path=str(project),
    )


class _Bridge:
    def __init__(self, project: Path, saved: Path):
        self.project = project
        self.saved = saved
        self.spec = None
        self.submitted = None

    def run_approved_builder(self, **kwargs):
        outdir = Path(kwargs["outdir"])
        outdir.mkdir(parents=True, exist_ok=True)
        touchstone = outdir / "result.s1p"
        touchstone.write_text(
            "# GHZ S DB R 50\n" + "\n".join("%s -15 0" % f for f in (5.0, 6.0, 7.0, 8.0)),
            encoding="utf-8",
        )
        return {
            "artifacts": [str(touchstone)],
            "run_id": "build-run",
            "result": {"saved": str(self.saved)},
        }

    def create_spec(self, campaign_dir, spec):
        self.spec = spec
        Path(campaign_dir).mkdir(parents=True, exist_ok=True)
        return {"ok": True}

    def submit(self, **kwargs):
        self.submitted = kwargs
        campaign = Path(kwargs["campaign_dir"])
        artifacts = campaign / "runs" / "solve-run" / "artifacts"
        artifacts.mkdir(parents=True, exist_ok=True)
        (artifacts / "result.s1p").write_text(
            "# GHZ S DB R 50\n" + "\n".join("%s -15 0" % f for f in (5.0, 6.0, 7.0, 8.0)),
            encoding="utf-8",
        )
        farfield = artifacts / "farfield"
        farfield.mkdir()
        (farfield / "E_phi0.csv").write_text("theta,gain\n0,5\n30,3\n", encoding="utf-8")
        return {"run_id": "solve-run"}

    def status(self, campaign_dir, run_id):
        campaign = Path(campaign_dir)
        return {
            "status": "COMPLETED",
            "artifact_dir": str(campaign / "runs" / run_id / "artifacts"),
        }


def test_solve_adapter_submits_and_returns_measured_evaluation(tmp_path: Path):
    project = tmp_path / "base.aedt"
    project.write_text("{}", encoding="utf-8")
    saved = tmp_path / "saved.aedt"
    saved.write_text("{}", encoding="utf-8")
    bridge = _Bridge(project, saved)
    config = ApprovedBuilderConfig(
        campaign_id="solve-campaign",
        campaign_dir=str(tmp_path / "campaign"),
        builder_id="p0_model",
        project_path=str(project),
        outdir_root=str(tmp_path / "runs"),
        port=12345,
        design="Trunc_10GHz",
    )
    solve = GuardedSolveConfig(
        design="Trunc_10GHz",
        fixed_params={"design": "Trunc_10GHz", "gap_mm": 0.384},
        frequencies_ghz=(5.0, 8.0),
        num_tasks=1,
        run_daemon=False,
        run_params={"symmetry": "none", "setup_name": "Setup1"},
    )
    executor = GuardedSolveExecutor(_case(project), config, solve, bridge=bridge)
    from types import SimpleNamespace

    evaluation = executor.evaluate(SimpleNamespace(candidate_id="c1", params={"x": 5.0}))
    assert evaluation.status == "COMPLETED"
    assert evaluation.run_id == "solve-run"
    assert evaluation.metrics["s11"] == -15.0
    assert evaluation.origin["guard_campaign"]
    assert bridge.submitted["num_tasks"] == 1
    assert bridge.spec["project"] == str(saved.resolve())


def test_solve_spec_carries_constraints_and_frequency_range(tmp_path: Path):
    project = tmp_path / "base.aedt"
    project.write_text("{}", encoding="utf-8")
    saved = tmp_path / "saved.aedt"
    saved.write_text("{}", encoding="utf-8")
    bridge = _Bridge(project, saved)
    config = ApprovedBuilderConfig(
        campaign_id="solve-campaign-2", campaign_dir=str(tmp_path / "campaign2"),
        builder_id="p0_model", project_path=str(project), outdir_root=str(tmp_path / "runs2"),
        port=12345, design="Trunc_10GHz",
    )
    executor = GuardedSolveExecutor(_case(project), config,
        GuardedSolveConfig(design="Trunc_10GHz", run_daemon=False), bridge=bridge)
    from types import SimpleNamespace
    executor.evaluate(SimpleNamespace(candidate_id="c2", params={"x": 5.0}))
    assert bridge.spec["frequency_ghz"] == [5.0, 8.0]
    assert bridge.spec["metrics"][0]["kind"] == "s11"
