# -*- coding: utf-8 -*-
"""Offline tests for the generic optimisation layer (aster core + module A base)."""
from __future__ import annotations

import math
import os
import unittest

from aster.core import (
    CaseSpec,
    Constraint,
    FamilySpec,
    Objective,
    ParamSpace,
    ParamSpec,
    latin_hypercube,
)
from aster.optimize import (
    Evaluation,
    ParetoArchive,
    compare,
    constraint_violation,
    dominates,
    evaluate_constraints,
    fast_non_dominated_sort,
    hypervolume_2d,
)
from aster.synthetic import SyntheticProblem, build_case, compare_strategies, evaluate_candidate

os.environ.setdefault("AEDT_GUARD_NO_DAEMON", "1")


def _space() -> ParamSpace:
    return ParamSpace(
        [
            ParamSpec(name="a", unit="mm", low=0.0, high=10.0, default=5.0),
            ParamSpec(name="b", unit="", low=0, high=3, default=1, kind="int"),
            ParamSpec(name="c", unit="", low=0, high=1, default=1, kind="choice", choices=(0, 1)),
        ]
    )


class ParamSpaceTests(unittest.TestCase):
    def test_validation_and_ranges(self):
        space = _space()
        clean = space.validate({"a": 2.5, "b": 2, "c": 1})
        self.assertEqual(clean, {"a": 2.5, "b": 2, "c": 1})
        with self.assertRaises(Exception):
            space.validate({"a": 11.0, "b": 1, "c": 1})
        with self.assertRaises(Exception):
            space.validate({"a": 1.0, "b": 9, "c": 1})
        with self.assertRaises(Exception):
            space.validate({"a": 1.0, "b": 1})
        with self.assertRaises(Exception):
            space.validate({"a": 1.0, "b": 1, "c": 1, "d": 2})

    def test_normalise_roundtrip(self):
        space = _space()
        params = {"a": 7.31, "b": 2, "c": 0}
        vector = space.to_vector(params)
        self.assertTrue(all(0.0 <= v <= 1.0 for v in vector))
        back = space.from_vector(vector)
        self.assertAlmostEqual(back["a"], 7.3, places=6)
        self.assertEqual(back["b"], 2)
        self.assertEqual(back["c"], 0)

    def test_latin_hypercube_stratification(self):
        space = _space()
        samples = latin_hypercube(space, 8, seed=3)
        self.assertEqual(len(samples), 8)
        buckets = sorted(int(space.to_vector(s)[0] * 8) for s in samples)
        self.assertEqual(buckets, list(range(8)))


class SpecGenerationTests(unittest.TestCase):
    def test_case_to_spec_is_valid(self):
        case = build_case()
        spec = case.to_spec()
        self.assertEqual(spec["schema_version"], 1)
        kinds = {m["kind"] for m in spec["metrics"]}
        self.assertIn("s11", kinds)
        self.assertIn("peak_gain", kinds)
        self.assertEqual(len(spec["objectives"]), 2)

    def test_duplicate_constraint_ids_rejected(self):
        case = build_case()
        case.constraints = [case.constraints[0], case.constraints[0]]
        with self.assertRaises(Exception):
            case.validate()

    def test_candidate_hashing_is_stable_and_param_sensitive(self):
        case = build_case()
        first = case.make_candidate({"x1": 0.3, "x2": 0.6})
        second = case.make_candidate({"x1": 0.3, "x2": 0.6})
        other = case.make_candidate({"x1": 0.31, "x2": 0.6})
        self.assertEqual(first.candidate_id, second.candidate_id)
        self.assertNotEqual(first.candidate_id, other.candidate_id)


