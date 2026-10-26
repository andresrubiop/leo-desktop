#!/usr/bin/env python3
"""Fase 1 sobre D-Bus real: leoctl notify/agent-event/watch → daemon → señal Event.

Run: dbus-run-session -- python3 tests/integration_events.py
HOME y XDG temporales; los hooks reciben payloads de ejemplo de la documentación, no reales.
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
from integration_stream import wait_for_daemon  # noqa: E402
from test_companion import CLAUDE_NOTIFICATION, CODEX_TURN  # noqa: E402
from leo.ipc import Client, NAME, PATH  # noqa: E402
from gi.repository import Gio, GLib  # noqa: E402


def main():
    with tempfile.TemporaryDirectory(prefix="leo-events-") as directory:
        home = Path(directory) / "home"
        (home / "Investigacion/modelo").mkdir(parents=True)
        env = dict(os.environ, HOME=str(home), XDG_CONFIG_HOME=str(home / ".config"),
                   XDG_DATA_HOME=str(home / ".local/share"), XDG_CACHE_HOME=str(home / ".cache"))
        process = subprocess.Popen([sys.executable, "-m", "leo.daemon"], cwd=ROOT, env=env)

        def leoctl(*args, stdin=None):
            return subprocess.run([sys.executable, "-m", "leo.cli", *args], cwd=ROOT, env=env, input=stdin,
                                  capture_output=True, text=True, timeout=30)
        try:
            client = Client(private=True)
            wait_for_daemon(client, process)
            received = []
            client.bus.signal_subscribe(NAME, NAME, "Event", PATH, None, Gio.DBusSignalFlags.NONE,
                                        lambda *args: received.append(json.loads(args[-1].unpack()[0])))

            def collect(count, seconds=20):
                context, end = GLib.MainContext.default(), time.monotonic() + seconds
                while len(received) < count and time.monotonic() < end:
                    context.iteration(False)
                    time.sleep(.02)
                assert len(received) >= count, received
                return received[count - 1]

            done = leoctl("notify", "--event", "done", "--title", "train <b>listo</b>", "--project", "modelo")
            assert done.returncode == 0, done.stdout + done.stderr
            event = collect(1)
            assert (event["event"], event["title"], event["project"], event["source"]) == \
                ("done", "train ‹b›listo‹/b›", "modelo", "leoctl"), event
            for _ in range(5):
                assert leoctl("notify", "--event", "info", "--title", "x", "--project", "modelo").returncode == 0
            limited = leoctl("notify", "--event", "info", "--title", "x", "--project", "modelo")
            assert limited.returncode == 1 and "Demasiados avisos" in limited.stdout, limited.stdout
            assert leoctl("notify", "--event", "info", "--title", "y" * 121).returncode == 1
            collect(6)

            hook = leoctl("agent-event", "--source", "claude", stdin=json.dumps(CLAUDE_NOTIFICATION))
            assert hook.returncode == 0 and hook.stdout == "", hook
            assert collect(7)["title"] == "Claude Code espera tu aprobación en LeoAvatar"
            codex = leoctl("agent-event", "--source", "codex", json.dumps(CODEX_TURN))
            assert codex.returncode == 0 and collect(8)["title"] == "Codex terminó en proyecto-tesis"
            broken = leoctl("agent-event", "--source", "claude", stdin="{roto")
            assert broken.returncode == 0 and broken.stdout == "", "Hooks must never fail or block the agent"
            print("PASS: leoctl notify, límite por origen, texto sin markup y hooks Claude/Codex sobre D-Bus real")

            denied = leoctl("watch", "--pid", str(os.getpid()))
            assert denied.returncode == 1 and "jobs" in denied.stdout, denied.stdout
            folder = {"id": "investigacion", "path": str(home / "Investigacion"), "label": "Investigación", "write": False}
            assert client.call({"op": "settings", "changes": {"jobs": True, "folders": [folder]}})["ok"]
            log = home / "Investigacion/modelo/train.log"
            script = "import time\nprint('step 1 loss: 0.9', flush=True)\nprint('step 2 loss: nan', flush=True)\ntime.sleep(2)\n"
            with log.open("w") as stream:
                job = subprocess.Popen([sys.executable, "-c", script], stdout=stream)
                watched = leoctl("watch", "--pid", str(job.pid), "--log", str(log))
                assert watched.returncode == 0, watched.stdout
                assert client.call({"op": "status"})["jobs"] == {"active": 1}
                job.wait(timeout=10)
            nan = collect(9)
            assert nan["event"] == "attention" and nan["title"].endswith("métrica con NaN o infinito"), nan
            finished = collect(10, seconds=15)
            assert finished["event"] == "done" and finished["title"].endswith("terminó"), finished
            assert client.call({"op": "status"})["jobs"] == {"active": 0}
            outside = leoctl("watch", "--log", str(Path(directory) / "fuera.log"))
            assert outside.returncode == 1
            print("PASS: job.watch real (capacidad, carpeta autorizada, NaN en el log y fin del proceso) por D-Bus")
        finally:
            process.terminate()
            process.wait(timeout=10)


if __name__ == "__main__":
    main()
