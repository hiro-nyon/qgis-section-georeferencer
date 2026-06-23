#!/usr/bin/env python3
"""Validate a Section Georeferencer analysis package outside QGIS.

Usage:
    python validate_section_package.py /path/to/section_package

Required packages:
    pandas, numpy, matplotlib, pillow

Optional:
    pyproj, for CRS transformation (e.g. WGS84 GPS → project CRS)
    geopandas, for loading world_projected.geojson / section_projected.gpkg
    scikit-learn, for Gaussian mixture cluster centers of z_section
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd
from PIL import Image

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("package_dir", help="Folder exported by Section Georeferencer")
    parser.add_argument(
        "--extra-gps",
        help="CSV with lon,lat,z columns (WGS84) to overlay on the section",
    )
    parser.add_argument(
        "--extra-crs",
        default="EPSG:4326",
        help="CRS of --extra-gps coordinates (default: EPSG:4326)",
    )
    args = parser.parse_args()
    package_dir = Path(args.package_dir)

    config = load_json(package_dir / "section_config.json")
    background = Image.open(package_dir / "section_background.png").convert("RGB")
    gps = read_gps(package_dir / "gps_projected.csv")
    plateau = load_plateau_geojson(package_dir / "plateau_projected.geojson")

    extra_records = []
    if args.extra_gps:
        extra_records = project_external_gps(
            Path(args.extra_gps), config, args.extra_crs
        )

    plot_overlay(
        background, gps, plateau, package_dir / "validation_overlay.png", config,
        extra_records=extra_records,
    )
    plot_z_histogram(gps, package_dir / "validation_z_histogram.png")
    write_floor_counts(package_dir, gps)
    maybe_write_gmm_centers(package_dir, gps)
    report_world_geojson(package_dir)
    report_geopackage(package_dir)


def load_json(path):
    with path.open("r", encoding="utf-8") as file_obj:
        return json.load(file_obj)


def transform_crs(x, y, z, source_crs, target_crs):
    """Transform coordinates between CRS using pyproj.

    Example:
        x, y, z = transform_crs(139.7, 35.6, 30.0, "EPSG:4326", "EPSG:6677")
    """
    try:
        from pyproj import Transformer
    except ImportError:
        raise ImportError(
            "pyproj is required for CRS transformation. "
            "Install with: pip install pyproj"
        )
    transformer = Transformer.from_crs(source_crs, target_crs, always_xy=True)
    x_out, y_out = transformer.transform(float(x), float(y))
    return float(x_out), float(y_out), float(z)


def project_world_xyz_to_pixel(x, y, z, config, source_crs=None):
    """Project one world/project CRS XYZ point to section image pixels.

    This mirrors the plugin's math and is useful when a separate Python
    workflow has GPS/PLATEAU coordinates already transformed to the same CRS as
    section_config.json, for example EPSG:6697.

    If source_crs is given (e.g. "EPSG:4326" for WGS84 GPS coordinates),
    the point is transformed to the section's project CRS before projection.

    Example with WGS84 GPS data:
        result = project_world_xyz_to_pixel(
            139.745, 35.658, 32.5, config, source_crs="EPSG:4326"
        )
    """
    target_crs = config.get("crs", "")
    if source_crs and target_crs and source_crs != target_crs:
        x, y, z = transform_crs(x, y, z, source_crs, target_crs)

    section = config["section"]
    height = config.get("height") or {}
    transform = config["transform"]["section_to_pixel"]
    p0 = section["p0"]
    p1 = section["p1"]
    z_offset = float(height.get("z_offset", 0.0))
    s, d, z_section = world_to_section(x, y, z, p0, p1, z_offset)
    u, v = section_to_pixel(s, z_section, transform)
    return {
        "s": s,
        "d": d,
        "z_section": z_section,
        "u": u,
        "v": v,
    }


def world_to_section(x, y, z, p0, p1, z_offset=0.0):
    direction, perpendicular, _length = compute_section_basis(p0, p1)
    dx = float(x) - float(p0[0])
    dy = float(y) - float(p0[1])
    s = dx * direction[0] + dy * direction[1]
    d = dx * perpendicular[0] + dy * perpendicular[1]
    return s, d, float(z) - float(z_offset)


def compute_section_basis(p0, p1):
    dx = float(p1[0]) - float(p0[0])
    dy = float(p1[1]) - float(p0[1])
    length = math.hypot(dx, dy)
    if length <= 0.0:
        raise ValueError("section p0/p1 length is zero")
    return (dx / length, dy / length), (-dy / length, dx / length), length


def section_to_pixel(s, z_section, coeffs):
    u_coeffs = coeffs["u"]
    v_coeffs = coeffs["v"]
    u = u_coeffs[0] + u_coeffs[1] * float(s) + u_coeffs[2] * float(z_section)
    v = v_coeffs[0] + v_coeffs[1] * float(s) + v_coeffs[2] * float(z_section)
    return u, v


def read_gps(path):
    if not path.exists():
        return pd.DataFrame()
    return pd.read_csv(path)


def load_plateau_geojson(path):
    if not path.exists():
        return {"type": "FeatureCollection", "features": []}
    return load_json(path)


def project_external_gps(csv_path, config, source_crs="EPSG:4326"):
    """Project an external GPS CSV (lon,lat,z or x,y,z) onto the section."""
    df = pd.read_csv(csv_path)
    lon_col = _first_col(df, ["lon", "longitude", "x", "lng"])
    lat_col = _first_col(df, ["lat", "latitude", "y"])
    z_col = _first_col(df, ["z", "altitude", "elevation", "height", "alt"])
    if lon_col is None or lat_col is None or z_col is None:
        print("Warning: --extra-gps CSV must have lon/lat/z columns")
        return []
    records = []
    for _, row in df.iterrows():
        try:
            result = project_world_xyz_to_pixel(
                float(row[lon_col]),
                float(row[lat_col]),
                float(row[z_col]),
                config,
                source_crs=source_crs,
            )
            records.append(result)
        except Exception:
            continue
    return records


def _first_col(df, candidates):
    lower_to_name = {c.lower(): c for c in df.columns}
    for c in candidates:
        if c in df.columns:
            return c
        match = lower_to_name.get(c.lower())
        if match:
            return match
    return None


def report_world_geojson(package_dir):
    path = package_dir / "world_projected.geojson"
    if not path.exists():
        return
    data = load_json(path)
    n = len(data.get("features", []))
    crs = data.get("crs_auth_id", "unknown")
    print("world_projected.geojson: {} features, CRS={}".format(n, crs))


def report_geopackage(package_dir):
    path = package_dir / "section_projected.gpkg"
    if not path.exists():
        return
    try:
        import geopandas as gpd
        import fiona
        layers = fiona.listlayers(str(path))
        for layer_name in layers:
            gdf = gpd.read_file(str(path), layer=layer_name)
            print(
                "section_projected.gpkg/{}: {} features, CRS={}".format(
                    layer_name, len(gdf), gdf.crs
                )
            )
    except ImportError:
        print("section_projected.gpkg: exists (install geopandas to inspect)")


def plot_overlay(background, gps, plateau, output_path, config, extra_records=None):
    fig, ax = plt.subplots(figsize=(12, 8))
    ax.imshow(background)
    draw_plateau(ax, plateau)
    if not gps.empty and {"u", "v"}.issubset(gps.columns):
        inside = gps["inside_buffer"].astype(str).str.lower().eq("true")
        ax.scatter(
            gps.loc[~inside, "u"],
            gps.loc[~inside, "v"],
            s=6,
            c="#999999",
            alpha=0.25,
            label="outside buffer",
        )
        ax.scatter(
            gps.loc[inside, "u"],
            gps.loc[inside, "v"],
            s=8,
            c="#1f77b4",
            alpha=0.75,
            label="inside buffer",
        )
    if extra_records:
        extra_u = [r["u"] for r in extra_records]
        extra_v = [r["v"] for r in extra_records]
        ax.scatter(
            extra_u,
            extra_v,
            s=12,
            c="#e63946",
            alpha=0.85,
            marker="^",
            label="external GPS",
        )
    ax.legend(loc="best")
    ax.set_title("GPS projected on section image")
    ax.set_xlim(0, background.width)
    ax.set_ylim(background.height, 0)
    ax.set_xlabel("u pixel")
    ax.set_ylabel("v pixel")
    subtitle = config.get("crs") or ""
    if subtitle:
        ax.text(0.01, 0.01, subtitle, transform=ax.transAxes, fontsize=8)
    fig.tight_layout()
    fig.savefig(output_path, dpi=200)
    plt.close(fig)


def draw_plateau(ax, plateau):
    features = plateau.get("features", []) if isinstance(plateau, dict) else []
    vertex_u = []
    vertex_v = []
    for feature in features:
        geometry = feature.get("geometry") or {}
        properties = feature.get("properties") or {}
        geometry_type = geometry.get("type")
        if geometry_type == "LineString":
            plotted = draw_plateau_segment_from_properties(ax, properties)
            if not plotted:
                draw_plateau_linestring_from_coordinates(ax, geometry)
        elif geometry_type == "Point":
            u = to_float(properties.get("u"))
            v = to_float(properties.get("v"))
            if u is not None and v is not None:
                vertex_u.append(u)
                vertex_v.append(v)
    if vertex_u:
        ax.scatter(
            vertex_u,
            vertex_v,
            s=10,
            c="#2ca02c",
            alpha=0.75,
            marker="x",
            label="PLATEAU/CAD vertices",
        )


def draw_plateau_segment_from_properties(ax, properties):
    u0 = to_float(properties.get("u0"))
    v0 = to_float(properties.get("v0"))
    u1 = to_float(properties.get("u1"))
    v1 = to_float(properties.get("v1"))
    if None in (u0, v0, u1, v1):
        return False
    ax.plot([u0, u1], [v0, v1], color="#ff7f0e", linewidth=1.0, alpha=0.8)
    return True


def draw_plateau_linestring_from_coordinates(ax, geometry):
    # Fallback for GeoJSON readers that only preserve section coordinates.
    # The package's LineString convention is [s, z_section], not pixels, so
    # these cannot be placed on the image without u/v properties.
    return


def plot_z_histogram(gps, output_path):
    fig, ax = plt.subplots(figsize=(8, 5))
    if not gps.empty and "z_section" in gps.columns:
        values = pd.to_numeric(gps["z_section"], errors="coerce").dropna()
        if not values.empty:
            ax.hist(values, bins=40, color="#4c78a8", alpha=0.85)
    ax.set_title("GPS z_section distribution")
    ax.set_xlabel("z_section")
    ax.set_ylabel("count")
    fig.tight_layout()
    fig.savefig(output_path, dpi=200)
    plt.close(fig)


def write_floor_counts(package_dir, gps):
    floor_path = package_dir / "floor_reference.csv"
    output_path = package_dir / "validation_floor_counts.csv"
    if gps.empty or "z_section" not in gps.columns or not floor_path.exists():
        pd.DataFrame(columns=["floor", "count"]).to_csv(output_path, index=False)
        return

    floors = pd.read_csv(floor_path)
    floor_col = first_existing_column(floors, ["floor", "name", "label", "storey"])
    z_col = first_existing_column(floors, ["z", "z_center", "center_z", "elevation"])
    if floor_col is None or z_col is None:
        pd.DataFrame(columns=["floor", "count"]).to_csv(output_path, index=False)
        return

    floor_z = pd.to_numeric(floors[z_col], errors="coerce").to_numpy()
    floor_labels = floors[floor_col].astype(str).to_numpy()
    gps_values = pd.to_numeric(gps["z_section"], errors="coerce")
    assigned = []
    for value in gps_values:
        if np.isnan(value) or len(floor_z) == 0:
            assigned.append("")
            continue
        nearest_index = int(np.nanargmin(np.abs(floor_z - value)))
        assigned.append(floor_labels[nearest_index])
    counts = pd.Series(assigned, name="floor").value_counts(dropna=False)
    counts.rename_axis("floor").reset_index(name="count").to_csv(output_path, index=False)


def maybe_write_gmm_centers(package_dir, gps):
    output_path = package_dir / "validation_gmm_centers.csv"
    if gps.empty or "z_section" not in gps.columns:
        return
    values = pd.to_numeric(gps["z_section"], errors="coerce").dropna().to_numpy()
    if len(values) < 3:
        return
    try:
        from sklearn.mixture import GaussianMixture
    except Exception:
        return
    n_components = min(6, max(1, int(np.sqrt(len(values) / 50.0))))
    model = GaussianMixture(n_components=n_components, random_state=0)
    model.fit(values.reshape(-1, 1))
    centers = np.sort(model.means_.reshape(-1))
    pd.DataFrame({"cluster": range(len(centers)), "z_section_center": centers}).to_csv(
        output_path, index=False
    )


def first_existing_column(dataframe, candidates):
    lower_to_name = {column.lower(): column for column in dataframe.columns}
    for candidate in candidates:
        if candidate in dataframe.columns:
            return candidate
        match = lower_to_name.get(candidate.lower())
        if match:
            return match
    return None


def to_float(value):
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


if __name__ == "__main__":
    main()
