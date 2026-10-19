# Changelog

## 0.2.3 (2026-10-19)

### Changed

- Consolidated tests around data integrity and user workflows: 122 test methods
  reduced to 50, retaining regression checks for the eight publication-review
  findings. Removed cosmetic and duplicate checks; release checks now run in
  the ZIP smoke test.

### Fixed

- Canceling section-line drawing deactivates the tool even when no previous
  map tool was active.

## 0.2.2 (2026-10-19)

### Fixed

- GCP fitting, deletion and clearing invalidate both GPS and PLATEAU projected
  results, preventing old pixels from being exported with a new transform.
- Section GeoTIFFs use a local engineering CRS in metres. The original map CRS
  is stored separately as `SECTION_SOURCE_CRS`, never assigned to section axes.
  Non-metre project CRSs are refused to keep distance and height units consistent.
- Per-layer projection results use QGIS layer IDs. Same-named layers no longer
  overwrite each other; renaming and reprojecting a layer replaces its own rows.
- Clearing an image clears its GCPs and transform. Loading a config without an
  image clears the previous canvas instead of mixing two sessions.
- Nested config types, finite numbers and affine inverse consistency are
  validated before changing the session; invalid configs keep current work.
- Invalid source CRSs and coordinate-transform initialization failures stop
  projection rather than silently treating raw coordinates as project coordinates.
- GPS attributes with conflicting `src_` names are preserved with unique names;
  the original-to-exported mapping is recorded in the projection summary.
- Pending settings are checked again after applying changes that may clear the
  transform, before projection or package export proceeds.

### Added

- Regression tests for all eight review findings and adjacent failure paths.
- QGIS/GDAL CI that rejects skipped tests, deterministic ZIP packaging with a
  SHA256 checksum, and an isolated plugin-loader/export smoke test of the ZIP.
- A synthetic project, section image, GCP config, building/GPS layers and
  walkthrough, plus a screenshot generated from those invented inputs.
- Source layer/feature identity in GIS exports and source attribute mapping.

### Compatibility

- Minimum declared QGIS version is now 3.44, matching the locally verified
  baseline (macOS QGIS 3.44.5). QGIS 4 is excluded. Linux CI is configured;
  Windows and native Linux desktop operation remain unverified.

## 0.2.1 (2026-10-19)

### Changed

- Prepared for GitHub publication under GPL-2.0-or-later; `metadata.txt` now carries the
  author, homepage, repository, and issue tracker.
- A building picked in step 1 is drawn on the map with an orange footprint
  fill and a red outline, so it stands out from the layer style and from other
  buildings; it is hidden with the plugin window and removed by **Clear**.

## 0.2.0 (2026-10-19)

### Changed (UI/UX)

- The building comes first: step 1 picks the PLATEAU/CAD building with **Pick
  on map** (one click on the QGIS map, Shift/Cmd-click for more) from a layer
  list in the plugin, without the QGIS selection tool. Step 2 draws the section
  line through it, and the PLATEAU reference is projected automatically when
  the line is set, and again when the line, buffer, z offset, or height field
  changes. **Use QGIS selection** remains for pre-selected features, and the
  separate **Load selected** step is gone.

- The window is now a step sidebar (one step expanded at a time, the next one
  opened when a step is finished) beside full-height section image and PLATEAU
  reference panels, instead of short canvases above a long scrolling column.
  The window opens at up to 1280x820, limited to the screen, and shrinks to
  the sidebar at the screen's right edge while **Draw on map** is active so the
  always-on-top window does not cover the map.
- The hint line follows the step numbers (it used to ask for step 3 before
  step 2), and routine messages no longer go to the QGIS message bar; only
  warnings do. "Projected N selected features" is no longer shown as a warning.
- Distances are shown in metres with millimetre precision, pixels with one
  decimal, and section coordinates without float noise.
- The confirmation for changing the section line or a projection setting lists
  the old and new value and, as bullets, what will be cleared.
- The RMS badge and the residual column are coloured green/amber/red (0.2 m /
  1 m), and with exactly three GCPs the badge says that the fit is exact and a
  fourth GCP is needed to check it.
- Map orientation labels are in English like the rest of the interface.
- Empty canvases explain what to load, both panels show the coordinates under
  the pointer, the reference panel has s/z axis ticks, and a wait cursor is
  shown while projecting, loading, and exporting.

### Fixed (UI/UX)

- Reference snapping preferred the nearest point on an edge over the corner it
  runs into, so a click a few pixels off a corner slid the GCP target 1-3 m
  along the wall while the 4-point RMS stayed small. Corners now win, and the
  snap tolerance is 12 screen pixels at any zoom (it was 12 scene units, which
  changed with zoom).
- 'Add GCP' reused the previously picked pixel after a click outside the image
  or a second press, producing duplicate GCPs (a duplicated pixel made the
  transform fail). Each pick is now used once, shown as an orange mark on both
  panels until it is added, and 'Add GCP' is disabled until both are picked.
  Registered GCP targets are marked on the reference panel too.
