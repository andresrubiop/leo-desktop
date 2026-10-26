"""F4: la ventana de trabajo para AT-SPI la dice GNOME; GTK 4 en Wayland no marca su ventana como ACTIVE."""
import unittest
from unittest.mock import patch

from leo.a11y import A11y


class State:
    ACTIVE, SHOWING = "active", "showing"


class States:
    def __init__(self, states):
        self.states = set(states)

    def contains(self, state):
        return state in self.states


class Node:
    def __init__(self, name, states=(), children=(), pid=0):
        self.name, self.states, self.children, self.pid = name, states, list(children), pid

    def get_child_count(self):
        return len(self.children)

    def get_child_at_index(self, index):
        return self.children[index]

    def get_state_set(self):
        return States(self.states)

    def get_name(self):
        return self.name

    def get_process_id(self):
        return self.pid


class Atspi:
    StateType = State

    def __init__(self, desktop):
        self.desktop = desktop

    def set_timeout(self, *_):
        pass

    def get_desktop(self, _index):
        return self.desktop


def desktop():
    editor = Node("gedit", pid=40, children=[Node("notas.md - gedit", (State.SHOWING,))])
    shell = Node("gnome-shell", pid=10, children=[Node("Main stage", (State.SHOWING,))])
    sandboxed = Node("Zotero", pid=99, children=[Node("Mi biblioteca - Zotero", (State.SHOWING,))])
    old = Node("xterm-app", pid=50, children=[Node("Vieja", (State.SHOWING, State.ACTIVE))])
    return Node("desktop", children=[shell, editor, sandboxed, old])


class WindowTests(unittest.TestCase):
    def window(self, focus):
        with patch("leo.a11y._atspi", return_value=Atspi(desktop())):
            app, frame = A11y(focus=focus)._window()
        return app.get_name(), frame.get_name()

    def test_gnome_names_the_window_even_without_the_active_state(self):
        focus = lambda: {"pid": 40, "title": "notas.md - gedit", "protected": False}  # noqa: E731
        self.assertEqual(self.window(focus), ("gedit", "notas.md - gedit"))

    def test_a_sandboxed_app_with_another_pid_is_found_by_its_title(self):
        focus = lambda: {"pid": 7, "title": "Mi biblioteca - Zotero", "protected": False}  # noqa: E731
        self.assertEqual(self.window(focus), ("Zotero", "Mi biblioteca - Zotero"))

    def test_protected_windows_are_never_read(self):
        with self.assertRaises(PermissionError):
            self.window(lambda: {"pid": 40, "title": "notas.md - gedit", "protected": True})

    def test_without_the_extension_the_active_state_still_works(self):
        def missing():
            raise RuntimeError("No FocusWindow in this shell")
        self.assertEqual(self.window(missing), ("xterm-app", "Vieja"))
        self.assertEqual(self.window(None), ("xterm-app", "Vieja"))


class Extents:
    def __init__(self, x, y, w, h):
        self.x, self.y, self.width, self.height = x, y, w, h


class Placed:
    """A node with PARENT extents and a parent, as GTK 4 reports them."""

    def __init__(self, role, extents, parent=None):
        self.role, self.extents, self.parent = role, Extents(*extents), parent

    def get_component_iface(self):
        return self

    def get_extents(self, _coords):
        return self.extents

    def get_parent(self):
        return self.parent

    def get_role(self):
        return self.role


class Roles:
    FRAME, WINDOW, DIALOG, APPLICATION, PANEL = "frame", "window", "dialog", "application", "panel"


class WindowRectTests(unittest.TestCase):
    def test_a_stack_page_offset_is_counted_once(self):
        # Measured in GNOME 50 with tests/guide_app.py: the real button is at (28, 101) from the frame.
        frame = Placed(Roles.FRAME, (0, 0, 460, 300))
        stack = Placed(Roles.PANEL, (0, 37, 460, 263), frame)
        page = Placed(Roles.PANEL, (28, 28, 404, 235), stack)
        box = Placed(Roles.PANEL, (28, 28, 404, 235), page)
        button = Placed(Roles.PANEL, (0, 36, 96, 34), box)
        fake = Atspi(None)
        fake.Role = Roles
        fake.CoordType = type("CoordType", (), {"PARENT": "parent"})
        from leo.a11y import window_rect
        with patch("leo.a11y._atspi", return_value=fake):
            self.assertEqual(window_rect(button), [28, 101, 96, 34])


