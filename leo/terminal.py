"""Conserva la paleta y fuente del widget para las conversaciones de Leo."""
import os
from pathlib import Path
import re
import tomllib

from gi.repository import Gio

from .config import config_dir
from .usage.storage import read, write
from .usage.themes import resolve, THEMES


def appearance():
    path = config_dir() / "terminal.json"
    saved = read(path)
    if not saved:
        legacy = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "ai-usage-widget/config.toml"
        try:
            data = legacy.read_bytes()
            saved = tomllib.loads(data.decode()).get("appearance", {}) if len(data) < 1024 * 1024 else {}
        except (OSError, ValueError):
            saved = {}
        saved = {"theme": saved.get("theme", "ubuntu"), "font": saved.get("font", ""), "opacity": saved.get("opacity", 0.96)}
        write(path, saved)
    interface = Gio.Settings.new("org.gnome.desktop.interface")
    theme = saved.get("theme", "ubuntu")
    if theme not in THEMES and theme != "ubuntu":
        theme = "ubuntu"
    colors = resolve(theme, interface.get_string("color-scheme") != "prefer-light")
    font = saved.get("font") or interface.get_string("monospace-font-name") or "Monospace 11"
    # Only pass font text, never arbitrary CSS from a configuration file.
    font = re.sub(r"[^\w .,-]", "", str(font))[:120]
    try:
        opacity = max(0.3, min(1, float(saved.get("opacity", 0.96))))
    except (TypeError, ValueError):
        opacity = 0.96
    return {"name": theme, "font": font, "opacity": opacity, **colors}


def gtk_css(theme):
    import gi
    gi.require_version("Pango", "1.0")
    from gi.repository import Pango
    desc = Pango.FontDescription.from_string(theme["font"])
    family = desc.get_family() or "Monospace"
    size = max(6, min(32, desc.get_size() / Pango.SCALE or 11))
    return f'''
    .terminal {{ background: {theme['bg']}; color: {theme['fg']}; font-family: "{family}", monospace; font-size: {size}pt; }}
    .terminal .title {{ color: {theme['palette'][10]}; font-size: {size + 3}pt; letter-spacing: 0; }}
    .terminal .subtitle, .terminal .muted {{ color: {theme['header_fg']}; font-size: {size}pt; }}
    .terminal .card, .terminal .bubble, .terminal .hint {{ background: transparent; color: {theme['fg']}; border: none; padding: 6px 0; border-radius: 0; }}
    .terminal .chip {{ background: {theme['header_bg']}; color: {theme['palette'][14]}; border-radius: 4px; }}
    .terminal entry {{ background: {theme['header_bg']}; color: {theme['fg']}; border: 1px solid {theme['border']}; border-radius: 4px; }}
    .terminal button {{ background: {theme['header_bg']}; color: {theme['header_fg']}; border: 1px solid {theme['border']}; border-radius: 4px; box-shadow: none; }}
    .terminal button:hover {{ border-color: {theme['accent']}; }}
    .terminal progressbar text {{ color: {theme['fg']}; }}
    .terminal progressbar progress {{ background: {theme['palette'][10]}; }}
    .terminal progressbar trough {{ background: {theme['header_bg']}; }}
    '''


def font_parts(theme):
    """(family, size in pt) of the terminal font, sanitized for CSS."""
    import gi
    gi.require_version("Pango", "1.0")
    from gi.repository import Pango
    desc = Pango.FontDescription.from_string(theme["font"])
    family = re.sub(r'[";{}]', "", desc.get_family() or "Monospace")
    return family, max(6, min(32, desc.get_size() / Pango.SCALE or 11))


