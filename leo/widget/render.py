"""Turns a Snapshot into terminal-style lines of (text, style) segments.

The same lines feed both front-ends: Pango markup for the GTK window and ANSI
escapes for the in-terminal mode, so both look identical. Only glyphs present
in Ubuntu Mono are used (█ ░ ▒ ▓ ─ • · …) so columns never drift."""

from __future__ import annotations

import getpass
import html
import socket
import time

from .themes import color
from .util import fmt_bytes, fmt_duration, text_width, truncate

SHADES = "·░▒▓█"
SECTION_TITLES = {"claude": "CLAUDE", "codex": "CODEX", "antigravity": "ANTIGRAVITY", "api": "API"}


class Line:
    __slots__ = ("segs", "width")

    def __init__(self):
        self.segs: list[tuple[str, str]] = []
        self.width = 0

    def add(self, text: str, style: str = "fg") -> Line:
        if text:
            self.segs.append((text, style))
            self.width += text_width(text)
        return self

    def pad(self, col: int) -> Line:
        if self.width < col:
            self.add(" " * (col - self.width))
        return self

    def right(self, segs: list[tuple[str, str]], end: int) -> Line:
        """Append ``segs`` right-aligned so they finish at column ``end``."""
        w = sum(text_width(t) for t, _ in segs)
        if w and self.width + 1 + w <= end:
            self.pad(end - w)
            for text, style in segs:
                self.add(text, style)
        return self

    @property
    def text(self) -> str:
        return "".join(t for t, _ in self.segs)


def money(amount: float) -> str:
    return f"${amount:,.0f}" if float(amount).is_integer() else f"${amount:,.2f}"


def level(pct: float) -> str:
    return "bgreen" if pct < 60 else "byellow" if pct < 85 else "bred"


def add_bar(line: Line, pct: float | None, width: int, style: str | None = None) -> None:
    pct = max(0.0, min(100.0, pct or 0.0))
    filled = int(round(pct / 100 * width))
    if pct > 0 and filled == 0:
        filled = 1
    line.add("[", "dim").add("█" * filled, style or level(pct)).add("░" * (width - filled), "dim").add("]", "dim")


def add_heat(line: Line, history, width: int) -> None:
    values = list(history)[-width:]
    line.add(" " * (width - len(values)))
    for v in values:
        idx = 0 if v < 3 else 1 if v < 25 else 2 if v < 50 else 3 if v < 75 else 4
        line.add(SHADES[idx], level(v) if idx else "dim")


def section_header(title: str, width: int, info: list[tuple[str, str]] | None = None) -> Line:
    line = Line().add("── ", "dim").add(title, "accent+b").add(" ", "dim")
    info = [seg for seg in (info or []) if seg[0]]
    info_w = sum(text_width(t) for t, _ in info)
    if info and line.width + info_w + 6 <= width:
        line.add("─" * (width - line.width - info_w - 4), "dim").add(" ")
        for text, style in info:
            line.add(text, style)
        line.add(" ──", "dim")
    else:
        line.add("─" * max(0, width - line.width), "dim")
    return line


def identity() -> tuple[str, str]:
    try:
        user = getpass.getuser()
    except (KeyError, OSError):
        user = "user"
    return user, socket.gethostname().split(".")[0]


def prompt_segments(user: str, host: str, command: str = "", compact: bool = False) -> list[tuple[str, str]]:
    segs = [] if compact else [(f"{user}@{host}", "green+b"), (":", "fg")]
    segs += [("~", "blue+b"), ("$ ", "fg")]
    if command:
        segs.append((command, "fg"))
    return segs


# ── sections ──────────────────────────────────────────────────────────────────

COMPACT_COLUMNS = 46


