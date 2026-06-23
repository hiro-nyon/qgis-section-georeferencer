import math
import unittest

from section_georeferencer.transforms import (
    compute_section_basis,
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


if __name__ == "__main__":
    unittest.main()
