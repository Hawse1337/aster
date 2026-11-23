# -*- coding: utf-8 -*-
"""Tests for the aster approved-builder adapter and completion gate (no AEDT)."""

from __future__ import annotations

import json
from pathlib import Path

from aster.approved_builder import ApprovedBuilderConfig, run_approved_campaign
from aster.core import CaseSpec, Constraint, FamilySpec, Objective, ParamSpace, ParamSpec
from aster.optimization_gate import campaign_gate


def _case(project: Path) -> CaseSpec:
    space = ParamSpace([ParamSpec(name="x", unit="mm", low=0, high=10, default=5)])
    return CaseSpec(
        case_id="adapter-test",
        project="adapter-test",
        design_type="test",
        frequency_ghz=(5.0, 8.0),
        family=FamilySpec(
            family_id="adapter-family",
            antenna_type="test",
            param_space=space,
            builder="test",
        ),
        constraints=[
            Constraint(
                id="s11",
                kind="s11",
                operator="<=",
                target=-10.0,
                unit="dB",
                band_ghz=(5.0, 8.0),
                violation_scale=1.0,
            )
        ],
        objectives=[Objective(id="s11_obj", kind="s11", direction="minimize")],
        evaluation={"frequencies_ghz": [5.0, 6.0, 7.0, 8.0]},
        project_path=str(project),
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
        return {"artifacts": [str(touchstone)], "run_id": "fake-run", "saved": None}


def test_approved_builder_campaign_passes_gate(tmp_path: Path):
    project = tmp_path / "base.aedt"
    project.write_text("{}", encoding="utf-8")
    config = ApprovedBuilderConfig(
        campaign_id="campaign-1",
        campaign_dir=str(tmp_path / "campaign"),
        builder_id="p0_model",
        project_path=str(project),
        outdir_root=str(tmp_path / "runs"),
        port=12345,
        design="Trunc_10GHz",
    )
    result = run_approved_campaign(
        _case(project), config, budget=1, candidates=[{"x": 5.0}], bridge=_FakeBridge()
    )
    assert result["gate"]["passed"] is True
    manifest = json.loads((tmp_path / "campaign" / "optimization_manifest.json").read_text(encoding="utf-8"))
    assert manifest["optimizer"] == "aster"
    assert manifest["runs"][0]["status"] == "COMPLETED"
    assert manifest["pareto"]
    assert manifest["acceptance"]["passed"] is True


def test_approved_builder_preserves_nested_saved_path(tmp_path: Path):
    project = tmp_path / "base.aedt"
    saved = tmp_path / "saved.aedt"
    project.write_text("{}", encoding="utf-8")
    saved.write_text("{}", encoding="utf-8")

    class _NestedBridge(_FakeBridge):
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
                "run_id": "fake-run",
                "result": {"saved": str(saved)},
            }

    config = ApprovedBuilderConfig(
        campaign_id="campaign-saved",
        campaign_dir=str(tmp_path / "campaign"),
        builder_id="p0_model",
        project_path=str(project),
        outdir_root=str(tmp_path / "runs"),
        port=12345,
        design="Trunc_10GHz",
    )
    from types import SimpleNamespace
    from aster.approved_builder import ApprovedBuilderExecutor

    evaluation = ApprovedBuilderExecutor(_case(project), config, bridge=_NestedBridge()).evaluate(
        SimpleNamespace(candidate_id="c1", params={"x": 5.0})
    )
    assert evaluation.origin["guard_result"]["saved"] == str(saved)


class _RecordingExecutor:
    def __init__(self):
        self.calls = []

    def evaluate(self, candidate):
        from aster.optimize import Evaluation

        self.calls.append(dict(candidate.params))
        return Evaluation(
            candidate_id=str(candidate.candidate_id),
            family_id="adapter-family",
            params=dict(candidate.params),
            metrics={"s11": -15.0},
            feasible=True,
            violations={},
            total_violation=0.0,
            run_id="injected-run",
            status="COMPLETED",
            origin={"injected": True},
        )


def test_run_campaign_uses_injected_executor(tmp_path: Path):
    project = tmp_path / "base.aedt"
    project.write_text("{}", encoding="utf-8")
    config = ApprovedBuilderConfig(
        campaign_id="campaign-injected",
        campaign_dir=str(tmp_path / "campaign"),
        builder_id="p0_model",
        project_path=str(project),
        outdir_root=str(tmp_path / "runs"),
        port=12345,
        design="Trunc_10GHz",
    )
    executor = _RecordingExecutor()
    result = run_approved_campaign(
        _case(project), config, budget=1, candidates=[{"x": 5.0}],
        bridge=_FakeBridge(), executor=executor,
    )
    assert executor.calls == [{"x": 5.0}]
    assert result["manifest"]["runs"][0]["run_id"] == "injected-run"


def test_gate_rejects_manifest_without_optimizer(tmp_path: Path):
    campaign = tmp_path / "campaign"
    campaign.mkdir()
    (campaign / "optimization_manifest.json").write_text(
        json.dumps({"optimizer": "manual", "source": "manual", "runs": []}), encoding="utf-8"
    )
    gate = campaign_gate(campaign)
    assert gate["passed"] is False
    assert "optimizer_is_aster" in gate["missing"]
