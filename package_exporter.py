"""Export reproducible section analysis packages."""

from __future__ import annotations

import csv
import json
import os
from datetime import datetime, timezone


GPS_COLUMNS = [
    "source_fid",
    "timestamp",
    "x",
    "y",
    "z_raw",
    "z_section",
    "s",
    "d",
    "u",
    "v",
    "inside_buffer",
    "z_source",
    "layer_name",
    "user_id",
    "device_id",
    "accuracy",
    "speed",
    "stay_flag",
]


def export_section_analysis_package(
    output_dir,
    canvas,
    config,
    gps_records,
    plateau_records,
    plateau_segments,
    projection_summary,
    gcp_residuals=None,
):
    """Write the complete section analysis package to an output directory."""
    os.makedirs(output_dir, exist_ok=True)

    background_path = os.path.join(output_dir, "section_background.png")
    preview_path = os.path.join(output_dir, "section_preview.png")
    config_path = os.path.join(output_dir, "section_config.json")
    gps_path = os.path.join(output_dir, "gps_projected.csv")
    plateau_path = os.path.join(output_dir, "plateau_projected.geojson")
    world_path = os.path.join(output_dir, "world_projected.geojson")
    gpkg_path = os.path.join(output_dir, "section_projected.gpkg")
    summary_path = os.path.join(output_dir, "projection_summary.json")

    canvas.export_background_png(background_path)
    canvas.export_png(preview_path)

    crs_auth_id = config.get("crs", "")

    write_json(config_path, config)
    write_gps_csv(gps_path, gps_records)
    write_plateau_geojson(plateau_path, plateau_records, plateau_segments)
    write_world_geojson(world_path, gps_records, plateau_records, crs_auth_id)
    write_geopackage(
        gpkg_path, gps_records, plateau_records, plateau_segments, crs_auth_id
    )
    write_json(summary_path, projection_summary)

    if gcp_residuals:
        write_gcp_residuals_csv(
            os.path.join(output_dir, "gcp_residuals.csv"), gcp_residuals
        )

    return {
        "section_background.png": background_path,
        "section_preview.png": preview_path,
        "section_config.json": config_path,
        "gps_projected.csv": gps_path,
        "plateau_projected.geojson": plateau_path,
        "world_projected.geojson": world_path,
        "section_projected.gpkg": gpkg_path,
        "projection_summary.json": summary_path,
    }


def utc_timestamp():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def write_json(path, data):
    with open(path, "w", encoding="utf-8") as file_obj:
        json.dump(data, file_obj, indent=2, ensure_ascii=False)
        file_obj.write("\n")


def write_gps_csv(path, records):
    extra_columns = _extra_columns(records, GPS_COLUMNS)
    columns = GPS_COLUMNS + extra_columns
    with open(path, "w", newline="", encoding="utf-8") as file_obj:
        writer = csv.DictWriter(file_obj, fieldnames=columns)
        writer.writeheader()
        for record in records:
            writer.writerow({column: _csv_value(record.get(column)) for column in columns})


def write_gcp_residuals_csv(path, residuals):
    columns = ["u", "v", "s_obs", "z_obs", "s_pred", "z_pred", "ds", "dz"]
    with open(path, "w", newline="", encoding="utf-8") as file_obj:
        writer = csv.DictWriter(file_obj, fieldnames=columns)
        writer.writeheader()
        for residual in residuals:
            writer.writerow(
                {column: _csv_value(residual.get(column)) for column in columns}
            )


def write_plateau_geojson(path, records, segments=None):
    features = []
    for record in records:
        properties = {
            key: _json_value(value)
            for key, value in record.items()
            if key not in ("u", "v", "s", "z_section")
        }
        properties.update(
            {
                "s": _json_value(record.get("s")),
                "d": _json_value(record.get("d")),
                "z_section": _json_value(record.get("z_section")),
                "u": _json_value(record.get("u")),
                "v": _json_value(record.get("v")),
                "export_geometry_convention": "Point geometry coordinates are [s, z_section]. Pixel coordinates are stored as u/v properties.",
            }
        )
        features.append(
            {
                "type": "Feature",
                "geometry": {
                    "type": "Point",
                    "coordinates": [
                        _json_value(record.get("s")),
                        _json_value(record.get("z_section")),
                    ],
                },
                "properties": properties,
            }
        )

    for segment in segments or []:
        properties = {
            key: _json_value(value)
            for key, value in segment.items()
            if key not in ("s0", "z0", "s1", "z1")
        }
        properties["export_geometry_convention"] = (
            "LineString geometry coordinates are [s, z_section]."
        )
        features.append(
            {
                "type": "Feature",
                "geometry": {
                    "type": "LineString",
                    "coordinates": [
                        [_json_value(segment.get("s0")), _json_value(segment.get("z0"))],
                        [_json_value(segment.get("s1")), _json_value(segment.get("z1"))],
                    ],
                },
                "properties": properties,
            }
        )

    write_json(
        path,
        {
            "type": "FeatureCollection",
            "name": "plateau_projected",
            "features": features,
        },
    )


