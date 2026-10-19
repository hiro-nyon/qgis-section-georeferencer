"""QGraphicsView canvas used to display section images and overlays."""

from __future__ import annotations

import math
from contextlib import contextmanager

from qgis.PyQt.QtCore import QPointF, QRectF, Qt, pyqtSignal
from qgis.PyQt.QtGui import (
    QBrush,
    QColor,
    QFont,
    QImage,
    QPainter,
    QPen,
    QPixmap,
    QTransform,
)
from qgis.PyQt.QtWidgets import (
    QGraphicsEllipseItem,
    QGraphicsItem,
    QGraphicsLineItem,
    QGraphicsPixmapItem,
    QGraphicsScene,
    QGraphicsSimpleTextItem,
    QGraphicsView,
    QStyleOptionGraphicsItem,
)


PDF_RENDER_DPI = 600

# A large sheet rendered at 600 DPI can reach hundreds of megapixels, and both
# the QPixmap and the full-resolution PNG export are held in memory. Cap the
# raster and lower the DPI instead of letting QGIS run out of memory.
MAX_PDF_PIXELS = 120_000_000
MIN_PDF_RENDER_DPI = 72

# A left drag pans the view, so a press/release pair is only treated as a
# coordinate pick when the pointer stayed essentially still.
CLICK_MOVE_TOLERANCE_PX = 4

# Overlay markers are sized in screen pixels so they stay visible on a
# 10000 px scan shown at 5% zoom. The full-resolution PNG export scales them
# up so that they keep roughly this size relative to a ~1500 px wide view.
EXPORT_MARKER_REFERENCE_PX = 1500.0

# Downscaled copies of the section image are drawn when zoomed out, because
# sampling a full-resolution scan at a few percent drops thin drawing lines.
PYRAMID_MIN_SIDE_PX = 512

GCP_COLOR = QColor(220, 30, 30)
PENDING_COLOR = QColor(255, 120, 0)
GPS_COLOR = QColor(30, 120, 255)
VERTEX_COLOR = QColor(30, 170, 90)
SEGMENT_COLOR = QColor(255, 145, 0)


def pyramid_level_index(level_of_detail, level_count):
    """Index of the downscaled level to draw, or -1 for the full image.

    Level k is downscaled by 2**(k+1). The coarsest level that still has at
    least one image pixel per screen pixel is chosen.
    """
    if level_count <= 0 or level_of_detail <= 0 or level_of_detail >= 0.5:
        return -1
    index = int(math.floor(math.log2(1.0 / level_of_detail))) - 1
    return max(0, min(level_count - 1, index))


def screen_marker(x, y, color, size, label=None, cross=True, fill_alpha=120):
    """A circle (+ cross, + label) that keeps its size in screen pixels."""
    half = size / 2.0
    pen = QPen(color)
    pen.setWidthF(1.5)
    pen.setCosmetic(True)
    brush_color = QColor(color)
    brush_color.setAlpha(fill_alpha)
    marker = QGraphicsEllipseItem(-half, -half, size, size)
    marker.setPen(pen)
    marker.setBrush(QBrush(brush_color))
    marker.setFlag(QGraphicsItem.ItemIgnoresTransformations, True)
    marker.setPos(float(x), float(y))
    if cross:
        for x0, y0, x1, y1 in ((-size, 0, size, 0), (0, -size, 0, size)):
            line = QGraphicsLineItem(x0, y0, x1, y1, marker)
            line.setPen(pen)
    if label is not None:
        text = QGraphicsSimpleTextItem(str(label), marker)
        font = QFont()
        font.setBold(True)
        font.setPixelSize(12)
        text.setFont(font)
        text.setBrush(QBrush(color))
        text.setPos(half + 2.0, half - 2.0)
    return marker


def screen_label(text, x, y, color, anchor="left"):
    """Text that keeps its size in screen pixels.

    ``anchor`` names where (x, y) sits on the label: "left", "right", "top".
    """
    item = QGraphicsSimpleTextItem(str(text))
    font = QFont()
    font.setPixelSize(11)
    item.setFont(font)
    item.setBrush(QBrush(color))
    item.setFlag(QGraphicsItem.ItemIgnoresTransformations, True)
    item.setPos(float(x), float(y))
    rect = item.boundingRect()
    if anchor == "right":
        dx, dy = -rect.width() - 4.0, -rect.height() / 2.0
    elif anchor == "top":
        dx, dy = -rect.width() / 2.0, 4.0
    else:
        dx, dy = 4.0, -rect.height() / 2.0
    item.setTransform(QTransform.fromTranslate(dx, dy))
    return item