class ModuleATests(unittest.TestCase):
    def _eval(self, gain, s11, feasible=True, violation=0.0):
        return Evaluation(
            candidate_id=f"c{gain}-{s11}",
            family_id="synthetic_v1",
            params={},
            metrics={"peak_gain": gain, "s11": s11},
            feasible=feasible,
            total_violation=violation,
        )

    def test_constraint_violation_normalisation(self):
        case = build_case()
        s11_constraint = case.constraints[0]
        self.assertEqual(constraint_violation(-20.0, s11_constraint), 0.0)
        self.assertAlmostEqual(constraint_violation(-7.0, s11_constraint), 1.0)
        missing = constraint_violation(None, s11_constraint)
        self.assertGreater(missing, 100.0)  # never feasible without a measurement

    def test_feasibility_first_comparison(self):
        case = build_case()
        a = self._eval(6.0, -12.0, feasible=True)
        b = self._eval(9.0, -6.0, feasible=False, violation=1.3)
        self.assertEqual(compare(a, b, case.objectives), -1)
        self.assertEqual(compare(b, a, case.objectives), 1)

    def test_dominance_trade_off_keeps_both(self):
        case = build_case()
        a = self._eval(6.0, -12.0)
        b = self._eval(5.0, -20.0)
        self.assertFalse(dominates(a, b, case.objectives))
        self.assertFalse(dominates(b, a, case.objectives))
        fronts = fast_non_dominated_sort([a, b], case.objectives)
        self.assertEqual(len(fronts), 1)
        self.assertEqual(len(fronts[0]), 2)

    def test_dominated_candidate_is_layered(self):
        case = build_case()
        best = self._eval(7.0, -20.0)
        worse = self._eval(6.0, -12.0)
        fronts = fast_non_dominated_sort([best, worse], case.objectives)
        self.assertEqual([len(f) for f in fronts], [1, 1])
        self.assertIs(fronts[0][0], best)

    def test_infeasible_never_enters_front(self):
        case = build_case()
        good = self._eval(6.0, -12.0)
        bad = self._eval(9.0, -6.0, feasible=False, violation=1.0)
        archive = ParetoArchive(case.objectives)
        archive.update([good, bad])
        self.assertEqual([e.candidate_id for e in archive.feasible_front()], [good.candidate_id])
        self.assertEqual(archive.best_by("peak_gain").candidate_id, good.candidate_id)

    def test_hypervolume_monotonic_with_front_extension(self):
        case = build_case()
        a = self._eval(5.0, -12.0)
        b = self._eval(6.0, -16.0)
        reference = (0.0, -30.0)
        first = hypervolume_2d([a], case.objectives[0], case.objectives[1], reference)
        both = hypervolume_2d([a, b], case.objectives[0], case.objectives[1], reference)
        self.assertGreater(both, first)

    def test_effective_bandwidth_constraint_requires_all_kinds(self):
        case = build_case()
        feasible, violations, total = evaluate_constraints(
            {"s11": -12.0, "peak_gain": 6.0, "ripple": 1.0}, case
        )
        self.assertTrue(feasible)
        self.assertEqual(total, 0.0)
        feasible, violations, _ = evaluate_constraints({"s11": -12.0}, case)
        self.assertFalse(feasible)
        self.assertIn("peak_gain", violations)


class SyntheticBenchmarkTests(unittest.TestCase):
    def test_feasible_designs_appear_and_improve_with_budget(self):
        results = compare_strategies(budgets=(8, 32), seed=7)
        first, second = results["rows"]
        self.assertGreater(second["lhs_feasible"], first["lhs_feasible"])
        # more budget must never degrade the best feasible gain found so far
        self.assertGreaterEqual(second["lhs_best_gain"], first["lhs_best_gain"])

    def test_lhs_not_worse_than_random_at_small_budget(self):
        results = compare_strategies(budgets=(16,), seed=11)
        row = results["rows"][0]
        self.assertGreaterEqual(row["lhs_feasible"], row["random_feasible"])

    def test_constrained_candidate_rejected(self):
        case = build_case(SyntheticProblem())
        problem = SyntheticProblem()
        weak = evaluate_candidate(case, {"x1": 0.05, "x2": 0.5}, problem)
        self.assertFalse(weak.feasible)
        self.assertIn("peak_gain", weak.violations)


if __name__ == "__main__":
    unittest.main()





