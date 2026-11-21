import math
import unittest

from aster.pattern_acceptance import ASTRABB_RING_TARGET, assess_ring_pattern


def sphere(gain, *, theta_step=2, phi_step=5):
    theta, phi = list(range(0, 181, theta_step)), list(range(0, 361, phi_step))
    return {"theta_deg": theta, "phi_deg": phi,
            "gain_db": [[gain(t, p) for p in phi] for t in theta]}


class RingAcceptanceTests(unittest.TestCase):
    def test_isotropic_fraction_is_half_and_seam_is_counted_once(self):
        result = assess_ring_pattern(sphere(lambda t, p: 0.0))
        self.assertEqual(result["status"], "SAMPLED_METRICS")
        self.assertEqual(result["grid"]["phi_count"], 72)
        self.assertAlmostEqual(result["metrics"]["belt_power_fraction"], 0.5, places=10)
        self.assertEqual(result["metrics"]["peak_theta_range_deg"], [0, 180])

    def test_sin_squared_belt_matches_solid_angle_integral(self):
        def sin_squared(theta, phi):
            return 10 * math.log10(max(1e-20, math.sin(math.radians(theta)) ** 2))

        result = assess_ring_pattern(sphere(sin_squared))
        self.assertAlmostEqual(result["metrics"]["belt_power_fraction"], 0.6875, places=3)
        self.assertEqual(result["metrics"]["peak_theta_deg"], 90)

    def test_peak_gain_does_not_hide_axial_or_equatorial_failure(self):
        result = assess_ring_pattern(sphere(lambda t, p: 9 if t == 0 else -5),
                                     target=ASTRABB_RING_TARGET)
        self.assertEqual(result["status"], "FAIL")
        self.assertFalse(result["checks"]["horizon_peak_dbi"]["passed"])
        self.assertFalse(result["checks"]["horizon_to_axial_db"]["passed"])

    def test_no_pass_from_sparse_cuts_predicted_or_wrong_quantity(self):
        for grid, kwargs in (
            (None, {}), (sphere(lambda t,p: 0.0, theta_step=45), {}),
            (sphere(lambda t,p: 0.0), {"source": "PREDICTED"}),
            (sphere(lambda t,p: 0.0), {"quantity": "dB(DirectivityTotal)"}),
        ):
            self.assertEqual(assess_ring_pattern(grid, **kwargs)["status"], "INSUFFICIENT_EVIDENCE")

    def test_inconsistent_periodic_seam_is_rejected(self):
        result = assess_ring_pattern(sphere(lambda t,p: p/180))
        self.assertEqual(result["status"], "INSUFFICIENT_EVIDENCE")
        self.assertIn("seam", result["reason"])

