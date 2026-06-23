"""Dock widget UI for the Section Georeferencer plugin."""

from __future__ import annotations

import os

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QColor, QCursor, QFont, QIcon
from qgis.PyQt.QtWidgets import (
    QAbstractItemView,
    QDockWidget,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)
from qgis.core import (
    Qgis,
    QgsCoordinateTransform,
    QgsPointXY,
    QgsProject,
    QgsUnitTypes,
    QgsWkbTypes,
)
from qgis.gui import QgsMapTool, QgsRubberBand, QgsVertexMarker

from . import config_io
from .layer_projector import project_point_layer, project_vector_layer_vertices
from .package_exporter import export_section_analysis_package, utc_timestamp
from .section_canvas import SectionCanvas, SectionReferenceCanvas
from .transforms import compute_section_basis, fit_affine_pixel_to_section


PLUGIN_NAME = "Section Georeferencer"
PLUGIN_VERSION = "0.1.0"

_STEP_DONE = "✓"
_STEP_BULLET = "•"

_DOCK_STYLESHEET = """
QDockWidget {
    font-size: 13px;
}
QDockWidget::title {
    padding: 6px 10px;
}

/* Group boxes as workflow steps */
QGroupBox {
    font-weight: bold;
    font-size: 12px;
    border: 1px solid palette(mid);
    border-radius: 4px;
    margin-top: 8px;
    padding: 10px 8px 8px 8px;
}
QGroupBox::title {
    subcontrol-origin: margin;
    subcontrol-position: top left;
    padding: 2px 8px;
    left: 6px;
}

/* Form fields */
QLineEdit {
    padding: 4px 6px;
    border: 1px solid palette(mid);
    border-radius: 3px;
    font-size: 12px;
    min-height: 22px;
}
QLineEdit:focus {
    border-color: #378ADD;
}

/* Buttons */
QPushButton {
    padding: 5px 10px;
    border: 1px solid palette(mid);
    border-radius: 3px;
    font-size: 11px;
    font-weight: bold;
    min-height: 22px;
}
QPushButton:hover {
    background-color: palette(midlight);
}
QPushButton:pressed {
    background-color: palette(mid);
}
QPushButton[primary="true"] {
    background-color: #378ADD;
    color: white;
    border-color: #2B7BD0;
}
QPushButton[primary="true"]:hover {
    background-color: #2B7BD0;
}
QPushButton[danger="true"] {
    color: #A32D2D;
    border-color: #E24B4A;
}

/* Table */
QTableWidget {
    border: 1px solid palette(mid);
    border-radius: 3px;
    font-size: 11px;
    gridline-color: palette(midlight);
    selection-background-color: #E6F1FB;
    selection-color: palette(text);
}
QTableWidget::item {
    padding: 3px 4px;
}
QHeaderView::section {
    background-color: palette(window);
    border: none;
    border-bottom: 1px solid palette(mid);
    font-size: 11px;
    font-weight: bold;
    padding: 4px 6px;
}

/* Scroll area */
QScrollArea {
    border: none;
}
"""


