#!/usr/bin/env python3
"""F6.2 ruta directa: «mira…» va al modelo de visión en streaming, sin el agente ni herramientas.

Run: dbus-run-session -- python3 tests/integration_look.py
Daemon real en un bus privado; Ollama simulado (texto fijo) y una captura simulada de GNOME Shell
exportada en ese bus (PNG sintético). Verifica el contrato, no la calidad del modelo de visión.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

import gi
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf, Gio, GLib  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
from fake_ollama import FakeOllama  # noqa: E402
from integration_stream import chat, listen, wait_for_daemon  # noqa: E402
from leo.ipc import Client  # noqa: E402

XML = """<node><interface name="io.github.leo.Capture">
<method name="CaptureScope"><arg name="options" type="s" direction="in"/>
<arg name="png" type="ay" direction="out"/><arg name="info" type="s" direction="out"/></method>
</interface></node>"""


def synthetic_png():
    image = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8, 600, 400)
    image.fill(0x202020ff)
    return image.save_to_bufferv("png", [], [])[1]


class FakeShell:
    """org.gnome.Shell on the private bus with only Leo's capture method."""

    def __init__(self, client):
        self.client, self.calls = client, []  # Keep the private connection alive: it owns the name.
        bus = client.bus
        info = Gio.DBusNodeInfo.new_for_xml(XML).interfaces[0]
        bus.register_object("/io/github/leo/Capture", info, self.handle, None, None)
        reply = bus.call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus", "RequestName",
                              GLib.Variant("(su)", ("org.gnome.Shell", 4)), None, Gio.DBusCallFlags.NONE, 3000, None)
        assert reply.unpack()[0] == 1, "org.gnome.Shell already owned on this private bus"

    def handle(self, _bus, _sender, _path, _interface, _method, parameters, invocation):
        self.calls.append(json.loads(parameters.unpack()[0]))
        info = {"scope": self.calls[-1]["scope"], "rect": [0, 0, 600, 400], "app": "Terminal"}
        invocation.return_value(GLib.Variant("(ays)", (synthetic_png(), json.dumps(info))))


def answer(payload):
    if any(m.get("images") for m in payload["messages"]):
        return {"content": "Veo el código 7391 en la terminal."}
    return {"content": "Respuesta del agente."}


