# Synthetic walkthrough

All images, geometries, and points here are invented test data, distributed
under the plugin's GPL-2.0-or-later license. They are not a real building,
PLATEAU product, GPS observations, or evidence about people or floor occupancy.

1. Open `demo.qgs` in QGIS 3.44.x. It loads the two GeoJSON layers and sets
   EPSG:6677. Coordinates are deliberately near the CRS origin.
2. Enable and open **Section Georeferencer**.
3. Use **Load config** to open `section_config.json` in this folder. The config
   loads the image, section line and four GCPs. No previous results are loaded.
4. Activate `synthetic_building` and use **Use QGIS selection** after selecting
   its single feature (or select the layer in step 1 and pick it on the map).
5. In step 5, project the building with **PLATEAU/CAD**, then activate
   `synthetic_gps` and project it with **GPS**. Clear any feature selection in
   the GPS layer to process all seven points.
6. Export a PNG or **Section Analysis Package**.

Expected: section line `(0,0)` to `(100,0)`, buffer 10 m, z offset 0 m, four
GCPs with near-zero fitting residuals, seven exported GPS rows, six inside the
buffer and one outside (y=30). The outside point stays in the CSV but is not
drawn in the plugin. Buildings are projected faces, not mesh-plane intersections.
The fit is intentionally exact; this is a software test, not external validation.

To try calibration from scratch, clear GCPs and pick these corners:

| Image u | Image v | Section s | Height z |
|---:|---:|---:|---:|
| 100 | 400 | 0 | 0 |
| 900 | 400 | 100 | 0 |
| 900 | 80 | 100 | 40 |
| 100 | 80 | 0 | 40 |

Regenerate the sample and screenshot from a development checkout by running
`scripts/create_demo.py` with QGIS's Python. The installed ZIP already contains
the sample; no additional Python packages are needed to open it in QGIS.
