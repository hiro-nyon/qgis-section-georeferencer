# Section Georeferencer

Section Georeferencer is a research MVP QGIS 3.28+ plugin for georeferencing
architectural section drawing images and overlaying projected 3D geospatial
data on top of the section.

This is not a normal map georeferencer. A normal map georeferencer links image
pixels to map coordinates such as x/y. This plugin links image pixels to local
section coordinates:

- `s`: distance along a user-defined section line
- `z`: height in the section drawing

The plugin then projects world coordinates `(x, y, z)` from GPS, CAD, or
PLATEAU-derived vector layers into `(s, z)` and draws the result over the
section image.

## Installation

Copy or symlink the `section_georeferencer` directory into your QGIS Python
plugin directory.

Typical plugin directories are:

- macOS: `~/Library/Application Support/QGIS/QGIS3/profiles/default/python/plugins/`
- Windows: `%APPDATA%\QGIS\QGIS3\profiles\default\python\plugins\`
- Linux: `~/.local/share/QGIS/QGIS3/profiles/default/python/plugins/`

Restart QGIS, then enable **Section Georeferencer** in the QGIS Plugin Manager.
The plugin adds a toolbar/menu action named **Section Georeferencer**.

## Basic Workflow

The dock walks through six numbered steps. Each step title shows `✓` and a short
summary once it is satisfied, and a hint line under the status bar always names
the next action.

1. Install and enable the plugin.
2. Load PLATEAU, GPS, or CAD-derived vector layers into QGIS.
3. Open **Plugins > Section Georeferencer**.
4. **1. Section line** — define the line with **Draw on map** (two clicks on the
   QGIS map canvas), **Manual** (the coordinate fields), or **From line** (a
   selected line feature). P0 is the left end (`s=0`) and P1 is the right end
   (`s=section length`) of the section-coordinate view. The map keeps these
   endpoint labels and a P0→P1 arrow visible so the orientation can be checked.
5. **2. PLATEAU reference** — select the target PLATEAU building feature in the
   active QGIS layer and click **Load selected**. The reference section appears
   in the right-hand panel.
6. **3. Section image** — load the scanned/paper CAD section drawing
   (PNG, JPEG, TIFF, or PDF).
7. **4. GCP manager** — for each GCP, click the CAD image to pick source pixel
   `(u, v)`, then click the PLATEAU reference to fill target `(s, z)`, then
   click **Add GCP**. The reference view snaps to projected vertices, segment
   endpoints, and nearest points on projected segments. Left-dragging either
   panel pans it; only a click that does not move picks a coordinate.
8. The affine transform is computed automatically as soon as 3 GCPs exist, and
   again after every add or delete. **Compute transform** recomputes on demand.
   The table shows the per-GCP residual so a mis-picked point is easy to spot,
   and selecting a row rings that GCP on the image.
9. **5. Layer projection** — project the active GPS point layer or the active
   PLATEAU/CAD vector layer. If features are selected in the active layer, only
   those are projected; otherwise the whole layer is processed. Projecting the
   same layer again **replaces** its previous result rather than appending a
   duplicate copy.
10. **6. Export** — export the overlaid section as PNG, or export a reproducible
   section analysis package.
11. Save or load the JSON configuration as needed.

## Paper CAD / PLATEAU / GPS Handoff Model

The intended handoff is:

1. In QGIS, prepare the project CRS, for example EPSG:6697.
2. Load the PLATEAU layer into QGIS with normal QGIS tools.
3. Select the relevant PLATEAU building feature in QGIS.
4. Define a section line in the QGIS XY map canvas by clicking two points, or
   use manual/selected-line input. The map drawing tool shows a crosshair
   marker, previews a dashed line from P0 to the cursor, and follows QGIS
   snapping settings when snapping is enabled. After the second point, a
   persistent P0→P1 arrow and color-coded endpoint labels show that P0 becomes
   the section view's left end and P1 becomes its right end.
5. Load the selected PLATEAU building as the reference
   section in the plugin.
6. Load the scanned CAD section drawing as the section background image.
7. Use GCPs to fit the CAD image pixel space to PLATEAU-derived section
   coordinates `(s, z)`: click the CAD image for `(u, v)` and click/snapped-pick
   the PLATEAU reference view for `(s, z)`.
8. Project PLATEAU/CAD and GPS layers.
9. Export **Section Analysis Package**.
10. In any normal Python environment, use the exported background image plus
   `gps_projected.csv`, `plateau_projected.geojson`, and `section_config.json`
   to redraw or statistically validate the overlay.

The external Python side does not need QGIS. It can either plot the exported
`u`/`v` columns directly or use the transform metadata in `section_config.json`
to project new `(x, y, z)` records that are already in the same CRS as the
exported config.

## CRS Assumptions

The QGIS project CRS is used as the section coordinate CRS. Manual section
coordinates are assumed to already be in the project CRS.

When a source layer CRS differs from the project CRS, the plugin transforms
feature x/y coordinates into the project CRS before computing section
coordinates.

Use a projected metric CRS whenever possible. If the project CRS is geographic
or uses non-meter units, section length and buffer width may not be metric. The
plugin warns about this but does not crash.

For workflows where GPS and PLATEAU layers are prepared in EPSG:6697, set the
QGIS project CRS to EPSG:6697 before defining the section line and exporting the
package. The exported `gps_projected.csv` stores `x`/`y` after transformation
into the QGIS project CRS, and also stores ready-to-plot section-image pixel
coordinates `u`/`v`. External Python validation normally uses `u`/`v` directly
to draw on `section_background.png`; it does not need QGIS or PyQGIS.

## Height Handling

Vertical CRS and height datum alignment are intentionally simple in this MVP:

```text
z_section = z - z_offset
```

Set `z_offset` in the dock if the source data height datum needs to be shifted
to match the section drawing. The user is responsible for height datum
alignment.

For z extraction, geometry Z is preferred. If geometry Z is missing, the plugin
uses the user-specified z field, then common fields such as `z`, `height`,
`altitude`, `elevation`, `measuredHeight`, and `bldg_measuredHeight`.

## Config JSON

The plugin can save and load a JSON config containing:

- plugin/version metadata
- project CRS authid
- section line and length
- image path
- z offset
- buffer width
- z field
- GCPs
- affine transform coefficients and RMS residual
- CAD deformation metrics: horizontal/vertical scale, principal stretch
  difference, shear, rotation, area scale, warning thresholds, and warnings

The GCP manager displays these deformation metrics after fitting. A warning is
shown when the maximum-to-minimum directional stretch differs by more than 20%
or when affine shear moves the image axes more than 5 degrees away from a right
angle. Rotation and uniform scaling are reported but do not trigger warnings.

Image paths are stored relative to the config file when possible. Missing image
paths produce a warning while preserving the rest of the loaded configuration.

## Section Analysis Package Export

Use **Export Section Analysis Package** to create a named package folder that
can be validated outside QGIS. Files inside the folder use the package name as
a prefix, and the exact file names are recorded in `package_files` in the
exported config, so consumers should read that mapping instead of hardcoding
names. With the default legacy names, the package contains:

- `section_background.png`: the original section image without GPS overlay
- `section_background_georeferenced.tif`: GeoTIFF version of the section
  background, georeferenced in section coordinates
- `section_preview.png`: section image with current projected overlays
- `section_config.json`: CRS, section line, buffer, z offset, image size, GCPs,
  affine transforms, RMS residual, source layer names, and export timestamp
- `gps_projected.csv`: every processed GPS point with section coordinates,
  pixel coordinates, raw z, z source, layer name, and `inside_buffer`
- `plateau_projected.geojson`: projected PLATEAU/CAD vertices and simple
  segments in section coordinates `(s, z_section)`, with pixel `u`/`v` kept as
  feature properties
- `world_projected.geojson`: the same GPS and PLATEAU records in project-CRS
  world coordinates `[x, y, z_raw]`, for loading straight into QGIS or geopandas
- `section_projected.gpkg`: GeoPackage with `gps`, `plateau`, and
  `plateau_segments` layers in world coordinates (written only when the GDAL
  Python bindings are available)
- `projection_summary.json`: per-source-layer processing counts and warnings
- `gcp_residuals.csv`: written when transform residuals are available

`section_config.json` inside the package is not byte-identical to a config saved
with **Save config**: its `image.path` points at the packaged background image
and the original file name is kept in `image.source_name`, so the package stays
self-contained and carries no absolute path from the exporting machine.

The GeoTIFF uses the fitted affine transform as a raster GeoTransform:

```text
GeoTIFF X = s_along_section
GeoTIFF Y = z_section
```

This is useful for external Python/GDAL workflows that want a georeferenced
section raster. It is not a normal plan-view map raster. Even if the QGIS
project CRS is EPSG:4979 or EPSG:6697, the GeoTIFF grid axes are still section
axes `(s, z_section)`, not longitude/latitude or map x/y. Use
`section_config.json` for the section line `p0/p1`, height offset, and affine
metadata needed to reconstruct the relationship to the original 3D project
coordinates.

GPS CSV export intentionally includes points outside the section buffer with
`inside_buffer=false`. This allows vertical stratification to be checked
reproducibly in an external Python environment instead of relying only on a PNG.

For PLATEAU/CAD, select the target building feature in QGIS before clicking the
projection button if the section should use one building only. Multiple selected
features are also supported.

## External Python Validation

An example QGIS-free validation script is included:

```bash
python examples/validate_section_package.py /path/to/exported_package
```

It uses `pandas`, `numpy`, `matplotlib`, `pillow`, and `json` to load the
package, replot GPS points and PLATEAU/CAD projected vertices/segments over the
section background, plot a `z_section` histogram, and export:

- `validation_overlay.png`
- `validation_z_histogram.png`
- `validation_floor_counts.csv`

If `floor_reference.csv` exists in the package folder, the script assigns each
GPS point to the nearest floor center. If scikit-learn is installed, it also
writes optional Gaussian mixture z-cluster centers.

## Limitations

- Only affine pixel-to-section transforms are implemented.
- PDF loading depends on the GDAL PDF driver (Poppler or PDFium) shipped with
  QGIS. The first page is rendered at up to 600 DPI; large sheets are rendered
  at a reduced DPI so the raster stays under roughly 120 megapixels.
- Strict 3D mesh-plane intersection is not implemented.
- Vector projection uses vertices within the section buffer.
- 2D polygon layers with a height attribute are drawn as a simple top-outline
  approximation.
- Height datum alignment must be handled by the user through `z_offset`.
- Buffer width assumes a metric projected CRS.
- Styling is intentionally simple for MVP output figures.
- PLATEAU/CAD package export stores projected vertices and simple segment
  features; it does not export exact mesh-section intersection curves.

## Future Work

- Multi-page PDF selection
- TPS or polynomial transforms
- Strict 3D mesh section intersection
- SVG/PDF export
- BEMS/CAD zone support
- Integration with the QGIS elevation profile panel

## Tests

`transforms.py`, `config_io.py`, and `package_exporter.py` do not import QGIS,
so they run under a plain Python 3 interpreter. From the repository root:

```bash
python -m unittest discover -s tests
```

`section_georeferencer_dock.py`, `section_canvas.py`, and `layer_projector.py`
import PyQGIS and Qt, so they must be exercised inside QGIS.

## License

Not yet chosen. Note that a QGIS plugin links against PyQGIS, which is
GPL-licensed, so distributing this plugin normally requires GPL-2.0-or-later.
Add a `LICENSE` file and fill in `metadata.txt` before publishing.

## Status

Research MVP, marked `experimental=True` in `metadata.txt`. Interfaces and the
package format may still change.
