"""Vigilancia de entrenamientos y procesos (LEO_SPEC F1.2), solo lectura y bajo la capacidad «jobs».

Un trabajo se vigila por PID (del mismo usuario) y/o por un log dentro de una carpeta autorizada.
Del log se lee únicamente la cola nueva; se conservan tipos de incidencia y valores numéricos de
métricas conocidas, nunca líneas de texto. Los avisos salen por `Events` como cualquier otro.
"""
import os
from pathlib import Path
import re
import shutil
import subprocess
import threading
import time

from .config import folder_path
from .events import project_name

TAIL = 64 * 1024
CARRY = 4096
MAX_WATCHES = 8
LIFETIME = 24 * 3600
ISSUES = {
    "traceback": ("failed", "excepción de Python (Traceback)", re.compile(r"Traceback \(most recent call last\)")),
    "cuda_oom": ("failed", "memoria de GPU agotada (CUDA out of memory)",
                 re.compile(r"CUDA out of memory|OutOfMemoryError", re.I)),
    "killed": ("failed", "proceso terminado por el sistema (Killed)",
               re.compile(r"^(?:[^\n]*\b\d+\s+)?Killed\b", re.M)),
    "nan": ("attention", "métrica con NaN o infinito",
            re.compile(r"(?:[:=]\s*|\b(?:loss|val_loss|train_loss|acc|accuracy|ppl|perplexity|grad_norm)\s+)"
                       r"[-+]?(?:nan|inf(?:inity)?)\b", re.I)),
}
FATAL = {"traceback", "cuda_oom", "killed"}
METRIC = re.compile(r"\b(epoch|step|iter|loss|val_loss|train_loss|acc|accuracy|val_acc|lr|ppl|perplexity)\b"
                    r"\s*[:=]?\s*([-+]?(?:\d+(?:\.\d+)?(?:e[-+]?\d+)?|nan|inf))", re.I)


