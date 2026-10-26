"""Portales Wayland. Invocar desde workers mientras corre el main loop de GLib."""
import secrets
import threading
from gi.repository import Gio, GLib

DEST = "org.freedesktop.portal.Desktop"
PATH = "/org/freedesktop/portal/desktop"


class Portals:
    def __init__(self, bus):
        self.bus = bus
        self.lock = threading.Lock()

    def _call(self, interface, method, signature, values, path=PATH):
        return self.bus.call_sync(DEST, path, "org.freedesktop.portal." + interface, method,
                                  GLib.Variant(signature, values), None,
                                  Gio.DBusCallFlags.NONE, 10000, None)

    def _request(self, interface, method, signature, values):
        token = "leo_" + secrets.token_hex(12)
        options = values[-1] | {"handle_token": GLib.Variant("s", token)}
        values = (*values[:-1], options)
        sender = self.bus.get_unique_name()[1:].replace(".", "_")
        path = f"{PATH}/request/{sender}/{token}"
        done, result = threading.Event(), []

        def response(_bus, _sender, _path, _iface, _signal, parameters):
            result.append(parameters.unpack())
            done.set()

        subscription = self.bus.signal_subscribe(DEST, "org.freedesktop.portal.Request", "Response",
                                                  path, None, Gio.DBusSignalFlags.NONE, response)
        try:
            handle = self._call(interface, method, signature, values).unpack()[0]
            if handle != path:
                self._call("Request", "Close", "()", (), path=handle)
                raise RuntimeError("El portal devolvió un identificador inesperado")
            if not done.wait(90):
                self._call("Request", "Close", "()", (), path=path)
                raise TimeoutError("El diálogo del portal expiró")
            code, data = result[0]
            if code != 0:
                raise PermissionError("Captura o control cancelado en el portal")
            return data
        finally:
            self.bus.signal_unsubscribe(subscription)

    def screenshot(self):
        with self.lock:
            data = self._request("Screenshot", "Screenshot", "(sa{sv})",
                                 ("", {"interactive": GLib.Variant("b", True)}))
            return {"uri": data["uri"], "message": "Captura local creada por el portal. No se envió a ningún modelo."}

    def move_pointer(self, dx, dy):
        with self.lock:
            data = self._request("RemoteDesktop", "CreateSession", "(a{sv})",
                                 ({"session_handle_token": GLib.Variant("s", "leo_" + secrets.token_hex(12))},))
            session = data["session_handle"]
            try:
                self._request("RemoteDesktop", "SelectDevices", "(oa{sv})",
                              (session, {"types": GLib.Variant("u", 2), "persist_mode": GLib.Variant("u", 0)}))
                selected = self._request("RemoteDesktop", "Start", "(osa{sv})", (session, "", {}))
                if not selected.get("devices", 0) & 2:
                    raise PermissionError("El portal no concedió permiso para el puntero")
                self._call("RemoteDesktop", "NotifyPointerMotion", "(oa{sv}dd)", (session, {}, float(dx), float(dy)))
                return "Puntero movido. Sesión de control cerrada."
            finally:
                self._call("Session", "Close", "()", (), path=session)