def cosmetic_line(x0, y0, x1, y1, color, width=1.5, style=None):
    """A line between scene points whose width is in screen pixels."""
    pen = QPen(color)
    pen.setWidthF(width)
    pen.setCosmetic(True)
    if style is not None:
        pen.setStyle(style)
    line = QGraphicsLineItem(float(x0), float(y0), float(x1), float(y1))
    line.setPen(pen)
    return line


def paint_placeholder(view, text):
    """Centered hint text in an empty view."""
    painter = QPainter(view.viewport())
    try:
        painter.setPen(QColor(140, 140, 140))
        painter.drawText(
            view.viewport().rect().adjusted(12, 12, -12, -12),
            int(Qt.AlignCenter) | int(Qt.TextWordWrap),
            text,
        )
    finally:
        painter.end()


class _PyramidPixmapItem(QGraphicsPixmapItem):
    """Pixmap item that draws a downscaled copy when zoomed out."""

    def __init__(self, pixmap):
        super().__init__(pixmap)
        self.setTransformationMode(Qt.SmoothTransformation)
        self._levels = None

    def levels(self):
        if self._levels is None:
            levels = []
            current = self.pixmap()
            while max(current.width(), current.height()) > PYRAMID_MIN_SIDE_PX:
                current = current.scaled(
                    max(1, current.width() // 2),
                    max(1, current.height() // 2),
                    Qt.IgnoreAspectRatio,
                    Qt.SmoothTransformation,
                )
                levels.append(current)
            self._levels = levels
        return self._levels

    def paint(self, painter, option, widget=None):
        level_of_detail = QStyleOptionGraphicsItem.levelOfDetailFromTransform(
            painter.worldTransform()
        )
        if level_of_detail >= 0.5:
            super().paint(painter, option, widget)
            return
        levels = self.levels()
        index = pyramid_level_index(level_of_detail, len(levels))
        if index < 0:
            super().paint(painter, option, widget)
            return
        level = levels[index]
        painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
        painter.drawPixmap(self.boundingRect(), level, QRectF(level.rect()))


class SectionCanvas(QGraphicsView):
    """Section drawing canvas with image, GCP, and projection overlays."""

    clickedPixel = pyqtSignal(float, float)
    # Pointer position in image pixels; NaN when the pointer leaves the image.
    hoveredPixel = pyqtSignal(float, float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)
        self.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
        self.setDragMode(QGraphicsView.ScrollHandDrag)
        self.viewport().setCursor(Qt.CrossCursor)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.AnchorViewCenter)
        self.setMouseTracking(True)
        self.viewport().setMouseTracking(True)
        self.image_item = None
        self.image_path = None
        self.gcp_items = []
        self.projected_items = []
        self.reference_overlay_items = []
        self.highlight_items = []
        self.pending_items = []
        self._press_pos = None

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
        self.image_item = _PyramidPixmapItem(pixmap)
        self.image_item.setPos(0, 0)
        self.image_item.setZValue(-100)
        self._scene.addItem(self.image_item)
        self._scene.setSceneRect(QRectF(0, 0, pixmap.width(), pixmap.height()))
        self.fit_to_image()

    def clear_image(self):
        self._scene.clear()
        self.image_item = None
        self.image_path = None
        self.gcp_items = []
        self.projected_items = []
        self.reference_overlay_items = []
        self.highlight_items = []
        self.pending_items = []

    def _remove_items(self, items):
        for item in items:
            if item.scene() is self._scene:
                self._scene.removeItem(item)

    def clear_gcp_markers(self):
        self._remove_items(self.gcp_items)
        self.gcp_items = []
        self.clear_highlight()

    def clear_highlight(self):
        self._remove_items(self.highlight_items)
        self.highlight_items = []

    def highlight_gcp(self, gcp):
        """Ring the GCP currently selected in the table."""
        self.clear_highlight()
        if gcp is None or self.image_item is None:
            return
        ring = screen_marker(
            float(gcp["u"]), float(gcp["v"]), QColor(255, 145, 0), 26.0,
            cross=False, fill_alpha=0,
        )
        pen = ring.pen()
        pen.setWidthF(2.5)
        ring.setPen(pen)
        ring.setZValue(30)
        self._scene.addItem(ring)
        self.highlight_items.append(ring)

    def set_pending_pixel(self, u, v):
        """Show the picked pixel that the next 'Add GCP' will use."""
        self.clear_pending_pixel()
        if self.image_item is None:
            return
        marker = screen_marker(u, v, PENDING_COLOR, 18.0, label="new", fill_alpha=40)
        marker.setZValue(40)
        self._scene.addItem(marker)
        self.pending_items.append(marker)

    def clear_pending_pixel(self):
        self._remove_items(self.pending_items)
        self.pending_items = []

    def clear_projected_overlays(self):
        self._remove_items(self.projected_items)
        self.projected_items = []

    def clear_reference_overlay(self):
        self._remove_items(self.reference_overlay_items)
        self.reference_overlay_items = []

    def clear_all_overlays(self):
        self.clear_gcp_markers()
        self.clear_projected_overlays()
        self.clear_reference_overlay()
        self.clear_pending_pixel()

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
            marker = screen_marker(
                float(gcp["u"]), float(gcp["v"]), GCP_COLOR, 12.0, label=index
            )
            marker.setZValue(20)
            self._scene.addItem(marker)
            self.gcp_items.append(marker)

    def draw_projected_points(self, records, color=None):
        color = GPS_COLOR if color is None else color
        for record in records:
            marker = screen_marker(
                float(record["u"]), float(record["v"]), color, 7.0,
                cross=False, fill_alpha=170,
            )
            marker.setZValue(18)
            self._scene.addItem(marker)
            self.projected_items.append(marker)

    def draw_projected_vertices(self, records, color=None):
        color = VERTEX_COLOR if color is None else color
        for record in records:
            marker = screen_marker(
                float(record["u"]), float(record["v"]), color, 6.0,
                cross=False, fill_alpha=150,
            )
            marker.setZValue(17)
            self._scene.addItem(marker)
            self.projected_items.append(marker)

    def draw_projected_segments(self, segments, color=None):
        color = SEGMENT_COLOR if color is None else color
        for segment in segments:
            line = cosmetic_line(
                segment["u0"], segment["v0"], segment["u1"], segment["v1"], color
            )
            line.setZValue(15)
            self._scene.addItem(line)
            self.projected_items.append(line)

    def draw_reference_overlay(self, records, segments, section_to_pixel_coeffs):
        """Draw PLATEAU reference as semi-transparent overlay using the affine transform."""
        self.clear_reference_overlay()
        u_c = section_to_pixel_coeffs["u"]
        v_c = section_to_pixel_coeffs["v"]

        def to_pixel(s, z):
            s, z = float(s), float(z)
            return (
                u_c[0] + u_c[1] * s + u_c[2] * z,
                v_c[0] + v_c[1] * s + v_c[2] * z,
            )

        segment_color = QColor(255, 145, 0, 170)
        for seg in segments or []:
            if None in (seg.get("s0"), seg.get("z0"), seg.get("s1"), seg.get("z1")):
                continue
            u0, v0 = to_pixel(seg["s0"], seg["z0"])
            u1, v1 = to_pixel(seg["s1"], seg["z1"])
            line = cosmetic_line(u0, v0, u1, v1, segment_color, width=2.0)
            line.setZValue(10)
            self._scene.addItem(line)
            self.reference_overlay_items.append(line)

        vertex_color = QColor(30, 170, 90, 200)
        for rec in records or []:
            if rec.get("inside_buffer") is False:
                continue
            if rec.get("s") is None or rec.get("z_section") is None:
                continue
            u, v = to_pixel(rec["s"], rec["z_section"])
            marker = screen_marker(u, v, vertex_color, 5.0, cross=False, fill_alpha=90)
            marker.setZValue(11)
            self._scene.addItem(marker)
            self.reference_overlay_items.append(marker)

    def export_png(self, path):
        rect = self._scene.sceneRect()
        if rect.isEmpty() or self.image_item is None:
            raise ValueError("No section image is loaded.")
        image = QImage(
            int(rect.width()),
            int(rect.height()),
            QImage.Format_ARGB32_Premultiplied,
        )
        if image.isNull():
            raise ValueError(
                "Could not allocate a {}x{} px export image. The section image "
                "is too large to export at full resolution.".format(
                    int(rect.width()), int(rect.height())
                )
            )
        image.fill(Qt.white)
        factor = max(1.0, max(rect.width(), rect.height()) / EXPORT_MARKER_REFERENCE_PX)
        painter = QPainter(image)
        painter.setRenderHints(
            QPainter.Antialiasing
            | QPainter.TextAntialiasing
            | QPainter.SmoothPixmapTransform
        )
        try:
            with self._export_overlay_style(factor):
                self._scene.render(painter, QRectF(image.rect()), rect)
        finally:
            painter.end()
        if not image.save(path, "PNG"):
            raise ValueError("Could not export PNG: {}".format(path))

    @contextmanager
    def _export_overlay_style(self, factor):
        """Scale screen-sized overlays for a full-resolution export and hide
        the interactive-only marks (pending pick, selection ring)."""
        restore = []
        hidden = self.pending_items + self.highlight_items
        for item in hidden:
            item.setVisible(False)
        for item in self.gcp_items + self.projected_items + self.reference_overlay_items:
            if item.flags() & QGraphicsItem.ItemIgnoresTransformations:
                restore.append((item.setScale, item.scale()))
                item.setScale(item.scale() * factor)
            elif isinstance(item, QGraphicsLineItem):
                pen = item.pen()
                restore.append((item.setPen, QPen(pen)))
                pen.setWidthF(pen.widthF() * factor)
                item.setPen(pen)
        try:
            yield
        finally:
            for setter, value in restore:
                setter(value)
            for item in hidden:
                item.setVisible(True)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._press_pos = event.pos()
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        press_pos = self._press_pos
        self._press_pos = None
        super().mouseReleaseEvent(event)
        # ScrollHandDrag swaps in a hand cursor while dragging.
        self.viewport().setCursor(Qt.CrossCursor)
        if event.button() != Qt.LeftButton or press_pos is None:
            return
        if (event.pos() - press_pos).manhattanLength() > CLICK_MOVE_TOLERANCE_PX:
            return  # the user panned the view rather than picking a pixel
        if self.image_item is None:
            return
        scene_pos = self.mapToScene(event.pos())
        if self.image_item.boundingRect().contains(
            QPointF(scene_pos.x(), scene_pos.y())
        ):
            self.clickedPixel.emit(float(scene_pos.x()), float(scene_pos.y()))

    def mouseMoveEvent(self, event):
        super().mouseMoveEvent(event)
        if self.image_item is None:
            return
        scene_pos = self.mapToScene(event.pos())
        if self.image_item.boundingRect().contains(scene_pos):
            self.hoveredPixel.emit(float(scene_pos.x()), float(scene_pos.y()))
        else:
            self.hoveredPixel.emit(math.nan, math.nan)

    def leaveEvent(self, event):
        self.hoveredPixel.emit(math.nan, math.nan)
        super().leaveEvent(event)

    def paintEvent(self, event):
        super().paintEvent(event)
        if self.image_item is None:
            paint_placeholder(
                self,
                "Load the scanned section drawing in step 3\n"
                "(PNG, JPEG, TIFF, or PDF).",
            )

    def wheelEvent(self, event):
        zoom_in = 1.15
        zoom_out = 1.0 / zoom_in
        factor = zoom_in if event.angleDelta().y() > 0 else zoom_out
        self.scale(factor, factor)
        event.accept()


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
        dpi = PDF_RENDER_DPI
        gdal.SetConfigOption("GDAL_PDF_DPI", str(dpi))
        ds = gdal.Open(str(path))
        if ds is None:
            raise ValueError(
                "Could not open PDF with GDAL. "
                "Ensure the GDAL PDF driver (Poppler or PDFium) is available."
            )
        dpi = _fit_pdf_dpi(ds.RasterXSize, ds.RasterYSize, dpi)
        if dpi != PDF_RENDER_DPI:
            ds = None
            gdal.SetConfigOption("GDAL_PDF_DPI", str(dpi))
            ds = gdal.Open(str(path))
            if ds is None:
                raise ValueError("Could not re-open PDF at a reduced resolution.")
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


def _fit_pdf_dpi(width, height, dpi):
    """Reduce the render DPI until the page fits under MAX_PDF_PIXELS."""
    pixels = int(width) * int(height)
    if pixels <= MAX_PDF_PIXELS or pixels <= 0:
        return dpi
    scale = math.sqrt(MAX_PDF_PIXELS / float(pixels))
    return max(MIN_PDF_RENDER_DPI, int(dpi * scale))


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
    # The raster axes are distance/height, never project map coordinates.
    # Replace any inherited source raster CRS with a local engineering CRS.
    srs = osr.SpatialReference()
    srs.SetLocalCS("Section coordinates (s, z_section)")
    srs.SetLinearUnits("metre", 1.0)
    dst.SetProjection(srs.ExportToWkt())
    if crs_auth_id:
        dst.SetMetadataItem("SECTION_SOURCE_CRS", str(crs_auth_id))
    dst.SetMetadataItem("SECTION_COORDINATE_MODEL", "X=s_along_section,Y=z_section")
    dst.SetMetadataItem(
        "SECTION_GEOREFERENCE_NOTE",
        "This GeoTIFF is georeferenced in section coordinates, not map x/y.",
    )


def nice_step(span, target_ticks=6):
    """A 1/2/5 x 10^n tick step giving about ``target_ticks`` ticks."""
    span = abs(float(span))
    if span <= 0.0 or not math.isfinite(span):
        return 1.0
    raw = span / max(1, int(target_ticks))
    magnitude = 10.0 ** math.floor(math.log10(raw))
    for multiple in (1.0, 2.0, 5.0, 10.0):
        if multiple * magnitude >= raw:
            return multiple * magnitude
    return 10.0 * magnitude


def choose_snap(candidates, x, y, tolerance):
    """Pick the snap target for a pointer at (x, y).

    Vertices and segment endpoints win over points along a segment: the
    nearest point on a wall is always closer than the corner it runs into,
    so plain nearest-candidate snapping slides GCP targets along walls.
    """
    limit2 = float(tolerance) * float(tolerance)
    for kinds in (("vertex", "segment_endpoint"), ("segment",)):
        best = None
        best_dist2 = limit2
        for candidate in candidates:
            if candidate["kind"] not in kinds:
                continue
            dx = candidate["x"] - x
            dy = candidate["y"] - y
            dist2 = dx * dx + dy * dy
            if dist2 <= best_dist2:
                best = candidate
                best_dist2 = dist2
        if best is not None:
            return best
    return None


class SectionReferenceCanvas(QGraphicsView):
    """Reference canvas for selected PLATEAU/CAD geometry in section coordinates."""

    clickedSection = pyqtSignal(float, float)
    # s, z under the pointer and the snap kind ("" when not snapped); NaN
    # when the pointer leaves the reference.
    hoveredSection = pyqtSignal(float, float, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)
        self.setRenderHints(QPainter.Antialiasing)
        self.setDragMode(QGraphicsView.ScrollHandDrag)
        self.viewport().setCursor(Qt.CrossCursor)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.AnchorViewCenter)
        self.setMouseTracking(True)
        self.viewport().setMouseTracking(True)
        self.records = []
        self.segments = []
        self.bounds = None
        self.reference_scale = 1.0
        self.margin = 60.0
        # Snap tolerance in screen pixels, whatever the zoom.
        self.snap_tolerance_px = 12.0
        self.snap_enabled = True
        self.snap_marker = None
        self.last_snap = None
        self.target_items = []
        self.pending_items = []
        self._press_pos = None

    def clear_reference(self):
        self._scene.clear()
        self.records = []
        self.segments = []
        self.bounds = None
        self.snap_marker = None
        self.last_snap = None
        self.target_items = []
        self.pending_items = []
        self._press_pos = None

    def has_reference(self):
        return bool(self.records or self.segments)

    def set_snap_enabled(self, enabled):
        self.snap_enabled = bool(enabled)
        if not self.snap_enabled:
            self.last_snap = None
            self._clear_snap_marker()

    def load_reference(self, records, segments=None):
        records = list(records or [])
        segments = list(segments or [])
        # Compute bounds first: if the selection has nothing inside the buffer
        # this raises, and the previously loaded reference is left untouched
        # instead of being replaced by an empty, unusable one.
        bounds = self._compute_bounds(records, segments)
        self.clear_reference()
        self.records = records
        self.segments = segments
        self.bounds = bounds
        self._draw_reference()
        self.fit_to_reference()

    def fit_to_reference(self):
        rect = self._scene.sceneRect()
        if not rect.isEmpty():
            self.fitInView(rect, Qt.KeepAspectRatio)

    def draw_gcp_targets(self, gcps):
        """Mark the s/z target of every GCP, numbered like the table."""
        self._remove(self.target_items)
        self.target_items = []
        if self.bounds is None:
            return
        for index, gcp in enumerate(gcps, start=1):
            x, y = self.section_to_pixel(gcp["s"], gcp["z"])
            marker = screen_marker(x, y, GCP_COLOR, 12.0, label=index)
            marker.setZValue(50)
            self._scene.addItem(marker)
            self.target_items.append(marker)

    def set_pending_target(self, s, z):
        """Show the picked s/z that the next 'Add GCP' will use."""
        self.clear_pending_target()
        if self.bounds is None:
            return
        x, y = self.section_to_pixel(s, z)
        marker = screen_marker(x, y, PENDING_COLOR, 18.0, label="new", fill_alpha=40)
        marker.setZValue(60)
        self._scene.addItem(marker)
        self.pending_items.append(marker)

    def clear_pending_target(self):
        self._remove(self.pending_items)
        self.pending_items = []

    def _remove(self, items):
        for item in items:
            if item.scene() is self._scene:
                self._scene.removeItem(item)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._press_pos = event.pos()
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        press_pos = self._press_pos
        self._press_pos = None
        super().mouseReleaseEvent(event)
        self.viewport().setCursor(Qt.CrossCursor)
        if event.button() != Qt.LeftButton or press_pos is None:
            return
        if (event.pos() - press_pos).manhattanLength() > CLICK_MOVE_TOLERANCE_PX:
            return  # the user panned the view rather than picking a target
        if self.bounds is None:
            return
        scene_pos = self.mapToScene(event.pos())
        snap = self._snap_scene_pos(scene_pos) if self.snap_enabled else None
        if snap is not None:
            s, z = snap["s"], snap["z"]
        else:
            s, z = self.pixel_to_section(scene_pos.x(), scene_pos.y())
        self.clickedSection.emit(float(s), float(z))

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
            if snap is not None:
                self.hoveredSection.emit(
                    float(snap["s"]), float(snap["z"]), str(snap["kind"])
                )
            else:
                s, z = self.pixel_to_section(scene_pos.x(), scene_pos.y())
                self.hoveredSection.emit(float(s), float(z), "")
        super().mouseMoveEvent(event)

    def leaveEvent(self, event):
        self._clear_snap_marker()
        self.hoveredSection.emit(math.nan, math.nan, "")
        super().leaveEvent(event)

    def paintEvent(self, event):
        super().paintEvent(event)
        if self.bounds is None:
            paint_placeholder(
                self,
                "Pick the PLATEAU building in step 1 and draw the section\n"
                "line through it in step 2.",
            )

    def wheelEvent(self, event):
        zoom_in = 1.15
        zoom_out = 1.0 / zoom_in
        factor = zoom_in if event.angleDelta().y() > 0 else zoom_out
        self.scale(factor, factor)
        event.accept()

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

    def _compute_bounds(self, records, segments):
        s_values = []
        z_values = []
        for record in records:
            if record.get("inside_buffer") is False:
                continue
            if record.get("s") is None or record.get("z_section") is None:
                continue
            s_values.append(float(record["s"]))
            z_values.append(float(record["z_section"]))
        for segment in segments:
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
        self._draw_axes()

        segment_pen = QPen(SEGMENT_COLOR)
        segment_pen.setWidthF(1.6)
        segment_pen.setCosmetic(True)
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

        for record in self.records:
            if record.get("inside_buffer") is False:
                continue
            if record.get("s") is None or record.get("z_section") is None:
                continue
            x, y = self.section_to_pixel(record["s"], record["z_section"])
            marker = screen_marker(x, y, VERTEX_COLOR, 6.0, cross=False, fill_alpha=110)
            marker.setZValue(5)
            self._scene.addItem(marker)

    def _draw_axes(self):
        min_s, max_s, min_z, max_z = self.bounds
        left, top = self.section_to_pixel(min_s, max_z)
        right, bottom = self.section_to_pixel(max_s, min_z)
        axis_color = QColor(120, 120, 120)
        grid_color = QColor(200, 200, 200)
        self._scene.addItem(cosmetic_line(left, bottom, right, bottom, axis_color, 1.0))
        self._scene.addItem(cosmetic_line(left, top, left, bottom, axis_color, 1.0))

        s_step = nice_step(max_s - min_s)
        value = math.ceil(min_s / s_step) * s_step
        while value <= max_s + 1e-9:
            x, _y = self.section_to_pixel(value, min_z)
            grid = cosmetic_line(x, top, x, bottom, grid_color, 1.0, Qt.DotLine)
            grid.setZValue(-10)
            self._scene.addItem(grid)
            self._scene.addItem(
                screen_label(_tick_text(value, s_step), x, bottom, axis_color, "top")
            )
            value += s_step

        z_step = nice_step(max_z - min_z)
        value = math.ceil(min_z / z_step) * z_step
        while value <= max_z + 1e-9:
            _x, y = self.section_to_pixel(min_s, value)
            grid = cosmetic_line(left, y, right, y, grid_color, 1.0, Qt.DotLine)
            grid.setZValue(-10)
            self._scene.addItem(grid)
            self._scene.addItem(
                screen_label(_tick_text(value, z_step), left, y, axis_color, "right")
            )
            value += z_step

        self._scene.addItem(screen_label("s (m)", right, bottom + 1.0, axis_color, "left"))
        self._scene.addItem(screen_label("z (m)", left, top, axis_color, "left"))

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

        view_scale = abs(self.transform().m11()) or 1.0
        return choose_snap(candidates, x, y, self.snap_tolerance_px / view_scale)

    def _show_snap_marker(self, x, y, snapped):
        if self.snap_marker is None:
            self.snap_marker = QGraphicsEllipseItem(-6.0, -6.0, 12.0, 12.0)
            self.snap_marker.setFlag(QGraphicsItem.ItemIgnoresTransformations, True)
            self.snap_marker.setZValue(100)
            self._scene.addItem(self.snap_marker)
        color = QColor(220, 60, 30) if snapped else QColor(120, 120, 120)
        brush = QColor(color)
        brush.setAlpha(80 if snapped else 30)
        pen = QPen(color)
        pen.setWidthF(1.5 if snapped else 1.0)
        pen.setCosmetic(True)
        self.snap_marker.setPen(pen)
        self.snap_marker.setBrush(QBrush(brush))
        self.snap_marker.setPos(float(x), float(y))

    def _clear_snap_marker(self):
        if self.snap_marker is not None:
            if self.snap_marker.scene() is self._scene:
                self._scene.removeItem(self.snap_marker)
            self.snap_marker = None


def _tick_text(value, step):
    if abs(value) < step * 1e-6:
        value = 0.0
    if step >= 1.0:
        return "{:.0f}".format(value)
    decimals = max(0, -int(math.floor(math.log10(step))))
    return "{:.{}f}".format(value, decimals)


def _closest_point_on_segment(px, py, x0, y0, x1, y1):
    dx = float(x1) - float(x0)
    dy = float(y1) - float(y0)
    length2 = dx * dx + dy * dy
    if length2 <= 0.0:
        return None
    t = ((float(px) - float(x0)) * dx + (float(py) - float(y0)) * dy) / length2
    t = max(0.0, min(1.0, t))
    return float(x0) + t * dx, float(y0) + t * dy
