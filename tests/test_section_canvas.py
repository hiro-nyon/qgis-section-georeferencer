import os
import tempfile
import unittest

from qgis_support import HAVE_QGIS, SKIP_REASON, plugin_module, start_qgis


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

    def tearDown(self):
        self.view.deleteLater()

    def test_snap_priority_and_screen_pixel_tolerance_at_any_zoom(self):
        for scale in (0.25, 1.0, 4.0):
            with self.subTest(scale=scale):
                # A corner wins even when the wall is closer to the pointer.
                snap = self.snap_at_screen_offset(scale, 5.0, -2.0)
                self.assertEqual((snap["s"], snap["z"]), (0.0, 0.0))
                self.assertIsNone(self.snap_at_screen_offset(scale, 0.0, -20.0))
                self.assertIsNotNone(self.snap_at_screen_offset(scale, 0.0, -8.0))
                x, y = self.view.section_to_pixel(5.0, 0.0)
                snap = self.view._snap_scene_pos(self.QPointF(x, y - 2.0 / scale))
                self.assertEqual(snap["kind"], "segment")
                self.assertAlmostEqual(snap["s"], 5.0)


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

    def tearDown(self):
        self.view.deleteLater()

    def test_export_scales_markers_and_hides_the_pending_pick(self):
        self.view.draw_gcp_markers([{"u": 2000.0, "v": 1500.0}])
        marker = self.view.gcp_items[0]
        rect = marker.deviceTransform(self.view.viewportTransform()).mapRect(marker.boundingRect())
        self.assertGreater(rect.width(), 10.0)
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

    def test_geotiff_has_local_section_crs_and_preserves_affine(self):
        from osgeo import gdal, osr
        path = os.path.join(self.tempdir.name, "section.tif")
        coeffs = {"s": [10, 0.5, 0.1], "z": [50, 0.2, -0.5]}
        self.view.export_section_geotiff(path, coeffs, "EPSG:6677")
        ds = gdal.Open(path)
        srs = osr.SpatialReference(wkt=ds.GetProjection())
        self.assertTrue(srs.IsLocal())
        self.assertIsNone(srs.GetAuthorityCode(None))
        self.assertEqual(ds.GetMetadataItem("SECTION_SOURCE_CRS"), "EPSG:6677")
        self.assertEqual(ds.GetGeoTransform(), (10, 0.5, 0.1, 50, 0.2, -0.5))
        self.assertEqual((ds.RasterXSize, ds.RasterYSize), (4000, 3000))
        ds = None
