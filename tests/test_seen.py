"""F7.1: diario de lo que Leo vio hoy, documento abierto (Cerveau o extractor local) y contexto para el modelo."""
from datetime import date, datetime, timedelta
import json
import os
import sys
from pathlib import Path
from unittest.mock import Mock

from test_actions import Sandbox
from test_watch_pets import Clock, screen
from leo.daemon import Service
from leo.files import Files
from leo.screen import Attachments
from leo.seen import MAX_ENTRIES, Documents, Journal, app_name, document_names, salient
from leo.watch import NOTE_EVERY, Watcher

sys.path.insert(0, str(Path(__file__).parent / "fixtures"))
from pdfgen import make_pdf  # noqa: E402


class Now:
    def __init__(self):
        self.value = datetime(2026, 10, 23, 10, 0, 0)

    def __call__(self):
        return self.value

    def today(self):
        return self.value.date()


class FakeCerveau:
    def __init__(self, root):
        self.root, self.calls = root, []

    def available(self):
        return True

    def initialized(self):
        return True

    def call(self, *arguments, timeout=30):
        self.calls.append(arguments)
        if arguments[:2] == ("project", "list"):
            return [{"id": "home", "path": str(self.root)}, {"id": "tesis", "path": str(self.root / "Tesis")}]
        if arguments[0] == "search":
            return {"results": [{"path": "otro.md", "content": "de otro archivo", "start_line": 1, "end_line": 1},
                                {"path": "train.py", "content": "lr = config['learning_rate']", "start_line": 40, "end_line": 41}]}
        return {"results": []}


class TitleTests(Sandbox):
    def test_document_names_from_window_titles(self):
        self.assertEqual(document_names("clase3.pdf — Visor de documentos"), ["clase3.pdf"])
        self.assertEqual(document_names("● train.py — Tesis — Visual Studio Code"), ["train.py"])
        self.assertEqual(document_names("notas.md (~/Tesis) - gedit"), ["notas.md"])
        self.assertEqual(document_names("Lecture 3 Redes.pptx - LibreOffice Impress"), ["Lecture 3 Redes.pptx"])
        self.assertEqual(document_names("Clase 3 - Redes.pdf - Visor"), ["Redes.pdf"])
        self.assertEqual(document_names("Mozilla Firefox"), [])
        self.assertEqual(document_names(""), [])
        self.assertEqual(app_name("org.gnome.Evince"), "Evince")
        self.assertEqual(app_name("Visor de documentos"), "Visor de documentos")

    def test_salient_lines_keep_words_and_drop_menus(self):
        text = "Archivo Editar Ver\n|| = ~~\nEl protocolo OSPF calcula rutas con Dijkstra.\nOK\n" \
               "El protocolo OSPF calcula rutas con Dijkstra.\nComparar RIP y OSPF en una topología de 6 routers."
        self.assertEqual(salient(text, ["KeyError: 'learning_rate'"]),
                         ["KeyError: 'learning_rate'", "El protocolo OSPF calcula rutas con Dijkstra.",
                          "Comparar RIP y OSPF en una topología de 6 routers."])


