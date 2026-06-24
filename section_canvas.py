"""QGraphicsView canvas used to display section images and overlays."""

from __future__ import annotations

from qgis.PyQt.QtCore import QPointF, QRectF, Qt, pyqtSignal
from qgis.PyQt.QtGui import QBrush, QColor, QImage, QPainter, QPen, QPixmap
from qgis.PyQt.QtWidgets import QGraphicsEllipseItem, QGraphicsLineItem, QGraphicsScene, QGraphicsTextItem, QGraphicsView


PDF_RENDER_DPI = 600


class SectionCanvas(QGraphicsView):
    """Section drawing canvas with image, GCP, and projection overlays."""

    clickedPixel = pyqtSignal(float, float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)
        self.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
        self.setDragMode(QGraphicsView.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.AnchorViewCenter)
        self.image_item = None
        self.image_path = None
        self.gcp_items = []
        self.projected_items = []
        self.reference_overlay_items = []

    def has_image(self):
        return self.image_item is not None

    def load_image(self, path):
        if str(path).lower().endswith(".pdf"):
            pixmap = _load_pdf_as_pixmap(path)
        else:
            pixmap = QPixmap(path)
        if pixmap.isNull():
            raise ValueError("Could not load image: {}".format(path))
        self.clear_image()
        self.image_path = path
        self.image_item = self._scene.addPixmap(pixmap)
        self.image_item.setPos(0, 0)
        self.image_item.setZValue(-100)
        self._scene.setSceneRect(QRectF(0, 0, pixmap.width(), pixmap.height()))
        self.fit_to_image()

    def clear_image(self):
        self._scene.clear()
        self.image_item = None
        self.image_path = None
        self.gcp_items = []
        self.projected_items = []
        self.reference_overlay_items = []

    def clear_gcp_markers(self):
        for item in self.gcp_items:
            self._scene.removeItem(item)
        self.gcp_items = []

    def clear_projected_overlays(self):
        for item in self.projected_items:
            self._scene.removeItem(item)
        self.projected_items = []

    def clear_reference_overlay(self):
        for item in self.reference_overlay_items:
            self._scene.removeItem(item)
        self.reference_overlay_items = []

    def clear_all_overlays(self):
        self.clear_gcp_markers()
        self.clear_projected_overlays()
        self.clear_reference_overlay()

    def fit_to_image(self):
        if self.image_item is not None:
            self.fitInView(self.image_item.boundingRect(), Qt.KeepAspectRatio)

    def image_size(self):
        if self.image_item is None:
            return None
        pixmap = self.image_item.pixmap()
        return int(pixmap.width()), int(pixmap.height())

    def export_background_png(self, path):
        if self.image_item is None:
            raise ValueError("No section image is loaded.")
        if self.image_path and not str(self.image_path).lower().endswith(".pdf"):
            try:
                from osgeo import gdal
            except ImportError:
                gdal = None
            if gdal is not None and _export_source_raster_png(
                self.image_path, path, gdal
            ):
                return
        pixmap = self.image_item.pixmap()
        if pixmap.isNull() or not pixmap.save(path, "PNG"):
            raise ValueError("Could not export background PNG: {}".format(path))

    def export_section_geotiff(self, path, pixel_to_section_coeffs, crs_auth_id=None):
        """Export the background image as a section-space GeoTIFF.

        GeoTransform coordinates are section coordinates:
        X = s, Y = z_section. This is intentionally not a true map x/y raster.
        The section line in the package config is required to reconstruct the
        corresponding 3D world/project CRS relation.
        """
        if self.image_item is None:
            raise ValueError("No section image is loaded.")
        try:
            from osgeo import gdal, osr
        except ImportError as exc:
            raise ValueError(
                "GDAL Python bindings are required to export GeoTIFF. "
                "They are included with QGIS."
            ) from exc

        pixmap = self.image_item.pixmap()
        if pixmap.isNull():
            raise ValueError("No section image is loaded.")

        import os
        import tempfile

        if self.image_path and not str(self.image_path).lower().endswith(".pdf"):
            if _export_source_raster_geotiff(
                self.image_path,
                path,
                pixel_to_section_coeffs,
                crs_auth_id,
                gdal,
                osr,
            ):
                return

        temp_fd, temp_path = tempfile.mkstemp(suffix=".png")
        os.close(temp_fd)
        try:
            if not pixmap.save(temp_path, "PNG"):
                raise ValueError("Could not prepare temporary PNG for GeoTIFF export.")
            src = gdal.Open(temp_path)
            if src is None:
                raise ValueError("GDAL could not open temporary PNG.")
            driver = gdal.GetDriverByName("GTiff")
            if driver is None:
                raise ValueError("GDAL GTiff driver is not available.")
            dst = driver.CreateCopy(str(path), src, strict=0, options=["COMPRESS=LZW"])
            src = None
            if dst is None:
                raise ValueError("Could not create GeoTIFF: {}".format(path))

            _apply_section_georeference(
                dst, pixel_to_section_coeffs, crs_auth_id, osr
            )
            dst.FlushCache()
            dst = None
        finally:
            try:
                os.remove(temp_path)
            except OSError:
                pass

    def draw_gcp_markers(self, gcps):
        self.clear_gcp_markers()
        for index, gcp in enumerate(gcps, start=1):
            self._add_marker(
                float(gcp["u"]),
                float(gcp["v"]),
                QColor(220, 30, 30),
                7.0,
                self.gcp_items,
                label=str(index),
            )

    def draw_projected_points(self, records, color=None):
        if color is None:
            color = QColor(30, 120, 255)
        for record in records:
            self._add_marker(
                float(record["u"]),
                float(record["v"]),
                color,
                5.0,
                self.projected_items,
            )

    def draw_projected_vertices(self, records, color=None):
        if color is None:
            color = QColor(30, 170, 90)
        for record in records:
            self._add_marker(
                float(record["u"]),
                float(record["v"]),
                color,
                4.0,
                self.projected_items,
            )

    def draw_projected_segments(self, segments, color=None):
        if color is None:
            color = QColor(255, 145, 0)
        pen = QPen(color)
        pen.setWidthF(1.5)
        for segment in segments:
            line = QGraphicsLineItem(
                float(segment["u0"]),
                float(segment["v0"]),
                float(segment["u1"]),
                float(segment["v1"]),
            )
            line.setPen(pen)
            line.setZValue(15)
            self._scene.addItem(line)
            self.projected_items.append(line)

    def draw_reference_overlay(self, records, segments, section_to_pixel_coeffs):
        """Draw PLATEAU reference as semi-transparent overlay using the affine transform."""
        self.clear_reference_overlay()
        u_c = section_to_pixel_coeffs["u"]
        v_c = section_to_pixel_coeffs["v"]

        seg_pen = QPen(QColor(255, 145, 0, 140))
        seg_pen.setWidthF(2.0)
        for seg in segments or []:
            if None in (seg.get("s0"), seg.get("z0"), seg.get("s1"), seg.get("z1")):
                continue
            s0, z0 = float(seg["s0"]), float(seg["z0"])
            s1, z1 = float(seg["s1"]), float(seg["z1"])
            u0 = u_c[0] + u_c[1] * s0 + u_c[2] * z0
            v0 = v_c[0] + v_c[1] * s0 + v_c[2] * z0
            u1 = u_c[0] + u_c[1] * s1 + u_c[2] * z1
            v1 = v_c[0] + v_c[1] * s1 + v_c[2] * z1
            line = QGraphicsLineItem(u0, v0, u1, v1)
            line.setPen(seg_pen)
            line.setZValue(10)
            self._scene.addItem(line)
            self.reference_overlay_items.append(line)

        pt_pen = QPen(QColor(30, 170, 90, 160))
        pt_pen.setWidthF(1.2)
        pt_brush = QBrush(QColor(30, 170, 90, 80))
        for rec in records or []:
            if rec.get("inside_buffer") is False:
                continue
            if rec.get("s") is None or rec.get("z_section") is None:
                continue
            s, z = float(rec["s"]), float(rec["z_section"])
            u = u_c[0] + u_c[1] * s + u_c[2] * z
            v = v_c[0] + v_c[1] * s + v_c[2] * z
            r = 5.0
            ellipse = QGraphicsEllipseItem(u - r / 2, v - r / 2, r, r)
            ellipse.setPen(pt_pen)
            ellipse.setBrush(pt_brush)
            ellipse.setZValue(11)
            self._scene.addItem(ellipse)
            self.reference_overlay_items.append(ellipse)

    def export_png(self, path):
        rect = self._scene.sceneRect()
        if rect.isEmpty() or self.image_item is None:
            raise ValueError("No section image is loaded.")
        image = QImage(
            int(rect.width()),
            int(rect.height()),
            QImage.Format_ARGB32_Premultiplied,
        )
        image.fill(Qt.white)
        painter = QPainter(image)
        painter.setRenderHints(
            QPainter.Antialiasing
            | QPainter.TextAntialiasing
            | QPainter.SmoothPixmapTransform
        )
        try:
            self._scene.render(painter, QRectF(image.rect()), rect)
        finally:
            painter.end()
        if not image.save(path, "PNG"):
            raise ValueError("Could not export PNG: {}".format(path))

    def mousePressEvent(self, event):
        if self.image_item is not None and event.button() == Qt.LeftButton:
            scene_pos = self.mapToScene(event.pos())
            rect = self.image_item.boundingRect()
            if rect.contains(QPointF(scene_pos.x(), scene_pos.y())):
                self.clickedPixel.emit(float(scene_pos.x()), float(scene_pos.y()))
        super().mousePressEvent(event)

    def wheelEvent(self, event):
        zoom_in = 1.15
        zoom_out = 1.0 / zoom_in
        factor = zoom_in if event.angleDelta().y() > 0 else zoom_out
        self.scale(factor, factor)

    def _add_marker(self, u, v, color, size, store, label=None):
        half = size / 2.0
        pen = QPen(color)
        pen.setWidthF(1.3)
        brush = QColor(color)
        brush.setAlpha(120)
        ellipse = QGraphicsEllipseItem(u - half, v - half, size, size)
        ellipse.setPen(pen)
        ellipse.setBrush(QBrush(brush))
        ellipse.setZValue(20)
        self._scene.addItem(ellipse)
        store.append(ellipse)

        line_h = QGraphicsLineItem(u - size, v, u + size, v)
        line_v = QGraphicsLineItem(u, v - size, u, v + size)
        line_h.setPen(pen)
        line_v.setPen(pen)
        line_h.setZValue(21)
        line_v.setZValue(21)
        self._scene.addItem(line_h)
        self._scene.addItem(line_v)
        store.extend([line_h, line_v])

        if label is not None:
            text = QGraphicsTextItem(label)
            text.setDefaultTextColor(color)
            text.setPos(u + size, v + size)
            text.setZValue(22)
            self._scene.addItem(text)
            store.append(text)


