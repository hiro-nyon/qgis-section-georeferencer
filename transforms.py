"""Pure Python section coordinate and affine transform utilities.

This module intentionally does not import QGIS so it can be tested with a
normal Python interpreter.
"""

from __future__ import annotations

import math


SINGULAR_TOLERANCE = 1.0e-12


def compute_section_basis(p0, p1):
    """Return direction, left-hand perpendicular, and section length."""
    x0, y0 = _xy(p0)
    x1, y1 = _xy(p1)
    dx = x1 - x0
    dy = y1 - y0
    length = math.hypot(dx, dy)
    if length <= 0.0:
        raise ValueError("Section line length is zero; P0 and P1 must differ.")
    direction = (dx / length, dy / length)
    perpendicular = (-dy / length, dx / length)
    return direction, perpendicular, length


def world_to_section(x, y, z, p0, p1, z_offset=0.0):
    """Project a world coordinate into local section coordinates."""
    x0, y0 = _xy(p0)
    direction, perpendicular, _length = compute_section_basis(p0, p1)
    dx = float(x) - x0
    dy = float(y) - y0
    s = dx * direction[0] + dy * direction[1]
    d = dx * perpendicular[0] + dy * perpendicular[1]
    z_section = float(z) - float(z_offset)
    return s, d, z_section


def fit_affine_pixel_to_section(gcps):
    """Fit pixel-to-section and inverse affine transforms from GCPs.

    The fitted model is:

        s = a0 + a1*u + a2*v
        z = b0 + b1*u + b2*v
    """
    try:
        import numpy as np
    except Exception as exc:  # pragma: no cover - depends on environment
        raise ImportError(
            "numpy is required to compute the affine transform. "
            "Install numpy in the QGIS Python environment."
        ) from exc

    points = [_gcp_values(gcp) for gcp in gcps]
    if len(points) < 3:
        raise ValueError("At least 3 GCPs are required to compute an affine transform.")

    design = np.array([[1.0, u, v] for u, v, _s, _z in points], dtype=float)
    s_obs = np.array([s for _u, _v, s, _z in points], dtype=float)
    z_obs = np.array([z for _u, _v, _s, z in points], dtype=float)

    coeff_s, _resid_s, rank_s, _singular_s = np.linalg.lstsq(design, s_obs, rcond=None)
    coeff_z, _resid_z, rank_z, _singular_z = np.linalg.lstsq(design, z_obs, rcond=None)
    if rank_s < 3 or rank_z < 3:
        raise ValueError(
            "GCP pixel coordinates are degenerate; use non-collinear image points."
        )

    matrix = np.array(
        [[coeff_s[1], coeff_s[2]], [coeff_z[1], coeff_z[2]]],
        dtype=float,
    )
    determinant = float(np.linalg.det(matrix))
    if abs(determinant) <= SINGULAR_TOLERANCE:
        raise ValueError(
            "Affine transform is singular or nearly singular; revise the GCPs."
        )

    inverse = np.linalg.inv(matrix)
    offset = np.array([coeff_s[0], coeff_z[0]], dtype=float)
    inverse_offset = -inverse.dot(offset)

    coeff_u = [float(inverse_offset[0]), float(inverse[0, 0]), float(inverse[0, 1])]
    coeff_v = [float(inverse_offset[1]), float(inverse[1, 0]), float(inverse[1, 1])]

    residuals = []
    squared_distances = []
    for u, v, s, z in points:
        s_pred = float(coeff_s[0] + coeff_s[1] * u + coeff_s[2] * v)
        z_pred = float(coeff_z[0] + coeff_z[1] * u + coeff_z[2] * v)
        ds = s_pred - s
        dz = z_pred - z
        residuals.append(
            {
                "u": float(u),
                "v": float(v),
                "s_obs": float(s),
                "z_obs": float(z),
                "s_pred": s_pred,
                "z_pred": z_pred,
                "ds": float(ds),
                "dz": float(dz),
            }
        )
        squared_distances.append(ds * ds + dz * dz)

    rms = math.sqrt(sum(squared_distances) / len(squared_distances))
    return {
        "pixel_to_section": {
            "s": [float(value) for value in coeff_s],
            "z": [float(value) for value in coeff_z],
        },
        "section_to_pixel": {
            "u": coeff_u,
            "v": coeff_v,
        },
        "residuals": residuals,
        "rms": float(rms),
    }


def pixel_to_section(u, v, coeffs):
    """Apply pixel-to-section affine coefficients."""
    s_coeffs = coeffs["s"]
    z_coeffs = coeffs["z"]
    u = float(u)
    v = float(v)
    s = s_coeffs[0] + s_coeffs[1] * u + s_coeffs[2] * v
    z = z_coeffs[0] + z_coeffs[1] * u + z_coeffs[2] * v
    return float(s), float(z)


def section_to_pixel(s, z, coeffs):
    """Apply section-to-pixel affine coefficients."""
    u_coeffs = coeffs["u"]
    v_coeffs = coeffs["v"]
    s = float(s)
    z = float(z)
    u = u_coeffs[0] + u_coeffs[1] * s + u_coeffs[2] * z
    v = v_coeffs[0] + v_coeffs[1] * s + v_coeffs[2] * z
    return float(u), float(v)


def _xy(point):
    return float(point[0]), float(point[1])


def _gcp_values(gcp):
    if isinstance(gcp, dict):
        return (
            float(gcp["u"]),
            float(gcp["v"]),
            float(gcp["s"]),
            float(gcp["z"]),
        )
    return (float(gcp[0]), float(gcp[1]), float(gcp[2]), float(gcp[3]))
