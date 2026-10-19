"""Load the built ZIP in an isolated QGIS profile and export the bundled demo.

Uses QGIS's actual plugin loader with a small interface adapter; does not touch
an installed plugin or the user's profile. It is not an interactive GUI test.
"""
import configparser
import csv
import importlib
import os
from pathlib import Path
import sys
import tempfile
import zipfile

from build_plugin import build

ROOT = Path(__file__).resolve().parents[1]
meta = configparser.ConfigParser()
meta.read(ROOT / 'metadata.txt')
version = meta['general']['version']
archive = ROOT / 'dist' / ('section_georeferencer-' + version + '.zip')

with tempfile.TemporaryDirectory(prefix='section-zip-smoke-') as temp:
    temp = Path(temp)
    assert build(temp / 'rebuild').read_bytes() == archive.read_bytes(), 'ZIP build is not reproducible'
    profile = temp / 'profile'
    profile.mkdir()
    os.environ['QGIS_CUSTOM_CONFIG_PATH'] = str(profile)
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    plugins = temp / 'plugins'
    plugins.mkdir()
    with zipfile.ZipFile(archive) as source:
        assert source.testzip() is None
        for name in source.namelist():
            assert name.startswith('section_georeferencer/') and '..' not in Path(name).parts
            parts = Path(name).parts
            assert not any(part.startswith('.') for part in parts)
            assert not {'__pycache__', 'tests', 'scripts'}.intersection(parts)
            assert not name.endswith('~')
            assert source.read(name) == ROOT.joinpath(*parts[1:]).read_bytes(), name
        source.extractall(plugins)
    installed = plugins / 'section_georeferencer'
    assert (installed / 'LICENSE').exists()
    from qgis.core import QgsApplication, QgsProject, QgsVectorLayer, Qgis
    from qgis.gui import QgsMapCanvas
    from qgis.PyQt.QtWidgets import QMainWindow
    app = QgsApplication([], True, str(profile))
    app.initQgis()

    class Iface:
        def __init__(self):
            self.window = QMainWindow()
            self.canvas = QgsMapCanvas(self.window)
            self.window.setCentralWidget(self.canvas)
            self.layer = None
            self.messages = []
        def mainWindow(self): return self.window
        def mapCanvas(self): return self.canvas
        def activeLayer(self): return self.layer
        def messageBar(self): return self
        def pushMessage(self, *args, **kwargs): self.messages.append(args)
        def addToolBarIcon(self, action): pass
        def removeToolBarIcon(self, action): pass
        def addPluginToMenu(self, menu, action): pass
        def removePluginMenu(self, menu, action): pass

    iface = Iface()
    import qgis.utils
    qgis.utils.iface = iface
    qgis.utils.plugin_paths.insert(0, str(plugins))
    sys.path.insert(0, str(plugins))
    qgis.utils.updateAvailablePlugins()
    assert qgis.utils.loadPlugin('section_georeferencer'), 'ZIP plugin import failed'
    assert qgis.utils.startPlugin('section_georeferencer'), 'ZIP plugin startup failed'
    plugin = qgis.utils.plugins['section_georeferencer']
    assert Path(importlib.import_module('section_georeferencer').__file__).parent == installed
    plugin.toggle_dock(True)
    app.processEvents()
    dock = plugin.dock
    runtime = importlib.import_module('section_georeferencer.section_georeferencer_dock')
    assert runtime.PLUGIN_VERSION == version
    assert '## ' + version + ' (' in (installed / 'CHANGELOG.md').read_text()
    demo = installed / 'examples' / 'synthetic'
    project = QgsProject.instance()
    assert project.read(str(demo / 'demo.qgs'))
    layers = {layer.name(): layer for layer in project.mapLayers().values()}
    assert all(layer.isValid() for layer in layers.values())
    assert len(layers) == 2
    io = importlib.import_module('section_georeferencer.config_io')
    sample = io.load_config(str(demo / 'section_config.json'))
    assert sample['version'] == version
    assert dock._apply_config(sample, str(demo / 'section_config.json'))
    building, gps = layers['synthetic_building'], layers['synthetic_gps']
    dock._set_building(building, [f.id() for f in building.getFeatures()])
    iface.layer = building
    dock._project_active_vector_layer()
    iface.layer = gps
    dock._project_active_point_layer()
    assert len(dock._gps_records()) == 7
    assert sum(r['inside_buffer'] for r in dock._gps_records()) == 6
    assert dock._package_prerequisites_ok()
    exporter = importlib.import_module('section_georeferencer.package_exporter')
    output = temp / 'export'
    config = dock._build_config()
    config['package_files'] = exporter.package_file_names()
    exporter.export_section_analysis_package(
        str(output), dock.canvas, config, dock._gps_records(), dock._plateau_records(),
        dock._plateau_segments(), dock._build_projection_summary('smoke-test'))
    with (output / 'gps_projected.csv').open() as stream:
        rows = list(csv.DictReader(stream))
    assert len(rows) == 7
    from osgeo import gdal, osr, ogr
    raster = gdal.Open(str(output / 'section_background_georeferenced.tif'))
    assert osr.SpatialReference(wkt=raster.GetProjection()).IsLocal()
    assert raster.GetMetadataItem('SECTION_SOURCE_CRS') == 'EPSG:6677'
    raster = None
    gpkg = ogr.Open(str(output / 'section_projected.gpkg'))
    assert gpkg.GetLayerByName('gps').GetFeatureCount() == 7
    gpkg = None
    # Every package file is present, and package-relative background reloads.
    for key, name in config['package_files'].items():
        if key != 'gcp_residuals': assert (output / name).exists(), name
    package_config = output / 'section_config.json'
    assert dock._apply_config(io.load_config(str(package_config)), str(package_config))
    assert dock.canvas.has_image()
    assert Path(dock.image_path) == output / 'section_background.png'
    assert qgis.utils.unloadPlugin('section_georeferencer')
    project.clear()
    print('ZIP smoke test passed:', version, '| QGIS', Qgis.QGIS_VERSION,
          '| isolated load/start/export/reload/unload | 7 GPS rows, 6 inside buffer')
