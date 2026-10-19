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
        self.assertFalse(self.dock._has_projection())
        self.assertEqual(self.dock._build_config()["section"]["p1"], [0.0, 100.0])
        # The building is re-projected onto the new line: only its x=10 end
        # lies within the 10 m buffer of the line x=0.
        self.assertTrue(self.dock.reference_records)
        self.assertEqual({r["x"] for r in self.dock.reference_records}, {10.0})

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

    def test_window_shrinks_to_the_sidebar_while_drawing_on_the_map(self):
        from qgis.core import QgsPointXY

        self.dock.resize(1200, 700)
        self.dock.show()
        before = self.dock.size()
        self.dock._start_section_line_map_tool()
        self.assertLess(self.dock.width(), 500)
        self.dock._finish_drawn_section_line(QgsPointXY(0, 0), QgsPointXY(100, 0))
        self.assertEqual(self.dock.size(), before)
        self.dock.hide()

    def test_cancelling_the_map_tool_restores_the_window(self):
        self.dock.resize(1200, 700)
        self.dock.show()
        before = self.dock.size()
        self.dock._start_section_line_map_tool()
        self.dock._cancel_drawn_section_line()
        self.assertEqual(self.dock.size(), before)
        self.dock.hide()

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


    def test_gps_projection_honours_the_selection(self):
        self.full_session()
        layer = self.layer(
            "PointZ?crs=" + PROJECTED, ["PointZ(10 1 3)", "PointZ(20 1 3)", "PointZ(30 1 3)"]
        )
        layer.selectByIds([next(layer.getFeatures()).id()])
        self.iface.layer = layer
        self.dock._project_active_point_layer()
        self.assertEqual(len(self.dock.gps_results[layer.name()]["records"]), 1)

    # GCP picking flow -------------------------------------------------------

    def test_a_pick_is_used_once(self):
        self.dock._set_section((0.0, 0.0), (100.0, 0.0))
        self.dock._load_image(self.image_path)
        self.assertFalse(self.dock.add_gcp_button.isEnabled())
        self.dock._on_canvas_clicked(10.0, 20.0)
        self.dock._on_reference_clicked(1.0, 2.0)
        self.assertTrue(self.dock.add_gcp_button.isEnabled())
        self.assertEqual(len(self.dock.canvas.pending_items), 1)
        self.dock._add_gcp()
        self.assertEqual(len(self.dock.gcps), 1)
        self.assertIsNone(self.dock.current_pixel)
        self.assertEqual(self.dock.gcp_s_edit.text(), "")
        self.assertFalse(self.dock.add_gcp_button.isEnabled())
        self.assertEqual(self.dock.canvas.pending_items, [])
        # A second 'Add GCP' (or a click outside the image) has no pixel to
        # reuse.
        self.dock.gcp_s_edit.setText("5")
        self.dock.gcp_z_edit.setText("6")
        self.dock._add_gcp()
        self.assertEqual(len(self.dock.gcps), 1)

    def test_gcp_targets_are_marked_on_the_reference(self):
        self.full_session()
        self.assertEqual(len(self.dock.reference_canvas.target_items), 3)

    def test_three_gcps_explain_that_rms_is_not_a_check(self):
        self.dock._set_section((0.0, 0.0), (100.0, 0.0))
        self.dock._load_image(self.image_path)
        self.add_gcps()
        self.assertIn("add a 4th", self.dock.rms_label.text())

    def test_table_shows_millimetres(self):
        self.dock._set_section((0.0, 0.0), (100.0, 0.0))
        self.dock._load_image(self.image_path)
        self.dock._on_canvas_clicked(10.25, 20.0)
        self.dock.gcp_s_edit.setText("96.1613676812")
        self.dock.gcp_z_edit.setText("63.768")
        self.dock._add_gcp()
        texts = [self.dock.gcp_table.item(0, c).text() for c in range(4)]
        self.assertEqual(texts, ["10.2", "20.0", "96.161", "63.768"])

    # messages, hints, titles -------------------------------------------------

    def test_routine_messages_stay_out_of_the_qgis_message_bar(self):
        self.dock._set_section((0.0, 0.0), (100.0, 0.0))
        self.dock._load_image(self.image_path)
        self.assertEqual(self.iface.messages, [])
        self.dock._add_gcp()  # nothing picked: a warning
        self.assertEqual(len(self.iface.messages), 1)

    def test_hint_follows_the_step_numbers(self):
        self.assertIn("step 1", self.dock.hint_label.text())
        self.load_reference()
        self.assertIn("step 2", self.dock.hint_label.text())
        self.dock._set_section((0.0, 0.0), (100.0, 0.0))
        self.assertIn("step 3", self.dock.hint_label.text())

    def test_steps_show_progress_and_open_the_next_step(self):
        self.assertEqual(self.dock.steps.currentIndex(), 0)
        self.load_reference()
        self.assertTrue(self.dock.steps.itemText(0).startswith("✓ 1. Building"))
        self.assertEqual(self.dock.steps.currentIndex(), 1)
        self.dock._set_section((0.0, 0.0), (100.0, 0.0))
        self.assertTrue(self.dock.steps.itemText(1).startswith("✓ 2. Section line"))
        self.assertEqual(self.dock.steps.currentIndex(), 2)

    # building first, then the section line -------------------------------

    def test_building_first_then_line_loads_the_reference(self):
        layer = self.building_layer()
        fid = next(layer.getFeatures()).id()
        self.dock._set_building(layer, [fid])
        self.assertEqual(self.dock.reference_records, [])
        self.assertEqual(list(layer.selectedFeatureIds()), [fid])
        self.dock._set_section((0.0, 0.0), (100.0, 0.0))
        self.assertTrue(self.dock.reference_records)
        self.assertTrue(self.dock.reference_canvas.has_reference())

    def test_line_after_reference_reprojects_the_building(self):
        self.full_session()
        self.answer = True
        self.dock._set_section((0.0, -1.0), (100.0, -1.0))
        self.assertTrue(self.dock.reference_records)
        self.assertEqual(self.dock.reference_records[0]["d"], 1.0)

    def test_line_that_misses_the_building_says_so(self):
        self.load_reference()
        self.dock._set_section((0.0, 500.0), (100.0, 500.0))
        self.assertEqual(self.dock.reference_records, [])
        self.assertIn("does not pass within", self.dock.status_label.text())
        self.assertIn("misses the picked building", self.dock.hint_label.text())

    def test_buffer_change_reprojects_the_building(self):
        from qgis.PyQt.QtWidgets import QApplication

        self.full_session()
        self.answer = True
        self.dock.buffer_width_edit.setText("2")
        self.dock._on_projection_setting_edited()
        QApplication.processEvents()
        self.assertTrue(self.dock.reference_records)
        self.assertEqual(self.dock.applied_settings["buffer_width"], 2.0)

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

    def test_qgis_selection_can_still_be_used(self):
        layer = self.building_layer()
        layer.selectAll()
        self.iface.layer = layer
        self.dock._use_qgis_selection_as_building()
        self.assertEqual(len(self.dock.building_fids), 1)

    def test_loading_a_config_clears_the_building(self):
        self.full_session()
        path = self.save_config()
        self.dock._apply_config(self.config_io.load_config(path), path)
        self.assertEqual(self.dock.building_fids, [])

    def test_section_edits_have_no_float_noise(self):
        p0 = (-12418.485, -34142.1600002)
        self.dock._set_section(p0, (-12418.485, -34030.04))
        self.assertEqual(self.dock.y0_edit.text(), "-34142.16")
        self.dock._load_image(self.image_path)
        self.add_gcps()
        self.dock._use_manual_section()
        self.assertEqual(self.confirm_calls, [])
        self.assertEqual(self.dock.section_p0, p0)

    def test_dialog_lists_what_will_be_cleared(self):
        self.full_session()
        self.dock.z_offset_edit.setText("1.5")
        self.dock._on_projection_setting_edited()
        text = self.confirm_calls[0]
        self.assertIn("z offset: 0 → 1.5", text)
        self.assertIn("• GCPs and transform", text)


if __name__ == "__main__":
    unittest.main()
