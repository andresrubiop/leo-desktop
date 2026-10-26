"""Agentes «cocinando»: sesiones activas de Claude Code, Codex y Antigravity (petición del usuario, 2026-10-23).

Junto a Leo aparece una mascotita por cada sesión que está trabajando. Solo se usan metadatos:
- la fecha de modificación de sus archivos de sesión (nunca se abre su contenido);
- la CPU y la carpeta de trabajo de sus procesos, del mismo usuario y dentro de su carpeta personal
  (`/proc`), para no perder una sesión durante una herramienta larga que no escribe en su archivo.
No se toca la configuración de esas herramientas ni se instalan hooks.
Por proyecto: sesiones = máx(archivos escritos hace poco, procesos con CPU). Tope de mascotas por herramienta.
"""
import glob
import os
from pathlib import Path
import re
import threading
import time

RECENT = 45        # s: un archivo de sesión escrito hace menos de esto está cocinando
CPU_ACTIVE = 0.01  # fracción de un núcleo durante la ventana de muestreo
MAX_PER_TOOL = 6
TOOLS = {
    "claude": {"label": "Claude", "processes": {"claude"}},
    "codex": {"label": "Codex", "processes": {"codex"}},
    "antigravity": {"label": "Antigravity", "processes": {"agy"}},
}


def claude_key(path):
    """Claude Code guarda cada proyecto en una carpeta con la ruta de trabajo y los separadores como guiones."""
    return re.sub(r"[^A-Za-z0-9]", "-", path)


def claude_label(key):
    return key.rstrip("-").rsplit("-", 1)[-1] or None


def session_files(home, now):
    """[(tool, project key or None, label or None)] for session files written in the last RECENT seconds."""
    found = []
    for path in glob.glob(f"{home}/.claude*/projects/*/*.jsonl"):
        try:
            if now - os.stat(path).st_mtime < RECENT:
                key = os.path.basename(os.path.dirname(path))
                found.append(("claude", key, claude_label(key)))
        except OSError:
            continue
    days = {time.strftime("%Y/%m/%d", time.localtime(now - offset)) for offset in (0, 86400)}
    patterns = [("codex", f"{home}/.codex*/sessions/{day}/rollout-*.jsonl") for day in days]
    patterns += [("antigravity", f"{home}/.gemini/antigravity/conversations/*.pb"),
                 ("antigravity", f"{home}/.gemini/antigravity/log/cli-*.log")]
    for tool, pattern in patterns:
        for path in glob.glob(pattern):
            try:
                if now - os.stat(path).st_mtime < RECENT:
                    found.append((tool, None, None))
            except OSError:
                continue
    return found


class Processes:
    """CPU of the user's agent processes between two samples, from /proc (comm, stat, cwd)."""

    def __init__(self, root="/proc", clock=time.monotonic):
        self.root, self.clock = root, clock
        self.previous, self.tick = {}, os.sysconf("SC_CLK_TCK")

    def sample(self, home):
        """[(tool, cwd)] of processes that used more than CPU_ACTIVE of a core since the previous sample."""
        names = {name: tool for tool, spec in TOOLS.items() for name in spec["processes"]}
        now, current, active = self.clock(), {}, []
        for entry in os.scandir(self.root):
            if not entry.name.isdigit():
                continue
            try:
                if entry.stat().st_uid != os.getuid():
                    continue
                with open(f"{entry.path}/comm") as stream:
                    tool = names.get(stream.read().strip())
                if not tool:
                    continue
                with open(f"{entry.path}/cmdline", "rb") as stream:
                    if b"--chrome-native-host" in stream.read():
                        continue  # Browser bridge, not a session. Arguments are not kept.
                with open(f"{entry.path}/stat") as stream:
                    fields = stream.read().rsplit(")", 1)[1].split()
                ticks = int(fields[11]) + int(fields[12])  # utime + stime
                cwd = os.readlink(f"{entry.path}/cwd")
            except (OSError, ValueError, IndexError):
                continue
            if not (cwd == home or cwd.startswith(home + os.sep)):
                continue
            current[entry.name] = (ticks, now)
            before = self.previous.get(entry.name)
            if before and now > before[1] and (ticks - before[0]) / self.tick / (now - before[1]) >= CPU_ACTIVE:
                active.append((tool, cwd))
        self.previous = current
        return active


def pets(files, processes):
    """One entry per cooking session: [{id, tool, label, project}] with stable ids for the avatar."""
    groups = {}
    for tool, key, label in files:
        group = groups.setdefault((tool, key if tool == "claude" else None), {"files": 0, "procs": 0, "label": label})
        group["files"] += 1
    for tool, cwd in processes:
        key = claude_key(cwd) if tool == "claude" else None
        group = groups.setdefault((tool, key), {"files": 0, "procs": 0, "label": None})
        group["procs"] += 1
        group["label"] = group["label"] or os.path.basename(cwd)
    result, per_tool = [], {}
    for (tool, key), group in sorted(groups.items(), key=lambda item: (item[0][0], item[0][1] or "")):
        for index in range(max(group["files"], group["procs"])):
            if per_tool.get(tool, 0) >= MAX_PER_TOOL:
                break
            per_tool[tool] = per_tool.get(tool, 0) + 1
            result.append({"id": f"{tool}:{key or '-'}:{index}", "tool": tool, "label": TOOLS[tool]["label"],
                           "project": group["label"]})
    return result


class Cooking:
    """Background scan every `interval` seconds while the setting is on; `current()` is cheap."""

    def __init__(self, settings, home=None, interval=5.0, processes=None):
        self.settings, self.interval = settings, interval
        self.home = str(home or Path.home())
        self.processes = processes or Processes()
        self.state, self.lock = [], threading.Lock()
        self.stop = threading.Event()

    def scan(self):
        if not self.settings.snapshot()["agent_pets"]:
            found = []
        else:
            found = pets(session_files(self.home, time.time()), self.processes.sample(self.home))
        with self.lock:
            self.state = found
        return found

    def current(self):
        with self.lock:
            return list(self.state)

    def start(self):
        threading.Thread(target=self._loop, daemon=True, name="leo-cooking").start()

    def _loop(self):
        while not self.stop.wait(self.interval):
            try:
                self.scan()
            except Exception:  # A transient /proc or glob error never stops the scan.
                pass

    def close(self):
        self.stop.set()
