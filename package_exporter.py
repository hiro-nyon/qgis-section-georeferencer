"""Export reproducible section analysis packages."""

from __future__ import annotations

import copy
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

DEFAULT_PACKAGE_FILES = {
    "section_background": "section_background.png",
    "section_preview": "section_preview.png",
    "section_config": "section_config.json",
    "section_background_georeferenced": "section_background_georeferenced.tif",
    "gps_projected": "gps_projected.csv",
    "plateau_projected": "plateau_projected.geojson",
    "world_projected": "world_projected.geojson",
    "section_projected_gpkg": "section_projected.gpkg",
    "projection_summary": "projection_summary.json",
    "gcp_residuals": "gcp_residuals.csv",
}


def export_section_analysis_package(
    output_dir,
    canvas,
    config,
    gps_records,
    plateau_records,
    plateau_segments,
    projection_summary,
    gcp_residuals=None,
    file_names=None,
):
    """Write the complete section analysis package to an output directory."""
    os.makedirs(output_dir, exist_ok=True)

    package_files = package_file_names(file_names=file_names)

    background_path = os.path.join(output_dir, package_files["section_background"])
    preview_path = os.path.join(output_dir, package_files["section_preview"])
    config_path = os.path.join(output_dir, package_files["section_config"])
    gps_path = os.path.join(output_dir, package_files["gps_projected"])
    plateau_path = os.path.join(output_dir, package_files["plateau_projected"])
    geotiff_path = os.path.join(
        output_dir, package_files["section_background_georeferenced"]
    )
    world_path = os.path.join(output_dir, package_files["world_projected"])
    gpkg_path = os.path.join(output_dir, package_files["section_projected_gpkg"])
    summary_path = os.path.join(output_dir, package_files["projection_summary"])

    canvas.export_background_png(background_path)
    canvas.export_png(preview_path)
    _export_section_geotiff_if_possible(canvas, geotiff_path, config)

    crs_auth_id = config.get("crs", "")

    write_json(config_path, localize_config_image_path(config, package_files))
    write_gps_csv(gps_path, gps_records)
    write_plateau_geojson(plateau_path, plateau_records, plateau_segments)
    write_world_geojson(world_path, gps_records, plateau_records, crs_auth_id)
    write_geopackage(
        gpkg_path, gps_records, plateau_records, plateau_segments, crs_auth_id
    )
    write_json(summary_path, projection_summary)

    if gcp_residuals:
        write_gcp_residuals_csv(
            os.path.join(output_dir, package_files["gcp_residuals"]), gcp_residuals
        )

    return {
        filename: os.path.join(output_dir, filename)
        for filename in package_files.values()
        if filename != package_files["gcp_residuals"] or gcp_residuals
    }


def package_file_names(package_name=None, file_names=None):
    names = dict(DEFAULT_PACKAGE_FILES)
    if package_name:
        prefix = sanitize_package_name(package_name)
        names = {
            key: _prefix_file_name(prefix, value)
            for key, value in names.items()
        }
    if file_names:
        for key, value in file_names.items():
            if key in names and value:
                names[key] = _clean_file_name(value)
    return names


def localize_config_image_path(config, package_files):
    """Return a copy of the config whose image path is package-relative.

    The in-memory config stores the absolute path of the section image on the
    machine that ran the export. Writing that into the package would leak a
    local path and break for anyone who receives the folder, so the exported
    config points at the packaged background image instead and keeps only the
    original file name for provenance.
    """
    localized = copy.deepcopy(config)
    image = localized.get("image")
    if not isinstance(image, dict):
        return localized
    source_path = image.get("path")
    if source_path:
        image["source_name"] = os.path.basename(str(source_path))
    image["path"] = package_files.get(
        "section_background", DEFAULT_PACKAGE_FILES["section_background"]
    )
    return localized


def sanitize_package_name(name):
    cleaned = str(name or "").strip()
    for char in '/\\:*?"<>|':
        cleaned = cleaned.replace(char, "_")
    cleaned = cleaned.strip(" .")
    return cleaned or "section_package"


def _clean_file_name(name):
    return sanitize_package_name(os.path.basename(str(name)))


def _prefix_file_name(prefix, file_name):
    stem, extension = os.path.splitext(file_name)
    return "{}_{}{}".format(prefix, stem, extension)


def utc_timestamp():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _export_section_geotiff_if_possible(canvas, path, config):
    transform = config.get("transform") or {}
    pixel_to_section = transform.get("pixel_to_section")
    if not pixel_to_section:
        return
    geotiff = config.get("geotiff") or {}
    crs_auth_id = geotiff.get("crs_authid") or None
    canvas.export_section_geotiff(path, pixel_to_section, crs_auth_id)


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
