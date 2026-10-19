import unittest

from qgis_support import HAVE_QGIS, SKIP_REASON, plugin_module, start_qgis

IDENTITY = {"u": [0.0, 1.0, 0.0], "v": [0.0, 0.0, 1.0]}
P0 = (0.0, 0.0)
P1 = (100.0, 0.0)


def _memory_layer(definition, wkts, attributes=None):
    from qgis.core import QgsFeature, QgsGeometry, QgsVectorLayer

    layer = QgsVectorLayer(definition, "test", "memory")
    features = []
    for index, wkt in enumerate(wkts):
        feature = QgsFeature(layer.fields())
        feature.setGeometry(QgsGeometry.fromWkt(wkt))
        for name, value in (attributes or [{}] * len(wkts))[index].items():
            feature[name] = value
        features.append(feature)
    layer.dataProvider().addFeatures(features)
    return layer


@unittest.skipUnless(HAVE_QGIS, SKIP_REASON)
class SegmentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        start_qgis()
        cls.projector = plugin_module("layer_projector")
        from qgis.core import QgsCoordinateReferenceSystem

        cls.crs = QgsCoordinateReferenceSystem("EPSG:6677")

    def project(self, layer):
        return self.projector.project_vector_layer_vertices(
            layer, self.crs, P0, P1, IDENTITY, 0.0, 5.0
        )

    def assert_no_segment_between(self, segments, s_a, s_b):
        for segment in segments:
            ends = {(segment["s0"], segment["z0"]), (segment["s1"], segment["z1"])}
            self.assertNotEqual(ends, {s_a, s_b}, "segment bridges two rings/parts")

    def test_parts_of_a_multipolygon_are_not_joined(self):
        # Two vertical faces of a PLATEAU-like building, 4 edges each.
        layer = _memory_layer(
            "MultiPolygonZ?crs=EPSG:6677",
            [
                "MultiPolygonZ(((0 0 0, 10 0 0, 10 0 10, 0 0 10, 0 0 0)),"
                "((20 0 0, 30 0 0, 30 0 10, 20 0 10, 20 0 0)))"
            ],
        )
        segments = self.project(layer)["segments"]
        self.assertEqual(len(segments), 8)
        self.assert_no_segment_between(segments, (0.0, 0.0), (20.0, 0.0))

    def test_interior_ring_is_not_joined_to_the_exterior_ring(self):
        layer = _memory_layer(
            "PolygonZ?crs=EPSG:6677",
            [
                "PolygonZ((0 0 0, 10 0 0, 10 0 10, 0 0 10, 0 0 0),"
                "(2 0 2, 4 0 2, 4 0 4, 2 0 2))"
            ],
        )
        segments = self.project(layer)["segments"]
        self.assertEqual(len(segments), 4 + 3)
        self.assert_no_segment_between(segments, (0.0, 0.0), (2.0, 2.0))

    def test_ring_closing_edge_is_kept(self):
        layer = _memory_layer(
            "PolygonZ?crs=EPSG:6677", ["PolygonZ((0 0 0, 10 0 0, 10 0 10, 0 0 0))"]
        )
        segments = self.project(layer)["segments"]
        ends = [((s["s0"], s["z0"]), (s["s1"], s["z1"])) for s in segments]
        self.assertIn(((10.0, 10.0), (0.0, 0.0)), ends)

    def test_multipoint_layer_produces_no_segments(self):
        layer = _memory_layer(
            "MultiPointZ?crs=EPSG:6677", ["MultiPointZ((1 0 1), (2 0 2), (3 0 3))"]
        )
        result = self.project(layer)
        self.assertEqual(len(result["records"]), 3)
        self.assertEqual(result["segments"], [])


