#!/usr/bin/env python3
"""Fase 3 sobre D-Bus real con Ollama guionizado y fixtures sintéticos (curso, paper y horario ficticios).

Run: dbus-run-session -- python3 tests/integration_profile.py   (~40 s: espera un aviso de clase real)
"""
from datetime import datetime, timedelta
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


def script_for(home):
    redes, paper = str(home / "Cursos/Redes"), str(home / "Investigacion/paper-atencion.pdf")
    plans = {
        "Recuerda que prefiero respuestas cortas": [calls(("memory_remember", {"text": "Prefiere respuestas cortas"})),
                                                    {"content": "Lo recordaré."}],
        "¿Qué clases tengo hoy?": [calls(("schedule_today", {"when": "hoy"})), {"content": "Tienes Redes Neuronales."}],
        "Resume paper-atencion.pdf": [calls(("files_summarize", {"path": paper, "focus": ""})), {"content": "Resumen listo."}],
        "Prepara la sesión 8 de Redes Neuronales": [
            calls(("files_create_draft", {"folder": redes, "name": "sesion-8.md", "text": "# Sesión 8\n## Objetivos"})),
            {"content": "Borrador creado en Leo-borradores."}],
    }

    def script(payload):
        prompt = payload["messages"][-1]["content"]
        if payload["messages"][-1]["role"] == "user" and prompt.startswith("Resume en 4 a 8 viñetas"):
            return {"content": "- Estudia atención en datos pequeños (p. 1)\n- Precisión 0.91 (p. 3)"}
        messages = payload["messages"]
        last_user = max(i for i, m in enumerate(messages) if m["role"] == "user")
        rounds = sum(1 for m in messages[last_user:] if m["role"] == "tool")
        steps = plans.get(messages[last_user]["content"])
        return steps[min(rounds, len(steps) - 1)] if steps else {"content": "Hola."}
    return script


def main():
    with tempfile.TemporaryDirectory(prefix="leo-profile-") as directory:
        home = Path(directory) / "home"
        for path in ("Cursos/Redes", "Investigacion"):
            (home / path).mkdir(parents=True)
        make_pdf(home / "Investigacion/paper-atencion.pdf", [["Attention for small datasets (ficticio)"],
                                                              ["Method: tiny transformer"], ["Results: accuracy 0.91"]])
        make_pdf(Path(directory) / "horario.pdf", [[
            "Hora           Lunes              Martes             Miércoles          Jueves",
            "08:00-10:00    Redes Neuronales                      Redes Neuronales",
            "               Aula 3.2                              Aula 3.2"]])
        with FakeOllama(tools=True, script=script_for(home), delay=0) as ollama:
            env = dict(os.environ, HOME=str(home), XDG_CONFIG_HOME=str(home / ".config"),
                       XDG_DATA_HOME=str(home / ".local/share"), XDG_CACHE_HOME=str(home / ".cache"),
                       LEO_OLLAMA_PORT=str(ollama.port), GSETTINGS_BACKEND="memory")
            process = subprocess.Popen([sys.executable, "-m", "leo.daemon"], cwd=ROOT, env=env)
            try:
                run(home, Path(directory), process, ollama)
            finally:
                process.terminate()
                process.wait(timeout=10)


