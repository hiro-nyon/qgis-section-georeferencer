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
class LayerProjectorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        start_qgis()
        cls.projector = plugin_module("layer_projector")
        from qgis.core import QgsCoordinateReferenceSystem

        cls.crs = QgsCoordinateReferenceSystem("EPSG:6677")

    def project(self, layer, *, vertices=False, z_field=None):
        project = (self.projector.project_vector_layer_vertices if vertices
                   else self.projector.project_point_layer)
        return project(layer, self.crs, P0, P1, IDENTITY, 0.0, 5.0, z_field=z_field)

    def test_polygon_rings_close_without_bridging_parts_or_holes(self):
        outer = [(0, 0), (10, 0), (10, 10), (0, 10), (0, 0)]
        second = [(20, 0), (30, 0), (30, 10), (20, 10), (20, 0)]
        hole = [(2, 2), (4, 2), (4, 4), (2, 2)]
        cases = [
            ("MultiPolygonZ", "MultiPolygonZ(((0 0 0,10 0 0,10 0 10,0 0 10,0 0 0)),"
             "((20 0 0,30 0 0,30 0 10,20 0 10,20 0 0)))", [outer, second]),
            ("PolygonZ", "PolygonZ((0 0 0,10 0 0,10 0 10,0 0 10,0 0 0),"
             "(2 0 2,4 0 2,4 0 4,2 0 2))", [outer, hole]),
        ]
        for geometry, wkt, rings in cases:
            with self.subTest(geometry=geometry):
                result = self.project(_memory_layer(geometry + "?crs=EPSG:6677", [wkt]), vertices=True)
                edges = [((s["s0"], s["z0"]), (s["s1"], s["z1"])) for s in result["segments"]]
                self.assertCountEqual(edges, [edge for ring in rings for edge in zip(ring, ring[1:])])

    def test_multipoints_do_not_become_lines(self):
        layer = _memory_layer("MultiPointZ?crs=EPSG:6677", ["MultiPointZ((1 0 1),(2 0 2),(3 0 3))"])
        result = self.project(layer, vertices=True)
        self.assertEqual(len(result["records"]), 3)
        self.assertEqual(result["segments"], [])

    def test_point_height_precedence_and_fallback(self):
        cases = [
            ("PointZ(5 1 0)", "floor_h", 12.5, "floor_h", 12.5, "floor_h"),
            ("PointZ(5 1 0)", "Floor_H", 3, "floor_h", 3, "Floor_H"),
            ("PointZ(5 1 7)", "floor_h", None, "floor_h", 7, "geometry_z"),
            ("PointZ(5 1 7)", "height", 99, "missing", 7, "geometry_z"),
            ("PointZ(5 1 7)", "height", 99, None, 7, "geometry_z"),
            ("Point(5 1)", "height", 4, None, 4, "height"),
        ]
        for wkt, field, value, requested, height, source in cases:
            with self.subTest(wkt=wkt, field=field, requested=requested):
                definition = wkt.split("(")[0] + "?crs=EPSG:6677&field=" + field + ":double"
                result = self.project(_memory_layer(definition, [wkt], [{field: value}]), z_field=requested)
                record = result["records"][0]
                self.assertEqual((record["z_raw"], record["z_source"]), (height, source))
                if requested == "missing":
                    self.assertTrue(any("missing" in warning for warning in result["warnings"]))

    def test_building_height_field_only_fills_2d_geometry(self):
        # Applying measuredHeight to 3D vertices would flatten the building.
        for geometry, wkt, expected in (
            ("PolygonZ", "PolygonZ((0 0 0,10 0 0,10 0 12,0 0 0))", [0, 0, 12, 0]),
            ("Polygon", "Polygon((0 0,10 0,10 1,0 0))", [30, 30, 30, 30]),
        ):
            with self.subTest(geometry=geometry):
                layer = _memory_layer(geometry + "?crs=EPSG:6677&field=measuredHeight:double",
                                      [wkt], [{"measuredHeight": 30}])
                result = self.project(layer, vertices=True, z_field="measuredHeight")
                self.assertEqual([r["z_raw"] for r in result["records"]], expected)

    def project_one(self, definition, attributes):
        layer = _memory_layer(definition, ["PointZ(5 1 3)"], [attributes])
        return self.projector.project_point_layer(
            layer, self.crs, P0, P1, IDENTITY, 0.0, 5.0
        )["records"][0]

    def test_source_attributes_keep_nulls_and_datetime_meaning(self):
        from qgis.PyQt.QtCore import QDate, QDateTime, QTime, QTimeZone

        for zone, suffix in ((None, ""), (QTimeZone(b"Asia/Tokyo"), "+09:00")):
            with self.subTest(timezone=suffix):
                args = [QDate(2026, 10, 1), QTime(8, 30)]
                if zone is not None:
                    args.append(zone)
                record = self.project_one(
                    "PointZ?crs=EPSG:6677&field=recordedat:datetime&field=dailyid:string&field=accuracy:double",
                    {"recordedat": QDateTime(*args), "dailyid": "abc", "accuracy": None})
                self.assertEqual(record["recordedat"], "2026-10-01T08:30:00" + suffix)
                self.assertEqual(record["dailyid"], "abc")
                self.assertIsNone(record["accuracy"])

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
