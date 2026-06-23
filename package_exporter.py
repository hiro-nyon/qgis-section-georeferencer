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
    summary_path = os.path.join(output_dir, "projection_summary.json")

    canvas.export_background_png(background_path)
    canvas.export_png(preview_path)

    write_json(config_path, config)
    write_gps_csv(gps_path, gps_records)
    write_plateau_geojson(plateau_path, plateau_records, plateau_segments)
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