def render_system(s, tr, width: int, compact: bool = False) -> list[Line]:
    info = [(s.cpu_model, "dim"), (f" · {s.threads} {tr('threads')}", "dim")] \
        if s.cpu_model and not compact else []
    lines = [section_header(tr("system"), width, info)]
    bar_w = 12 if compact else 20
    col_bar = 5 if compact else 6
    col_extra = col_bar + bar_w + 2 + 5 + 2  # label, [bar], pct, gap
    heat_max = 0 if compact else 24
    gib = "G" if compact else " GiB"

    def heat(line, history):
        heat_w = min(heat_max, width - line.width - 2)
        if heat_w >= 6:
            line.pad(width - heat_w)
            add_heat(line, history, heat_w)

    cpu = Line().add("CPU", "bold").pad(col_bar)
    add_bar(cpu, s.cpu, bar_w)
    cpu.add(f"{s.cpu:4.0f}%").pad(col_extra)
    if compact:
        if s.cpu_temp:
            cpu.add(f"{s.cpu_temp:.0f}°C", "dim")
        if s.cpu_freq:
            cpu.right([(f"{s.cpu_freq:.1f}GHz", "dim")], width)
    else:
        if s.cpu_freq:
            cpu.add(f"{s.cpu_freq:.1f} GHz  ", "dim")
        if s.cpu_temp:
            cpu.add(f"{s.cpu_temp:.0f}°C", "dim")
        heat(cpu, s.cpu_history)
    lines.append(cpu)

    mem_pct = 100.0 * s.mem_used / s.mem_total if s.mem_total else 0
    ram = Line().add("RAM", "bold").pad(col_bar)
    add_bar(ram, mem_pct, bar_w)
    ram.add(f"{mem_pct:4.0f}%").pad(col_extra)
    ram.add(f"{s.mem_used / 2**30:.1f}/{s.mem_total / 2**30:.1f}{gib}", "dim")
    if s.swap_total and not compact:
        ram.right([(f"swap {s.swap_used / 2**30:.1f}/{s.swap_total / 2**30:.1f}", "dim")], width)
    lines.append(ram)

    dgpus = [g for g in s.gpus if g.kind == "dgpu"]
    for i, g in enumerate(s.gpus):
        if g.kind == "dgpu":
            label = "GPU" if len(dgpus) == 1 else f"GPU{i}"
            line = Line().add(label, "bold").pad(col_bar)
            if g.state == "suspended":
                line.add(truncate(f"{tr('sleeping')} · {g.name}", width - line.width), "dim")
                lines.append(line)
                continue
            add_bar(line, g.util, bar_w)
            line.add(f"{g.util or 0:4.0f}%" if g.util is not None else "   ? ").pad(col_extra)
            if g.temp is not None:
                line.add(f"{g.temp:.0f}°C  ", "dim")
            if g.power is not None:
                if compact:
                    line.right([(f"{g.power:.0f}W", "dim")], width)
                else:
                    line.add(f"{g.power:.0f} W", "dim")
            heat(line, g.history)
            lines.append(line)
            if g.mem_total:
                pct = 100.0 * (g.mem_used or 0) / g.mem_total
                vram = Line().add("VRAM", "bold").pad(col_bar)
                add_bar(vram, pct, bar_w)
                vram.add(f"{pct:4.0f}%").pad(col_extra)
                vram.add(f"{(g.mem_used or 0) / 2**30:.1f}/{g.mem_total / 2**30:.1f}{gib}", "dim")
                if not compact:
                    vram.right([(truncate(g.name, width - vram.width - 2), "fg")], width)
                lines.append(vram)
            elif not compact:
                lines.append(Line().pad(col_extra).add(truncate(g.name, width - col_extra), "fg"))
        elif not compact:  # the iGPU clock is not worth a line in compact mode
            line = Line().add("iGPU", "bold").pad(col_bar)
            if g.state == "suspended":
                line.add(truncate(f"{tr('sleeping')} · {g.name}", width - line.width), "dim")
            elif g.freq is not None and g.freq_max:
                add_bar(line, g.util, bar_w, "bcyan")
                line.add(f" {g.freq}/{g.freq_max} MHz", "dim")
                line.right([(truncate(g.name, width - line.width - 2), "fg")], width)
            elif g.util is not None:
                add_bar(line, g.util, bar_w)
                line.add(f"{g.util:4.0f}%").pad(col_extra)
                line.add(truncate(g.name, width - line.width), "fg")
            else:
                line.add(truncate(g.name, width - line.width), "dim")
            lines.append(line)
    if not s.gpus:
        lines.append(Line().add("GPU", "bold").pad(col_bar).add(tr("no_gpu"), "dim"))
    return lines


def _status_style(acc, now: float) -> str:
    if acc.auth == "nologin":
        return "dim"
    if acc.auth in ("expired", "error") and not acc.windows:
        return "bred"
    if acc.source == "live":
        return "bgreen"
    if acc.windows:
        return "byellow"
    return "dim"


