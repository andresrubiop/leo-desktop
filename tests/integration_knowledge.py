#!/usr/bin/env python3
"""Cerveau (antes ContextOS) REAL + daemon/D-Bus reales, HOME temporal y documentos sintéticos.

dbus-run-session -- python3 tests/integration_knowledge.py [--real-model]
Por defecto el LLM es guionizado; --real-model también comprueba el chat con Ollama real.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(ROOT / "tests/fixtures"))
from fake_ollama import FakeOllama
from integration_agent import calls
from integration_stream import wait_for_daemon
from leo.ipc import Client
from pdfgen import make_pdf


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--real-model", action="store_true")
    args = parser.parse_args()
    executable = os.environ.get("LEO_CERVEAU_CLI") or str(Path.home() / "Cerveau/.venv/bin/cerveau")
    if not Path(executable).is_file():
        raise SystemExit("Falta Cerveau real; indica LEO_CERVEAU_CLI")
    with tempfile.TemporaryDirectory(prefix="leo-cerveau-") as directory:
        home = Path(directory) / "home"
        course, other = home / "Cursos/Redes", home / "NoAutorizado"
        course.mkdir(parents=True)
        other.mkdir()
        (course / "sesion.md").write_text("# Curso ficticio\nLa retropropagacion calcula gradientes mediante la regla de la cadena.\n")
        make_pdf(course / "clase.pdf", [["Documento ficticio de la primera pagina"],
                                        ["Segunda pagina sobre retropropagacion y gradientes"]])
        (other / "privado.md").write_text("retropropagacion FUERA_DEL_CURSO\n")
        env = dict(os.environ, HOME=str(home), XDG_CONFIG_HOME=str(home / ".config"),
                   XDG_DATA_HOME=str(home / ".local/share"), XDG_CACHE_HOME=str(home / ".cache"),
                   CERVEAU_HOME=str(home), LEO_CERVEAU_CLI=executable, GSETTINGS_BACKEND="memory")
        subprocess.run([executable, "--home", str(home), "init"], env=env, capture_output=True, check=True)
        subprocess.run([executable, "--home", str(home), "project", "add", "Otro", str(other),
                        "--id", "otro", "--kind", "directory"], env=env, capture_output=True, check=True)
        subprocess.run([executable, "--home", str(home), "index", "--project", "otro"],
                       env=env, capture_output=True, check=True)

        def script(payload):
            messages = payload["messages"]
            if any(m["role"] == "tool" for m in messages):
                return {"content": "La retropropagacion usa la regla de la cadena (sesion.md, línea 2)."}
            return calls(("knowledge_search", {"query": "retropropagacion"}))

        with FakeOllama(models=("qwen3:8b",), tools=True, script=script, delay=0) as ollama:
            check(env | {"LEO_OLLAMA_PORT": str(ollama.port)}, course, ollama)
        if args.real_model:
            check(env, course, None)


def check(env, course, ollama):
    process = subprocess.Popen([sys.executable, "-m", "leo.daemon"], cwd=ROOT, env=env,
                               stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    client = Client(private=True)
    try:
        wait_for_daemon(client, process)
        status = client.call({"op": "knowledge", "action": "status", "args": {}})
        assert status["ok"] and status["available"] and status["initialized"] and status["authorized_folders"] == 0, status
        folder = {"id": "redes", "path": str(course), "label": "Redes ficticias", "write": False, "kind": "curso"}
        assert client.call({"op": "settings", "changes": {"files": True, "folders": [folder]}})["ok"]
        indexed = client.call({"op": "knowledge", "action": "index", "args": {}})
        assert indexed["ok"] and indexed["indexed_folders"] == 1 and indexed["registered_folders"] == 1, indexed
        found = client.call({"op": "tool", "name": "knowledge.search", "args": {"query": "retropropagacion"}})
        assert found["ok"] and found["result"]["results"], found
        results = found["result"]["results"]
        assert all(Path(r["path"]).is_relative_to(course) for r in results)
        assert any("regla de la cadena" in r["snippet"] and r["start_line"] > 0 for r in results), results
        # Cerveau indexes PDF text; Leo cites the page, never the lines of the extracted text.
        assert any(r["path"].endswith("clase.pdf") and "Segunda pagina" in r["snippet"] and r["page"] == 2
                   and "start_line" not in r for r in results), results
        reply = client.call({"op": "chat", "prompt": "Usa knowledge_search para buscar retropropagacion en mis cursos. "
                             "Responde según el fragmento encontrado y cita sus líneas."})
        assert reply["ok"] and any(s["tool"] == "knowledge.search" and s["status"] == "done" for s in reply["steps"]), reply
        if ollama:
            tools = [m["content"] for m in ollama.requests[-1]["messages"] if m["role"] == "tool"]
            assert tools and tools[-1].startswith("Contenido externo") and "FUERA_DEL_CURSO" not in tools[-1]
        else:
            assert "cadena" in reply["result"].lower(), reply
        assert client.call({"op": "settings", "changes": {"folders": []}})["ok"]
        denied = client.call({"op": "tool", "name": "knowledge.search", "args": {"query": "retropropagacion"}})
        assert not denied["ok"] and "autorizadas" in denied["error"], denied
        print("PASS: Cerveau real → índice/consulta solo del curso autorizado → citas de líneas y del PDF por página → "
              + ("LLM guionizado" if ollama else "Ollama REAL") + " → revocación", flush=True)
    finally:
        client.close()
        process.terminate()
        process.communicate(timeout=15)


if __name__ == "__main__":
    main()
