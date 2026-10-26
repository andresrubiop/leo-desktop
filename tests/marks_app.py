"""Disposable GTK window for the numbered-marks battery (F6.7): 23 icon buttons whose accessible names say nothing
about the icon («Acción N», shuffled), so only the picture can tell them apart."""
import os
import random
import sys

import gi
gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk  # noqa: E402

sys.path.insert(0, os.path.dirname(__file__))

from marks_names import ICONS, names  # noqa: E402


app = Gtk.Application(application_id="io.github.leo.TestMarks")


def activate(application):
    window = Gtk.ApplicationWindow(application=application, title="Leo Marcas Prueba", default_width=620, default_height=420)
    grid = Gtk.Grid(row_spacing=14, column_spacing=14, margin_top=20, margin_start=20, margin_end=20, margin_bottom=20)
    labels = names()
    order = list(ICONS)
    random.Random(11).shuffle(order)  # Grid position unrelated to the names.
    for index, icon in enumerate(order):
        button = Gtk.Button(icon_name=icon + "-symbolic")
        button.get_child().set_pixel_size(32)
        button.update_property([Gtk.AccessibleProperty.LABEL], [labels[icon]])
        button.set_size_request(64, 64)
        grid.attach(button, index % 6, index // 6, 1, 1)
    window.set_child(grid)
    window.present()
    GLib.timeout_add_seconds(int(os.environ.get("LEO_MARKS_SECONDS", "900")), lambda: (window.close(), False)[1])


if __name__ == "__main__":
    app.connect("activate", activate)
    app.run([])