def _window_label(label: str, compact: bool) -> str:
    if compact and label.startswith("Claude "):  # "Claude Opus 4.6" → "Opus 4.6"
        return label[len("Claude "):]
    return label


def render_accounts(provider: str, accounts: list, tr, width: int, now: float,
                    name_w: int | None = None, compact: bool = False) -> list[Line]:
    lines = [section_header(SECTION_TITLES[provider], width)]
    name_w = name_w or max(8, min(16, max(text_width(a.name) for a in accounts)))
    labels = [_window_label(w.label, compact) for a in accounts for w in a.windows]
    labels += [label for a in accounts for label, _ in a.balances]
    # at least 7 columns so "5h"/"7d" rows line up across sections and with "credit"
    label_w = max(7, min(16 if compact else 18, max((text_width(x) for x in labels), default=2)))
    gap = 1 if compact else 2
    reset_w = 7 if compact else text_width(tr("resets_in", t="00d 00h"))
    # indent + label + gap + [bar] + pct + gap + reset text
    bar_w = max(8, min(12 if compact else 20, width - (2 + label_w + gap + 2 + 4 + 2 + reset_w)))

    for acc in accounts:
        line = Line().add("• ", _status_style(acc, now)).add(acc.name, "bold").pad(2 + name_w + 2)
        if acc.auth == "nologin":
            if compact:
                text = tr("nologin_short")
            else:
                text = tr("nologin", cmd=acc.command) if acc.command else tr("nologin_tab")
            line.add(truncate(text, width - line.width), "dim")
            lines.append(line)
            continue
        right: list[tuple[str, str]] = []
        if acc.source in ("cache", "local") and acc.fetched_at:
            right.append((tr("cached", age=fmt_duration(now - acc.fetched_at)), "yellow"))
        if acc.is_limited(now):
            right += [(" " if right else "", "fg"), (tr("limit"), "bred+b")]
        if acc.plan:
            right += [(" · " if right else "", "dim"), (acc.plan, "cyan")]
        right_w = sum(text_width(t) for t, _ in right)
        room = width - line.width - right_w - 2
        if room >= (6 if compact else 4):
            line.add(truncate(acc.email, room), "dim" if compact else "fg")
        line.right(right, width)
        lines.append(line)

        if acc.auth == "expired":
            hint = tr("expired", cmd=acc.command) if acc.command else tr("expired_tab")
            lines.append(Line().add("  ").add(truncate(hint, width - 2), "yellow"))
        for w in acc.windows:
            used = w.current_used(now)
            row = Line().add("  ").add(truncate(_window_label(w.label, compact), label_w), "fg")
            row.pad(2 + label_w + gap)
            add_bar(row, used, bar_w)
            row.add(f"{used:4.0f}%")
            if w.is_credit:
                amounts = {"used": money(w.used_usd or 0), "limit": money(w.limit_usd)}
                if compact:
                    text = f"{amounts['used']}/{amounts['limit']}"
                elif w.resets_at:
                    text = tr("credit_text", date=tr.date(w.resets_at), **amounts)
                else:
                    text = tr("credit_nodate", **amounts)
            elif w.resets_at and w.resets_at > now:
                left = fmt_duration(w.resets_at - now)
                text = left if compact else tr("resets_in", t=left)
            elif w.resets_at:
                text = "" if compact else tr("reset_done")
            else:
                text = ""
            if text:
                row.add("  ")
                row.add(truncate(text, width - row.width), "dim")
            lines.append(row)
        for label, value in acc.balances:
            row = Line().add("  ").add(truncate(label, label_w), "fg").pad(2 + label_w + gap)
            lines.append(row.add(truncate(value, width - row.width), "bold"))
        if not acc.windows and not acc.balances and acc.auth in ("ok", "error") and not acc.notes:
            msg = tr("rate_limited") if acc.rate_limited else tr("error", msg=acc.error) if acc.error \
                else tr("nodata", cmd=acc.command or acc.name)
            lines.append(Line().add("  ").add(truncate(msg, width - 2), "red" if acc.error else "dim"))
        for note in acc.notes:
            lines.append(Line().add("  · ", "dim").add(truncate(note, width - 4), "dim"))
    return lines


