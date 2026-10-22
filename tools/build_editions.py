#!/usr/bin/env python3
"""Build Noctune editions from a complete source checkout without changing it."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import re
import tempfile
import tomllib
import zipfile


ROOT = Path(__file__).resolve().parents[1]
FREE_EXCLUDES = frozenset({
    "effects.py", "environment.py", "lasers.py", "network.py", "looks.py",
})
EDITION_DETAILS = {
    "free": (
        "Noctune Free",
        "Audio-reactive object materials, ready in a few clicks",
    ),
    "pro": (
        "Noctune Pro",
        "Audio-reactive materials, beams, lasers and environment effects",
    ),
}


def package_files(source_dir: Path, edition: str) -> dict[str, bytes]:
    """Return the complete extension payload, including its edition metadata."""
    if edition not in EDITION_DETAILS:
        raise ValueError(f"Unknown edition: {edition}")
    source_dir = source_dir.resolve()
    manifest_path = source_dir / "blender_manifest.toml"
    manifest = manifest_path.read_text(encoding="utf-8")
    metadata = tomllib.loads(manifest)
    if metadata.get("id") != "afterglow":
        raise ValueError("Expected the afterglow extension manifest")
    if not (source_dir / "edition.py").is_file():
        raise ValueError("Source is missing edition.py")
    if edition == "pro":
        missing = sorted(name for name in FREE_EXCLUDES if not (source_dir / name).is_file())
        if missing:
            raise ValueError(
                f"Pro environment sources are missing: {', '.join(missing)}. "
                "Build Noctune Pro from the private noctune-pro repository"
            )

    name, tagline = EDITION_DETAILS[edition]
    manifest, count = re.subn(r"(?m)^name\s*=.*$", f'name = "{name}"', manifest, count=1)
    if count != 1:
        raise ValueError("Manifest is missing its name")
    manifest, count = re.subn(r"(?m)^tagline\s*=.*$", f'tagline = "{tagline}"', manifest, count=1)
    if count != 1:
        raise ValueError("Manifest is missing its tagline")

    files = {}
    for path in sorted(source_dir.rglob("*")):
        relative = path.relative_to(source_dir)
        if not path.is_file() or any(part.startswith(".") or part == "__pycache__" for part in relative.parts):
            continue
        if path.suffix in {".pyc", ".pyo"}:
            continue
        key = relative.as_posix()
        if edition == "free" and key in FREE_EXCLUDES:
            continue
        files[key] = path.read_bytes()
    files["blender_manifest.toml"] = manifest.encode("utf-8")
    edition_source = files["edition.py"].decode("utf-8")
    for constant, value in (("IS_PRO", str(edition == "pro")), ("NAME", repr(name))):
        edition_source, count = re.subn(
            rf"(?m)^{constant}\s*=.*$", f"{constant} = {value}", edition_source, count=1,
        )
        if count != 1:
            raise ValueError(f"edition.py is missing {constant}")
    files["edition.py"] = edition_source.encode("utf-8")
    # Include the GPL text in both redistributable packages.
    if "LICENSE" not in files:
        files["LICENSE"] = (source_dir.parent / "LICENSE").read_bytes()
    return files


def build(edition: str, output_dir: Path, source_dir: Path = ROOT / "afterglow") -> Path:
    """Write a deterministic, root-layout Blender extension archive."""
    output_dir = output_dir.resolve()
    public_dist = ROOT / "dist"
    if edition == "pro" and (output_dir == public_dist or public_dist in output_dir.parents):
        raise ValueError("Keep Pro packages out of public dist; use build/private or another local directory")
    files = package_files(source_dir, edition)
    metadata = tomllib.loads(files["blender_manifest.toml"].decode("utf-8"))
    filename = f"noctune-{edition}-{metadata['version']}.zip"
    output_dir.mkdir(parents=True, exist_ok=True)
    destination = output_dir / filename
    # A temporary sibling keeps an interrupted build from replacing a good ZIP.
    handle, temporary_name = tempfile.mkstemp(prefix=f".{filename}.", dir=output_dir)
    os.close(handle)
    temporary = Path(temporary_name)
    try:
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
            for name, payload in sorted(files.items()):
                info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
                info.create_system = 3
                info.external_attr = 0o100644 << 16
                info.compress_type = zipfile.ZIP_DEFLATED
                archive.writestr(info, payload, compresslevel=9)
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)
    return destination


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--edition", choices=tuple(EDITION_DETAILS), default="free")
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    try:
        destination = build(args.edition, args.output_dir)
    except (ValueError, OSError, tomllib.TOMLDecodeError) as exc:
        parser.exit(1, f"Build failed: {exc}\n")
    print(destination)


if __name__ == "__main__":
    main()
