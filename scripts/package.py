#!/usr/bin/env python3
"""Paquetes reproducibles en contenido, sin fotos ni configuraciones privadas."""
from pathlib import Path
import sys
import tarfile
import zipfile

ROOT = Path(__file__).resolve().parent.parent


def version():
    import tomllib
    return tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]


def main():
    # SVG coloured from traits.json derive from the photos: publishing them is gate G7 (LEO_SPEC.md).
    if (ROOT / "assets/leo/traits.json").exists() and "--allow-traits" not in sys.argv[1:]:
        raise SystemExit("assets/leo/traits.json existe: los SVG pueden derivar de las fotos. "
                         "Empaquetar solo con aprobación G7 y --allow-traits.")
    dist = ROOT / "dist"
    dist.mkdir(exist_ok=True)
    with zipfile.ZipFile(dist / "leo@local.desktop.shell-extension.zip", "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted((ROOT / "extension").glob("*")):
            if path.is_file():
                archive.write(path, path.name)
        for path in [*sorted((ROOT / "assets/leo").glob("*.svg")), ROOT / "assets/leo/third-eye.json"]:
            archive.write(path, "assets/" + path.name)
    with tarfile.open(dist / f"leo-desktop-{version()}.tar.gz", "w:gz") as archive:
        for folder in ("leo", "scripts", "extension", "tests", "docs", "assets/leo"):
            for path in sorted((ROOT / folder).rglob("*")):
                if path.is_file() and path.suffix in {".py", ".sh", ".md", ".js", ".mjs", ".css", ".svg", ".jsonl"} and "__pycache__" not in path.parts:
                    archive.add(path, arcname="leo-desktop/" + str(path.relative_to(ROOT)))
        for name in ("extension/metadata.json", "assets/leo/third-eye.json", "README.md", "AGENTS.md", "LEO_AGENTS.md", "pyproject.toml", ".gitignore"):
            archive.add(ROOT / name, arcname="leo-desktop/" + name)
    print("Paquetes creados en dist/. No incluyen avatar/, retrato ni manifiesto de fotos.")


if __name__ == "__main__":
    main()
