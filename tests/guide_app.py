"""Disposable GTK wizard for the guide test (F6.7): Siguiente → Acepto las condiciones → Finalizar.

Reaching the last page writes LEO_GUIDE_DONE (if set). Content is fictional.
"""
import os

import gi
gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk  # noqa: E402

app = Gtk.Application(application_id="io.github.leo.TestGuide")


def activate(application):
    window = Gtk.ApplicationWindow(application=application, title="Asistente de prueba", default_width=460, default_height=300)
    stack = Gtk.Stack()

    def page(*widgets):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18, margin_top=28, margin_start=28, margin_end=28)
        for widget in widgets:
            box.append(widget)
        return box
    following = Gtk.Button(label="Siguiente", halign=Gtk.Align.START)
    accept = Gtk.CheckButton(label="Acepto las condiciones")
    finish = Gtk.Button(label="Finalizar", halign=Gtk.Align.START, sensitive=False)
    stack.add_named(page(Gtk.Label(label="Bienvenido al asistente de prueba"), following), "one")
    stack.add_named(page(Gtk.Label(label="Lee las condiciones ficticias"), accept, finish), "two")
    stack.add_named(page(Gtk.Label(label="Configuración terminada")), "three")
    following.connect("clicked", lambda _b: stack.set_visible_child_name("two"))
    accept.connect("toggled", lambda check: finish.set_sensitive(check.get_active()))

    def done(_button):
        stack.set_visible_child_name("three")
        marker = os.environ.get("LEO_GUIDE_DONE")
        if marker:
            with open(marker, "w") as stream:
                stream.write("done\n")
    finish.connect("clicked", done)
    window.set_child(stack)
    window.present()
    GLib.timeout_add_seconds(int(os.environ.get("LEO_GUIDE_SECONDS", "400")), lambda: (window.close(), False)[1])


app.connect("activate", activate)
app.run([])
