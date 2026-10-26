"""Diario de deshacer (LEO_SPEC F2.7): cada mutación guarda cómo revertirla.

Entradas `{id, plan, at, tool, summary, undo, undone}` en un JSON 0600 con escritura atómica y
como máximo 200 entradas. Un plan agrupa las mutaciones de una misma petición: «deshaz eso»
revierte el último plan completo, en orden inverso.
"""
import contextvars
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import uuid

from .config import data_dir

PLAN = contextvars.ContextVar("leo_plan", default=None)
LIMIT = 200


class Journal:
    def __init__(self, path=None, clock=time.time):
        self.path = path or data_dir() / "journal.json"
        self.clock = clock
        self.lock = threading.Lock()
        try:
            self.entries = json.loads(self.path.read_text())
            if not isinstance(self.entries, list):
                self.entries = []
        except (OSError, ValueError):
            self.entries = []

    def _save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd, name = tempfile.mkstemp(prefix=".journal-", dir=self.path.parent)
        try:
            with os.fdopen(fd, "w") as stream:
                json.dump(self.entries, stream, ensure_ascii=False)
            os.replace(name, self.path)
        finally:
            Path(name).unlink(missing_ok=True)

    def record(self, tool, summary, undo):
        with self.lock:
            entry = {"id": (self.entries[-1]["id"] + 1) if self.entries else 1,
                     "plan": PLAN.get() or uuid.uuid4().hex, "at": round(self.clock()),
                     "tool": tool, "summary": summary, "undo": undo, "undone": False}
            self.entries = (self.entries + [entry])[-LIMIT:]
            self._save()
            return entry["id"]

    def last_plan(self):
        """Entries of the most recent plan that still has something to undo, oldest first."""
        with self.lock:
            pending = [e for e in self.entries if not e["undone"]]
            if not pending:
                return []
            plan = pending[-1]["plan"]
            return [dict(e) for e in pending if e["plan"] == plan]

    def describe(self):
        entries = self.last_plan()
        if not entries:
            return "No hay operaciones de Leo para deshacer."
        return "Deshacer: " + "; ".join(e["summary"] for e in reversed(entries))

    def mark_undone(self, ids):
        with self.lock:
            for entry in self.entries:
                if entry["id"] in ids:
                    entry["undone"] = True
            self._save()
