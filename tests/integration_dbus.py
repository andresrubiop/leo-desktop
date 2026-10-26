#!/usr/bin/env python3
"""Run under dbus-run-session. Writes only to temporary XDG directories."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from leo.ipc import Client, NAME
from gi.repository import Gio, GLib


def main():
    with tempfile.TemporaryDirectory(prefix="leo-integration-") as directory:
        os.environ["XDG_CONFIG_HOME"] = directory + "/config"
        os.environ["XDG_DATA_HOME"] = directory + "/data"
        process = subprocess.Popen([sys.executable, "-m", "leo.daemon"], cwd=ROOT)
        try:
            client = Client()
            for _ in range(60):
                try:
                    # Wait for our isolated daemon without activating an installed
                    # service that could inherit the user's normal XDG paths.
                    owned = client.bus.call_sync(
                        "org.freedesktop.DBus", "/org/freedesktop/DBus",
                        "org.freedesktop.DBus", "NameHasOwner",
                        GLib.Variant("(s)", (NAME,)), None,
                        Gio.DBusCallFlags.NONE, 3000, None).unpack()[0]
                    if not owned:
                        raise RuntimeError("Daemon not ready")
                    status = client.call({"op": "status"})
                    break
                except Exception:
                    if process.poll() is not None: raise RuntimeError("Daemon exited")
                    time.sleep(.1)
            else:
                raise TimeoutError("Daemon did not acquire bus name")
            assert status["ok"] and not status["settings"]["screen"]
            assert not client.call({"op": "settings", "changes": {"codex": True}})["ok"]
            assert not client.call({"op": "settings", "changes": {"claude": True}})["ok"]
            for provider in ("codex", "claude"):
                denied = client.call({"op": "tool", "name": "remote.ask", "args": {"provider": provider, "prompt": "hola"}})
                assert not denied["ok"] and "LLM local" in denied["error"]
            position = {"monitor": 1, "x": .25, "y": .6}
            assert client.call({"op": "settings", "changes": {"roam": True, "position": position}})["ok"]
            assert client.call({"op": "status"})["settings"]["position"] == position
            blocked = client.call({"op": "tool", "name": "screen.capture", "args": {}})
            assert not blocked["ok"]
            unknown = client.call({"op": "shell", "command": "ls"})
            assert not unknown["ok"]
            assert client.call({"op": "settings", "changes": {"files": True, "commands": True}})["ok"]
            req = client.call({"op": "tool", "name": "note.save", "args": {"name": "integration", "text": "real D-Bus"}})
            assert req["ok"] and req["approval"]
            assert not (Path(directory) / "data/leo/notes/integration.md").exists()
            done = client.call({"op": "decide", "token": req["approval"], "approved": True})
            assert done["ok"] and Path(done["result"]).read_text() == "real D-Bus"
            assert Path(done["result"]) == Path(directory) / "data/leo/notes/integration.md"
            assert not client.call({"op": "decide", "token": req["approval"], "approved": True})["ok"]
            req = client.call({"op": "tool", "name": "system.command", "args": {"command": "uptime"}})
            done = client.call({"op": "decide", "token": req["approval"], "approved": True})
            assert done["ok"] and done["result"].strip()
            assert client.call({"op": "status"})["state"] == "idle"
            # F7.3: the AI Usage Widget's view, instant (never waits for the network); bad arguments refused.
            # Live quotas off first: this daemon shares the real HOME, and a test never queries the user's accounts.
            assert client.call({"op": "settings", "changes": {"usage_live": False}})["ok"]
            view = client.call({"op": "ai", "action": "snapshot", "args": {"width": 66}})  # Warms up the monitor.
            started = time.monotonic()
            view = client.call({"op": "ai", "action": "snapshot", "args": {"width": 66}})
            assert view["ok"] and time.monotonic() - started < 1, "La vista de uso debe ser instantánea"
            assert view["live"] is False and any("SISTEMA" in "".join(t for t, _ in line) for line in view["lines"]), \
                "Vista de uso sin la sección del sistema"
            assert not client.call({"op": "ai", "action": "snapshot", "args": {"width": 500}})["ok"]
            gray = client.call({"op": "ai", "action": "grayscale", "args": {}})  # Query only: never touch the real dconf.
            assert gray["ok"] and isinstance(gray["grayscale"], bool), gray
            print("PASS: D-Bus real, daemon, permisos, aprobación, nota exclusiva, replay denegado, diagnóstico fijo, posición y rechazo de proveedores remotos")
        finally:
            process.terminate()
            process.wait(timeout=10)
        from portal_contract import run
        run()


if __name__ == "__main__":
    main()
