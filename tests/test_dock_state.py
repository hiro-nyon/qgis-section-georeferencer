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
        self.iface.window.close()
        self.iface.window.deleteLater()
        QgsProject.instance().removeAllMapLayers()

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

    def building_layer(self):
        return self.layer(
            "PolygonZ?crs=" + PROJECTED,
            ["PolygonZ((10 0 0, 80 0 0, 80 0 40, 10 0 40, 10 0 0))"],
        )

    def load_reference(self):
        layer = self.building_layer()
        self.dock._set_building(layer, [f.id() for f in layer.getFeatures()])

    def project_gps(self):
        layer = self.layer("PointZ?crs=" + PROJECTED, ["PointZ(50 1 10)"])
        self.iface.layer = layer
        self.dock._project_active_point_layer()
        return layer

    def full_session(self):
        self.load_reference()
        self.dock._set_section((0.0, 0.0), (100.0, 0.0))
        self.dock._load_image(self.image_path)
        self.add_gcps()
        self.project_gps()
        self.assertTrue(self.dock.reference_records)
        self.assertIsNotNone(self.dock.transform_result)
        self.assertTrue(self.dock._gps_records())

    def test_line_change_can_be_declined_then_confirmed(self):
        self.full_session()
        self.answer = False
        self.dock.x1_edit.setText("0")
        self.dock.y1_edit.setText("100")
        self.dock._use_manual_section()
        self.assertEqual(self.dock.section_p1, (100.0, 0.0))
        self.assertEqual(self.dock.y1_edit.text(), "0")
        self.assertEqual(len(self.dock.gcps), 3)
        self.assertTrue(self.dock._has_projection())
        self.answer = True
        self.assertTrue(self.dock._set_section((0.0, 0.0), (0.0, 100.0)))
        self.assertEqual(len(self.confirm_calls), 2)
        self.assertEqual(self.dock.gcps, [])
        self.assertIsNone(self.dock.transform_result)
        self.assertFalse(self.dock._has_projection())
        self.assertEqual(self.dock._build_config()["section"]["p1"], [0.0, 100.0])
        # The building is re-projected onto the new line: only its x=10 end
        # lies within the 10 m buffer of the line x=0.
        self.assertTrue(self.dock.reference_records)
        self.assertEqual({r["x"] for r in self.dock.reference_records}, {10.0})

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
        for crs in ("EPSG:6697", "EPSG:6676"):
            with self.subTest(crs=crs):
                self.set_project_crs(crs)
                self.assertFalse(self.dock._projection_prerequisites_ok())
                self.assertFalse(self.dock._package_prerequisites_ok())
                self.assertEqual(self.dock._build_config()["crs"], PROJECTED)
                self.set_project_crs(PROJECTED)
                self.assertTrue(self.dock._package_prerequisites_ok())

    def test_offset_change_preserves_or_clears_dependent_state(self):
        self.full_session()
        self.answer = False
        self.dock.z_offset_edit.setText("5")
        self.dock._on_projection_setting_edited()
        self.assertEqual(self.dock.z_offset_edit.text(), "0")
        self.assertEqual(len(self.dock.gcps), 3)
        self.assertEqual(self.dock._build_config()["height"]["z_offset"], 0.0)
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
        from qgis.PyQt.QtWidgets import QApplication
        QApplication.processEvents()
        self.assertTrue(self.dock.reference_records)
        self.assertEqual(self.dock.applied_settings["buffer_width"], 2.0)

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
        self.assertEqual(self.dock.building_fids, [])
        self.assertFalse(self.dock._has_projection())

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

    def test_gps_projection_honours_the_selection(self):
        self.full_session()
        layer = self.layer(
            "PointZ?crs=" + PROJECTED, ["PointZ(10 1 3)", "PointZ(20 1 3)", "PointZ(30 1 3)"]
        )
        layer.selectByIds([next(layer.getFeatures()).id()])
        self.iface.layer = layer
        self.dock._project_active_point_layer()
        self.assertEqual(len(self.dock.gps_results[layer.id()]["records"]), 1)

    def test_a_pick_is_used_once(self):
        self.dock._set_section((0.0, 0.0), (100.0, 0.0))
        self.dock._load_image(self.image_path)
        self.dock._on_canvas_clicked(10.0, 20.0)
        self.dock._on_reference_clicked(1.0, 2.0)
        self.dock._add_gcp()
        self.assertEqual(len(self.dock.gcps), 1)
        self.assertIsNone(self.dock.current_pixel)
        # A second 'Add GCP' (or a click outside the image) has no pixel to
        # reuse.
        self.dock.gcp_s_edit.setText("5")
        self.dock.gcp_z_edit.setText("6")
        self.dock._add_gcp()
        self.assertEqual(len(self.dock.gcps), 1)

    def test_line_that_misses_the_building_says_so(self):
        self.load_reference()
        self.dock._set_section((0.0, 500.0), (100.0, 500.0))
        self.assertEqual(self.dock.reference_records, [])
        self.assertIn("does not pass within", self.dock.status_label.text())
        self.assertIn("misses the picked building", self.dock.hint_label.text())

    def test_pick_on_map_selects_the_clicked_building(self):
        from qgis.core import QgsRectangle
        from qgis.gui import QgsMapMouseEvent
        from qgis.PyQt.QtCore import QEvent, QPoint, Qt

        layer = self.layer(
            "Polygon?crs=" + PROJECTED,
            ["Polygon((0 0, 10 0, 10 10, 0 10, 0 0))", "Polygon((20 0, 30 0, 30 10, 20 10, 20 0))"],
        )
        canvas = self.iface.canvas
        self.iface.window.resize(600, 400)
        self.iface.window.show()
        canvas.setDestinationCrs(layer.crs())
        canvas.setLayers([layer])
        canvas.setExtent(QgsRectangle(-5, -15, 35, 25))
        canvas.refresh()
        canvas.waitWhileRendering()
        self.dock.building_layer_combo.setLayer(layer)
        self.dock._start_building_pick()
        tool = self.dock.building_pick_tool
        self.assertIs(canvas.mapTool(), tool)

        def click(x, y, modifiers=Qt.NoModifier):
            pixel = canvas.mapSettings().mapToPixel().transform(x, y)
            event = QgsMapMouseEvent(
                canvas, QEvent.MouseButtonRelease, QPoint(int(pixel.x()), int(pixel.y())),
                Qt.LeftButton, Qt.LeftButton, modifiers,
            )
            tool.canvasReleaseEvent(event)

        ids = [f.id() for f in layer.getFeatures()]
        click(25, 5, Qt.ShiftModifier)
        self.assertEqual(self.dock.building_fids, [ids[1]])
        self.assertIs(canvas.mapTool(), tool)  # Shift keeps picking
        click(5, 5)
        self.assertEqual(self.dock.building_fids, [ids[0]])
        self.assertIsNot(canvas.mapTool(), tool)
        self.assertIsNone(self.dock.building_pick_tool)
        self.iface.window.hide()
        highlight = self.dock.building_highlight
        self.assertIsNotNone(highlight)
        self.assertIs(highlight.scene(), self.iface.canvas.scene())
        # Hidden with the plugin window, shown again with it.
        self.dock.set_orientation_guide_visible(False)
        self.assertFalse(highlight.isVisible())
        self.dock.set_orientation_guide_visible(True)
        self.assertTrue(highlight.isVisible())
        self.dock._clear_plateau_reference()
        self.assertIsNone(self.dock.building_highlight)
        self.assertIsNone(highlight.scene())

    def test_footprint_of_lod2_faces(self):
        from qgis.core import QgsGeometry

        # Roof face plus vertical wall faces that are zero-area slivers in 2D.
        building = QgsGeometry.fromWkt(
            "MultiPolygonZ(((0 0 10, 10 0 10, 10 8 10, 0 8 10, 0 0 10)),"
            "((0 0 0, 10 0 0, 10 0 10, 0 0 10, 0 0 0)),"
            "((10 0 0, 10 8 0, 10 8 10, 10 0 10, 10 0 0)))"
        )
        footprint = self.dock_module.building_footprint([building])
        self.assertAlmostEqual(footprint.area(), 80.0)
        self.assertFalse(footprint.constGet().is3D())
        self.assertIsNone(self.dock_module.building_footprint([]))

    def test_gcp_edit_invalidates_both_projection_types_and_reprojection_matches(self):
        self.full_session()
        self.iface.layer = self.dock._building_layer()
        self.dock._project_active_vector_layer()
        self.assertTrue(self.dock.plateau_results)
        self.dock.current_pixel = (10.0, 10.0)
        self.dock.gcp_s_edit.setText("0")
        self.dock.gcp_z_edit.setText("60")
        self.dock._add_gcp()
        self.assertFalse(self.dock._has_projection())
        self.assertFalse(self.dock.canvas.projected_items)
        self.project_gps()
        record = self.dock._gps_records()[0]
        expected = plugin_module("transforms").section_to_pixel(
            record["s"], record["z_section"], self.dock.transform_result["section_to_pixel"]
        )
        self.assertAlmostEqual(record["u"], expected[0])
        self.assertAlmostEqual(record["v"], expected[1])

    def test_deleting_or_clearing_gcps_clears_projection(self):
        self.full_session()
        self.dock.gcp_table.setCurrentCell(0, 0)
        self.dock._delete_selected_gcp()
        self.assertFalse(self.dock._has_projection())
        self.assertIsNone(self.dock.transform_result)
        self.full_session()
        self.dock._clear_gcps()
        self.assertFalse(self.dock._has_projection())

    def test_failed_fit_clears_projection(self):
        self.full_session()
        self.dock.gcps = [{"u": 0, "v": 0, "s": 0, "z": 0}] * 3
        self.dock._compute_transform()
        self.assertIsNone(self.dock.transform_result)
        self.assertFalse(self.dock._has_projection())

    def test_same_names_and_layer_rename_do_not_lose_or_duplicate_rows(self):
        self.full_session()
        first = self.iface.layer
        second = self.layer("PointZ?crs=" + PROJECTED, ["PointZ(70 1 20)"])
        second.setName(first.name())
        self.iface.layer = second
        self.dock._project_active_point_layer()
        self.assertEqual({r["x"] for r in self.dock._gps_records()}, {50, 70})
        second.setName("renamed")
        self.dock._project_active_point_layer()
        self.assertEqual(len(self.dock._gps_records()), 2)
        self.assertEqual({r["source_layer_id"] for r in self.dock._gps_records()}, {first.id(), second.id()})
        summary = self.dock._build_projection_summary("test")
        self.assertEqual(summary["source_layers"]["gps"], sorted([first.name(), "renamed"]))
        self.assertEqual(summary["gps"]["processed"], 2)

    def test_same_named_vector_layers_are_distinct(self):
        self.full_session()
        for _ in range(2):
            self.iface.layer = self.building_layer()
            self.dock._project_active_vector_layer()
        self.assertEqual(len(self.dock.plateau_results), 2)
        self.assertEqual(len(self.dock._plateau_records()), 10)

    def test_clear_then_new_image_requires_new_gcps(self):
        self.full_session()
        self.dock._clear_image()
        from qgis.PyQt.QtGui import QImage
        path = os.path.join(self.tempdir.name, "new.png")
        QImage(200, 200, QImage.Format_RGB32).save(path)
        self.dock._load_image(path)
        self.assertEqual(self.dock.gcps, [])
        self.assertIsNone(self.dock.transform_result)
        self.assertFalse(self.dock._package_prerequisites_ok())

    def test_config_without_image_clears_the_canvas(self):
        self.full_session()
        data = self.dock._build_config()
        data["image"]["path"] = ""
        self.assertTrue(self.dock._apply_config(data, "/tmp/config.json"))
        self.assertFalse(self.dock.canvas.has_image())
        self.assertFalse(self.dock._package_prerequisites_ok())
        # Retained config GCPs must not silently attach to an arbitrary image.
        self.assertTrue(self.dock._image_replaces_state(self.image_path))

    def test_unreadable_config_image_does_not_keep_previous_image(self):
        self.full_session()
        data = self.dock._build_config()
        path = os.path.join(self.tempdir.name, "broken.png")
        with open(path, "w") as stream:
            stream.write("not an image")
        data["image"]["path"] = path
        self.assertTrue(self.dock._apply_config(data, "/tmp/config.json"))
        self.assertFalse(self.dock.canvas.has_image())
        self.assertIn("could not be loaded", self.dock.status_label.text())

    def test_malformed_config_keeps_the_entire_session(self):
        self.full_session()
        before = self.dock._build_config()
        rows = self.dock._gps_records()
        for data in ({"section": "oops"}, {"image": {"path": []}}, {"crs": "EPSG:invalid"},
                     {"gcps": [{"u": 1}]}, {"transform": {"pixel_to_section": {}}}):
            with self.subTest(data=data):
                self.assertFalse(self.dock._apply_config(data, "/tmp/bad.json"))
                self.assertEqual(self.dock._build_config(), before)
                self.assertEqual(self.dock._gps_records(), rows)
                self.assertTrue(self.dock.canvas.has_image())

    def test_invalid_source_crs_keeps_previous_projection(self):
        from qgis.core import QgsCoordinateReferenceSystem
        self.full_session()
        previous = self.dock._gps_records()
        self.iface.layer.setCrs(QgsCoordinateReferenceSystem())
        self.dock._project_active_point_layer()
        self.assertEqual(self.dock._gps_records(), previous)
        self.assertIn("no valid CRS", self.dock.status_label.text())

    def test_pending_offset_edit_cannot_project_with_a_cleared_transform(self):
        self.full_session()
        self.dock.z_offset_edit.setText("5")
        self.dock._project_active_point_layer()
        self.assertIsNone(self.dock.transform_result)
        self.assertFalse(self.dock._has_projection())

    def test_section_creation_requires_a_metric_crs(self):
        for crs in ("EPSG:6697", "EPSG:2263"):
            with self.subTest(crs=crs):
                self.set_project_crs(crs)
                with self.assertRaises(ValueError):
                    self.dock._set_section((0, 0), (100, 0))
                self.assertIsNone(self.dock.section_p0)
                self.dock._start_section_line_map_tool()
                self.assertIsNone(self.dock.section_line_tool)

    def test_map_drawing_restores_window_and_tool_after_any_exit(self):
        from qgis.core import QgsPointXY

        self.dock.resize(1200, 700)
        self.dock.show()
        before = self.dock.size()
        for outcome in ("finish", "cancel", "invalid"):
            with self.subTest(outcome=outcome):
                self.dock._start_section_line_map_tool()
                tool = self.dock.section_line_tool
                self.assertIs(self.iface.canvas.mapTool(), tool)
                self.assertLess(self.dock.width(), 500)
                if outcome == "cancel":
                    self.dock._cancel_drawn_section_line()
                else:
                    end = QgsPointXY(100, 0) if outcome == "finish" else QgsPointXY(0, 0)
                    self.dock._finish_drawn_section_line(QgsPointXY(0, 0), end)
                self.assertEqual(self.dock.size(), before)
                self.assertIsNone(self.dock.section_line_tool)
                self.assertIsNot(self.iface.canvas.mapTool(), tool)
        self.dock.hide()