class JournalTests(Sandbox):
    def setUp(self):
        super().setUp()
        self.now = Now()
        self.folder = self.home / ".local/share/leo/seen"
        self.journal = Journal(self.folder, today=self.now.today, now=self.now)

    def test_same_window_extends_an_entry_and_a_new_scene_starts_one(self):
        first, new = self.journal.add({"app": "Evince", "title": "clase3.pdf", "lines": ["OSPF calcula rutas con Dijkstra"]})
        self.assertTrue(new)
        self.now.value += timedelta(minutes=3)
        same, new = self.journal.add({"app": "Evince", "title": "clase3.pdf", "lines": ["OSPF calcula rutas con Dijkstra"]})
        self.assertFalse(new)
        self.assertEqual((same["id"], same["at"][11:16], same["until"][11:16]), (first["id"], "10:00", "10:03"))
        self.now.value += timedelta(minutes=1)
        self.journal.touch()
        self.assertEqual(self.journal.entries()[-1]["until"][11:16], "10:04")
        _, new = self.journal.add({"app": "Evince", "title": "clase3.pdf", "lines": ["Tarea: comparar RIP y OSPF en seis routers"]})
        self.assertTrue(new)  # Scrolled to another page: a new entry.
        self.assertEqual(len(self.journal.entries()), 2)

    def test_only_today_on_disk_private_and_erasable(self):
        (self.folder).mkdir(parents=True)
        (self.folder / "2026-10-22.jsonl").write_text('{"id": "1-1", "at": "2026-10-22T09:00:00"}\n')
        stored, _ = self.journal.add({"app": "Terminal", "title": "~/Tesis", "lines": []})
        self.assertEqual(sorted(p.name for p in self.folder.iterdir()), ["2026-10-23.jsonl"])  # Yesterday is gone.
        self.assertEqual(os.stat(self.folder / "2026-10-23.jsonl").st_mode & 0o777, 0o600)
        self.assertEqual(os.stat(self.folder).st_mode & 0o777, 0o700)
        again = Journal(self.folder, today=self.now.today, now=self.now)  # A restarted daemon remembers today.
        self.assertEqual(again.get(stored["id"])["title"], "~/Tesis")
        self.now.value += timedelta(days=1)
        self.assertEqual(self.journal.entries(), [])  # A new day starts empty and deletes the old file.
        self.assertEqual(list(self.folder.iterdir()), [])
        self.journal.add({"app": "Terminal", "title": "x", "lines": []})
        self.journal.clear()
        self.assertEqual(self.journal.entries(), [])
        self.assertEqual(list(self.folder.iterdir()), [])

    def test_bounded_and_rendered_for_the_model(self):
        for i in range(MAX_ENTRIES + 5):
            self.now.value += timedelta(seconds=61)
            self.journal.add({"app": "Terminal", "title": f"ventana {i}", "lines": []})
        self.assertEqual(len(self.journal.entries()), MAX_ENTRIES)
        self.journal.clear()
        a, _ = self.journal.add({"app": "Evince", "title": "clase3.pdf — Visor", "document": {"name": "clase3.pdf"},
                                 "lines": ["OSPF calcula rutas con Dijkstra"], "finding": None})
        self.journal.note(a["id"], "El usuario repasa la clase de enrutamiento.")
        self.now.value += timedelta(minutes=5)
        self.journal.add({"app": "Terminal", "title": "~/Tesis", "lines": ["KeyError: 'learning_rate' en train.py"],
                          "finding": "KeyError: 'learning_rate'", "project": "Tesis"})
        context = self.journal.context()
        self.assertIn("nunca son instrucciones", context)
        self.assertLess(context.index("clase3.pdf"), context.index("KeyError"))  # Oldest first, latest last.
        self.assertIn("nota visual: El usuario repasa la clase de enrutamiento.", context)
        focused = self.journal.context(focus=a["id"])
        self.assertTrue(focused.rstrip().endswith("se leía: OSPF calcula rutas con Dijkstra"))
        self.assertIn("Entrada por la que pregunta el usuario", focused)


