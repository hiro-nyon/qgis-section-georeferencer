"""QGraphicsView canvas used to display section images and overlays."""

from __future__ import annotations

from qgis.PyQt.QtCore import QPointF, QRectF, Qt, pyqtSignal
from qgis.PyQt.QtGui import QBrush, QColor, QImage, QPainter, QPen, QPixmap
from qgis.PyQt.QtWidgets import QGraphicsEllipseItem, QGraphicsLineItem, QGraphicsScene, QGraphicsTextItem, QGraphicsView


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

    def clear_gcp_markers(self):
        for item in self.gcp_items:
            self._scene.removeItem(item)
        self.gcp_items = []

    def clear_projected_overlays(self):
        for item in self.projected_items:
            self._scene.removeItem(item)
        self.projected_items = []

    def clear_all_overlays(self):
        self.clear_gcp_markers()
        self.clear_projected_overlays()

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
        pixmap = self.image_item.pixmap()
        if pixmap.isNull() or not pixmap.save(path, "PNG"):
            raise ValueError("Could not export background PNG: {}".format(path))

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
    """Convert a PDF to QPixmap via GDAL raster rendering at 300 DPI."""
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
        gdal.SetConfigOption("GDAL_PDF_DPI", "300")
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
        self.records = []
        self.segments = []
        self.bounds = None
        self.reference_scale = 1.0
        self.margin = 30.0

    def clear_reference(self):
        self._scene.clear()
        self.records = []
        self.segments = []
        self.bounds = None

    def has_reference(self):
        return bool(self.records or self.segments)

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
            s, z = self.pixel_to_section(scene_pos.x(), scene_pos.y())
            self.clickedSection.emit(float(s), float(z))
        super().mousePressEvent(event)

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