class MarkedTests(unittest.TestCase):
    """`ui.point` by description: numbered marks on a window capture; the model's number picks the element."""

    def actions(self, answer="2", capture_error=None):
        import gi
        gi.require_version("GdkPixbuf", "2.0")
        from gi.repository import GdkPixbuf
        from unittest.mock import Mock
        from leo.actions import Actions
        image = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8, 400, 300)
        image.fill(0xffffffff)
        png = image.save_to_bufferv("png", [], [])[1]
        a11y = Mock()
        a11y.locate.side_effect = LookupError("No encuentro «la papelera» en la ventana activa")
        a11y.focus.return_value = {"pid": 7, "title": "Iconos", "frame": [100, 50, 400, 300], "buffer": [90, 40, 420, 320]}
        app, frame = Mock(), Mock()
        app.get_process_id.return_value, app.get_name.return_value, frame.get_name.return_value = 7, "iconos", "Iconos"
        a11y.elements.return_value = (app, frame, [
            {"name": "Acción 1", "role": "button", "rect": [20, 20, 64, 64], "origin": "frame"},
            {"name": "Acción 2", "role": "button", "rect": [100, 20, 64, 64], "origin": "frame"}])
        capture = Mock()
        if capture_error:
            capture.capture.side_effect = capture_error
        else:
            capture.capture.return_value = {"png": png, "rect": [100, 50, 400, 300], "scope": "window", "app": "Iconos"}
        vision = Mock()
        vision.model.return_value = "qwen2.5vl:7b"
        vision._look.return_value = (answer, (400, 300))
        vision._box.return_value = (None, "")  # Grounding finds nothing: the numbered marks decide.
        points = []
        return Actions(Mock(), a11y=a11y, vision=vision, capture=capture, point=points.append), points

    def test_the_numbered_element_is_pointed_with_doubt(self):
        actions, points = self.actions()
        actions.vision._look.side_effect = [("El número 2.", None), ("sí", None)]  # Marks: 2; its crop: yes.
        result = actions("ui.point", {"name": "la papelera"})
        self.assertEqual((result["pointed"], result["marks"], result["method"]), ("Acción 2", 2, "verificado"))
        self.assertEqual(result["opinions"], {"grounding": None, "marks": "Acción 2"})
        self.assertEqual((points[0]["rect"], points[0]["origin"], points[0]["doubt"]), ([100, 20, 64, 64], "frame", True))

    def test_no_number_or_no_screen_permission_says_so(self):
        actions, points = self.actions("0")
        with self.assertRaises(LookupError):
            actions("ui.point", {"name": "la papelera"})
        actions, points = self.actions()
        actions.vision._look.side_effect = [("2", None), ("no", None)]  # A number its crop does not confirm.
        with self.assertRaises(LookupError):
            actions("ui.point", {"name": "la papelera"})
        actions, _ = self.actions(capture_error=PermissionError("Habilita Pantalla"))
        with self.assertRaisesRegex(LookupError, "habilita Pantalla"):
            actions("ui.point", {"name": "la papelera"})
        self.assertEqual(points, [])

    def test_grounding_on_an_element_wins(self):
        actions, points = self.actions("1")
        actions.vision._box.return_value = ([10, 30, 20, 20], "")  # Centre (20, 40): inside «Acción 1» in the capture.
        result = actions("ui.point", {"name": "la papelera"})
        self.assertEqual((result["pointed"], result["method"]), ("Acción 1", "acuerdo"))

    def test_a_disagreement_is_settled_by_looking_at_each_crop(self):
        actions, _ = self.actions()
        actions.vision._box.return_value = ([10, 30, 20, 20], "")  # Grounding: «Acción 1».
        actions.vision._look.side_effect = [("2", None), ("no", None), ("sí", None)]  # Marks: 2; crops: 1 no, 2 yes.
        result = actions("ui.point", {"name": "la papelera"})
        self.assertEqual((result["pointed"], result["method"]), ("Acción 2", "verificado"))
