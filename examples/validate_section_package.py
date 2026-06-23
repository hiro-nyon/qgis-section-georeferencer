#!/usr/bin/env python3
"""Validate a Section Georeferencer analysis package outside QGIS.

Usage:
    python validate_section_package.py /path/to/section_package

Required packages:
    pandas, numpy, matplotlib, pillow

Optional:
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
    args = parser.parse_args()
    package_dir = Path(args.package_dir)

    config = load_json(package_dir / "section_config.json")
    background = Image.open(package_dir / "section_background.png").convert("RGB")
    gps = read_gps(package_dir / "gps_projected.csv")
    plateau = load_plateau_geojson(package_dir / "plateau_projected.geojson")

    plot_overlay(background, gps, plateau, package_dir / "validation_overlay.png", config)
    plot_z_histogram(gps, package_dir / "validation_z_histogram.png")
    write_floor_counts(package_dir, gps)
    maybe_write_gmm_centers(package_dir, gps)


def load_json(path):
    with path.open("r", encoding="utf-8") as file_obj:
        return json.load(file_obj)


def project_world_xyz_to_pixel(x, y, z, config):
    """Project one world/project CRS XYZ point to section image pixels.

    This mirrors the plugin's math and is useful when a separate Python
    workflow has GPS/PLATEAU coordinates already transformed to the same CRS as
    section_config.json, for example EPSG:6697.
    """
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


def plot_overlay(background, gps, plateau, output_path, config):
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
