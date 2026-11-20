"""Frequency sampling and measurement provenance must survive band acceptance."""
from __future__ import annotations

import json
import math
import tempfile
import unittest
from pathlib import Path

from antopt.band_acceptance import (BandAcceptanceSpec, assess_s11_band,
                                    assess_touchstone_band)


# Exact MA rows from the existing AstraBB delivered s1p, not synthetic GP output.
ASTRABB_S1P = """! Historical AstraBB 24-32 GHz export, HFSS 2026.1.0
# GHz S MA R 50
24 0.240330350182511 144.017994711626
25 0.158050379086706 121.539456008616
26 0.0956719984961837 94.9285877736215
27 0.0584340902789402 75.2488898858093
28 0.0465978544901372 -64.6364248075177
29 0.286276520565591 -161.821625451751
30 0.272658362760955 90.514697470262
31 0.178098885087101 -81.5065612574129
32 0.444057088907509 136.978784741986
"""


class BandAcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.spec = BandAcceptanceSpec((24, 32), max_gap_ghz=1)
        self.points = [(frequency, -15.0) for frequency in range(24, 33)]

    def assess(self, points=None, spec=None, **kwargs):
        return assess_s11_band(self.points if points is None else points,
                               self.spec if spec is None else spec,
                               source=kwargs.pop("source", "MEASURED"), **kwargs)

    def test_settings_reject_nonfinite_or_unsafe_values(self):
        settings = [{"band_ghz": (32, 24)}, {"band_ghz": (0, 32)},
                    {"band_ghz": (24, math.inf)}, {"band_ghz": "24"},
                    {"max_gap_ghz": 0}, {"max_gap_ghz": math.nan},
                    {"max_gap_ghz": math.inf}, {"max_gap_ghz": True},
                    {"threshold_db": 1}, {"threshold_db": math.nan},
                    {"guard_margin_db": -1}, {"guard_margin_db": math.inf}]
        for changes in settings:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                BandAcceptanceSpec(**{"band_ghz": (24, 32), "max_gap_ghz": 1, **changes})

    def test_sampling_gap_must_be_explicit(self):
        with self.assertRaises(TypeError):
            BandAcceptanceSpec((24, 32))

    def test_nine_passing_samples_never_prove_continuous_matching(self):
        result = self.assess()
        self.assertEqual(result["status"], "SAMPLED_PASS")
        self.assertEqual(result["discrete_evidence"]["valid_sample_count"], 9)
        self.assertTrue(result["discrete_evidence"]["complete_for_sampling_policy"])
        self.assertIsNone(result["continuous_band_pass"])
        self.assertEqual(result["continuous_band_status"], "UNPROVEN_FROM_SAMPLES")
        self.assertEqual(result["passing_sample_ranges"][0]["span_ghz"], 8)
        self.assertEqual(result["passing_sample_ranges"][0]["evidence"], "DISCRETE_SAMPLES")

    def test_sparse_passing_endpoints_request_the_missing_band(self):
        result = self.assess([(24, -15), (32, -15)])
        self.assertEqual(result["status"], "INSUFFICIENT_EVIDENCE")
        self.assertFalse(result["usable_for_acceptance"])
        self.assertEqual(result["rescan_bands"], [
            {"low_ghz": 24.0, "high_ghz": 32.0, "reasons": ["SAMPLING_GAP"]}])
        self.assertEqual(len(result["passing_sample_ranges"]), 2)

    def test_missing_endpoints_are_not_inferred_from_outside_samples(self):
        result = self.assess([(23, -15), (25, -15), (31, -15), (33, -15)],
                             preview_frequencies_ghz=[24, 32])
        self.assertEqual(result["status"], "INSUFFICIENT_EVIDENCE")
        self.assertEqual(result["discrete_evidence"]["endpoints_present"], {"low": False, "high": False})
        self.assertEqual(result["discrete_evidence"]["out_of_band_sample_count"], 2)
        self.assertIn("MISSING_LOW_ENDPOINT", result["rescan_bands"][0]["reasons"])
        self.assertIn("MISSING_HIGH_ENDPOINT", result["rescan_bands"][-1]["reasons"])
        self.assertEqual(result["interpolation_preview"]["points"][0]["s11_db"], -15)

    def test_invalid_interior_value_breaks_the_passing_range(self):
        points = [(24, -15), (25, math.nan), (26, -15), (32, -15)]
        result = self.assess(points, BandAcceptanceSpec((24, 32), max_gap_ghz=8),
                             preview_frequencies_ghz=[25, 25.5])
        self.assertEqual(result["status"], "INSUFFICIENT_EVIDENCE")
        self.assertEqual(len(result["passing_sample_ranges"]), 2)
        self.assertEqual(result["passing_sample_ranges"][0]["high_ghz"], 24)
        self.assertTrue(all(p["s11_db"] is None for p in result["interpolation_preview"]["points"]))
        self.assertTrue(any("INVALID_SAMPLE" in b["reasons"] for b in result["rescan_bands"]))
        json.dumps(result, allow_nan=False)

    def test_unlocatable_bad_row_prevents_complete_sampled_acceptance(self):
        result = self.assess([*self.points, (math.nan, -12)])
        self.assertEqual(result["status"], "INSUFFICIENT_EVIDENCE")
        self.assertIn("UNLOCATED_INVALID_ROW", result["rescan_bands"][0]["reasons"])
        json.dumps(result, allow_nan=False)

    def test_unsorted_duplicate_frequency_uses_the_worst_value(self):
        points = [*reversed(self.points), (28, -8), (28, -30)]
        result = self.assess(points)
        self.assertEqual(result, self.assess(sorted(points)))
        self.assertEqual(result["status"], "FAIL")
        self.assertEqual(result["worst_point"]["frequency_ghz"], 28)
        self.assertEqual(result["worst_point"]["margin_db"], -2)
        self.assertEqual(result["discrete_evidence"]["duplicate_frequency_count"], 2)
        self.assertEqual(result["discrete_evidence"]["conflicting_duplicate_frequency_count"], 1)
        self.assertFalse(result["continuous_band_pass"])

    def test_bad_duplicate_cannot_be_hidden_by_a_passing_value(self):
        result = self.assess([*self.points, (28, math.nan)])
        self.assertEqual(result["status"], "INSUFFICIENT_EVIDENCE")
        self.assertEqual(result["discrete_evidence"]["usable_sample_count"], 8)

    def test_guard_margin_failure_does_not_invent_a_nominal_failure(self):
        points = [*self.points, (29, -10.4)]
        result = self.assess(points, BandAcceptanceSpec((24, 32), 1, guard_margin_db=0.5))
        self.assertEqual(result["status"], "FAIL")
        self.assertTrue(result["discrete_evidence"]["nominal_samples_pass"])
        self.assertFalse(result["discrete_evidence"]["guarded_samples_pass"])
        self.assertAlmostEqual(result["worst_point"]["guarded_margin_db"], -0.1)
        self.assertEqual(result["risk_points"][0]["frequency_ghz"], 29)
        self.assertIsNone(result["continuous_band_pass"])

    def test_actual_failure_can_disprove_matching_even_when_other_data_are_missing(self):
        result = self.assess([(28, -7)])
        self.assertEqual(result["status"], "FAIL")
        self.assertFalse(result["discrete_evidence"]["complete_for_sampling_policy"])
        self.assertFalse(result["continuous_band_pass"])

    def test_prediction_or_unknown_source_never_becomes_measurement(self):
        for source, expected in [("PREDICTED", "PREDICTED_ONLY"),
                                 ("UNKNOWN", "INSUFFICIENT_EVIDENCE")]:
            with self.subTest(source=source):
                result = self.assess([(28, -7)], source=source)
                self.assertEqual(result["status"], expected)
                self.assertFalse(result["usable_for_acceptance"])
                self.assertFalse(result["discrete_evidence"]["trusted"])
                self.assertIsNone(result["continuous_band_pass"])

    def test_missing_or_mismatched_contract_cannot_validate_the_current_run(self):
        for contract in (None, {"identity": "old"}):
            with self.subTest(contract=contract):
                result = self.assess([(28, -7)], learning_contract=contract,
                                     expected_contract={"identity": "current"})
                self.assertEqual(result["status"], "INCOMPATIBLE_EVIDENCE")
                self.assertFalse(result["usable_for_acceptance"])
                self.assertIsNone(result["continuous_band_pass"])

    def test_matching_contract_is_explicit_and_missing_expected_identity_is_invalid(self):
        result = self.assess(learning_contract={"identity": "current"},
                             expected_contract={"identity": "current"})
        self.assertTrue(result["contract_compatible"])
        self.assertEqual(result["status"], "SAMPLED_PASS")
        with self.assertRaises(ValueError):
            self.assess(expected_contract={})

    def test_preview_is_separate_no_extrapolation_and_cannot_fill_a_sampling_gap(self):
        result = self.assess([(24, -18), (32, -12)],
                             preview_frequencies_ghz=[23, 24, 28, 32, 33])
        self.assertEqual(result["status"], "INSUFFICIENT_EVIDENCE")
        preview = result["interpolation_preview"]
        self.assertEqual(preview["source"], "PREDICTED")
        self.assertFalse(preview["usable_for_acceptance"])
        self.assertEqual(preview["points"][2]["s11_db"], -15)
        self.assertTrue(preview["points"][2]["exceeds_max_gap"])
        self.assertIsNone(preview["points"][0]["s11_db"])
        self.assertIsNone(preview["points"][-1]["s11_db"])
        self.assertEqual(result["discrete_evidence"]["valid_sample_count"], 2)

    def test_dict_points_and_small_endpoint_roundoff_are_supported(self):
        points = [{"freq_ghz": 24 + 1e-10, "s11_db": -15},
                  {"frequency_ghz": 32 - 1e-10, "s11_db": -15}]
        result = self.assess(points, BandAcceptanceSpec((24, 32), 8))
        self.assertEqual(result["status"], "SAMPLED_PASS")
        self.assertTrue(all(result["discrete_evidence"]["endpoints_present"].values()))

    def test_empty_curve_is_insufficient_and_serializable(self):
        result = self.assess([])
        self.assertEqual(result["status"], "INSUFFICIENT_EVIDENCE")
        self.assertIsNone(result["worst_point"])
        self.assertEqual(result["passing_sample_ranges"], [])
        self.assertIn("NO_VALID_SAMPLES", result["rescan_bands"][0]["reasons"])
        json.dumps(result, allow_nan=False)

    def test_extreme_values_cannot_overflow_serialized_margins_or_preview(self):
        result = self.assess([(24, 1e308), (32, -1e308)], preview_frequencies_ghz=[28])
        self.assertEqual(result["interpolation_preview"]["points"][0]["s11_db"], 0)
        json.dumps(result, allow_nan=False)
        overflow = self.assess([(24, 1e308), (32, -1e308)],
                               BandAcceptanceSpec((24, 32), 8, threshold_db=-1e308))
        self.assertEqual(overflow["status"], "INSUFFICIENT_EVIDENCE")
        json.dumps(overflow, allow_nan=False)


class TouchstoneBandTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / "result.s1p"

    def check_text(self, text, spec=None):
        self.path.write_text(text, encoding="utf-8")
        return assess_touchstone_band(self.path, spec or BandAcceptanceSpec((24, 32), 1), source="MEASURED")

    def test_shared_parser_converts_ma_ri_db_and_frequency_units(self):
        for fmt, value in [("DB", "-20 0"), ("MA", "0.1 0"), ("RI", "0.1 0")]:
            with self.subTest(fmt=fmt):
                result = self.check_text("# MHz S %s R 50\n24000 %s\n32000 %s\n" % (fmt, value, value),
                                         BandAcceptanceSpec((24, 32), 8))
                self.assertEqual(result["status"], "SAMPLED_PASS")
                self.assertAlmostEqual(result["worst_point"]["s11_db"], -20)
                self.assertEqual(result["artifact"]["parsed_rows"], 2)
                self.assertEqual(len(result["artifact"]["sha256"]), 64)

    def test_parser_dropped_row_is_not_silent_passing_evidence(self):
        result = self.check_text("# GHz S DB R 50\n24 -15 0\n28 bad 0\n32 -15 0\n",
                                 BandAcceptanceSpec((24, 32), 8))
        self.assertEqual(result["status"], "INSUFFICIENT_EVIDENCE")
        self.assertEqual(result["artifact"]["omitted_rows"], 1)
        self.assertEqual(len(result["passing_sample_ranges"]), 2)
        self.assertTrue(any("TOUCHSTONE_ROWS_OMITTED" in b["reasons"] for b in result["rescan_bands"]))

    def test_suspect_extra_columns_are_not_silently_accepted(self):
        result = self.check_text("# GHz S DB R 50\n24 -15 0 extra\n32 -15 0\n",
                                 BandAcceptanceSpec((24, 32), 8))
        self.assertEqual(result["status"], "INSUFFICIENT_EVIDENCE")
        self.assertFalse(result["usable_for_acceptance"])

    def test_non_s_parameters_and_non_one_port_files_are_rejected(self):
        with self.assertRaises(ValueError):
            self.check_text("# GHz Z RI R 50\n24 50 0\n32 50 0\n")
        with self.assertRaises(ValueError):
            assess_touchstone_band(self.path.with_suffix(".s2p"), BandAcceptanceSpec((24, 32), 1), source="MEASURED")

    def test_invalid_magnitude_or_unrecognized_header_cannot_look_matched(self):
        result = self.check_text("# GHz S MA R 50\n24 -0.1 0\n32 0.1 0\n",
                                 BandAcceptanceSpec((24, 32), 8))
        self.assertEqual(result["status"], "INSUFFICIENT_EVIDENCE")
        with self.assertRaises(ValueError):
            self.check_text("# THz S DB R 50\n24 -15 0\n32 -15 0\n")

    def test_historical_astrabb_full_band_failure_is_not_hidden_by_its_good_subband(self):
        result = self.check_text(ASTRABB_S1P)
        self.assertEqual(result["status"], "FAIL")
        self.assertEqual(result["discrete_evidence"]["valid_sample_count"], 9)
        self.assertEqual(result["worst_point"]["frequency_ghz"], 32)
        self.assertAlmostEqual(result["worst_point"]["s11_db"], -7.051223849793403)
        self.assertAlmostEqual(result["worst_point"]["margin_db"], -2.948776150206597)
        self.assertEqual((result["passing_sample_ranges"][0]["low_ghz"],
                          result["passing_sample_ranges"][0]["high_ghz"]), (24, 31))
        self.assertFalse(result["continuous_band_pass"])

    def test_historical_subband_still_needs_dense_validation_and_has_little_margin(self):
        result = self.check_text(ASTRABB_S1P, BandAcceptanceSpec((24, 31), 1))
        self.assertEqual(result["status"], "SAMPLED_PASS")
        self.assertEqual(result["worst_point"]["frequency_ghz"], 29)
        self.assertAlmostEqual(result["worst_point"]["margin_db"], 0.864285397720451)
        self.assertIsNone(result["continuous_band_pass"])
        dense = self.check_text(ASTRABB_S1P, BandAcceptanceSpec((24, 31), 0.25))
        self.assertEqual(dense["status"], "INSUFFICIENT_EVIDENCE")
        self.assertEqual(len(dense["rescan_bands"]), 7)
        guarded = self.check_text(ASTRABB_S1P, BandAcceptanceSpec((24, 31), 1, guard_margin_db=1))
        self.assertEqual(guarded["status"], "FAIL")
        self.assertEqual(guarded["risk_points"][0]["frequency_ghz"], 29)
        self.assertIsNone(guarded["continuous_band_pass"])


if __name__ == "__main__":
    unittest.main()
