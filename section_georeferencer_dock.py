"""Dock widget UI for the Section Georeferencer plugin."""

from __future__ import annotations

import math
import os
from contextlib import contextmanager

from qgis.PyQt.QtCore import Qt, QTimer
from qgis.PyQt.QtGui import QBrush, QColor, QCursor
from qgis.PyQt.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QDockWidget,
    QApplication,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QToolBox,
    QVBoxLayout,
    QWidget,
)
from qgis.core import (
    Qgis,
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsExpression,
    QgsFeature,
    QgsFeatureRequest,
    QgsGeometry,
    QgsMapLayerProxyModel,
    QgsPointXY,
    QgsProject,
    QgsUnitTypes,
    QgsWkbTypes,
)
from qgis.gui import (
    QgsHighlight,
    QgsMapLayerComboBox,
    QgsMapTool,
    QgsMapToolIdentifyFeature,
    QgsRubberBand,
    QgsVertexMarker,
)

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
    pixel_to_section,
)


PLUGIN_NAME = "Section Georeferencer"
PLUGIN_VERSION = "0.2.4"

_STEP_DONE = "✓"
_STEP_BULLET = "•"

_DOCK_STYLESHEET = """
QDockWidget {
    font-size: 13px;
}
QDockWidget::title {
    padding: 6px 10px;
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

/* Workflow steps */
QToolBox::tab {
    font-weight: bold;
    font-size: 12px;
    padding: 4px 8px;
    border: 1px solid palette(mid);
    border-radius: 3px;
}
QToolBox::tab:selected {
    background-color: palette(highlight);
    color: palette(highlighted-text);
}

/* Scroll area */
QScrollArea {
    border: none;
}
"""

_STEP_TITLES = (
    "1. Building",
    "2. Section line",
    "3. Section image",
    "4. GCPs",
    "5. Projection",
    "6. Export",
)
# The building comes first: the section line is then drawn through it, and the
# PLATEAU reference is projected as soon as both exist.
STEP_BUILDING, STEP_SECTION, STEP_IMAGE, STEP_GCP, STEP_PROJECTION, STEP_EXPORT = range(6)

# Window width while the section line is drawn on the QGIS map: the step
# sidebar only.
COMPACT_WINDOW_WIDTH = 380

