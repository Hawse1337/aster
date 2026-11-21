import json
import unittest

from aster.core import ParamSpace, ParamSpec
from aster.surrogate.feasibility import fit_feasibility


class LocalFeasibilityTests(unittest.TestCase):
    def setUp(self):
        self.space = ParamSpace([
            ParamSpec("x", "mm", 0, 20, 10), ParamSpec("y", "mm", 0, 10, 5),
            ParamSpec("feed", "", 0, 1, "A", kind="choice", choices=("A", "B")),
        ])
        self.points = [(self.point(i), i >= 10) for i in range(21)]

    def point(self, x, *, y=None, feed="A"):
        return {"x": x, "y": x/2 if y is None else y, "feed": feed}

    def test_classifies_only_along_validated_geometry_support(self):
        model = fit_feasibility(self.space, self.points)
        self.assertTrue(model.diagnostics["enabled"], model.diagnostics)
        self.assertLess(model.diagnostics["brier"], model.diagnostics["baseline_brier"])
        self.assertGreater(model.predict(self.point(15.25)), .5)
        self.assertLess(model.predict(self.point(4.5)), .5)
        self.assertIsNone(model.predict(self.point(10,y=6)))
        self.assertIsNone(model.predict(self.point(10,feed="B")))
        self.assertIsNone(model.predict(self.point(21)))
        self.assertEqual(model.diagnostics["false_pass"], 1)
        self.assertEqual(model.diagnostics["missed_pass"], 0)
        self.assertGreaterEqual(model.diagnostics["validated"], 11)
        json.dumps(model.diagnostics, allow_nan=False)

    def test_replicates_and_invalid_labels_do_not_inflate_validation(self):
        original = fit_feasibility(self.space, self.points)
        rows = self.points * 5 + [(self.point(10), None), ({"x": 12}, True)]
        model = fit_feasibility(self.space, rows)
        self.assertEqual(model.diagnostics["geometries"], len(self.points))
        self.assertEqual(model.diagnostics["ignored_rows"], 2)
        self.assertEqual(model.diagnostics["brier"], original.diagnostics["brier"])
        self.assertEqual(model.diagnostics["validated"], original.diagnostics["validated"])

    def test_conflicting_repeats_disable_their_stratum_only(self):
        points = self.points + [(self.point(10), False)]
        points += [(self.point(i,feed="B"), i>=10) for i in range(21)]
        model = fit_feasibility(self.space,points)
        self.assertIsNone(model.predict(self.point(12)))
        self.assertIsNotNone(model.predict(self.point(12,feed="B")))
        self.assertEqual(model.diagnostics["strata"][0]["conflicting_geometries"], 1)

    def test_insufficient_or_unskilful_samples_abstain(self):
        for rows in ([], self.points[:5], [(p,True) for p,_ in self.points],
                     [(p,bool(i%2)) for i,(p,_) in enumerate(self.points)]):
            model = fit_feasibility(self.space, rows)
            self.assertFalse(model.diagnostics["enabled"], model.diagnostics)
            self.assertIsNone(model.predict(self.point(10)))

    def test_void_between_clusters_and_extrapolation_abstain(self):
        xs = [x/10 for x in range(11)] + [19+x/10 for x in range(11)]
        rows = [(self.point(x),x>10) for x in xs]
        model = fit_feasibility(self.space, rows)
        self.assertTrue(model.diagnostics["enabled"])
        self.assertIsNone(model.predict(self.point(9)))
        self.assertIsNotNone(model.predict(self.point(19.55)))
