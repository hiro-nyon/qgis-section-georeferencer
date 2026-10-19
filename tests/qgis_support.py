"""Shared setup for tests that need PyQGIS.

These tests are skipped under a plain Python interpreter. Run them with the
Python that ships with QGIS, for example on macOS:

    PYTHONHOME=/Applications/QGIS.app/Contents/Frameworks \\
    QT_QPA_PLATFORM=offscreen \\
    /Applications/QGIS.app/Contents/MacOS/python3.12 -m unittest discover -s tests
"""

import importlib.util
import os
import sys

PLUGIN_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PACKAGE_NAME = "section_georeferencer"

try:
    from qgis.core import QgsApplication
except ImportError:  # pragma: no cover - depends on the interpreter
    QgsApplication = None

HAVE_QGIS = QgsApplication is not None
SKIP_REASON = "PyQGIS is not available; run with the QGIS Python interpreter."

_app = None


def start_qgis():
    """Start one headless QgsApplication for the whole test process."""
    global _app
    if _app is None:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        _app = QgsApplication.instance() or QgsApplication([], True)
        _app.initQgis()
    return _app


def plugin_module(name):
    """Import a plugin submodule as part of the plugin package.

    The plugin uses relative imports, so its modules have to be imported as a
    package whatever the checkout directory is called.
    """
    if PACKAGE_NAME not in sys.modules:
        spec = importlib.util.spec_from_file_location(
            PACKAGE_NAME,
            os.path.join(PLUGIN_DIR, "__init__.py"),
            submodule_search_locations=[PLUGIN_DIR],
        )
        package = importlib.util.module_from_spec(spec)
        sys.modules[PACKAGE_NAME] = package
        spec.loader.exec_module(package)
    return importlib.import_module("{}.{}".format(PACKAGE_NAME, name))


class FakeIface:
    """The part of QgisInterface the dock uses, with a real map canvas."""

    def __init__(self):
        from qgis.gui import QgsMapCanvas
        from qgis.PyQt.QtWidgets import QMainWindow

        self.window = QMainWindow()
        self.canvas = QgsMapCanvas(self.window)
        self.window.setCentralWidget(self.canvas)
        self.layer = None
        self.messages = []

    def mapCanvas(self):
        return self.canvas

    def activeLayer(self):
        return self.layer

    def mainWindow(self):
        return self.window

    def messageBar(self):
        return self

    def pushMessage(self, title, message, level=None, duration=None):
        self.messages.append(message)