def main():
    with tempfile.TemporaryDirectory(prefix="leo-look-") as directory, FakeOllama(
            models=("qwen3:8b", "qwen2.5vl:7b"), tools=True, script=answer,
            capabilities={"qwen2.5vl:7b": ["completion", "vision"], "qwen3:8b": ["completion", "tools"]}) as ollama:
        env = dict(os.environ, XDG_CONFIG_HOME=directory + "/config", XDG_DATA_HOME=directory + "/data",
                   XDG_CACHE_HOME=directory + "/cache", LEO_OLLAMA_PORT=str(ollama.port), HOME=directory + "/home")
        os.makedirs(directory + "/home/Proyecto/docs")
        subprocess.run(["git", "init", "-q", "-b", "main", directory + "/home/Proyecto"], check=True)
        Path(directory + "/home/Proyecto/docs/progress.md").write_text("## Fase\nPróximo paso: probar a Leo.\n")
        os.environ.update(XDG_CONFIG_HOME=env["XDG_CONFIG_HOME"], XDG_DATA_HOME=env["XDG_DATA_HOME"])
        process = subprocess.Popen([sys.executable, "-m", "leo.daemon"], cwd=ROOT, env=env)
        try:
            owner = Client(private=True)
            wait_for_daemon(owner, process)
            shell = FakeShell(Client(private=True))
            received = listen(owner)

            before = len(ollama.requests)
            reply = chat(owner, {"op": "chat", "prompt": "Leo, mira esta ventana y dime qué código aparece", "request_id": "look-0"})
            assert reply["ok"] and shell.calls == [], ("Sin el permiso Pantalla no hay ruta directa ni captura", reply)
            assert any("tools" in r for r in ollama.requests[before:]), "Sin permiso, sigue el camino del agente"

            assert owner.call({"op": "settings", "changes": {"screen": True}})["ok"]
            before = len(ollama.requests)
            reply = chat(owner, {"op": "chat", "prompt": "Leo, mira esta ventana y dime qué código aparece", "request_id": "look-1"})
            assert reply["ok"] and "7391" in reply["result"], reply
            assert [s["tool"] for s in reply["steps"]] == ["screen.look"] and reply["steps"][0]["status"] == "done", reply
            sent = ollama.requests[before:]
            assert len(sent) == 1 and sent[0]["stream"] and sent[0]["messages"][0]["images"], sent
            assert not any("tools" in r for r in sent), "La ruta directa no pasa por el agente"
            assert sent[0]["model"] == "qwen2.5vl:7b", sent[0]["model"]
            assert shell.calls[-1]["scope"] == "window" and "org.keepassxc.keepassxc" in shell.calls[-1]["protected"]
            GLib.MainContext.default().iteration(False)
            assert "".join(t for rid, t in received if rid == "look-1") == reply["result"], received

            before, calls = len(ollama.requests), len(shell.calls)
            reply = chat(owner, {"op": "chat", "prompt": "Leo, mira las dos pantallas y abre la calculadora", "request_id": "look-2"})
            assert reply["ok"] and any("tools" in r for r in ollama.requests[before:]), "Mirar y actuar va por el agente"
            reply = chat(owner, {"op": "chat", "prompt": "Leo, mira las dos pantallas", "request_id": "look-3"})
            assert reply["ok"] and shell.calls[-1]["scope"] == "all" and len(shell.calls) > calls, shell.calls
            print("PASS: «mira…» va directo al modelo de visión en streaming, sin agente ni herramientas; "
                  "ámbito y ventanas protegidas llegan a GNOME; sin permiso o con acciones sigue el agente")

            # F6.3: a gesture captures once; follow-ups reuse the capture in memory; «Olvidar» drops it.
            calls = len(shell.calls)
            # Calls that capture run the loop: this same process serves the simulated GNOME Shell.
            attached = chat(owner, {"op": "look", "action": "attach", "args": {"scope": "region", "rect": [10, 20, 300, 200]}})
            assert attached["ok"] and attached["attached"]["scope"] == "region" and attached["attached"]["thumbnail"], attached
            assert len(shell.calls) == calls + 1 and shell.calls[-1]["rect"] == [10, 20, 300, 200], shell.calls[-1]
            before = len(ollama.requests)
            reply = chat(owner, {"op": "chat", "prompt": "¿Qué código aparece?", "request_id": "look-4", "remember": True})
            assert reply["ok"] and "7391" in reply["result"] and len(shell.calls) == calls + 1, "Sin nueva captura"
            assert ollama.requests[before]["messages"][0]["images"] and reply["attached"]["expires_in"] <= 120, reply
            reply = chat(owner, {"op": "chat", "prompt": "Leo, abre la calculadora", "request_id": "look-5", "remember": True})
            assert reply["ok"] and len(shell.calls) == calls + 1, "Las acciones no usan la captura recordada"
            assert owner.call({"op": "look", "action": "forget", "args": {}})["forgotten"]
            assert owner.call({"op": "look", "action": "status", "args": {}})["attached"] is None
            before = len(ollama.requests)
            reply = chat(owner, {"op": "chat", "prompt": "¿Qué código aparece?", "request_id": "look-6", "remember": True})
            assert reply["ok"] and not any(m.get("images") for r in ollama.requests[before:] for m in r["messages"])
            reply = chat(owner, {"op": "chat", "prompt": "Leo, mira esta ventana", "request_id": "look-7", "remember": True})
            assert reply["ok"] and reply["attached"]["scope"] == "window", "«mira…» del globo también se recuerda"
            other = Client(private=True)
            assert other.call({"op": "look", "action": "status", "args": {}})["attached"] is None, "Memoria por cliente"
            for bad in ({"scope": "region"}, {"scope": "window", "rect": [0, 0, 50, 50]}, {"scope": "todo"},
                        {"scope": "region", "rect": [0, 0, 2, 2]}):
                assert not owner.call({"op": "look", "action": "attach", "args": bad})["ok"], bad
            assert owner.call({"op": "settings", "changes": {"screen": False}})["ok"]
            reply = chat(owner, {"op": "chat", "prompt": "¿Qué código aparece?", "request_id": "look-8", "remember": True})
            assert not reply["ok"] and owner.call({"op": "look", "action": "status", "args": {}})["attached"] is None, reply
            assert not owner.call({"op": "look", "action": "attach", "args": {"scope": "window"}})["ok"]
            print("PASS: gesto → una captura en memoria; seguimiento sin recapturar; acciones no la usan; Olvidar, "
                  "memoria por cliente y revocación la descartan")

            # Leo atento: posture → scope; only the avatar that watches may ask for an observation right away.
            assert owner.call({"op": "settings", "changes": {"screen": True, "watch": True}})["ok"]
            assert owner.call({"op": "watch", "action": "state", "args": {"mode": "perched"}})["watching"] == "perched"
            calls = len(shell.calls)
            observed = chat(owner, {"op": "watch", "action": "now", "args": {}})
            assert observed["ok"] and observed["observed"]["scope"] == "perched", observed
            assert shell.calls[-1]["scope"] == "perched" and len(shell.calls) == calls + 1, shell.calls[-1]
            assert not other.call({"op": "watch", "action": "now", "args": {}})["ok"]
            status = owner.call({"op": "status"})
            assert status["watching"] == "perched" and isinstance(status["cooking"], list), status
            assert owner.call({"op": "watch", "action": "status", "args": {}})["log"], "Metadatos de lo observado"
            card = owner.call({"op": "watch", "action": "window", "args": {"title": "notas.md — Proyecto — Visual Studio Code"}})
            assert card["ok"] and card["context"]["name"] == "Proyecto" and card["context"]["branch"] == "main", card
            assert card["context"]["next"] == "Próximo paso: probar a Leo.", card
            assert owner.call({"op": "status"})["context"]["name"] == "Proyecto"
            assert other.call({"op": "watch", "action": "window", "args": {"title": "~/Proyecto"}})["context"] is None
            asked = chat(owner, {"op": "chat", "prompt": "Leo, ¿qué ves?", "request_id": "look-9"})
            assert asked["ok"] and asked["tools"] == ["watch.look"], asked
            assert asked["result"].startswith("Estoy observando tu ventana de Terminal (proyecto Proyecto).") and "7391" in asked["result"], asked
            assert shell.calls[-1].get("quiet") is True, "Leo atento captura en silencio: sin destello ni ocultar a Leo"
            # F7.1: each new scene goes to today's diary with its document; a question about an entry carries both.
            project = directory + "/home/Proyecto"
            Path(project + "/notas.md").write_text("El learning_rate del experimento es 0.003.\n")
            assert owner.call({"op": "settings", "changes": {"files": True, "folders": [
                {"id": "proyecto", "path": project, "label": "Proyecto", "write": False}]}})["ok"]
            assert owner.call({"op": "watch", "action": "state", "args": {"mode": "off"}})["ok"]
            assert owner.call({"op": "watch", "action": "state", "args": {"mode": "perched"}})["ok"]
            assert owner.call({"op": "watch", "action": "window", "args": {
                "title": "notas.md — Proyecto — Visual Studio Code", "app": "Code", "ids": ["code"]}})["ok"]
            assert chat(owner, {"op": "watch", "action": "now", "args": {}})["ok"]
            diary = owner.call({"op": "watch", "action": "seen", "args": {}})
            entry = next(e for e in diary["entries"] if (e.get("document") or {}).get("name") == "notas.md")
            assert entry["document"]["path"] == project + "/notas.md" and entry["app"] == "Terminal", entry
            assert not owner.call({"op": "chat", "prompt": "x", "seen": "no-es-un-id"})["ok"]
            before = len(ollama.requests)
            reply = chat(owner, {"op": "chat", "prompt": "¿Qué learning rate usa?", "request_id": "look-seen", "seen": entry["id"]})
            sent = json.dumps(ollama.requests[before:], ensure_ascii=False)
            assert reply["ok"] and "seen.diary" in reply["tools"], reply
            assert "Diario de lo que Leo vio hoy" in sent and "Entrada por la que pregunta" in sent and "0.003" in sent, sent[:500]
            protected = owner.call({"op": "watch", "action": "window", "args": {
                "title": "Banco.kdbx - KeePassXC", "ids": ["org.keepassxc.keepassxc"]}})
            assert protected["ok"] and protected["context"] is None, protected
            assert owner.call({"op": "watch", "action": "state", "args": {"mode": "off"}})["watching"] is None
            asked = chat(owner, {"op": "chat", "prompt": "Leo, ¿qué estás mirando?", "request_id": "look-10"})
            assert asked["ok"] and "estoy quieto" in asked["result"] and asked["tools"] == ["watch.status"], asked
            assert owner.call({"op": "watch", "action": "state", "args": {"mode": "perched"}})["watching"] == "perched"
            assert not owner.call({"op": "watch", "action": "state", "args": {"mode": "todo"}})["ok"]
            assert owner.call({"op": "watch", "action": "state", "args": {"mode": "off"}})["watching"] is None
            assert owner.call({"op": "watch", "action": "clear", "args": {}})["cleared"]
            assert owner.call({"op": "watch", "action": "status", "args": {}})["log"] == []
            print("PASS: Leo atento sobre una ventana captura solo esa ventana; observación inmediata solo del avatar; "
                  "estado con postura y mascotas; proyecto por el título, «¿qué estás mirando?» y borrar registro")
        finally:
            process.terminate()
            process.wait(timeout=10)


if __name__ == "__main__":
    main()
