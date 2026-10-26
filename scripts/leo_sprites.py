#!/usr/bin/env python3
"""Sprites PNG locales a partir de la hoja elegida (LEO_SPEC F5.3; derivados solo en tu equipo, G7).

1. `--views`: recorta el fondo verde plano de `chosen.json`, separa cada vista del perro y las guarda en
   `assets/leo/generated/views/view-N.png` (de izquierda a derecha). Imprime solo tamaños.
2. `--pose sit=1 --pose curious-left=1:mirror …`: encaja una vista en el lienzo de la mascota (168 px, pies a
   158 px) y crea `assets/leo/generated/sprites/POSE.png` y `POSE@2x.png`. La extensión usa estos PNG en tu
   instalación local y recurre al SVG para cualquier pose que falte (por ejemplo, los 24 cuadros de marcha,
   que requieren el rig por recortes aún pendiente). Nunca se empaquetan ni se publican.
"""
import argparse
import json
from pathlib import Path

import gi
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf, GLib  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
SIZE, FEET = 168, 158
POSES = {"sit", "sleep", "alert", "blink", "happy", "curious-left", "curious-right", "watching", "point-left",
         "point-right", "celebrate", "reading", *(f"pet-{i}" for i in range(8))}


def rgba(pixbuf):
    if not pixbuf.get_has_alpha():
        pixbuf = pixbuf.add_alpha(False, 0, 0, 0)
    return pixbuf


def key_green(pixbuf):
    """Flat green background → transparent, with spill suppression on the dog's edges."""
    pixbuf = rgba(pixbuf)
    width, height, stride = pixbuf.get_width(), pixbuf.get_height(), pixbuf.get_rowstride()
    data = bytearray(pixbuf.get_pixels())
    for y in range(height):
        row = y * stride
        for x in range(row, row + width * 4, 4):
            r, g, b = data[x], data[x + 1], data[x + 2]
            if g > 90 and g > r * 1.45 and g > b * 1.3:
                data[x + 3] = 0
            elif g > r * 1.15 and g > b * 1.1:
                data[x + 1] = max(r, b)
                data[x + 3] = 180
    return GdkPixbuf.Pixbuf.new_from_bytes(GLib.Bytes.new(bytes(data)), GdkPixbuf.Colorspace.RGB, True, 8,
                                           width, height, stride)


def views(pixbuf, cell=4, minimum=.015):
    """Bounding boxes (x, y, w, h) of separate opaque regions, left to right, on a coarse grid."""
    width, height, stride = pixbuf.get_width(), pixbuf.get_height(), pixbuf.get_rowstride()
    data = pixbuf.get_pixels()
    cols, rows = width // cell, height // cell
    solid = [[data[(y * cell) * stride + (x * cell) * 4 + 3] > 128 for x in range(cols)] for y in range(rows)]
    seen, boxes = set(), []
    for y in range(rows):
        for x in range(cols):
            if not solid[y][x] or (x, y) in seen:
                continue
            stack, cells = [(x, y)], []
            seen.add((x, y))
            while stack:
                cx, cy = stack.pop()
                cells.append((cx, cy))
                for nx, ny in ((cx + 1, cy), (cx - 1, cy), (cx, cy + 1), (cx, cy - 1)):
                    if 0 <= nx < cols and 0 <= ny < rows and solid[ny][nx] and (nx, ny) not in seen:
                        seen.add((nx, ny))
                        stack.append((nx, ny))
            if len(cells) >= minimum * cols * rows:
                xs, ys = [c[0] for c in cells], [c[1] for c in cells]
                boxes.append((min(xs) * cell, min(ys) * cell, (max(xs) - min(xs) + 1) * cell, (max(ys) - min(ys) + 1) * cell))
    return sorted(boxes)


def fit(view, scale=1, mirror=False):
    """The view on the mascot canvas: bottom on the feet line, centred, aspect kept."""
    size, feet = SIZE * scale, FEET * scale
    if mirror:
        view = view.flip(True)
    factor = min((feet - 10 * scale) / view.get_height(), (size - 4 * scale) / view.get_width())
    width, height = max(1, round(view.get_width() * factor)), max(1, round(view.get_height() * factor))
    scaled = view.scale_simple(width, height, GdkPixbuf.InterpType.HYPER)
    canvas = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, True, 8, size, size)
    canvas.fill(0x00000000)
    scaled.composite(canvas, (size - width) // 2, feet - height, width, height, (size - width) // 2, feet - height,
                     1, 1, GdkPixbuf.InterpType.NEAREST, 255)
    return canvas


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--generated", type=Path, default=ROOT / "assets/leo/generated")
    parser.add_argument("--views", action="store_true")
    parser.add_argument("--pose", action="append", default=[], metavar="POSE=VISTA[:mirror]")
    args = parser.parse_args(argv)
    if args.views:
        chosen = json.loads((args.generated / "chosen.json").read_text())["file"]
        keyed = key_green(GdkPixbuf.Pixbuf.new_from_file(str(args.generated / "candidates" / Path(chosen).name)))
        found = views(keyed)
        out = args.generated / "views"
        out.mkdir(parents=True, exist_ok=True)
        for old in out.glob("view-*.png"):
            old.unlink()
        for index, (x, y, w, h) in enumerate(found):
            keyed.new_subpixbuf(x, y, w, h).copy().savev(str(out / f"view-{index}.png"), "png", [], [])
        print(json.dumps({"views": [{"view": i, "width": w, "height": h} for i, (_, _, w, h) in enumerate(found)]}))
    sprites = args.generated / "sprites"
    for item in args.pose:
        pose, _, spec = item.partition("=")
        index, _, flag = spec.partition(":")
        if pose not in POSES or not index.isdigit() or flag not in ("", "mirror"):
            parser.error(f"Pose inválida: {item} (poses: {', '.join(sorted(POSES))})")
        view = GdkPixbuf.Pixbuf.new_from_file(str(args.generated / "views" / f"view-{index}.png"))
        sprites.mkdir(parents=True, exist_ok=True)
        fit(view, 1, flag == "mirror").savev(str(sprites / f"{pose}.png"), "png", [], [])
        fit(view, 2, flag == "mirror").savev(str(sprites / f"{pose}@2x.png"), "png", [], [])
        print(f"{pose}: {SIZE}px y {SIZE * 2}px")
    if args.pose:
        print("Instálalos en tu equipo con: python3 scripts/install.py (no se empaquetan ni se publican)")


if __name__ == "__main__":
    main()
