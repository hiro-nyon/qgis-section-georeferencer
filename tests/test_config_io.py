import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config_io  # noqa: E402


class SaveLoadTests(unittest.TestCase):
    def test_image_path_is_stored_relative_to_the_config(self):
        with tempfile.TemporaryDirectory() as directory:
            image_path = os.path.join(directory, "drawings", "section.png")
            config_path = os.path.join(directory, "section.json")
            config_io.save_config(config_path, {"image": {"path": image_path}})
            with open(config_path, encoding="utf-8") as file_obj:
                stored = json.load(file_obj)
        self.assertEqual(stored["image"]["path"], os.path.join("drawings", "section.png"))

    def test_saving_does_not_mutate_the_caller_config(self):
        with tempfile.TemporaryDirectory() as directory:
            config = {"image": {"path": os.path.join(directory, "section.png")}}
            config_io.save_config(os.path.join(directory, "c.json"), config)
            self.assertTrue(os.path.isabs(config["image"]["path"]))

    def test_relative_image_path_resolves_against_the_config(self):
        resolved = config_io.resolve_image_path(
            os.path.join("drawings", "section.png"),
            os.path.join("/tmp", "packages", "section.json"),
        )
        self.assertEqual(
            resolved, os.path.join("/tmp", "packages", "drawings", "section.png")
        )

    def test_absolute_image_path_is_kept(self):
        absolute = os.path.join("/tmp", "section.png")
        self.assertEqual(config_io.resolve_image_path(absolute, "/other/c.json"), absolute)

    def test_empty_image_path_round_trips(self):
        self.assertEqual(config_io.resolve_image_path("", "/tmp/c.json"), "")


class LoadErrorTests(unittest.TestCase):
    def test_malformed_json_raises_value_error(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "bad.json")
            with open(path, "w", encoding="utf-8") as file_obj:
                file_obj.write("{not json")
            with self.assertRaises(ValueError):
                config_io.load_config(path)

    def test_missing_file_raises_value_error(self):
        with self.assertRaises(ValueError):
            config_io.load_config("/nonexistent/section-config.json")

    def test_non_object_json_raises_value_error(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "list.json")
            with open(path, "w", encoding="utf-8") as file_obj:
                json.dump([1, 2, 3], file_obj)
            with self.assertRaises(ValueError):
                config_io.load_config(path)


class ConfigValidationTests(unittest.TestCase):
    def test_rejects_malformed_nested_values(self):
        invalid = [
            {"section": "oops"}, {"height": [1]}, {"projection": "bad"},
            {"image": {"path": ["scan.png"]}}, {"crs": 6677},
            {"section": {"p0": [0, 0]}},
            {"section": {"p0": [0, 0], "p1": [0, 0]}},
            {"section": {"p0": [0, 0], "p1": [1, float("inf")]}},
            {"height": {"z_offset": float("nan")}},
            {"projection": {"buffer_width_m": -1}},
            {"gcps": "bad"}, {"gcps": [{"u": 1}]},
            {"transform": {"pixel_to_section": {"s": [1]}}},
        ]
        for data in invalid:
            with self.subTest(data=data), self.assertRaises(ValueError):
                config_io.validate_config(data)

    def test_rejects_inconsistent_inverse(self):
        with self.assertRaises(ValueError):
            config_io.validate_config({"transform": {
                "pixel_to_section": {"s": [0, 1, 0], "z": [0, 0, 1]},
                "section_to_pixel": {"u": [0, 2, 0], "v": [0, 0, 1]},
            }})

    def test_validator_does_not_mutate_input_and_accepts_partial_config(self):
        original = {"height": {"z_offset": "2"}}
        validated = config_io.validate_config(original)
        self.assertEqual(validated["height"]["z_offset"], 2.0)
        self.assertEqual(original, {"height": {"z_offset": "2"}})


if __name__ == "__main__":
    unittest.main()
