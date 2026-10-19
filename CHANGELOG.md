# Changelog

## Unreleased

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
