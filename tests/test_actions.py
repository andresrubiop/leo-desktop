"""Fase 2: archivos en carpetas autorizadas, papelera freedesktop, diario de deshacer,
recordatorios y ajustes del escritorio. HOME y XDG temporales; nada toca el escritorio real."""
from datetime import datetime, timedelta
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from leo.actions import Actions
from leo.config import Settings
from leo.desktop import Desktop
from leo.events import Events
from leo.files import Files, trash_dir
from leo.journal import LIMIT, PLAN, Journal
from leo.reminders import Reminders


class Sandbox(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.home = self.root / "home"
        for path in ("Tesis/datos", "Lectura", ".config", ".local/share", "Tesis/.oculto"):
            (self.home / path).mkdir(parents=True)
        patcher = patch.dict(os.environ, {"HOME": str(self.home), "XDG_DATA_HOME": str(self.home / ".local/share"),
                                          "XDG_CONFIG_HOME": str(self.home / ".config")})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.settings = Settings()
        self.settings.update({"folders": [
            {"id": "tesis", "path": str(self.home / "Tesis"), "label": "Tesis", "write": True},
            {"id": "lectura", "path": str(self.home / "Lectura"), "label": "Lectura", "write": False}]})
        self.files = Files(self.settings)
        self.journal = Journal()
        self.sent = []
        self.reminders = Reminders(Events(self.sent.append))


class FileTests(Sandbox):
    def test_authorization_includes_deep_descendants_without_extra_permissions(self):
        nested = self.home / "Tesis" / "/".join(f"nivel{i}" for i in range(12))
        nested.mkdir(parents=True)
        target = nested / "hallazgo-profundo.txt"
        target.write_text("Dato sintético autorizado")
        (self.home / "Tesis/enlace-fuera.txt").symlink_to(self.root / "externo.txt")
        self.assertEqual(self.files.read_text(str(target))["text"], "Dato sintético autorizado")
        self.assertEqual([r["path"] for r in self.files.search("hallazgo-profundo")["results"]], [str(target)])
        self.assertEqual(self.files.search("enlace-fuera")["results"], [])
        self.settings.update({"folders": []})
        with self.assertRaises(PermissionError): self.files.read_text(str(target))

    def test_resolution_stays_inside_authorized_folders(self):
        (self.home / "Tesis/notas.md").write_text("hola")
        self.assertEqual(self.files.resolve("notas.md"), self.home / "Tesis/notas.md")
        self.assertEqual(self.files.resolve("~/Tesis/notas.md"), self.home / "Tesis/notas.md")
        (self.home / "Tesis/.oculto/x.md").write_text("x")
        (self.home / "Tesis/fuera").symlink_to(self.root)
        (self.home / "secreto.txt").write_text("x")
        for path, error in (("Tesis/.oculto/x.md", PermissionError), ("Tesis/fuera", PermissionError),
                            ("~/secreto.txt", PermissionError), ("/etc/passwd", PermissionError),
                            ("Tesis/../secreto.txt", PermissionError), ("no-existe.md", FileNotFoundError)):
            with self.assertRaises(error, msg=path): self.files.resolve(path)
        (self.home / "Lectura/paper.txt").write_text("x")
        self.files.resolve("Lectura/paper.txt")
        with self.assertRaises(PermissionError): self.files.resolve("Lectura/paper.txt", write=True)

    def test_read_search_and_list_skip_hidden_binary_and_large(self):
        (self.home / "Tesis/datos/resultados-redes.csv").write_text("a,b\n1,2\n")
        (self.home / "Tesis/.oculto/redes-secreto.md").write_text("x")
        (self.home / "Tesis/binario.bin").write_bytes(b"\x00\x01")
        (self.home / "Tesis/grande.txt").write_text("x" * (256 * 1024 + 1))
        found = [r["path"] for r in self.files.search("REDES")["results"]]
        self.assertEqual(found, [str(self.home / "Tesis/datos/resultados-redes.csv")])
        names = [e["name"] for e in self.files.list("Tesis")["entries"]]
        self.assertNotIn(".oculto", names)
        self.assertEqual(self.files.read_text("Tesis/datos/resultados-redes.csv")["text"], "a,b\n1,2\n")
        for path in ("Tesis/binario.bin", "Tesis/grande.txt"):
            with self.assertRaises(ValueError): self.files.read_text(path)

    def test_mutations_never_overwrite_and_need_writable_folders(self):
        self.files.create_draft("Tesis", "idea", "uno")
        self.assertEqual((self.home / "Tesis/idea.md").read_text(), "uno")
        with self.assertRaises(FileExistsError): self.files.create_draft("Tesis", "idea.md", "dos")
        for name in ("../escape.md", ".oculto.md", "script.sh"):
            with self.assertRaises(ValueError): self.files.create_draft("Tesis", name, "x")
        with self.assertRaises(PermissionError): self.files.create_draft("Lectura", "x.md", "x")
        (self.home / "Tesis/datos/idea.md").write_text("otra")
        with self.assertRaises(FileExistsError): self.files.move("Tesis/idea.md", "Tesis/datos")
        with self.assertRaises(FileExistsError): self.files.copy("Tesis/idea.md", "Tesis/datos")
        with self.assertRaises(ValueError): self.files.move("Tesis/datos", "Tesis/datos")
        with self.assertRaises(PermissionError): self.files.trash("Lectura")
        self.assertEqual((self.home / "Tesis/datos/idea.md").read_text(), "otra")

    def test_trash_follows_freedesktop_and_restores(self):
        (self.home / "Tesis/borrador.md").write_text("v1")
        self.files.trash("Tesis/borrador.md")
        (self.home / "Tesis/borrador.md").write_text("v2")
        result, undo = self.files.trash("Tesis/borrador.md")
        self.assertEqual(undo["name"], "borrador.md.2")
        info = (trash_dir() / "info/borrador.md.2.trashinfo").read_text()
        self.assertIn(f"Path={self.home}/Tesis/borrador.md", info)
        self.assertIn("DeletionDate=", info)
        self.assertEqual((trash_dir() / "files/borrador.md.2").read_text(), "v2")
        self.assertFalse((self.home / "Tesis/borrador.md").exists())


class UndoTests(Sandbox):
    def actions(self, desktop=None):
        return Actions(None, files=self.files, journal=self.journal, reminders=self.reminders, desktop=desktop)

    def test_undo_reverts_the_last_plan_in_reverse_order(self):
        actions = self.actions()
        token = PLAN.set("plan-1")
        actions("files.create_draft", {"folder": "Tesis", "name": "idea.md", "text": "hola"})
        actions("files.rename", {"path": "Tesis/idea.md", "new_name": "idea-final.md"})
        actions("files.copy", {"source": "Tesis/idea-final.md", "destination_folder": "Tesis/datos"})
        PLAN.reset(token)
        token = PLAN.set("plan-2")
        (self.home / "Tesis/viejo.txt").write_text("viejo")
        actions("files.trash", {"path": "Tesis/viejo.txt"})
        PLAN.reset(token)
        self.assertIn("files.trash", self.journal.describe())
        self.assertEqual(actions("undo.last", {})["undone"], [f"{self.home}/Tesis/viejo.txt restaurado de la papelera"])
        self.assertEqual((self.home / "Tesis/viejo.txt").read_text(), "viejo")
        undone = actions("undo.last", {})["undone"]
        self.assertEqual(len(undone), 3)
        for path in ("Tesis/idea.md", "Tesis/idea-final.md", "Tesis/datos/idea-final.md"):
            self.assertFalse((self.home / path).exists(), path)
        self.assertEqual(len(list((trash_dir() / "files").iterdir())), 2)  # Created files: trashed, not deleted.
        self.assertEqual(actions("undo.last", {}), "No hay operaciones de Leo para deshacer")

    def test_undo_stops_instead_of_overwriting(self):
        actions = self.actions()
        actions("files.create_draft", {"folder": "Tesis", "name": "a.md", "text": "a"})
        actions("files.move", {"source": "Tesis/a.md", "destination_folder": "Tesis/datos"})
        (self.home / "Tesis/a.md").write_text("nuevo del usuario")
        with self.assertRaises(FileExistsError): actions("undo.last", {})
        self.assertEqual((self.home / "Tesis/a.md").read_text(), "nuevo del usuario")
        self.assertTrue((self.home / "Tesis/datos/a.md").exists())

    def test_journal_is_private_bounded_and_persistent(self):
        for i in range(LIMIT + 5):
            self.journal.record("note.save", f"n{i}", {"op": "trash_created", "path": f"/x/{i}"})
        self.assertEqual(self.journal.path.stat().st_mode & 0o777, 0o600)
        entries = json.loads(self.journal.path.read_text())
        self.assertEqual(len(entries), LIMIT)
        self.assertEqual(Journal().last_plan()[0]["summary"], f"n{LIMIT + 4}")

    def test_settings_and_reminders_are_journaled_and_reverted(self):
        values = {"org.gnome.desktop.interface": {"color-scheme": "default"},
                  "org.gnome.desktop.notifications": {"show-banners": True}}

        class Fake:
            def __init__(self, schema): self.data = values[schema]
            def get_string(self, key): return self.data[key]
            def set_string(self, key, value): self.data[key] = value
            def get_boolean(self, key): return self.data[key]
            def set_boolean(self, key, value): self.data[key] = value
        calls = []

        def run(argv, **kwargs):
            calls.append(argv)
            return subprocess.CompletedProcess(argv, 0, "Volume: 0.35\n", "")
        actions = self.actions(Desktop(Fake, run))
        actions("settings.set", {"key": "dark_mode", "value": "on"})
        self.assertEqual(values["org.gnome.desktop.interface"]["color-scheme"], "prefer-dark")
        actions("undo.last", {})
        self.assertEqual(values["org.gnome.desktop.interface"]["color-scheme"], "default")
        with patch("leo.desktop.shutil.which", return_value="/usr/bin/wpctl"):
            result = actions("settings.set", {"key": "volume", "value": "70"})
            self.assertEqual(result["previous"], "35")
            self.assertEqual(calls[-1], ["/usr/bin/wpctl", "set-volume", "@DEFAULT_AUDIO_SINK@", "0.70"])
            actions("undo.last", {})
            self.assertEqual(calls[-1][-1], "0.35")
        for key, value in (("volume", "200"), ("volume", "1;reboot"), ("dark_mode", "yes"), ("proxy", "on")):
            with self.assertRaises(ValueError): actions("settings.set", {"key": key, "value": value})
        later = (datetime.now() + timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M")
        created = actions("reminder.create", {"text": "Enviar notas", "at": later})
        actions("undo.last", {})
        self.assertEqual(self.reminders.list()["pending"], [])
        token = PLAN.set("cancel-plan")
        again = actions("reminder.create", {"text": "Enviar notas", "at": later})
        PLAN.reset(token)
        actions("reminder.cancel", {"id": again["id"]})
        actions("undo.last", {})
        self.assertEqual([r["id"] for r in self.reminders.list()["pending"]], [again["id"]])
        self.assertNotEqual(created["id"], again["id"])


class ReminderTests(Sandbox):
    def test_parse_fire_missed_and_persist(self):
        now = [datetime(2026, 10, 23, 9, 0).timestamp()]
        reminders = Reminders(Events(self.sent.append), clock=lambda: now[0])
        reminders.create("Clase de redes", "2026-10-23T09:15")
        reminders.timer(5, "té")
        for bad in ("ayer", "2026-10-22T09:00", "2028-01-01T00:00"):
            with self.assertRaises(ValueError): reminders.create("x", bad)
        with self.assertRaises(ValueError): reminders.timer(0, "x")
        now[0] += 6 * 60
        self.assertEqual(reminders.check(), 1)
        self.assertEqual(self.sent[-1]["title"], "Temporizador terminado: té")
        self.assertEqual(reminders.path.stat().st_mode & 0o777, 0o600)
        now[0] += 3600  # Leo was off: the reminder is reported as missed on start.
        restarted = Reminders(Events(self.sent.append), clock=lambda: now[0])
        self.assertEqual(restarted.check(missed=True), 1)
        self.assertEqual(self.sent[-1]["title"], "Recordatorio perdido (23/10 09:15): Clase de redes")
        self.assertEqual(restarted.list(), {"pending": []})


if __name__ == "__main__":
    unittest.main()