def _load_pdf_as_pixmap(path):
    """Convert a PDF to QPixmap via GDAL raster rendering."""
    import os
    import tempfile

    try:
        from osgeo import gdal
    except ImportError:
        raise ValueError(
            "GDAL Python bindings are required to load PDF files. "
            "They are included with QGIS."
        )

    old_dpi = gdal.GetConfigOption("GDAL_PDF_DPI")
    try:
        gdal.SetConfigOption("GDAL_PDF_DPI", str(PDF_RENDER_DPI))
        ds = gdal.Open(str(path))
        if ds is None:
            raise ValueError(
                "Could not open PDF with GDAL. "
                "Ensure the GDAL PDF driver (Poppler or PDFium) is available."
            )
        temp_fd, temp_path = tempfile.mkstemp(suffix=".png")
        os.close(temp_fd)
        try:
            gdal.Translate(temp_path, ds, format="PNG")
            ds = None
            pixmap = QPixmap(temp_path)
        finally:
            ds = None
            try:
                os.remove(temp_path)
            except OSError:
                pass
    finally:
        gdal.SetConfigOption("GDAL_PDF_DPI", old_dpi)
    return pixmap


def _export_source_raster_geotiff(
    source_path,
    output_path,
    pixel_to_section_coeffs,
    crs_auth_id,
    gdal,
    osr,
):
    src = gdal.Open(str(source_path))
    if src is None:
        return False
    driver = gdal.GetDriverByName("GTiff")
    if driver is None:
        src = None
        return False
    dst = driver.CreateCopy(str(output_path), src, strict=0, options=["COMPRESS=LZW"])
    src = None
    if dst is None:
        return False
    _apply_section_georeference(dst, pixel_to_section_coeffs, crs_auth_id, osr)
    dst.FlushCache()
    dst = None
    return True


