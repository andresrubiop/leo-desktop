"""Portal protocol contract on a PRIVATE bus; this is not a real desktop capture."""
import threading
from gi.repository import Gio, GLib
from leo.portals import Portals, DEST, PATH

XML = '''<node>
<interface name="org.freedesktop.portal.Screenshot"><method name="Screenshot">
<arg type="s" direction="in"/><arg type="a{sv}" direction="in"/><arg type="o" direction="out"/>
</method></interface>
<interface name="org.freedesktop.portal.RemoteDesktop">
<method name="CreateSession"><arg type="a{sv}" direction="in"/><arg type="o" direction="out"/></method>
<method name="SelectDevices"><arg type="o" direction="in"/><arg type="a{sv}" direction="in"/><arg type="o" direction="out"/></method>
<method name="Start"><arg type="o" direction="in"/><arg type="s" direction="in"/><arg type="a{sv}" direction="in"/><arg type="o" direction="out"/></method>
<method name="NotifyPointerMotion"><arg type="o" direction="in"/><arg type="a{sv}" direction="in"/><arg type="d" direction="in"/><arg type="d" direction="in"/></method>
</interface></node>'''
SESSION_XML = '<node><interface name="org.freedesktop.portal.Session"><method name="Close"/></interface></node>'


def run():
    bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    acquired = bus.call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus", "RequestName",
                            GLib.Variant("(su)", (DEST, 4)), None, Gio.DBusCallFlags.NONE, 2000, None).unpack()[0]
    assert acquired == 1, "Run only on a private bus without a real portal"
    calls, errors = [], []
    mode = {"cancel": False}
    session = PATH + "/session/leo_test/session"
    def handle(_bus, sender, _path, _interface, method, parameters, invocation):
        args = parameters.unpack()
        calls.append((method, args))
        if method in {"NotifyPointerMotion", "Close"}:
            invocation.return_value(GLib.Variant("()", ()))
            return
        options = args[-1]
        token = options["handle_token"]
        handle_path = PATH + "/request/" + sender[1:].replace(".", "_") + "/" + token
        invocation.return_value(GLib.Variant("(o)", (handle_path,)))
        data = {"Screenshot": {"uri": GLib.Variant("s", "file:///tmp/test-only.png")},
                "CreateSession": {"session_handle": GLib.Variant("s", session)},
                "SelectDevices": {}, "Start": {"devices": GLib.Variant("u", 2)}}[method]
        def respond():
            bus.emit_signal(sender, handle_path, "org.freedesktop.portal.Request", "Response",
                            GLib.Variant("(ua{sv})", (1 if mode["cancel"] and method == "Start" else 0, data)))
            return GLib.SOURCE_REMOVE
        GLib.timeout_add(10, respond)
    registrations = [bus.register_object(PATH, info, handle, None, None)
                     for info in Gio.DBusNodeInfo.new_for_xml(XML).interfaces]
    registrations.append(bus.register_object(session, Gio.DBusNodeInfo.new_for_xml(SESSION_XML).interfaces[0], handle, None, None))
    loop = GLib.MainLoop()
    def test():
        try:
            portals = Portals(bus)
            assert portals.screenshot()["uri"] == "file:///tmp/test-only.png"
            assert calls[-1][1][-1]["interactive"] is True
            portals.move_pointer(40, 0)
            assert [name for name, args in calls[-5:]] == ["CreateSession", "SelectDevices", "Start", "NotifyPointerMotion", "Close"]
            assert calls[-4][1][-1]["types"] == 2 and calls[-4][1][-1]["persist_mode"] == 0
            mode["cancel"] = True
            try:
                portals.move_pointer(40, 0)
                raise AssertionError("Cancelled portal accepted")
            except PermissionError:
                pass
            assert calls[-1][0] == "Close" and calls[-2][0] == "Start"
        except BaseException as exc:
            errors.append(exc)
        finally:
            GLib.idle_add(lambda: (loop.quit(), False)[1])
    thread = threading.Thread(target=test)
    thread.start()
    loop.run()
    thread.join()
    for registration in registrations: bus.unregister_object(registration)
    if errors: raise errors[0]
    print("PASS: contrato de portales simulado sobre D-Bus real; interactivo, puntero limitado, cancelación y cierre")
