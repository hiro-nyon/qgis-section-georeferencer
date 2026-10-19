"""Fail CI if any test is skipped, including missing QGIS or GDAL imports."""

import os
from pathlib import Path
import sys
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from qgis.core import Qgis
from osgeo import gdal

print("QGIS", Qgis.QGIS_VERSION, "GDAL", gdal.VersionInfo(), flush=True)
root = Path(__file__).resolve().parents[1]
suite = unittest.defaultTestLoader.discover(str(root / "tests"))
result = unittest.TextTestRunner(verbosity=2).run(suite)
if result.skipped:
    print("QGIS CI must run every test; skipped:", result.skipped)
sys.exit(0 if result.wasSuccessful() and not result.skipped else 1)
