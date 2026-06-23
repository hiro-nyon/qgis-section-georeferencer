"""Projection utilities for QGIS layers."""

from __future__ import annotations

import math

from qgis.core import (
    QgsCoordinateTransform,
    QgsCoordinateTransformContext,
    QgsPointXY,
    QgsProject,
    QgsWkbTypes,
)

from .transforms import section_to_pixel, world_to_section


DEFAULT_Z_FIELDS = (
    "z",
    "height",
    "altitude",
    "elevation",
    "measuredHeight",
    "bldg_measuredHeight",
)

GPS_PRESERVE_FIELDS = (
    "timestamp",
    "user_id",
    "device_id",
    "accuracy",
    "speed",
    "stay_flag",
)

PLATEAU_PRESERVE_FIELDS = (
    "building_id",
    "bldg_id",
    "gml_id",
    "height",
    "measuredHeight",
    "bldg_measuredHeight",
    "storey",
    "floor",
    "storeysAboveGround",
    "storeysBelowGround",
)


def project_point_layer(
    layer,
    project_crs,
    p0,
    p1,
    section_to_pixel_coeffs,
    z_offset=0.0,
    buffer_width=10.0,
    z_field=None,
):
    """Project point layer features into section image pixels.

    Returns all points that have a usable z value, including points outside the
    section buffer. Callers should draw only records with inside_buffer=True.
    """
    warnings = []
    records = []
    summary = {
        "processed": 0,
        "inside_buffer": 0,
        "skipped_no_z": 0,
        "skipped_invalid_geometry": 0,
        "skipped_projection_error": 0,
    }
    if layer is None:
        return _point_result(records, summary, ["No active layer."])
    if not _is_geometry_type(layer, QgsWkbTypes.PointGeometry):
        return _point_result(records, summary, ["Active layer is not a point layer."])

    transformer = _coordinate_transform(layer, project_crs, warnings)
    z_candidates = _z_candidates(z_field)
    layer_name = _layer_name(layer)

    for feature in layer.getFeatures():
        geometry = feature.geometry()
        if geometry is None or geometry.isEmpty():
            summary["skipped_invalid_geometry"] += 1
            continue
        points = list(geometry.vertices())
        if not points:
            summary["skipped_invalid_geometry"] += 1
            continue
        for point in points:
            summary["processed"] += 1
            z, z_source = _z_from_point_or_attributes(point, feature, layer, z_candidates)
            if z is None:
                summary["skipped_no_z"] += 1
                continue
            try:
                x, y = _transform_xy(point.x(), point.y(), transformer)
                s, d, z_section = world_to_section(x, y, z, p0, p1, z_offset)
                u, v = section_to_pixel(s, z_section, section_to_pixel_coeffs)
                inside_buffer = abs(d) <= float(buffer_width)
            except Exception as exc:
                summary["skipped_projection_error"] += 1
                warnings.append("Skipped feature {}: {}".format(feature.id(), exc))
                continue
            if inside_buffer:
                summary["inside_buffer"] += 1
            record = {
                "type": "point",
                "source_fid": feature.id(),
                "x": x,
                "y": y,
                "z_raw": z,
                "z_section": z_section,
                "s": s,
                "d": d,
                "u": u,
                "v": v,
                "inside_buffer": inside_buffer,
                "z_source": z_source,
                "layer_name": layer_name,
            }
            record.update(_selected_attributes(feature, layer, GPS_PRESERVE_FIELDS))
            records.append(record)
    return _point_result(records, summary, _dedupe(warnings))


