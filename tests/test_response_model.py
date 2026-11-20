"""Response previews retain frequency/provenance and never become measured evidence."""
import unittest
import warnings

import numpy as np

from aster.response_model import S11ResponseModel


CONTRACT = {"identity": "same-model-and-measurement"}


def rows():
    return [{"run_id": str(i), "status": "COMPLETED", "created_utc": str(i).zfill(2),
             "params": {"radius": 2.0 + i / 9.0}, "learning_contract": CONTRACT,
             "s11_points": [[24.0, -12 - i / 9], [28.0, -18 - i / 9], [32.0, -11 - i / 9]]}
            for i in range(10)]


class ResponseModelTests(unittest.TestCase):
    def model(self, records, **kwargs):
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            return S11ResponseModel(["radius"], [(2, 3)], CONTRACT, **kwargs).fit(records)

    def test_learns_response_in_physical_units_without_changing_evidence(self):
        data = rows()
        model = self.model(data)
        prediction = model.predict({"radius": 2.45}, band_ghz=(24, 32))
        np.testing.assert_allclose(prediction["mean_db"], [-12.45, -18.45, -11.45], atol=0.2)
        self.assertEqual(prediction["frequency_ghz"], [24, 28, 32])
        self.assertEqual(prediction["source"], "PREDICTED")
        self.assertEqual(data[0]["s11_points"][0], [24, -12])
        self.assertGreaterEqual(prediction["diagnostics"]["holdout_samples"], 2)

    def test_repeated_designs_do_not_supply_holdout_support(self):
        data = rows()[:2] * 8
        model = self.model(data)
        self.assertEqual(model.diagnostics["samples"], 2)
        self.assertFalse(model.diagnostics["usable_for_selection"])
        with self.assertRaisesRegex(ValueError, "insufficient"):
            model.predict({"radius": 2.45})

    def test_other_contract_predictions_and_changed_axes_are_excluded(self):
        data = rows()
        data[0]["learning_contract"] = {"identity": "different-frequency-or-model"}
        data[1]["source"] = "PREDICTED"
        data[2]["s11_points"] = [[24, -12], [27, -18], [32, -11]]
        model = self.model(data)
        self.assertEqual(model.diagnostics["samples"], 7)
        self.assertEqual(len(model.diagnostics["excluded"]), 3)

    def test_frequency_extrapolation_and_out_of_bounds_are_rejected(self):
        model = self.model(rows())
        with self.assertRaisesRegex(ValueError, "frequency"):
            model.predict({"radius": 2.45}, band_ghz=(24, 33))
        with self.assertRaisesRegex(ValueError, "bounds"):
            model.predict({"radius": 4})

    def test_quality_failure_is_visible_even_if_model_can_draw_a_curve(self):
        data = rows()
        for i, row in enumerate(data):
            row["s11_points"][0][1] = -4 if i % 2 else -24
        model = self.model(data, max_mae_db=0.01)
        prediction = model.predict({"radius": 2.45})
        self.assertFalse(prediction["usable_for_selection"])
        self.assertGreater(prediction["diagnostics"]["holdout_mae_db"], 0.01)

    def test_marginal_ranges_do_not_prove_multidimensional_support(self):
        data = rows()
        for row in data:
            row["params"]["length"] = row["params"]["radius"]
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            model = S11ResponseModel(["radius", "length"], [(2, 3), (2, 3)], CONTRACT).fit(data)
        prediction = model.predict({"radius": 2.2, "length": 2.8})
        self.assertTrue(prediction["within_measured_parameter_coverage"])
        self.assertFalse(prediction["within_measured_subspace"])
        self.assertFalse(prediction["usable_for_selection"])


if __name__ == "__main__":
    unittest.main()
