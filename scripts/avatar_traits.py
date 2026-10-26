"""Paleta y proporciones del avatar a partir de `assets/leo/traits.json` (LEO_SPEC.md F5 §9.1).

El JSON lo produce `scripts/leo_traits.py` en local a partir de las fotos; aquí solo se leen
números y colores validados. Sin archivo, los valores coinciden con el avatar ilustrado actual,
de modo que los SVG generados no cambian. Valores fuera de rango se acotan; nunca se confía en
texto libre del JSON.
"""
import json
import re
from pathlib import Path

TRAITS = Path(__file__).resolve().parent.parent / 'assets/leo/traits.json'
HEX = re.compile(r'^#[0-9a-fA-F]{6}$')

# Color definitivo actual de cada rol (tras el retoque de avatar_detail.finish).
DEFAULT_PALETTE = {
    'coat_light': '#edc18a', 'coat_mid': '#c58a4e', 'coat_deep': '#84512f',
    'muzzle_light': '#a18d6e', 'muzzle_mid': '#776c53', 'muzzle_dark': '#414238',
    'nose': '#34342c', 'tongue': '#66526c', 'eye': '#6b4e30',
}
# Proporción relativa al dibujo actual (1 = sin cambio) y opacidad de pliegues.
LIMITS = {'head_width': (.92, 1.12, 1.0), 'ear_size': (.75, 1.2, 1.0), 'fold_depth': (.4, .8, .58)}

# Colores de trabajo de los generadores -> rol. Un solo paso de sustitución evita encadenar
# reemplazos cuando un color medido coincide con otro marcador.
PLACEHOLDERS = {
    '#d59a58': 'coat_mid', '#f0c58d': 'coat_light', '#985a32': 'coat_deep',
    '#a48b67': 'muzzle_light', '#796449': 'muzzle_mid', '#493f31': 'muzzle_dark',
    '#a38a66': 'muzzle_light', '#776247': 'muzzle_mid', '#443b30': 'muzzle_dark',
    '#34342c': 'nose', '#59574b': 'nose', '#66526c': 'tongue', '#6b4e30': 'eye',
}
# Sin traits.json se conserva exactamente el resultado anterior.
LEGACY = {'#d59a58': '#c58a4e', '#f0c58d': '#edc18a', '#985a32': '#84512f',
          '#a48b67': '#a18d6e', '#796449': '#776c53', '#493f31': '#414238'}


def _clamp(value, low, high, default):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value != value:
        return default
    return min(high, max(low, float(value)))


def load(path=TRAITS):
    """Devuelve {'palette', 'proportions', 'measured'}; `measured` indica si hay traits válidos."""
    palette, proportions = dict(DEFAULT_PALETTE), {k: v[2] for k, v in LIMITS.items()}
    try:
        data = json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return {'palette': palette, 'proportions': proportions, 'measured': False}
    if not isinstance(data, dict) or data.get('version') != 1:
        return {'palette': palette, 'proportions': proportions, 'measured': False}
    measured = data.get('palette') if isinstance(data.get('palette'), dict) else {}
    for role in palette:
        value = measured.get(role)
        if isinstance(value, str) and HEX.match(value):
            palette[role] = value.lower()
    given = data.get('proportions') if isinstance(data.get('proportions'), dict) else {}
    for key, (low, high, default) in LIMITS.items():
        proportions[key] = _clamp(given.get(key), low, high, default)
    return {'palette': palette, 'proportions': proportions, 'measured': True}


def recolor(svg, traits):
    """Sustituye los colores de trabajo por la paleta final en una sola pasada."""
    if traits['measured']:
        table = {old: traits['palette'][role] for old, role in PLACEHOLDERS.items()}
    else:
        table = LEGACY
    return re.sub('|'.join(map(re.escape, table)), lambda m: table[m.group(0)], svg)