def panel_css(theme):
    """The whole settings panel as a terminal, like the AI Usage Widget (LEO_SPEC F7.2): its palette and font,
    flat controls with thin borders, dim comments and an accent for what is selected."""
    family, size = font_parts(theme)
    fg, bg, accent, border = theme["fg"], theme["bg"], theme["accent"], theme["border"]
    hbg, hfg, p = theme["header_bg"], theme["header_fg"], theme["palette"]
    dim, small = p[8], max(6, size - 1)
    return f'''
    window.leo-window, window.leo-window.background {{ background: {bg}; color: {fg}; }}
    window.leo-window, window.leo-window label, window.leo-window button, window.leo-window entry,
    window.leo-window textview, window.leo-window dropdown, window.leo-window popover {{
        font-family: "{family}", monospace; font-size: {size}pt; }}
    headerbar.leo-header {{ background: {hbg}; color: {hfg}; box-shadow: none; border-bottom: 1px solid {border};
        min-height: 32px; padding: 0 6px; }}
    headerbar.leo-header button, headerbar.leo-header windowcontrols button {{ color: {hfg}; background: none;
        border: none; box-shadow: none; min-width: 24px; min-height: 24px; padding: 0 6px; border-radius: 999px; }}
    headerbar.leo-header button:hover {{ background-color: alpha({hfg}, .14); }}
    .sidebar {{ background: {hbg}; border-right: 1px solid {border}; padding: 12px 8px; }}
    .brand {{ color: {p[10]}; font-weight: bold; }}
    .eyebrow, .subtitle, .muted, .hero-caption {{ color: {dim}; }}
    .muted, .eyebrow {{ font-size: {small}pt; }}
    .nav {{ background: none; border: none; box-shadow: none; padding: 2px 6px; min-height: 22px; border-radius: 4px;
        color: alpha({hfg}, .72); }}
    .nav:hover {{ background-color: alpha({fg}, .08); }}
    .nav:checked {{ color: {accent}; background-color: alpha({accent}, .14); font-weight: bold; }}
    .page {{ padding: 12px 18px 16px 18px; }}
    .prompt-line {{ margin-bottom: 2px; }}
    .title {{ color: {fg}; font-weight: bold; }}
    .rule-title, .section-title {{ color: {accent}; font-weight: bold; }}
    .rule {{ color: {dim}; }}
    separator.rule-line {{ background: none; min-height: 0; border-top: 1px dashed alpha({dim}, .8); }}
    .usage-view {{ background: alpha({fg}, .03); border: 1px solid {border}; border-radius: 6px; padding: 8px 12px; }}
    .seen-time {{ color: {accent}; font-weight: bold; }}
    .seen-what {{ color: {fg}; }}
    .seen-read {{ color: {dim}; font-size: {small}pt; }}
    .seen-note {{ color: {p[14]}; font-size: {small}pt; }}
    .seen-finding {{ color: {p[11]}; font-size: {small}pt; }}
    .card {{ background: alpha({fg}, .03); border: 1px solid {border}; border-radius: 6px; padding: 0 12px; }}
    .setting-row {{ padding: 7px 0; }}
    .setting-row + .setting-row {{ border-top: 1px dashed alpha({dim}, .6); }}
    .row-title {{ color: {fg}; font-weight: bold; }}
    .hint {{ color: {dim}; background: none; border-left: 2px solid {border}; border-radius: 0; padding: 2px 10px; }}
    .hero {{ background: alpha({accent}, .05); border: 1px solid {border}; border-radius: 6px; padding: 10px 16px; }}
    .hero-title {{ color: {p[10]}; font-weight: bold; }}
    .chip {{ background: {hbg}; color: {p[14]}; border: 1px solid {border}; border-radius: 4px; padding: 1px 8px; }}
    .statusbar {{ background: {hbg}; border-top: 1px solid {border}; padding: 3px 12px; }}
    .statusbar label {{ font-size: {small}pt; }}
    .status-text {{ color: {hfg}; }}
    .cursor {{ color: {fg}; }}
    .cursor.off {{ color: transparent; }}
    .bubble {{ background: alpha({fg}, .04); border: 1px solid {border}; border-radius: 6px; padding: 10px; }}
    .error {{ color: {p[9]}; }}
    .ok {{ color: {p[10]}; }}
    .warn {{ color: {p[11]}; }}
    window.leo-window button {{ color: {fg}; background-color: alpha({fg}, .08); background-image: none;
        border: 1px solid {border}; border-radius: 6px; padding: 2px 10px; min-height: 24px; box-shadow: none;
        text-shadow: none; outline: none; }}
    window.leo-window button:hover {{ background-color: alpha({fg}, .16); }}
    window.leo-window button:disabled {{ color: alpha({fg}, .35); }}
    window.leo-window button.suggested-action {{ background-color: {accent}; border-color: {accent}; color: {bg};
        font-weight: bold; }}
    window.leo-window button.nav {{ background: none; border: none; }}
    window.leo-window button.nav:checked {{ background-color: alpha({accent}, .14); }}
    window.leo-window switch {{ background-color: alpha({fg}, .10); border: 1px solid {border}; border-radius: 999px;
        box-shadow: none; }}
    window.leo-window switch:checked {{ background-color: {accent}; border-color: {accent}; }}
    window.leo-window switch slider {{ background: {fg}; border: none; box-shadow: none; min-width: 16px;
        min-height: 16px; border-radius: 999px; }}
    window.leo-window switch:checked slider {{ background: {bg}; }}
    window.leo-window entry, window.leo-window textview, window.leo-window textview text {{ color: {fg};
        background-color: alpha({fg}, .06); caret-color: {fg}; }}
    window.leo-window spinbutton {{ color: {fg}; background-color: alpha({fg}, .06); border: 1px solid {border};
        border-radius: 6px; box-shadow: none; }}
    window.leo-window spinbutton text {{ color: {fg}; background: none; }}
    window.leo-window spinbutton button {{ background: none; border: none; border-left: 1px solid {border};
        border-radius: 0; min-width: 24px; }}
    window.leo-window spinbutton:focus-within {{ border-color: {accent}; }}
    window.leo-window entry {{ border: 1px solid {border}; border-radius: 6px; min-height: 26px; padding: 0 8px;
        box-shadow: none; outline: none; }}
    window.leo-window entry:focus-within {{ border-color: {accent}; }}
    window.leo-window entry text placeholder {{ color: alpha({fg}, .45); }}
    window.leo-window selection {{ background-color: alpha({accent}, .45); }}
    window.leo-window dropdown > button {{ background-color: alpha({fg}, .06); }}
    window.leo-window popover > contents {{ background: {hbg}; color: {fg}; border: 1px solid {border};
        border-radius: 6px; }}
    window.leo-window popover listview row:selected, window.leo-window popover row:hover {{
        background-color: alpha({accent}, .22); }}
    window.leo-window list, window.leo-window listview, window.leo-window list > row {{ background: transparent;
        color: {fg}; }}
    window.leo-window checkbutton check {{ background-color: alpha({fg}, .08); border: 1px solid {border};
        border-radius: 4px; }}
    window.leo-window checkbutton check:checked {{ background-color: {accent}; border-color: {accent}; color: {bg}; }}
    window.leo-window scrollbar {{ background: transparent; }}
    window.leo-window scrollbar slider {{ background-color: alpha({fg}, .25); min-width: 4px; }}
    window.leo-window progressbar trough {{ background: {hbg}; border: 1px solid {border}; }}
    window.leo-window progressbar progress {{ background: {p[10]}; }}
    '''


def prompt_markup(theme, command="", path="~"):
    """«usuario@equipo:~$ orden» in the terminal's colours, as Pango markup."""
    from gi.repository import GLib
    import getpass
    import socket
    try:
        user = getpass.getuser()
    except (KeyError, OSError):
        user = "user"
    host = socket.gethostname().split(".")[0]
    p, esc = theme["palette"], GLib.markup_escape_text
    return (f'<span foreground="{p[10]}" weight="bold">{esc(user)}@{esc(host)}</span>:'
            f'<span foreground="{p[12]}" weight="bold">{esc(path)}</span>$ {esc(command)}')
