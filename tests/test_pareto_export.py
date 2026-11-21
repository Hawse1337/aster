"""Measured evidence shared by front and progress exports."""
import json

from aster.cases import lens28_ref
from aster.pareto import archive_from_runs, export_pareto


def test_export_progress_and_front_share_measured_evidence(tmp_path):
    case = lens28_ref.build_case()
    base = {"case": case.case_id, "status": "COMPLETED",
            "metrics": {"s11_bandwidth": 3.0, "peak_gain": 6.5, "ripple": 1.0}}
    rows = [{**base, "run_id": "legacy"},  # Older measured rows omit source/feasible.
            {**base, "run_id": "pred", "source": "predicted"},
            {**base, "run_id": "flag", "is_prediction": True},
            {**base, "run_id": "wrong", "case": "different"},
            {**base, "run_id": "failed", "status": "FAILED"},
            {**base, "run_id": "missing", "metrics": {"peak_gain": float("nan")}},
            {**base, "run_id": "infeasible", "feasible": False},
            {**base, "run_id": "rejected", "constraint_pass": False},
            {**base, "run_id": "recorded", "violations": {"ripple": 0.5}},
            {**base, "run_id": "mapped", "constraint_pass": {"ripple": False}},
            {**base, "run_id": "band", "metric_checks": {"ripple": True}, "metrics":
             {**base["metrics"], "ripple": {"value": 1.0, "passed": 0}}}]
    expected_ids = ["legacy", "infeasible", "rejected", "recorded", "mapped", "band"]
    archive = archive_from_runs(case, rows)
    assert list(archive.entries) == expected_ids
    assert [e.run_id for e in archive.first_front()] == ["legacy"]
    assert archive.entries["recorded"].violations["ripple"] == 0.5
    path = tmp_path / "front.json"
    summary = export_pareto(case, rows, path, fmt="json")
    payload = json.loads(path.read_text(encoding="utf8"))
    assert summary["evaluated"] == payload["meta"]["runs"] == len(expected_ids)
    assert [r["evaluated"] for r in payload["progress"]] == expected_ids
    assert [r["run_id"] for r in payload["front"]] == ["legacy"]