def project_vector_layer_vertices(
    layer,
    project_crs,
    p0,
    p1,
    section_to_pixel_coeffs,
    z_offset=0.0,
    buffer_width=10.0,
    z_field=None,
    features=None,
):
    """Project vertices from line/polygon/multipart vector layers."""
    warnings = []
    records = []
    segments = []
    summary = {
        "processed_features": 0,
        "processed_vertices": 0,
        "projected_vertices": 0,
        "inside_buffer_vertices": 0,
        "skipped_no_z": 0,
        "skipped_invalid_geometry": 0,
        "skipped_projection_error": 0,
    }
    if layer is None:
        return _vector_result(records, segments, summary, ["No active layer."])

    geometry_type = QgsWkbTypes.geometryType(layer.wkbType())
    if geometry_type not in (
        QgsWkbTypes.LineGeometry,
        QgsWkbTypes.PolygonGeometry,
        QgsWkbTypes.PointGeometry,
    ):
        return _vector_result(records, segments, summary, ["Unsupported geometry type."])

    transformer = _coordinate_transform(layer, project_crs, warnings)
    z_candidates = _z_candidates(z_field)
    layer_name = _layer_name(layer)
    feature_iterable = features if features is not None else layer.getFeatures()

    for feature in feature_iterable:
        summary["processed_features"] += 1
        geometry = feature.geometry()
        if geometry is None or geometry.isEmpty():
            summary["skipped_invalid_geometry"] += 1
            continue
        last_record = None
        geometry_type_name = QgsWkbTypes.displayString(geometry.wkbType())
        for vertex_index, point in enumerate(geometry.vertices()):
            summary["processed_vertices"] += 1
            z, z_source = _z_from_point_or_attributes(point, feature, layer, z_candidates)
            if z is None:
                summary["skipped_no_z"] += 1
                last_record = None
                continue
            try:
                x, y = _transform_xy(point.x(), point.y(), transformer)
                s, d, z_section = world_to_section(x, y, z, p0, p1, z_offset)
                u, v = section_to_pixel(s, z_section, section_to_pixel_coeffs)
                inside_buffer = abs(d) <= float(buffer_width)
            except Exception as exc:
                summary["skipped_projection_error"] += 1
                last_record = None
                warnings.append("Skipped feature {}: {}".format(feature.id(), exc))
                continue
            summary["projected_vertices"] += 1
            if inside_buffer:
                summary["inside_buffer_vertices"] += 1
            record = {
                "type": "vertex",
                "source_fid": feature.id(),
                "geometry_type": geometry_type_name,
                "x": x,
                "y": y,
                "z_raw": z,
                "z_section": z_section,
                "s": s,
                "d": d,
                "u": u,
                "v": v,
                "inside_buffer": inside_buffer,
                "z_source": z_source,
                "layer_name": layer_name,
                "vertex_index": vertex_index,
            }
            record.update(_selected_attributes(feature, layer, PLATEAU_PRESERVE_FIELDS))
            records.append(record)
            if inside_buffer and last_record is not None and last_record.get("inside_buffer"):
                segments.append(
                    {
                        "source_fid": feature.id(),
                        "layer_name": layer_name,
                        "u0": last_record["u"],
                        "v0": last_record["v"],
                        "u1": record["u"],
                        "v1": record["v"],
                        "s0": last_record["s"],
                        "z0": last_record["z_section"],
                        "s1": record["s"],
                        "z1": record["z_section"],
                    }
                )
            last_record = record if inside_buffer else None
    return _vector_result(records, segments, summary, _dedupe(warnings))


def _is_geometry_type(layer, expected):
    return QgsWkbTypes.geometryType(layer.wkbType()) == expected


def _coordinate_transform(layer, project_crs, warnings):
    layer_crs = layer.crs()
    if not layer_crs.isValid() or not project_crs.isValid() or layer_crs == project_crs:
        return None
    try:
        project = QgsProject.instance()
        return QgsCoordinateTransform(layer_crs, project_crs, project)
    except TypeError:
        return QgsCoordinateTransform(
            layer_crs, project_crs, QgsCoordinateTransformContext()
        )
    except Exception as exc:
        warnings.append("Could not create coordinate transform: {}".format(exc))
        return None


def _transform_xy(x, y, transformer):
    if transformer is None:
        return float(x), float(y)
    point = transformer.transform(QgsPointXY(float(x), float(y)))
    return float(point.x()), float(point.y())


def _z_candidates(user_field):
    candidates = []
    if user_field:
        candidates.append(str(user_field).strip())
    candidates.extend(DEFAULT_Z_FIELDS)
    return [field for field in candidates if field]


def _z_from_point_or_attributes(point, feature, layer, candidates):
    z = _z_from_point(point)
    if z is not None:
        return z, "geometry_z"
    return _z_from_attributes(feature, layer, candidates)


def _z_from_point(point):
    try:
        if hasattr(point, "is3D") and not point.is3D():
            return None
    except Exception:
        pass
    try:
        z = point.z()
    except Exception:
        return None
    try:
        value = float(z)
    except (TypeError, ValueError):
        return None
    if math.isnan(value):
        return None
    return value


def _z_from_attributes(feature, layer, candidates):
    try:
        field_names = [field.name() for field in layer.fields()]
    except Exception:
        return None
    lower_to_name = {name.lower(): name for name in field_names}
    for candidate in candidates:
        field_name = candidate if candidate in field_names else lower_to_name.get(candidate.lower())
        if not field_name:
            continue
        try:
            value = feature[field_name]
        except Exception:
            continue
        if value in (None, ""):
            continue
        try:
            value = float(value)
        except (TypeError, ValueError):
            continue
        if math.isnan(value):
            continue
        return value, field_name
    return None, None


def _selected_attributes(feature, layer, names):
    try:
        field_names = [field.name() for field in layer.fields()]
    except Exception:
        return {}
    lower_to_name = {name.lower(): name for name in field_names}
    values = {}
    for requested in names:
        field_name = requested if requested in field_names else lower_to_name.get(requested.lower())
        if not field_name:
            continue
        try:
            value = feature[field_name]
        except Exception:
            continue
        values[requested] = _jsonable_value(value)
    return values


def _jsonable_value(value):
    if value is None:
        return None
    try:
        if hasattr(value, "toString"):
            return value.toString()
    except Exception:
        pass
    if isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _layer_name(layer):
    try:
        return layer.name()
    except Exception:
        return ""


def _point_result(records, summary, warnings):
    return {
        "records": records,
        "draw_records": [record for record in records if record.get("inside_buffer")],
        "summary": summary,
        "warnings": warnings,
    }


def _vector_result(records, segments, summary, warnings):
    return {
        "records": records,
        "draw_records": [record for record in records if record.get("inside_buffer")],
        "segments": segments,
        "draw_segments": segments,
        "summary": summary,
        "warnings": warnings,
    }


def _dedupe(values):
    seen = set()
    result = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result