@unittest.skipUnless(HAVE_QGIS, SKIP_REASON)
class HeightFieldTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        start_qgis()
        cls.projector = plugin_module("layer_projector")
        from qgis.core import QgsCoordinateReferenceSystem

        cls.crs = QgsCoordinateReferenceSystem("EPSG:6677")

    def project(self, layer, z_field):
        return self.projector.project_point_layer(
            layer, self.crs, P0, P1, IDENTITY, 0.0, 5.0, z_field=z_field
        )

    def test_named_height_field_wins_over_geometry_z(self):
        layer = _memory_layer(
            "PointZ?crs=EPSG:6677&field=floor_h:double",
            ["PointZ(5 1 0)"],
            [{"floor_h": 12.5}],
        )
        record = self.project(layer, "floor_h")["records"][0]
        self.assertEqual(record["z_raw"], 12.5)
        self.assertEqual(record["z_source"], "floor_h")

    def test_field_name_matches_case_insensitively(self):
        layer = _memory_layer(
            "PointZ?crs=EPSG:6677&field=Floor_H:double",
            ["PointZ(5 1 0)"],
            [{"Floor_H": 3.0}],
        )
        record = self.project(layer, "floor_h")["records"][0]
        self.assertEqual(record["z_raw"], 3.0)

    def test_empty_named_field_falls_back_to_geometry_z(self):
        layer = _memory_layer(
            "PointZ?crs=EPSG:6677&field=floor_h:double",
            ["PointZ(5 1 7)"],
            [{"floor_h": None}],
        )
        record = self.project(layer, "floor_h")["records"][0]
        self.assertEqual(record["z_raw"], 7.0)
        self.assertEqual(record["z_source"], "geometry_z")

    def test_missing_named_field_warns_and_uses_geometry_z(self):
        layer = _memory_layer("PointZ?crs=EPSG:6677", ["PointZ(5 1 7)"])
        result = self.project(layer, "floor_h")
        self.assertEqual(result["records"][0]["z_source"], "geometry_z")
        self.assertTrue(any("floor_h" in warning for warning in result["warnings"]))

    def test_without_named_field_geometry_z_beats_default_fields(self):
        layer = _memory_layer(
            "PointZ?crs=EPSG:6677&field=height:double",
            ["PointZ(5 1 7)"],
            [{"height": 99.0}],
        )
        record = self.project(layer, None)["records"][0]
        self.assertEqual(record["z_raw"], 7.0)

    def test_default_field_used_for_2d_geometry(self):
        layer = _memory_layer(
            "Point?crs=EPSG:6677&field=height:double",
            ["Point(5 1)"],
            [{"height": 4.0}],
        )
        record = self.project(layer, None)["records"][0]
        self.assertEqual((record["z_raw"], record["z_source"]), (4.0, "height"))


@unittest.skipUnless(HAVE_QGIS, SKIP_REASON)
class VertexHeightFieldTests(unittest.TestCase):
    """For buildings the field is a fallback for 2D geometry only; using it
    over geometry Z would flatten a 3D building to one height."""

    @classmethod
    def setUpClass(cls):
        start_qgis()
        cls.projector = plugin_module("layer_projector")
        from qgis.core import QgsCoordinateReferenceSystem

        cls.crs = QgsCoordinateReferenceSystem("EPSG:6677")

    def project(self, layer):
        return self.projector.project_vector_layer_vertices(
            layer, self.crs, P0, P1, IDENTITY, 0.0, 5.0, z_field="measuredHeight"
        )

    def test_geometry_z_of_a_3d_building_is_kept(self):
        layer = _memory_layer(
            "PolygonZ?crs=EPSG:6677&field=measuredHeight:double",
            ["PolygonZ((0 0 0, 10 0 0, 10 0 12, 0 0 0))"],
            [{"measuredHeight": 30.0}],
        )
        heights = [record["z_raw"] for record in self.project(layer)["records"]]
        self.assertEqual(heights, [0.0, 0.0, 12.0, 0.0])

    def test_field_is_used_for_a_2d_building(self):
        layer = _memory_layer(
            "Polygon?crs=EPSG:6677&field=measuredHeight:double",
            ["Polygon((0 0, 10 0, 10 1, 0 0))"],
            [{"measuredHeight": 30.0}],
        )
        records = self.project(layer)["records"]
        self.assertEqual({record["z_raw"] for record in records}, {30.0})
        self.assertEqual(records[0]["z_source"], "measuredHeight")


