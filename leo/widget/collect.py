"""La vista del AI Usage Widget dentro de Leo (LEO_SPEC F7.3): sistema, cuotas, Ollama y ComfyUI.

Cada petición muestrea CPU/RAM/GPU (el monitor conserva la muestra anterior para el porcentaje y el historial).
Ollama y ComfyUI (solo en 127.0.0.1) se leen como mucho cada 20 s y las cuotas de las cuentas cada 5 min, siempre
en un hilo aparte: pedir una instantánea nunca espera a la red. Las cuentas son las de Leo (`leo/usage/hub.py`):
Claude en vivo con su sesión (G18), Codex por su App Server oficial, Antigravity desde su app local y las claves de
API solo del llavero. El dibujo es el del widget (`render.py`): líneas de segmentos (texto, estilo) que el panel
convierte a Pango con su tema y `leoctl usage` a colores ANSI.
"""
from dataclasses import dataclass, field
import threading
import time

from .comfyui import fetch as fetch_comfyui
from .i18n import Translator
from .ollama import fetch as fetch_ollama
from .providers.common import Account, Window
from .render import Line, render_sections, section_header, status_segments
from .system import SystemMonitor

SECTIONS = ["system", "claude", "codex", "antigravity", "api", "ollama", "comfyui"]
COLUMNS = 66
LOCAL_EVERY = 20     # s entre lecturas de Ollama y ComfyUI
ACCOUNTS_EVERY = 10  # s entre relecturas de la lista de cuentas (metadatos y llavero)
USAGE_EVERY = 300    # s entre consultas de cuotas (como el widget)
REFRESH_AT_ONCE = 6  # cuentas por ronda


@dataclass
class Snapshot:
    system: object = None
    accounts: dict = field(default_factory=dict)
    ollama: object = None
    comfyui: object = None
    usage_updated: float | None = None
    next_usage: float | None = None
    refreshing: bool = False
    config_error: str | None = None


def account_of(item, now):
    """One of Leo's accounts (hub) as the widget's `Account`."""
    provider = "api" if item.get("kind") == "key" else item["provider"]
    windows = [Window(w["label"], w["used"], w.get("resets_at"), w.get("used_usd"), w.get("limit_usd"))
               for w in item.get("windows", [])]
    fetched = item.get("fetched_at")
    source = "live" if fetched and now - fetched < 600 and not item.get("error") else "cache" if fetched else "none"
    auth = item.get("auth") if item.get("auth") in {"ok", "nologin", "expired", "error"} else "ok"
    return Account(provider, item.get("name", provider), path=item.get("path", ""), email=item.get("email", ""),
                   plan=item.get("plan", ""), auth=auth, windows=windows, notes=list(item.get("notes", [])),
                   source=source, fetched_at=fetched, error=item.get("error", "") if not windows else "",
                   key=item["id"], balances=[tuple(b) for b in item.get("balances", [])])


class Collector:
    def __init__(self, hub, live=lambda: True, clock=time.time):
        self.hub, self.live, self.clock = hub, live, clock
        self.tr = Translator("es")
        self.monitor = None
        self.lock = threading.Lock()
        self.ollama = self.comfyui = None
        self.accounts, self.accounts_at = [], None
        self.local_at = self.usage_at = None
        self.working = False

    def snapshot(self, width=COLUMNS, refresh=False):
        """{"lines": [[[texto, estilo], …], …], "status": […], "refreshing": bool}; never waits for the network."""
        now = self.clock()
        if self.monitor is None:
            self.monitor = SystemMonitor()
        system = self.monitor.sample(2.0)
        with self.lock:
            due_local = self.local_at is None or now - self.local_at >= LOCAL_EVERY
            due_usage = refresh or self.usage_at is None or now - self.usage_at >= USAGE_EVERY
            due_accounts = refresh or self.accounts_at is None or now - self.accounts_at >= ACCOUNTS_EVERY
            start = (due_local or due_usage or due_accounts) and not self.working
            if start:
                self.working = True
            accounts = list(self.accounts)
        if start:
            threading.Thread(target=self._gather, args=(due_local, due_usage), daemon=True, name="leo-usage").start()
        grouped = {}
        for item in accounts:
            if not item.get("hidden"):
                account = account_of(item, now)
                grouped.setdefault(account.provider, []).append(account)
        with self.lock:
            snap = Snapshot(system=system, accounts=grouped, ollama=self.ollama, comfyui=self.comfyui,
                            usage_updated=self.usage_at, next_usage=(self.usage_at + USAGE_EVERY) if self.usage_at else None,
                            refreshing=self.working)
        width = max(40, min(120, int(width)))
        blocks = render_sections(snap, {"general": {"sections": SECTIONS}, "appearance": {"compact": False}},
                                 self.tr, width, now)
        lines = [line for block in blocks for line in block] or [section_header("LEO", width)]
        return {"lines": [[list(seg) for seg in line.segs] for line in lines],
                "status": [list(seg) for seg in status_segments(snap, self.tr, now)], "refreshing": snap.refreshing,
                "live": bool(self.live())}

    def _gather(self, local, usage):
        try:
            if local:
                ollama, comfyui = None, None
                try:
                    ollama = fetch_ollama()
                except Exception:
                    pass
                try:
                    comfyui = fetch_comfyui()
                except Exception:
                    pass
                with self.lock:
                    self.ollama, self.comfyui, self.local_at = ollama, comfyui, self.clock()
            if usage:
                self.refresh_usage()
            try:
                accounts = self.hub.accounts()["accounts"]
            except Exception:
                accounts = None
            with self.lock:
                if accounts is not None:
                    self.accounts = accounts
                self.accounts_at = self.clock()
        finally:
            with self.lock:
                self.working = False

    def refresh_usage(self):
        """Quotas of the visible profiles (Claude, Codex, Antigravity), at most a few per round."""
        if self.live():
            try:
                accounts = self.hub.accounts()["accounts"]
            except Exception:
                accounts = []
            profiles = [a for a in accounts if a.get("kind") == "profile" and not a.get("hidden")
                        and a["provider"] in {"claude", "codex", "antigravity"}]
            for account in profiles[:REFRESH_AT_ONCE]:
                try:
                    self.hub.dispatch("refresh", {"id": account["id"]})
                except Exception:  # One account never stops the others.
                    pass
        with self.lock:
            self.usage_at = self.clock()


def ansi(lines):
    """The same lines with terminal colours, for `leoctl usage`."""
    from .render import to_ansi
    return to_ansi([_line(segs) for segs in lines])


def _line(segs):
    line = Line()
    for text, style in segs:
        line.add(text, style)
    return line