- GCP markers, GPS points, and projected vertices were sized in image pixels
  and were invisible on a large scan at fit zoom (about 0.2 screen pixels on a
  600 dpi A3 sheet). They now have a fixed screen size, and the PNG export
  scales them to the image size.
- Thin drawing lines broke up or vanished when zoomed out. A downscaled image
  pyramid is drawn below 50% zoom.
- GPS projection ignored the feature selection that the README says it honours.

### Fixed

- PLATEAU/CAD segments bridged the last vertex of one part, face, or ring to
  the first vertex of the next. LOD1/LOD2 buildings (MultiPolygonZ of faces)
  gained edges that do not exist in the source, in the reference view, the snap
  targets, the overlay, and the exported segments. Multipoint layers no longer
  produce segments at all.
- A geographic project CRS such as EPSG:6697, which the README used to
  recommend, made section distance and buffer degrees. Defining the section
  line, loading a reference, projecting, and exporting are now refused until a
  projected CRS is set, and the README points to the JGD2011 Plane Rectangular
  CS zones instead.
- Changing the section line, z offset, buffer, or height field after results
  existed kept the old results, so an exported config described settings its
  rows were not made with. The plugin now asks before clearing the results that
  depend on the changed value, and keeps the old value if declined. A project
  CRS change after the line was defined blocks projection and export, loading
  a config clears the previous PLATEAU reference, and a config saved in another
  CRS is flagged on load. A config saved in a geographic CRS restores its image
  and settings but not its line and GCPs, which are in degrees. The GCP residual column no longer shows the previous
  transform's residuals after a config load.
- A drawn section line that failed (for example a zero-length double-click)
  left the map tool active, re-firing the same error on every later click.
- A typed **Height field** was ignored whenever the geometry had Z, so PointZ
  GPS data with z=0 used 0. For point layers the named field now takes
  precedence; line and polygon layers keep geometry Z so 3D buildings are not
  flattened, and use the field only for geometry without Z.
- `gps_projected.csv` kept only six fixed attributes, dropping observation
  times such as TRIMA `recordedat`; it now keeps every source attribute. Empty
  attributes were written as the string `NULL`, and dates as locale strings
  such as `Thu Oct 1 08:30:00 2026`; they are now empty cells and ISO 8601
  without any time-zone shift.
- `world_projected.geojson` carried its CRS only in a custom member, so GDAL and
  QGIS read projected coordinates as WGS84 (EPSG:4979). It now declares the
  project CRS in the GeoJSON `crs` member.
- A layer whose fields could not be read raised `TypeError` instead of being
  skipped, aborting the whole projection run.
- Left-dragging to pan the section image or the PLATEAU reference no longer
  overwrites the picked GCP pixel or target `s`/`z`; only a click that does not
  move the pointer picks a coordinate.
- Projecting the same layer twice appended a second copy of every record to the
  exported CSV/GeoJSON/GeoPackage. A repeated projection now replaces that
  layer's previous result, and `projection_summary.json` counts are rebuilt from
  the stored per-layer results so they always match the exported rows.
- Deleting a GCP below the 3-point minimum left the PLATEAU overlay drawn with
  the discarded transform.
- Loading a config without an image left the step titles, the workflow hints,
  and the reference overlay showing the previous session's state.
- Loading a config with a null or non-numeric RMS crashed the dock.
- Loading a different section image kept GCPs that belong to the old pixel
  space; the plugin now asks before discarding them. Clearing the image also
  clears the projected overlay it was carrying.
- Loading a PLATEAU reference with nothing inside the buffer left the reference
  canvas half-initialised; the previous reference is now kept instead.
- `section_config.json` inside an exported package no longer carries the
  absolute path of the section image on the exporting machine. It points at the
  packaged background image, with the original name in `image.source_name`.
- A package folder path with a trailing separator produced file-name prefixes
  that did not match the folder.
- Large PDFs are rendered at a reduced DPI (capped near 120 megapixels) instead
  of exhausting memory, and a failed full-resolution PNG export now reports why.

### Added

- CAD affine-deformation reporting in the GCP manager and saved/exported JSON,
  including axis scale, principal stretch difference, shear, rotation, area
  scale, and warnings above the recorded 20% stretch or 5-degree shear limits.
- A persistent map-canvas orientation guide after setting the section line:
  P0 is labeled as the section view's left end (`s=0`), P1 as its right end
  (`s=section length`), with a P0→P1 direction arrow between them.
- Per-GCP residual column in the GCP table, and a highlight ring on the section
  image for the selected row.
- A next-step hint line under the status bar.
- `projection_summary.json` records the GPS and PLATEAU source layer names.
- Unit tests for `config_io` and `package_exporter` alongside the existing
  transform tests; the whole suite runs with
  `python -m unittest discover -s tests` from the repository root.

## 0.1.0

- Initial research MVP.