@unittest.skipUnless(HAVE_QGIS, SKIP_REASON)
class GpsAttributeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        start_qgis()
        cls.projector = plugin_module("layer_projector")
        from qgis.core import QgsCoordinateReferenceSystem

        cls.crs = QgsCoordinateReferenceSystem("EPSG:6677")

    def project_one(self, definition, attributes):
        layer = _memory_layer(definition, ["PointZ(5 1 3)"], [attributes])
        return self.projector.project_point_layer(
            layer, self.crs, P0, P1, IDENTITY, 0.0, 5.0
        )["records"][0]

    def test_every_source_attribute_is_kept(self):
        from qgis.PyQt.QtCore import QDate, QDateTime, QTime

        record = self.project_one(
            "PointZ?crs=EPSG:6677&field=recordedat:datetime&field=dailyid:string",
            {
                "recordedat": QDateTime(QDate(2026, 10, 1), QTime(8, 30, 0)),
                "dailyid": "abc",
            },
        )
        self.assertEqual(record["dailyid"], "abc")
        self.assertIn("recordedat", record)

    def test_naive_datetime_is_iso_and_not_shifted(self):
        from qgis.PyQt.QtCore import QDate, QDateTime, QTime

        record = self.project_one(
            "PointZ?crs=EPSG:6677&field=recordedat:datetime",
            {"recordedat": QDateTime(QDate(2026, 10, 1), QTime(8, 30, 0))},
        )
        self.assertEqual(record["recordedat"], "2026-10-01T08:30:00")

    def test_datetime_keeps_the_offset_it_already_has(self):
        from qgis.PyQt.QtCore import QDate, QDateTime, QTime, QTimeZone

        value = QDateTime(QDate(2026, 10, 1), QTime(8, 30, 0), QTimeZone(b"Asia/Tokyo"))
        record = self.project_one(
            "PointZ?crs=EPSG:6677&field=recordedat:datetime", {"recordedat": value}
        )
        self.assertEqual(record["recordedat"], "2026-10-01T08:30:00+09:00")

    def test_null_attribute_is_none_not_the_string_null(self):
        record = self.project_one(
            "PointZ?crs=EPSG:6677&field=user_id:string&field=accuracy:double",
            {"user_id": None, "accuracy": None},
        )
        self.assertIsNone(record["user_id"])
        self.assertIsNone(record["accuracy"])

    def test_attribute_named_like_a_computed_column_is_prefixed(self):
        record = self.project_one(
            "PointZ?crs=EPSG:6677&field=x:string&field=s:string",
            {"x": "source-x", "s": "source-s"},
        )
        self.assertEqual(record["x"], 5.0)
        self.assertEqual(record["src_x"], "source-x")
        self.assertEqual(record["src_s"], "source-s")


    def test_colliding_attribute_names_survive_in_any_field_order(self):
        for fields in (["x", "src_x", "src_src_x"], ["src_src_x", "src_x", "x"]):
            with self.subTest(fields=fields):
                record = self.project_one(
                    "PointZ?crs=EPSG:6677&" + "&".join("field=" + f + ":string" for f in fields),
                    {"x": "original-x", "src_x": "original-src-x", "src_src_x": "original-src-src-x"},
                )
                self.assertEqual(record["x"], 5.0)
                self.assertEqual(record["src_src_src_x"], "original-x")
                self.assertEqual(record["src_x"], "original-src-x")
                self.assertEqual(record["src_src_x"], "original-src-src-x")


@unittest.skipUnless(HAVE_QGIS, SKIP_REASON)
class InvalidCrsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        start_qgis()

    def test_invalid_crs_never_returns_untransformed_records(self):
        from qgis.core import QgsCoordinateReferenceSystem
        projector = plugin_module("layer_projector")
        for geometry, wkt, method in (
            ("PointZ", "PointZ(139.7 35.6 10)", projector.project_point_layer),
            ("LineStringZ", "LineStringZ(139.7 35.6 10, 139.8 35.6 20)", projector.project_vector_layer_vertices),
        ):
            with self.subTest(geometry=geometry):
                layer = _memory_layer(geometry, [wkt])
                layer.setCrs(QgsCoordinateReferenceSystem())
                result = method(layer, QgsCoordinateReferenceSystem("EPSG:6677"), P0, P1, IDENTITY)
                self.assertEqual(result["records"], [])
                self.assertIn("no valid CRS", result["error"])

    def test_transform_initialization_failure_stops_projection(self):
        from unittest.mock import patch
        from qgis.core import QgsCoordinateReferenceSystem
        projector = plugin_module("layer_projector")
        layer = _memory_layer("PointZ?crs=EPSG:4326", ["PointZ(139.7 35.6 10)"])
        with patch.object(projector, "QgsCoordinateTransform", side_effect=RuntimeError("failed")):
            result = projector.project_point_layer(layer, QgsCoordinateReferenceSystem("EPSG:6677"), P0, P1, IDENTITY)
        self.assertEqual(result["records"], [])
        self.assertIn("failed", result["error"])


if __name__ == "__main__":
    unittest.main()