def write_world_geojson(path, gps_records, plateau_records, crs_auth_id):
    """Write a GeoJSON with world CRS coordinates for GIS interoperability.

    Point geometry uses [x, y, z_raw] in the project CRS so the output can
    be loaded directly in QGIS, geopandas, or other GIS tools.
    """
    features = []
    for record in gps_records:
        feature = _world_point_feature(record, "gps")
        if feature:
            features.append(feature)
    for record in plateau_records:
        feature = _world_point_feature(record, "plateau")
        if feature:
            features.append(feature)

    geojson = {
        "type": "FeatureCollection",
        "name": "section_world_coordinates",
        "crs_auth_id": crs_auth_id or None,
        "features": features,
    }
    write_json(path, geojson)


def _world_point_feature(record, source_type):
    x = record.get("x")
    y = record.get("y")
    if x is None or y is None:
        return None
    z_raw = record.get("z_raw")
    coordinates = [_json_value(x), _json_value(y)]
    if z_raw is not None:
        coordinates.append(_json_value(z_raw))
    properties = {
        "source": source_type,
        "s": _json_value(record.get("s")),
        "d": _json_value(record.get("d")),
        "z_section": _json_value(record.get("z_section")),
        "u": _json_value(record.get("u")),
        "v": _json_value(record.get("v")),
        "inside_buffer": record.get("inside_buffer"),
        "layer_name": record.get("layer_name"),
    }
    return {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": coordinates},
        "properties": properties,
    }


def write_geopackage(
    path, gps_records, plateau_records, plateau_segments, crs_auth_id
):
    """Write a GeoPackage with world coordinates using GDAL/OGR."""
    try:
        from osgeo import ogr, osr
    except ImportError:
        return

    driver = ogr.GetDriverByName("GPKG")
    if driver is None:
        return

    if os.path.exists(path):
        driver.DeleteDataSource(path)

    ds = driver.CreateDataSource(path)
    if ds is None:
        return

    srs = osr.SpatialReference()
    if crs_auth_id:
        srs.SetFromUserInput(str(crs_auth_id))

    _write_gpkg_point_layer(ds, "gps", gps_records, srs)
    _write_gpkg_point_layer(ds, "plateau", plateau_records, srs)
    _write_gpkg_segment_layer(ds, "plateau_segments", plateau_segments, srs)
    ds = None


def _write_gpkg_point_layer(ds, name, records, srs):
    from osgeo import ogr

    layer = ds.CreateLayer(name, srs, ogr.wkbPoint25D)
    for field, ftype in [
        ("s", ogr.OFTReal),
        ("d", ogr.OFTReal),
        ("z_section", ogr.OFTReal),
        ("inside_buffer", ogr.OFTInteger),
        ("layer_name", ogr.OFTString),
    ]:
        layer.CreateField(ogr.FieldDefn(field, ftype))
    for record in records:
        x, y = record.get("x"), record.get("y")
        if x is None or y is None:
            continue
        feat = ogr.Feature(layer.GetLayerDefn())
        geom = ogr.Geometry(ogr.wkbPoint25D)
        geom.AddPoint(float(x), float(y), float(record.get("z_raw") or 0))
        feat.SetGeometry(geom)
        feat.SetField("s", float(record.get("s", 0)))
        feat.SetField("d", float(record.get("d", 0)))
        feat.SetField("z_section", float(record.get("z_section", 0)))
        feat.SetField("inside_buffer", 1 if record.get("inside_buffer") else 0)
        feat.SetField("layer_name", str(record.get("layer_name") or ""))
        layer.CreateFeature(feat)


def _write_gpkg_segment_layer(ds, name, segments, srs):
    from osgeo import ogr

    if not segments:
        return
    layer = ds.CreateLayer(name, srs, ogr.wkbLineString25D)
    for field, ftype in [
        ("s0", ogr.OFTReal),
        ("z0", ogr.OFTReal),
        ("s1", ogr.OFTReal),
        ("z1", ogr.OFTReal),
        ("layer_name", ogr.OFTString),
    ]:
        layer.CreateField(ogr.FieldDefn(field, ftype))
    for seg in segments:
        x0, y0 = seg.get("x0"), seg.get("y0")
        x1, y1 = seg.get("x1"), seg.get("y1")
        if None in (x0, y0, x1, y1):
            continue
        feat = ogr.Feature(layer.GetLayerDefn())
        line = ogr.Geometry(ogr.wkbLineString25D)
        line.AddPoint(float(x0), float(y0), float(seg.get("z_raw0") or 0))
        line.AddPoint(float(x1), float(y1), float(seg.get("z_raw1") or 0))
        feat.SetGeometry(line)
        feat.SetField("s0", float(seg.get("s0", 0)))
        feat.SetField("z0", float(seg.get("z0", 0)))
        feat.SetField("s1", float(seg.get("s1", 0)))
        feat.SetField("z1", float(seg.get("z1", 0)))
        feat.SetField("layer_name", str(seg.get("layer_name") or ""))
        layer.CreateFeature(feat)


def _extra_columns(records, required):
    required_set = set(required)
    extras = []
    for record in records:
        for key in record.keys():
            if key in required_set or key in extras or key == "type":
                continue
            extras.append(key)
    return extras


def _csv_value(value):
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return value


def _json_value(value):
    if value is None:
        return None
    if isinstance(value, (str, int, float, bool)):
        return value
    return str(value)
