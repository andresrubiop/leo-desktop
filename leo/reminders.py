"""Recordatorios y temporizadores persistentes (LEO_SPEC F2.6; base de F3.3).

Se guardan en un JSON 0600, sobreviven a reinicios y los vencidos mientras Leo no estaba se
avisan al arrancar como «perdidos». Los avisos salen por `Events` (pose, globo y notificación).
"""
from datetime import datetime
import json
import os
from pathlib import Path
import tempfile
import threading
import time

from .config import data_dir
from .events import plain

YEAR = 366 * 24 * 3600


def parse_local(value, now):
    if not isinstance(value, str):
        raise ValueError("Fecha y hora: AAAA-MM-DDTHH:MM")
    try:
        moment = datetime.fromisoformat(value.strip().replace(" ", "T", 1))
    except ValueError:
        raise ValueError("Fecha y hora: AAAA-MM-DDTHH:MM") from None
    if moment.tzinfo is None:
        moment = moment.astimezone()  # Local time of this computer.
    at = moment.timestamp()
    if at < now - 60:
        raise ValueError("Esa hora ya pasó")
    if at > now + YEAR:
        raise ValueError("Como máximo un año en el futuro")
    return at


class Reminders:
    def __init__(self, events, path=None, clock=time.time, interval=15.0):
        self.events, self.clock, self.interval = events, clock, interval
        self.path = path or data_dir() / "reminders.json"
        self.lock = threading.Lock()
        self.stop = threading.Event()
        self.thread = None
        try:
            self.items = json.loads(self.path.read_text())
            if not isinstance(self.items, list):
                self.items = []
        except (OSError, ValueError):
            self.items = []

    def _save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd, name = tempfile.mkstemp(prefix=".reminders-", dir=self.path.parent)
        try:
            with os.fdopen(fd, "w") as stream:
                json.dump(self.items, stream, ensure_ascii=False)
            os.replace(name, self.path)
        finally:
            Path(name).unlink(missing_ok=True)

    def _add(self, kind, text, at):
        with self.lock:
            pending = [i for i in self.items if i["state"] == "pending"]
            if len(pending) >= 100:
                raise RuntimeError("Demasiados recordatorios pendientes (máximo 100)")
            item = {"id": max((i["id"] for i in self.items), default=0) + 1, "kind": kind, "text": text,
                    "at": round(at), "state": "pending", "created": round(self.clock())}
            # Keep recent history bounded; pending items are never dropped.
            done = [i for i in self.items if i["state"] != "pending"][-200:]
            self.items = sorted(done + pending, key=lambda i: i["id"]) + [item]
            self._save()
        public = self.describe(item)
        return public, {"op": "reminder_cancel", "id": item["id"]}

    def create(self, text, at):
        return self._add("reminder", plain(text, 200, "recordatorio"), parse_local(at, self.clock()))

    def timer(self, minutes, label):
        if type(minutes) is not int or not 1 <= minutes <= 1440:
            raise ValueError("Temporizador de 1 a 1440 minutos")
        return self._add("timer", plain(label, 80, "temporizador"), self.clock() + minutes * 60)

    @staticmethod
    def describe(item):
        return {"id": item["id"], "tipo": "temporizador" if item["kind"] == "timer" else "recordatorio",
                "texto": item["text"], "cuando": datetime.fromtimestamp(item["at"]).isoformat(timespec="minutes"),
                "estado": item["state"]}

    def list(self):
        with self.lock:
            return {"pending": [self.describe(i) for i in sorted(self.items, key=lambda i: i["at"]) if i["state"] == "pending"]}

    def _set_state(self, ident, old, new):
        if type(ident) is not int:
            raise ValueError("Id de recordatorio inválido")
        with self.lock:
            item = next((i for i in self.items if i["id"] == ident), None)
            if item is None or item["state"] != old:
                raise ValueError(f"No hay un recordatorio {ident} en estado {old}")
            item["state"] = new
            self._save()
            return self.describe(item)

    def cancel(self, ident):
        return self._set_state(ident, "pending", "cancelled"), {"op": "reminder_restore", "id": ident}

    def revert(self, undo):
        if undo["op"] == "reminder_cancel":
            return "cancelado: " + self._set_state(undo["id"], "pending", "cancelled")["texto"]
        return "restaurado: " + self._set_state(undo["id"], "cancelled", "pending")["texto"]

    def start(self):
        self.check(missed=True)
        self.thread = threading.Thread(target=self._loop, name="leo-reminders", daemon=True)
        self.thread.start()

    def close(self):
        self.stop.set()
        if self.thread:
            self.thread.join(timeout=5)

    def _loop(self):
        while not self.stop.wait(self.interval):
            self.check()

    def check(self, missed=False):
        now = self.clock()
        with self.lock:
            due = [i for i in self.items if i["state"] == "pending" and i["at"] <= now]
            for item in due:
                item["state"] = "fired"
            if due:
                self._save()
        for item in due:
            late = missed and now - item["at"] > 120
            when = datetime.fromtimestamp(item["at"]).strftime("%d/%m %H:%M")
            title = (f"Temporizador terminado: {item['text']}" if item["kind"] == "timer"
                     else f"Recordatorio{' perdido (' + when + ')' if late else ''}: {item['text']}")
            try:
                self.events.publish(f"reminder:{item['id']}", "attention", title[:120], "", "reminder")
            except (RuntimeError, ValueError):
                pass
        return len(due)
