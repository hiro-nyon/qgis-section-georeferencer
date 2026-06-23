"""Dock widget UI for the Section Georeferencer plugin."""

from __future__ import annotations

import os

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtWidgets import (
    QAbstractItemView,
    QDockWidget,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
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

from . import config_io
from .layer_projector import project_point_layer, project_vector_layer_vertices
from .package_exporter import export_section_analysis_package, utc_timestamp
from .section_canvas import SectionCanvas
from .transforms import compute_section_basis, fit_affine_pixel_to_section


PLUGIN_NAME = "Section Georeferencer"
PLUGIN_VERSION = "0.1.0"


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
        self._build_ui()
        self._update_crs_labels()
        self._update_section_labels()
        self._update_gcp_table()

    def _build_ui(self):
        root = QWidget(self)
        root_layout = QVBoxLayout(root)

        self.status_label = QLabel("Ready")
        self.status_label.setWordWrap(True)
        root_layout.addWidget(self.status_label)

        self.canvas = SectionCanvas()
        self.canvas.setMinimumHeight(340)
        self.canvas.clickedPixel.connect(self._on_canvas_clicked)
        root_layout.addWidget(self.canvas, stretch=1)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        controls = QWidget()
        controls_layout = QVBoxLayout(controls)
        controls_layout.addWidget(self._section_group())
        controls_layout.addWidget(self._image_group())
        controls_layout.addWidget(self._gcp_group())
        controls_layout.addWidget(self._projection_group())
        controls_layout.addWidget(self._export_config_group())
        controls_layout.addStretch(1)
        scroll.setWidget(controls)
        root_layout.addWidget(scroll, stretch=0)

        self.setWidget(root)

    def _section_group(self):
        group = QGroupBox("1. Section Line")
        layout = QVBoxLayout(group)
        form = QFormLayout()
        self.x0_edit = QLineEdit()
        self.y0_edit = QLineEdit()
        self.x1_edit = QLineEdit()
        self.y1_edit = QLineEdit()
        for widget in (self.x0_edit, self.y0_edit, self.x1_edit, self.y1_edit):
            widget.setPlaceholderText("0.0")
        form.addRow("x0", self.x0_edit)
        form.addRow("y0", self.y0_edit)
        form.addRow("x1", self.x1_edit)
        form.addRow("y1", self.y1_edit)
        layout.addLayout(form)

        button_row = QHBoxLayout()
        manual_button = QPushButton("Use Manual Coordinates")
        manual_button.clicked.connect(self._use_manual_section)
        selected_button = QPushButton("Use Selected Line Feature")
        selected_button.clicked.connect(self._use_selected_line)
        button_row.addWidget(manual_button)
        button_row.addWidget(selected_button)
        layout.addLayout(button_row)

        self.section_length_label = QLabel("Section length: -")
        self.project_crs_label = QLabel("Project CRS: -")
        self.crs_warning_label = QLabel("")
        self.crs_warning_label.setWordWrap(True)
        self.crs_warning_label.setStyleSheet("color: #a65f00;")
        layout.addWidget(self.section_length_label)
        layout.addWidget(self.project_crs_label)
        layout.addWidget(self.crs_warning_label)
        return group

    def _image_group(self):
        group = QGroupBox("2. Section Image")
        layout = QHBoxLayout(group)
        load_button = QPushButton("Load Image")
        load_button.clicked.connect(self._load_image_dialog)
        clear_button = QPushButton("Clear Image")
        clear_button.clicked.connect(self._clear_image)
        fit_button = QPushButton("Fit View")
        fit_button.clicked.connect(self.canvas.fit_to_image)
        layout.addWidget(load_button)
        layout.addWidget(clear_button)
        layout.addWidget(fit_button)
        return group

    def _gcp_group(self):
        group = QGroupBox("3. GCP Manager")
        layout = QVBoxLayout(group)
        self.clicked_pixel_label = QLabel("Clicked pixel: -")
        layout.addWidget(self.clicked_pixel_label)

        form = QFormLayout()
        self.gcp_s_edit = QLineEdit()
        self.gcp_z_edit = QLineEdit()
        self.gcp_s_edit.setPlaceholderText("section s")
        self.gcp_z_edit.setPlaceholderText("section z")
        form.addRow("target s", self.gcp_s_edit)
        form.addRow("target z", self.gcp_z_edit)
        layout.addLayout(form)

        button_row = QHBoxLayout()
        add_button = QPushButton("Add GCP")
        add_button.clicked.connect(self._add_gcp)
        delete_button = QPushButton("Delete Selected GCP")
        delete_button.clicked.connect(self._delete_selected_gcp)
        clear_button = QPushButton("Clear GCPs")
        clear_button.clicked.connect(self._clear_gcps)
        button_row.addWidget(add_button)
        button_row.addWidget(delete_button)
        button_row.addWidget(clear_button)
        layout.addLayout(button_row)

        self.gcp_table = QTableWidget(0, 4)
        self.gcp_table.setHorizontalHeaderLabels(["u", "v", "s", "z"])
        self.gcp_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.gcp_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.gcp_table.verticalHeader().setVisible(False)
        self.gcp_table.setMinimumHeight(130)
        layout.addWidget(self.gcp_table)

        compute_button = QPushButton("Compute Transform")
        compute_button.clicked.connect(self._compute_transform)
        self.rms_label = QLabel("RMS residual: -")
        layout.addWidget(compute_button)
        layout.addWidget(self.rms_label)
        return group

    def _projection_group(self):
        group = QGroupBox("4. Projection Settings")
        layout = QVBoxLayout(group)
        form = QFormLayout()
        self.z_offset_edit = QLineEdit("0.0")
        self.buffer_width_edit = QLineEdit("10.0")
        self.z_field_edit = QLineEdit()
        self.z_field_edit.setPlaceholderText("optional height field")
        form.addRow("z_offset", self.z_offset_edit)
        form.addRow("buffer width m", self.buffer_width_edit)
        form.addRow("z source field", self.z_field_edit)
        layout.addLayout(form)

        point_button = QPushButton("Project Active Point Layer as GPS Points")
        point_button.clicked.connect(self._project_active_point_layer)
        vertex_button = QPushButton("Project Selected/Active Vector Layer Vertices as PLATEAU/CAD Geometry")
        vertex_button.clicked.connect(self._project_active_vector_layer)
        clear_button = QPushButton("Clear Projected Overlay")
        clear_button.clicked.connect(self._clear_projected_overlay)
        layout.addWidget(point_button)
        layout.addWidget(vertex_button)
        layout.addWidget(clear_button)
        return group

    def _export_config_group(self):
        group = QGroupBox("5. Export and Config")
        layout = QHBoxLayout(group)
        save_button = QPushButton("Save Config JSON")
        save_button.clicked.connect(self._save_config_dialog)
        load_button = QPushButton("Load Config JSON")
        load_button.clicked.connect(self._load_config_dialog)
        export_button = QPushButton("Export Overlay PNG")
        export_button.clicked.connect(self._export_png_dialog)
        package_button = QPushButton("Export Section Analysis Package")
        package_button.clicked.connect(self._export_section_analysis_package_dialog)
        layout.addWidget(save_button)
        layout.addWidget(load_button)
        layout.addWidget(export_button)
        layout.addWidget(package_button)
        return group

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
        except Exception as exc:
            self._warn("Could not use selected line: {}".format(exc))

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
            "Images (*.png *.jpg *.jpeg *.tif *.tiff)",
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
        self._info("Loaded image: {}".format(os.path.basename(path)))

    def _clear_image(self):
        self.canvas.clear_image()
        self.image_path = None
        self.current_pixel = None
        self.clicked_pixel_label.setText("Clicked pixel: -")
        self._info("Image and overlays cleared.")

    def _on_canvas_clicked(self, u, v):
        self.current_pixel = (float(u), float(v))
        self.clicked_pixel_label.setText(
            "Clicked pixel: u={:.3f}, v={:.3f}".format(u, v)
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
        self.transform_result = None
        self._update_gcp_table()
        self.canvas.draw_gcp_markers(self.gcps)
        self._update_transform_label()
        self._info("GCP added.")

    def _delete_selected_gcp(self):
        row = self.gcp_table.currentRow()
        if row < 0 or row >= len(self.gcps):
            self._warn("Select a GCP row to delete.")
            return
        self.gcps.pop(row)
        self.transform_result = None
        self._update_gcp_table()
        self.canvas.draw_gcp_markers(self.gcps)
        self._update_transform_label()
        self._info("Selected GCP deleted.")

    def _clear_gcps(self):
        self.gcps = []
        self.transform_result = None
        self._update_gcp_table()
        self.canvas.clear_gcp_markers()
        self._update_transform_label()
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
        self._info("Affine transform computed.")

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
            "gps_projected": "gps_projected.csv",
            "plateau_projected": "plateau_projected.geojson",
            "projection_summary": "projection_summary.json",
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
            self.rms_label.setText("RMS residual: -")
        else:
            self.rms_label.setText(
                "RMS residual: {:.6g}".format(self.transform_result.get("rms", 0.0))
            )

    def _update_section_labels(self):
        if self.section_length is None:
            self.section_length_label.setText("Section length: -")
        else:
            self.section_length_label.setText(
                "Section length: {:.3f}".format(self.section_length)
            )
        self._update_crs_labels()

    def _update_crs_labels(self):
        crs = QgsProject.instance().crs()
        if crs.isValid():
            self.project_crs_label.setText("Project CRS: {}".format(crs.authid() or crs.description()))
        else:
            self.project_crs_label.setText("Project CRS: invalid")
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
        self._push_message(message, Qgis.Info)

    def _warn(self, message):
        self.status_label.setText(message)
        self._push_message(message, Qgis.Warning)

    def _push_message(self, message, level):
        try:
            self.iface.messageBar().pushMessage(PLUGIN_NAME, message, level=level, duration=5)
        except Exception:
            if level == Qgis.Critical:
                QMessageBox.critical(self, PLUGIN_NAME, message)


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
