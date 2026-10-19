"""Dock widget UI for the Section Georeferencer plugin."""

from __future__ import annotations

import math
import os

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QColor, QCursor
from qgis.PyQt.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
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
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)
from qgis.core import (
    Qgis,
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsPointXY,
    QgsProject,
    QgsUnitTypes,
    QgsWkbTypes,
)
from qgis.gui import QgsMapTool, QgsRubberBand, QgsVertexMarker

from . import config_io
from .layer_projector import project_point_layer, project_vector_layer_vertices
from .package_exporter import (
    export_section_analysis_package,
    package_file_names,
    sanitize_package_name,
    utc_timestamp,
)
from .section_canvas import SectionCanvas, SectionReferenceCanvas
from .transforms import (
    analyze_affine_deformation,
    compute_section_basis,
    deformation_warnings,
    fit_affine_pixel_to_section,
)


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
        # authid of the project CRS the section line coordinates are in.
        self.section_crs = None
        # z offset / buffer / height field that the PLATEAU reference, GCP
        # heights, and projected results were made with. Exports must not
        # describe those results with values the user typed afterwards.
        self.applied_settings = None
        self._reconciling_settings = False
        self.image_path = None
        self.current_pixel = None
        self.gcps = []
        self.transform_result = None
        # Projection results are keyed by source layer name so that projecting
        # the same layer twice replaces its previous result instead of
        # silently duplicating every record in the export.
        self.gps_results = {}
        self.plateau_results = {}
        self.reference_records = []
        self.reference_segments = []
        self.section_line_tool = None
        self.previous_map_tool = None
        self.orientation_guide = None
        self._build_ui()
        try:
            self.orientation_guide = SectionOrientationGuide(self.iface.mapCanvas())
        except Exception:
            # The rest of the dock remains usable in unusual/headless QGIS
            # setups where no map canvas is available.
            self.orientation_guide = None
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

        self.hint_label = QLabel("")
        self.hint_label.setWordWrap(True)
        self.hint_label.setStyleSheet(
            "padding: 4px 12px; font-size: 11px; color: palette(dark);"
            "border-left: 3px solid palette(mid);"
        )
        root_layout.addWidget(self.hint_label)

        splitter = QSplitter(Qt.Horizontal)
        splitter.setChildrenCollapsible(False)

        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(0)
        left_label = QLabel("Section image")
        left_label.setAlignment(Qt.AlignCenter)
        left_label.setStyleSheet(
            "font-size: 11px; font-weight: bold; color: palette(dark);"
            "padding: 2px 0; background-color: palette(window);"
            "border-bottom: 1px solid palette(mid);"
        )
        left_layout.addWidget(left_label)
        self.canvas = SectionCanvas()
        self.canvas.setMinimumHeight(250)
        self.canvas.clickedPixel.connect(self._on_canvas_clicked)
        left_layout.addWidget(self.canvas, stretch=1)

        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(0)
        right_label = QLabel("PLATEAU reference")
        right_label.setAlignment(Qt.AlignCenter)
        right_label.setStyleSheet(
            "font-size: 11px; font-weight: bold; color: palette(dark);"
            "padding: 2px 0; background-color: palette(window);"
            "border-bottom: 1px solid palette(mid);"
        )
        right_layout.addWidget(right_label)
        self.reference_canvas = SectionReferenceCanvas()
        self.reference_canvas.setMinimumHeight(250)
        self.reference_canvas.clickedSection.connect(self._on_reference_clicked)
        right_layout.addWidget(self.reference_canvas, stretch=1)

        splitter.addWidget(left_panel)
        splitter.addWidget(right_panel)
        splitter.setSizes([500, 500])
        root_layout.addWidget(splitter, stretch=1)

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
        button_row = QHBoxLayout()
        button_row.setSpacing(4)
        load_button = _primary_button("Load selected")
        load_button.setToolTip(
            "Project selected PLATEAU/CAD feature(s) into section coordinates.\n"
            "Click the PLATEAU reference panel (right) to snap to vertices/segments and fill GCP target s/z."
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

        option_row = QHBoxLayout()
        option_row.setSpacing(10)
        self.reference_snap_check = QCheckBox("Snap")
        self.reference_snap_check.setChecked(True)
        self.reference_snap_check.setToolTip(
            "Snap PLATEAU reference clicks to vertices, segment endpoints, and nearest segment points."
        )
        self.reference_snap_check.stateChanged.connect(self._set_reference_snap_enabled)
        self.reference_overlay_check = QCheckBox("Overlay on CAD")
        self.reference_overlay_check.setChecked(True)
        self.reference_overlay_check.setToolTip(
            "After 3 or more GCPs compute a transform, overlay the PLATEAU reference on the CAD image."
        )
        self.reference_overlay_check.stateChanged.connect(self._set_reference_overlay_enabled)
        option_row.addWidget(self.reference_snap_check)
        option_row.addWidget(self.reference_overlay_check)
        option_row.addStretch()
        layout.addLayout(option_row)
        return self._reference_group_box

    def _section_group(self):
        self._section_group_box = QGroupBox("1. Section line")
        layout = QVBoxLayout(self._section_group_box)
        layout.setSpacing(6)

        p0_row = QHBoxLayout()
        p0_row.setSpacing(6)
        self.x0_edit = QLineEdit()
        self.y0_edit = QLineEdit()
        p0_label = QLabel("P0 / LEFT")
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
        p1_label = QLabel("P1 / RIGHT")
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
        draw_button.setToolTip(
            "Click the section drawing's left endpoint (P0), then its right endpoint (P1)"
        )
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

        self.gcp_table = QTableWidget(0, 5)
        self.gcp_table.setHorizontalHeaderLabels(
            ["u (px)", "v (px)", "s (m)", "z (m)", "residual (m)"]
        )
        self.gcp_table.setToolTip(
            "Select a row to highlight that GCP on the section image.\n"
            "residual = distance between the target s/z and the value the "
            "fitted transform predicts for the picked pixel."
        )
        self.gcp_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.gcp_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.gcp_table.itemSelectionChanged.connect(self._on_gcp_selection_changed)
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
        self.deformation_label = QLabel("CAD deformation: -")
        self.deformation_label.setWordWrap(True)
        self.deformation_label.setToolTip(
            "Affine deformation of the CAD image. Scale and rotation are reported; "
            "warnings use principal stretch difference (>20%) and shear (>5°)."
        )
        self.deformation_label.setStyleSheet(
            "font-size: 11px; color: palette(dark); padding: 3px 6px;"
        )
        layout.addWidget(self.deformation_label)
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
            "Attribute field name for height (e.g. floor_h, measuredHeight).\n"
            "Point layers: this field overrides geometry Z.\n"
            "Line/polygon layers: geometry Z is kept; the field is used only\n"
            "where the geometry has no Z.\n"
            "Leave empty to use geometry Z or auto-detect."
        )
        z_field_col.addWidget(z_field_label)
        z_field_col.addWidget(self.z_field_edit)
        layout.addLayout(z_field_col)
        for edit in (self.z_offset_edit, self.buffer_width_edit, self.z_field_edit):
            edit.editingFinished.connect(self._on_projection_setting_edited)

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
        if not self._project_crs_usable():
            return
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
        self._info(
            "Click the endpoint that should appear on the LEFT of the section drawing "
            "(P0), then click the RIGHT endpoint (P1). Snapping follows QGIS settings."
        )

    def _finish_drawn_section_line(self, p0, p1):
        error = None
        applied = False
        try:
            applied = self._set_section((p0.x(), p0.y()), (p1.x(), p1.y()))
            self._update_workflow_state()
        except ValueError as exc:
            error = str(exc)
        # The tool has already removed its rubber bands, so it is restored in
        # every outcome; otherwise later map clicks re-fire the same result.
        try:
            canvas = self.iface.mapCanvas()
            if self.previous_map_tool is not None:
                canvas.setMapTool(self.previous_map_tool)
            else:
                canvas.unsetMapTool(self.section_line_tool)
        except Exception:
            pass
        self.section_line_tool = None
        self.previous_map_tool = None
        if error is not None:
            self._warn(error)
        elif applied:
            self._info(
                "Section line set. P0 is the LEFT end and P1 is the RIGHT end of "
                "the section-coordinate view."
            )

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
            applied = self._set_section(p0, p1)
            self._update_workflow_state()
        except ValueError as exc:
            self._warn(str(exc))
            return
        if not applied:
            self._show_section_in_edits()

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
        if not self._section_crs_ok():
            return
        settings = self._settings_for_use()
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
        self.applied_settings = dict(settings)
        self.reference_info_label.setText(
            "Loaded {} selected feature(s), {} vertices inside buffer. Move near a vertex/segment to snap, then click to fill target s/z.".format(
                len(selected_features),
                len(result["draw_records"]),
            )
        )
        self._update_reference_overlay()
        self._update_workflow_state()
        if result["warnings"]:
            self._warn("PLATEAU reference loaded with warning: {}".format(" ".join(result["warnings"][:3])))
        else:
            self._info("PLATEAU reference section loaded.")

    def _clear_plateau_reference(self):
        self._reset_reference_state()
        self._update_workflow_state()
        self._info("PLATEAU reference cleared.")

    def _reset_reference_state(self):
        self.reference_canvas.clear_reference()
        self.reference_records = []
        self.reference_segments = []
        self.canvas.clear_reference_overlay()
        self.reference_info_label.setText(
            "Select a PLATEAU building feature, then load as snap target for GCPs."
        )

    def _on_reference_clicked(self, s, z):
        self.gcp_s_edit.setText(_format_float(s))
        self.gcp_z_edit.setText(_format_float(z))
        self._info("GCP target set from PLATEAU reference: s={}, z={}".format(
            _format_float(s),
            _format_float(z),
        ))

    def _set_reference_snap_enabled(self, state):
        enabled = state == Qt.Checked
        self.reference_canvas.set_snap_enabled(enabled)

    def _set_reference_overlay_enabled(self, _state):
        self._update_reference_overlay()

    def _set_section(self, p0, p1, confirm=True, crs_authid=None):
        """Set the section line; return False if the user kept the old one.

        Every s value (PLATEAU reference, GCP targets, transform, projected
        results) is measured along the current line, so a different line
        discards them after confirmation instead of mixing two lines in one
        export. Raises ValueError for a zero-length line or an unusable CRS.
        """
        _direction, _perp, length = compute_section_basis(p0, p1)
        if crs_authid is None:
            crs = QgsProject.instance().crs()
            problem = _geographic_crs_problem(crs)
            if problem:
                raise ValueError(problem)
            crs_authid = crs.authid()
        new_p0 = (float(p0[0]), float(p0[1]))
        new_p1 = (float(p1[0]), float(p1[1]))
        changes_line = not (
            crs_authid == self.section_crs
            and _same_xy(new_p0, self.section_p0)
            and _same_xy(new_p1, self.section_p1)
        )
        if not changes_line:
            # The edits show 12 significant digits; re-applying them must not
            # count as a new line or replace the full-precision coordinates.
            new_p0, new_p1 = self.section_p0, self.section_p1
        if changes_line and self._has_section_dependents():
            if confirm and not self._confirm(
                "Change section line",
                "The PLATEAU reference, GCP targets, transform, and projected "
                "overlay were measured along the current section line.\n\n"
                "Use the new line and clear them?",
            ):
                self._info("Section line unchanged.")
                return False
            self._clear_section_dependents()
        self.section_p0 = new_p0
        self.section_p1 = new_p1
        self.section_length = float(length)
        self.section_crs = crs_authid
        self._show_section_in_edits()
        if self.orientation_guide is not None:
            self.orientation_guide.set_section(self.section_p0, self.section_p1)
        self._update_section_labels()
        self._info(
            "Section line set. P0 = section LEFT; P1 = section RIGHT."
        )
        return True

    def _clear_section(self):
        self.section_p0 = None
        self.section_p1 = None
        self.section_length = None
        self.section_crs = None
        for edit in (self.x0_edit, self.y0_edit, self.x1_edit, self.y1_edit):
            edit.clear()
        if self.orientation_guide is not None:
            self.orientation_guide.clear()
        self._update_section_labels()

    def _show_section_in_edits(self):
        if self.section_p0 is None or self.section_p1 is None:
            return
        self.x0_edit.setText(_format_float(self.section_p0[0]))
        self.y0_edit.setText(_format_float(self.section_p0[1]))
        self.x1_edit.setText(_format_float(self.section_p1[0]))
        self.y1_edit.setText(_format_float(self.section_p1[1]))

    def _has_section_dependents(self):
        return bool(
            self.reference_records
            or self.reference_segments
            or self.gcps
            or self.transform_result is not None
            or self._has_projection()
        )

    def _clear_section_dependents(self):
        self._reset_reference_state()
        self._reset_projection_state()
        self._reset_gcp_state()
        self.applied_settings = None

    def _reset_gcp_state(self):
        self.gcps = []
        self.transform_result = None
        self.canvas.clear_gcp_markers()
        self.canvas.clear_reference_overlay()
        self._update_gcp_table()
        self._update_transform_label()

    def set_orientation_guide_visible(self, visible):
        """Show the map guide only while this plugin's dock is visible."""
        if self.orientation_guide is not None:
            self.orientation_guide.set_visible(visible)

    def cleanup(self):
        """Remove map-canvas items owned by this dock before plugin unload."""
        if self.section_line_tool is not None:
            tool = self.section_line_tool
            self.section_line_tool = None
            try:
                canvas = self.iface.mapCanvas()
                if self.previous_map_tool is not None:
                    canvas.setMapTool(self.previous_map_tool)
                else:
                    tool.cleanup()
            except Exception:
                try:
                    tool.cleanup()
                except Exception:
                    pass
            self.previous_map_tool = None
        if self.orientation_guide is not None:
            self.orientation_guide.dispose()
            self.orientation_guide = None

    def _load_image_dialog(self):
        path, _filter = QFileDialog.getOpenFileName(
            self,
            "Load section image",
            "",
            "Images (*.png *.jpg *.jpeg *.tif *.tiff *.pdf)",
        )
        if not path:
            return
        replaces_state = self._image_replaces_state(path)
        if replaces_state and not self._confirm_discarding_gcps():
            return
        self._load_image(path, clear_state=replaces_state)

    def _image_replaces_state(self, path):
        """True when the new image invalidates the current GCPs and overlays."""
        if self.image_path is None:
            return False
        if os.path.abspath(path) == os.path.abspath(self.image_path):
            return False
        return bool(self.gcps or self._has_projection())

    def _confirm_discarding_gcps(self):
        return self._confirm(
            "Load section image",
            "The current GCPs, transform, and projected overlay belong to the "
            "image that is loaded now.\n\n"
            "Load the new image and clear them?",
        )

    def _load_image(self, path, clear_state=False):
        try:
            self.canvas.load_image(path)
        except ValueError as exc:
            # Nothing has been discarded yet, so the previous session survives
            # an unreadable file.
            self._warn(str(exc))
            return
        if clear_state:
            self.gcps = []
            self.transform_result = None
            self._reset_projection_state()
            self._update_gcp_table()
            self._update_transform_label()
        self.image_path = path
        self.current_pixel = None
        self.clicked_pixel_label.setText("Pixel: -")
        # Loading an image rebuilds the scene from scratch, so every overlay
        # has to be redrawn from the dock's state.
        self.canvas.draw_gcp_markers(self.gcps)
        self._redraw_projected_overlays()
        self._update_reference_overlay()
        self._update_image_info()
        self._update_workflow_state()
        self._info("Loaded image: {}".format(os.path.basename(path)))

    def _clear_image(self):
        self.canvas.clear_image()
        self.image_path = None
        self.current_pixel = None
        self.clicked_pixel_label.setText("Pixel: -")
        # The scene is gone with the image; drop the overlays it was carrying
        # so the step summary and the exported package cannot disagree with it.
        self._reset_projection_state()
        self._update_image_info()
        self._update_workflow_state()
        self._info("Image and projected overlays cleared. GCPs are kept.")

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
        settings = self._settings_for_use()
        if settings is None:
            return
        u, v = self.current_pixel
        self.gcps.append({"u": u, "v": v, "s": s, "z": z})
        self.applied_settings = dict(settings)
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
            self._update_reference_overlay()
            self._update_workflow_state()
            self._info("GCP deleted ({} remaining).".format(len(self.gcps)))

    def _clear_gcps(self):
        self._reset_gcp_state()
        self._update_workflow_state()
        self._info("GCPs cleared.")

    def _compute_transform(self):
        try:
            self.transform_result = fit_affine_pixel_to_section(self.gcps)
        except (ImportError, ValueError) as exc:
            self.transform_result = None
            self._update_transform_label()
            self._update_gcp_table()
            self._update_reference_overlay()
            self._update_workflow_state()
            self._warn(str(exc))
            return
        self._update_transform_label()
        self._update_gcp_table()
        self._update_reference_overlay()
        self._update_workflow_state()
        warnings = deformation_warnings(self.transform_result.get("deformation"))
        message = "Affine transform computed (RMS={} m from {} GCPs).".format(
            _rms_text(self.transform_result.get("rms")), len(self.gcps)
        )
        if warnings:
            self._warn("{} CAD deformation warning: {}.".format(message, "; ".join(warnings)))
        else:
            self._info(message)

    def _project_active_point_layer(self):
        if not self._projection_prerequisites_ok():
            return
        settings = self._settings_for_use()
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
        layer_name = _layer_name(layer)
        replaced = layer_name in self.gps_results
        self.gps_results[layer_name] = result
        self.applied_settings = dict(settings)
        self._redraw_projected_overlays()
        self._update_workflow_state()
        self._show_projection_result(
            result["summary"].get("processed", 0),
            result["summary"].get("inside_buffer", 0),
            _skipped_count(result["summary"]),
            result["warnings"],
            note=_replaced_note(replaced, layer_name),
        )

    def _project_active_vector_layer(self):
        if not self._projection_prerequisites_ok():
            return
        settings = self._settings_for_use()
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
        layer_name = _layer_name(layer)
        if selected_features:
            result["warnings"].append(
                "Projected {} selected PLATEAU/CAD feature(s) from layer {}.".format(
                    len(selected_features), layer_name
                )
            )
        replaced = layer_name in self.plateau_results
        self.plateau_results[layer_name] = result
        self.applied_settings = dict(settings)
        self._redraw_projected_overlays()
        self._update_workflow_state()
        self._show_projection_result(
            result["summary"].get("processed_vertices", 0),
            result["summary"].get("inside_buffer_vertices", 0),
            _skipped_count(result["summary"]),
            result["warnings"],
            note=_replaced_note(replaced, layer_name),
        )

    def _clear_projected_overlay(self):
        self._reset_projection_state()
        self._update_workflow_state()
        self._info("Projected overlay cleared.")

    def _reset_projection_state(self):
        self.canvas.clear_projected_overlays()
        self.gps_results = {}
        self.plateau_results = {}

    def _has_projection(self):
        return bool(self.gps_results or self.plateau_results)

    def _redraw_projected_overlays(self):
        """Rebuild every projected overlay item from the stored results."""
        self.canvas.clear_projected_overlays()
        for result in self.gps_results.values():
            self.canvas.draw_projected_points(result["draw_records"])
        for result in self.plateau_results.values():
            self.canvas.draw_projected_vertices(result["draw_records"])
            self.canvas.draw_projected_segments(result["draw_segments"])

    def _gps_records(self):
        return [
            record
            for result in self.gps_results.values()
            for record in result["records"]
        ]

    def _plateau_records(self):
        return [
            record
            for result in self.plateau_results.values()
            for record in result["records"]
        ]

    def _plateau_segments(self):
        return [
            segment
            for result in self.plateau_results.values()
            for segment in result["segments"]
        ]

    def _projection_warnings(self):
        return _dedupe(
            [
                warning
                for results in (self.gps_results, self.plateau_results)
                for result in results.values()
                for warning in result["warnings"]
            ]
        )

    def _save_config_dialog(self):
        path, _filter = QFileDialog.getSaveFileName(
            self, "Save section config", "", "JSON (*.json)"
        )
        if not path:
            return
        if not path.lower().endswith(".json"):
            path += ".json"
        if self._settings_for_use() is None:
            return
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
        if self._settings_for_use() is None:
            return
        default_name = sanitize_package_name(
            "section_package_{}".format(utc_timestamp())
        )
        output_dir, _filter = QFileDialog.getSaveFileName(
            self, "Create section analysis package folder", default_name, "Folder (*)"
        )
        if not output_dir:
            return
        # A trailing separator would make basename() empty and the file-name
        # prefix stop matching the folder the user named.
        output_dir = output_dir.rstrip("/\\") or output_dir
        if os.path.isfile(output_dir):
            self._warn("A file already exists at the selected package path.")
            return
        if os.path.isdir(output_dir) and os.listdir(output_dir):
            result = QMessageBox.question(
                self,
                "Export package",
                (
                    "The selected package folder already exists and is not empty.\n"
                    "Files with the same names may be overwritten. Continue?"
                ),
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if result != QMessageBox.Yes:
                return
        export_timestamp = utc_timestamp()
        package_name = sanitize_package_name(os.path.basename(output_dir))
        package_files = package_file_names(package_name)
        package_config = self._build_config()
        image_size = self.canvas.image_size()
        package_config["package_name"] = package_name
        package_config["export_timestamp"] = export_timestamp
        package_config.setdefault("image", {})["width"] = image_size[0] if image_size else None
        package_config.setdefault("image", {})["height"] = image_size[1] if image_size else None
        package_config["source_layer_names"] = self._source_layer_names()
        package_config["package_files"] = package_files
        package_config["geotiff"] = {
            "path": package_files["section_background_georeferenced"],
            "coordinate_model": "section_space",
            "x_axis": "s_along_section",
            "y_axis": "z_section",
            "crs_authid": package_config.get("crs", ""),
            "warning": (
                "GeoTIFF coordinates are section coordinates (s,z_section), "
                "not map x/y. Use the exported config p0/p1 to reconstruct "
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
                self._gps_records(),
                self._plateau_records(),
                self._plateau_segments(),
                projection_summary,
                gcp_residuals,
                package_files,
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
                "deformation": self.transform_result.get("deformation"),
            }
        return {
            "plugin": PLUGIN_NAME,
            "version": PLUGIN_VERSION,
            "crs": self.section_crs or (crs.authid() if crs.isValid() else ""),
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
            "gps": _rebuild_summary(_empty_gps_summary(), self.gps_results),
            "plateau": _rebuild_summary(_empty_plateau_summary(), self.plateau_results),
            "source_layers": {
                "gps": sorted(self.gps_results),
                "plateau": sorted(self.plateau_results),
            },
            "warnings": self._projection_warnings(),
        }

    def _apply_config(self, data, config_path):
        load_warnings = []
        # The config replaces the section line, the settings, the GCPs, and
        # the transform, so the PLATEAU reference and anything projected in
        # the previous session no longer belong to it.
        if self._has_projection():
            load_warnings.append("Previously projected overlay was cleared.")
        if self.reference_records or self.reference_segments:
            load_warnings.append("Previous PLATEAU reference was cleared.")
        self._clear_section_dependents()
        self._clear_section()
        section = data.get("section") or {}
        p0 = section.get("p0")
        p1 = section.get("p1")
        project_authid = QgsProject.instance().crs().authid()
        config_authid = str(data.get("crs") or "").strip() or None
        config_geographic = bool(config_authid) and bool(
            _geographic_crs_problem(QgsCoordinateReferenceSystem(config_authid))
        )
        if p0 and p1 and config_geographic:
            # Earlier versions recommended EPSG:6697; such a line and the GCP
            # targets measured along it are in degrees and cannot be reused.
            load_warnings.append(
                "The config was saved in the geographic CRS {}, so its section "
                "line and GCP targets are in degrees and were not restored. "
                "Set a projected CRS such as JGD2011 / Japan Plane Rectangular "
                "CS (EPSG:6669-6687), redefine the section line, and pick the "
                "GCPs again.".format(config_authid)
            )
        elif p0 and p1:
            try:
                if config_authid and config_authid != project_authid:
                    # Keep the line in the CRS it was saved in; the CRS guard
                    # then blocks use until the project CRS matches again.
                    self._set_section(
                        (p0[0], p0[1]), (p1[0], p1[1]), confirm=False,
                        crs_authid=config_authid,
                    )
                    load_warnings.append(
                        "The config section line is in {} but the project CRS "
                        "is {}. Set the project CRS to {} before using it.".format(
                            config_authid, project_authid or "unset", config_authid
                        )
                    )
                else:
                    self._set_section((p0[0], p0[1]), (p1[0], p1[1]), confirm=False)
            except Exception as exc:
                load_warnings.append(
                    "Config section could not be restored: {}".format(exc)
                )

        height = data.get("height") or {}
        projection = data.get("projection") or {}
        self.z_offset_edit.setText(_number_text(height.get("z_offset"), 0.0))
        self.buffer_width_edit.setText(
            _number_text(projection.get("buffer_width_m"), 10.0)
        )
        self.z_field_edit.setText(str(projection.get("z_field", "") or ""))

        self.gcps = []
        for gcp in [] if config_geographic else data.get("gcps") or []:
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
        # The GCP heights were picked with the config's z offset.
        self.applied_settings = (
            self._projection_settings(report=False) if self.gcps else None
        )

        transform = None if config_geographic else data.get("transform")
        if isinstance(transform, dict) and transform.get("pixel_to_section") and transform.get("section_to_pixel"):
            self.transform_result = dict(transform)
            try:
                self.transform_result["deformation"] = analyze_affine_deformation(
                    self.transform_result["pixel_to_section"]
                )
            except ValueError as exc:
                load_warnings.append("CAD deformation could not be measured: {}".format(exc))
        else:
            self.transform_result = None
        self._update_transform_label()
        self._update_gcp_table()
        if self.transform_result is not None:
            warnings = deformation_warnings(self.transform_result.get("deformation"))
            if warnings:
                load_warnings.append("CAD deformation warning: {}.".format("; ".join(warnings)))

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
        self._update_reference_overlay()
        self._update_workflow_state()
        if load_warnings:
            self._warn("Config loaded with warning: {}".format(" ".join(load_warnings)))
        else:
            self._info("Config loaded.")

    def _projection_prerequisites_ok(self):
        if self.section_p0 is None or self.section_p1 is None:
            self._warn("Define a section line before projecting layers.")
            return False
        if not self._section_crs_ok():
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
        if not self._section_crs_ok():
            return False
        if not self.canvas.has_image():
            self._warn("Load a section image before exporting a package.")
            return False
        if self.transform_result is None:
            self._warn("Compute an affine transform before exporting a package.")
            return False
        return True

    def _projection_settings(self, report=True):
        try:
            z_offset = self._float_from_edit(self.z_offset_edit, "z_offset")
            buffer_width = self._float_from_edit(
                self.buffer_width_edit, "buffer width"
            )
            if not (math.isfinite(z_offset) and math.isfinite(buffer_width)):
                raise ValueError("z_offset and buffer width must be finite numbers.")
        except ValueError as exc:
            if report:
                self._warn(str(exc))
            return None
        if buffer_width < 0:
            if report:
                self._warn("Buffer width must be zero or positive.")
            return None
        return {
            "z_offset": z_offset,
            "buffer_width": buffer_width,
            "z_field": self.z_field_edit.text().strip() or None,
        }

    def _settings_for_use(self):
        """Current projection settings, reconciled with existing results.

        Returns None when the settings are invalid or when the user chose to
        keep the results made with the previous settings (the edits are then
        restored to those settings).
        """
        settings = self._projection_settings()
        if settings is None:
            return None
        if self._reconcile_settings(settings):
            return settings
        return None

    def _on_projection_setting_edited(self):
        if self._reconciling_settings:
            return
        settings = self._projection_settings(report=False)
        if settings is not None:
            self._reconcile_settings(settings)

    def _reconcile_settings(self, settings):
        applied = self.applied_settings
        if applied is None or self._reconciling_settings:
            return True
        changed = [key for key in applied if applied.get(key) != settings.get(key)]
        if not changed:
            return True
        has_reference = bool(self.reference_records or self.reference_segments)
        # z_section = z - z_offset, so a new offset also moves every GCP
        # height; the buffer and height field only change which vertices and
        # heights the reference and the projections pick up.
        clears_gcps = "z_offset" in changed and bool(
            self.gcps or self.transform_result is not None
        )
        cleared = []
        if has_reference:
            cleared.append("the PLATEAU reference")
        if clears_gcps:
            cleared.append("the GCPs and transform")
        if self._has_projection():
            cleared.append("the projected overlay")
        if not cleared:
            self.applied_settings = dict(settings) if self.gcps else None
            return True
        names = {
            "z_offset": "z offset",
            "buffer_width": "buffer",
            "z_field": "height field",
        }
        self._reconciling_settings = True
        try:
            accepted = self._confirm(
                "Change projection settings",
                "The {} changed. {} {} made with the previous value.\n\n"
                "Apply the new value and clear {}?".format(
                    " and ".join(names[key] for key in changed),
                    _capitalize(_join_words(cleared)),
                    "was" if len(cleared) == 1 else "were",
                    _join_words(cleared),
                ),
            )
            if not accepted:
                self._show_settings_in_edits(applied)
                self._info("Kept the previous projection settings.")
                return False
            if has_reference:
                self._reset_reference_state()
            if clears_gcps:
                self._reset_gcp_state()
            self._reset_projection_state()
        finally:
            self._reconciling_settings = False
        self.applied_settings = dict(settings) if self.gcps else None
        self._update_workflow_state()
        self._info("Projection settings changed; {} cleared.".format(_join_words(cleared)))
        return True

    def _show_settings_in_edits(self, settings):
        self.z_offset_edit.setText(_format_float(settings["z_offset"]))
        self.buffer_width_edit.setText(_format_float(settings["buffer_width"]))
        self.z_field_edit.setText(settings.get("z_field") or "")

    def _project_crs_usable(self):
        problem = _geographic_crs_problem(QgsProject.instance().crs())
        if problem:
            self._warn(problem)
            return False
        return True

    def _section_crs_ok(self):
        """The section line is only meaningful in the CRS it was drawn in."""
        if not self._project_crs_usable():
            return False
        current = QgsProject.instance().crs().authid()
        if self.section_crs and current != self.section_crs:
            self._warn(
                "The project CRS changed from {} to {} after the section line "
                "was defined. Switch the project CRS back to {} or redefine "
                "the section line.".format(
                    self.section_crs, current or "unset", self.section_crs
                )
            )
            return False
        return True

    def _confirm(self, title, text):
        answer = QMessageBox.question(
            self, title, text, QMessageBox.Yes | QMessageBox.No, QMessageBox.No
        )
        return answer == QMessageBox.Yes

    def _show_projection_result(
        self, processed_count, inside_count, skipped_count, warnings, note=None
    ):
        message = "Processed {} item(s); inside buffer {}; skipped {}.".format(
            processed_count, inside_count, skipped_count
        )
        if note:
            message = "{} {}".format(note, message)
        if warnings:
            message += " " + " ".join(warnings[:3])
        if warnings or skipped_count:
            self._warn(message)
        else:
            self._info(message)

    def _source_layer_names(self):
        return _dedupe(
            [name for name in list(self.gps_results) + list(self.plateau_results) if name]
        )

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

        self.reference_overlay_check.setEnabled(has_ref and step3_ok)

        if step2_ok:
            self._image_group_box.setTitle("{} 3. Section image  [loaded]".format(_STEP_DONE))
        else:
            self._image_group_box.setTitle("{} 3. Section image".format(_STEP_BULLET))

        if step3_ok:
            self._gcp_group_box.setTitle(
                "{} 4. GCP manager  [{} pts, RMS={}]".format(
                    _STEP_DONE, n_gcps, _rms_text(self.transform_result.get("rms"))
                )
            )
        elif n_gcps > 0:
            self._gcp_group_box.setTitle(
                "{} 4. GCP manager  [{}/{} pts]".format(_STEP_BULLET, n_gcps, max(3, n_gcps))
            )
        else:
            self._gcp_group_box.setTitle("{} 4. GCP manager".format(_STEP_BULLET))

        n_gps = len(self._gps_records())
        n_plateau = len(self._plateau_records())
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

        self.hint_label.setText(
            "Next: {}".format(
                self._next_step_hint(step1_ok, step2_ok, step3_ok, has_ref, n_gcps)
            )
        )

    def _next_step_hint(self, step1_ok, step2_ok, step3_ok, has_ref, n_gcps):
        if not step1_ok:
            return (
                "define the section line — click 'Draw on map' and pick P0 and P1 "
                "on the QGIS map canvas."
            )
        if not step2_ok:
            return "load the scanned section drawing with 'Load image' in step 3."
        if not has_ref:
            return (
                "select the target PLATEAU/CAD feature in the QGIS layer, then "
                "click 'Load selected' in step 2 to get snap targets."
            )
        if not step3_ok:
            return (
                "add {} more GCP(s): click a point on the section image (left), "
                "click the matching point on the PLATEAU reference (right), then "
                "'Add GCP'. The transform is computed automatically at 3.".format(
                    max(0, 3 - n_gcps)
                )
            )
        if not self._has_projection():
            return (
                "activate a GPS or PLATEAU/CAD layer in the QGIS layer panel and "
                "project it in step 5."
            )
        return "export the overlay PNG or the full analysis package in step 6."

    def _update_gcp_table(self):
        residuals = []
        if self.transform_result is not None:
            residuals = self.transform_result.get("residuals") or []
        self.gcp_table.setRowCount(len(self.gcps))
        for row, gcp in enumerate(self.gcps):
            cells = [
                _format_float(gcp["u"]),
                _format_float(gcp["v"]),
                _format_float(gcp["s"]),
                _format_float(gcp["z"]),
                _residual_text(residuals, row),
            ]
            for column, text in enumerate(cells):
                item = QTableWidgetItem(text)
                item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                self.gcp_table.setItem(row, column, item)
        self.gcp_table.resizeColumnsToContents()

    def _on_gcp_selection_changed(self):
        # Also reached while the table is being rebuilt, including during
        # __init__ before any GCP exists.
        if not self.gcps:
            self.canvas.clear_highlight()
            return
        row = self.gcp_table.currentRow()
        selection_model = self.gcp_table.selectionModel()
        if 0 <= row < len(self.gcps) and selection_model is not None and selection_model.hasSelection():
            self.canvas.highlight_gcp(self.gcps[row])
        else:
            self.canvas.clear_highlight()

    def _update_reference_overlay(self):
        overlay_check = getattr(self, "reference_overlay_check", None)
        overlay_enabled = overlay_check is None or overlay_check.isChecked()
        if (
            not overlay_enabled
            or self.transform_result is None
            or not (self.reference_records or self.reference_segments)
        ):
            self.canvas.clear_reference_overlay()
            return
        self.canvas.draw_reference_overlay(
            self.reference_records,
            self.reference_segments,
            self.transform_result["section_to_pixel"],
        )

    def _update_transform_label(self):
        if self.transform_result is None:
            self.rms_label.setText("RMS: -")
            self.rms_label.setStyleSheet("font-size: 11px; color: palette(dark);")
            self.deformation_label.setText("CAD deformation: -")
            self.deformation_label.setStyleSheet(
                "font-size: 11px; color: palette(dark); padding: 3px 6px;"
            )
        else:
            self.rms_label.setText(
                "RMS: {}".format(_rms_text(self.transform_result.get("rms")))
            )
            self.rms_label.setStyleSheet(
                "font-size: 11px; font-weight: bold; color: #0F6E56;"
                "background-color: #E1F5EE; padding: 2px 6px; border-radius: 3px;"
            )
            deformation = self.transform_result.get("deformation")
            if deformation is None:
                try:
                    deformation = analyze_affine_deformation(
                        self.transform_result["pixel_to_section"]
                    )
                    self.transform_result["deformation"] = deformation
                except (KeyError, ValueError):
                    deformation = None
            self.deformation_label.setText(_deformation_text(deformation))
            warnings = deformation_warnings(deformation)
            if warnings:
                self.deformation_label.setStyleSheet(
                    "font-size: 11px; font-weight: bold; color: #854F0B;"
                    "background-color: #FAEEDA; padding: 4px 6px; border-radius: 3px;"
                )
            else:
                self.deformation_label.setStyleSheet(
                    "font-size: 11px; color: #085041;"
                    "background-color: #E1F5EE; padding: 4px 6px; border-radius: 3px;"
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
        problem = _geographic_crs_problem(crs)
        if problem:
            return problem
        if self.section_crs and crs.authid() != self.section_crs:
            return (
                "The section line was defined in {}; switch the project CRS "
                "back or redefine the line.".format(self.section_crs)
            )
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


def _geographic_crs_problem(crs):
    """Why a CRS cannot carry section coordinates, or "" if it can.

    s, d, and the buffer are planar distances in CRS units while z is in
    metres. In a geographic CRS (EPSG:6697, JGD2011 + height, included) they
    would be degrees.
    """
    if not crs.isValid():
        return (
            "The project CRS is not set. Set a projected metric CRS such as "
            "JGD2011 / Japan Plane Rectangular CS (EPSG:6669-6687)."
        )
    if crs.isGeographic():
        return (
            "The project CRS {} is geographic, so section distance and buffer "
            "would be in degrees. Set a projected metric CRS such as "
            "JGD2011 / Japan Plane Rectangular CS (EPSG:6669-6687).".format(
                crs.authid() or crs.description()
            )
        )
    return ""


def _same_xy(a, b):
    if a is None or b is None:
        return a is b
    return all(
        math.isclose(float(u), float(v), rel_tol=1e-11, abs_tol=1e-6)
        for u, v in zip(a, b)
    )


def _join_words(items):
    if len(items) <= 1:
        return "".join(items)
    return "{} and {}".format(", ".join(items[:-1]), items[-1])


def _capitalize(text):
    return text[:1].upper() + text[1:]


def _primary_button(text):
    btn = QPushButton(text)
    btn.setProperty("primary", True)
    return btn


def _format_float(value):
    try:
        return "{:.12g}".format(float(value))
    except Exception:
        return str(value)


def _number_text(value, default):
    """Format a config number for a line edit, falling back on bad values."""
    try:
        return _format_float(float(value))
    except (TypeError, ValueError):
        return _format_float(default)


def _residual_text(residuals, row):
    """Per-GCP fit error, or '-' when no transform has been computed yet."""
    if row >= len(residuals):
        return "-"
    residual = residuals[row]
    try:
        return "{:.4g}".format(
            math.hypot(float(residual["ds"]), float(residual["dz"]))
        )
    except (KeyError, TypeError, ValueError):
        return "-"


def _rms_text(value):
    try:
        return "{:.4g}".format(float(value))
    except (TypeError, ValueError):
        return "n/a"


def _deformation_text(deformation):
    if not isinstance(deformation, dict):
        return "CAD deformation: n/a"
    try:
        text = (
            "CAD deformation: u={:.5g} m/px, v={:.5g} m/px | "
            "stretch diff={:.1f}% | shear={:.1f}° | rotation={:.1f}° | "
            "area={:.5g} m²/px²"
        ).format(
            float(deformation["scale_u_m_per_px"]),
            float(deformation["scale_v_m_per_px"]),
            float(deformation["stretch_difference_percent"]),
            float(deformation["shear_degrees"]),
            float(deformation["rotation_degrees"]),
            float(deformation["area_scale_m2_per_px2"]),
        )
    except (KeyError, TypeError, ValueError):
        return "CAD deformation: n/a"
    warnings = deformation_warnings(deformation)
    if warnings:
        return "⚠ {} | {}".format(text, "; ".join(warnings))
    return text


def _float_or_default(text, default):
    try:
        return float(str(text).strip())
    except ValueError:
        return default


class SectionOrientationGuide:
    """Show which map endpoint becomes each end of the section-coordinate view."""

    _LINE_COLOR = QColor(74, 78, 165, 235)
    _P0_COLOR = QColor(25, 113, 194, 235)
    _P1_COLOR = QColor(217, 95, 14, 235)

    def __init__(self, canvas):
        self.canvas = canvas
        self.p0 = None
        self.p1 = None
        self.visible = True
        self._disposed = False

        self.line_halo = self._rubber_band(QColor(255, 255, 255, 220), 7)
        self.line = self._rubber_band(self._LINE_COLOR, 4)
        self.arrow = self._rubber_band(self._LINE_COLOR, 4)
        self.p0_leader = self._rubber_band(self._P0_COLOR, 3, Qt.DashLine)
        self.p1_leader = self._rubber_band(self._P1_COLOR, 3, Qt.DashLine)

        self.start_marker = self._marker(QgsVertexMarker.ICON_BOX, self._P0_COLOR)
        self.end_marker = self._marker(QgsVertexMarker.ICON_CIRCLE, self._P1_COLOR)

        self.start_label = self._label(
            "断面左  P0  (s=0)", "white", "rgba(25, 113, 194, 235)"
        )
        self.end_label = self._label(
            "断面右  P1  (s=L)", "white", "rgba(217, 95, 14, 235)"
        )

        self._bands = (
            self.line_halo,
            self.line,
            self.arrow,
            self.p0_leader,
            self.p1_leader,
        )
        self._markers = (self.start_marker, self.end_marker)
        self._labels = (self.start_label, self.end_label)
        self._signals = []
        for signal_name in (
            "extentsChanged",
            "rotationChanged",
            "destinationCrsChanged",
            "mapCanvasRefreshed",
        ):
            signal = getattr(canvas, signal_name, None)
            if signal is None:
                continue
            try:
                signal.connect(self._redraw)
                self._signals.append(signal)
            except Exception:
                pass
        self._apply_visibility(False)

    def _rubber_band(self, color, width, line_style=None):
        band = QgsRubberBand(self.canvas, QgsWkbTypes.LineGeometry)
        band.setColor(color)
        band.setWidth(width)
        if line_style is not None:
            try:
                band.setLineStyle(line_style)
            except Exception:
                pass
        return band

    def _marker(self, icon_type, color):
        marker = QgsVertexMarker(self.canvas)
        marker.setIconType(icon_type)
        marker.setIconSize(13)
        marker.setPenWidth(3)
        marker.setColor(color)
        return marker

    def _label(self, text, foreground, background):
        label = QLabel(text, self.canvas)
        label.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        label.setStyleSheet(
            "QLabel {{ color: {}; background-color: {}; border: 1px solid {}; "
            "border-radius: 4px; padding: 3px 6px; font-size: 11px; "
            "font-weight: bold; }}".format(foreground, background, foreground)
        )
        label.setToolTip(
            "P0 is s=0 at the section view's left end; P1 is s=L at its right end"
        )
        label.adjustSize()
        return label

    def set_section(self, p0, p1):
        self.p0 = QgsPointXY(float(p0[0]), float(p0[1]))
        self.p1 = QgsPointXY(float(p1[0]), float(p1[1]))
        self._redraw()

    def clear(self):
        self.p0 = None
        self.p1 = None
        self._redraw()

    def set_visible(self, visible):
        self.visible = bool(visible)
        self._redraw()

    def _redraw(self, *_args):
        if self._disposed or self.p0 is None or self.p1 is None:
            self._apply_visibility(False)
            return

        dx = self.p1.x() - self.p0.x()
        dy = self.p1.y() - self.p0.y()
        length = math.hypot(dx, dy)
        if length <= 0.0:
            self._apply_visibility(False)
            return
        ux, uy = dx / length, dy / length
        left_x, left_y = -uy, ux
        map_units_per_pixel = self.canvas.mapSettings().mapUnitsPerPixel()

        arrow_back = min(22.0 * map_units_per_pixel, length * 0.28)
        arrow_half_width = min(10.0 * map_units_per_pixel, length * 0.13)
        arrow_base_x = self.p1.x() - ux * arrow_back
        arrow_base_y = self.p1.y() - uy * arrow_back
        arrow_left = QgsPointXY(
            arrow_base_x + left_x * arrow_half_width,
            arrow_base_y + left_y * arrow_half_width,
        )
        arrow_right = QgsPointXY(
            arrow_base_x - left_x * arrow_half_width,
            arrow_base_y - left_y * arrow_half_width,
        )

        self._set_line(self.line_halo, self.p0, self.p1)
        self._set_line(self.line, self.p0, self.p1)
        self._set_polyline(self.arrow, (arrow_left, self.p1, arrow_right))
        self.start_marker.setCenter(self.p0)
        self.end_marker.setCenter(self.p1)

        along_offset = 24.0 * map_units_per_pixel
        side_offset = 18.0 * map_units_per_pixel
        start_anchor = QgsPointXY(
            self.p0.x() - ux * along_offset + left_x * side_offset,
            self.p0.y() - uy * along_offset + left_y * side_offset,
        )
        end_anchor = QgsPointXY(
            self.p1.x() + ux * along_offset + left_x * side_offset,
            self.p1.y() + uy * along_offset + left_y * side_offset,
        )
        self._set_line(self.p0_leader, self.p0, start_anchor)
        self._set_line(self.p1_leader, self.p1, end_anchor)
        self._place_label(self.start_label, start_anchor)
        self._place_label(self.end_label, end_anchor)
        self._apply_visibility(self.visible)

    def _set_line(self, band, p0, p1):
        self._set_polyline(band, (p0, p1))

    @staticmethod
    def _set_polyline(band, points):
        band.reset(QgsWkbTypes.LineGeometry)
        last = len(points) - 1
        for index, point in enumerate(points):
            band.addPoint(point, index == last)

    def _place_label(self, label, map_point):
        pixel = self.canvas.mapSettings().mapToPixel().transform(map_point)
        label.adjustSize()
        label.move(
            int(round(pixel.x() - label.width() / 2.0)),
            int(round(pixel.y() - label.height() / 2.0)),
        )
        label.raise_()

    def _apply_visibility(self, visible):
        for item in getattr(self, "_bands", ()) + getattr(self, "_markers", ()):
            try:
                item.setVisible(bool(visible))
            except Exception:
                pass
        for label in getattr(self, "_labels", ()):
            label.setVisible(bool(visible))

    def dispose(self):
        if self._disposed:
            return
        self._disposed = True
        for signal in self._signals:
            try:
                signal.disconnect(self._redraw)
            except Exception:
                pass
        self._signals = []
        for item in self._bands + self._markers:
            try:
                self.canvas.scene().removeItem(item)
            except Exception:
                pass
        for label in self._labels:
            label.hide()
            label.deleteLater()
        try:
            self.canvas.refresh()
        except Exception:
            pass


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


def _rebuild_summary(empty, results):
    """Sum per-layer summaries from scratch so counts match the exported rows."""
    summary = dict(empty)
    for result in results.values():
        for key, value in result["summary"].items():
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                summary[key] = summary.get(key, 0) + value
            else:
                summary[key] = value
    return summary


def _layer_name(layer):
    try:
        return layer.name()
    except Exception:
        return ""


def _replaced_note(replaced, layer_name):
    """Replacing a layer's earlier projection is expected, not a warning."""
    if not replaced:
        return None
    return "Replaced the previous projection of layer {}.".format(layer_name)


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


def _skipped_count(summary):
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
