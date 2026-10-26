"""Disposable GTK application launched by the conversation integration test."""
import gi
gi.require_version("Gtk", "4.0")
from gi.repository import Gtk, GLib

app = Gtk.Application(application_id="io.github.leo.TestOpen")


def activate(application):
    window = Gtk.ApplicationWindow(application=application, title="Leo TestOpen", default_width=300, default_height=180)
    window.set_child(Gtk.Label(label="Aplicación abierta desde la conversación de Leo"))
    window.present()
    GLib.timeout_add_seconds(20, lambda: (window.close(), False)[1])


app.connect("activate", activate)
app.run([])
