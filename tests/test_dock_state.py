import os
import tempfile
import unittest

from qgis_support import HAVE_QGIS, SKIP_REASON, FakeIface, plugin_module, start_qgis

PROJECTED = "EPSG:6677"
GCPS = [(10.0, 90.0, 10.0, 0.0), (90.0, 90.0, 80.0, 0.0), (90.0, 10.0, 80.0, 40.0)]


@unittest.skipUnless(HAVE_QGIS, SKIP_REASON)
class DockStateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        start_qgis()
        cls.dock_module = plugin_module("section_georeferencer_dock")
        cls.config_io = plugin_module("config_io")
        from qgis.PyQt.QtGui import QColor, QImage

        cls.tempdir = tempfile.TemporaryDirectory()
        cls.image_path = os.path.join(cls.tempdir.name, "section.png")
        image = QImage(100, 100, QImage.Format_RGB32)
        image.fill(QColor("white"))
        image.save(cls.image_path)

    @classmethod
    def tearDownClass(cls):
        cls.tempdir.cleanup()

    def setUp(self):
        from qgis.core import QgsCoordinateReferenceSystem, QgsProject

        QgsProject.instance().setCrs(QgsCoordinateReferenceSystem(PROJECTED))
        self.iface = FakeIface()
        self.dock = self.dock_module.SectionGeoreferencerDock(self.iface)
        self.confirm_calls = []
        self.answer = True

        def confirm(title, text):
            self.confirm_calls.append(text)
            return self.answer

        self.dock._confirm = confirm

    def tearDown(self):
        from qgis.core import QgsProject

        self.dock.cleanup()
        self.dock.deleteLater()
        QgsProject.instance().removeAllMapLayers()

    # helpers ------------------------------------------------------------

    def set_project_crs(self, authid):
        from qgis.core import QgsCoordinateReferenceSystem, QgsProject

        QgsProject.instance().setCrs(QgsCoordinateReferenceSystem(authid))

    def layer(self, definition, wkts):
        from qgis.core import QgsFeature, QgsGeometry, QgsProject, QgsVectorLayer

        layer = QgsVectorLayer(definition, "layer_{}".format(len(wkts)), "memory")
        features = []
        for wkt in wkts:
            feature = QgsFeature(layer.fields())
            feature.setGeometry(QgsGeometry.fromWkt(wkt))
            features.append(feature)
        layer.dataProvider().addFeatures(features)
        QgsProject.instance().addMapLayer(layer)
        return layer

    def add_gcps(self):
        for u, v, s, z in GCPS:
            self.dock.current_pixel = (u, v)
            self.dock.gcp_s_edit.setText(str(s))
            self.dock.gcp_z_edit.setText(str(z))
            self.dock._add_gcp()

    def load_reference(self):
        layer = self.layer(
            "PolygonZ?crs=" + PROJECTED,
            ["PolygonZ((10 0 0, 80 0 0, 80 0 40, 10 0 40, 10 0 0))"],
        )
        layer.selectAll()
        self.iface.layer = layer
        self.dock._load_selected_plateau_reference()

    def project_gps(self):
        layer = self.layer("PointZ?crs=" + PROJECTED, ["PointZ(50 1 10)"])
        self.iface.layer = layer
        self.dock._project_active_point_layer()
        return layer

    def full_session(self):
        self.dock._set_section((0.0, 0.0), (100.0, 0.0))
        self.load_reference()
        self.dock._load_image(self.image_path)
        self.add_gcps()
        self.project_gps()
        self.assertTrue(self.dock.reference_records)
        self.assertIsNotNone(self.dock.transform_result)
        self.assertTrue(self.dock._gps_records())

    # geographic CRS (EPSG:6697) -----------------------------------------

    def test_geographic_project_crs_refuses_a_section_line(self):
        self.set_project_crs("EPSG:6697")
        with self.assertRaises(ValueError):
            self.dock._set_section((139.7, 35.6), (139.8, 35.6))
        self.assertIsNone(self.dock.section_p0)

    def test_geographic_project_crs_does_not_start_the_map_tool(self):
        self.set_project_crs("EPSG:6697")
        self.dock._start_section_line_map_tool()
        self.assertIsNone(self.dock.section_line_tool)
        self.assertIn("geographic", self.dock.status_label.text())

    def test_switching_to_geographic_crs_blocks_projection(self):
        self.full_session()
        self.set_project_crs("EPSG:6697")
        self.assertFalse(self.dock._projection_prerequisites_ok())
        self.assertFalse(self.dock._package_prerequisites_ok())

    # section line changes -----------------------------------------------

    def test_new_line_clears_dependent_results_when_confirmed(self):
        self.full_session()
        self.answer = True
        self.assertTrue(self.dock._set_section((0.0, 0.0), (0.0, 100.0)))
        self.assertEqual(len(self.confirm_calls), 1)
        self.assertEqual(self.dock.gcps, [])
        self.assertIsNone(self.dock.transform_result)
        self.assertEqual(self.dock.reference_records, [])
        self.assertFalse(self.dock._has_projection())
        self.assertEqual(self.dock._build_config()["section"]["p1"], [0.0, 100.0])

    def test_declining_keeps_the_old_line_and_results(self):
        self.full_session()
        self.answer = False
        self.dock.x1_edit.setText("0")
        self.dock.y1_edit.setText("100")
        self.dock._use_manual_section()
        self.assertEqual(self.dock.section_p1, (100.0, 0.0))
        self.assertEqual(self.dock.y1_edit.text(), "0")
        self.assertEqual(len(self.dock.gcps), 3)
        self.assertTrue(self.dock._has_projection())

    def test_reapplying_the_same_line_does_not_ask(self):
        self.full_session()
        self.dock._use_manual_section()
        self.assertEqual(self.confirm_calls, [])
        self.assertEqual(len(self.dock.gcps), 3)

    def test_reapplying_a_full_precision_line_does_not_ask(self):
        # Draw on map / From line / config give full-precision coordinates,
        # but the edits show 12 significant digits.
        p0 = (-8523.456789123456, -37812.12345678912)
        p1 = (-8423.456789123457, -37812.12345678911)
        self.dock._set_section(p0, p1)
        self.dock._load_image(self.image_path)
        self.add_gcps()
        self.dock._use_manual_section()
        self.assertEqual(self.confirm_calls, [])
        self.assertEqual(len(self.dock.gcps), 3)
        self.assertEqual(self.dock.section_p0, p0)

    def test_project_crs_change_blocks_projection_and_export(self):
        self.full_session()
        self.set_project_crs("EPSG:6676")
        self.assertFalse(self.dock._projection_prerequisites_ok())
        self.assertFalse(self.dock._package_prerequisites_ok())
        self.assertEqual(self.dock._build_config()["crs"], PROJECTED)
        self.set_project_crs(PROJECTED)
        self.assertTrue(self.dock._package_prerequisites_ok())

    def test_failed_drawn_line_restores_the_map_tool(self):
        from qgis.core import QgsPointXY

        self.dock._start_section_line_map_tool()
        tool = self.dock.section_line_tool
        self.assertIs(self.iface.canvas.mapTool(), tool)
        point = QgsPointXY(1.0, 1.0)
        self.dock._finish_drawn_section_line(point, point)
        self.assertIsNot(self.iface.canvas.mapTool(), tool)
        self.assertIsNone(self.dock.section_line_tool)

    # projection settings --------------------------------------------------

    def test_z_offset_change_declined_restores_the_edit(self):
        self.full_session()
        self.answer = False
        self.dock.z_offset_edit.setText("5")
        self.dock._on_projection_setting_edited()
        self.assertEqual(self.dock.z_offset_edit.text(), "0")
        self.assertEqual(len(self.dock.gcps), 3)
        self.assertEqual(self.dock._build_config()["height"]["z_offset"], 0.0)

    def test_z_offset_change_confirmed_clears_gcps_and_results(self):
        self.full_session()
        self.answer = True
        self.dock.z_offset_edit.setText("5")
        self.dock._on_projection_setting_edited()
        self.assertEqual(self.dock.gcps, [])
        self.assertIsNone(self.dock.transform_result)
        self.assertEqual(self.dock.reference_records, [])
        self.assertFalse(self.dock._has_projection())

    def test_export_with_an_unapplied_edit_asks_first(self):
        # On macOS a button click does not take focus, so the edit can still
        # be pending when Export is pressed.
        self.full_session()
        self.answer = False
        self.dock.z_offset_edit.setText("5")
        self.assertIsNone(self.dock._settings_for_use())
        self.assertEqual(len(self.confirm_calls), 1)
        self.assertEqual(self.dock._build_config()["height"]["z_offset"], 0.0)

    def test_buffer_change_keeps_gcps_but_clears_reference_and_overlay(self):
        self.full_session()
        self.answer = True
        self.dock.buffer_width_edit.setText("2")
        self.dock._on_projection_setting_edited()
        self.assertEqual(len(self.dock.gcps), 3)
        self.assertIsNotNone(self.dock.transform_result)
        self.assertEqual(self.dock.reference_records, [])
        self.assertFalse(self.dock._has_projection())

    def test_buffer_change_with_only_gcps_does_not_ask(self):
        self.dock._set_section((0.0, 0.0), (100.0, 0.0))
        self.dock._load_image(self.image_path)
        self.add_gcps()
        self.dock.buffer_width_edit.setText("2")
        self.dock._on_projection_setting_edited()
        self.assertEqual(self.confirm_calls, [])
        self.assertEqual(self.dock.applied_settings["buffer_width"], 2.0)

    # config load ----------------------------------------------------------

    def save_config(self):
        path = os.path.join(self.tempdir.name, "config.json")
        self.config_io.save_config(path, self.dock._build_config())
        return path

    def test_loading_a_config_clears_the_previous_reference(self):
        self.full_session()
        path = self.save_config()
        self.dock._apply_config(self.config_io.load_config(path), path)
        self.assertEqual(self.dock.reference_records, [])
        self.assertEqual(len(self.dock.gcps), 3)
        self.assertEqual(self.dock.gcp_table.item(0, 4).text(), "-")

    def test_config_from_another_crs_is_flagged_and_blocked(self):
        self.full_session()
        path = self.save_config()
        self.set_project_crs("EPSG:6676")
        self.dock._apply_config(self.config_io.load_config(path), path)
        self.assertEqual(self.dock.section_crs, PROJECTED)
        self.assertIn("EPSG:6677", self.dock.status_label.text())
        self.assertFalse(self.dock._package_prerequisites_ok())

    def test_geographic_config_line_is_not_restored(self):
        # Configs saved while the README recommended EPSG:6697 hold the line
        # and GCP targets in degrees.
        path = os.path.join(self.tempdir.name, "geographic.json")
        self.config_io.save_config(
            path,
            {
                "crs": "EPSG:6697",
                "section": {"p0": [139.70, 35.60], "p1": [139.71, 35.60]},
                "gcps": [{"u": u, "v": v, "s": s / 1e5, "z": z} for u, v, s, z in GCPS],
                "height": {"z_offset": 2.5},
            },
        )
        self.dock._apply_config(self.config_io.load_config(path), path)
        self.assertIsNone(self.dock.section_p0)
        self.assertEqual(self.dock.gcps, [])
        self.assertEqual(self.dock.z_offset_edit.text(), "2.5")
        self.assertIn("geographic", self.dock.status_label.text())


if __name__ == "__main__":
    unittest.main()