# GCP fit quality bands for RMS and per-GCP residuals, in metres.
RMS_GOOD_M = 0.2
RMS_FAIR_M = 1.0


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
        self._geometry_before_map_drawing = None
        self.image_path = None
        self.current_pixel = None
        self.gcps = []
        self.transform_result = None
        # Projection results are keyed by source layer ID so that projecting
        # the same layer twice replaces its previous result instead of
        # silently duplicating every record in the export.
        self.gps_results = {}
        self.plateau_results = {}
        self.reference_records = []
        self.reference_segments = []
        self.section_line_tool = None
        self.building_pick_tool = None
        self.building_pick_layer = None
        self.previous_map_tool = None
        # The PLATEAU building(s) the section is about: layer id + feature ids.
        self.building_layer_id = None
        self.building_fids = []
        # Map overlay that marks the picked building(s).
        self.building_highlight = None
        self._guide_visible = True
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

        main_splitter = QSplitter(Qt.Horizontal)
        main_splitter.setChildrenCollapsible(False)

        # The step pages wire buttons to the canvases, so build those first.
        self.canvas = SectionCanvas()
        self.canvas.setMinimumSize(200, 250)
        self.canvas.clickedPixel.connect(self._on_canvas_clicked)
        self.canvas.hoveredPixel.connect(self._on_image_hover)
        self.image_readout = _readout_label()
        self.reference_canvas = SectionReferenceCanvas()
        self.reference_canvas.setMinimumSize(200, 250)
        self.reference_canvas.clickedSection.connect(self._on_reference_clicked)
        self.reference_canvas.hoveredSection.connect(self._on_reference_hover)
        self.reference_readout = _readout_label()

        # Steps live in a sidebar so the canvases get the full window height;
        # only the step being worked on is expanded.
        self.steps = QToolBox()
        self.steps.setMinimumWidth(300)
        for page, title in zip(
            (
                self._building_group(),
                self._section_group(),
                self._image_group(),
                self._gcp_group(),
                self._projection_group(),
                self._export_config_group(),
            ),
            _STEP_TITLES,
        ):
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setFrameShape(QFrame.NoFrame)
            page.setContentsMargins(4, 4, 4, 4)
            # Keep the controls packed at the top of a tall page.
            page.layout().addStretch(1)
            scroll.setWidget(page)
            self.steps.addItem(scroll, title)
        main_splitter.addWidget(self.steps)

        canvas_splitter = QSplitter(Qt.Horizontal)
        canvas_splitter.setChildrenCollapsible(False)
        for title, view, readout in (
            ("Section image", self.canvas, self.image_readout),
            ("PLATEAU reference", self.reference_canvas, self.reference_readout),
        ):
            panel = QWidget()
            panel_layout = QVBoxLayout(panel)
            panel_layout.setContentsMargins(0, 0, 0, 0)
            panel_layout.setSpacing(0)
            label = QLabel(title)
            label.setAlignment(Qt.AlignCenter)
            label.setStyleSheet(
                "font-size: 11px; font-weight: bold; color: palette(dark);"
                "padding: 2px 0; background-color: palette(window);"
                "border-bottom: 1px solid palette(mid);"
            )
            panel_layout.addWidget(label)
            panel_layout.addWidget(view, stretch=1)
            panel_layout.addWidget(readout)
            canvas_splitter.addWidget(panel)
        canvas_splitter.setSizes([500, 500])
        self._canvas_area = canvas_splitter
        main_splitter.addWidget(canvas_splitter)
        main_splitter.setStretchFactor(0, 0)
        main_splitter.setStretchFactor(1, 1)
        main_splitter.setSizes([340, 760])
        root_layout.addWidget(main_splitter, stretch=1)
        self._on_image_hover(math.nan, math.nan)
        self._on_reference_hover(math.nan, math.nan, "")

        self.setWidget(root)

    def _building_group(self):
        self._reference_group_box = QWidget()
        layout = QVBoxLayout(self._reference_group_box)
        layout.setSpacing(6)

        layer_label = QLabel("PLATEAU / CAD building layer")
        layer_label.setStyleSheet("font-size: 10px; color: palette(dark);")
        layout.addWidget(layer_label)
        self.building_layer_combo = QgsMapLayerComboBox()
        self.building_layer_combo.setFilters(_polygon_layer_filter())
        self.building_layer_combo.setAllowEmptyLayer(False)
        active = self.iface.activeLayer()
        if active is not None and self.building_layer_combo.findText(active.name()) >= 0:
            self.building_layer_combo.setLayer(active)
        layout.addWidget(self.building_layer_combo)

        button_row = QHBoxLayout()
        button_row.setSpacing(4)
        pick_button = _primary_button("Pick on map")
        pick_button.setToolTip(
            "Click the building on the QGIS map.\n"
            "Shift-click adds or removes more buildings; Esc or right-click finishes."
        )
        pick_button.clicked.connect(self._start_building_pick)
        selection_button = QPushButton("Use QGIS selection")
        selection_button.setToolTip(
            "Use the features already selected in the active QGIS layer"
        )
        selection_button.clicked.connect(self._use_qgis_selection_as_building)
        clear_button = QPushButton("Clear")
        clear_button.clicked.connect(self._clear_plateau_reference)
        button_row.addWidget(pick_button)
        button_row.addWidget(selection_button)
        button_row.addWidget(clear_button)
        layout.addLayout(button_row)

        self.building_info_label = QLabel(_NO_BUILDING_TEXT)
        self.building_info_label.setWordWrap(True)
        self.building_info_label.setStyleSheet("font-size: 11px; color: palette(dark);")
        layout.addWidget(self.building_info_label)

        self.reference_info_label = QLabel(_NO_REFERENCE_TEXT)
        self.reference_info_label.setWordWrap(True)
        self.reference_info_label.setStyleSheet("color: palette(dark); font-size: 11px;")
        layout.addWidget(self.reference_info_label)

        option_row = QHBoxLayout()
        option_row.setSpacing(10)
        self.reference_snap_check = QCheckBox("Snap")
        self.reference_snap_check.setChecked(True)
        self.reference_snap_check.setToolTip(
            "Snap PLATEAU reference clicks to corners first, then to edges."
        )
        self.reference_snap_check.stateChanged.connect(self._set_reference_snap_enabled)
        self.reference_overlay_check = QCheckBox("Overlay on CAD")
        self.reference_overlay_check.setChecked(True)
        self.reference_overlay_check.setToolTip(
            "After 3 or more GCPs compute a transform, overlay the PLATEAU reference on the CAD image."
        )
        self.reference_overlay_check.stateChanged.connect(self._set_reference_overlay_enabled)
        fit_button = QPushButton("Fit")
        fit_button.setToolTip("Fit the PLATEAU reference panel to the building")
        fit_button.clicked.connect(self.reference_canvas.fit_to_reference)
        option_row.addWidget(self.reference_snap_check)
        option_row.addWidget(self.reference_overlay_check)
        option_row.addStretch()
        option_row.addWidget(fit_button)
        layout.addLayout(option_row)
        return self._reference_group_box

    def _section_group(self):
        self._section_group_box = QWidget()
        layout = QVBoxLayout(self._section_group_box)
        layout.setSpacing(6)

        grid = QGridLayout()
        grid.setHorizontalSpacing(6)
        grid.setVerticalSpacing(4)
        for column, text in ((1, "x"), (2, "y")):
            header = QLabel(text)
            header.setStyleSheet("font-size: 10px; color: palette(dark);")
            grid.addWidget(header, 0, column)
        self.x0_edit = QLineEdit()
        self.y0_edit = QLineEdit()
        self.x1_edit = QLineEdit()
        self.y1_edit = QLineEdit()
        for row, text, edits in (
            (1, "P0 / LEFT", (self.x0_edit, self.y0_edit)),
            (2, "P1 / RIGHT", (self.x1_edit, self.y1_edit)),
        ):
            label = QLabel(text)
            label.setStyleSheet("font-weight: bold; font-size: 11px; color: palette(dark);")
            grid.addWidget(label, row, 0)
            for column, widget in enumerate(edits, start=1):
                widget.setPlaceholderText("0.0")
                widget.setToolTip("Project CRS coordinate")
                grid.addWidget(widget, row, column)
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(2, 1)
        layout.addLayout(grid)

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
        self._image_group_box = QWidget()
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
        self._gcp_group_box = QWidget()
        layout = QVBoxLayout(self._gcp_group_box)
        layout.setSpacing(6)

        self.clicked_pixel_label = QLabel(_NO_PIXEL_TEXT)
        self.clicked_pixel_label.setWordWrap(True)
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
        add_button.setToolTip(
            "Register the picked image pixel (orange mark, left) with the "
            "target s/z (orange mark, right) as a Ground Control Point"
        )
        add_button.clicked.connect(self._add_gcp)
        self.add_gcp_button = add_button
        self.gcp_s_edit.textChanged.connect(self._update_add_gcp_state)
        self.gcp_z_edit.textChanged.connect(self._update_add_gcp_state)
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
            ["u (px)", "v (px)", "s (m)", "z (m)", "res. (m)"]
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
        self.rms_label.setWordWrap(True)
        self.rms_label.setToolTip(
            "Green: RMS up to {:.1f} m. Amber: up to {:.1f} m. Red: above. "
            "The residual column uses the same colours per GCP.".format(
                RMS_GOOD_M, RMS_FAIR_M
            )
        )
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
        self._projection_group_box = QWidget()
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
        group = QWidget()
        layout = QVBoxLayout(group)
        layout.setSpacing(4)

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
        self._finish_map_tools()
        self.previous_map_tool = canvas.mapTool()
        self.section_line_tool = SectionLineMapTool(
            canvas,
            self._finish_drawn_section_line,
            self._cancel_drawn_section_line,
        )
        canvas.setMapTool(self.section_line_tool)
        try:
            self.section_line_tool.deactivated.connect(self._restore_after_map_drawing)
        except Exception:
            pass
        self._compact_for_map_drawing()
        self._info(
            "Click the endpoint that should appear on the LEFT of the section drawing "
            "(P0), then click the RIGHT endpoint (P1). Snapping follows QGIS settings. "
            "Esc or right-click cancels."
        )

    def _compact_for_map_drawing(self):
        """Shrink the always-on-top window to its step sidebar at the screen's
        right edge, so it does not cover the map while P0/P1 are clicked."""
        if not self.isWindow() or self._geometry_before_map_drawing is not None:
            return
        self._geometry_before_map_drawing = self.saveGeometry()
        width = COMPACT_WINDOW_WIDTH
        self._canvas_area.hide()
        # Recompute the minimum size now, or resize() is clamped to the
        # minimum that still includes the hidden canvases.
        self._canvas_area.parentWidget().updateGeometry()
        for layout in (self.widget().layout(), self.layout()):
            if layout is not None:
                # activate() alone is a no-op until the hide's deferred
                # layout request arrives.
                layout.invalidate()
                layout.activate()
        self.resize(width, self.height())
        screen = self.screen() if hasattr(self, "screen") else None
        if screen is not None:
            available = screen.availableGeometry()
            self.move(available.right() - self.frameGeometry().width() + 1, self.y())

    def _restore_after_map_drawing(self):
        geometry = self._geometry_before_map_drawing
        self._geometry_before_map_drawing = None
        if geometry is not None:
            self._canvas_area.show()
            self.restoreGeometry(geometry)

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
        self._restore_after_map_drawing()
        if error is not None:
            self._warn(error)
        elif applied and not self.building_fids:
            # With a building picked, the reference load has just reported
            # its own result; keep that message.
            self._info(
                "Section line set. P0 is the LEFT end and P1 is the RIGHT end of "
                "the section-coordinate view."
            )

    def _cancel_drawn_section_line(self):
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
        self._restore_after_map_drawing()
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

    # -- building (step 1) ------------------------------------------------

    def _start_building_pick(self):
        layer = self.building_layer_combo.currentLayer()
        if layer is None:
            self._warn(
                "Add the PLATEAU building layer to QGIS first; it then appears "
                "in the layer list of step 1."
            )
            return
        try:
            canvas = self.iface.mapCanvas()
        except Exception:
            self._warn("QGIS map canvas is not available.")
            return
        self._finish_map_tools()
        self.previous_map_tool = canvas.mapTool()
        self.building_pick_layer = layer
        self.building_pick_tool = BuildingPickMapTool(
            canvas, layer, self._on_building_picked, self._finish_building_pick
        )
        canvas.setMapTool(self.building_pick_tool)
        tool = self.building_pick_tool
        try:
            tool.deactivated.connect(lambda: self._on_pick_tool_deactivated(tool))
        except Exception:
            pass
        self._compact_for_map_drawing()
        self._info(
            "Click the building on the QGIS map ({}). Shift-click adds or removes "
            "more buildings; Esc or right-click finishes.".format(layer.name())
        )

    def _on_building_picked(self, feature, modifiers):
        layer = self.building_pick_layer
        if feature is None:
            self._info(
                "No building of {} under the cursor. Click inside the building.".format(
                    _layer_name(layer)
                )
            )
            return
        additive = bool(
            int(modifiers)
            & int(Qt.ShiftModifier | Qt.ControlModifier | Qt.MetaModifier)
        )
        if additive:
            # Shift/Cmd-click toggles this building and keeps the tool active.
            fids = list(self.building_fids) if self.building_layer_id == layer.id() else []
            picked = _highest_lod_building_fids(layer, [feature.id()])
            if all(fid in fids for fid in picked):
                fids = [fid for fid in fids if fid not in picked]
            else:
                fids.extend(fid for fid in picked if fid not in fids)
            self._set_building(layer, fids)
            return
        self._finish_building_pick()
        self._set_building(layer, [feature.id()])

    def _on_pick_tool_deactivated(self, tool):
        # The user switched to another QGIS map tool while picking.
        if self.building_pick_tool is tool:
            self.building_pick_tool = None
            self.previous_map_tool = None
        self._restore_after_map_drawing()

    def _finish_building_pick(self):
        tool = self.building_pick_tool
        if tool is None:
            return
        self.building_pick_tool = None
        try:
            canvas = self.iface.mapCanvas()
            if self.previous_map_tool is not None:
                canvas.setMapTool(self.previous_map_tool)
            else:
                canvas.unsetMapTool(tool)
        except Exception:
            pass
        self.previous_map_tool = None
        self._restore_after_map_drawing()

    def _finish_map_tools(self):
        """Only one of the plugin's map tools is active at a time."""
        if self.building_pick_tool is not None:
            self._finish_building_pick()
        if self.section_line_tool is not None:
            self._cancel_drawn_section_line()

    def _use_qgis_selection_as_building(self):
        layer = self.iface.activeLayer()
        if layer is None or not hasattr(layer, "selectedFeatureIds"):
            self._warn("Select the PLATEAU building in the active QGIS vector layer first.")
            return
        fids = list(layer.selectedFeatureIds())
        if not fids:
            self._warn("Select the PLATEAU building in the active QGIS vector layer first.")
            return
        self._set_building(layer, fids)

    def _set_building(self, layer, fids):
        """Make these features the building of the section.

        They are also selected in their layer, so they are highlighted on the
        map and 'PLATEAU/CAD' in step 5 projects the same building.
        """
        fids = _highest_lod_building_fids(layer, fids)
        if not fids:
            self._clear_plateau_reference()
            self._info("No building selected.")
            return
        previous = self._building_layer()
        if previous is not None and previous is not layer:
            previous.deselect(self.building_fids)
        self.building_layer_id = layer.id()
        self.building_fids = list(fids)
        layer.selectByIds(self.building_fids)
        if self.building_layer_combo.currentLayer() is not layer:
            self.building_layer_combo.setLayer(layer)
        self.building_info_label.setText(_building_summary(layer, self.building_fids))
        self.building_info_label.setStyleSheet("font-size: 11px; color: palette(text);")
        self._update_building_highlight()
        if self.section_p0 is not None and self.section_p1 is not None:
            self._load_plateau_reference()
        else:
            self._update_workflow_state()
            self._info(
                "Building selected. Next, draw the section line through it in step 2."
            )
        self._advance_step(STEP_BUILDING)

    def _update_building_highlight(self):
        """Fill the picked building's footprint on the map in a colour that
        stands out from any layer style and from the QGIS selection."""
        self._remove_building_highlight()
        layer = self._building_layer()
        if layer is None or not self.building_fids:
            return
        try:
            canvas = self.iface.mapCanvas()
        except Exception:
            return
        geometries = [
            feature.geometry()
            for feature in layer.getFeatures(
                QgsFeatureRequest().setFilterFids(self.building_fids)
            )
        ]
        footprint = building_footprint(geometries)
        if footprint is None:
            # No roof area in plan (e.g. walls only): outline what there is.
            geometries = [g for g in geometries if g is not None and not g.isEmpty()]
            if not geometries:
                return
            footprint = QgsGeometry.collectGeometry(geometries)
        highlight = QgsHighlight(canvas, footprint, layer)
        highlight.setColor(BUILDING_OUTLINE_COLOR)
        highlight.setFillColor(BUILDING_FILL_COLOR)
        highlight.setWidth(3)
        highlight.setVisible(self._guide_visible)
        self.building_highlight = highlight

    def _remove_building_highlight(self):
        highlight = self.building_highlight
        self.building_highlight = None
        if highlight is None:
            return
        try:
            scene = highlight.scene()
            if scene is not None:
                scene.removeItem(highlight)
        except Exception:
            pass

    def _building_layer(self):
        if not self.building_layer_id:
            return None
        return QgsProject.instance().mapLayer(self.building_layer_id)

    def _clear_building(self):
        layer = self._building_layer()
        if layer is not None and self.building_fids:
            try:
                layer.deselect(self.building_fids)
            except Exception:
                pass
        self.building_layer_id = None
        self.building_fids = []
        self._remove_building_highlight()
        self.building_info_label.setText(_NO_BUILDING_TEXT)
        self.building_info_label.setStyleSheet("font-size: 11px; color: palette(dark);")

    def _load_plateau_reference(self):
        """Project the chosen building onto the current section line."""
        if self.section_p0 is None or self.section_p1 is None:
            return
        layer = self._building_layer()
        if layer is None or not self.building_fids:
            self._warn("Pick the PLATEAU building in step 1 first.")
            return
        if not self._section_crs_ok():
            return
        settings = self._settings_for_use()
        if settings is None:
            return
        features = list(
            layer.getFeatures(QgsFeatureRequest().setFilterFids(self.building_fids))
        )
        if not features:
            self._warn("The picked building is no longer in layer {}.".format(layer.name()))
            return

        identity_section_to_pixel = {
            "u": [0.0, 1.0, 0.0],
            "v": [0.0, 0.0, 1.0],
        }
        with _busy():
            result = project_vector_layer_vertices(
                layer,
                QgsProject.instance().crs(),
                self.section_p0,
                self.section_p1,
                identity_section_to_pixel,
                settings["z_offset"],
                settings["buffer_width"],
                settings["z_field"],
                features=features,
            )
        if result.get("error"):
            self._warn(result["error"])
            return
        try:
            self.reference_canvas.load_reference(
                result["draw_records"], result["draw_segments"]
            )
        except ValueError:
            self._reset_reference_state()
            self._update_workflow_state()
            self._warn(
                "The section line does not pass within {} m of the picked "
                "building. Redraw the line through the building (step 2) or "
                "widen the buffer (step 5).".format(_format_float(settings["buffer_width"]))
            )
            return
        self.reference_records = result["draw_records"]
        self.reference_segments = result["draw_segments"]
        self.applied_settings = dict(settings)
        self.reference_canvas.draw_gcp_targets(self.gcps)
        try:
            self.reference_canvas.set_pending_target(
                float(self.gcp_s_edit.text()), float(self.gcp_z_edit.text())
            )
        except ValueError:
            pass
        self.reference_info_label.setText(
            "Reference: {} vertices within the {} m buffer. Click the right-hand "
            "panel to pick GCP targets.".format(
                len(result["draw_records"]), _format_float(settings["buffer_width"])
            )
        )
        self._update_reference_overlay()
        self._update_workflow_state()
        if result["warnings"]:
            self._warn("PLATEAU reference loaded with warning: {}".format(" ".join(result["warnings"][:3])))
        else:
            self._info(
                "PLATEAU reference loaded: {} vertices of the building lie on the "
                "section.".format(len(result["draw_records"]))
            )

    def _clear_plateau_reference(self):
        self._clear_building()
        self._reset_reference_state()
        self._update_workflow_state()
        self._info("Building and PLATEAU reference cleared.")

    def _reset_reference_state(self):
        self.reference_canvas.clear_reference()
        self.reference_records = []
        self.reference_segments = []
        self.canvas.clear_reference_overlay()
        self.reference_info_label.setText(_NO_REFERENCE_TEXT)

    def _on_reference_clicked(self, s, z):
        self.gcp_s_edit.setText(_format_m(s))
        self.gcp_z_edit.setText(_format_m(z))
        self.reference_canvas.set_pending_target(s, z)
        if self.current_pixel is None:
            self._info(
                "Target s={} m, z={} m picked. Now click the matching point on "
                "the section image.".format(_format_m(s), _format_m(z))
            )
        else:
            self._info(
                "Target s={} m, z={} m picked. Click 'Add GCP' to register the "
                "pair.".format(_format_m(s), _format_m(z))
            )

    def _on_image_hover(self, u, v):
        if math.isnan(u) or math.isnan(v):
            self.image_readout.setText("Click the image to pick a GCP pixel. Drag to pan, wheel to zoom.")
            return
        text = "u {:.1f}  v {:.1f} px".format(u, v)
        if self.transform_result is not None:
            try:
                s, z = pixel_to_section(u, v, self.transform_result["pixel_to_section"])
                text += "   →  s {} m  z {} m".format(_format_m(s), _format_m(z))
            except (KeyError, TypeError, ValueError):
                pass
        self.image_readout.setText(text)

    def _on_reference_hover(self, s, z, kind):
        if math.isnan(s) or math.isnan(z):
            self.reference_readout.setText(
                "Click to pick the GCP target. Corners snap first. Drag to pan, wheel to zoom."
            )
            return
        snap = {"vertex": "corner", "segment_endpoint": "corner", "segment": "edge"}.get(kind)
        self.reference_readout.setText(
            "s {} m  z {} m{}".format(
                _format_m(s), _format_m(z), "   (snap: {})".format(snap) if snap else ""
            )
        )

    def _update_add_gcp_state(self, *_args):
        ready = (
            self.current_pixel is not None
            and bool(self.gcp_s_edit.text().strip())
            and bool(self.gcp_z_edit.text().strip())
        )
        self.add_gcp_button.setEnabled(ready)

    def _clear_pending_pick(self):
        self.current_pixel = None
        self.clicked_pixel_label.setText(_NO_PIXEL_TEXT)
        self.gcp_s_edit.clear()
        self.gcp_z_edit.clear()
        self.canvas.clear_pending_pixel()
        self.reference_canvas.clear_pending_target()
        self._update_add_gcp_state()

    def _redraw_gcp_marks(self):
        self.canvas.draw_gcp_markers(self.gcps)
        self.reference_canvas.draw_gcp_targets(self.gcps)

    def _set_reference_snap_enabled(self, _state):
        self.reference_canvas.set_snap_enabled(self.reference_snap_check.isChecked())

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
        needs_reference = changes_line or not (
            self.reference_records or self.reference_segments
        )
        if changes_line and self._has_section_dependents():
            if confirm and not self._confirm(
                "Change section line",
                "These were measured along the current section line and will "
                "be cleared:\n\n{}\n\nUse the new line?".format(
                    _bullets(self._section_dependent_names())
                ),
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
        self._update_workflow_state()
        self._advance_step(STEP_SECTION)
        self._info(
            "Section line set. P0 = section LEFT; P1 = section RIGHT."
        )
        if needs_reference and self.building_fids:
            self._load_plateau_reference()
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
        self.x0_edit.setText(_format_coord(self.section_p0[0]))
        self.y0_edit.setText(_format_coord(self.section_p0[1]))
        self.x1_edit.setText(_format_coord(self.section_p1[0]))
        self.y1_edit.setText(_format_coord(self.section_p1[1]))

    def _section_dependent_names(self):
        names = []
        if self.reference_records or self.reference_segments:
            names.append("PLATEAU reference")
        if self.gcps or self.transform_result is not None:
            names.append("GCPs and transform")
        if self._has_projection():
            names.append("Projected overlay")
        return names

    def _advance_step(self, step):
        """Open the next step after finishing the one the user is on."""
        if self.steps.currentIndex() == step and step + 1 < self.steps.count():
            self.steps.setCurrentIndex(step + 1)

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
        self._reset_projection_state()
        self.gcps = []
        self.transform_result = None
        self._clear_pending_pick()
        self._redraw_gcp_marks()
        self.canvas.clear_reference_overlay()
        self._update_gcp_table()
        self._update_transform_label()

    def set_orientation_guide_visible(self, visible):
        """Show the map guide and the building highlight only while this
        plugin's window is visible."""
        self._guide_visible = bool(visible)
        if self.orientation_guide is not None:
            self.orientation_guide.set_visible(visible)
        if self.building_highlight is not None:
            self.building_highlight.setVisible(self._guide_visible)

    def cleanup(self):
        """Remove map-canvas items owned by this dock before plugin unload."""
        if self.building_pick_tool is not None:
            self._finish_building_pick()
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
        self._remove_building_highlight()

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
            return bool(self.gcps or self.transform_result is not None or self._has_projection())
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
            with _busy():
                self.canvas.load_image(path)
        except ValueError as exc:
            # Nothing has been discarded yet, so the previous session survives
            # an unreadable file.
            self._warn(str(exc))
            return False
        if clear_state:
            self.gcps = []
            self.transform_result = None
            self._reset_projection_state()
            self._update_gcp_table()
            self._update_transform_label()
        self.image_path = path
        self.current_pixel = None
        self.clicked_pixel_label.setText(_NO_PIXEL_TEXT)
        self._update_add_gcp_state()
        # Loading an image rebuilds the scene from scratch, so every overlay
        # has to be redrawn from the dock's state.
        self._redraw_gcp_marks()
        self._redraw_projected_overlays()
        self._update_reference_overlay()
        self._update_image_info()
        self._update_workflow_state()
        self._advance_step(STEP_IMAGE)
        self._info("Loaded image: {}".format(os.path.basename(path)))
        return True

    def _clear_image(self):
        self.canvas.clear_image()
        self.image_path = None
        self.current_pixel = None
        self.clicked_pixel_label.setText(_NO_PIXEL_TEXT)
        self._update_add_gcp_state()
        # The scene is gone with the image; drop the overlays it was carrying
        # so the step summary and the exported package cannot disagree with it.
        self._reset_gcp_state()
        self._update_image_info()
        self._update_workflow_state()
        self._info("Image, GCPs, transform, and projected overlays cleared.")

    def _on_canvas_clicked(self, u, v):
        self.current_pixel = (float(u), float(v))
        self.clicked_pixel_label.setText(
            "Picked pixel: u={:.1f}, v={:.1f} (orange mark)".format(u, v)
        )
        self.canvas.set_pending_pixel(u, v)
        self._update_add_gcp_state()
        if self.gcp_s_edit.text().strip() and self.gcp_z_edit.text().strip():
            self._info("Image point picked. Click 'Add GCP' to register the pair.")
        else:
            self._info(
                "Image point picked. Now click the matching point on the "
                "PLATEAU reference (right), or type s and z."
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
        # A pick is used once: keeping it would let a second click on 'Add
        # GCP', or a click outside the image, silently reuse the old pixel.
        self._clear_pending_pick()
        self._update_gcp_table()
        self._redraw_gcp_marks()
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
        self._reset_projection_state()
        self._update_gcp_table()
        self._redraw_gcp_marks()
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
        # Stored u/v and segment pixels belong to the previous transform.
        had_projection = self._has_projection()
        self._reset_projection_state()
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
        message = "Affine transform computed (RMS {} m from {} GCPs).".format(
            _rms_text(self.transform_result.get("rms")), len(self.gcps)
        )
        if had_projection:
            message += " Previous projections cleared; project the layers again in step 5."
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
        if not self._projection_prerequisites_ok():
            return
        layer = self.iface.activeLayer()
        project_crs = QgsProject.instance().crs()
        try:
            selected_features = layer.selectedFeatures()
        except Exception:
            selected_features = []
        with _busy():
            result = project_point_layer(
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
        if result.get("error"):
            self._warn(result["error"])
            return
        layer_name = _layer_name(layer)
        result["source_layer_name"] = layer_name
        notes = []
        if selected_features:
            notes.append(
                "Projected {} selected feature(s) of {}.".format(
                    len(selected_features), layer_name
                )
            )
        replaced = layer.id() in self.gps_results
        self.gps_results[layer.id()] = result
        self.applied_settings = dict(settings)
        self._redraw_projected_overlays()
        self._update_workflow_state()
        self._show_projection_result(
            result["summary"].get("processed", 0),
            result["summary"].get("inside_buffer", 0),
            _skipped_count(result["summary"]),
            result["warnings"],
            note=" ".join(
                note for note in [_replaced_note(replaced, layer_name)] + notes if note
            ) or None,
        )

    def _project_active_vector_layer(self):
        if not self._projection_prerequisites_ok():
            return
        settings = self._settings_for_use()
        if settings is None:
            return
        if not self._projection_prerequisites_ok():
            return
        layer = self.iface.activeLayer()
        project_crs = QgsProject.instance().crs()
        selected_features = []
        try:
            selected_features = layer.selectedFeatures()
        except Exception:
            selected_features = []
        with _busy():
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
        if result.get("error"):
            self._warn(result["error"])
            return
        layer_name = _layer_name(layer)
        result["source_layer_name"] = layer_name
        notes = []
        if selected_features:
            # Informational, not a warning: projecting the selection is the
            # documented behaviour.
            notes.append(
                "Projected {} selected feature(s) of {}.".format(
                    len(selected_features), layer_name
                )
            )
        replaced = layer.id() in self.plateau_results
        self.plateau_results[layer.id()] = result
        self.applied_settings = dict(settings)
        self._redraw_projected_overlays()
        self._update_workflow_state()
        self._show_projection_result(
            result["summary"].get("processed_vertices", 0),
            result["summary"].get("inside_buffer_vertices", 0),
            _skipped_count(result["summary"]),
            result["warnings"],
            note=" ".join(
                note for note in [_replaced_note(replaced, layer_name)] + notes if note
            ) or None,
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
        # Applying pending settings can invalidate the transform.
        if not self._package_prerequisites_ok():
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
            "crs_authid": None,
            "source_crs_authid": package_config.get("crs", ""),
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
            with _busy():
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
                "gps": sorted(r["source_layer_name"] for r in self.gps_results.values()),
                "plateau": sorted(r["source_layer_name"] for r in self.plateau_results.values()),
            },
            "source_layer_ids": {
                "gps": sorted(self.gps_results),
                "plateau": sorted(self.plateau_results),
            },
            "gps_attribute_maps": {
                layer_id: result.get("attribute_map", {})
                for layer_id, result in self.gps_results.items()
            },
            "warnings": self._projection_warnings(),
        }

    def _apply_config(self, data, config_path):
        try:
            data = config_io.validate_config(data)
            config_crs = data.get("crs")
            if config_crs and not QgsCoordinateReferenceSystem(config_crs).isValid():
                raise ValueError("Config has an invalid CRS: {}".format(config_crs))
            transform = data.get("transform")
            if transform is not None:
                transform["deformation"] = analyze_affine_deformation(
                    transform["pixel_to_section"]
                )
        except (ValueError, TypeError) as exc:
            self._warn("Could not load config; current session kept: {}".format(exc))
            return False
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
        self._clear_building()
        section = data.get("section") or {}
        p0 = section.get("p0")
        p1 = section.get("p1")
        project_authid = QgsProject.instance().crs().authid()
        config_authid = str(data.get("crs") or "").strip() or None
        config_crs_problem = (
            _geographic_crs_problem(QgsCoordinateReferenceSystem(config_authid))
            if config_authid else ""
        )
        if p0 and p1 and config_crs_problem:
            # Earlier versions recommended EPSG:6697; such a line and the GCP
            # targets measured along it are in degrees and cannot be reused.
            load_warnings.append(
                "{} The config section line and GCP targets were not restored. "
                "Redefine the line in a metric projected CRS and pick the GCPs "
                "again.".format(config_crs_problem)
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
        for gcp in [] if config_crs_problem else data.get("gcps") or []:
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

        transform = None if config_crs_problem else data.get("transform")
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
        # A missing, empty, or unreadable new image must never retain the
        # previous session's image under this config's transform.
        self.canvas.clear_image()
        self.image_path = None
        if image_path:
            resolved = config_io.resolve_image_path(image_path, config_path)
            if os.path.exists(resolved):
                if not self._load_image(resolved):
                    load_warnings.append("Config image could not be loaded: {}".format(resolved))
            else:
                self.canvas.clear_image()
                self._redraw_gcp_marks()
                load_warnings.append("Image path in config does not exist: {}".format(resolved))
        else:
            self._redraw_gcp_marks()
        self._update_image_info()
        self._update_reference_overlay()
        self._update_workflow_state()
        if load_warnings:
            self._warn("Config loaded with warning: {}".format(" ".join(load_warnings)))
        else:
            self._info("Config loaded.")
        return True

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
            cleared.append("PLATEAU reference")
        if clears_gcps:
            cleared.append("GCPs and transform")
        if self._has_projection():
            cleared.append("Projected overlay")
        if not cleared:
            self.applied_settings = dict(settings) if self.gcps else None
            return True
        names = {
            "z_offset": "z offset",
            "buffer_width": "Buffer",
            "z_field": "Height field",
        }
        changes = "\n".join(
            "{}: {} → {}".format(
                names[key],
                _setting_text(applied.get(key)),
                _setting_text(settings.get(key)),
            )
            for key in changed
        )
        self._reconciling_settings = True
        try:
            accepted = self._confirm(
                "Change projection settings",
                "{}\n\nThese were made with the previous value and will be "
                "cleared:\n\n{}\n\nApply the new value?".format(
                    changes, _bullets(cleared)
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
        self._info("Projection settings changed. Cleared: {}.".format(", ".join(cleared)))
        if has_reference and self.building_fids:
            # Re-project the building with the new value once the current
            # action (which is still reading the settings) has finished.
            QTimer.singleShot(0, self._load_plateau_reference)
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
            [r["source_layer_name"] for r in
             list(self.gps_results.values()) + list(self.plateau_results.values())
             if r["source_layer_name"]]
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
        section_ok = self.section_p0 is not None and self.section_p1 is not None
        image_ok = self.canvas.has_image()
        transform_ok = self.transform_result is not None
        has_ref = bool(self.reference_records or self.reference_segments)
        n_gcps = len(self.gcps)

        def title(step, done, summary=None):
            text = "{} {}".format(_STEP_DONE if done else _STEP_BULLET, _STEP_TITLES[step])
            if summary:
                text += "  [{}]".format(summary)
            self.steps.setItemText(step, text)

        has_building = bool(self.building_fids)
        if has_building and has_ref:
            building_summary = "{} bldg, {} vtx".format(
                len(self.building_fids), len(self.reference_records)
            )
        elif has_building:
            building_summary = "{} bldg".format(len(self.building_fids))
        else:
            building_summary = None
        title(STEP_BUILDING, has_building, building_summary)
        title(
            STEP_SECTION,
            section_ok and self.section_length is not None,
            "{:.1f} m".format(self.section_length) if section_ok and self.section_length else None,
        )
        self.reference_overlay_check.setEnabled(has_ref and transform_ok)
        title(STEP_IMAGE, image_ok, "loaded" if image_ok else None)
        if transform_ok:
            gcp_summary = "{} GCPs, RMS {} m".format(
                n_gcps, _rms_text(self.transform_result.get("rms"))
            )
        elif n_gcps:
            gcp_summary = "{}/3 GCPs".format(n_gcps)
        else:
            gcp_summary = None
        title(STEP_GCP, transform_ok, gcp_summary)

        n_gps = len(self._gps_records())
        n_plateau = len(self._plateau_records())
        parts = []
        if n_gps:
            parts.append("{} GPS".format(n_gps))
        if n_plateau:
            parts.append("{} vtx".format(n_plateau))
        title(STEP_PROJECTION, bool(parts), ", ".join(parts) or None)
        title(STEP_EXPORT, False)

        self.hint_label.setText(
            "Next: {}".format(
                self._next_step_hint(section_ok, image_ok, transform_ok, has_ref, n_gcps)
            )
        )

    def _next_step_hint(self, section_ok, image_ok, transform_ok, has_ref, n_gcps):
        # Same order as the numbered steps in the sidebar.
        if not self.building_fids:
            return (
                "step 1 — click 'Pick on map', then click the PLATEAU building on "
                "the QGIS map."
            )
        if not section_ok:
            return (
                "step 2 — draw the section line through the building: click "
                "'Draw on map', then P0 (left end of the drawing) and P1 (right end)."
            )
        if not has_ref:
            return (
                "the section line misses the picked building — redraw it through "
                "the building (step 2) or widen the buffer (step 5)."
            )
        if not image_ok:
            return "step 3 — load the scanned section drawing with 'Load image'."
        if not transform_ok:
            return (
                "step 4 — add {} more GCP(s): click a point on the section image "
                "(left), click the matching point on the PLATEAU reference "
                "(right), then 'Add GCP'. The transform is computed at 3; add a "
                "4th to check its accuracy.".format(max(0, 3 - n_gcps))
            )
        if not self._has_projection():
            return (
                "step 5 — activate a GPS or PLATEAU/CAD layer in the QGIS layer "
                "panel and project it."
            )
        return "step 6 — export the overlay PNG or the full analysis package."

    def _update_gcp_table(self):
        residuals = []
        if self.transform_result is not None:
            residuals = self.transform_result.get("residuals") or []
        self.gcp_table.setRowCount(len(self.gcps))
        for row, gcp in enumerate(self.gcps):
            residual = _residual_value(residuals, row)
            cells = [
                "{:.1f}".format(float(gcp["u"])),
                "{:.1f}".format(float(gcp["v"])),
                _format_m(gcp["s"]),
                _format_m(gcp["z"]),
                "-" if residual is None else _format_m(residual),
            ]
            for column, text in enumerate(cells):
                item = QTableWidgetItem(text)
                item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                if column == 4 and residual is not None:
                    colors = _QUALITY_COLORS[_fit_quality(residual)]
                    item.setBackground(QBrush(QColor(colors[0])))
                    item.setForeground(QBrush(QColor(colors[1])))
                self.gcp_table.setItem(row, column, item)

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
            rms = self.transform_result.get("rms")
            if len(self.gcps) == 3:
                # Three points always fit an affine exactly, so RMS says nothing.
                self.rms_label.setText(
                    "RMS: {} m — 3 GCPs fit exactly; add a 4th to check accuracy".format(
                        _rms_text(rms)
                    )
                )
                colors = _QUALITY_COLORS[None]
            else:
                self.rms_label.setText("RMS: {} m".format(_rms_text(rms)))
                colors = _QUALITY_COLORS[_fit_quality(rms)]
            self.rms_label.setStyleSheet(
                "font-size: 11px; font-weight: bold; color: {};"
                "background-color: {}; padding: 2px 6px; border-radius: 3px;".format(
                    colors[1], colors[0]
                )
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
            value = float(text)
            if not math.isfinite(value):
                raise ValueError()
            return value
        except ValueError as exc:
            raise ValueError("{} must be a number.".format(label)) from exc

    def _info(self, message):
        # Routine feedback stays in this window; the QGIS message bar is only
        # used for warnings so that it is not flooded on every click.
        self.status_label.setText(message)
        self.status_label.setStyleSheet(
            "padding: 6px 12px; font-size: 12px;"
            "border-left: 4px solid #1D9E75;"
            "background-color: palette(window);"
            "color: palette(text);"
        )

    def _warn(self, message):
        self.status_label.setText("⚠ " + message)
        self.status_label.setStyleSheet(
            "padding: 6px 12px; font-size: 12px; font-weight: bold;"
            "border-left: 4px solid #BA7517;"
            "background-color: palette(window);"
            "color: palette(text);"
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
    if crs.mapUnits() != QgsUnitTypes.DistanceMeters:
        return "Set a projected CRS in metres; section distance and height must use the same unit."
    return ""


def _same_xy(a, b):
    if a is None or b is None:
        return a is b
    return all(
        math.isclose(float(u), float(v), rel_tol=1e-11, abs_tol=1e-6)
        for u, v in zip(a, b)
    )


_NO_PIXEL_TEXT = "Picked pixel: - (click the section image)"
_NO_BUILDING_TEXT = "No building yet. Click 'Pick on map', then click the building on the QGIS map."
_NO_REFERENCE_TEXT = "The reference section appears once the section line (step 2) is drawn."


BUILDING_FILL_COLOR = QColor(255, 110, 0, 150)
BUILDING_OUTLINE_COLOR = QColor(214, 40, 40)


def building_footprint(geometries):
    """2D footprint of building geometries, or None.

    A PLATEAU LOD2 building is a MultiPolygonZ of roof and wall faces; the
    walls project to zero-area slivers and some faces are invalid in 2D, so
    each face is flattened and repaired before the union.
    """
    pieces = []
    for geometry in geometries:
        if geometry is None or geometry.isEmpty():
            continue
        for part in geometry.constParts():
            flat = part.clone()
            flat.dropZValue()
            flat.dropMValue()
            piece = QgsGeometry(flat).makeValid()
            if not piece.isEmpty() and piece.area() > 1e-6:
                pieces.append(piece)
    if not pieces:
        return None
    footprint = QgsGeometry.unaryUnion(pieces)
    if footprint is None or footprint.isEmpty():
        footprint = QgsGeometry.collectGeometry(pieces)
    return footprint


def _polygon_layer_filter():
    layer_filter = getattr(Qgis, "LayerFilter", None)
    if layer_filter is not None:
        return layer_filter.PolygonLayer
    return QgsMapLayerProxyModel.PolygonLayer


def _highest_lod_building_fids(layer, fids):
    """Resolve mixed-LOD copies of a picked object within the same layer.

    QGIS identifies overlapping features in provider order, not LOD order.
    Require a shared object identifier; never infer identity from overlap or
    upgrade across layers. Source, object type and part IDs keep distinct
    datasets, semantic surfaces and building parts separate.
    """
    fields = {name.lower(): name for name in layer.fields().names()}
    lod_field = fields.get("lod")
    if lod_field is None:
        return list(dict.fromkeys(fids))
    groups = {}
    resolved = []
    for fid in fids:
        feature = layer.getFeature(fid)
        if not feature.isValid():
            continue
        identity = next((fields[name] for name in
                         ("gml_id", "gmlid", "buildingid", "building_id", "bldg_id", "id")
                         if name in fields and str(feature[fields[name]]) not in ("", "NULL", "None")), None)
        if identity is None:
            resolved.append(fid)
            continue
        keys = [identity] + [fields[name] for name in ("source", "type", "partid", "branchid")
                             if name in fields and fields[name] != identity]
        expression = " AND ".join(QgsExpression.createFieldEqualityExpression(key, feature[key]) for key in keys)
        if expression not in groups:
            best_lod, best_fids = -1, []
            for candidate in layer.getFeatures(QgsFeatureRequest().setFilterExpression(expression)):
                try:
                    lod = float(candidate[lod_field])
                except (TypeError, ValueError):
                    continue
                if not math.isfinite(lod) or lod < 0 or candidate.geometry().isEmpty():
                    continue
                if lod > best_lod:
                    best_lod, best_fids = lod, [candidate.id()]
                elif lod == best_lod:
                    best_fids.append(candidate.id())
            groups[expression] = best_fids
        resolved.extend(groups[expression] or [fid])
    return list(dict.fromkeys(resolved))


def _building_summary(layer, fids):
    """One line describing the picked building(s)."""
    if len(fids) != 1:
        return "{} buildings picked in {}.".format(len(fids), layer.name())
    try:
        feature = layer.getFeature(fids[0])
    except Exception:
        return "1 building picked in {}.".format(layer.name())
    names = feature.fields().names()

    def value(*candidates):
        for candidate in candidates:
            if candidate in names:
                item = feature[candidate]
                if item is not None and str(item) not in ("", "NULL"):
                    return item
        return None

    parts = []
    lod = value("lod", "LOD", "LoD")
    if lod is not None:
        parts.append("LOD{}".format(lod))
    identifier = value("name", "buildingID", "gml_id", "id")
    if identifier is not None:
        parts.append(str(identifier))
    height = value("measuredHeight", "bldg_measuredHeight", "height")
    if height is not None:
        try:
            parts.append("{:.1f} m".format(float(height)))
        except (TypeError, ValueError):
            pass
    storeys = value("storeysAboveGround")
    if storeys is not None:
        parts.append("{} floors".format(storeys))
    detail = " · ".join(parts) if parts else "feature {}".format(fids[0])
    return "Picked: {} ({}).".format(detail, layer.name())

# (background, foreground) per fit quality; explicit pairs stay readable in
# light and dark themes.
_QUALITY_COLORS = {
    "good": ("#E1F5EE", "#0F6E56"),
    "fair": ("#FAEEDA", "#854F0B"),
    "poor": ("#FBE3E3", "#A32D2D"),
    None: ("#ECECEC", "#444444"),
}


def _fit_quality(value):
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(value):
        return None
    if value <= RMS_GOOD_M:
        return "good"
    if value <= RMS_FAIR_M:
        return "fair"
    return "poor"


@contextmanager
def _busy():
    """Wait cursor while the UI thread is busy (projection, export, loading)."""
    QApplication.setOverrideCursor(QCursor(Qt.WaitCursor))
    try:
        yield
    finally:
        QApplication.restoreOverrideCursor()


def _readout_label():
    label = QLabel("")
    # Long readouts must not set the window's minimum width.
    label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
    label.setStyleSheet(
        "font-size: 11px; color: palette(dark); padding: 2px 6px;"
        "border-top: 1px solid palette(mid);"
    )
    return label


def _format_m(value):
    """Metres for display and the s/z edits: millimetre precision."""
    try:
        return "{:.3f}".format(float(value))
    except (TypeError, ValueError):
        return str(value)


def _format_coord(value):
    """Map coordinates without float noise (micrometre precision)."""
    try:
        text = "{:.6f}".format(float(value)).rstrip("0").rstrip(".")
    except (TypeError, ValueError):
        return str(value)
    return "0" if text in ("-0", "") else text


def _bullets(items):
    return "\n".join("• {}".format(item) for item in items)


def _setting_text(value):
    if value is None or value == "":
        return "(auto)"
    if isinstance(value, float):
        return _format_float(value)
    return str(value)


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


def _residual_value(residuals, row):
    """Per-GCP fit error in metres, or None without a transform."""
    if row >= len(residuals):
        return None
    residual = residuals[row]
    try:
        return math.hypot(float(residual["ds"]), float(residual["dz"]))
    except (KeyError, TypeError, ValueError):
        return None


def _rms_text(value):
    try:
        return "{:.3f}".format(float(value))
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
            "LEFT  P0  (s=0)", "white", "rgba(25, 113, 194, 235)"
        )
        self.end_label = self._label(
            "RIGHT  P1  (s=L)", "white", "rgba(217, 95, 14, 235)"
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


class BuildingPickMapTool(QgsMapToolIdentifyFeature):
    """Click a building on the map; Shift/Cmd-click adds or removes more."""

    def __init__(self, canvas, layer, picked_callback, finished_callback):
        super().__init__(canvas, layer)
        self.picked_callback = picked_callback
        self.finished_callback = finished_callback
        self._modifiers = Qt.NoModifier
        self._hit = False
        self.featureIdentified[QgsFeature].connect(self._on_identified)

    def canvasReleaseEvent(self, event):
        if event.button() == Qt.RightButton:
            self.finished_callback()
            return
        if event.button() != Qt.LeftButton:
            return
        self._modifiers = event.modifiers()
        self._hit = False
        super().canvasReleaseEvent(event)
        if not self._hit:
            self.picked_callback(None, self._modifiers)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self.finished_callback()

    def _on_identified(self, feature):
        if self._hit:
            return  # overlapping buildings: keep the first one under the cursor
        self._hit = True
        self.picked_callback(feature, self._modifiers)


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