def _export_source_raster_png(source_path, output_path, gdal):
    src = gdal.Open(str(source_path))
    if src is None:
        return False
    try:
        result = gdal.Translate(str(output_path), src, format="PNG")
        if result is None:
            return False
        result.FlushCache()
        result = None
        return True
    finally:
        src = None


def _apply_section_georeference(dst, pixel_to_section_coeffs, crs_auth_id, osr):
    s_coeffs = pixel_to_section_coeffs["s"]
    z_coeffs = pixel_to_section_coeffs["z"]
    dst.SetGeoTransform(
        (
            float(s_coeffs[0]),
            float(s_coeffs[1]),
            float(s_coeffs[2]),
            float(z_coeffs[0]),
            float(z_coeffs[1]),
            float(z_coeffs[2]),
        )
    )
    if crs_auth_id:
        srs = osr.SpatialReference()
        if srs.SetFromUserInput(str(crs_auth_id)) == 0:
            dst.SetProjection(srs.ExportToWkt())
    dst.SetMetadataItem("SECTION_COORDINATE_MODEL", "X=s_along_section,Y=z_section")
    dst.SetMetadataItem(
        "SECTION_GEOREFERENCE_NOTE",
        "This GeoTIFF is georeferenced in section coordinates, not map x/y.",
    )


