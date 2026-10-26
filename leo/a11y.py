"""Accesibilidad AT-SPI bajo demanda (LEO_SPEC F4.3). Capacidad `accessibility`, apagada por defecto.

Lee la ventana activa (nombre, rol, estados y acciones, con tope de nodos), busca elementos por su
nombre visible y los activa con la interfaz `Action` (con aprobación). Nunca lee el contenido de
campos: solo nombres accesibles, y de los campos de contraseña ni eso. La primera lectura de cada
aplicación en la sesión pide consentimiento. Las coordenadas son de ventana; la extensión las
convierte a pantalla con el rectángulo del búfer de la ventana (hipótesis del spike de Wayland).

Medido en GNOME 50/Wayland (2026-10-23): GTK 4 expone la ventana (`showing`, `visible`) pero no el estado
`active`, aunque Mutter le dé el foco. Por eso la ventana de trabajo la dice GNOME (`focus`: PID, título e
identificadores de la extensión) y `active` queda como respaldo. Las ventanas protegidas no se leen.
"""
import threading

from .broker import APPROVED
from .conversation import normalize

MAX_NODES = 300
NAME_LIMIT = 120
PREFERRED_ACTIONS = ("click", "press", "activate", "toggle", "jump", "default.activate")
INTERACTIVE = {"push button", "toggle button", "check box", "radio button", "menu item", "check menu item",
               "radio menu item", "link", "page tab", "combo box", "list item", "spin button", "slider",
               "switch", "menu", "tree item", "table cell", "entry", "text", "password text", "button"}
PASSWORD_ROLES = {"password text"}
TOGGLES = {"check box", "radio button", "toggle button", "switch"}


def _atspi():
    import gi
    gi.require_version("Atspi", "2.0")
    from gi.repository import Atspi
    return Atspi


def _action_names(action):
    Atspi = _atspi()
    # get_name replaces get_action_name in newer libatspi; older ones (2.52) only have the latter.
    name = getattr(Atspi.Action, "get_name", None) or Atspi.Action.get_action_name
    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        return [name(action, i) for i in range(min(action.get_n_actions(), 8))]


def window_rect(node):
    """[x, y, w, h] from the window's origin, adding PARENT extents up to the frame. GTK 4 reports WINDOW
    coordinates with a GtkStack page's offset counted twice (the page and its child repeat the same PARENT bounds):
    a parent with exactly the child's PARENT bounds is that duplicate and is skipped (measured 2026-10-23)."""
    Atspi = _atspi()
    component = node.get_component_iface()
    if component is None:
        return None
    own = component.get_extents(Atspi.CoordType.PARENT)
    x, y, previous = own.x, own.y, (own.x, own.y, own.width, own.height)
    parent = node.get_parent()
    for _ in range(60):
        if parent is None or parent.get_role() in (Atspi.Role.FRAME, Atspi.Role.WINDOW, Atspi.Role.DIALOG,
                                                   Atspi.Role.APPLICATION):
            break
        bounds = parent.get_component_iface()
        if bounds is None:
            break
        extents = bounds.get_extents(Atspi.CoordType.PARENT)
        current = (extents.x, extents.y, extents.width, extents.height)
        if current != previous:
            x, y = x + extents.x, y + extents.y
        previous, parent = current, parent.get_parent()
    return [x, y, own.width, own.height]


def origin(app):
    """Where window coordinates start: GTK 3 counts its client-side shadows (the buffer); GTK 4, the frame."""
    try:
        toolkit, version = (app.get_toolkit_name() or "").upper(), app.get_toolkit_version() or ""
    except Exception:
        return "frame"
    return "buffer" if toolkit.startswith("GTK") and version.startswith("3") else "frame"


