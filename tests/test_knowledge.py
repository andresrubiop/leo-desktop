"""Límites de autorización de la conexión con Cerveau (antes ContextOS), sin documentos personales."""
import subprocess
from unittest.mock import patch

from test_profile import ProfileSandbox
from leo.cerveau import Cerveau, page_of
from leo.knowledge import Knowledge


class KnowledgeTests(ProfileSandbox):
    def test_revoked_permissions_hidden_missing_and_symlink_results_are_filtered(self):
        self.settings.update({"files": True})
        root = self.home / "Investigacion"
        (root / "ok.md").write_text("dato ficticio")
        (root / ".oculto").mkdir()
        (root / ".oculto/x.md").write_text("oculto")
        (self.home / "otro.md").write_text("fuera")
        (root / "link.md").symlink_to(self.home / "otro.md")

        class Backend:
            def search(inner, query, roots, limit):
                return [{"path": str(root / p), "page": None, "snippet": "dato"}
                        for p in ("ok.md", ".oculto/x.md", "link.md", "ausente.md")]
        knowledge = Knowledge(self.settings, Backend())
        self.assertEqual([r["path"] for r in knowledge.search("dato")["results"]], [str(root / "ok.md")])
        self.settings.update({"files": False})
        with self.assertRaises(PermissionError): knowledge.search("dato")
        self.settings.update({"files": True})

        def revoke(*args):
            self.settings.update({"folders": []})
            return [{"path": str(root / "ok.md"), "page": None, "snippet": "dato"}]
        knowledge.backend.search = revoke
        self.assertEqual(knowledge.search("dato")["results"], [])

    def test_cli_failures_do_not_leak_output(self):
        backend = Cerveau(executable="/usr/bin/false", workspace=self.home)
        with patch("leo.cerveau.subprocess.run", return_value=subprocess.CompletedProcess([], 1, b"", b"PRIVATE")):
            with self.assertRaisesRegex(RuntimeError, "no completó") as caught:
                backend.call("project", "list")
            self.assertNotIn("PRIVATE", str(caught.exception))
        with patch("leo.cerveau.subprocess.run", side_effect=subprocess.TimeoutExpired([], 1)):
            with self.assertRaisesRegex(RuntimeError, "agotó"): backend.call("project", "list")

    def test_index_requires_files_permission_and_course_folder(self):
        knowledge = Knowledge(self.settings, Cerveau())
        with self.assertRaises(PermissionError): knowledge.index()
        self.settings.update({"files": True, "folders": []})
        with self.assertRaises(PermissionError): knowledge.index()

    def test_projects_must_match_authorized_root_exactly(self):
        backend = Cerveau(workspace=self.home)
        root = str(self.home / "Investigacion")
        with patch.object(backend, "initialized", return_value=True), patch.object(backend, "call", return_value=[
                {"id": "all", "path": str(self.home)}, {"id": "course", "path": root}]):
            self.assertEqual([p["id"] for p in backend.projects([root])], ["course"])

    def test_document_snippets_are_cited_by_page_not_by_extracted_lines(self):
        root = self.home / "Investigacion"
        (root / "paper.pdf").write_bytes(b"%PDF ficticio")
        pages = [{"page": 1, "text": "Introduccion"}, {"page": 2, "text": "La tasa de\naprendizaje   fue 0.001 en CartPole"}]
        self.assertEqual(page_of(pages, "\nla\nLa tasa de aprendizaje fue 0.001\n"), 2)
        self.assertIsNone(page_of(pages, "texto que no aparece"))
        backend = Cerveau(workspace=self.home)
        reply = {"results": [{"source": "file", "project": "r", "path": "paper.pdf", "content": "La tasa de aprendizaje",
                              "start_line": 40, "end_line": 41}]}
        with patch.object(backend, "projects", return_value=[{"id": "r", "path": str(root)}]), \
                patch.object(backend, "call", return_value=reply), patch("leo.cerveau.documents.pages", return_value=pages):
            found = backend.search("tasa", [str(root)], 8)
        self.assertEqual((found[0]["page"], found[0]["start_line"], found[0]["end_line"]), (2, None, None))
