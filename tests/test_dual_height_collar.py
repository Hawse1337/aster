"""Structure A: the ground-ring collar can carry two heights (inner for directivity, outer for bandwidth)."""
from __future__ import annotations

import unittest

from antopt.cases import lens28_var
from antopt.parametric.profile import with_ring_collar


def tip_face_profile() -> list[list[float]]:
    """A closed profile whose top face is horizontal at z=2 and covers radii 0..6 mm."""
    return [[0.0, 0.0], [6.0, 0.0], [6.0, 2.0], [0.0, 2.0]]


class CollarProfileTests(unittest.TestCase):
    def collar_points(self, points):
        return [point for point in points if 1.2 - 1e-9 <= point[0] <= 2.4 + 1e-9 and point[1] >= 2.0 - 1e-9]

    def test_a_single_height_collar_is_unchanged(self):
        points = with_ring_collar(tip_face_profile(), 1.2, 2.3, 0.6)
        collar = self.collar_points(points)
        heights = {round(point[1], 6) for point in collar}
        self.assertEqual(heights, {2.0, 2.3})

    def test_a_split_collar_carries_two_tops(self):
        points = with_ring_collar(tip_face_profile(), 1.2, 2.3, 0.5,
                                  mid_radius_mm=1.9, outer_height_mm=0.6)
        collar = self.collar_points(points)
        xs = {round(point[0], 3) for point in collar}
        self.assertIn(1.9, xs, "the split radius must appear in the profile")
        tops = sorted({round(point[1], 6) for point in collar if point[1] > 2.0})
        self.assertEqual(tops, [2.25, 2.3], "inner 0.50 -> top 2.25, outer 0.60 -> top 2.30")

    def test_equal_heights_collapse_to_one_step(self):
        points = with_ring_collar(tip_face_profile(), 1.2, 2.3, 0.6,
                                  mid_radius_mm=1.9, outer_height_mm=0.6)
        xs = [round(point[0], 3) for point in points]
        self.assertNotIn(1.9, xs, "equal heights must not add a spurious step")

    def test_a_split_inside_the_inner_radius_is_refused(self):
        for mid in (1.2, 1.1, 0.9):
            with self.subTest(mid=mid):
                with self.assertRaises(ValueError):
                    with_ring_collar(tip_face_profile(), 1.2, 2.3, 0.5,
                                     mid_radius_mm=mid, outer_height_mm=0.6)

    def test_a_split_at_or_beyond_the_outer_radius_is_one_collar(self):
        for mid in (2.3, 2.4):
            with self.subTest(mid=mid):
                points = with_ring_collar(tip_face_profile(), 1.2, 2.3, 0.5,
                                          mid_radius_mm=mid, outer_height_mm=0.6)
                xs = [round(point[0], 3) for point in points]
                self.assertNotIn(1.9, xs)

    def test_a_non_positive_outer_height_is_refused(self):
        with self.assertRaises(ValueError):
            with_ring_collar(tip_face_profile(), 1.2, 2.3, 0.5,
                             mid_radius_mm=1.9, outer_height_mm=0.0)


class CaseIntegrationTests(unittest.TestCase):
    base = None

    def setUp(self):
        self.base = dict(lens28_var.build_space().default())

    def test_the_parameter_space_carries_the_two_new_dimensions(self):
        names = [spec.name for spec in lens28_var.build_space()]
        self.assertIn("ring_mid", names)
        self.assertIn("ring_height_outer", names)
        self.assertEqual(len(names), 10)

    def test_the_middle_level_defaults_to_the_two_level_geometry(self):
        base = dict(self.base, ring_outer=3.0, ring_mid=2.0, ring_height=0.6,
                    ring_height_outer=0.8)
        without = dict(base)
        explicit = dict(base, ring_height_mid_delta=0.0)
        self.assertEqual(lens28_var.operations_full(without), lens28_var.operations_full(explicit))

    def test_a_positive_delta_raises_the_middle_level(self):
        params = dict(self.base, ring_outer=3.0, ring_mid=2.0, ring_height=0.6,
                      ring_height_outer=0.8, ring_height_mid_delta=0.2)
        ops = lens28_var.operations_full(params)
        cone = next(op for op in ops
                    if op["op"] == "revolve_profile" and op["args"]["name"] == "cone_dn")
        points = [(round(x, 3), round(z, 3)) for x, z in cone["args"]["points"]]
        self.assertIn((2.0, -0.6), points, "middle level rises by delta/2")
        self.assertNotIn((2.0, -0.7), points)

    def test_a_delta_that_buries_the_middle_level_is_rejected(self):
        params = dict(self.base, ring_outer=3.0, ring_mid=2.0, ring_height=0.2,
                      ring_height_mid_delta=-0.6)
        report = lens28_var.geometry_checks(params)
        self.assertFalse(report.valid)

    def test_the_default_geometry_is_unchanged(self):
        default = dict(self.base, ring_outer=2.3)
        explicit = dict(self.base, ring_outer=2.3, ring_mid=2.3, ring_height_outer=0.6)
        self.assertEqual(lens28_var.operations_full(default), lens28_var.operations_full(explicit))

    def test_the_split_geometry_reaches_the_revolved_profile(self):
        params = dict(self.base, ring_outer=2.3, ring_mid=1.9,
                      ring_height=0.5, ring_height_outer=0.6)
        ops = lens28_var.operations_full(params)
        cone = next(op for op in ops
                    if op["op"] == "revolve_profile" and op["args"]["name"] == "cone_dn")
        points = [(round(x, 3), round(z, 3)) for x, z in cone["args"]["points"]]
        self.assertIn((1.9, -0.75), points, "the flat inner section reaches the split")
        self.assertIn((2.3, -0.7), points, "the outer radius sits at the outer height")
        self.assertNotIn((1.9, -0.7), points,
                         "no vertical step: the collar ramps instead of stepping")

    def test_a_split_inside_the_coax_wall_is_rejected_by_the_checks(self):
        report = lens28_var.geometry_checks(dict(self.base, ring_outer=2.3, ring_mid=1.1))
        self.assertFalse(report.valid)

    def test_a_valid_split_passes_the_checks(self):
        report = lens28_var.geometry_checks(dict(self.base, ring_outer=2.3, ring_mid=1.9,
                                                 ring_height_outer=0.6))
        self.assertTrue(report.valid, report.checks)

    def test_a_non_positive_outer_height_is_rejected(self):
        report = lens28_var.geometry_checks(dict(self.base, ring_outer=2.3, ring_mid=1.9,
                                                 ring_height_outer=0.0))
        self.assertFalse(report.valid)


if __name__ == "__main__":
    unittest.main()
