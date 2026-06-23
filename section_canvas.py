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
