import os
import tempfile
import unittest

from qgis_support import HAVE_QGIS, SKIP_REASON, plugin_module, start_qgis


def _candidate(kind, x, y):
    return {"kind": kind, "x": x, "y": y, "s": x, "z": y}


@unittest.skipUnless(HAVE_QGIS, SKIP_REASON)
class PureHelperTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        start_qgis()
        cls.canvas = plugin_module("section_canvas")

    def test_corner_wins_over_a_closer_point_on_the_wall(self):
        candidates = [
            _candidate("vertex", 0.0, 0.0),
            _candidate("segment", 5.0, 0.0),  # foot of the pointer on the wall
        ]
        snap = self.canvas.choose_snap(candidates, 5.0, 2.0, 12.0)
        self.assertEqual(snap["kind"], "vertex")

    def test_wall_is_used_when_no_corner_is_in_reach(self):
        candidates = [
            _candidate("vertex", 0.0, 0.0),
            _candidate("segment", 30.0, 0.0),
        ]
        snap = self.canvas.choose_snap(candidates, 30.0, 2.0, 12.0)
        self.assertEqual(snap["kind"], "segment")

    def test_nothing_in_reach_does_not_snap(self):
        candidates = [_candidate("vertex", 0.0, 0.0)]
        self.assertIsNone(self.canvas.choose_snap(candidates, 50.0, 50.0, 12.0))

    def test_nice_step(self):
        self.assertEqual(self.canvas.nice_step(100.0), 20.0)
        self.assertEqual(self.canvas.nice_step(7.0), 2.0)
        self.assertAlmostEqual(self.canvas.nice_step(0.9), 0.2)

    def test_pyramid_level_index(self):
        index = self.canvas.pyramid_level_index
        self.assertEqual(index(1.0, 5), -1)
        self.assertEqual(index(0.5, 5), -1)
        self.assertEqual(index(0.3, 5), 0)  # half resolution
        self.assertEqual(index(0.1, 5), 2)  # 1/8 still >= 1 px per screen px
        self.assertEqual(index(0.001, 3), 2)  # clamped to the coarsest level
        self.assertEqual(index(0.1, 0), -1)


@unittest.skipUnless(HAVE_QGIS, SKIP_REASON)
class ReferenceSnapTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        start_qgis()
        cls.canvas_module = plugin_module("section_canvas")

    def setUp(self):
        from qgis.PyQt.QtCore import QPointF

        self.QPointF = QPointF
        self.view = self.canvas_module.SectionReferenceCanvas()
        self.view.resize(600, 400)
        records = [
            {"s": s, "z_section": z, "inside_buffer": True}
            for s, z in ((0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0))
        ]
        segments = [
            {"s0": 0.0, "z0": 0.0, "s1": 10.0, "z1": 0.0},
            {"s0": 10.0, "z0": 0.0, "s1": 10.0, "z1": 10.0},
        ]
        self.view.load_reference(records, segments)

    def snap_at_screen_offset(self, scale, dx_px, dy_px):
        """Snap for a pointer dx/dy screen pixels away from corner (0, 0)."""
        self.view.resetTransform()
        self.view.scale(scale, scale)
        x, y = self.view.section_to_pixel(0.0, 0.0)
        scene = self.QPointF(x + dx_px / scale, y + dy_px / scale)
        return self.view._snap_scene_pos(scene)

    def test_near_a_corner_snaps_to_the_corner(self):
        snap = self.snap_at_screen_offset(1.0, 5.0, -2.0)
        self.assertEqual((snap["s"], snap["z"]), (0.0, 0.0))

    def test_tolerance_is_in_screen_pixels_when_zoomed_in(self):
        # 20 screen px away is out of reach at any zoom; scene-unit
        # tolerance used to grab it when zoomed in.
        self.assertIsNone(self.snap_at_screen_offset(4.0, 0.0, -20.0))
        self.assertIsNotNone(self.snap_at_screen_offset(4.0, 0.0, -8.0))

    def test_tolerance_is_in_screen_pixels_when_zoomed_out(self):
        self.assertIsNotNone(self.snap_at_screen_offset(0.25, 0.0, -8.0))

    def test_gcp_targets_and_pending_target_are_drawn(self):
        self.view.draw_gcp_targets([{"s": 0.0, "z": 0.0}, {"s": 10.0, "z": 10.0}])
        self.assertEqual(len(self.view.target_items), 2)
        self.view.set_pending_target(5.0, 5.0)
        self.assertEqual(len(self.view.pending_items), 1)
        self.view.clear_pending_target()
        self.assertEqual(self.view.pending_items, [])


@unittest.skipUnless(HAVE_QGIS, SKIP_REASON)
class ImageOverlayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        start_qgis()
        cls.canvas_module = plugin_module("section_canvas")
        from qgis.PyQt.QtGui import QColor, QImage

        cls.tempdir = tempfile.TemporaryDirectory()
        cls.image_path = os.path.join(cls.tempdir.name, "scan.png")
        image = QImage(4000, 3000, QImage.Format_RGB32)
        image.fill(QColor("white"))
        image.save(cls.image_path)

    @classmethod
    def tearDownClass(cls):
        cls.tempdir.cleanup()

    def setUp(self):
        self.view = self.canvas_module.SectionCanvas()
        self.view.resize(500, 400)
        self.view.load_image(self.image_path)

    def count_pixels(self, path, predicate, center, radius):
        from qgis.PyQt.QtGui import QColor, QImage

        image = QImage(path)
        cx, cy = center
        count = 0
        for x in range(cx - radius, cx + radius):
            for y in range(cy - radius, cy + radius):
                if predicate(QColor(image.pixel(x, y))):
                    count += 1
        return count

    def test_markers_keep_their_screen_size(self):
        from qgis.PyQt.QtWidgets import QGraphicsItem

        self.view.draw_gcp_markers([{"u": 2000.0, "v": 1500.0}])
        marker = self.view.gcp_items[0]
        self.assertTrue(marker.flags() & QGraphicsItem.ItemIgnoresTransformations)
        # Fit view of a 4000 px image in a 500 px widget is ~0.12x; the
        # marker is still 12 screen pixels across.
        rect = marker.deviceTransform(self.view.viewportTransform()).mapRect(
            marker.boundingRect()
        )
        self.assertGreater(rect.width(), 10.0)

    def test_export_scales_markers_and_hides_the_pending_pick(self):
        self.view.draw_gcp_markers([{"u": 2000.0, "v": 1500.0}])
        self.view.set_pending_pixel(1000.0, 1000.0)
        path = os.path.join(self.tempdir.name, "preview.png")
        self.view.export_png(path)

        def red(color):
            return color.red() > 150 and color.green() < 120 and color.blue() < 120

        def orange(color):
            return color.red() > 200 and 60 < color.green() < 190 and color.blue() < 80

        # 12 px marker x (4000 / 1500) ~ 32 px across in the export.
        self.assertGreater(self.count_pixels(path, red, (2000, 1500), 40), 150)
        self.assertEqual(self.count_pixels(path, orange, (1000, 1000), 40), 0)
        self.assertEqual(self.view.gcp_items[0].scale(), 1.0)
        self.assertTrue(self.view.pending_items[0].isVisible())

    def test_zoomed_out_view_draws_a_downscaled_level(self):
        item = self.view.image_item
        levels = item.levels()
        self.assertGreaterEqual(len(levels), 3)
        self.assertEqual(levels[0].width(), 2000)


if __name__ == "__main__":
    unittest.main()
