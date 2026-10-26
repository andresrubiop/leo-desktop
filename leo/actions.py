import os
import platform
from pathlib import Path
import shutil
import subprocess
from gi.repository import Gio, GLib

from .broker import APPS, COMMANDS
from .config import data_dir
from .portals import Portals


def save_note(directory, name, text):
    """Exclusive create relative to an opened directory; no symlink following."""
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    dirfd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        fd = os.open(name + ".md", os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                     0o600, dir_fd=dirfd)
        with os.fdopen(fd, "w") as stream:
            stream.write(text)
    finally:
        os.close(dirfd)
    return str(directory / (name + ".md"))


MUTATIONS = {"files.create_draft", "files.move", "files.rename", "files.copy", "files.trash",
             "reminder.create", "reminder.cancel", "timer.start", "settings.set", "note.save",
             "memory.remember", "memory.forget"}


class Actions:
    def __init__(self, bus, jobs=None, files=None, journal=None, reminders=None, desktop=None, memory=None, schedule=None,
                 summarize=None, a11y=None, vision=None, point=None, knowledge=None, capture=None):
        self.bus = bus
        self.portals = Portals(bus)
        self.jobs, self.files, self.journal = jobs, files, journal
        self.reminders, self.desktop, self.memory = reminders, desktop, memory
        self.schedule, self.summarize = schedule, summarize
        self.a11y, self.vision, self.point = a11y, vision, point or (lambda item: None)
        self.knowledge = knowledge
        self.capture = capture

    def __call__(self, name, args):
        if name in MUTATIONS:
            result, undo = self._mutate(name, args)
            if undo and self.journal:
                self.journal.record(name, self._summary(name, args), undo)
            return result
        if name == "undo.last":
            return self.undo()
        return self._run(name, args)

    @staticmethod
    def _summary(name, args):
        text = ", ".join(f"{k}={v if len(str(v)) < 80 else str(v)[:77] + '…'}" for k, v in args.items()
                         if k != "text" or name == "memory.remember")
        return f"{name}({text})"

    def _mutate(self, name, args):
        if name == "note.save":
            path = save_note(data_dir() / "notes", args["name"], args["text"])
            return path, {"op": "trash_created", "path": path}
        if name == "files.create_draft":
            return self.files.create_draft(args["folder"], args["name"], args["text"])
        if name == "files.move":
            return self.files.move(args["source"], args["destination_folder"])
        if name == "files.rename":
            return self.files.rename(args["path"], args["new_name"])
        if name == "files.copy":
            return self.files.copy(args["source"], args["destination_folder"])
        if name == "files.trash":
            return self.files.trash(args["path"])
        if name == "reminder.create":
            return self.reminders.create(args["text"], args["at"])
        if name == "timer.start":
            return self.reminders.timer(args["minutes"], args["label"])
        if name == "reminder.cancel":
            return self.reminders.cancel(args["id"])
        if name == "memory.remember":
            return self.memory.add(args["text"])
        if name == "memory.forget":
            return self.memory.forget(args["id"])
        return self.desktop.set(args["key"], args["value"])

    def undo(self):
        """Reverts the last plan in reverse order; stops at the first step that cannot be reverted."""
        from .files import revert
        entries = self.journal.last_plan()
        if not entries:
            return "No hay operaciones de Leo para deshacer"
        done, reverted = [], []
        try:
            for entry in reversed(entries):
                undo = entry["undo"]
                if undo["op"].startswith("reminder_"):
                    reverted.append(self.reminders.revert(undo))
                elif undo["op"].startswith("memory_"):
                    reverted.append(self.memory.revert(undo))
                elif undo["op"] == "setting":
                    reverted.append(self.desktop.revert(undo))
                else:
                    reverted.append(revert(undo))
                done.append(entry["id"])
        finally:
            self.journal.mark_undone(done)
        return {"undone": reverted}

    def _run(self, name, args):

        if name == "system.info":
            return {"system": platform.system(), "kernel": platform.release(),
                    "session": os.environ.get("XDG_SESSION_TYPE", "unknown"),
                    "desktop": os.environ.get("XDG_CURRENT_DESKTOP", "unknown")}
        if name == "app.open":
            app = Gio.DesktopAppInfo.new(APPS[args["app"]])
            if app is None:
                raise RuntimeError("La aplicación no está instalada con el ID esperado")
            app.launch([], None)
            return "Aplicación abierta"
        if name == "app.launch":
            from .conversation import catalog
            app = catalog().get(args["desktop_id"])
            if app is None:
                raise ValueError("La aplicación ya no está instalada")
            app.launch([], None)
            return "Aplicación abierta: " + app.get_display_name()
        if name == "terminal.open":
            executable = next((shutil.which(x) for x in ("ptyxis", "kgx", "gnome-terminal") if shutil.which(x)), None)
            if not executable:
                raise RuntimeError("No se encontró GNOME Console ni GNOME Terminal")
            process = subprocess.Popen([executable], cwd=Path.home(), stdin=subprocess.DEVNULL,
                                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            # Reap the launcher without blocking the daemon or holding a terminal open.
            import threading
            threading.Thread(target=process.wait, daemon=True).start()
            return "Terminal vacía abierta"
        if name == "music.control":
            names = self.bus.call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus",
                                       "org.freedesktop.DBus", "ListNames", None, None,
                                       Gio.DBusCallFlags.NONE, 3000, None).unpack()[0]
            players = sorted(n for n in names if n == "org.mpris.MediaPlayer2.spotify"
                             or n.startswith("org.mpris.MediaPlayer2.spotify."))
            if not players:
                raise RuntimeError("Spotify no está abierto o no publica MPRIS")
            self.bus.call_sync(players[0], "/org/mpris/MediaPlayer2", "org.mpris.MediaPlayer2.Player",
                               args["action"], None, None, Gio.DBusCallFlags.NONE, 5000, None)
            return "Acción enviada a Spotify"
        if name == "system.command":
            result = subprocess.run(COMMANDS[args["command"]], capture_output=True, text=True,
                                    timeout=10, check=True, env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"})
            return result.stdout[:16000]
        if name == "screen.capture":
            return self.portals.screenshot()
        if name == "pointer.move":
            return self.portals.move_pointer(args["dx"], args["dy"])
        if name == "files.search":
            return self.files.search(args["query"])
        if name == "knowledge.search":
            return self.knowledge.search(args["query"])
        if name == "files.list":
            return self.files.list(args["path"])
        if name == "files.read_text":
            return self.files.read_text(args["path"])
        if name == "reminder.list":
            return self.reminders.list()
        if name == "memory.list":
            return self.memory.list()
        if name == "schedule.today":
            return self.schedule.day(args["when"])
        if name == "files.summarize":
            from .documents import pages, summarize
            target = self.files.resolve(args["path"], directory=False)
            return summarize(self.summarize, pages(target), args["focus"].strip()[:200], target.name)
        if name in {"screen.look", "screen.locate", "files.look"}:
            from . import vision
            if name == "files.look":
                return vision.look_file(self.vision, self.files, args["path"], args["page"], args["question"])
            shot = self.capture.capture()
            captured = {"scope": shot["scope"], "app": shot["app"], **({"note": shot["note"]} if shot["note"] else {})}
            if name == "screen.look":
                return {**self.vision.ask_bytes(shot["png"], args["question"]), "captured": captured}
            found = self.vision.locate_bytes(shot["png"], args["target"])
            if found["bbox"]:
                if shot["rect"]:
                    from .screen import to_stage
                    self.point({"space": "stage", "rect": to_stage(found["bbox"], found["image"], shot["rect"]),
                                "label": args["target"], "doubt": True})
                else:  # Extensión anterior: captura completa del escritorio.
                    self.point({"space": "screen", "rect": found["bbox"], "image": found["image"], "label": args["target"], "doubt": True})
            return {"located": bool(found["bbox"]), "doubt": True, "model": found["model"], "captured": captured,
                    "note": "Creo que es aquí; puede no ser exacto." if found["bbox"] else "No lo encontré en la captura."}
        if name in {"files.latex", "screen.latex", "slides.review", "pdf.table", "curves.review"}:
            from . import cases
            if name == "files.latex":
                return cases.file_equation(self.vision, self.files, args["path"], args["page"])
            if name == "screen.latex":
                from .vision import load_bytes
                shot = self.capture.capture()
                return {**cases.equation(self.vision, load_bytes(shot["png"])), "captured": {"scope": shot["scope"], "app": shot["app"]}}
            if name == "slides.review":
                return cases.review_slides(self.files, args["path"], self.vision)
            if name == "pdf.table":
                return cases.pdf_table(self.files, args["path"], args["page"], self.vision)
            return cases.review_curves(self.vision, self.files, args["path"])
        if name == "ui.read":
            return self.a11y.read()
        if name == "ui.point":
            try:
                element = self.a11y.locate(args["name"])
            except LookupError as missing:
                element = self._marked(args["name"], missing)  # F6.7: by description, on numbered marks.
            self.point({"space": "window", "pid": element["pid"], "window": element["window"], "rect": element["rect"],
                        "origin": element.get("origin", "buffer"),
                        "label": element["name"], "doubt": element.get("doubt", False),
                        **({"say": args["say"][:200], "hold": 150} if args.get("say") else {})})
            result = {"pointed": element["name"], "role": element["role"], "app": element["app"]}
            if element.get("marks"):
                result.update(marks=element["marks"], method=element["method"], opinions=element["opinions"],
                              note="Lo busqué por su aspecto en una captura de la ventana; creo que es este.")
            return result
        if name == "ui.press":
            return self.a11y.press(args["name"])
        if name == "files.open":
            return self.files.open(args["path"])
        if name == "job.watch":
            return self.jobs.watch(args)
        if name == "gpu.status":
            from .jobs import gpu_status
            return gpu_status()
        if name == "remote.ask":
            raise PermissionError("Leo responde únicamente con el LLM local")
        raise ValueError("Herramienta desconocida")

    def _marked(self, description, missing):
        """Set-of-Marks fallback (F6.7): one quiet capture of the working window with its interactive elements
        boxed and numbered; the local vision model answers a number and AT-SPI gives the exact rectangle."""
        from . import marks
        from .screen import screen_turn
        from .vision import load_bytes
        if self.capture is None or self.vision is None or getattr(self.a11y, "focus", None) is None:
            raise missing
        try:
            focus = self.a11y.focus()
        except Exception:
            raise missing from None
        app, frame, items = self.a11y.elements()
        try:
            with screen_turn(True, scope="window"):
                shot = self.capture.capture(quiet=True, own=True)  # The window's own pixels: Leo never covers an icon.
        except PermissionError:
            raise LookupError(str(missing) + ". Para buscarlo por su aspecto, habilita Pantalla en Privacidad.") from None
        rect, image = shot["rect"], load_bytes(shot["png"])
        if not rect or not focus.get("buffer") or not focus.get("frame"):
            raise missing
        scale = image.get_width() / rect[2]
        boxes, kept = [], []
        for item in items:
            box = marks.to_image(item["rect"], focus[item.get("origin", "buffer")], rect, scale)
            if box[0] >= -1 and box[1] >= -1 and box[0] + box[2] <= image.get_width() + 1 and box[1] + box[3] <= image.get_height() + 1:
                boxes.append(box)
                kept.append(item)
            if len(boxes) >= marks.MAX_MARKS:
                break
        if not boxes:
            raise missing
        index, method, opinions = marks.decide(self.vision, image, shot["png"], boxes, description)
        if index is None:
            raise LookupError(f"No encuentro «{description}» en la ventana activa, ni por nombre ni entre los elementos marcados")
        item = kept[index]
        return {"pid": app.get_process_id(), "app": app.get_name() or "", "window": frame.get_name() or "",
                "name": item["name"] or description, "role": item["role"], "rect": item["rect"], "doubt": True,
                "marks": len(boxes), "method": method, "origin": item.get("origin", "buffer"),
                "opinions": {k: (kept[v]["name"] if v is not None else None) for k, v in opinions.items()}}

