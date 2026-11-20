"""Synthetic field/CSV tests only; no AEDT export or physical validation."""
from __future__ import annotations

import contextlib
from dataclasses import replace
import io
import json
import math
from pathlib import Path
import tempfile
import unittest

import numpy as np

from antopt.aperture import ApertureField, diagnose_aperture, example_csv, main, read_aperture_csv


def make_field(ez=1.0, ep=0.0, er=0.0, *, nphi=12, z=None):
    phi = np.arange(nphi) * (2 * math.pi / nphi)
    z = np.array([-0.00125, 0.00125]) if z is None else np.asarray(z, dtype=float)
    shape = (len(z), nphi)
    ez, ep, er = (np.broadcast_to(np.asarray(x, dtype=complex), shape) for x in (ez, ep, er))
    e = np.stack((er * np.cos(phi) - ep * np.sin(phi),
                  er * np.sin(phi) + ep * np.cos(phi), ez), axis=-1)
    return ApertureField(28e9, 0.08, phi, z, e, "common", "exp(+jwt)", "SYNTHETIC")


class FieldDiagnosticsTests(unittest.TestCase):
    def test_uniform_axial_field_area_and_conservation(self):
        report = diagnose_aperture(make_field(ez=3 + 4j, ep=2, er=1))
        integral = report["squared_field_integrals_v2"]
        area = 2 * math.pi * 0.08 * 0.0025
        self.assertAlmostEqual(integral["e_z"], area * 25)
        self.assertAlmostEqual(integral["e_phi"], area * 4)
        self.assertAlmostEqual(integral["e_r"], area)
        self.assertAlmostEqual(integral["tangential"], integral["e_z"] + integral["e_phi"])
        self.assertAlmostEqual(integral["total_electric"], area * 30)
        self.assertAlmostEqual(report["ez_fraction_of_tangential_squared_field"], 25 / 29)
        self.assertEqual(report["azimuthal_coherence"]["e_z"]["within_z_first_order_coherence_magnitude"], 1)
        self.assertAlmostEqual(report["sector_symmetry"]["rotations"][0]["relative_complex_l2"], 0, places=12)

    def test_cylindrical_projection_is_linear_and_preserves_norm(self):
        rng = np.random.default_rng(41)
        a = rng.normal(size=(2, 12, 3)) + 1j * rng.normal(size=(2, 12, 3))
        b = rng.normal(size=(2, 12, 3)) + 1j * rng.normal(size=(2, 12, 3))
        template = make_field()
        aa, bb = replace(template, e_global_v_per_m=a), replace(template, e_global_v_per_m=b)
        cc = replace(template, e_global_v_per_m=2j * a - 3 * b)
        for ca, cb, result in zip(aa.cylindrical_components(), bb.cylindrical_components(), cc.cylindrical_components()):
            np.testing.assert_allclose(result, 2j * ca - 3 * cb, rtol=1e-13, atol=1e-13)
        cyl = aa.cylindrical_components()
        np.testing.assert_allclose(sum(np.abs(x) ** 2 for x in cyl), np.sum(np.abs(a) ** 2, axis=-1))

    def test_complex_scaling_preserves_ratios_and_scales_energy(self):
        phi = np.arange(12) * 2 * math.pi / 12
        field = make_field(ez=1 + 0.4 * np.cos(3 * phi), ep=0.2j * np.exp(1j * phi))
        first = diagnose_aperture(field)
        second = diagnose_aperture(replace(field, e_global_v_per_m=field.e_global_v_per_m * (2j)))
        self.assertAlmostEqual(first["ez_fraction_of_tangential_squared_field"], second["ez_fraction_of_tangential_squared_field"])
        for key, value in first["squared_field_integrals_v2"].items():
            self.assertAlmostEqual(second["squared_field_integrals_v2"][key], 4 * value)
        for channel in ("e_z", "e_phi"):
            for key in ("within_z_first_order_coherence_magnitude", "within_z_coherent_energy_fraction"):
                self.assertAlmostEqual(first["azimuthal_coherence"][channel][key], second["azimuthal_coherence"][channel][key])
        for a, b in zip(first["sector_symmetry"]["rotations"], second["sector_symmetry"]["rotations"]):
            self.assertAlmostEqual(a["relative_complex_l2"], b["relative_complex_l2"])

    def test_helical_phase_detected_even_when_amplitude_and_aligned_field_match(self):
        phi = np.arange(12) * 2 * math.pi / 12
        report = diagnose_aperture(make_field(ez=np.exp(1j * phi)))
        stats = report["azimuthal_coherence"]["e_z"]
        self.assertLess(stats["within_z_first_order_coherence_magnitude"], 1e-12)
        self.assertLess(stats["within_z_coherent_energy_fraction"], 1e-24)
        self.assertIsNone(stats["per_z"][0]["circular_mean_phase_deg"])
        self.assertIn("unbounded", stats["per_z"][0]["phase_condition"])
        rotation = report["sector_symmetry"]["rotations"][0]
        self.assertAlmostEqual(rotation["relative_complex_l2"], math.sqrt(3))
        self.assertAlmostEqual(rotation["relative_amplitude_l2"], 0)
        self.assertAlmostEqual(rotation["phase_offset_deg"], 120)
        self.assertAlmostEqual(rotation["phase_aligned_relative_l2"], 0)

    def test_uniform_local_azimuthal_field_is_rotationally_symmetric(self):
        # Cartesian vectors rotate with phi; comparing raw Ex/Ey by index
        # would incorrectly call this azimuthally uniform field asymmetric.
        report = diagnose_aperture(make_field(ez=0, ep=1 + 2j))
        self.assertAlmostEqual(report["ez_fraction_of_tangential_squared_field"], 0)
        for rotation in report["sector_symmetry"]["rotations"]:
            self.assertLess(rotation["relative_complex_l2"], 1e-14)

    def test_nonuniform_z_uses_area_weights_not_sample_count(self):
        z = np.array([0, 0.001, 0.004])
        report = diagnose_aperture(make_field(ez=np.array([1, 2, 3])[:, None], z=z))
        weighted_square = 0.0005 * 1 + 0.002 * 4 + 0.0015 * 9
        self.assertAlmostEqual(report["squared_field_integrals_v2"]["e_z"], 2 * math.pi * 0.08 * weighted_square)

    def test_z_dependent_phase_does_not_masquerade_as_azimuth_decoherence(self):
        report = diagnose_aperture(make_field(ez=np.array([1, -1])[:, None]))
        self.assertAlmostEqual(report["azimuthal_coherence"]["e_z"]["within_z_first_order_coherence_magnitude"], 1)
        self.assertAlmostEqual(report["azimuthal_coherence"]["e_z"]["per_z"][1]["circular_mean_phase_deg"], 180)

    def test_phase_wrap_circular_dispersion(self):
        values = np.exp(1j * np.deg2rad([179, -179] * 6))
        row = diagnose_aperture(make_field(ez=values))["azimuthal_coherence"]["e_z"]["per_z"][0]
        self.assertAlmostEqual(abs(row["circular_mean_phase_deg"]), 180)
        self.assertAlmostEqual(row["amplitude_weighted_circular_std_deg"], 1, places=3)

    def test_amplitude_taper_distinguished_from_phase_coherence(self):
        values = np.array([0, 1] * 6)
        row = diagnose_aperture(make_field(ez=values))["azimuthal_coherence"]["e_z"]["per_z"][0]
        self.assertEqual(row["nonzero_phi_samples"], 6)
        self.assertAlmostEqual(row["first_order_coherence_magnitude"], 1)
        self.assertAlmostEqual(row["coherent_energy_fraction"], 0.5)

    def test_zero_field_has_no_phase_or_fraction(self):
        report = diagnose_aperture(make_field(ez=0))
        self.assertIsNone(report["ez_fraction_of_tangential_squared_field"])
        self.assertEqual(report["sector_symmetry"]["condition"], "zero_tangential_field")
        self.assertEqual(report["sector_symmetry"]["rotations"], [])
        row = report["azimuthal_coherence"]["e_z"]["per_z"][0]
        self.assertIsNone(row["first_order_coherence_magnitude"])
        self.assertEqual(row["phase_condition"], "zero_field")
        json.dumps(report, allow_nan=False)

    def test_incompatible_sector_grid_is_explicit(self):
        report = diagnose_aperture(make_field(nphi=10), sectors=3)
        self.assertFalse(report["conditions"]["sector_grid_compatible"])
        self.assertEqual(report["sector_symmetry"]["condition"], "incompatible_phi_count_no_interpolation")
        self.assertEqual(report["sector_symmetry"]["rotations"], [])
        self.assertEqual(len(diagnose_aperture(make_field(nphi=10), sectors=5)["sector_symmetry"]["rotations"]), 4)

    def test_threefold_amplitude_symmetry_and_local_defect(self):
        phi = np.arange(12) * 2 * math.pi / 12
        symmetric = make_field(ez=1 + 0.4 * np.cos(3 * phi))
        rotations = diagnose_aperture(symmetric)["sector_symmetry"]["rotations"]
        self.assertTrue(all(row["relative_complex_l2"] < 1e-14 for row in rotations))
        damaged = symmetric.e_global_v_per_m.copy()
        damaged[:, 0, 2] += 0.5j
        rotations = diagnose_aperture(replace(symmetric, e_global_v_per_m=damaged))["sector_symmetry"]["rotations"]
        self.assertTrue(all(row["relative_complex_l2"] > 0.1 for row in rotations))

    def test_extreme_dynamic_range_is_explicit_not_zero_field_or_nan(self):
        for amplitude in (1e-200, 1e200):
            with self.subTest(amplitude=amplitude), np.errstate(over="ignore", under="ignore"), self.assertRaisesRegex(ValueError, "dynamic range"):
                diagnose_aperture(make_field(ez=amplitude))

    def test_missing_angles_nonfinite_and_unsorted_arrays_are_refused(self):
        template = make_field()
        for phi in (template.phi_rad[:-1], template.phi_rad[::-1]):
            with self.subTest(phi=phi), self.assertRaises(ValueError):
                replace(template, phi_rad=phi, e_global_v_per_m=template.e_global_v_per_m[:, :len(phi), :])
        with self.assertRaises(ValueError):
            replace(template, z_m=np.array([0, np.nan]))
        with self.assertRaises(ValueError):
            replace(template, e_global_v_per_m=np.full((2, 12, 3), complex(float("inf"), 1)))
        with self.assertRaises(ValueError):
            replace(template, frequency_hz=-1)
        for sectors in (1, True, 2.5):
            with self.subTest(sectors=sectors), self.assertRaises(ValueError):
                diagnose_aperture(template, sectors=sectors)

    def test_constructor_does_not_retain_mutable_input_alias(self):
        template = make_field()
        original = np.ones((2, 12, 3), dtype=complex)
        field = replace(template, e_global_v_per_m=original)
        original[:] = 0
        self.assertTrue(np.all(field.e_global_v_per_m == 1))
        self.assertFalse(field.e_global_v_per_m.flags.writeable)


class CsvAndCliTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "near_field.csv"

    def read(self, text=None):
        self.path.write_text(example_csv() if text is None else text, encoding="utf-8")
        return read_aperture_csv(self.path)

    def test_example_units_and_synthetic_provenance(self):
        field = self.read()
        self.assertEqual(field.declared_source, "SYNTHETIC")
        self.assertEqual(field.frequency_hz, 28e9)
        self.assertEqual(field.radius_m, 0.08)
        np.testing.assert_allclose(field.z_m, [-0.00125, 0.00125])
        self.assertIn("not radiated power", diagnose_aperture(field)["limitations"][0])

    def test_equivalent_units_produce_identical_si_fields(self):
        first = diagnose_aperture(self.read())
        text = example_csv().replace("frequency=28", "frequency=28000").replace("frequency_unit=GHz", "frequency_unit=MHz")
        text = text.replace(",28,synthetic_common_excitation,", ",28000,synthetic_common_excitation,")
        text = text.replace("radius=80", "radius=0.08").replace("length_unit=mm", "length_unit=m")
        text = text.replace(",-1.25,", ",-0.00125,").replace(",1.25,", ",0.00125,")
        text = text.replace("field_unit=V/m", "field_unit=mV/m").replace(",0,0,0,0,1,0", ",0,0,0,0,1000,0")
        second = diagnose_aperture(self.read(text))
        self.assertEqual(first["squared_field_integrals_v2"], second["squared_field_integrals_v2"])

    def test_rad_angle_units_and_negative_time_convention(self):
        text = example_csv().replace("angle_unit=deg", "angle_unit=rad").replace("exp(+jwt)", "exp(-jwt)")
        rows = []
        for line in text.splitlines():
            if line and line[0].isdigit():
                phi, rest = line.split(",", 1)
                line = f"{math.radians(float(phi)):.16g},{rest}"
            rows.append(line)
        field = self.read("\n".join(rows))
        self.assertEqual(field.phasor_convention, "exp(-jwt)")
        self.assertAlmostEqual(diagnose_aperture(field)["ez_fraction_of_tangential_squared_field"], 1)

    def test_mixed_frequency_reference_units_and_frames_refused(self):
        original = example_csv()
        changes = [original.replace(",28,synthetic_common_excitation,", ",29,synthetic_common_excitation,", 1),
                   original.replace(",28,synthetic_common_excitation,", ",28,separately_normalized,", 1),
                   original.replace("field_unit=V/m", "field_unit=dB(GainTotal)"),
                   original.replace("coordinate_system=Global", "coordinate_system=Local"),
                   original.replace("cylinder_axis=global_z", "cylinder_axis=global_x"),
                   original.replace("# phasor_convention=exp(+jwt)\n", ""),
                   original.replace("# source=SYNTHETIC\n", "# source=SYNTHETIC\n# source=HFSS\n"),
                   original.replace(",0,0,0,0,1,0", ",0,0,0,0,nan,0", 1)]
        for text in changes:
            with self.subTest(text=text[:90]), self.assertRaises(ValueError):
                self.read(text)

    def test_missing_cell_repeated_sample_and_incomplete_columns_refused(self):
        lines = example_csv().splitlines()
        invalid = ["\n".join(lines[:-1]), "\n".join(lines + [lines[-1]]),
                   example_csv().replace("ex_im,", "extra,")]
        for text in invalid:
            with self.subTest(text=text[-100:]), self.assertRaises(ValueError):
                self.read(text)

    def test_missing_whole_azimuth_column_or_z_row_cannot_be_a_coarser_export(self):
        lines = example_csv().splitlines()
        no_column = [line for line in lines if not line.startswith("60,")]
        no_z_plane = [line for line in lines if ",-1.25," not in line]
        for changed in (no_column, no_z_plane):
            with self.assertRaisesRegex(ValueError, "count"):
                self.read("\n".join(changed))

    def test_periodic_seam_checked_and_counted_once(self):
        original = example_csv()
        duplicates = "360,-1.25,28,synthetic_common_excitation,0,0,0,0,1,0\n360,1.25,28,synthetic_common_excitation,0,0,0,0,1,0\n"
        original_integral = diagnose_aperture(self.read())["squared_field_integrals_v2"]
        field = self.read(original + duplicates)
        self.assertTrue(field.seam_removed)
        self.assertEqual(len(field.phi_rad), 6)
        self.assertEqual(diagnose_aperture(field)["squared_field_integrals_v2"], original_integral)
        with self.assertRaisesRegex(ValueError, "seam"):
            self.read(original + duplicates.replace(",1,0", ",0,1"))
        with self.assertRaises(ValueError):
            self.read(original + duplicates + duplicates)

    def test_gain_csv_is_not_converted_to_complex_field(self):
        with self.assertRaises(ValueError):
            self.read('"Freq [GHz]","Phi [deg]","Theta [deg]","dB(GainTotal) []"\n28,0,90,1.5\n')

    def test_cli_example_json_and_invalid_input(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(main(["--example"]), 0)
        field = self.read(out.getvalue())
        self.assertEqual(field.declared_source, "SYNTHETIC")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(main([str(self.path), "--sectors", "3"]), 0)
        self.assertEqual(json.loads(out.getvalue())["kind"], "CYLINDRICAL_APERTURE_FIELD_DIAGNOSTIC")
        target = Path(self.temp.name) / "report.json"
        self.assertEqual(main([str(self.path), "--output", str(target)]), 0)
        self.assertEqual(json.loads(target.read_text())["input"]["declared_source"], "SYNTHETIC")
        self.path.write_text("gain only", encoding="utf-8")
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            self.assertEqual(main([str(self.path)]), 2)
        self.assertEqual(out.getvalue(), "")
        self.assertIn("missing explicit metadata", err.getvalue())


if __name__ == "__main__":
    unittest.main()
