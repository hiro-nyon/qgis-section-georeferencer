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

1. Install and enable the plugin.
2. Load PLATEAU, GPS, or CAD-derived vector layers into QGIS.
3. Open **Plugins > Section Georeferencer**.
4. Define the section line from manual coordinates or a selected line feature.
5. Select the target PLATEAU building feature and click **Load Selected
   PLATEAU Reference**. The reference section appears in the dock.
6. Load a scanned/paper CAD section drawing image that was already converted
   from PDF or scan data to PNG, JPEG, or TIFF.
7. For each GCP, click the CAD image to pick source pixel `(u, v)`, then click
   the PLATEAU reference section to fill target `(s, z)`.
8. Add at least 3 GCPs and click **Compute Transform**.
9. Project an active GPS point layer or active PLATEAU/CAD vector layer.
   If one PLATEAU building feature is selected in the active layer, only that
   selected building is projected. If no feature is selected, the active vector
   layer is processed.
10. Export the overlaid section as PNG, or export a reproducible section
   analysis package.
11. Save or load the JSON configuration as needed.

## Paper CAD / PLATEAU / GPS Handoff Model

The intended handoff is:

1. In QGIS, prepare the project CRS, for example EPSG:6697.
2. Load the scanned CAD section drawing as the section background image.
3. Define a section line in the QGIS project CRS.
4. Select the relevant PLATEAU building feature and load it as the reference
   section in the plugin.
5. Use GCPs to fit the CAD image pixel space to PLATEAU-derived section
   coordinates `(s, z)`: click the CAD image for `(u, v)` and the PLATEAU
   reference view for `(s, z)`.
6. Project PLATEAU/CAD and GPS layers.
7. Export **Section Analysis Package**.
8. In any normal Python environment, use the exported background image plus
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

Image paths are stored relative to the config file when possible. Missing image
paths produce a warning while preserving the rest of the loaded configuration.

## Section Analysis Package Export

Use **Export Section Analysis Package** to write a folder that can be validated
outside QGIS. The package contains:

- `section_background.png`: the original section image without GPS overlay
- `section_preview.png`: section image with current projected overlays
- `section_config.json`: CRS, section line, buffer, z offset, image size, GCPs,
  affine transforms, RMS residual, source layer names, and export timestamp
- `gps_projected.csv`: every processed GPS point with section coordinates,
  pixel coordinates, raw z, z source, layer name, and `inside_buffer`
- `plateau_projected.geojson`: projected PLATEAU/CAD vertices and simple
  segments in section coordinates
- `projection_summary.json`: processing counts and warnings
- `gcp_residuals.csv`: written when transform residuals are available

GPS CSV export intentionally includes points outside the section buffer with
`inside_buffer=false`. This allows vertical stratification to be checked
reproducibly in an external Python environment instead of relying only on a PNG.

For PLATEAU/CAD, select the target building feature in QGIS before clicking the
projection button if the section should use one building only. Multiple selected
features are also supported.

## External Python Validation

An example QGIS-free validation script is included:

```bash
python section_georeferencer/examples/validate_section_package.py /path/to/exported_package
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
- Section drawing PDFs must be converted to image files before loading.
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

- Direct PDF loading
- TPS or polynomial transforms
- Strict 3D mesh section intersection
- SVG/PDF export
- BEMS/CAD zone support
- Integration with the QGIS elevation profile panel

## Transform Tests

The pure transform logic does not import QGIS and can be tested with:

```bash
python -m unittest section_georeferencer.tests.test_transforms
```

The QGIS UI and layer projection modules must be exercised inside QGIS because
they depend on PyQGIS and Qt.
