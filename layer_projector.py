"""Projection utilities for QGIS layers."""

from __future__ import annotations

import math

from qgis.core import (
    QgsCoordinateTransform,
    QgsCoordinateTransformContext,
    QgsPointXY,
    QgsProject,
    QgsVertexId,
    QgsWkbTypes,
)
from qgis.PyQt.QtCore import QDate, QDateTime, Qt, QTime

from .transforms import section_to_pixel, world_to_section


DEFAULT_Z_FIELDS = (
    "z",
    "height",
    "altitude",
    "elevation",
    "measuredHeight",
    "bldg_measuredHeight",
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

POINT_RECORD_FIELDS = {
    "type", "source_fid", "source_layer_id", "x", "y", "z_raw", "z_section",
    "s", "d", "u", "v", "inside_buffer", "z_source", "layer_name",
}


def project_point_layer(
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
    """Project point layer features into section image pixels.

    Returns all points that have a usable z value, including points outside the
    section buffer. Callers should draw only records with inside_buffer=True.
    Every source attribute is carried into the record (GPS observation times
    such as TRIMA ``recordedat`` included) so that exports keep them.
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

    try:
        transformer = _coordinate_transform(layer, project_crs, warnings)
    except ValueError as exc:
        result = _point_result(records, summary, [str(exc)])
        result["error"] = str(exc)
        return result
    layer_name = _layer_name(layer)
    field_names = _field_names(layer)
    attribute_map = _attribute_names(field_names, POINT_RECORD_FIELDS)
    z_lookup = _z_lookup(
        z_field, field_names, layer_name, warnings, overrides_geometry_z=True
    )
    feature_iterable = features if features is not None else layer.getFeatures()

    for feature in feature_iterable:
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
            z, z_source = _z_value(point, feature, z_lookup)
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
                "source_layer_id": layer.id(),
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
            record.update(_all_attributes(feature, attribute_map))
            records.append(record)
    result = _point_result(records, summary, _dedupe(warnings))
    result["attribute_map"] = attribute_map
    return result


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

    try:
        transformer = _coordinate_transform(layer, project_crs, warnings)
    except ValueError as exc:
        result = _vector_result(records, segments, summary, [str(exc)])
        result["error"] = str(exc)
        return result
    layer_name = _layer_name(layer)
    field_names = _field_names(layer)
    # A 3D building keeps its geometry Z even with a height field named: a
    # per-feature value such as measuredHeight would flatten every vertex.
    z_lookup = _z_lookup(
        z_field,
        field_names,
        layer_name,
        warnings,
        overrides_geometry_z=geometry_type == QgsWkbTypes.PointGeometry,
    )
    # Points of a multipoint are not connected, so only lines and polygons
    # produce segments.
    draws_segments = geometry_type != QgsWkbTypes.PointGeometry
    feature_iterable = features if features is not None else layer.getFeatures()

    for feature in feature_iterable:
        summary["processed_features"] += 1
        geometry = feature.geometry()
        if geometry is None or geometry.isEmpty():
            summary["skipped_invalid_geometry"] += 1
            continue
        last_record = None
        last_ring = None
        geometry_type_name = QgsWkbTypes.displayString(geometry.wkbType())
        for vertex_index, ring, point in _vertices_with_ring(geometry):
            # A segment may only join consecutive vertices of the same ring.
            # Bridging the end of one part/face/ring to the start of the next
            # draws edges that do not exist in the source geometry.
            if ring != last_ring:
                last_record = None
                last_ring = ring
            summary["processed_vertices"] += 1
            z, z_source = _z_value(point, feature, z_lookup)
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
                "source_layer_id": layer.id(),
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
            record.update(
                _selected_attributes(feature, field_names, PLATEAU_PRESERVE_FIELDS)
            )
            records.append(record)
            if (
                draws_segments
                and inside_buffer
                and last_record is not None
                and last_record.get("inside_buffer")
            ):
                segments.append(
                    {
                        "source_fid": feature.id(),
                        "source_layer_id": layer.id(),
                        "layer_name": layer_name,
                        "u0": last_record["u"],
                        "v0": last_record["v"],
                        "u1": record["u"],
                        "v1": record["v"],
                        "s0": last_record["s"],
                        "z0": last_record["z_section"],
                        "s1": record["s"],
                        "z1": record["z_section"],
                        "x0": last_record["x"],
                        "y0": last_record["y"],
                        "z_raw0": last_record["z_raw"],
                        "x1": record["x"],
                        "y1": record["y"],
                        "z_raw1": record["z_raw"],
                    }
                )
            last_record = record if inside_buffer else None
    return _vector_result(records, segments, summary, _dedupe(warnings))


def _is_geometry_type(layer, expected):
    return QgsWkbTypes.geometryType(layer.wkbType()) == expected


def _coordinate_transform(layer, project_crs, warnings):
    layer_crs = layer.crs()
    if not layer_crs.isValid():
        raise ValueError("Source layer '{}' has no valid CRS. Set its CRS before projecting.".format(_layer_name(layer)))
    if not project_crs.isValid():
        raise ValueError("Set a valid project CRS before projecting.")
    if layer_crs == project_crs:
        return None
    try:
        project = QgsProject.instance()
        try:
            transform = QgsCoordinateTransform(layer_crs, project_crs, project)
        except TypeError:
            transform = QgsCoordinateTransform(
                layer_crs, project_crs, QgsCoordinateTransformContext()
            )
        if not transform.isValid():
            raise ValueError("Coordinate transform is invalid.")
        return transform
    except Exception as exc:
        raise ValueError("Could not create coordinate transform: {}".format(exc)) from exc


def _transform_xy(x, y, transformer):
    if transformer is None:
        return float(x), float(y)
    point = transformer.transform(QgsPointXY(float(x), float(y)))
    return float(point.x()), float(point.y())


def _vertices_with_ring(geometry):
    """Yield (vertex_index, (part, ring), point) for every vertex.

    The (part, ring) key changes whenever iteration moves to another part of a
    multi-geometry, another face of a polyhedral surface, or another polygon
    ring, which is where consecutive vertices stop being connected.
    """
    abstract = geometry.constGet()
    vertex_id = QgsVertexId()
    index = 0
    while True:
        found, point = abstract.nextVertex(vertex_id)
        if not found:
            return
        yield index, (vertex_id.part, vertex_id.ring), point
        index += 1


def _field_names(layer):
    try:
        return [field.name() for field in layer.fields()]
    except Exception:
        return []


def _resolve_field(name, field_names):
    if name in field_names:
        return name
    lowered = name.lower()
    for field_name in field_names:
        if field_name.lower() == lowered:
            return field_name
    return None


def _z_lookup(user_field, field_names, layer_name, warnings, overrides_geometry_z):
    """Resolve where heights come from, once per layer.

    For point layers an explicitly named height field takes precedence over
    geometry Z: the user named it because the geometry Z is not the height
    they want (GPS points stored as PointZ with z=0 are common). For lines and
    polygons geometry Z wins and the named field fills in 2D geometry. The
    common height field names are the last fallback.
    """
    explicit = None
    requested = str(user_field or "").strip()
    if requested:
        explicit = _resolve_field(requested, field_names)
        if explicit is None:
            warnings.append(
                "Height field '{}' is not in layer {}; used geometry Z or an "
                "auto-detected height field instead.".format(requested, layer_name)
            )
    defaults = []
    for candidate in DEFAULT_Z_FIELDS:
        resolved = _resolve_field(candidate, field_names)
        if resolved and resolved != explicit and resolved not in defaults:
            defaults.append(resolved)
    return {
        "explicit": explicit,
        "explicit_first": bool(overrides_geometry_z),
        "defaults": defaults,
    }


def _z_value(point, feature, z_lookup):
    explicit = z_lookup["explicit"]
    if explicit and z_lookup["explicit_first"]:
        value = _numeric_attribute(feature, explicit)
        if value is not None:
            return value, explicit
    z = _z_from_point(point)
    if z is not None:
        return z, "geometry_z"
    fallbacks = list(z_lookup["defaults"])
    if explicit and not z_lookup["explicit_first"]:
        fallbacks.insert(0, explicit)
    for field_name in fallbacks:
        value = _numeric_attribute(feature, field_name)
        if value is not None:
            return value, field_name
    return None, None


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


def _numeric_attribute(feature, field_name):
    try:
        value = feature[field_name]
    except Exception:
        return None
    if _is_null(value) or value == "":
        return None
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(value):
        return None
    return value


def _selected_attributes(feature, field_names, names):
    values = {}
    for requested in names:
        field_name = _resolve_field(requested, field_names)
        if not field_name:
            continue
        try:
            value = feature[field_name]
        except Exception:
            continue
        values[requested] = _jsonable_value(value)
    return values


def _attribute_names(field_names, computed):
    """Reserve original names first; prefix conflicts until a free name exists."""
    used = set(computed) | set(field_names)
    mapping = {}
    for name in field_names:
        key = name
        if name in computed:
            key = "src_" + name
            while key in used:
                key = "src_" + key
        mapping[name] = key
        used.add(key)
    return mapping


def _all_attributes(feature, attribute_map):
    """Carry every attribute using the recorded collision-free name mapping."""
    values = {}
    for field_name, key in attribute_map.items():
        try:
            value = feature[field_name]
        except Exception:
            continue
        values[key] = _jsonable_value(value)
    return values


def _is_null(value):
    if value is None:
        return True
    # PyQt5 builds of QGIS return NULL attributes as a null QVariant.
    is_null = getattr(value, "isNull", None)
    if is_null is not None and not isinstance(value, (str, bytes)):
        try:
            return bool(is_null())
        except Exception:
            return False
    return False


def _jsonable_value(value):
    """Plain JSON/CSV value for an attribute.

    Dates and times use ISO 8601. A datetime keeps exactly the time-zone
    information it already carries: a naive value (for example a JST GPS
    ``recordedat``) stays naive and is never shifted.
    """
    if _is_null(value):
        return None
    if isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, QDateTime):
        return value.toString(Qt.ISODateWithMs if value.time().msec() else Qt.ISODate)
    if isinstance(value, QDate):
        return value.toString(Qt.ISODate)
    if isinstance(value, QTime):
        return value.toString(Qt.ISODateWithMs if value.msec() else Qt.ISODate)
    try:
        if hasattr(value, "toString"):
            return value.toString()
    except Exception:
        pass
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
