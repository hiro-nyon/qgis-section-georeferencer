import csv
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from package_exporter import (  # noqa: E402
    localize_config_image_path,
    package_file_names,
    sanitize_package_name,
    write_gps_csv,
    write_plateau_geojson,
)


class PackageFileNameTests(unittest.TestCase):
    def test_default_names_are_unprefixed(self):
        names = package_file_names()
        self.assertEqual(names["section_config"], "section_config.json")

    def test_package_name_prefixes_every_file(self):
        names = package_file_names("bldg_a")
        self.assertEqual(names["section_config"], "bldg_a_section_config.json")
        self.assertEqual(names["gps_projected"], "bldg_a_gps_projected.csv")

    def test_path_separators_are_stripped_from_package_name(self):
        self.assertEqual(sanitize_package_name("a/b:c"), "a_b_c")

    def test_empty_package_name_falls_back(self):
        self.assertEqual(sanitize_package_name("   "), "section_package")


class ConfigImagePathTests(unittest.TestCase):
    def test_absolute_source_path_is_replaced_by_package_file(self):
        config = {"image": {"path": "/Users/someone/private/section.png"}}
        names = package_file_names("bldg_a")
        localized = localize_config_image_path(config, names)
        self.assertEqual(localized["image"]["path"], names["section_background"])
        self.assertEqual(localized["image"]["source_name"], "section.png")

    def test_original_config_is_not_mutated(self):
        config = {"image": {"path": "/tmp/section.png"}}
        localize_config_image_path(config, package_file_names())
        self.assertEqual(config["image"]["path"], "/tmp/section.png")

    def test_missing_image_section_is_tolerated(self):
        localized = localize_config_image_path({"crs": "EPSG:6697"}, package_file_names())
        self.assertEqual(localized["crs"], "EPSG:6697")


class WriterTests(unittest.TestCase):
    def test_gps_csv_keeps_outside_buffer_rows_and_extra_fields(self):
        records = [
            {"source_fid": 1, "u": 10.0, "v": 20.0, "inside_buffer": True},
            {"source_fid": 2, "u": 11.0, "v": 21.0, "inside_buffer": False, "floor": "3F"},
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "gps.csv")
            write_gps_csv(path, records)
            with open(path, newline="", encoding="utf-8") as file_obj:
                rows = list(csv.DictReader(file_obj))
        self.assertEqual(len(rows), 2)
        self.assertIn("floor", rows[0])
        self.assertEqual(rows[0]["inside_buffer"], "true")
        self.assertEqual(rows[0]["floor"], "")
        self.assertEqual(rows[1]["inside_buffer"], "false")
        self.assertEqual(rows[1]["floor"], "3F")

    def test_plateau_geojson_uses_section_coordinates_and_keeps_pixels(self):
        records = [{"s": 1.5, "z_section": 4.0, "u": 100.0, "v": 200.0, "d": 0.5}]
        segments = [
            {"s0": 0.0, "z0": 0.0, "s1": 1.0, "z1": 2.0, "u0": 1, "v0": 2, "u1": 3, "v1": 4}
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "plateau.geojson")
            write_plateau_geojson(path, records, segments)
            with open(path, encoding="utf-8") as file_obj:
                data = json.load(file_obj)
        point, line = data["features"]
        self.assertEqual(point["geometry"]["coordinates"], [1.5, 4.0])
        self.assertEqual(point["properties"]["u"], 100.0)
        self.assertEqual(line["geometry"]["coordinates"], [[0.0, 0.0], [1.0, 2.0]])
        self.assertEqual(line["properties"]["u0"], 1)


if __name__ == "__main__":
    unittest.main()
