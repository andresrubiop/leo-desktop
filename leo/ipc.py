"""Protocolo D-Bus de sesión; solo datos JSON, nunca código."""
import json
from gi.repository import Gio, GLib

NAME = "io.github.leo.Companion"
PATH = "/io/github/leo/Companion"
XML = f'''<node><interface name="{NAME}">
<method name="Call"><arg type="s" direction="in" name="request"/>
<arg type="s" direction="out" name="response"/></method>
<signal name="Chunk"><arg type="s" name="request_id"/><arg type="s" name="text"/></signal>
<signal name="Event"><arg type="s" name="event"/></signal>
<signal name="Plan"><arg type="s" name="request_id"/><arg type="s" name="plan"/></signal>
<signal name="Point"><arg type="s" name="target"/></signal>
</interface></node>'''
REQUEST_ID = r"[A-Za-z0-9_-]{1,64}"


def decode(raw):
    if len(raw) > 64000:
        raise ValueError("Petición demasiado grande")
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("Se esperaba un objeto JSON")
    return value


class Client:
    def __init__(self, private=False):
        self.private = private
        self.closed = False
        self.cancel = Gio.Cancellable()
        self.bus = (Gio.DBusConnection.new_for_address_sync(
            Gio.dbus_address_get_for_bus_sync(Gio.BusType.SESSION, None),
            Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION,
            None, None) if private else Gio.bus_get_sync(Gio.BusType.SESSION, None))

    def close(self):
        self.closed = True
        self.cancel.cancel()
        if self.private and not self.bus.is_closed():
            self.bus.close_sync(None)

    def call(self, payload, timeout=240000):
        reply = self.bus.call_sync(NAME, PATH, NAME, "Call", GLib.Variant("(s)", (json.dumps(payload),)),
                                   GLib.VariantType.new("(s)"), Gio.DBusCallFlags.NONE, timeout, None)
        return json.loads(reply.unpack()[0])

    def call_async(self, payload, callback):
        if self.closed:
            return
        def finished(bus, result):
            try:
                response = json.loads(bus.call_finish(result).unpack()[0])
            except Exception as exc:
                response = {"ok": False, "error": str(exc)}
            if not self.closed:
                callback(response)
        self.bus.call(NAME, PATH, NAME, "Call", GLib.Variant("(s)", (json.dumps(payload),)),
                      GLib.VariantType.new("(s)"), Gio.DBusCallFlags.NONE, 240000, self.cancel, finished)
