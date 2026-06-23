"""JSON configuration helpers for Section Georeferencer."""

from __future__ import annotations

import copy
import json
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
    if not isinstance(data, dict):
        raise ValueError("Config JSON must contain an object at the top level.")
    return data


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
