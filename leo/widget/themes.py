"""Color themes. "ubuntu-*" use the exact palette of Ubuntu's terminal (Ptyxis)."""

from __future__ import annotations

COLOR_INDEX = {
    "black": 0, "red": 1, "green": 2, "yellow": 3, "blue": 4, "magenta": 5, "cyan": 6,
    "white": 7, "dim": 8, "bred": 9, "bgreen": 10, "byellow": 11, "bblue": 12,
    "bmagenta": 13, "bcyan": 14, "bwhite": 15,
}

THEMES = {
    "ubuntu-dark": {
        "bg": "#300A24", "fg": "#FFFFFF", "accent": "#E95420",
        "header_bg": "#260819", "header_fg": "#EADCE5", "border": "#4C1D3D",
        "palette": ["#1B1B1B", "#CC1A12", "#4E9A06", "#C4A000", "#3667A6", "#7F5985",
                    "#06989A", "#D5D5D5", "#838383", "#F93632", "#8AE234", "#FCE94F",
                    "#729FCF", "#AD7FA8", "#34E2E2", "#EEEEEC"],
    },
    "ubuntu-light": {
        "bg": "#F8F8F8", "fg": "#121212", "accent": "#C7400F",
        "header_bg": "#EBEBEB", "header_fg": "#2E2E2E", "border": "#D0D0D0",
        "palette": ["#F2F2F2", "#F93B2E", "#3F7E04", "#A88F00", "#5B91D7", "#A77DAD",
                    "#047B7D", "#1B1B1B", "#707070", "#CE191C", "#26420C", "#80721B",
                    "#395E86", "#80577C", "#064141", "#212121"],
        # on a light background the "bright" colors are the dark ones; keep bars vivid
        "aliases": {"bgreen": "green", "byellow": "yellow", "bred": "red", "bcyan": "cyan"},
    },
    "tango": {
        "bg": "#2E3436", "fg": "#D3D7CF", "accent": "#FCAF3E",
        "header_bg": "#25292B", "header_fg": "#D3D7CF", "border": "#3E4446",
        "palette": ["#2E3436", "#CC0000", "#4E9A06", "#C4A000", "#3465A4", "#75507B",
                    "#06989A", "#D3D7CF", "#7F8582", "#EF2929", "#8AE234", "#FCE94F",
                    "#729FCF", "#AD7FA8", "#34E2E2", "#EEEEEC"],
    },
    "matrix": {
        "bg": "#050A05", "fg": "#5CFF7A", "accent": "#B6FFC4",
        "header_bg": "#020502", "header_fg": "#5CFF7A", "border": "#0F3A17",
        "palette": ["#050A05", "#FF5555", "#22B33A", "#9BE85A", "#2BAA55", "#3FD06A",
                    "#5CFF7A", "#9CFFB0", "#2E7A3C", "#FF7777", "#5CFF7A", "#D4FF6A",
                    "#5CFF7A", "#7DFF96", "#A6FFB8", "#D8FFE0"],
    },
}


def resolve(name: str, prefer_dark: bool = True) -> dict:
    if name == "ubuntu":
        name = "ubuntu-dark" if prefer_dark else "ubuntu-light"
    return THEMES.get(name, THEMES["ubuntu-dark"])


def color(theme: dict, style: str) -> str:
    if style == "fg":
        return theme["fg"]
    if style == "accent":
        return theme["accent"]
    style = theme.get("aliases", {}).get(style, style)
    idx = COLOR_INDEX.get(style)
    return theme["palette"][idx] if idx is not None else theme["fg"]
