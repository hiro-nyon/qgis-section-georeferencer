import csv
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from package_exporter import (  # noqa: E402
    geojson_crs_member,
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


class WorldGeojsonCrsTests(unittest.TestCase):
    # QGIS x/y in JGD2011 / Japan Plane Rectangular CS IX: easting, northing.
    RECORD = {"x": 10000.0, "y": -50000.0, "z_raw": 3.0, "s": 1.0}

    def write(self, directory, crs_auth_id="EPSG:6677"):
        path = os.path.join(directory, "world.geojson")
        write_world_geojson(path, [self.RECORD], [], crs_auth_id)
        return path

    def test_crs_member_names_the_project_crs(self):
        with tempfile.TemporaryDirectory() as directory:
            with open(self.write(directory), encoding="utf-8") as file_obj:
                data = json.load(file_obj)
        self.assertEqual(
            data["crs"],
            {"type": "name", "properties": {"name": "urn:ogc:def:crs:EPSG::6677"}},
        )
        self.assertEqual(data["crs_auth_id"], "EPSG:6677")

    def test_no_crs_member_without_a_crs(self):
        self.assertIsNone(geojson_crs_member(""))
        self.assertIsNone(geojson_crs_member(None))

    def test_non_epsg_identifier_is_passed_through(self):
        member = geojson_crs_member("IGNF:LAMB93")
        self.assertEqual(member["properties"]["name"], "urn:ogc:def:crs:IGNF::LAMB93")

    @unittest.skipUnless(ogr is not None, "GDAL Python bindings are not available")
    def test_gdal_reads_the_project_crs_and_easting_northing_order(self):
        with tempfile.TemporaryDirectory() as directory:
            dataset = ogr.Open(self.write(directory))
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


if __name__ == "__main__":
    unittest.main()
