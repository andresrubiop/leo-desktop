"""F6.4: proyecto en el que trabaja el usuario a partir del título de la ventana que Leo observa."""
from pathlib import Path
import subprocess
from unittest.mock import Mock

from test_actions import Sandbox
from leo.context import Projects, hints
from leo.screen import Attachments
from leo.watch import Watcher, asks


def repo(path, progress=None):
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", "-b", "main", str(path)], check=True)
    if progress:
        (path / "docs").mkdir(exist_ok=True)
        (path / "docs/progress.md").write_text(progress)
    return path


class ContextTests(Sandbox):
    def setUp(self):
        super().setUp()
        self.tesis = repo(self.home / "Tesis", "# Estado\n## Fase 1\nTodo bien.\n## Fase 2\nPróximo paso: entrenar el modelo grande.\n")
        repo(self.home / "Cursos/Redes")
        repo(self.home / "a/Dup")
        repo(self.home / "b/Dup")
        repo(self.home / ".config/oculto")
        self.projects = Projects(home=self.home)

    def test_titles_of_terminals_and_editors(self):
        self.assertEqual(hints("train.py — Tesis — Visual Studio Code"), ([], ["Tesis"]))
        self.assertEqual(hints("ana@pc: ~/Tesis/codigo"), (["~/Tesis/codigo"], []))
        for title, expected in (("ana@pc: ~/Tesis/datos/crudos", self.tesis),  # A missing subfolder still maps.
                                (f"{self.home}/Tesis", self.tesis), ("notas.md — Redes — Visual Studio Code", self.home / "Cursos/Redes"),
                                ("x.py — Dup — Visual Studio Code", None),  # Ambiguous: not guessed.
                                ("~/.config/oculto", None), ("Firefox", None), ("", None)):
            self.assertEqual(self.projects.resolve(title), expected, title)

    def test_card_with_branch_changes_and_next_step(self):
        (self.tesis / "nuevo.py").write_text("print(1)\n")
        card = self.projects.card(self.tesis)
        self.assertEqual((card["name"], card["branch"], card["changes"]), ("Tesis", "main", 2))  # docs/ + nuevo.py
        self.assertEqual(card["next"], "Próximo paso: entrenar el modelo grande.")
        self.assertIsNone(self.projects.card(self.home / "Cursos/Redes")["next"])

    def test_watcher_knows_the_project_only_while_watching(self):
        self.settings.update({"watch": True, "screen": True})
        watcher = Watcher(self.settings, Mock(), Mock(), Attachments(), read=lambda pixbuf: "")
        watcher.projects = self.projects
        self.assertIsNone(watcher.set_window(":1.4", "~/Tesis")["context"])  # Not watching yet.
        watcher.set_state(":1.4", "perched")
        self.assertIsNone(watcher.set_window(":1.5", "~/Tesis")["context"])  # Another client.
        self.assertEqual(watcher.set_window(":1.4", "~/Tesis")["context"]["name"], "Tesis")
        self.assertIn("Proyecto: Tesis (rama main", watcher.describe())
        self.assertIn("solo esa", watcher.describe())
        watcher.set_state(":1.4", "off")
        self.assertIsNone(watcher.context)
        self.assertIn("estoy quieto", watcher.describe())
        self.settings.update({"watch": False})
        self.assertIn("apagado", watcher.describe())
        with self.assertRaises(ValueError):
            watcher.set_window(":1.4", "x" * 301)

    def test_questions_about_what_leo_watches(self):
        for text in ("Leo, ¿qué estás mirando?", "¿Qué estás observando?", "que andas vigilando", "Leo, ¿qué ves?",
                     "¿Y qué ves?", "¿Qué es lo que ves?", "Descríbeme lo que ves", "¿qué hay ahí?"):
            self.assertTrue(asks(text), text)
        for text in ("¿Qué ves en mi pantalla?", "mira esto", "¿Qué estás haciendo?", "¿qué estás viendo en la ventana?",
                     "¿Qué opinas?"):
            self.assertFalse(asks(text), text)