class AngularCoverageTests(unittest.TestCase):
    def coverage(self, *args, **kwargs):
        from aster.pattern_acceptance import AngularCoverageSpec, assess_angular_coverage
        return assess_angular_coverage(args[0], AngularCoverageSpec(**kwargs))

    def snapshot(self, freq, theta, phi, values):
        return {"frequency_ghz": freq, "source": "MEASURED", "quantity": "dB(RealizedGainTotal)",
                "coordinate_system": "Global", "sphere": {"theta_deg": theta, "phi_deg": phi,
                "gain_db": [[values(t,p) for p in phi] for t in theta]}}

    def test_horizontal_ring_cross_frequency_margin(self):
        phis = list(range(0, 361, 10))
        rows = [self.snapshot(f, [90], phis, lambda t,p: 3.5 if f == 24 and p == 60 else 5)
                for f in (24, 25)]
        out = self.coverage(rows, frequencies_ghz=[24,25], theta_deg=(90,90), phi_deg=(0,360),
                            min_realized_gain_dbi=4, max_ripple_db=1)
        self.assertEqual(out["status"], "FAIL")
        self.assertAlmostEqual(out["metrics"]["coverage_margin_db"], -.5)
        self.assertEqual(out["metrics"]["limiting_frequency_ghz"], 24)
        self.assertEqual(out["rows"][0]["sample_count"], 36)
        self.assertEqual(out["rows"][0]["min_phi_deg"], 60)

    def test_wrapped_sector_uses_only_target_angles(self):
        phis = [0,10,20,30,90,330,340,350]
        snap = self.snapshot(10, [10,20,30,40], phis, lambda t,p: -20 if t==40 or p==90 else 6)
        result = self.coverage([snap], frequencies_ghz=[10], theta_deg=(10,30), phi_deg=(-30,30),
                               min_realized_gain_dbi=5, max_ripple_db=.1)
        self.assertEqual(result["status"], "SAMPLED_PASS")
        self.assertEqual(result["metrics"]["min_realized_gain_dbi"], 6)
        self.assertEqual(result["rows"][0]["sample_count"], 21)

    def test_missing_sparse_and_incompatible_quantities_do_not_rank(self):
        snap = self.snapshot(10, [50], [0,10,20], lambda t,p: 6)
        spec = dict(frequencies_ghz=[10], theta_deg=(50,50), phi_deg=(0,20), min_realized_gain_dbi=0)
        for rows, changes in (([],{}), ([snap],{"frequencies_ghz":[10,11]}),
                              ([snap],{"max_step_deg":5}), ([snap],{"phi_deg":(0,25)}),
                              ([{**snap,"quantity":"dB(GainTotal)"}],{}),
                              ([{**snap,"source":"PREDICTED"}],{}), ([{**snap,"normalized":True}],{}),
                              ([{**snap,"coordinate_system_recorded":False}],{}), ([snap,snap],{})):
            result = self.coverage(rows, **{**spec, **changes})
            self.assertEqual(result["status"], "INSUFFICIENT_EVIDENCE")
            self.assertEqual(result["metrics"], {})

    def test_measured_violation_and_missing_frequency_retains_failure(self):
        snap = self.snapshot(10,[0],[0],lambda t,p:1)
        result = self.coverage([snap], frequencies_ghz=[10,11], theta_deg=(0,0), phi_deg=(0,0),
                               min_realized_gain_dbi=2)
        self.assertEqual(result["status"], "FAIL")
        self.assertEqual(result["missing_frequencies_ghz"], [11])
        self.assertEqual(result["metrics"], {})

    def test_native_csv_binds_coordinate_metadata_and_units(self):
        import hashlib
        import json
        import tempfile
        from pathlib import Path
        from aster.farfield import load_coverage_snapshots
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/"gain.csv"
            path.write_text("Freq [MHz],Theta [rad],Phi [deg],dB(RealizedGainTotal) []\n"
                            f"10000,{math.pi/2},350,6\n10000,{math.pi/2},0,7\n")
            meta = path.with_name("gain.csv.meta.json")
            with self.assertRaisesRegex(ValueError, "metadata"):
                load_coverage_snapshots(path)
            def bind():
                meta.write_text(json.dumps({"source":"MEASURED","coordinate_system":"AntennaFrame",
                                "run_id":"trial42", "sha256":hashlib.sha256(path.read_bytes()).hexdigest()}))
            bind()
            (snapshot,) = load_coverage_snapshots(path)
            self.assertEqual(snapshot["frequency_ghz"], 10)
            self.assertEqual(snapshot["sphere"]["theta_deg"], [90])
            self.assertEqual(snapshot["coordinate_system"], "AntennaFrame")
            self.assertEqual(snapshot["run_id"], "trial42")
            path.write_text(path.read_text().replace("RealizedGainTotal","GainTotal"))
            with self.assertRaisesRegex(ValueError, "SHA-256"):
                load_coverage_snapshots(path)
            bind()
            with self.assertRaisesRegex(ValueError, "RealizedGainTotal"):
                load_coverage_snapshots(path)

    def test_json_export_rejects_holes_conflicts_and_wrong_quantity(self):
        import json
        import tempfile
        from pathlib import Path
        from aster.farfield import load_coverage_snapshots
        data = {"source":"MEASURED", "coordinate_system":"Global", "quantity":"dB(RealizedGainTotal)",
                "samples":[[10,50,350,4],[10,50,0,4]]}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/"ff.json"
            path.write_text(json.dumps(data))
            self.assertEqual(len(load_coverage_snapshots(path)), 1)
            for changes in ({"quantity":"dB(GainTotal)"},{"samples":data["samples"]+[[10,50,360,-1]]},
                            {"samples":data["samples"]+[[10,60,350,4]]}):
                path.write_text(json.dumps({**data, **changes}))
                with self.assertRaises(ValueError):
                    load_coverage_snapshots(path)

    def test_requires_common_identity_to_combine_files(self):
        base = self.snapshot(10,[0],[0],lambda t,p:3)
        rows = [{**base, "source_file":"a", "run_id":"study1"},
                {**base, "frequency_ghz":11, "source_file":"b"}]
        spec = dict(frequencies_ghz=[10,11], theta_deg=(0,0), phi_deg=(0,0), min_realized_gain_dbi=1)
        self.assertEqual(self.coverage(rows,**spec)["status"], "INSUFFICIENT_EVIDENCE")
        rows[1]["run_id"] = "study2"
        self.assertEqual(self.coverage(rows,**spec)["status"], "INSUFFICIENT_EVIDENCE")
        rows[1]["run_id"] = "study1"
        self.assertEqual(self.coverage(rows,**spec)["status"], "SAMPLED_PASS")
