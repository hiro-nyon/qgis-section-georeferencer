"""Build a deterministic installable ZIP, using only explicitly allowed files."""

import configparser
import hashlib
from pathlib import Path
import zipfile


ROOT = Path(__file__).resolve().parents[1]


def build(output_dir=None):
    metadata = configparser.ConfigParser()
    metadata.read(ROOT / "metadata.txt", encoding="utf-8")
    version = metadata["general"]["version"]
    destination = Path(output_dir) if output_dir else ROOT / "dist"
    destination.mkdir(parents=True, exist_ok=True)
    archive = destination / ("section_georeferencer-" + version + ".zip")
    files = list(ROOT.glob("*.py"))
    files += [ROOT / name for name in ("metadata.txt", "icon.svg", "LICENSE", "README.md", "CHANGELOG.md")]
    files += list((ROOT / "examples").glob("*.py"))
    files += [p for p in (ROOT / "examples" / "synthetic").rglob("*")
              if p.is_file() and p.suffix in (".png", ".qgs", ".json", ".geojson", ".md")]
    files += list((ROOT / "docs").glob("*.png"))
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as output:
        for path in sorted(files):
            member = "section_georeferencer/" + path.relative_to(ROOT).as_posix()
            info = zipfile.ZipInfo(member, date_time=(2026, 10, 19, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            output.writestr(info, path.read_bytes())
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    archive.with_suffix(".zip.sha256").write_text(digest + "  " + archive.name + "\n", encoding="utf-8")
    print(archive)
    print("SHA256 " + digest)
    return archive


if __name__ == "__main__":
    build()
