#!/usr/bin/env python3
"""Genera únicamente el avatar ilustrado. Nunca escribe en avatar/."""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


from avatar_svg import sprite, walking_sprite
from avatar_traits import load


def build():
    source = ROOT / "avatar"
    output = ROOT / "assets/leo"
    output.mkdir(parents=True, exist_ok=True)
    photos = sorted(p for p in source.glob("*") if p.is_file() and p.suffix.lower() in {".jpg", ".jpeg", ".png"})
    # traits.json (scripts/leo_traits.py) replaces the estimated palette and proportions.
    traits = load()
    provisional = not photos and not traits["measured"]
    for pose in ("sit", "sleep", "alert", "blink", "happy", "curious-left", "curious-right", "watching", "point-left", "point-right", "celebrate", "reading",
                 *(f"pet-{i}" for i in range(8))):
        (output / f"{pose}.svg").write_text(sprite(pose, provisional, traits))
    for frame in range(24):
        (output / f"walk-{frame}.svg").write_text(walking_sprite(frame, provisional, traits))
    manifest = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in photos}
    (output / "source-manifest.json").write_text(json.dumps(manifest, indent=2))
    # Retire only our derived portrait; originals are never modified.
    (output / "portrait.png").unlink(missing_ok=True)
    origin = "paleta medida en traits.json" if traits["measured"] else "paleta ilustrada estimada"
    print(f"Recursos locales listos ({origin}): {len(photos)} originales leídos, ninguno modificado")


if __name__ == "__main__":
    build()
