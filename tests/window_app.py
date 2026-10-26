"""Ventana GTK desechable para el test aislado: Leo vive dentro de ella y la observa (argv: título, texto).
Se cierra sola a los 3 min."""
import sys

import gi
gi.require_version("Gtk", "4.0")
from gi.repository import Gtk, GLib

app = Gtk.Application(application_id="io.github.leo.TestWindow")


def activate(application):
    title = sys.argv[1] if len(sys.argv) > 1 else "Leo Ventana de prueba"
    window = Gtk.ApplicationWindow(application=application, title=title, default_width=700, default_height=420)
    text = sys.argv[2] if len(sys.argv) > 2 else "Ventana de prueba para Leo atento"
    shown = Gtk.Label()
    shown.set_markup(f'<span font_family="monospace" size="xx-large">{GLib.markup_escape_text(text)}</span>')
    window.set_child(shown)
    window.present()
    GLib.timeout_add_seconds(180, lambda: (window.close(), False)[1])


app.connect("activate", activate)
app.run(sys.argv[:1])
