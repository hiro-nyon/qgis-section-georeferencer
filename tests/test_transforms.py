import math
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from transforms import (
    analyze_affine_deformation, compute_section_basis, deformation_warnings,
    fit_affine_pixel_to_section, pixel_to_section, section_to_pixel, world_to_section,
)


class TransformTests(unittest.TestCase):
    def test_world_to_section(self):
        for point, end, offset, expected in (
            ((7, 3, 12), (10, 0), 2, (7, 3, 10)),
            ((10, 0, 5), (10, 10), 1, (10 / math.sqrt(2), -10 / math.sqrt(2), 4)),
        ):
            with self.subTest(end=end):
                actual = world_to_section(*point, (0, 0), end, z_offset=offset)
                for value, target in zip(actual, expected):
                    self.assertAlmostEqual(value, target)

    def test_affine_fit_and_inverse(self):
        cases = [
            ([{"u": 0, "v": 0, "s": 10, "z": 20},
              {"u": 10, "v": 0, "s": 30, "z": 20},
              {"u": 0, "v": 10, "s": 10, "z": 50}], (4, 5), (18, 35)),
            ([(0, 0, 1, 2), (10, 0, 21, 7), (0, 10, -9, 32),
              (10, 10, 11, 37), (5, 7, 4, 25.5)], (2, 3), (2, 12)),
        ]
        for gcps, pixel, expected in cases:
            with self.subTest(gcps=len(gcps)):
                result = fit_affine_pixel_to_section(gcps)
                self.assertLess(result["rms"], 1e-10)
                section = pixel_to_section(*pixel, result["pixel_to_section"])
                recovered = section_to_pixel(*section, result["section_to_pixel"])
                for actual, target in zip(section + recovered, expected + pixel):
                    self.assertAlmostEqual(actual, target)
                self.assertIn("deformation", result)

    def test_degenerate_inputs_are_rejected(self):
        with self.assertRaises(ValueError):
            compute_section_basis((0, 0), (0, 0))
        for gcps in ([(0, 0, 0, 0), (1, 1, 1, 1)],
                     [(0, 0, 0, 0), (10, 0, 10, 0), (20, 0, 20, 0)]):
            with self.subTest(gcps=gcps), self.assertRaises(ValueError):
                fit_affine_pixel_to_section(gcps)

    def test_deformation_metrics_for_rigid_transform(self):
        angle = math.radians(30.0)
        deformation = analyze_affine_deformation(
            {
                "s": [5.0, math.cos(angle), -math.sin(angle)],
                "z": [7.0, math.sin(angle), math.cos(angle)],
            }
        )
        self.assertAlmostEqual(deformation["scale_u_m_per_px"], 1.0)
        self.assertAlmostEqual(deformation["scale_v_m_per_px"], 1.0)
        self.assertAlmostEqual(deformation["stretch_difference_percent"], 0.0)
        self.assertAlmostEqual(deformation["shear_degrees"], 0.0)
        self.assertAlmostEqual(deformation["rotation_degrees"], 30.0)
        self.assertEqual(deformation_warnings(deformation), [])

    def test_deformation_warnings(self):
        for coeffs, metric, expected, warning in (
            ({"s": [0, 1.5, 0], "z": [0, 0, 1]}, "stretch_difference_percent", 50, "stretch"),
            ({"s": [0, 1, math.tan(math.radians(10))], "z": [0, 0, 1]}, "shear_degrees", 10, "shear"),
        ):
            with self.subTest(metric=metric):
                result = analyze_affine_deformation(coeffs)
                self.assertAlmostEqual(result[metric], expected)
                self.assertTrue(any(warning in text for text in deformation_warnings(result)))
