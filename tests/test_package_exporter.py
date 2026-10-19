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
    write_world_geojson,
)

try:
    from osgeo import ogr, osr
except ImportError:  # pragma: no cover - depends on the interpreter
    ogr = None


class PackageExportTests(unittest.TestCase):
    def test_portable_names_and_image_path(self):
        for raw, expected in (("a/b:c", "a_b_c"), ("   ", "section_package")):
            with self.subTest(name=raw):
                self.assertEqual(sanitize_package_name(raw), expected)
        names = package_file_names("bldg_a")
        self.assertEqual(names["section_config"], "bldg_a_section_config.json")
        self.assertEqual(names["gps_projected"], "bldg_a_gps_projected.csv")
        source = "/Users/someone/private/section.png"
        config = {"image": {"path": source}}
        localized = localize_config_image_path(config, names)
        self.assertEqual(localized["image"]["path"], names["section_background"])
        self.assertEqual(localized["image"]["source_name"], "section.png")
        self.assertEqual(config["image"]["path"], source)

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

    @unittest.skipUnless(ogr is not None, "GDAL Python bindings are not available")
    def test_gdal_reads_the_project_crs_and_easting_northing_order(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "world.geojson")
            write_world_geojson(path, [{"x": 10000, "y": -50000, "z_raw": 3, "s": 1}], [], "EPSG:6677")
            dataset = ogr.Open(path)
            layer = dataset.GetLayer(0)
            srs = layer.GetSpatialRef()
            self.assertEqual(srs.GetAuthorityCode(None), "6677")
            feature = layer.GetNextFeature()
            geometry = feature.GetGeometryRef().Clone()
            srs = srs.Clone()
            feature = None
            dataset = None
        wgs84 = osr.SpatialReference()
        wgs84.ImportFromEPSG(4326)
        wgs84.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
        geometry.AssignSpatialReference(srs)
        geometry.TransformTo(wgs84)
        # 10 km east and 50 km south of the zone IX origin (36N, 139.8333E).
        self.assertAlmostEqual(geometry.GetX(), 139.94, places=1)
        self.assertAlmostEqual(geometry.GetY(), 35.55, places=1)