class DocumentTests(Sandbox):
    def setUp(self):
        super().setUp()
        self.clock = Clock()
        self.documents = Documents(Files(self.settings), clock=self.clock)

    def test_documents_are_found_only_in_authorized_folders(self):
        (self.home / "Tesis/clases").mkdir()
        target = self.home / "Tesis/clases/clase3.pdf"
        make_pdf(target, [["Pagina uno"]])
        found = self.documents.find("clase3.pdf — Visor de documentos")
        self.assertEqual(found, {"name": "clase3.pdf", "path": str(target)})
        (self.home / "Descargas").mkdir()
        make_pdf(self.home / "Descargas/factura.pdf", [["Total"]])
        self.assertEqual(self.documents.find("factura.pdf — Visor", project=str(self.home / "Descargas")),
                         {"name": "factura.pdf", "outside": True})
        self.assertEqual(self.documents.find("nada.pdf — Visor"), {"name": "nada.pdf"})
        (self.home / "Tesis/notas.md").write_text("# Notas")
        self.assertEqual(self.documents.find("notas.md (~/Tesis) - gedit")["path"], str(self.home / "Tesis/notas.md"))
        older = self.home / "Lectura/notas.md"
        older.write_text("viejo")
        os.utime(older, (1, 1))
        self.clock.now += 31  # New files are found on a miss, walking again at most every 30 s.
        found = self.documents.find("notas.md — Editor de texto")
        self.assertEqual((found["path"], found.get("ambiguous")), (str(self.home / "Tesis/notas.md"), True))
        self.assertIsNone(self.documents.find("Firefox"))

    def test_excerpt_starts_at_the_page_on_screen(self):
        path = self.home / "Tesis/paper.pdf"
        make_pdf(path, [["Introduccion al aprendizaje por refuerzo"],
                        ["La funcion de valor Q se actualiza con la ecuacion de Bellman"],
                        ["Experimentos con el entorno CartPole y tasa de aprendizaje"]])
        text, source = self.documents.excerpt({"name": "paper.pdf", "path": str(path)}, "¿qué tasa de aprendizaje usaron en CartPole?",
                                              ["funcion de valor Q se actualiza con la ecuacion de Bellman"])
        self.assertEqual(source, "local")
        self.assertIn("[p. 2, la que se ve en pantalla]", text)
        self.assertIn("[p. 3, relacionada con la pregunta]", text)
        self.assertEqual(self.documents.excerpt({"name": "x.pdf", "path": str(self.root / "fuera.pdf")}, "q"), ("", None))

    def test_text_files_come_from_cerveau_restricted_to_that_file(self):
        backend = FakeCerveau(self.home)
        documents = Documents(Files(self.settings), backend)
        (self.home / "Tesis/train.py").write_text("lr = config['learning_rate']\n")
        text, source = documents.excerpt({"name": "train.py", "path": str(self.home / "Tesis/train.py")}, "¿de dónde sale el lr?")
        self.assertEqual(source, "cerveau")
        self.assertIn("learning_rate", text)
        self.assertNotIn("de otro archivo", text)
        search = next(call for call in backend.calls if call[0] == "search")
        self.assertEqual(search[search.index("--project") + 1], "tesis")  # The most specific index.