class SectionReferenceCanvas(QGraphicsView):
    """Reference canvas for selected PLATEAU/CAD geometry in section coordinates."""

    clickedSection = pyqtSignal(float, float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)
        self.setRenderHints(QPainter.Antialiasing)
        self.setDragMode(QGraphicsView.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.AnchorViewCenter)
        self.setMouseTracking(True)
        self.viewport().setMouseTracking(True)
        self.records = []
        self.segments = []
        self.bounds = None
        self.reference_scale = 1.0
        self.margin = 30.0
        self.snap_tolerance_px = 12.0
        self.snap_enabled = True
        self.snap_marker = None
        self.last_snap = None

    def clear_reference(self):
        self._scene.clear()
        self.records = []
        self.segments = []
        self.bounds = None
        self.snap_marker = None
        self.last_snap = None

    def has_reference(self):
        return bool(self.records or self.segments)

    def set_snap_enabled(self, enabled):
        self.snap_enabled = bool(enabled)
        if not self.snap_enabled:
            self.last_snap = None
            self._clear_snap_marker()

    def load_reference(self, records, segments=None):
        self.clear_reference()
        self.records = list(records or [])
        self.segments = list(segments or [])
        self.bounds = self._compute_bounds()
        self._draw_reference()
        self.fit_to_reference()

    def fit_to_reference(self):
        rect = self._scene.sceneRect()
        if not rect.isEmpty():
            self.fitInView(rect, Qt.KeepAspectRatio)

    def mousePressEvent(self, event):
        if self.bounds is not None and event.button() == Qt.LeftButton:
            scene_pos = self.mapToScene(event.pos())
            snap = self._snap_scene_pos(scene_pos) if self.snap_enabled else None
            if snap is not None:
                s, z = snap["s"], snap["z"]
            else:
                s, z = self.pixel_to_section(scene_pos.x(), scene_pos.y())
            self.clickedSection.emit(float(s), float(z))
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self.bounds is not None:
            scene_pos = self.mapToScene(event.pos())
            snap = self._snap_scene_pos(scene_pos) if self.snap_enabled else None
            self.last_snap = snap
            if not self.snap_enabled:
                self._clear_snap_marker()
            elif snap is not None:
                self._show_snap_marker(snap["x"], snap["y"], True)
            else:
                self._show_snap_marker(scene_pos.x(), scene_pos.y(), False)
        super().mouseMoveEvent(event)

    def wheelEvent(self, event):
        zoom_in = 1.15
        zoom_out = 1.0 / zoom_in
        factor = zoom_in if event.angleDelta().y() > 0 else zoom_out
        self.scale(factor, factor)

    def section_to_pixel(self, s, z):
        min_s, _max_s, _min_z, max_z = self.bounds
        x = self.margin + (float(s) - min_s) * self.reference_scale
        y = self.margin + (max_z - float(z)) * self.reference_scale
        return x, y

    def pixel_to_section(self, x, y):
        min_s, _max_s, _min_z, max_z = self.bounds
        s = ((float(x) - self.margin) / self.reference_scale) + min_s
        z = max_z - ((float(y) - self.margin) / self.reference_scale)
        return s, z

    def _compute_bounds(self):
        s_values = []
        z_values = []
        for record in self.records:
            if record.get("inside_buffer") is False:
                continue
            if record.get("s") is None or record.get("z_section") is None:
                continue
            s_values.append(float(record["s"]))
            z_values.append(float(record["z_section"]))
        for segment in self.segments:
            for s_key, z_key in (("s0", "z0"), ("s1", "z1")):
                if segment.get(s_key) is None or segment.get(z_key) is None:
                    continue
                s_values.append(float(segment[s_key]))
                z_values.append(float(segment[z_key]))
        if not s_values or not z_values:
            raise ValueError("Selected PLATEAU/CAD geometry has no vertices inside the section buffer.")
        min_s = min(s_values)
        max_s = max(s_values)
        min_z = min(z_values)
        max_z = max(z_values)
        if min_s == max_s:
            min_s -= 1.0
            max_s += 1.0
        if min_z == max_z:
            min_z -= 1.0
            max_z += 1.0
        span_s = max_s - min_s
        span_z = max_z - min_z
        target_width = 900.0
        target_height = 420.0
        self.reference_scale = min(target_width / span_s, target_height / span_z)
        self.reference_scale = max(self.reference_scale, 0.1)
        return min_s, max_s, min_z, max_z

    def _draw_reference(self):
        if self.bounds is None:
            return
        min_s, max_s, min_z, max_z = self.bounds
        width = (max_s - min_s) * self.reference_scale + self.margin * 2.0
        height = (max_z - min_z) * self.reference_scale + self.margin * 2.0
        self._scene.setSceneRect(QRectF(0, 0, width, height))

        axis_pen = QPen(QColor(120, 120, 120))
        axis_pen.setWidthF(1.0)
        bottom_y = self.margin + (max_z - min_z) * self.reference_scale
        self._scene.addLine(self.margin, bottom_y, width - self.margin, bottom_y, axis_pen)
        self._scene.addLine(self.margin, self.margin, self.margin, bottom_y, axis_pen)

        segment_pen = QPen(QColor(255, 145, 0))
        segment_pen.setWidthF(1.4)
        for segment in self.segments:
            if None in (
                segment.get("s0"),
                segment.get("z0"),
                segment.get("s1"),
                segment.get("z1"),
            ):
                continue
            x0, y0 = self.section_to_pixel(segment["s0"], segment["z0"])
            x1, y1 = self.section_to_pixel(segment["s1"], segment["z1"])
            self._scene.addLine(x0, y0, x1, y1, segment_pen)

        point_pen = QPen(QColor(30, 170, 90))
        point_pen.setWidthF(1.2)
        point_brush = QBrush(QColor(30, 170, 90, 110))
        for record in self.records:
            if record.get("inside_buffer") is False:
                continue
            if record.get("s") is None or record.get("z_section") is None:
                continue
            x, y = self.section_to_pixel(record["s"], record["z_section"])
            item = QGraphicsEllipseItem(x - 3.0, y - 3.0, 6.0, 6.0)
            item.setPen(point_pen)
            item.setBrush(point_brush)
            self._scene.addItem(item)

    def _snap_scene_pos(self, scene_pos):
        candidates = []
        x = float(scene_pos.x())
        y = float(scene_pos.y())

        for record in self.records:
            if record.get("inside_buffer") is False:
                continue
            if record.get("s") is None or record.get("z_section") is None:
                continue
            px, py = self.section_to_pixel(record["s"], record["z_section"])
            candidates.append(
                {
                    "x": px,
                    "y": py,
                    "s": float(record["s"]),
                    "z": float(record["z_section"]),
                    "kind": "vertex",
                }
            )

        for segment in self.segments:
            if None in (
                segment.get("s0"),
                segment.get("z0"),
                segment.get("s1"),
                segment.get("z1"),
            ):
                continue
            x0, y0 = self.section_to_pixel(segment["s0"], segment["z0"])
            x1, y1 = self.section_to_pixel(segment["s1"], segment["z1"])
            candidates.append(
                {
                    "x": x0,
                    "y": y0,
                    "s": float(segment["s0"]),
                    "z": float(segment["z0"]),
                    "kind": "segment_endpoint",
                }
            )
            candidates.append(
                {
                    "x": x1,
                    "y": y1,
                    "s": float(segment["s1"]),
                    "z": float(segment["z1"]),
                    "kind": "segment_endpoint",
                }
            )
            closest = _closest_point_on_segment(x, y, x0, y0, x1, y1)
            if closest is not None:
                cx, cy = closest
                s, z = self.pixel_to_section(cx, cy)
                candidates.append(
                    {
                        "x": cx,
                        "y": cy,
                        "s": s,
                        "z": z,
                        "kind": "segment",
                    }
                )

        best = None
        best_dist2 = self.snap_tolerance_px * self.snap_tolerance_px
        for candidate in candidates:
            dx = candidate["x"] - x
            dy = candidate["y"] - y
            dist2 = dx * dx + dy * dy
            if dist2 <= best_dist2:
                best = candidate
                best_dist2 = dist2
        return best

    def _show_snap_marker(self, x, y, snapped):
        if self.snap_marker is None:
            self.snap_marker = QGraphicsEllipseItem(-5.0, -5.0, 10.0, 10.0)
            self.snap_marker.setZValue(100)
            self._scene.addItem(self.snap_marker)
        color = QColor(220, 60, 30) if snapped else QColor(120, 120, 120)
        brush = QColor(color)
        brush.setAlpha(80 if snapped else 30)
        pen = QPen(color)
        pen.setWidthF(1.5 if snapped else 1.0)
        self.snap_marker.setPen(pen)
        self.snap_marker.setBrush(QBrush(brush))
        self.snap_marker.setRect(float(x) - 5.0, float(y) - 5.0, 10.0, 10.0)

    def _clear_snap_marker(self):
        if self.snap_marker is not None:
            self._scene.removeItem(self.snap_marker)
            self.snap_marker = None


def _closest_point_on_segment(px, py, x0, y0, x1, y1):
    dx = float(x1) - float(x0)
    dy = float(y1) - float(y0)
    length2 = dx * dx + dy * dy
    if length2 <= 0.0:
        return None
    t = ((float(px) - float(x0)) * dx + (float(py) - float(y0)) * dy) / length2
    t = max(0.0, min(1.0, t))
    return float(x0) + t * dx, float(y0) + t * dy