class SectionGeoreferencerDock(QDockWidget):
    """Main dock widget for section georeferencing workflow."""

    def __init__(self, iface, parent=None):
        super().__init__(PLUGIN_NAME, parent)
        self.iface = iface
        self.section_p0 = None
        self.section_p1 = None
        self.section_length = None
        self.image_path = None
        self.current_pixel = None
        self.gcps = []
        self.transform_result = None
        self.projected_records = []
        self.projected_segments = []
        self.gps_projected_records = []
        self.plateau_projected_records = []
        self.plateau_projected_segments = []
        self.gps_summary = _empty_gps_summary()
        self.plateau_summary = _empty_plateau_summary()
        self.projection_warnings = []
        self.reference_records = []
        self.reference_segments = []
        self.section_line_tool = None
        self.previous_map_tool = None
        self._build_ui()
        self._update_crs_labels()
        self._update_section_labels()
        self._update_gcp_table()
        self._update_workflow_state()
        try:
            QgsProject.instance().crsChanged.connect(self._update_crs_labels)
        except Exception:
            pass

    def _build_ui(self):
        root = QWidget(self)
        root.setStyleSheet(_DOCK_STYLESHEET)
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        self.status_label = QLabel("Ready")
        self.status_label.setWordWrap(True)
        self.status_label.setStyleSheet(
            "padding: 6px 12px; font-size: 12px;"
            "border-left: 3px solid #378ADD;"
            "background-color: palette(window);"
        )
        root_layout.addWidget(self.status_label)

        self.canvas = SectionCanvas()
        self.canvas.setMinimumHeight(300)
        self.canvas.clickedPixel.connect(self._on_canvas_clicked)
        root_layout.addWidget(self.canvas, stretch=1)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        controls = QWidget()
        controls_layout = QVBoxLayout(controls)
        controls_layout.setContentsMargins(6, 4, 6, 4)
        controls_layout.setSpacing(2)
        controls_layout.addWidget(self._section_group())
        controls_layout.addWidget(self._reference_group())
        controls_layout.addWidget(self._image_group())
        controls_layout.addWidget(self._gcp_group())
        controls_layout.addWidget(self._projection_group())
        controls_layout.addWidget(self._export_config_group())
        controls_layout.addStretch(1)
        scroll.setWidget(controls)
        root_layout.addWidget(scroll, stretch=0)

        self.setWidget(root)

    def _reference_group(self):
        self._reference_group_box = QGroupBox("2. PLATEAU reference")
        layout = QVBoxLayout(self._reference_group_box)
        layout.setSpacing(6)
        self.reference_info_label = QLabel(
            "Select a PLATEAU building feature, then load as snap target for GCPs."
        )
        self.reference_info_label.setWordWrap(True)
        self.reference_info_label.setStyleSheet("color: palette(dark); font-size: 11px;")
        layout.addWidget(self.reference_info_label)
        self.reference_canvas = SectionReferenceCanvas()
        self.reference_canvas.setMinimumHeight(140)
        self.reference_canvas.clickedSection.connect(self._on_reference_clicked)
        layout.addWidget(self.reference_canvas)
        button_row = QHBoxLayout()
        button_row.setSpacing(4)
        load_button = _primary_button("Load selected")
        load_button.setToolTip(
            "Project selected PLATEAU/CAD feature(s) into section coordinates.\n"
            "Click this reference view to snap to vertices/segments and fill GCP target s/z."
        )
        load_button.clicked.connect(self._load_selected_plateau_reference)
        clear_button = QPushButton("Clear")
        clear_button.clicked.connect(self._clear_plateau_reference)
        fit_button = QPushButton("Fit")
        fit_button.clicked.connect(self.reference_canvas.fit_to_reference)
        button_row.addWidget(load_button)
        button_row.addWidget(clear_button)
        button_row.addWidget(fit_button)
        layout.addLayout(button_row)
        return self._reference_group_box

    def _section_group(self):
        self._section_group_box = QGroupBox("1. Section line")
        layout = QVBoxLayout(self._section_group_box)
        layout.setSpacing(6)

        p0_row = QHBoxLayout()
        p0_row.setSpacing(6)
        self.x0_edit = QLineEdit()
        self.y0_edit = QLineEdit()
        p0_label = QLabel("P0")
        p0_label.setStyleSheet("font-weight: bold; font-size: 11px; color: palette(dark); min-width: 20px;")
        p0_row.addWidget(p0_label)
        for lbl, widget in [("x", self.x0_edit), ("y", self.y0_edit)]:
            widget.setPlaceholderText("0.0")
            widget.setToolTip("Project CRS coordinate")
            col = QVBoxLayout()
            col.setSpacing(1)
            fl = QLabel(lbl)
            fl.setStyleSheet("font-size: 10px; color: palette(dark);")
            col.addWidget(fl)
            col.addWidget(widget)
            p0_row.addLayout(col)
        layout.addLayout(p0_row)

        p1_row = QHBoxLayout()
        p1_row.setSpacing(6)
        self.x1_edit = QLineEdit()
        self.y1_edit = QLineEdit()
        p1_label = QLabel("P1")
        p1_label.setStyleSheet("font-weight: bold; font-size: 11px; color: palette(dark); min-width: 20px;")
        p1_row.addWidget(p1_label)
        for lbl, widget in [("x", self.x1_edit), ("y", self.y1_edit)]:
            widget.setPlaceholderText("0.0")
            widget.setToolTip("Project CRS coordinate")
            col = QVBoxLayout()
            col.setSpacing(1)
            fl = QLabel(lbl)
            fl.setStyleSheet("font-size: 10px; color: palette(dark);")
            col.addWidget(fl)
            col.addWidget(widget)
            p1_row.addLayout(col)
        layout.addLayout(p1_row)

        button_row = QHBoxLayout()
        button_row.setSpacing(4)
        draw_button = QPushButton("Draw on map")
        draw_button.setToolTip("Click two points on the QGIS map canvas to define the section line")
        draw_button.clicked.connect(self._start_section_line_map_tool)
        manual_button = QPushButton("Manual")
        manual_button.setToolTip("Set section line from the coordinate fields above")
        manual_button.clicked.connect(self._use_manual_section)
        selected_button = QPushButton("From line")
        selected_button.setToolTip("Set section line from selected line feature in active layer")
        selected_button.clicked.connect(self._use_selected_line)
        button_row.addWidget(draw_button)
        button_row.addWidget(manual_button)
        button_row.addWidget(selected_button)
        layout.addLayout(button_row)

        info_row = QHBoxLayout()
        info_row.setSpacing(12)
        self.section_length_label = QLabel("Length: -")
        self.section_length_label.setStyleSheet("font-size: 11px; color: palette(dark);")
        self.project_crs_label = QLabel("CRS: -")
        self.project_crs_label.setStyleSheet("font-size: 11px; color: palette(dark);")
        info_row.addWidget(self.section_length_label)
        info_row.addWidget(self.project_crs_label)
        info_row.addStretch()
        layout.addLayout(info_row)

        self.crs_warning_label = QLabel("")
        self.crs_warning_label.setWordWrap(True)
        self.crs_warning_label.setStyleSheet("color: #BA7517; font-size: 11px; padding: 2px 0;")
        layout.addWidget(self.crs_warning_label)
        return self._section_group_box

    def _image_group(self):
        self._image_group_box = QGroupBox("3. Section image")
        layout = QVBoxLayout(self._image_group_box)
        layout.setSpacing(6)
        self.image_info_label = QLabel("No image loaded")
        self.image_info_label.setWordWrap(True)
        self.image_info_label.setStyleSheet("color: palette(dark); font-size: 11px;")
        layout.addWidget(self.image_info_label)
        button_row = QHBoxLayout()
        button_row.setSpacing(4)
        load_button = _primary_button("Load image")
        load_button.setToolTip("Load section image (PNG, JPG, TIFF, PDF)")
        load_button.clicked.connect(self._load_image_dialog)
        clear_button = QPushButton("Clear")
        clear_button.clicked.connect(self._clear_image)
        fit_button = QPushButton("Fit view")
        fit_button.clicked.connect(self.canvas.fit_to_image)
        button_row.addWidget(load_button)
        button_row.addWidget(clear_button)
        button_row.addWidget(fit_button)
        layout.addLayout(button_row)
        return self._image_group_box

    def _gcp_group(self):
        self._gcp_group_box = QGroupBox("4. GCP manager")
        layout = QVBoxLayout(self._gcp_group_box)
        layout.setSpacing(6)

        self.clicked_pixel_label = QLabel("Pixel: -")
        self.clicked_pixel_label.setToolTip("Click on the section image to pick a pixel position")
        self.clicked_pixel_label.setStyleSheet("font-size: 11px; color: palette(dark);")
        layout.addWidget(self.clicked_pixel_label)

        sz_row = QHBoxLayout()
        sz_row.setSpacing(6)
        self.gcp_s_edit = QLineEdit()
        self.gcp_z_edit = QLineEdit()
        self.gcp_s_edit.setPlaceholderText("distance (m)")
        self.gcp_z_edit.setPlaceholderText("height (m)")
        self.gcp_s_edit.setToolTip("Distance along the section line from P0 (meters)")
        self.gcp_z_edit.setToolTip("Height at this point (meters, after z_offset)")
        for lbl, widget in [("s (distance m)", self.gcp_s_edit), ("z (height m)", self.gcp_z_edit)]:
            col = QVBoxLayout()
            col.setSpacing(1)
            fl = QLabel(lbl)
            fl.setStyleSheet("font-size: 10px; color: palette(dark);")
            col.addWidget(fl)
            col.addWidget(widget)
            sz_row.addLayout(col)
        layout.addLayout(sz_row)

        button_row = QHBoxLayout()
        button_row.setSpacing(4)
        add_button = _primary_button("Add GCP")
        add_button.setToolTip("Register clicked pixel as a Ground Control Point")
        add_button.clicked.connect(self._add_gcp)
        delete_button = QPushButton("Delete")
        delete_button.setProperty("danger", True)
        delete_button.clicked.connect(self._delete_selected_gcp)
        clear_button = QPushButton("Clear all")
        clear_button.clicked.connect(self._clear_gcps)
        button_row.addWidget(add_button)
        button_row.addWidget(delete_button)
        button_row.addWidget(clear_button)
        layout.addLayout(button_row)

        self.gcp_table = QTableWidget(0, 4)
        self.gcp_table.setHorizontalHeaderLabels(["u (px)", "v (px)", "s (m)", "z (m)"])
        self.gcp_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.gcp_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.gcp_table.verticalHeader().setVisible(False)
        self.gcp_table.setMinimumHeight(110)
        self.gcp_table.setMaximumHeight(160)
        self.gcp_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        layout.addWidget(self.gcp_table)

        transform_row = QHBoxLayout()
        transform_row.setSpacing(6)
        compute_button = QPushButton("Compute transform")
        compute_button.setToolTip("Compute affine transform from GCPs (minimum 3 required)")
        compute_button.clicked.connect(self._compute_transform)
        self.rms_label = QLabel("RMS: -")
        self.rms_label.setStyleSheet("font-size: 11px; color: palette(dark);")
        transform_row.addWidget(compute_button)
        transform_row.addWidget(self.rms_label)
        transform_row.addStretch()
        layout.addLayout(transform_row)
        return self._gcp_group_box

    def _projection_group(self):
        self._projection_group_box = QGroupBox("5. Layer projection")
        layout = QVBoxLayout(self._projection_group_box)
        layout.setSpacing(6)

        params_row = QHBoxLayout()
        params_row.setSpacing(6)
        self.z_offset_edit = QLineEdit("0.0")
        self.z_offset_edit.setToolTip(
            "Height datum offset: z_section = z_raw - z_offset.\n"
            "Set to local ground level if cross-section uses relative height."
        )
        self.buffer_width_edit = QLineEdit("10.0")
        self.buffer_width_edit.setToolTip(
            "Maximum perpendicular distance (m) from the section line.\n"
            "Points farther than this are marked outside_buffer."
        )
        for lbl, widget in [("z offset (m)", self.z_offset_edit), ("Buffer (m)", self.buffer_width_edit)]:
            col = QVBoxLayout()
            col.setSpacing(1)
            fl = QLabel(lbl)
            fl.setStyleSheet("font-size: 10px; color: palette(dark);")
            col.addWidget(fl)
            col.addWidget(widget)
            params_row.addLayout(col)
        layout.addLayout(params_row)

        z_field_col = QVBoxLayout()
        z_field_col.setSpacing(1)
        z_field_label = QLabel("Height field")
        z_field_label.setStyleSheet("font-size: 10px; color: palette(dark);")
        self.z_field_edit = QLineEdit()
        self.z_field_edit.setPlaceholderText("auto-detect")
        self.z_field_edit.setToolTip(
            "Attribute field name for height (e.g. measuredHeight).\n"
            "Leave empty to use geometry Z or auto-detect."
        )
        z_field_col.addWidget(z_field_label)
        z_field_col.addWidget(self.z_field_edit)
        layout.addLayout(z_field_col)

        button_row = QHBoxLayout()
        button_row.setSpacing(4)
        point_button = QPushButton("GPS points")
        point_button.setToolTip("Project active point layer onto the section as GPS data")
        point_button.clicked.connect(self._project_active_point_layer)
        vertex_button = QPushButton("PLATEAU/CAD")
        vertex_button.setToolTip(
            "Project selected features (or all features) from the active vector layer\n"
            "onto the section as building/CAD geometry"
        )
        vertex_button.clicked.connect(self._project_active_vector_layer)
        clear_button = QPushButton("Clear overlay")
        clear_button.clicked.connect(self._clear_projected_overlay)
        button_row.addWidget(point_button)
        button_row.addWidget(vertex_button)
        button_row.addWidget(clear_button)
        layout.addLayout(button_row)
        return self._projection_group_box

    def _export_config_group(self):
        group = QGroupBox("6. Export")
        layout = QVBoxLayout(group)
        layout.setSpacing(4)

        from qgis.PyQt.QtWidgets import QGridLayout
        grid = QGridLayout()
        grid.setSpacing(4)

        save_button = QPushButton("Save config")
        save_button.setToolTip("Save section line, GCPs, and transform to JSON")
        save_button.clicked.connect(self._save_config_dialog)
        load_button = QPushButton("Load config")
        load_button.setToolTip("Restore section line, GCPs, and transform from JSON")
        load_button.clicked.connect(self._load_config_dialog)
        export_button = QPushButton("Export PNG")
        export_button.setToolTip("Export the section image with overlaid points as PNG")
        export_button.clicked.connect(self._export_png_dialog)
        package_button = _primary_button("Export package")
        package_button.setToolTip(
            "Export a complete analysis package:\n"
            "section_config.json, gps_projected.csv,\n"
            "plateau_projected.geojson, world_projected.geojson,\n"
            "section_projected.gpkg, section images"
        )
        package_button.clicked.connect(self._export_section_analysis_package_dialog)

        grid.addWidget(save_button, 0, 0)
        grid.addWidget(load_button, 0, 1)
        grid.addWidget(export_button, 1, 0)
        grid.addWidget(package_button, 1, 1)
        layout.addLayout(grid)
        return group

    def _start_section_line_map_tool(self):
        try:
            canvas = self.iface.mapCanvas()
        except Exception:
            self._warn("QGIS map canvas is not available.")
            return
        self.previous_map_tool = canvas.mapTool()
        self.section_line_tool = SectionLineMapTool(
            canvas,
            self._finish_drawn_section_line,
            self._cancel_drawn_section_line,
        )
        canvas.setMapTool(self.section_line_tool)
        self._info("Click P0 on the map, then move the cursor and click P1. Snapping follows QGIS snapping settings.")

    def _finish_drawn_section_line(self, p0, p1):
        try:
            self._set_section((p0.x(), p0.y()), (p1.x(), p1.y()))
            self._update_workflow_state()
        except ValueError as exc:
            self._warn(str(exc))
            return
        try:
            canvas = self.iface.mapCanvas()
            if self.previous_map_tool is not None:
                canvas.setMapTool(self.previous_map_tool)
        except Exception:
            pass
        self.section_line_tool = None
        self.previous_map_tool = None
        self._info("Section line set from map XY clicks.")

    def _cancel_drawn_section_line(self):
        try:
            canvas = self.iface.mapCanvas()
            if self.previous_map_tool is not None:
                canvas.setMapTool(self.previous_map_tool)
        except Exception:
            pass
        self.section_line_tool = None
        self.previous_map_tool = None
        self._info("Section line drawing canceled.")

    def _use_manual_section(self):
        try:
            p0 = (
                self._float_from_edit(self.x0_edit, "x0"),
                self._float_from_edit(self.y0_edit, "y0"),
            )
            p1 = (
                self._float_from_edit(self.x1_edit, "x1"),
                self._float_from_edit(self.y1_edit, "y1"),
            )
            self._set_section(p0, p1)
            self._update_workflow_state()
        except ValueError as exc:
            self._warn(str(exc))

    def _use_selected_line(self):
        layer = self.iface.activeLayer()
        if layer is None or not hasattr(layer, "selectedFeatures"):
            self._warn("No active vector layer with selected line features.")
            return
        selected = layer.selectedFeatures()
        if not selected:
            self._warn("Select one line feature in the active layer first.")
            return
        feature = selected[0]
        geometry = feature.geometry()
        if geometry is None or geometry.isEmpty():
            self._warn("Selected feature has no geometry.")
            return
        if QgsWkbTypes.geometryType(geometry.wkbType()) != QgsWkbTypes.LineGeometry:
            self._warn("Selected feature is not a line geometry.")
            return
        vertices = list(geometry.vertices())
        if len(vertices) < 2:
            self._warn("Selected line feature must have at least two vertices.")
            return
        try:
            p0 = self._layer_point_to_project_xy(layer, vertices[0])
            p1 = self._layer_point_to_project_xy(layer, vertices[-1])
            self._set_section(p0, p1)
            self._update_workflow_state()
        except Exception as exc:
            self._warn("Could not use selected line: {}".format(exc))

    def _load_selected_plateau_reference(self):
        if self.section_p0 is None or self.section_p1 is None:
            self._warn("Define the section line before loading a PLATEAU reference.")
            return
        settings = self._projection_settings()
        if settings is None:
            return
        layer = self.iface.activeLayer()
        if layer is None or not hasattr(layer, "selectedFeatures"):
            self._warn("Select one PLATEAU/CAD feature in the active vector layer first.")
            return
        try:
            selected_features = layer.selectedFeatures()
        except Exception:
            selected_features = []
        if not selected_features:
            self._warn("Select the target PLATEAU building feature before loading the reference.")
            return

        identity_section_to_pixel = {
            "u": [0.0, 1.0, 0.0],
            "v": [0.0, 0.0, 1.0],
        }
        result = project_vector_layer_vertices(
            layer,
            QgsProject.instance().crs(),
            self.section_p0,
            self.section_p1,
            identity_section_to_pixel,
            settings["z_offset"],
            settings["buffer_width"],
            settings["z_field"],
            features=selected_features,
        )
        try:
            self.reference_canvas.load_reference(
                result["draw_records"], result["draw_segments"]
            )
        except ValueError as exc:
            self._warn(str(exc))
            return
        self.reference_records = result["draw_records"]
        self.reference_segments = result["draw_segments"]
        self.reference_info_label.setText(
            "Loaded {} selected feature(s), {} vertices inside buffer. Move near a vertex/segment to snap, then click to fill target s/z.".format(
                len(selected_features),
                len(result["draw_records"]),
            )
        )
        if result["warnings"]:
            self._warn("PLATEAU reference loaded with warning: {}".format(" ".join(result["warnings"][:3])))
        else:
            self._info("PLATEAU reference section loaded.")

    def _clear_plateau_reference(self):
        self.reference_canvas.clear_reference()
        self.reference_records = []
        self.reference_segments = []
        self.reference_info_label.setText(
            "Select a PLATEAU building feature, then load as snap target for GCPs."
        )
        self._info("PLATEAU reference cleared.")

    def _on_reference_clicked(self, s, z):
        self.gcp_s_edit.setText(_format_float(s))
        self.gcp_z_edit.setText(_format_float(z))
        self._info("GCP target set from PLATEAU reference: s={}, z={}".format(
            _format_float(s),
            _format_float(z),
        ))

    def _set_section(self, p0, p1):
        _direction, _perp, length = compute_section_basis(p0, p1)
        self.section_p0 = (float(p0[0]), float(p0[1]))
        self.section_p1 = (float(p1[0]), float(p1[1]))
        self.section_length = float(length)
        self.x0_edit.setText(_format_float(self.section_p0[0]))
        self.y0_edit.setText(_format_float(self.section_p0[1]))
        self.x1_edit.setText(_format_float(self.section_p1[0]))
        self.y1_edit.setText(_format_float(self.section_p1[1]))
        self._update_section_labels()
        self._info("Section line set.")

    def _load_image_dialog(self):
        path, _filter = QFileDialog.getOpenFileName(
            self,
            "Load section image",
            "",
            "Images (*.png *.jpg *.jpeg *.tif *.tiff *.pdf)",
        )
        if path:
            self._load_image(path)

    def _load_image(self, path):
        try:
            self.canvas.load_image(path)
        except ValueError as exc:
            self._warn(str(exc))
            return
        self.image_path = path
        self.current_pixel = None
        self.clicked_pixel_label.setText("Clicked pixel: -")
        self.canvas.draw_gcp_markers(self.gcps)
        self._update_image_info()
        self._update_workflow_state()
        self._info("Loaded image: {}".format(os.path.basename(path)))

    def _clear_image(self):
        self.canvas.clear_image()
        self.image_path = None
        self.current_pixel = None
        self.clicked_pixel_label.setText("Clicked pixel: -")
        self._update_image_info()
        self._update_workflow_state()
        self._info("Image and overlays cleared.")

    def _on_canvas_clicked(self, u, v):
        self.current_pixel = (float(u), float(v))
        self.clicked_pixel_label.setText(
            "Pixel: u={:.1f}, v={:.1f}".format(u, v)
        )

    def _add_gcp(self):
        if self.current_pixel is None:
            self._warn("Click the section image to choose an image pixel first.")
            return
        try:
            s = self._float_from_edit(self.gcp_s_edit, "target s")
            z = self._float_from_edit(self.gcp_z_edit, "target z")
        except ValueError as exc:
            self._warn(str(exc))
            return
        u, v = self.current_pixel
        self.gcps.append({"u": u, "v": v, "s": s, "z": z})
        self._update_gcp_table()
        self.canvas.draw_gcp_markers(self.gcps)
        if len(self.gcps) >= 3:
            self._compute_transform()
        else:
            self.transform_result = None
            self._update_transform_label()
            self._update_workflow_state()
            self._info("GCP added ({}/3 minimum).".format(len(self.gcps)))

    def _delete_selected_gcp(self):
        row = self.gcp_table.currentRow()
        if row < 0 or row >= len(self.gcps):
            self._warn("Select a GCP row to delete.")
            return
        self.gcps.pop(row)
        self._update_gcp_table()
        self.canvas.draw_gcp_markers(self.gcps)
        if len(self.gcps) >= 3:
            self._compute_transform()
        else:
            self.transform_result = None
            self._update_transform_label()
            self._update_workflow_state()
            self._info("GCP deleted ({} remaining).".format(len(self.gcps)))

    def _clear_gcps(self):
        self.gcps = []
        self.transform_result = None
        self._update_gcp_table()
        self.canvas.clear_gcp_markers()
        self._update_transform_label()
        self._update_workflow_state()
        self._info("GCPs cleared.")

    def _compute_transform(self):
        try:
            self.transform_result = fit_affine_pixel_to_section(self.gcps)
        except (ImportError, ValueError) as exc:
            self.transform_result = None
            self._update_transform_label()
            self._warn(str(exc))
            return
        self._update_transform_label()
        self._update_workflow_state()
        self._info("Affine transform computed (RMS={:.4g}).".format(
            self.transform_result.get("rms", 0.0)
        ))

    def _project_active_point_layer(self):
        if not self._projection_prerequisites_ok():
            return
        settings = self._projection_settings()
        if settings is None:
            return
        layer = self.iface.activeLayer()
        project_crs = QgsProject.instance().crs()
        result = project_point_layer(
            layer,
            project_crs,
            self.section_p0,
            self.section_p1,
            self.transform_result["section_to_pixel"],
            settings["z_offset"],
            settings["buffer_width"],
            settings["z_field"],
        )
        draw_records = result["draw_records"]
        self.canvas.draw_projected_points(draw_records)
        self.projected_records.extend(draw_records)
        self.gps_projected_records.extend(result["records"])
        _merge_summary(self.gps_summary, result["summary"])
        self.projection_warnings.extend(result["warnings"])
        self._update_workflow_state()
        self._show_projection_result(
            result["summary"].get("processed", 0),
            result["summary"].get("inside_buffer", 0),
            _gps_skipped_count(result["summary"]),
            result["warnings"],
        )

    def _project_active_vector_layer(self):
        if not self._projection_prerequisites_ok():
            return
        settings = self._projection_settings()
        if settings is None:
            return
        layer = self.iface.activeLayer()
        project_crs = QgsProject.instance().crs()
        selected_features = []
        try:
            selected_features = layer.selectedFeatures()
        except Exception:
            selected_features = []
        result = project_vector_layer_vertices(
            layer,
            project_crs,
            self.section_p0,
            self.section_p1,
            self.transform_result["section_to_pixel"],
            settings["z_offset"],
            settings["buffer_width"],
            settings["z_field"],
            features=selected_features or None,
        )
        draw_records = result["draw_records"]
        draw_segments = result["draw_segments"]
        self.canvas.draw_projected_vertices(draw_records)
        self.canvas.draw_projected_segments(draw_segments)
        self.projected_records.extend(draw_records)
        self.projected_segments.extend(draw_segments)
        self.plateau_projected_records.extend(result["records"])
        self.plateau_projected_segments.extend(result["segments"])
        _merge_summary(self.plateau_summary, result["summary"])
        self.projection_warnings.extend(result["warnings"])
        if selected_features:
            self.projection_warnings.append(
                "Projected {} selected PLATEAU/CAD feature(s) from layer {}.".format(
                    len(selected_features), layer.name()
                )
            )
        self._update_workflow_state()
        self._show_projection_result(
            result["summary"].get("processed_vertices", 0),
            result["summary"].get("inside_buffer_vertices", 0),
            _plateau_skipped_count(result["summary"]),
            result["warnings"],
        )

    def _clear_projected_overlay(self):
        self.canvas.clear_projected_overlays()
        self.projected_records = []
        self.projected_segments = []
        self.gps_projected_records = []
        self.plateau_projected_records = []
        self.plateau_projected_segments = []
        self.gps_summary = _empty_gps_summary()
        self.plateau_summary = _empty_plateau_summary()
        self.projection_warnings = []
        self._update_workflow_state()
        self._info("Projected overlay cleared.")

    def _save_config_dialog(self):
        path, _filter = QFileDialog.getSaveFileName(
            self, "Save section config", "", "JSON (*.json)"
        )
        if not path:
            return
        if not path.lower().endswith(".json"):
            path += ".json"
        config = self._build_config()
        try:
            config_io.save_config(path, config)
        except ValueError as exc:
            self._warn(str(exc))
            return
        except OSError as exc:
            self._warn("Could not save config: {}".format(exc))
            return
        self._info("Config saved: {}".format(os.path.basename(path)))

    def _load_config_dialog(self):
        path, _filter = QFileDialog.getOpenFileName(
            self, "Load section config", "", "JSON (*.json)"
        )
        if not path:
            return
        try:
            data = config_io.load_config(path)
        except ValueError as exc:
            self._warn(str(exc))
            return
        self._apply_config(data, path)

    def _export_png_dialog(self):
        if not self.canvas.has_image():
            self._warn("Load a section image before exporting.")
            return
        path, _filter = QFileDialog.getSaveFileName(
            self, "Export overlay PNG", "", "PNG (*.png)"
        )
        if not path:
            return
        if not path.lower().endswith(".png"):
            path += ".png"
        try:
            self.canvas.export_png(path)
        except ValueError as exc:
            self._warn(str(exc))
            return
        self._info("PNG exported: {}".format(os.path.basename(path)))

    def _export_section_analysis_package_dialog(self):
        if not self._package_prerequisites_ok():
            return
        output_dir = QFileDialog.getExistingDirectory(
            self, "Choose section analysis package output folder", ""
        )
        if not output_dir:
            return
        export_timestamp = utc_timestamp()
        package_config = self._build_config()
        image_size = self.canvas.image_size()
        package_config["export_timestamp"] = export_timestamp
        package_config.setdefault("image", {})["width"] = image_size[0] if image_size else None
        package_config.setdefault("image", {})["height"] = image_size[1] if image_size else None
        package_config["source_layer_names"] = self._source_layer_names()
        package_config["package_files"] = {
            "section_background": "section_background.png",
            "section_preview": "section_preview.png",
            "section_background_georeferenced": "section_background_georeferenced.tif",
            "gps_projected": "gps_projected.csv",
            "plateau_projected": "plateau_projected.geojson",
            "world_projected": "world_projected.geojson",
            "section_projected_gpkg": "section_projected.gpkg",
            "projection_summary": "projection_summary.json",
        }
        package_config["geotiff"] = {
            "path": "section_background_georeferenced.tif",
            "coordinate_model": "section_space",
            "x_axis": "s_along_section",
            "y_axis": "z_section",
            "crs_authid": package_config.get("crs", ""),
            "warning": (
                "GeoTIFF coordinates are section coordinates (s,z_section), "
                "not map x/y. Use section_config.json p0/p1 to reconstruct "
                "world/project CRS relationships."
            ),
        }

        projection_summary = self._build_projection_summary(export_timestamp)
        gcp_residuals = []
        if self.transform_result is not None:
            gcp_residuals = self.transform_result.get("residuals") or []
        try:
            export_section_analysis_package(
                output_dir,
                self.canvas,
                package_config,
                self.gps_projected_records,
                self.plateau_projected_records,
                self.plateau_projected_segments,
                projection_summary,
                gcp_residuals,
            )
        except (OSError, ValueError) as exc:
            self._warn("Could not export section analysis package: {}".format(exc))
            return
        self._info("Section analysis package exported: {}".format(output_dir))

    def _build_config(self):
        crs = QgsProject.instance().crs()
        z_offset = _float_or_default(self.z_offset_edit.text(), 0.0)
        buffer_width = _float_or_default(self.buffer_width_edit.text(), 10.0)
        transform = None
        if self.transform_result is not None:
            transform = {
                "pixel_to_section": self.transform_result.get("pixel_to_section"),
                "section_to_pixel": self.transform_result.get("section_to_pixel"),
                "rms": self.transform_result.get("rms"),
            }
        return {
            "plugin": PLUGIN_NAME,
            "version": PLUGIN_VERSION,
            "crs": crs.authid() if crs.isValid() else "",
            "section": {
                "p0": list(self.section_p0) if self.section_p0 else None,
                "p1": list(self.section_p1) if self.section_p1 else None,
                "length": self.section_length,
            },
            "image": {
                "path": self.image_path or "",
            },
            "height": {
                "z_offset": z_offset,
                "height_datum_note": "",
            },
            "projection": {
                "buffer_width_m": buffer_width,
                "z_field": self.z_field_edit.text().strip(),
            },
            "gcps": list(self.gcps),
            "transform": transform,
        }

    def _build_projection_summary(self, export_timestamp):
        return {
            "export_timestamp": export_timestamp,
            "gps": dict(self.gps_summary),
            "plateau": dict(self.plateau_summary),
            "warnings": _dedupe(self.projection_warnings),
        }

    def _apply_config(self, data, config_path):
        load_warnings = []
        section = data.get("section") or {}
        p0 = section.get("p0")
        p1 = section.get("p1")
        if p0 and p1:
            try:
                self._set_section((p0[0], p0[1]), (p1[0], p1[1]))
            except Exception as exc:
                self._warn("Config section could not be restored: {}".format(exc))

        height = data.get("height") or {}
        projection = data.get("projection") or {}
        self.z_offset_edit.setText(str(height.get("z_offset", 0.0)))
        self.buffer_width_edit.setText(str(projection.get("buffer_width_m", 10.0)))
        self.z_field_edit.setText(str(projection.get("z_field", "") or ""))

        self.gcps = []
        for gcp in data.get("gcps") or []:
            try:
                self.gcps.append(
                    {
                        "u": float(gcp["u"]),
                        "v": float(gcp["v"]),
                        "s": float(gcp["s"]),
                        "z": float(gcp["z"]),
                    }
                )
            except Exception:
                continue
        self._update_gcp_table()

        transform = data.get("transform")
        if isinstance(transform, dict) and transform.get("pixel_to_section") and transform.get("section_to_pixel"):
            self.transform_result = transform
        else:
            self.transform_result = None
        self._update_transform_label()

        image_path = (data.get("image") or {}).get("path")
        self.image_path = None
        if image_path:
            resolved = config_io.resolve_image_path(image_path, config_path)
            if os.path.exists(resolved):
                self._load_image(resolved)
            else:
                self.canvas.clear_image()
                self.canvas.draw_gcp_markers(self.gcps)
                load_warnings.append("Image path in config does not exist: {}".format(resolved))
        else:
            self.canvas.draw_gcp_markers(self.gcps)
        if load_warnings:
            self._warn("Config loaded with warning: {}".format(" ".join(load_warnings)))
        else:
            self._info("Config loaded.")

    def _projection_prerequisites_ok(self):
        if self.section_p0 is None or self.section_p1 is None:
            self._warn("Define a section line before projecting layers.")
            return False
        if not self.canvas.has_image():
            self._warn("Load a section image before projecting layers.")
            return False
        if self.transform_result is None:
            self._warn("Compute an affine transform before projecting layers.")
            return False
        layer = self.iface.activeLayer()
        if layer is None or not hasattr(layer, "getFeatures"):
            self._warn("No active vector layer is available.")
            return False
        return True

    def _package_prerequisites_ok(self):
        if self.section_p0 is None or self.section_p1 is None:
            self._warn("Define a section line before exporting a package.")
            return False
        if not self.canvas.has_image():
            self._warn("Load a section image before exporting a package.")
            return False
        if self.transform_result is None:
            self._warn("Compute an affine transform before exporting a package.")
            return False
        return True

    def _projection_settings(self):
        try:
            z_offset = self._float_from_edit(self.z_offset_edit, "z_offset")
            buffer_width = self._float_from_edit(
                self.buffer_width_edit, "buffer width"
            )
        except ValueError as exc:
            self._warn(str(exc))
            return None
        if buffer_width < 0:
            self._warn("Buffer width must be zero or positive.")
            return None
        return {
            "z_offset": z_offset,
            "buffer_width": buffer_width,
            "z_field": self.z_field_edit.text().strip() or None,
        }

    def _show_projection_result(self, processed_count, inside_count, skipped_count, warnings):
        message = "Processed {} item(s); inside buffer {}; skipped {}.".format(
            processed_count, inside_count, skipped_count
        )
        if warnings:
            message += " " + " ".join(warnings[:3])
        if warnings or skipped_count:
            self._warn(message)
        else:
            self._info(message)

    def _source_layer_names(self):
        names = []
        for record in self.gps_projected_records + self.plateau_projected_records:
            name = record.get("layer_name")
            if name and name not in names:
                names.append(name)
        return names

    def _update_image_info(self):
        if self.image_path and self.canvas.has_image():
            name = os.path.basename(self.image_path)
            size = self.canvas.image_size()
            if size:
                self.image_info_label.setText(
                    "{} ({}x{} px)".format(name, size[0], size[1])
                )
            else:
                self.image_info_label.setText(name)
            self.image_info_label.setStyleSheet("font-size: 11px; color: palette(text);")
        else:
            self.image_info_label.setText("No image loaded")
            self.image_info_label.setStyleSheet("font-size: 11px; color: palette(dark);")

    def _update_workflow_state(self):
        step1_ok = self.section_p0 is not None and self.section_p1 is not None
        step2_ok = self.canvas.has_image()
        step3_ok = self.transform_result is not None
        n_gcps = len(self.gcps)

        if step1_ok and self.section_length is not None:
            self._section_group_box.setTitle(
                "{} 1. Section line  [{:.1f} m]".format(_STEP_DONE, self.section_length)
            )
        else:
            self._section_group_box.setTitle("{} 1. Section line".format(_STEP_BULLET))

        has_ref = bool(self.reference_records or self.reference_segments)
        if has_ref:
            n_ref = len(self.reference_records)
            self._reference_group_box.setTitle(
                "{} 2. PLATEAU reference  [{} vtx]".format(_STEP_DONE, n_ref)
            )
        else:
            self._reference_group_box.setTitle("{} 2. PLATEAU reference".format(_STEP_BULLET))

        if step2_ok:
            self._image_group_box.setTitle("{} 3. Section image  [loaded]".format(_STEP_DONE))
        else:
            self._image_group_box.setTitle("{} 3. Section image".format(_STEP_BULLET))

        if step3_ok:
            rms = self.transform_result.get("rms", 0.0)
            self._gcp_group_box.setTitle(
                "{} 4. GCP manager  [{} pts, RMS={:.4g}]".format(_STEP_DONE, n_gcps, rms)
            )
        elif n_gcps > 0:
            self._gcp_group_box.setTitle(
                "{} 4. GCP manager  [{}/{} pts]".format(_STEP_BULLET, n_gcps, max(3, n_gcps))
            )
        else:
            self._gcp_group_box.setTitle("{} 4. GCP manager".format(_STEP_BULLET))

        n_gps = len(self.gps_projected_records)
        n_plateau = len(self.plateau_projected_records)
        if n_gps or n_plateau:
            parts = []
            if n_gps:
                parts.append("{} GPS".format(n_gps))
            if n_plateau:
                parts.append("{} PLATEAU".format(n_plateau))
            self._projection_group_box.setTitle(
                "{} 5. Layer projection  [{}]".format(_STEP_DONE, ", ".join(parts))
            )
        else:
            self._projection_group_box.setTitle("{} 5. Layer projection".format(_STEP_BULLET))

    def _update_gcp_table(self):
        self.gcp_table.setRowCount(len(self.gcps))
        for row, gcp in enumerate(self.gcps):
            values = [gcp["u"], gcp["v"], gcp["s"], gcp["z"]]
            for column, value in enumerate(values):
                item = QTableWidgetItem(_format_float(value))
                item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                self.gcp_table.setItem(row, column, item)
        self.gcp_table.resizeColumnsToContents()

    def _update_transform_label(self):
        if self.transform_result is None:
            self.rms_label.setText("RMS: -")
            self.rms_label.setStyleSheet("font-size: 11px; color: palette(dark);")
        else:
            rms = self.transform_result.get("rms", 0.0)
            self.rms_label.setText("RMS: {:.4g}".format(rms))
            self.rms_label.setStyleSheet(
                "font-size: 11px; font-weight: bold; color: #0F6E56;"
                "background-color: #E1F5EE; padding: 2px 6px; border-radius: 3px;"
            )

    def _update_section_labels(self):
        if self.section_length is None:
            self.section_length_label.setText("Length: -")
        else:
            self.section_length_label.setText(
                "Length: {:.3f} m".format(self.section_length)
            )
        self._update_crs_labels()

    def _update_crs_labels(self):
        crs = QgsProject.instance().crs()
        if crs.isValid():
            self.project_crs_label.setText("CRS: {}".format(crs.authid() or crs.description()))
        else:
            self.project_crs_label.setText("CRS: invalid")
        warning = self._project_crs_warning(crs)
        self.crs_warning_label.setText(warning)

    def _project_crs_warning(self, crs):
        if not crs.isValid():
            return "Project CRS is invalid; section distance and buffer behavior may be incorrect."
        if crs.isGeographic():
            return "Project CRS is geographic/non-metric. Use a projected CRS for metric section length and buffer width."
        try:
            if crs.mapUnits() != QgsUnitTypes.DistanceMeters:
                return "Project CRS map units are not meters. Buffer width may not be metric."
        except Exception:
            return "Could not verify project CRS units."
        return ""

    def _layer_point_to_project_xy(self, layer, point):
        project_crs = QgsProject.instance().crs()
        layer_crs = layer.crs()
        x = float(point.x())
        y = float(point.y())
        if layer_crs.isValid() and project_crs.isValid() and layer_crs != project_crs:
            transformer = QgsCoordinateTransform(layer_crs, project_crs, QgsProject.instance())
            point_xy = transformer.transform(QgsPointXY(x, y))
            return (float(point_xy.x()), float(point_xy.y()))
        return (x, y)

    def _float_from_edit(self, edit, label):
        text = edit.text().strip()
        try:
            return float(text)
        except ValueError as exc:
            raise ValueError("{} must be a number.".format(label)) from exc

    def _info(self, message):
        self.status_label.setText(message)
        self.status_label.setStyleSheet(
            "padding: 6px 12px; font-size: 12px;"
            "border-left: 3px solid #1D9E75;"
            "background-color: palette(window);"
            "color: #085041;"
        )
        self._push_message(message, Qgis.Info)

    def _warn(self, message):
        self.status_label.setText(message)
        self.status_label.setStyleSheet(
            "padding: 6px 12px; font-size: 12px;"
            "border-left: 3px solid #BA7517;"
            "background-color: palette(window);"
            "color: #854F0B;"
        )
        self._push_message(message, Qgis.Warning)

    def _push_message(self, message, level):
        try:
            self.iface.messageBar().pushMessage(PLUGIN_NAME, message, level=level, duration=5)
        except Exception:
            if level == Qgis.Critical:
                QMessageBox.critical(self, PLUGIN_NAME, message)


