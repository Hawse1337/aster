"""Pareto export and radiation efficiency (GPT module A closure)."""
from __future__ import annotations

import csv
import json
import tempfile
import unittest
from pathlib import Path

from aster.core import Objective
from aster.export import (front_rows, progress_rows, write_front_csv, write_front_json)
from aster.optimize import Evaluation, ParetoArchive
from aster.physics.radiation import (efficiency_from_db, efficiency_from_powers,
                                      radiation_efficiency_metric)


def entry(cid, bandwidth, gain, feasible=True):
    return Evaluation(candidate_id=cid, family_id="lens28_ref",
                      params={"ring_outer": 2.2, "tip_offset": 1.0},
                      metrics={"s11_bandwidth": bandwidth, "peak_gain": gain},
                      feasible=feasible, total_violation=0.0, run_id="run-" + cid)


class ParetoExportTests(unittest.TestCase):
    def setUp(self):
        self.archive = ParetoArchive([Objective(id="bw", kind="s11_bandwidth", direction="maximize"),
                                      Objective(id="gain", kind="peak_gain", direction="maximize")])
        self.archive.update([entry("a", 3.0, 6.4), entry("b", 2.0, 6.9),
                             entry("c", 1.0, 5.0), entry("d", 4.0, 4.0, feasible=False)])

    def test_front_rows_only_keep_feasible_non_dominated(self):
        rows = front_rows(self.archive)
        self.assertEqual([row["candidate_id"] for row in rows], ["a", "b"])
        self.assertEqual(rows[0]["params"]["ring_outer"], 2.2)
        self.assertIn("peak_gain", rows[0]["metrics"])

    def test_csv_is_flat_with_prefixed_columns(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_front_csv(Path(tmp) / "pareto.csv", front_rows(self.archive))
            header = path.read_text(encoding="utf-8").splitlines()[0]
            self.assertIn("candidate_id", header)
            self.assertIn("param:ring_outer", header)
            self.assertIn("metric:peak_gain", header)
            with path.open(encoding="utf-8") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[0]["metric:s11_bandwidth"], "3.0")

    def test_json_keeps_progress_and_meta(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_front_json(Path(tmp) / "pareto.json", front_rows(self.archive),
                                    progress=progress_rows([{"evaluations": 4},
                                                            {"evaluations": 8}]),
                                    meta={"case": "lens28_ref"})
            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(payload["front_size"], 2)
            self.assertEqual(payload["meta"]["case"], "lens28_ref")
            self.assertEqual([row["round"] for row in payload["progress"]], [1, 2])

    def test_empty_archive_exports_header_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            empty = ParetoArchive([])
            path = write_front_csv(Path(tmp) / "empty.csv", front_rows(empty))
            self.assertEqual(len(path.read_text(encoding="utf-8").strip().splitlines()), 1)


class ExecutorEnrichmentTests(unittest.TestCase):
    def test_executor_adds_the_metric_only_when_computable(self):
        from aster.executor import GuardExecutor

        enriched = GuardExecutor._with_radiation_efficiency({"directivity_db": 9.0,
                                                             "gain_db": 8.0})
        self.assertAlmostEqual(enriched["radiation_efficiency"], 10 ** -0.1)
        untouched = GuardExecutor._with_radiation_efficiency({"peak_gain": 6.4})
        self.assertNotIn("radiation_efficiency", untouched)
        given = GuardExecutor._with_radiation_efficiency({"radiation_efficiency": 0.5})
        self.assertAlmostEqual(given["radiation_efficiency"], 0.5)


class RadiationEfficiencyTests(unittest.TestCase):
    def test_power_ratio(self):
        self.assertAlmostEqual(efficiency_from_powers(0.85, 1.0), 0.85)
        self.assertAlmostEqual(efficiency_from_powers(1.2, 1.0), 1.0)  # clamped
        with self.assertRaises(ValueError):
            efficiency_from_powers(1.0, 0.0)
        with self.assertRaises(ValueError):
            efficiency_from_powers(-1.0, 1.0)

    def test_db_difference(self):
        self.assertAlmostEqual(efficiency_from_db(-1.0, 0.0), 10 ** -0.1)
        self.assertAlmostEqual(efficiency_from_db(0.0, 0.0), 1.0)

    def test_metric_prefers_existing_value(self):
        self.assertAlmostEqual(
            radiation_efficiency_metric({"radiation_efficiency": 0.7}), 0.7)

    def test_metric_from_powers_and_from_db(self):
        self.assertAlmostEqual(
            radiation_efficiency_metric({"radiated_power_w": 0.9, "accepted_power_w": 1.0}),
            0.9)
        value = radiation_efficiency_metric({"directivity_db": 9.0, "gain_db": 8.0})
        self.assertAlmostEqual(value, 10 ** -0.1)

    def test_metric_undoes_mismatch_for_realized_gain(self):
        # S11 = -10 dB -> 10% reflected -> 0.4576 dB mismatch loss
        value = radiation_efficiency_metric({"directivity_db": 9.0, "realized_gain_h": 7.54,
                                             "s11": -10.0})
        self.assertAlmostEqual(value, 10 ** -0.1, places=3)

    def test_metric_returns_none_without_directivity(self):
        self.assertIsNone(radiation_efficiency_metric({"peak_gain": 6.4}))
        self.assertIsNone(radiation_efficiency_metric({"directivity_db": 9.0}))


class EfficiencyConstraintTests(unittest.TestCase):
    """The efficiency kind must work as a constraint, not only as an objective."""

    def _case(self):
        from aster.core import CaseSpec, Constraint, Objective

        return CaseSpec(case_id="eff", project="p", design_type="d",
                        frequency_ghz=[28.0], family="f",
                        objectives=[Objective(id="gain", kind="peak_gain",
                                              direction="maximize")],
                        constraints=[Constraint(id="eff", kind="radiation_efficiency",
                                                operator=">=", target=0.6)])

    def test_a_passing_value_is_feasible(self):
        from aster.optimize import evaluate_constraints

        feasible, violations, total = evaluate_constraints(
            {"radiation_efficiency": 0.75}, self._case())
        self.assertTrue(feasible)
        self.assertEqual(violations, {})
        self.assertEqual(total, 0.0)

    def test_a_failing_value_is_not(self):
        from aster.optimize import evaluate_constraints

        feasible, violations, total = evaluate_constraints(
            {"radiation_efficiency": 0.45}, self._case())
        self.assertFalse(feasible)
        self.assertAlmostEqual(violations["eff"], 0.15, places=6)
        self.assertAlmostEqual(total, 0.15, places=6)

    def test_a_missing_metric_is_a_violation_not_a_pass(self):
        from aster.optimize import evaluate_constraints

        feasible, violations, total = evaluate_constraints({}, self._case())
        self.assertFalse(feasible)
        self.assertGreater(total, 1.0)


if __name__ == "__main__":
    unittest.main()
