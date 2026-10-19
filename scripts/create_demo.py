"""Regenerate synthetic sample data and a screenshot using QGIS's Python."""
import json
import os
from pathlib import Path
import sys
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tests'))
from qgis_support import start_qgis, plugin_module, FakeIface
app = start_qgis()
from qgis.core import QgsProject, QgsCoordinateReferenceSystem, QgsVectorLayer, QgsRectangle, QgsReferencedRectangle, Qgis
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QImage, QPainter, QColor, QPen, QFont

out = ROOT / 'examples' / 'synthetic'
out.mkdir(parents=True, exist_ok=True)
image = QImage(1000, 550, QImage.Format_RGB32)
image.fill(QColor('#faf9f6'))
painter = QPainter(image)
painter.setRenderHint(QPainter.Antialiasing)
painter.setPen(QPen(QColor('#374151'), 3))
painter.drawRect(100, 80, 800, 320)
painter.setFont(QFont('Arial', 16))
for height in range(0, 41, 10):
    y = 400 - height * 8
    painter.drawLine(100, y, 900, y)
    painter.drawText(20, y + 6, str(height) + ' m')
painter.setPen(QColor('#596579'))
painter.drawText(100, 505, 'SYNTHETIC SECTION  |  100 m x 40 m  |  Not a real building')
painter.end()
image.save(str(out / 'section.png'))

crs = {'type': 'name', 'properties': {'name': 'urn:ogc:def:crs:EPSG::6677'}}
def write_layer(name, features):
    data = {'type': 'FeatureCollection', 'name': name, 'crs': crs, 'features': features}
    path = out / (name + '.geojson')
    path.write_text(json.dumps(data, indent=2) + '\n', encoding='utf-8')
    layer = QgsVectorLayer(str(path), name, 'ogr')
    assert layer.isValid()
    QgsProject.instance().addMapLayer(layer)
    return layer

def feature(geometry, properties):
    return {'type': 'Feature', 'geometry': geometry, 'properties': properties}

# Four vertical faces: a synthetic rectangular building straddling y=0.
faces = [
    [[0,-2,0],[100,-2,0],[100,-2,40],[0,-2,40],[0,-2,0]],
    [[0,2,0],[100,2,0],[100,2,40],[0,2,40],[0,2,0]],
    [[0,-2,0],[0,2,0],[0,2,40],[0,-2,40],[0,-2,0]],
    [[100,-2,0],[100,2,0],[100,2,40],[100,-2,40],[100,-2,0]],
]
project = QgsProject.instance()
project.setCrs(QgsCoordinateReferenceSystem('EPSG:6677'))
building = write_layer('synthetic_building', [feature(
    {'type':'MultiPolygon','coordinates': [[face] for face in faces]},
    {'building_id':'synthetic-01','measuredHeight':40.0})])
points = [(12,0,5),(25,1,5),(45,-1,15),(62,0,15),(75,1,25),(87,-1,35),(52,30,25)]
gps = write_layer('synthetic_gps', [feature(
    {'type':'Point','coordinates': list(point)}, {'sample_id':i+1,'note':'synthetic'})
    for i, point in enumerate(points)])

iface = FakeIface()
iface.canvas.setDestinationCrs(project.crs())
iface.canvas.setLayers([gps, building])
iface.canvas.setExtent(QgsRectangle(-10,-10,110,40))
dock = plugin_module('section_georeferencer_dock').SectionGeoreferencerDock(iface)
dock._set_building(building, [f.id() for f in building.getFeatures()])
dock._set_section((0,0),(100,0))
dock._load_image(str(out / 'section.png'))
dock.gcps = [{'u':u,'v':v,'s':s,'z':z} for u,v,s,z in
             [(100,400,0,0),(900,400,100,0),(900,80,100,40),(100,80,0,40)]]
dock._redraw_gcp_marks()
dock._compute_transform()
iface.layer = building
dock._project_active_vector_layer()
iface.layer = gps
dock._project_active_point_layer()
plugin_module('config_io').save_config(str(out / 'section_config.json'), dock._build_config())
project.setFileName(str(out / 'demo.qgs'))
project.setFilePathStorage(Qgis.FilePathType.Relative)
project.viewSettings().setDefaultViewExtent(QgsReferencedRectangle(QgsRectangle(-10,-10,110,40), project.crs()))
assert project.write()
# Keep the example self-contained: its renderers are in the QGS already; no
# machine identity or generated default style database is needed.
tree = ET.parse(out / 'demo.qgs')
for key in ('saveUser', 'saveUserFull', 'saveDateTime'):
    tree.getroot().attrib.pop(key, None)
for style in tree.iter('ProjectStyleSettings'):
    style.attrib.pop('projectStyleId', None)
    style.attrib.pop('iccProfileId', None)
for author in tree.iter('author'):
    author.text = 'Section Georeferencer synthetic example'
tree.write(out / 'demo.qgs', encoding='utf-8', xml_declaration=True)
for generated in ('demo.qgs~', 'demo_attachments.zip'):
    (out / generated).unlink(missing_ok=True)
dock.resize(1440, 700)
dock.show()
app.processEvents()
dock.canvas.fit_to_image()
dock.reference_canvas.fit_to_reference()
dock.steps.setCurrentIndex(3)
app.processEvents()
assert dock.grab().save(str(ROOT / 'docs' / 'synthetic-demo.png'))
dock.cleanup()
dock.close()
print('Synthetic sample and screenshot written.')