def gpu_status(run=subprocess.run):
    """nvidia-smi con argv fijo y sin shell; no supone que exista una GPU NVIDIA."""
    executable = shutil.which("nvidia-smi", path="/usr/bin:/bin:/usr/local/bin")
    if not executable:
        return {"available": False, "error": "nvidia-smi no está instalado"}
    try:
        result = run([executable, "--query-gpu=name,utilization.gpu,memory.used,memory.total,temperature.gpu",
                      "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=5,
                     env={"PATH": "/usr/bin:/bin", "LANG": "C"})
    except (OSError, subprocess.TimeoutExpired):
        return {"available": False, "error": "nvidia-smi no respondió"}
    if result.returncode:
        return {"available": False, "error": "nvidia-smi devolvió un error"}

    def number(text):
        try:
            return float(text)
        except ValueError:
            return None
    gpus = []
    for line in result.stdout.splitlines()[:8]:
        parts = [part.strip() for part in line.split(",")]
        if len(parts) == 5:
            gpus.append({"name": project_name(parts[0]), "utilization_percent": number(parts[1]),
                         "memory_used_mib": number(parts[2]), "memory_total_mib": number(parts[3]),
                         "temperature_c": number(parts[4])})
    return {"available": True, "gpus": gpus}



def gpu_processes(run=subprocess.run):
    """Names of the processes computing on NVIDIA GPUs (a training, Ollama…); [] without nvidia-smi."""
    executable = shutil.which("nvidia-smi", path="/usr/bin:/bin:/usr/local/bin")
    if not executable:
        return []
    try:
        result = run([executable, "--query-compute-apps=process_name", "--format=csv,noheader"], capture_output=True,
                     text=True, timeout=5, env={"PATH": "/usr/bin:/bin", "LANG": "C"})
    except (OSError, subprocess.TimeoutExpired):
        return ["?"]  # Unknown: assume busy.
    if result.returncode:
        return ["?"]
    return [Path(line.strip()).name for line in result.stdout.splitlines()[:32] if line.strip()]

def _proc_start(pid):
    """Start time from /proc/<pid>/stat; detects PID reuse. None if gone or a zombie."""
    try:
        stat = Path(f"/proc/{pid}/stat").read_text()
    except OSError:
        return None
    fields = stat[stat.rindex(")") + 2:].split()
    return None if fields[0] == "Z" else fields[19]


class Jobs:
    def __init__(self, settings, events, interval=5.0, clock=time.time):
        self.settings, self.events, self.interval, self.clock = settings, events, interval, clock
        self.watches, self.ids, self.lock = {}, 0, threading.Lock()
        self.stop = threading.Event()
        self.thread = None

    def start(self):
        self.thread = threading.Thread(target=self._loop, name="leo-jobs", daemon=True)
        self.thread.start()

    def close(self):
        self.stop.set()
        if self.thread:
            self.thread.join(timeout=5)

    def _log_path(self, log):
        real = Path(log).resolve(strict=True)
        if not real.is_file():
            raise ValueError("El log debe ser un archivo")
        for folder in self.settings.snapshot()["folders"]:
            try:
                root = folder_path(folder["path"])  # Revalidated: a folder may have been swapped.
                relative = real.relative_to(root)
            except ValueError:
                continue
            if not any(part.startswith(".") for part in relative.parts):
                return real
        raise PermissionError("El log debe estar dentro de una carpeta autorizada en Ajustes")

    def watch(self, args):
        if not self.settings.snapshot()["jobs"]:
            raise PermissionError("Habilita la capacidad «jobs» en Permisos")
        watch = {"pid": None, "start": None, "log": None, "offset": None, "carry": "",
                 "issues": [], "metrics": {}, "state": "running", "since": self.clock()}
        if "pid" in args:
            pid = args["pid"]
            try:
                owner = os.stat(f"/proc/{pid}").st_uid
            except OSError:
                raise ValueError(f"No existe el proceso {pid}") from None
            if owner != os.getuid():
                raise PermissionError("Solo se vigilan procesos de tu propio usuario")
            watch.update(pid=pid, start=_proc_start(pid))
            if watch["start"] is None:
                raise ValueError(f"El proceso {pid} ya terminó")
            try:
                watch["name"] = project_name(Path(f"/proc/{pid}/comm").read_text().strip()) or f"pid {pid}"
            except OSError:
                watch["name"] = f"pid {pid}"
        if "log" in args:
            watch["log"] = self._log_path(args["log"])
            watch.setdefault("name", project_name(watch["log"].name) or "log")
        with self.lock:
            self.watches = {k: w for k, w in self.watches.items() if w["state"] == "running"}
            if len(self.watches) >= MAX_WATCHES:
                raise RuntimeError(f"Máximo {MAX_WATCHES} trabajos vigilados a la vez")
            self.ids += 1
            watch["id"] = self.ids
            self.watches[self.ids] = watch
        self._check(watch)  # Report existing problems in the log right away.
        return {"watch": watch["id"], "name": watch["name"], "pid": watch["pid"],
                "log": str(watch["log"]) if watch["log"] else None}

    def _read(self, watch):
        """New bytes from the tail of the log; whole lines only, nothing older than TAIL."""
        try:
            fd = os.open(watch["log"], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        except OSError:
            return ""
        try:
            size = os.fstat(fd).st_size
            offset = watch["offset"]
            if offset is None or size < offset:
                offset, watch["carry"] = max(0, size - TAIL), ""
            offset = max(offset, size - TAIL)
            os.lseek(fd, offset, os.SEEK_SET)
            data = os.read(fd, size - offset) if size > offset else b""
            watch["offset"] = offset + len(data)
        finally:
            os.close(fd)
        text = watch["carry"] + data.decode("utf-8", "replace")
        complete, _, rest = text.rpartition("\n")
        watch["carry"] = rest[-CARRY:]
        return complete

    def _publish(self, watch, event, title):
        try:
            self.events.publish(f"job:{watch['id']}", event, title, watch["name"], "job")
        except (RuntimeError, ValueError):
            pass  # Rate-limited: the status keeps the issue for chat and the panel.

    def _check(self, watch):
        if watch["state"] != "running":
            return
        if watch["log"]:
            text = self._read(watch)
            for key, value in METRIC.findall(text):
                watch["metrics"][key.lower()] = value
            for issue, (event, description, pattern) in ISSUES.items():
                if issue not in watch["issues"] and pattern.search(text):
                    watch["issues"].append(issue)
                    self._publish(watch, event, f"{watch['name']}: {description}")
            if watch["pid"] is None and FATAL & set(watch["issues"]):
                watch["state"] = "failed"
                return
        if watch["pid"] is not None and _proc_start(watch["pid"]) != watch["start"]:
            problems = [ISSUES[i][1] for i in watch["issues"] if i in FATAL]
            watch["state"] = "failed" if problems else "finished"
            self._publish(watch, "failed" if problems else "done",
                          f"{watch['name']} terminó" + (f" con {problems[0]}" if problems else ""))
        elif self.clock() - watch["since"] > LIFETIME:
            watch["state"] = "expired"

    def _loop(self):
        while not self.stop.wait(self.interval):
            with self.lock:
                watches = list(self.watches.values())
            for watch in watches:
                try:
                    self._check(watch)
                except Exception:
                    watch["state"] = "error"

    def summary(self):
        with self.lock:
            return {"active": sum(w["state"] == "running" for w in self.watches.values())}

    def report(self):
        """Last measured state for the local model: names, states, issue types and numbers only."""
        now = self.clock()
        with self.lock:
            jobs = [{"name": w["name"], "pid": w["pid"], "log": w["log"].name if w["log"] else None,
                     "state": w["state"], "minutes": round((now - w["since"]) / 60, 1),
                     "issues": [ISSUES[i][1] for i in w["issues"]], "metrics": dict(w["metrics"])}
                    for w in self.watches.values()]
        return {"jobs": jobs, "gpu": gpu_status() if self.settings.snapshot()["jobs"] else None}
