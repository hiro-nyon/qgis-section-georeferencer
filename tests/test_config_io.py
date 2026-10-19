import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config_io


class ConfigTests(unittest.TestCase):
    def test_portable_round_trip_does_not_mutate_input(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "section.json")
            for image in (os.path.join(directory, "drawings", "section.png"), ""):
                with self.subTest(image=image):
                    config = {"image": {"path": image}, "height": {"z_offset": "2"}}
                    config_io.save_config(path, config)
                    loaded = config_io.load_config(path)
                    self.assertEqual(config["image"]["path"], image)
                    self.assertEqual(config["height"]["z_offset"], "2")
                    self.assertEqual(loaded["height"]["z_offset"], 2)
                    self.assertFalse(os.path.isabs(loaded["image"]["path"]))
                    self.assertEqual(config_io.resolve_image_path(loaded["image"]["path"], path), image)
                    self.assertEqual(config_io.resolve_image_path(image, path), image)

    def test_unreadable_or_non_object_config_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "bad.json")
            with self.assertRaises(ValueError):
                config_io.load_config(path)
            for text in ("{not json", json.dumps([1, 2, 3])):
                with self.subTest(text=text):
                    with open(path, "w", encoding="utf-8") as stream:
                        stream.write(text)
                    with self.assertRaises(ValueError):
                        config_io.load_config(path)

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
            {"transform": {
                "pixel_to_section": {"s": [0, 1, 0], "z": [0, 0, 1]},
                "section_to_pixel": {"u": [0, 2, 0], "v": [0, 0, 1]},
            }},
        ]
        for data in invalid:
            with self.subTest(data=data), self.assertRaises(ValueError):
                config_io.validate_config(data)
