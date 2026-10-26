#!/usr/bin/env python3
"""Mide paleta y pliegues de Leo en las fotos locales de `avatar/` (LEO_SPEC.md F5 §9.1).

Se ejecuta en el equipo del usuario; requiere numpy y OpenCV (venv o paquetes del sistema:
`python3 -m pip install numpy opencv-python-headless` dentro de un venv, o `apt install
python3-opencv`). Las fotos se abren solo en lectura; la salida es `assets/leo/traits.json`
con números y colores hexadecimales, sin píxeles ni miniaturas. Los SHA256 de las fotos se
comprueban antes y después: si cambian, el script falla y no escribe nada.

Uso: python3 scripts/leo_traits.py [--photos avatar] [--output assets/leo/traits.json]
     [--set tongue=#2e2a3c] [--set ear_size=.9]   # ajustes manuales opcionales
"""
import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
from avatar_traits import DEFAULT_PALETTE, HEX, LIMITS  # noqa: E402

PHOTO_SUFFIXES = {'.jpg', '.jpeg', '.png'}
MAX_SIDE = 640  # Suficiente para color medio; acota memoria y tiempo de GrabCut.


def digests(photos):
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in photos}


def _hex(lab_pixel, cv2, np):
    bgr = cv2.cvtColor(np.uint8([[lab_pixel]]), cv2.COLOR_LAB2BGR)[0, 0]
    return '#%02x%02x%02x' % (int(bgr[2]), int(bgr[1]), int(bgr[0]))


def segment(image, cv2, np):
    """Máscara del perro con GrabCut, suponiendo que ocupa la zona central de la foto."""
    h, w = image.shape[:2]
    mask = np.zeros((h, w), np.uint8)
    rect = (int(w * .05), int(h * .05), int(w * .9), int(h * .9))
    cv2.grabCut(image, mask, rect, np.zeros((1, 65), np.float64), np.zeros((1, 65), np.float64), 5,
                cv2.GC_INIT_WITH_RECT)
    fg = np.isin(mask, (cv2.GC_FGD, cv2.GC_PR_FGD)).astype(np.uint8)
    fg = cv2.morphologyEx(fg, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))
    count, labels, stats, _ = cv2.connectedComponentsWithStats(fg)
    if count < 2:
        return fg.astype(bool)
    return labels == 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))


