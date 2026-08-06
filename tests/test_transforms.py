import math
import os
import sys
import unittest

# The plugin directory is the repository root, and a clone can be named
# anything, so import the pure-Python modules by path rather than assuming the
# package is importable as `section_georeferencer`.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from transforms import (  # noqa: E402
    analyze_affine_deformation,
    compute_section_basis,
    deformation_warnings,
    fit_affine_pixel_to_section,
    pixel_to_section,
    section_to_pixel,
    world_to_section,
)


class TransformTests(unittest.TestCase):
    def test_compute_section_basis_horizontal(self):
        direction, perpendicular, length = compute_section_basis((0, 0), (10, 0))
        self.assertAlmostEqual(length, 10.0)
        self.assertAlmostEqual(direction[0], 1.0)
        self.assertAlmostEqual(direction[1], 0.0)
        self.assertAlmostEqual(perpendicular[0], 0.0)
        self.assertAlmostEqual(perpendicular[1], 1.0)

    def test_world_to_section_horizontal(self):
        s, d, z = world_to_section(7, 3, 12, (0, 0), (10, 0), z_offset=2)
        self.assertAlmostEqual(s, 7.0)
        self.assertAlmostEqual(d, 3.0)
        self.assertAlmostEqual(z, 10.0)

    def test_world_to_section_diagonal(self):
        s, d, z = world_to_section(10, 0, 5, (0, 0), (10, 10), z_offset=1)
        self.assertAlmostEqual(s, 10.0 / math.sqrt(2.0))
        self.assertAlmostEqual(d, -10.0 / math.sqrt(2.0))
        self.assertAlmostEqual(z, 4.0)

    def test_exact_affine_fit_three_gcps(self):
        gcps = [
            {"u": 0, "v": 0, "s": 10, "z": 20},
            {"u": 10, "v": 0, "s": 30, "z": 20},
            {"u": 0, "v": 10, "s": 10, "z": 50},
        ]
        result = fit_affine_pixel_to_section(gcps)
        self.assertAlmostEqual(result["rms"], 0.0)
        s, z = pixel_to_section(4, 5, result["pixel_to_section"])
        self.assertAlmostEqual(s, 18.0)
        self.assertAlmostEqual(z, 35.0)

    def test_affine_fit_more_than_three_gcps(self):
        gcps = [
            (0, 0, 1, 2),
            (10, 0, 21, 7),
            (0, 10, -9, 32),
            (10, 10, 11, 37),
            (5, 7, 4, 25.5),
        ]
        result = fit_affine_pixel_to_section(gcps)
        self.assertLess(result["rms"], 1.0e-10)
        s, z = pixel_to_section(2, 3, result["pixel_to_section"])
        self.assertAlmostEqual(s, 2.0)
        self.assertAlmostEqual(z, 12.0)

    def test_inverse_transform_consistency(self):
        gcps = [
            (0, 0, 100, 200),
            (20, 0, 140, 210),
            (0, 30, 85, 260),
            (20, 30, 125, 270),
        ]
        result = fit_affine_pixel_to_section(gcps)
        s, z = pixel_to_section(12.5, 18.5, result["pixel_to_section"])
        u, v = section_to_pixel(s, z, result["section_to_pixel"])
        self.assertAlmostEqual(u, 12.5)
        self.assertAlmostEqual(v, 18.5)

    def test_fewer_than_three_gcps_raises(self):
        with self.assertRaises(ValueError):
            fit_affine_pixel_to_section([(0, 0, 0, 0), (1, 1, 1, 1)])

    def test_zero_length_section_line_raises(self):
        with self.assertRaises(ValueError):
            compute_section_basis((0, 0), (0, 0))

    def test_singular_gcp_configuration_raises(self):
        gcps = [
            (0, 0, 0, 0),
            (10, 0, 10, 0),
            (20, 0, 20, 0),
        ]
        with self.assertRaises(ValueError):
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

    def test_non_uniform_stretch_triggers_warning(self):
        deformation = analyze_affine_deformation(
            {"s": [0.0, 1.5, 0.0], "z": [0.0, 0.0, 1.0]}
        )
        self.assertAlmostEqual(deformation["stretch_difference_percent"], 50.0)
        self.assertTrue(deformation["warning"])
        self.assertIn("stretch", deformation["warnings"][0])

    def test_shear_triggers_warning(self):
        shear = math.tan(math.radians(10.0))
        deformation = analyze_affine_deformation(
            {"s": [0.0, 1.0, shear], "z": [0.0, 0.0, 1.0]}
        )
        self.assertAlmostEqual(deformation["shear_degrees"], 10.0)
        self.assertTrue(any("shear" in warning for warning in deformation["warnings"]))

    def test_fitted_transform_contains_deformation_record(self):
        gcps = [
            (0, 0, 0, 0),
            (10, 0, 20, 0),
            (0, 10, 0, 10),
        ]
        result = fit_affine_pixel_to_section(gcps)
        self.assertIn("deformation", result)
        self.assertAlmostEqual(
            result["deformation"]["stretch_difference_percent"], 100.0
        )


if __name__ == "__main__":
    unittest.main()
