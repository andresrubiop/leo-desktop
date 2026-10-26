#!/usr/bin/env python3
"""Streaming over a real private D-Bus with the real daemon and a simulated Ollama.

Run: dbus-run-session -- python3 tests/integration_stream.py
Checks the transport contract only (unicast Chunk signals, final reply, status while busy,
num_ctx and default model); answer quality needs the real local model (scripts/eval.py).
"""
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
from fake_ollama import ANSWER, FakeOllama  # noqa: E402
from leo.ipc import Client, NAME, PATH  # noqa: E402
from gi.repository import Gio, GLib  # noqa: E402


def wait_for_daemon(client, process):
    for _ in range(100):
        try:
            owned = client.bus.call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
                                         "NameHasOwner", GLib.Variant("(s)", (NAME,)), None,
                                         Gio.DBusCallFlags.NONE, 3000, None).unpack()[0]
            if owned and client.call({"op": "status"})["ok"]:
                return
        except Exception:
            pass
        if process.poll() is not None:
            raise RuntimeError("Daemon exited")
        time.sleep(.1)
    raise TimeoutError("Daemon did not acquire bus name")


def listen(client):
    received = []
    client.bus.signal_subscribe(NAME, NAME, "Chunk", PATH, None, Gio.DBusSignalFlags.NONE,
                                lambda *args: received.append(args[-1].unpack()))
    return received


def chat(client, payload, during=None):
    """Async Call while the GLib loop delivers signals; `during` runs once the first chunk is seen."""
    loop, reply = GLib.MainLoop(), {}

    def finished(bus, result):
        reply.update(json.loads(bus.call_finish(result).unpack()[0]))
        loop.quit()
    client.bus.call(NAME, PATH, NAME, "Call", GLib.Variant("(s)", (json.dumps(payload),)),
                    GLib.VariantType.new("(s)"), Gio.DBusCallFlags.NONE, 60000, None, finished)
    if during:
        GLib.timeout_add(250, lambda: (during(), False)[1])
    GLib.timeout_add_seconds(60, loop.quit)
    loop.run()
    return reply


def main():
    with tempfile.TemporaryDirectory(prefix="leo-stream-") as directory, FakeOllama() as ollama:
        env = dict(os.environ, XDG_CONFIG_HOME=directory + "/config", XDG_DATA_HOME=directory + "/data",
                   XDG_CACHE_HOME=directory + "/cache", LEO_OLLAMA_PORT=str(ollama.port))
        os.environ.update(XDG_CONFIG_HOME=env["XDG_CONFIG_HOME"], XDG_DATA_HOME=env["XDG_DATA_HOME"])
        process = subprocess.Popen([sys.executable, "-m", "leo.daemon"], cwd=ROOT, env=env)
        try:
            owner, other = Client(private=True), Client(private=True)
            wait_for_daemon(owner, process)
            assert owner.call({"op": "settings", "changes": {"num_ctx": 4096}})["ok"]
            mine, theirs = listen(owner), listen(other)
            status_times = []

            def probe():
                for client in (other, owner):  # Another client, and the requester itself.
                    start = time.monotonic()
                    status = client.call({"op": "status"})
                    status_times.append(time.monotonic() - start)
                    assert status["ok"] and status["state"] == "thinking", status
            reply = chat(owner, {"op": "chat", "prompt": "Hola", "request_id": "req-1"}, during=probe)
            other_loop = GLib.MainContext.default()
            while other_loop.iteration(False):
                pass
            assert reply["ok"], reply
            assert reply["result"] == "".join(ANSWER) and reply["action"] is None and reply["tools"] == []
            assert len(mine) >= 2 and all(rid == "req-1" for rid, _ in mine), mine
            assert "".join(text for _, text in mine) == reply["result"], mine
            assert theirs == [], "Chunks must be unicast to the requesting client"
            assert len(status_times) == 2 and max(status_times) < .5, status_times
            sent = ollama.requests[-1]
            assert sent["stream"] and sent["options"]["num_ctx"] == 4096 and sent["model"] == "qwen3:8b", sent
            before = len(mine)
            plain = chat(owner, {"op": "chat", "prompt": "Hola otra vez"})
            assert plain["ok"] and plain["result"] == "".join(ANSWER) and len(mine) == before
            assert not ollama.requests[-1]["stream"]
            for bad in ({"op": "chat", "prompt": "x", "request_id": "a b"},
                        {"op": "chat", "prompt": "x", "request_id": 7},
                        {"op": "status", "request_id": "x"}):
                assert not owner.call(bad)["ok"], bad
            asked = chat(owner, {"op": "chat", "prompt": "¿Cómo va el entrenamiento?"})
            context = next(m["content"] for m in ollama.requests[-1]["messages"] if m["role"] == "tool")
            assert asked["tools"] == ["job.status"] and "«jobs» está desactivada" in context, (asked, context)
            assert owner.call({"op": "settings", "changes": {"jobs": True}})["ok"]
            asked = chat(owner, {"op": "chat", "prompt": "Leo, ¿cómo va el entrenamiento?"})
            context = next(m["content"] for m in ollama.requests[-1]["messages"] if m["role"] == "tool")
            assert asked["tools"] == ["job.status"] and '"jobs": []' in context and '"gpu"' in context, context
            print(f"PASS: streaming D-Bus real con Ollama simulado; {before} chunks unicast, texto literal, "
                  f"status {max(status_times) * 1000:.0f} ms durante inferencia, num_ctx y qwen3:8b por defecto")
        finally:
            process.terminate()
            process.wait(timeout=10)


if __name__ == "__main__":
    main()