class A11y:
    def __init__(self, timeout_ms=1500, focus=None):
        self.timeout_ms = timeout_ms
        self.focus = focus  # () -> {"pid", "title", "protected"} of GNOME's working window, or raises.
        self.lock = threading.Lock()
        self.granted = set()

    def _window(self, pid=None):
        """(application, frame) of the user's working window, or of `pid` when given (tests)."""
        Atspi = _atspi()
        Atspi.set_timeout(self.timeout_ms, self.timeout_ms * 2)
        title = ""
        if pid is None and self.focus is not None:
            try:
                focus = self.focus()
            except Exception:
                focus = None  # Without the extension (X11 tests, older shell): the ACTIVE state alone.
            if focus:
                if focus.get("protected"):
                    raise PermissionError("Es una ventana protegida: Leo no lee su interfaz")
                pid, title = focus.get("pid") or None, focus.get("title") or ""
        desktop = Atspi.get_desktop(0)
        titled = fallback = None
        apps = [desktop.get_child_at_index(i) for i in range(min(desktop.get_child_count(), 200))]
        for strict in (True, False):  # Second pass: a sandboxed app may report another PID; its title still matches.
            for app in apps:
                if app is None or (strict and pid is not None and app.get_process_id() != pid):
                    continue
                for j in range(min(app.get_child_count(), 50)):
                    frame = app.get_child_at_index(j)
                    if frame is None:
                        continue
                    states = frame.get_state_set()
                    if strict and states.contains(Atspi.StateType.ACTIVE) and (pid is not None or not title):
                        return app, frame
                    if title and titled is None and (frame.get_name() or "") == title:
                        titled = (app, frame)
                    if strict and pid is not None and fallback is None and states.contains(Atspi.StateType.SHOWING):
                        fallback = (app, frame)
            if titled or fallback or not title:
                break
        if titled or fallback:
            return titled or fallback
        raise LookupError("No encuentro la ventana activa en la accesibilidad (¿la aplicación la expone?)")

    @staticmethod
    def _key(app):
        return app.get_name() or f"pid {app.get_process_id()}"

    def consent_needed(self, pid=None):
        """Approval text when this application has not been read in this session yet."""
        try:
            app, frame = self._window(pid)
        except Exception:
            return None  # Nothing to consent to: the tool itself will report the error.
        key = self._key(app)
        if key in self.granted:
            return None
        return f"Leer la interfaz de «{key}» (ventana «{(frame.get_name() or '')[:60]}») durante esta sesión"

    def _authorize(self, app):
        key = self._key(app)
        if key not in self.granted:
            if not APPROVED.get():
                raise PermissionError(f"Autoriza primero la lectura de «{key}»")
            self.granted.add(key)

    def _nodes(self, frame):
        """Breadth-first visible nodes: [(node, info)]; contents of fields are never read."""
        Atspi = _atspi()
        queue, found = [frame], []
        while queue and len(found) < MAX_NODES:
            node = queue.pop(0)
            try:
                role = node.get_role_name() or ""
                states = node.get_state_set()
                if node is not frame and not states.contains(Atspi.StateType.SHOWING):
                    continue
                name = "" if role in PASSWORD_ROLES else (node.get_name() or "")[:NAME_LIMIT]
                if role in PASSWORD_ROLES:
                    name = "(campo de contraseña)"
                action = node.get_action_iface()
                actions = _action_names(action) if action else []
                # GTK 4 marks usable widgets SENSITIVE, not ENABLED (measured in GNOME 50, 2026-10-23).
                enabled = states.contains(Atspi.StateType.ENABLED) or states.contains(Atspi.StateType.SENSITIVE)
                info = {"role": role, "name": name, "actions": actions,
                        "states": (["enabled"] if enabled else []) +
                        [s for s, t in (("focused", Atspi.StateType.FOCUSED), ("checked", Atspi.StateType.CHECKED),
                                        ("selected", Atspi.StateType.SELECTED)) if states.contains(t)]}
                found.append((node, info))
                queue.extend(node.get_child_at_index(i) for i in range(min(node.get_child_count(), 100)))
            except Exception:
                continue
        return found

    def read(self, pid=None):
        with self.lock:
            app, frame = self._window(pid)
            self._authorize(app)
            nodes = self._nodes(frame)
        items = [{"n": i, **info} for i, (_, info) in enumerate(nodes) if info["name"] or info["actions"]]
        return {"app": self._key(app), "window": frame.get_name() or "", "elements": items,
                "truncated": len(nodes) >= MAX_NODES}

    def _find(self, frame, name):
        wanted = normalize(name or "")
        if not wanted:
            raise ValueError("Indica el nombre visible del elemento")
        best, score = None, 0
        for node, info in self._nodes(frame):
            if info["role"] in PASSWORD_ROLES or not info["name"]:
                continue
            label = normalize(info["name"])
            points = 3 if label == wanted else 2 if label.startswith(wanted) else 1 if wanted in label else 0
            if points and info["role"] in INTERACTIVE:
                points += 1
            if points > score:
                best, score = (node, info), points
        if best is None:
            raise LookupError(f"No encuentro «{name}» en la ventana activa")
        return best

    def locate(self, name, pid=None):
        """Element and its rectangle in window coordinates, for pointing at it."""
        Atspi = _atspi()
        with self.lock:
            app, frame = self._window(pid)
            self._authorize(app)
            node, info = self._find(frame, name)
            rect = window_rect(node)
        if rect and info["role"] in TOGGLES and rect[2] > 8 * rect[3]:
            rect[2] = 8 * rect[3]  # A stretched check box: point at its box and text, not the empty space after them.
        if rect is None or rect[2] <= 0 or rect[3] <= 0:
            raise LookupError(f"«{info['name']}» no tiene posición en pantalla")
        return {"pid": app.get_process_id(), "app": self._key(app), "window": frame.get_name() or "",
                "name": info["name"], "role": info["role"], "rect": rect, "origin": origin(app)}

    def elements(self, pid=None):
        """(app, frame, [{"name", "role", "rect"}]) of the visible interactive elements with a position, in window
        coordinates, for numbered marks (F6.7). Password fields keep no name."""
        Atspi = _atspi()
        with self.lock:
            app, frame = self._window(pid)
            self._authorize(app)
            items = []
            for node, info in self._nodes(frame):
                if info["role"] not in INTERACTIVE or "enabled" not in info["states"]:
                    continue
                rect = window_rect(node)
                if rect is None or rect[2] < 6 or rect[3] < 6:
                    continue
                items.append({"name": info["name"], "role": info["role"], "rect": rect, "origin": origin(app)})
        return app, frame, items

    def press(self, name, pid=None):
        with self.lock:
            app, frame = self._window(pid)
            self._authorize(app)
            node, info = self._find(frame, name)
            action = node.get_action_iface()
            if not action or not action.get_n_actions():
                raise ValueError(f"«{info['name']}» no admite acciones")
            names = _action_names(action)
            index = next((names.index(a) for a in PREFERRED_ACTIONS if a in names), 0)
            if not action.do_action(index):
                raise RuntimeError(f"La aplicación rechazó «{names[index]}» en «{info['name']}»")
        return {"pressed": info["name"], "role": info["role"], "action": names[index], "app": self._key(app)}
