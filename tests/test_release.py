import ast
import configparser
import importlib.util
from pathlib import Path
import tempfile
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[1]


class ReleaseTests(unittest.TestCase):
    def test_version_agrees_in_metadata_runtime_and_sample(self):
        import json
        metadata = configparser.ConfigParser()
        metadata.read(ROOT / "metadata.txt")
        version = metadata["general"]["version"]
        tree = ast.parse((ROOT / "section_georeferencer_dock.py").read_text())
        runtime = next(ast.literal_eval(node.value) for node in tree.body
                       if isinstance(node, ast.Assign)
                       and any(isinstance(t, ast.Name) and t.id == "PLUGIN_VERSION" for t in node.targets))
        self.assertEqual(version, runtime)
        sample = json.loads((ROOT / "examples/synthetic/section_config.json").read_text())
        self.assertEqual(version, sample["version"])
        self.assertIn("## " + version + " (", (ROOT / "CHANGELOG.md").read_text())

    def test_archive_is_reproducible_and_contains_only_distribution_files(self):
        spec = importlib.util.spec_from_file_location("build_plugin", ROOT / "scripts/build_plugin.py")
        builder = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(builder)
        with tempfile.TemporaryDirectory() as directory:
            archive = builder.build(directory)
            first = archive.read_bytes()
            self.assertEqual(builder.build(directory).read_bytes(), first)
            with zipfile.ZipFile(archive) as source:
                names = source.namelist()
                self.assertIsNone(source.testzip())
                for required in ("LICENSE", "metadata.txt", "__init__.py", "icon.svg",
                                 "examples/synthetic/demo.qgs", "docs/synthetic-demo.png"):
                    self.assertIn("section_georeferencer/" + required, names)
                for name in names:
                    self.assertTrue(name.startswith("section_georeferencer/"))
                    self.assertNotIn("__pycache__", name)
                    self.assertNotIn(".git", name)
                    self.assertFalse(name.endswith("~"))
                    relative = name.removeprefix("section_georeferencer/")
                    self.assertEqual(source.read(name), (ROOT / relative).read_bytes())


if __name__ == "__main__":
    unittest.main()
