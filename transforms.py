"""Pure Python section coordinate and affine transform utilities.

This module intentionally does not import QGIS so it can be tested with a
normal Python interpreter.
"""

from __future__ import annotations

import math


SINGULAR_TOLERANCE = 1.0e-12
DEFORMATION_STRETCH_WARNING_PERCENT = 20.0
DEFORMATION_SHEAR_WARNING_DEGREES = 5.0


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
    pixel_to_section_coeffs = {
        "s": [float(value) for value in coeff_s],
        "z": [float(value) for value in coeff_z],
    }
    return {
        "pixel_to_section": pixel_to_section_coeffs,
        "section_to_pixel": {
            "u": coeff_u,
            "v": coeff_v,
        },
        "residuals": residuals,
        "rms": float(rms),
        "deformation": analyze_affine_deformation(pixel_to_section_coeffs),
    }


def analyze_affine_deformation(pixel_to_section_coeffs):
    """Return human-readable deformation metrics for an affine transform.

    Absolute scale and rotation are recorded but are not considered harmful.
    Warnings are based on the ratio of the principal stretches and on the
    departure of the transformed image axes from a right angle.
    """
    try:
        s_coeffs = pixel_to_section_coeffs["s"]
        z_coeffs = pixel_to_section_coeffs["z"]
        ds_du, ds_dv = float(s_coeffs[1]), float(s_coeffs[2])
        dz_du, dz_dv = float(z_coeffs[1]), float(z_coeffs[2])
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise ValueError("Invalid pixel-to-section affine coefficients.") from exc

    scale_u = math.hypot(ds_du, dz_du)
    scale_v = math.hypot(ds_dv, dz_dv)
    determinant = ds_du * dz_dv - ds_dv * dz_du
    if (
        scale_u <= SINGULAR_TOLERANCE
        or scale_v <= SINGULAR_TOLERANCE
        or abs(determinant) <= SINGULAR_TOLERANCE
    ):
        raise ValueError("Affine transform is singular or nearly singular.")

    axis_cosine = (ds_du * ds_dv + dz_du * dz_dv) / (scale_u * scale_v)
    axis_cosine = max(-1.0, min(1.0, axis_cosine))
    axis_angle = math.degrees(math.acos(axis_cosine))
    signed_shear = 90.0 - axis_angle

    # Singular values describe the maximum and minimum directional stretch
    # independently of rotation and of the chosen image axes.
    frobenius_squared = (
        ds_du * ds_du + ds_dv * ds_dv + dz_du * dz_du + dz_dv * dz_dv
    )
    discriminant = max(
        0.0, frobenius_squared * frobenius_squared - 4.0 * determinant * determinant
    )
    eigenvalue_max = 0.5 * (frobenius_squared + math.sqrt(discriminant))
    eigenvalue_min = 0.5 * (frobenius_squared - math.sqrt(discriminant))
    principal_scale_max = math.sqrt(max(0.0, eigenvalue_max))
    principal_scale_min = math.sqrt(max(0.0, eigenvalue_min))
    if principal_scale_min <= SINGULAR_TOLERANCE:
        raise ValueError("Affine transform is singular or nearly singular.")

    stretch_ratio = principal_scale_max / principal_scale_min
    stretch_difference_percent = (stretch_ratio - 1.0) * 100.0
    axis_scale_ratio = max(scale_u, scale_v) / min(scale_u, scale_v)
    axis_scale_difference_percent = (axis_scale_ratio - 1.0) * 100.0
    shear_degrees = abs(signed_shear)
    rotation_degrees = math.degrees(math.atan2(dz_du, ds_du))

    deformation = {
        "scale_u_m_per_px": float(scale_u),
        "scale_v_m_per_px": float(scale_v),
        "axis_scale_difference_percent": float(axis_scale_difference_percent),
        "principal_scale_max_m_per_px": float(principal_scale_max),
        "principal_scale_min_m_per_px": float(principal_scale_min),
        "stretch_ratio": float(stretch_ratio),
        "stretch_difference_percent": float(stretch_difference_percent),
        "axis_angle_degrees": float(axis_angle),
        "signed_shear_degrees": float(signed_shear),
        "shear_degrees": float(shear_degrees),
        "rotation_degrees": float(rotation_degrees),
        "area_scale_m2_per_px2": float(abs(determinant)),
        "orientation": "preserved" if determinant > 0.0 else "flipped",
        "warning_thresholds": {
            "stretch_difference_percent": DEFORMATION_STRETCH_WARNING_PERCENT,
            "shear_degrees": DEFORMATION_SHEAR_WARNING_DEGREES,
        },
    }
    warnings = deformation_warnings(deformation)
    deformation["warning"] = bool(warnings)
    deformation["warnings"] = warnings
    return deformation


def deformation_warnings(deformation):
    """Build warnings for deformation metrics using the recorded thresholds."""
    if not isinstance(deformation, dict):
        return []
    thresholds = deformation.get("warning_thresholds") or {}
    stretch_limit = float(
        thresholds.get(
            "stretch_difference_percent", DEFORMATION_STRETCH_WARNING_PERCENT
        )
    )
    shear_limit = float(
        thresholds.get("shear_degrees", DEFORMATION_SHEAR_WARNING_DEGREES)
    )
    warnings = []
    try:
        stretch = float(deformation["stretch_difference_percent"])
        if stretch > stretch_limit:
            warnings.append(
                "directional stretch difference {:.1f}% exceeds {:.1f}%".format(
                    stretch, stretch_limit
                )
            )
    except (KeyError, TypeError, ValueError):
        pass
    try:
        shear = float(deformation["shear_degrees"])
        if shear > shear_limit:
            warnings.append(
                "shear {:.1f}° exceeds {:.1f}°".format(shear, shear_limit)
            )
    except (KeyError, TypeError, ValueError):
        pass
    return warnings


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
