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


if __name__ == "__main__":
    unittest.main()