def render_ollama(st, tr, width: int, now: float, compact: bool = False) -> list[Line]:
    if st.running:
        version = f"v{st.version} · " if st.version and not compact else ""
        info = [(version, "dim"), (tr("ollama_running"), "bgreen")]
    else:
        info = [(tr("ollama_stopped"), "byellow")]
    if st.models:
        info += [(" · ", "dim"), (fmt_bytes(st.total_size), "dim")]
    lines = [section_header("OLLAMA", width, info)]
    if not st.models:
        lines.append(Line().add("  ").add(truncate(tr("no_models"), width - 2), "dim"))
        return lines
    name_w = max(10, min(20 if compact else 28, max(text_width(m.name) for m in st.models)))
    for m in st.models:
        line = Line().add("  ")
        line.add(truncate(m.name, name_w), "bold" if m.loaded else "fg" if st.running else "dim")
        line.pad(2 + name_w + 2)
        if not compact:
            line.add(f"{m.params:>6}", "dim").add("  ").add(f"{m.quant:<7}", "dim")
        line.add(f"{fmt_bytes(m.size):>8}", "fg" if st.running else "dim")
        if m.loaded:
            where = "CPU" if not m.vram_pct else "GPU" if compact else f"GPU {m.vram_pct:.0f}%"
            expiry = [(f" · {fmt_duration(m.expires_at - now)}", "dim")] \
                if m.expires_at and m.expires_at > now and not compact else []
            # drop details until the marker fits on narrow terminals
            for tail in ([("» " + where, "bgreen")] + expiry, [("» " + where, "bgreen")], [("»", "bgreen")]):
                if line.width + 2 + sum(text_width(t) for t, _ in tail) <= width:
                    line.add("  ")
                    for text, style in tail:
                        line.add(text, style)
                    break
        lines.append(line)
    return lines