def measure(image, cv2, np):
    """Colores Lab por región y densidad de bordes del manto para una foto (BGR)."""
    scale = MAX_SIDE / max(image.shape[:2])
    if scale < 1:
        image = cv2.resize(image, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    mask = segment(image, cv2, np)
    if mask.sum() < .05 * mask.size:
        return None
    lab = cv2.cvtColor(image, cv2.COLOR_BGR2LAB)
    pixels = lab[mask].astype(np.float32)
    lightness, chroma = pixels[:, 0], np.hypot(pixels[:, 1] - 128, pixels[:, 2] - 128)
    # Manto cobre/crema: cálido y no oscuro. Hocico y nariz: oscuros y poco saturados.
    coat = (lightness > 90) & (chroma > 12)
    dark = (lightness < 85) & (chroma < 22)
    if coat.sum() < .2 * len(pixels):
        return None
    result = {'coverage': float(mask.mean())}
    coat_pixels, coat_l = pixels[coat], lightness[coat]
    for role, (low, high) in {'coat_deep': (5, 20), 'coat_mid': (40, 60), 'coat_light': (82, 96)}.items():
        band = (coat_l >= np.percentile(coat_l, low)) & (coat_l <= np.percentile(coat_l, high))
        result[role] = np.median(coat_pixels[band], axis=0).tolist()
    if dark.sum() > .015 * len(pixels):
        dark_pixels, dark_l = pixels[dark], lightness[dark]
        for role, (low, high) in {'nose': (0, 12), 'muzzle_dark': (15, 40),
                                  'muzzle_mid': (45, 70), 'muzzle_light': (78, 98)}.items():
            band = (dark_l >= np.percentile(dark_l, low)) & (dark_l <= np.percentile(dark_l, high))
            result[role] = np.median(dark_pixels[band], axis=0).tolist()
    # Pliegues: densidad de bordes dentro del manto, excluyendo el contorno de la silueta.
    gray = cv2.GaussianBlur(cv2.cvtColor(image, cv2.COLOR_BGR2GRAY), (3, 3), 0)
    edges = cv2.Canny(gray, 40, 110) > 0
    inner = cv2.erode(mask.astype(np.uint8), np.ones((9, 9), np.uint8)) > 0
    coat_mask = np.zeros(mask.shape, bool)
    coat_mask[mask] = coat
    region = inner & coat_mask
    result['fold_edges'] = float(edges[region].mean()) if region.any() else None
    return result


def summarize(measurements, cv2, np):
    """Mediana entre fotos; los roles sin datos conservan el color ilustrado actual."""
    palette, sources = {}, {}
    for role in DEFAULT_PALETTE:
        values = [m[role] for m in measurements if role in m]
        if values:
            palette[role] = _hex(np.clip(np.median(values, axis=0), 0, 255).round(), cv2, np)
            sources[role] = len(values)
    edges = [m['fold_edges'] for m in measurements if m.get('fold_edges') is not None]
    proportions = {}
    if edges:
        # Heurística acotada: más bordes internos -> pliegues más marcados en el dibujo.
        low, high, _ = LIMITS['fold_depth']
        proportions['fold_depth'] = round(min(high, max(low, .4 + 2.5 * float(np.median(edges)))), 3)
    return palette, proportions, sources, (float(np.median(edges)) if edges else None)


def parse_overrides(items):
    palette, proportions = {}, {}
    for item in items:
        key, _, value = item.partition('=')
        if key in DEFAULT_PALETTE and HEX.match(value):
            palette[key] = value.lower()
        elif key in LIMITS:
            low, high, _ = LIMITS[key]
            try:
                number = float(value)
            except ValueError:
                raise SystemExit(f'Valor no numérico para {key}: {value!r}')
            if not low <= number <= high:
                raise SystemExit(f'{key} debe estar entre {low} y {high}')
            proportions[key] = number
        else:
            raise SystemExit(f'Ajuste no reconocido: {item!r} (roles: {", ".join([*DEFAULT_PALETTE, *LIMITS])})')
    return palette, proportions


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--photos', type=Path, default=ROOT / 'avatar')
    parser.add_argument('--output', type=Path, default=ROOT / 'assets/leo/traits.json')
    parser.add_argument('--set', action='append', default=[], metavar='ROL=VALOR')
    args = parser.parse_args(argv)
    manual_palette, manual_proportions = parse_overrides(args.set)
    try:
        import cv2
        import numpy as np
    except ImportError:
        raise SystemExit('Faltan numpy/OpenCV: usar un venv con `pip install numpy opencv-python-headless` '
                         'o `sudo apt install python3-opencv` (decisión del usuario).')
    photos = sorted(p for p in args.photos.glob('*') if p.is_file() and p.suffix.lower() in PHOTO_SUFFIXES)
    if not photos:
        raise SystemExit(f'No hay fotos en {args.photos}')
    before = digests(photos)
    measurements, skipped = [], []
    for photo in photos:
        image = cv2.imread(str(photo), cv2.IMREAD_COLOR)
        result = measure(image, cv2, np) if image is not None else None
        (measurements.append(result) if result else skipped.append(photo.name))
    if digests(photos) != before:
        raise SystemExit('Los SHA256 de las fotos cambiaron durante el análisis; no se escribe nada.')
    if not measurements:
        raise SystemExit('No se pudo segmentar al perro en ninguna foto.')
    palette, proportions, sources, edges = summarize(measurements, cv2, np)
    palette.update(manual_palette)
    proportions.update(manual_proportions)
    traits = {
        'version': 1,
        'palette': palette,
        'proportions': proportions,
        'measurements': {'photos_used': len(measurements), 'photos_skipped': len(skipped),
                         'roles_measured': sources, 'fold_edge_density': edges,
                         'manual': sorted([*manual_palette, *manual_proportions])},
        'source_sha256': before,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(traits, indent=2, ensure_ascii=False) + '\n')
    print(f'{args.output}: {len(measurements)} fotos medidas, {len(skipped)} omitidas; '
          f'roles medidos: {", ".join(sources) or "ninguno"}. Originales sin cambios.')


if __name__ == '__main__':
    main()
