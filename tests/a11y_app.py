"""Disposable GTK app for the accessibility tests: a button, a password field and a status label.

Clicking the 48×48 button named «Pulsar prueba» (by pointer or AT-SPI action) writes LEO_A11Y_CLICKED (if set) and changes
the label. The password is a fixed fictional value that must never appear in Leo's reading.
"""
import os
import sys

import gi
gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk  # noqa: E402

app = Gtk.Application(application_id="io.github.leo.TestA11y")


def activate(application):
    window = Gtk.ApplicationWindow(application=application, title="Leo A11y Prueba", default_width=420, default_height=260)
    content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16, margin_top=24, margin_start=24, margin_end=24)
    status = Gtk.Label(label="Estado: sin pulsar")
    secret = Gtk.PasswordEntry(text="clave-ficticia-123")
    secret.update_property([Gtk.AccessibleProperty.LABEL], ["Contraseña de prueba"])
    # 48×48 icon button: a click at the pointed spot hits it only if the error is under 24 px per axis.
    button = Gtk.Button(icon_name="document-print-symbolic")
    button.update_property([Gtk.AccessibleProperty.LABEL], ["Pulsar prueba"])
    button.set_size_request(48, 48)
    button.set_halign(Gtk.Align.START)
    button.set_valign(Gtk.Align.START)

    def clicked(_button):
        status.set_label("Estado: pulsado")
        marker = os.environ.get("LEO_A11Y_CLICKED")
        if marker:
            with open(marker, "a") as stream:
                stream.write("clicked\n")
    button.connect("clicked", clicked)
    for widget in (status, secret, button):
        content.append(widget)
    window.set_child(content)
    window.present()
    GLib.timeout_add_seconds(int(os.environ.get("LEO_A11Y_SECONDS", "60")), lambda: (window.close(), False)[1])


app.connect("activate", activate)
app.run(sys.argv[:1])