def run(home, directory, process, ollama):
    client = Client(private=True)
    wait_for_daemon(client, process)
    folders = [{"id": "redes", "path": str(home / "Cursos/Redes"), "label": "Redes Neuronales", "write": True, "kind": "curso"},
               {"id": "inv", "path": str(home / "Investigacion"), "label": "Investigación", "write": False,
                "kind": "investigacion"}]
    assert client.call({"op": "settings", "changes": {"files": True, "memory": True, "folders": folders}})["ok"]
    plans, cards, events = [], [], []
    decision = {"answer": True}
    client.bus.signal_subscribe(NAME, NAME, "Plan", PATH, None, Gio.DBusSignalFlags.NONE,
                                lambda *args: on_plan(client, args[-1].unpack(), plans, cards, decision))
    client.bus.signal_subscribe(NAME, NAME, "Event", PATH, None, Gio.DBusSignalFlags.NONE,
                                lambda *args: events.append(json.loads(args[-1].unpack()[0])))

    def chat(prompt, request_id):
        loop, reply = GLib.MainLoop(), {}

        def finished(bus, result):
            reply.update(json.loads(bus.call_finish(result).unpack()[0]))
            loop.quit()
        payload = {"op": "chat", "prompt": prompt, "request_id": request_id}
        client.bus.call(NAME, PATH, NAME, "Call", GLib.Variant("(s)", (json.dumps(payload),)),
                        GLib.VariantType.new("(s)"), Gio.DBusCallFlags.NONE, 60000, None, finished)
        GLib.timeout_add_seconds(60, loop.quit)
        loop.run()
        return reply

    reply = chat("Recuerda que prefiero respuestas cortas", "m1")
    assert reply["ok"] and reply["steps"][0]["status"] == "done" and cards[-1]["tool"] == "memory_remember", reply
    facts = client.call({"op": "memory", "action": "list", "args": {}})["facts"]
    assert [f["text"] for f in facts] == ["Prefiere respuestas cortas"], facts
    chat("Hola", "m2")
    assert "Prefiere respuestas cortas" in ollama.requests[-1]["messages"][0]["content"]
    print("PASS: memoria propuesta por el modelo, guardada solo tras aprobar e incluida en el prompt")

    imported = client.call({"op": "schedule", "action": "import", "args": {"path": str(directory / "horario.pdf")}})
    assert [c["name"] for c in imported["courses"]] == ["Redes Neuronales", "Redes Neuronales"], imported
    assert not client.call({"op": "schedule", "action": "get", "args": {}})["courses"], "Import must not save"
    now = datetime.now()
    soon = now + timedelta(minutes=10)
    if soon.date() != now.date():
        soon = now.replace(hour=23, minute=59)
    course = {**imported["courses"][0], "day": now.weekday(), "start": f"{soon:%H:%M}",
              "end": "23:59", "folder": "redes"}
    if course["start"] >= course["end"]:
        print("SKIP: aviso de clase (demasiado cerca de medianoche)")
    else:
        saved = client.call({"op": "schedule", "action": "set", "args": {"courses": [course]}})
        assert saved["ok"] and saved["courses"][0]["folder"] == "redes", saved
        end = time.monotonic() + 40
        context = GLib.MainContext.default()
        while not any(e.get("source") == "schedule" for e in events) and time.monotonic() < end:
            context.iteration(False)
            time.sleep(.05)
        alert = next(e for e in events if e.get("source") == "schedule")
        assert alert["title"].startswith("En ") and "Redes Neuronales en el salón Aula 3.2. ¿Abro los materiales?" in alert["title"]
        reply = chat("¿Qué clases tengo hoy?", "s1")
        tool = [m["content"] for m in ollama.requests[-1]["messages"] if m["role"] == "tool"][-1]
        assert reply["steps"][0]["status"] == "done" and str(home / "Cursos/Redes") in tool, tool
        print("PASS: horario importado sin guardar, confirmado, aviso de clase real por Event y schedule_today con carpeta")

    reply = chat("Resume paper-atencion.pdf", "d1")
    tool = [m["content"] for m in ollama.requests[-1]["messages"] if m["role"] == "tool"][-1]
    assert reply["steps"][0]["status"] == "done" and "(p. 3)" in tool and tool.startswith("Contenido externo"), tool
    reply = chat("Prepara la sesión 8 de Redes Neuronales", "d2")
    assert reply["steps"][0]["status"] == "done", reply
    assert (home / "Cursos/Redes/Leo-borradores/sesion-8.md").read_text().startswith("# Sesión 8")
    assert sorted(p.name for p in (home / "Cursos/Redes").iterdir()) == ["Leo-borradores"]
    print("PASS: resumen por páginas con citas y borrador de sesión confinado a Leo-borradores/ tras aprobar")


if __name__ == "__main__":
    main()