def _primary_button(text):
    btn = QPushButton(text)
    btn.setProperty("primary", True)
    return btn


def _format_float(value):
    try:
        return "{:.12g}".format(float(value))
    except Exception:
        return str(value)


def _float_or_default(text, default):
    try:
        return float(str(text).strip())
    except ValueError:
        return default


class SectionLineMapTool(QgsMapTool):
    """Two-click map tool with snapping, crosshair marker, and rubber band."""

    def __init__(self, canvas, finished_callback, canceled_callback):
        super().__init__(canvas)
        self.canvas = canvas
        self.finished_callback = finished_callback
        self.canceled_callback = canceled_callback
        self.points = []
        self.setCursor(QCursor(Qt.CrossCursor))

        self.preview_line = QgsRubberBand(canvas, QgsWkbTypes.LineGeometry)
        self.preview_line.setColor(QColor(30, 120, 255, 180))
        self.preview_line.setWidth(2)
        try:
            self.preview_line.setLineStyle(Qt.DashLine)
        except Exception:
            pass

        self.fixed_line = QgsRubberBand(canvas, QgsWkbTypes.LineGeometry)
        self.fixed_line.setColor(QColor(30, 120, 255, 220))
        self.fixed_line.setWidth(2)

        self.cursor_marker = QgsVertexMarker(canvas)
        self.cursor_marker.setIconType(QgsVertexMarker.ICON_CROSS)
        self.cursor_marker.setIconSize(14)
        self.cursor_marker.setPenWidth(2)
        self.cursor_marker.setColor(QColor(120, 120, 120))

        self.first_marker = QgsVertexMarker(canvas)
        self.first_marker.setIconType(QgsVertexMarker.ICON_X)
        self.first_marker.setIconSize(14)
        self.first_marker.setPenWidth(2)
        self.first_marker.setColor(QColor(30, 120, 255))
        self.first_marker.hide()

    def canvasMoveEvent(self, event):
        point, snapped = self._event_point(event)
        self._update_cursor_marker(point, snapped)
        if len(self.points) == 1:
            self._update_preview_line(self.points[0], point)

    def canvasReleaseEvent(self, event):
        if event.button() == Qt.RightButton:
            self.cancel()
            return
        if event.button() != Qt.LeftButton:
            return
        point, snapped = self._event_point(event)
        self.points.append(point)
        if len(self.points) == 1:
            self.first_marker.setCenter(point)
            self.first_marker.show()
            self._update_cursor_marker(point, snapped)
            return
        self._update_fixed_line(self.points[0], self.points[1])
        self.cleanup()
        self.finished_callback(self.points[0], self.points[1])

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self.cancel()

    def deactivate(self):
        self.cleanup()
        super().deactivate()

    def cancel(self):
        self.cleanup()
        self.canceled_callback()

    def cleanup(self):
        for item in (
            self.preview_line,
            self.fixed_line,
            self.cursor_marker,
            self.first_marker,
        ):
            try:
                self.canvas.scene().removeItem(item)
            except Exception:
                pass
        try:
            self.canvas.refresh()
        except Exception:
            pass

    def _event_point(self, event):
        snapped = False
        try:
            if event.isSnapped():
                return event.snapPoint(), True
        except Exception:
            pass
        try:
            match = self.canvas.snappingUtils().snapToMap(event.pos())
            if match.isValid():
                return match.point(), True
        except Exception:
            pass
        try:
            return event.mapPoint(), snapped
        except Exception:
            return self.toMapCoordinates(event.pos()), snapped

    def _update_cursor_marker(self, point, snapped):
        self.cursor_marker.setCenter(point)
        if snapped:
            self.cursor_marker.setColor(QColor(220, 60, 30))
        else:
            self.cursor_marker.setColor(QColor(120, 120, 120))

    def _update_preview_line(self, p0, p1):
        self.preview_line.reset(QgsWkbTypes.LineGeometry)
        self.preview_line.addPoint(p0, False)
        self.preview_line.addPoint(p1, True)

    def _update_fixed_line(self, p0, p1):
        self.fixed_line.reset(QgsWkbTypes.LineGeometry)
        self.fixed_line.addPoint(p0, False)
        self.fixed_line.addPoint(p1, True)


def _empty_gps_summary():
    return {
        "processed": 0,
        "inside_buffer": 0,
        "skipped_no_z": 0,
        "skipped_invalid_geometry": 0,
        "skipped_projection_error": 0,
    }


def _empty_plateau_summary():
    return {
        "processed_features": 0,
        "processed_vertices": 0,
        "projected_vertices": 0,
        "inside_buffer_vertices": 0,
        "skipped_no_z": 0,
        "skipped_invalid_geometry": 0,
        "skipped_projection_error": 0,
    }


def _merge_summary(target, update):
    for key, value in update.items():
        if isinstance(value, (int, float)):
            target[key] = target.get(key, 0) + value
        else:
            target[key] = value


def _gps_skipped_count(summary):
    return (
        summary.get("skipped_no_z", 0)
        + summary.get("skipped_invalid_geometry", 0)
        + summary.get("skipped_projection_error", 0)
    )


def _plateau_skipped_count(summary):
    return (
        summary.get("skipped_no_z", 0)
        + summary.get("skipped_invalid_geometry", 0)
        + summary.get("skipped_projection_error", 0)
    )


def _dedupe(values):
    result = []
    for value in values:
        if value in result:
            continue
        result.append(value)
    return result
