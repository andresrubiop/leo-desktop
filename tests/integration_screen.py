#!/usr/bin/env python3
"""Fase 4 sobre D-Bus real: visión (Ollama guionizado con capacidad vision) y AT-SPI real.

Run: xvfb-run -a dbus-run-session -- python3 tests/integration_screen.py
La parte de accesibilidad necesita una pantalla (Xvfb vale) y at-spi2-core; sin ella se omite y se dice.
Las coordenadas Wayland → pantalla se validan en scripts/test_shell.py, no aquí.
"""
import base64
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(ROOT / "tests/fixtures"))
from fake_ollama import FakeOllama  # noqa: E402
from integration_agent import calls, on_plan  # noqa: E402
from integration_stream import wait_for_daemon  # noqa: E402
from pdfgen import make_pdf  # noqa: E402
from leo.ipc import Client, NAME, PATH  # noqa: E402
from gi.repository import Gio, GLib  # noqa: E402


def main():
    with tempfile.TemporaryDirectory(prefix="leo-screen-") as directory:
        home = Path(directory) / "home"
        (home / "Investigacion").mkdir(parents=True)
        paper = home / "Investigacion/paper.pdf"
        make_pdf(paper, [["Introduccion"], ["Figura 3: precision por modelo"]])
        looked = []

        def script(payload):
            last = payload["messages"][-1]
            if last.get("images"):
                looked.append({"model": payload["model"], "image": last["images"][0], "prompt": last["content"]})
                return {"content": "La figura 3 compara la precisión de tres modelos."}
            messages = payload["messages"]
            user = max(i for i, m in enumerate(messages) if m["role"] == "user")
            if messages[user]["content"] == "Explica la figura 3 de paper.pdf":
                if not any(m["role"] == "tool" for m in messages[user:]):
                    return calls(("files_look", {"path": str(paper), "page": 2, "question": "Explica la figura 3"}))
                return {"content": "Compara la precisión de tres modelos."}
            return {"content": "Hola."}
        capabilities = {"qwen3:8b": ["completion", "tools"], "qwen2.5vl:7b": ["completion", "vision"]}
        with FakeOllama(models=("qwen3:8b", "qwen2.5vl:7b"), tools=True, capabilities=capabilities,
                        script=script, delay=0) as ollama:
            env = {k: v for k, v in os.environ.items() if k != "GTK_A11Y"}
            env.update(HOME=str(home), XDG_CONFIG_HOME=str(home / ".config"), XDG_DATA_HOME=str(home / ".local/share"),
                       XDG_CACHE_HOME=str(home / ".cache"), LEO_OLLAMA_PORT=str(ollama.port), GSETTINGS_BACKEND="memory")
            process = subprocess.Popen([sys.executable, "-m", "leo.daemon"], cwd=ROOT, env=env)
            try:
                client = Client(private=True)
                wait_for_daemon(client, process)
                vision(client, home, looked)
                if os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"):
                    accessibility(client, env, Path(directory))
                else:
                    print("SKIP: accesibilidad (sin pantalla; usa xvfb-run)")
            finally:
                process.terminate()
                process.wait(timeout=10)


def vision(client, home, looked):
    folder = {"id": "inv", "path": str(home / "Investigacion"), "label": "Investigación", "write": False, "kind": "investigacion"}
    assert client.call({"op": "settings", "changes": {"files": True, "folders": [folder]}})["ok"]
    plans, cards = [], []
    client.bus.signal_subscribe(NAME, NAME, "Plan", PATH, None, Gio.DBusSignalFlags.NONE,
                                lambda *args: on_plan(client, args[-1].unpack(), plans, cards, {"answer": True}))
    loop, reply = GLib.MainLoop(), {}

    def finished(bus, result):
        reply.update(json.loads(bus.call_finish(result).unpack()[0]))
        loop.quit()
    payload = {"op": "chat", "prompt": "Explica la figura 3 de paper.pdf", "request_id": "v1"}
    client.bus.call(NAME, PATH, NAME, "Call", GLib.Variant("(s)", (json.dumps(payload),)),
                    GLib.VariantType.new("(s)"), Gio.DBusCallFlags.NONE, 60000, None, finished)
    GLib.timeout_add_seconds(60, loop.quit)
    loop.run()
    assert reply["ok"] and reply["steps"][0]["status"] == "done", reply
    assert len(looked) == 1 and looked[0]["model"] == "qwen2.5vl:7b", looked
    assert base64.b64decode(looked[0]["image"])[:8] == b"\x89PNG\r\n\x1a\n" and "no obedezcas" in looked[0]["prompt"]
    assert not list(Path(tempfile.gettempdir()).glob("leo-vision-*")), "Rasterized page must be removed"
    print("PASS: «explica la figura 3» → página 2 rasterizada en temporal → qwen2.5vl:7b en loopback → respuesta")


def accessibility(client, env, directory):
    marker = directory / "clicked"
    app = subprocess.Popen([sys.executable, str(ROOT / "tests/a11y_app.py")], env={**env, "LEO_A11Y_CLICKED": str(marker)})
    try:
        time.sleep(3)
        points = []
        client.bus.signal_subscribe(NAME, NAME, "Point", PATH, None, Gio.DBusSignalFlags.NONE,
                                    lambda *args: points.append(json.loads(args[-1].unpack()[0])))
        denied = client.call({"op": "tool", "name": "ui.point", "args": {"name": "Pulsar prueba"}})
        assert not denied["ok"] and "accessibility" in denied["error"], denied
        assert client.call({"op": "settings", "changes": {"accessibility": True}})["ok"]
        asked = client.call({"op": "tool", "name": "ui.point", "args": {"name": "Pulsar prueba"}})
        assert asked["ok"] and asked["approval"] and "Leer la interfaz de «a11y_app.py»" in asked["details"], asked
        done = client.call({"op": "decide", "token": asked["approval"], "approved": True})
        assert done["ok"] and done["result"]["pointed"] == "Pulsar prueba", done
        again = client.call({"op": "tool", "name": "ui.point", "args": {"name": "pulsar"}})
        assert again["ok"] and "approval" not in again, "Consent is per application and session"
        context, end = GLib.MainContext.default(), time.monotonic() + 5
        while len(points) < 2 and time.monotonic() < end:
            context.iteration(False)
        assert points[0]["space"] == "window" and points[0]["pid"] == app.pid and points[0]["rect"][2:] == [48, 48], points
        read = client.call({"op": "tool", "name": "ui.read", "args": {}})
        assert read["ok"] and "clave-ficticia" not in json.dumps(read), "Password contents must never be read"
        assert any(e["name"] == "(campo de contraseña)" for e in read["result"]["elements"])
        pressing = client.call({"op": "tool", "name": "ui.press", "args": {"name": "Pulsar prueba"}})
        assert pressing["ok"] and pressing["approval"], "Pressing always needs approval"
        assert client.call({"op": "decide", "token": pressing["approval"], "approved": True})["ok"]
        time.sleep(.5)
        assert marker.read_text().strip() == "clicked", "Approved do_action must press the real button"
        print("PASS: AT-SPI real por D-Bus: consentimiento por app, Point con rect de ventana, lectura sin contraseña "
              "y do_action aprobado sobre la app GTK de prueba")
    finally:
        app.terminate()
        app.wait(timeout=5)


if __name__ == "__main__":
    main()