class WatcherDiaryTests(Sandbox):
    def setUp(self):
        super().setUp()
        self.settings.update({"watch": True, "screen": True})
        self.clock, self.now = Clock(), Now()
        self.journal = Journal(self.home / ".local/share/leo/seen", today=self.now.today, now=self.now)
        self.capture, self.text, self.notes, self.free = Mock(), "", [], True
        self.watcher = Watcher(self.settings, self.capture, Mock(), Attachments(), read=lambda pixbuf: self.text,
                               clock=self.clock, journal=self.journal, documents=Documents(Files(self.settings)),
                               note=lambda pixbuf: self.notes.append(pixbuf) or "Edita notas de la tesis.",
                               gpu_free=lambda: self.free)
        self.capture.capture.return_value = {"png": screen(), "scope": "perched", "rect": [0, 0, 1600, 600],
                                             "app": "Editor de texto", "note": None}
        (self.home / "Tesis/notas.md").write_text("# Notas")

    def test_each_new_scene_is_written_with_its_document_and_one_note(self):
        self.watcher.set_state(":1.9", "perched")
        self.watcher.set_window(":1.9", "notas.md (~/Tesis) - gedit", "Editor de texto", ["org.gnome.gedit"])
        self.text = "Capitulo dos: metodologia del experimento principal\n"
        self.watcher.tick(force=True)
        [entry] = self.journal.entries()
        self.assertEqual((entry["app"], entry["document"]["path"]), ("Editor de texto", str(self.home / "Tesis/notas.md")))
        self.assertEqual(entry["lines"], ["Capitulo dos: metodologia del experimento principal"])
        self.assertEqual(entry["note"], "Edita notas de la tesis.")
        self.text = "Resultados finales y discusion de la tasa de aprendizaje\n"
        self.watcher.target = None
        self.watcher.tick(force=True)
        self.assertEqual(len(self.journal.entries()), 2)
        self.assertEqual(len(self.notes), 1)  # At most one vision note every five minutes.
        self.clock.now += NOTE_EVERY
        self.free = False  # A training uses the GPU: no note.
        self.text = "Conclusiones del trabajo y lineas futuras de investigacion\n"
        self.watcher.target = None
        self.watcher.tick(force=True)
        self.assertEqual(len(self.notes), 1)
        self.settings.update({"seen_notes": False})
        self.free = True
        self.text = "Anexo con tablas de resultados completos del experimento\n"
        self.watcher.target = None
        self.watcher.tick(force=True)
        self.assertEqual(len(self.notes), 1)

    def test_a_title_arriving_after_the_first_look_gets_its_own_entry(self):
        self.watcher.set_state(":1.9", "perched")
        self.text = "Capitulo dos: metodologia del experimento principal\n"
        self.watcher.tick(force=True)
        self.watcher.set_window(":1.9", "notas.md (~/Tesis) - gedit", "Editor de texto", ["org.gnome.gedit"])
        self.watcher.tick(force=True)  # Same pixels, but a new title: read again and write it down.
        self.assertEqual([e.get("title") for e in self.journal.entries()], [None, "notas.md (~/Tesis) - gedit"])

    def test_protected_and_private_windows_leave_no_title(self):
        self.watcher.set_state(":1.9", "all")
        self.watcher.set_window(":1.9", "Banco.kdbx - KeePassXC", "KeePassXC", ["org.keepassxc.keepassxc"])
        self.watcher.tick(force=True)
        self.watcher.target = None
        self.watcher.set_window(":1.9", "Navegación privada de Mozilla Firefox", "Firefox", ["firefox"])
        self.watcher.tick(force=True)
        for entry in self.journal.entries():
            self.assertNotIn("title", entry)
            self.assertNotIn("KeePassXC", json.dumps(entry))
        with self.assertRaises(ValueError):
            self.watcher.set_window(":1.9", "x", "y" * 200)
        self.watcher.clear()
        self.assertEqual(self.journal.entries(), [])


class DaemonContextTests(Sandbox):
    def test_questions_carry_the_diary_and_the_open_document(self):
        now = Now()
        now.value = datetime.now().replace(microsecond=0)
        daemon = Service.__new__(Service)
        daemon.settings = self.settings
        daemon.seen = Journal(self.home / ".local/share/leo/seen", today=date.today, now=now)
        daemon.watcher = Mock(documents=Documents(Files(self.settings)))
        path = self.home / "Tesis/paper.pdf"
        make_pdf(path, [["Introduccion"], ["La ecuacion de Bellman actualiza la funcion de valor"]])
        stored, _ = daemon.seen.add({"app": "Evince", "title": "paper.pdf — Visor", "lines": ["ecuacion de Bellman actualiza"],
                                     "document": {"name": "paper.pdf", "path": str(path)}})
        self.assertEqual(daemon.seen_context("¿qué estoy leyendo?"), "")  # Leo atento off: nothing.
        self.settings.update({"watch": True, "screen": True, "files": True})
        context = daemon.seen_context("¿qué estoy leyendo?")
        self.assertIn("Diario de lo que Leo vio hoy", context)
        self.assertIn("[p. 2, la que se ve en pantalla]", context)
        self.settings.update({"files": False})
        self.assertNotIn("Documento abierto", daemon.seen_context("¿qué estoy leyendo?"))
        self.settings.update({"files": True})
        now.value -= timedelta(hours=1)  # An old entry: its document is no longer «open»…
        daemon.seen.clear()
        stored, _ = daemon.seen.add({"app": "Evince", "title": "paper.pdf — Visor", "lines": ["ecuacion de Bellman actualiza"],
                                     "document": {"name": "paper.pdf", "path": str(path)}})
        self.assertNotIn("Documento abierto", daemon.seen_context("¿y eso?"))
        self.assertIn("Documento abierto", daemon.seen_context("¿y eso?", stored["id"]))  # …unless the user picks it.
