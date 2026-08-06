# Changelog

## Unreleased

### Fixed

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
