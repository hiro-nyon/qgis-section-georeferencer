"""JSON configuration helpers for Section Georeferencer."""

from __future__ import annotations

import copy
import json
import math
import os


def save_config(path, config):
    """Save a plugin configuration to JSON.

    Image paths are stored relative to the config file when possible.
    """
    output = copy.deepcopy(config)
    base_dir = os.path.dirname(os.path.abspath(path))
    image_path = output.get("image", {}).get("path")
    if image_path:
        output.setdefault("image", {})["path"] = make_storable_image_path(
            image_path, base_dir
        )
    with open(path, "w", encoding="utf-8") as file_obj:
        json.dump(output, file_obj, indent=2, ensure_ascii=False)
        file_obj.write("\n")


def load_config(path):
    """Load a plugin configuration from JSON and raise clear ValueErrors."""
    try:
        with open(path, "r", encoding="utf-8") as file_obj:
            data = json.load(file_obj)
    except json.JSONDecodeError as exc:
        raise ValueError("Malformed JSON config: {}".format(exc)) from exc
    except OSError as exc:
        raise ValueError("Could not read config: {}".format(exc)) from exc
    return validate_config(data)


def validate_config(data):
    """Validate and normalize a config before the UI changes any session state.

    Optional sections support older configs, but supplied values must have the
    right shape and finite numbers. Unknown metadata is preserved.
    """
    if not isinstance(data, dict):
        raise ValueError("Config JSON must contain an object at the top level.")
    result = copy.deepcopy(data)
    for key in ("section", "image", "height", "projection"):
        value = result.get(key)
        if value is None:
            result[key] = {}
        elif not isinstance(value, dict):
            raise ValueError("Config '{}' must be an object.".format(key))
    for obj, key in ((result, "crs"), (result["image"], "path"),
                     (result["projection"], "z_field")):
        if obj.get(key) is not None and not isinstance(obj[key], str):
            raise ValueError("Config '{}' must be text.".format(key))
        if isinstance(obj.get(key), str) and "\x00" in obj[key]:
            raise ValueError("Config '{}' contains a null character.".format(key))
    section = result["section"]
    p0, p1 = section.get("p0"), section.get("p1")
    if (p0 is None) != (p1 is None):
        raise ValueError("Config section needs both p0 and p1.")
    if p0 is not None:
        for key in ("p0", "p1"):
            section[key] = _numbers(section[key], 2, "section." + key)
        if section["p0"] == section["p1"]:
            raise ValueError("Config section line has zero length.")
    for group, key in (("height", "z_offset"), ("projection", "buffer_width_m")):
        value = result[group].get(key)
        if value is not None:
            result[group][key] = _finite_number(value, group + "." + key)
    if result["projection"].get("buffer_width_m", 0) is None:
        result["projection"].pop("buffer_width_m")
    if result["projection"].get("buffer_width_m", 0) < 0:
        raise ValueError("Config buffer width must be zero or positive.")
    gcps = result.get("gcps")
    if gcps is None:
        gcps = []
    if not isinstance(gcps, list):
        raise ValueError("Config gcps must be a list.")
    result["gcps"] = []
    for index, gcp in enumerate(gcps):
        if not isinstance(gcp, dict) or not all(k in gcp for k in ("u", "v", "s", "z")):
            raise ValueError("Config GCP {} needs u, v, s and z.".format(index + 1))
        result["gcps"].append({
            k: _finite_number(gcp[k], "GCP {} {}".format(index + 1, k))
            for k in ("u", "v", "s", "z")
        })
    transform = result.get("transform")
    if transform is not None:
        if not isinstance(transform, dict):
            raise ValueError("Config transform must be an object or null.")
        for name, axes in (("pixel_to_section", ("s", "z")),
                           ("section_to_pixel", ("u", "v"))):
            coeffs = transform.get(name)
            if not isinstance(coeffs, dict) or not all(k in coeffs for k in axes):
                raise ValueError("Config transform is missing {} coefficients.".format(name))
            transform[name] = {k: _numbers(coeffs[k], 3, name + "." + k) for k in axes}
        a, b = transform["pixel_to_section"]["s"], transform["pixel_to_section"]["z"]
        c, d = transform["section_to_pixel"]["u"], transform["section_to_pixel"]["v"]
        # Check inverse consistency, including offsets, without requiring numpy.
        for actual, expected in ((c[0] + c[1]*a[0] + c[2]*b[0], 0),
                                 (d[0] + d[1]*a[0] + d[2]*b[0], 0),
                                 (c[1]*a[1] + c[2]*b[1], 1),
                                 (c[1]*a[2] + c[2]*b[2], 0),
                                 (d[1]*a[1] + d[2]*b[1], 0),
                                 (d[1]*a[2] + d[2]*b[2], 1)):
            if not math.isclose(actual, expected, rel_tol=1e-7, abs_tol=1e-7):
                raise ValueError("Config affine transforms are not mutual inverses.")
        # Derived UI metadata is recalculated, never trusted from the file.
        transform.pop("deformation", None)
        transform.pop("residuals", None)
        if transform.get("rms") is not None:
            transform["rms"] = _finite_number(transform["rms"], "transform.rms")
            if transform["rms"] < 0:
                raise ValueError("Config RMS cannot be negative.")
    return result


def _finite_number(value, label):
    try:
        if isinstance(value, bool):
            raise ValueError()
        number = float(value)
        if not math.isfinite(number):
            raise ValueError()
        return number
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("Config '{}' must be a finite number.".format(label)) from exc


def _numbers(value, length, label):
    if not isinstance(value, (list, tuple)) or len(value) != length:
        raise ValueError("Config '{}' needs {} numbers.".format(label, length))
    return [_finite_number(item, label) for item in value]


def make_storable_image_path(image_path, base_dir):
    if not image_path:
        return image_path
    absolute = os.path.abspath(image_path)
    try:
        relative = os.path.relpath(absolute, base_dir)
    except ValueError:
        return absolute
    if relative.startswith("..{}{}".format(os.sep, os.pardir)):
        return absolute
    return relative


def resolve_image_path(image_path, config_path):
    """Resolve a config image path against the config file location."""
    if not image_path:
        return image_path
    expanded = os.path.expanduser(image_path)
    if os.path.isabs(expanded):
        return expanded
    base_dir = os.path.dirname(os.path.abspath(config_path))
    return os.path.abspath(os.path.join(base_dir, expanded))
