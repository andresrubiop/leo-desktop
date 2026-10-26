#!/usr/bin/env python3
"""Anclas del tercer ojo de Leo (petición del usuario, 2026-10-23): dónde está su frente en cada pose y cuadro.

Mientras Leo observa, la extensión le abre un tercer ojo en la frente (al estilo del Doctor Strange). Aquí se
calcula, solo con números, el centro de la frente y su inclinación en el lienzo de 320 px de cada sprite ilustrado:
se genera el SVG en memoria con el mismo código que los sprites instalados y se componen las transformaciones de la
cabeza (inclinaciones de cada pose, balanceo de la marcha, espejo de point-left). No lee `avatar/` ni escribe sprites.
Salida: assets/leo/third-eye.json → {pose: {"x", "y", "angle", "profile"}}.
"""
import json
import math
from pathlib import Path
import re
import sys
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

POSES = ("sit", "sleep", "alert", "blink", "happy", "curious-left", "curious-right", "watching", "point-left",
         "point-right", "celebrate", "reading", *(f"pet-{i}" for i in range(8)))
# Head-local points (before the head transform): centre of the forehead and a point 16 units "up" along it.
FRONT = ((155, 76), (155, 60))  # Between the two vertical folds above the brows (front view).
# Profile (walking, facing right): the forehead slope ahead of the skull, parallel to the front of the face.
SLOPE = (-12 / math.hypot(12, 40), -40 / math.hypot(12, 40))
PROFILE = ((266, 100), (266 + 16 * SLOPE[0], 100 + 16 * SLOPE[1]))


def multiply(a, b):
    """3×3 affine matrices as (a, b, c, d, e, f), SVG order: [a c e; b d f; 0 0 1]."""
    return (a[0] * b[0] + a[2] * b[1], a[1] * b[0] + a[3] * b[1], a[0] * b[2] + a[2] * b[3],
            a[1] * b[2] + a[3] * b[3], a[0] * b[4] + a[2] * b[5] + a[4], a[1] * b[4] + a[3] * b[5] + a[5])


IDENTITY = (1, 0, 0, 1, 0, 0)


def parse(transform):
    matrix = IDENTITY
    for name, args in re.findall(r"(\w+)\(([^)]*)\)", transform or ""):
        values = [float(v) for v in re.split(r"[\s,]+", args.strip()) if v]
        if name == "matrix":
            step = tuple(values)
        elif name == "translate":
            step = (1, 0, 0, 1, values[0], values[1] if len(values) > 1 else 0)
        elif name == "scale":
            step = (values[0], 0, 0, values[1] if len(values) > 1 else values[0], 0, 0)
        elif name == "rotate":
            angle = math.radians(values[0])
            cx, cy = (values[1], values[2]) if len(values) == 3 else (0, 0)
            cos, sin = math.cos(angle), math.sin(angle)
            step = multiply(multiply((1, 0, 0, 1, cx, cy), (cos, sin, -sin, cos, 0, 0)), (1, 0, 0, 1, -cx, -cy))
        else:
            raise ValueError(f"Transformación no soportada: {name}")
        matrix = multiply(matrix, step)
    return matrix


def apply(matrix, point):
    return (matrix[0] * point[0] + matrix[2] * point[1] + matrix[4], matrix[1] * point[0] + matrix[3] * point[1] + matrix[5])


def head(svg, walking):
    """Full transform of the head group: ancestors plus its own transform."""
    def visit(element, matrix):
        matrix = multiply(matrix, parse(element.get("transform")))
        own = element.get("transform", "")
        if element.tag.endswith("}g") and (("scale(1.045" in own and "rotate(" in own) if walking
                                          else re.search(r"translate\(-1[04] (34|110)\)", own)):
            return matrix
        for child in element:
            found = visit(child, matrix)
            if found:
                return found
        return None
    found = visit(ET.fromstring(svg), IDENTITY)
    if not found:
        raise RuntimeError("No se encontró el grupo de la cabeza")
    return found


def anchor(svg, walking=False):
    matrix = head(svg, walking)
    centre, up = PROFILE if walking else FRONT
    x, y = apply(matrix, centre)
    ux, uy = apply(matrix, up)
    # Angle of the eye's vertical axis: 0 = upright; positive = clockwise on screen (y grows downwards).
    angle = math.degrees(math.atan2(ux - x, -(uy - y)))
    return {"x": round(x, 2), "y": round(y, 2), "angle": round(angle, 2), "profile": walking}


def anchors():
    from avatar_svg import sprite, walking_sprite
    result = {pose: anchor(sprite(pose)) for pose in POSES}
    result.update({f"walk-{frame}": anchor(walking_sprite(frame), walking=True) for frame in range(24)})
    return result


def main():
    output = ROOT / "assets/leo/third-eye.json"
    output.write_text(json.dumps(anchors(), indent=1) + "\n")
    print(f"Anclas del tercer ojo: {output}")


if __name__ == "__main__":
    main()