def _fitting_info(title: str, width: int, *choices: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """The richest header info that still fits next to ``title``."""
    for info in choices:
        if text_width(title) + 4 + sum(text_width(t) for t, _ in info) + 6 <= width:
            return info
    return []


def render_comfyui(st, tr, width: int, now: float, compact: bool = False) -> list[Line]:
    total = [(" · ", "dim"), (fmt_bytes(st.total_size), "dim")] if st.total_size else []
    if st.running:
        state = [(tr("comfy_generating") if st.jobs_running else tr("ollama_running"), "bgreen")]
        queued = [(" · " + tr("comfy_queued", n=st.jobs_pending), "dim")] if st.jobs_pending else []
        version = [(f"v{st.version} · ", "dim")] if st.version and not compact else []
        info = _fitting_info("COMFYUI", width, version + state + queued + total, state + queued + total, state)
    else:
        info = _fitting_info("COMFYUI", width, [(tr("ollama_stopped"), "dim")] + total)
    lines = [section_header("COMFYUI", width, info)]
    if not st.models:
        lines.append(Line().add("  ").add(truncate(tr("comfy_no_models"), width - 2), "dim"))
        return lines
    name_w = max(10, min(20 if compact else 28, max(text_width(m.name) for m in st.models)))
    held = st.vram_used if st.running and (st.vram_used or 0) >= 1 << 28 else None
    for m in st.models:
        line = Line().add("  ").add(truncate(m.name, name_w), "bold" if m.active else "fg")
        line.pad(2 + name_w + 2)
        if not compact:
            line.add(f"{m.quant:<7}", "dim").add("  ")
        line.add(f"{fmt_bytes(m.size):>8}", "fg")
        if m.active:
            marker = "» " + tr("comfy_generating")
        elif held and len(st.models) == 1:  # with one model, the memory ComfyUI holds is that model's
            marker = "» GPU" if compact else f"» GPU {fmt_bytes(held)}"
        else:
            marker = ""
        if marker and line.width + 2 + text_width(marker) <= width:
            line.add("  ").add(marker, "bgreen")
        lines.append(line)
    if st.parts:  # text encoders, VAE and LoRAs: needed to generate, and they take disk too
        size = fmt_bytes(sum(b for _, b in st.parts.values()))
        if compact:
            text = tr("comfy_parts_short", n=sum(n for n, _ in st.parts.values()))
        else:
            text = "+ " + " · ".join(tr(f"comfy_part_{kind}") + (f" ×{n}" if n > 1 else "")
                                     for kind in ("text_encoders", "vae", "loras")
                                     for n, _ in [st.parts.get(kind, (0, 0))] if n)
        lines.append(Line().add("  ").add(truncate(f"{text} · {size}", width - 2), "dim"))
    return lines


def split_columns(heights: list[int], columns: int) -> list[int]:
    """Cut a run of blocks into at most ``columns`` consecutive groups whose tallest
    group is as short as possible, using no more groups than that needs.
    Returns how many blocks go in each group."""
    n = len(heights)
    if not n:
        return []
    columns = max(1, min(columns, n))
    prefix = [0]
    for h in heights:
        prefix.append(prefix[-1] + h)
    inf = float("inf")
    best = [[inf] * (columns + 1) for _ in range(n + 1)]
    cut = [[0] * (columns + 1) for _ in range(n + 1)]
    best[0][0] = 0
    for k in range(1, columns + 1):
        for i in range(k, n + 1):
            for j in range(k - 1, i):
                cost = max(best[j][k - 1], prefix[i] - prefix[j])
                if cost < best[i][k]:
                    best[i][k], cut[i][k] = cost, j
    target = min(best[n][k] for k in range(1, columns + 1))
    k = next(k for k in range(1, columns + 1) if best[n][k] == target)
    sizes, i = [], n
    while k:
        j = cut[i][k]
        sizes.append(i - j)
        i, k = j, k - 1
    return sizes[::-1]


# ── small sizes (a docked widget the user narrowed down) ──────────────────────

def _mini_groups(snap, cfg: dict, tr, now: float) -> list[tuple[str, list[tuple]]]:
    """The essentials of every section, for layouts too small for the full view.
    Items: ("head", name, style) · ("metric", label, pct, style) ·
    ("text", label, value, style) · ("note", text, style)."""
    groups: list[tuple[str, list[tuple]]] = []
    for section in cfg["general"]["sections"]:
        items: list[tuple] = []
        if section == "system" and snap.system:
            s = snap.system
            items.append(("metric", "CPU", s.cpu, None))
            if s.mem_total:
                items.append(("metric", "RAM", 100.0 * s.mem_used / s.mem_total, None))
            dgpus = [g for g in s.gpus if g.kind == "dgpu"]
            for i, g in enumerate(dgpus):
                label = "GPU" if len(dgpus) == 1 else f"GPU{i}"
                if g.state == "suspended":
                    items.append(("text", label, "zZ", "dim"))
                    continue
                if g.util is not None:
                    items.append(("metric", label, g.util, None))
                if g.mem_total:
                    items.append(("metric", "VRAM", 100.0 * (g.mem_used or 0) / g.mem_total, None))
            groups.append((tr("system"), items))
        elif section in SECTION_TITLES and snap.accounts.get(section):
            for acc in snap.accounts[section]:
                items.append(("head", acc.name, _status_style(acc, now)))
                if acc.auth == "nologin":
                    items.append(("note", tr("nologin_short"), "dim"))
                    continue
                for w in acc.windows:
                    used = w.current_used(now)
                    label = tr("credit") if w.is_credit else _window_label(w.label, True)
                    items.append(("metric", label, used, "bred+b" if used >= 100 else None))
                for label, value in acc.balances:
                    items.append(("text", label, value, "bold"))
            groups.append((SECTION_TITLES[section], items))
        elif section == "ollama" and snap.ollama and snap.ollama.installed:
            st = snap.ollama
            items.append(("note", tr("ollama_running") if st.running else tr("ollama_stopped"),
                          "bgreen" if st.running else "byellow"))
            for m in st.models:
                if m.loaded:
                    items.append(("text", m.name, "CPU" if not m.vram_pct else "GPU", "bgreen"))
            groups.append(("OLLAMA", items))
        elif section == "comfyui" and snap.comfyui and snap.comfyui.installed:
            st = snap.comfyui
            state = tr("comfy_generating") if st.jobs_running else tr("ollama_running") if st.running \
                else tr("ollama_stopped")
            items.append(("note", state, "bgreen" if st.running else "dim"))
            for m in st.models:
                items.append(("note", m.name, "bold" if m.active else "fg"))
            groups.append(("COMFYUI", items))
    return groups


def _pct_text(pct: float) -> str:
    return f"{max(0.0, pct or 0.0):.0f}%"


def _flush_right(text: str, style: str, width: int) -> Line:
    text = truncate(text, width)
    return Line().pad(width - text_width(text)).add(text, style)


def render_narrow(snap, cfg: dict, tr, width: int, now: float | None = None, bars: bool = False) -> list[Line]:
    """A column as thin as a dock: one short line per figure (two when the label
    doesn't fit), plus a small bar under each one when ``bars`` (there's room)."""
    now = now or time.time()
    width = max(4, width)
    lines: list[Line] = []
    for title, items in _mini_groups(snap, cfg, tr, now):
        lines.append(section_header(title, width) if text_width(title) + 6 <= width
                     else Line().add(truncate(title, width), "accent+b"))
        labels = [it[1] for it in items if it[0] == "metric"]
        label_w = min(8, width // 3, max((text_width(x) for x in labels), default=0))
        for item in items:
            kind = item[0]
            if kind == "head":
                lines.append(Line().add("• ", item[2]).add(truncate(item[1], width - 2), "bold") if width >= 5
                             else Line().add(truncate(item[1], width), "bold"))
            elif kind == "note":
                lines.append(Line().add(truncate(item[1], width), item[2]))
            elif kind == "text":
                _, label, value, style = item
                if text_width(label) + 1 + text_width(value) <= width:
                    lines.append(Line().add(label).right([(value, style)], width))
                else:
                    lines.append(Line().add(truncate(label, width)))
                    lines.append(_flush_right(value, style, width))
            else:
                _, label, pct, style = item
                pct_s = _pct_text(pct)
                style = style or level(pct)
                bar_w = width - label_w - 1 - 2 - 5
                if width >= 14 and bar_w >= 3:
                    row = Line().add(truncate(label, label_w)).pad(label_w + 1)
                    add_bar(row, pct, bar_w)
                    lines.append(row.right([(pct_s, style)], width))
                    continue
                if text_width(label) + 1 + len(pct_s) <= width:
                    lines.append(Line().add(label).right([(pct_s, style)], width))
                else:  # long model names: label on its own line
                    lines.append(Line().add(truncate(label, width), "dim"))
                    lines.append(_flush_right(pct_s, style, width))
                if bars and width >= 4:
                    bar = Line()
                    add_bar(bar, pct, width - 2)
                    lines.append(bar)
    return lines


def render_strip(snap, cfg: dict, tr, width: int, now: float | None = None, bars: bool = False,
                 status: list[tuple[str, str]] | None = None) -> list[Line]:
    """A low band: every figure as a short cell, cells flowing across the width."""
    now = now or time.time()
    lines = [Line()]

    def put(segs: list[tuple[str, str]], sep: str):
        w = sum(text_width(t) for t, _ in segs)
        line = lines[-1]
        if line.width and line.width + text_width(sep) + w > width:
            line = Line()
            lines.append(line)
        elif line.width:
            line.add(sep, "dim")
        for text, style in segs:
            line.add(truncate(text, width - line.width), style)

    for i, (title, items) in enumerate(_mini_groups(snap, cfg, tr, now)):
        cells: list[tuple[list[tuple[str, str]], str]] = []
        for item in items:
            kind = item[0]
            if kind == "head":
                cells.append(([("• ", item[2]), (item[1], "bold")], "  "))
            elif kind == "note":
                cells.append(([(item[1], item[2])], " "))
            elif kind == "text":
                cells.append(([(item[1] + " ", "fg"), (item[2], item[3])], " "))
            else:
                _, label, pct, style = item
                segs = [(label + " ", "fg")]
                if bars:
                    bar = Line()
                    add_bar(bar, pct, 6)
                    segs += bar.segs + [(" ", "fg")]
                cells.append((segs + [(_pct_text(pct), style or level(pct))], " "))
        first = cells.pop(0)[0] if cells else []
        # the title travels with the first cell, so it never ends a row on its own
        put([(title, "accent+b"), (" " if first else "", "fg"), *first], "  │  " if i else "")
        for segs, sep in cells:
            put(segs, sep)
    if status:
        put(status, "  │  ")
    return lines


def body_width(cfg: dict) -> int:
    app = cfg["appearance"]
    return COMPACT_COLUMNS if app.get("compact") else app["columns"]


def render_sections(snap, cfg: dict, tr, width: int | None = None, now: float | None = None) -> list[list[Line]]:
    """Each visible section as its own block of lines (docked at the top, the
    widget lays the blocks out side by side)."""
    compact = bool(cfg["appearance"].get("compact"))
    width = width or body_width(cfg)
    now = now or time.time()
    shown = [a for p in SECTION_TITLES if p in cfg["general"]["sections"] for a in snap.accounts.get(p, [])]
    name_w = max(8, min(16, max((text_width(a.name) for a in shown), default=8)))
    blocks: list[list[Line]] = []
    for section in cfg["general"]["sections"]:
        if section == "system" and snap.system:
            blocks.append(render_system(snap.system, tr, width, compact))
        elif section in SECTION_TITLES and snap.accounts.get(section):
            blocks.append(render_accounts(section, snap.accounts[section], tr, width, now, name_w, compact))
        elif section == "ollama" and snap.ollama and snap.ollama.installed:
            blocks.append(render_ollama(snap.ollama, tr, width, now, compact))
        elif section == "comfyui" and snap.comfyui and snap.comfyui.installed:
            blocks.append(render_comfyui(snap.comfyui, tr, width, now, compact))
    if snap.config_error:
        blocks.append([Line().add(truncate(tr("config_error", msg=snap.config_error), width), "bred")])
    return blocks


def render_body(snap, cfg: dict, tr, width: int | None = None, now: float | None = None,
                user_host: tuple[str, str] | None = None) -> list[Line]:
    compact = bool(cfg["appearance"].get("compact"))
    width = width or body_width(cfg)
    now = now or time.time()
    lines: list[Line] = []
    if cfg["appearance"]["show_prompt"] and not compact:
        user, host = user_host or identity()
        top = Line()
        for text, style in prompt_segments(user, host, "ai-usage-widget"):
            top.add(text, style)
        top.right([(time.strftime("%H:%M", time.localtime(now)), "dim")], width)
        lines.append(top)
    for block in render_sections(snap, cfg, tr, width, now):
        lines += block
    return lines


def status_segments(snap, tr, now: float | None = None, compact: bool = False) -> list[tuple[str, str]]:
    now = now or time.time()
    if snap.refreshing and not snap.usage_updated:
        return [(tr("refreshing"), "dim")]
    segs = []
    if snap.usage_updated:
        segs.append((tr("updated", time=time.strftime("%H:%M", time.localtime(snap.usage_updated))), "dim"))
    if snap.refreshing:
        segs.append((" · " + tr("refreshing"), "dim"))
    elif snap.next_usage and snap.next_usage > now and not compact:
        segs.append((" · " + tr("next", t=fmt_duration(snap.next_usage - now)), "dim"))
    return segs


# ── output formats ────────────────────────────────────────────────────────────

ANSI = {
    "fg": "39", "dim": "90", "accent": "38;5;202", "bold": "1",
    "black": "30", "red": "31", "green": "32", "yellow": "33", "blue": "34", "magenta": "35",
    "cyan": "36", "white": "37", "bred": "91", "bgreen": "92", "byellow": "93", "bblue": "94",
    "bmagenta": "95", "bcyan": "96", "bwhite": "97",
}


def _ansi_codes(style: str) -> str:
    parts = style.split("+")
    codes = [ANSI.get(parts[0], "39")] if parts[0] != "bold" else ["1"]
    if "b" in parts[1:]:
        codes.append("1")
    return ";".join(codes)


def to_ansi(lines: list[Line], use_color: bool = True) -> str:
    out = []
    for line in lines:
        if not use_color:
            out.append(line.text.rstrip())
            continue
        out.append("".join(f"\x1b[{_ansi_codes(style)}m{text}\x1b[0m" for text, style in line.segs))
    return "\n".join(out)


def _span(text: str, style: str, theme: dict) -> str:
    parts = style.split("+")
    base, bold = parts[0], "b" in parts[1:] or parts[0] == "bold"
    fg = color(theme, "fg" if base == "bold" else base)
    attrs = f' foreground="{fg}"' + (' weight="bold"' if bold else "")
    return f"<span{attrs}>{html.escape(text, quote=False)}</span>"


def segments_to_pango(segs: list[tuple[str, str]], theme: dict) -> str:
    merged: list[list[str]] = []
    for text, style in segs:
        if merged and merged[-1][1] == style:
            merged[-1][0] += text
        else:
            merged.append([text, style])
    return "".join(_span(t, s, theme) for t, s in merged)


def to_pango(lines: list[Line], theme: dict) -> str:
    return "\n".join(segments_to_pango(line.segs, theme) for line in lines)
