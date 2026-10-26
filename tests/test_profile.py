"""Fase 3: carpetas de curso/investigación, memoria, horario, documentos y búsqueda con citas.
Todo con fixtures sintéticos (curso, papers y horario ficticios) en un HOME temporal."""
from datetime import datetime, timedelta
import json
from pathlib import Path
import sys
import unittest
import zipfile

from test_actions import Sandbox
from leo.actions import Actions
from leo.documents import marked, pages, summarize
from leo.events import Events
from leo.journal import Journal
from leo.knowledge import Knowledge, Unavailable
from leo.memory import PROMPT_CHARS, Memory
from leo.schedule import Schedule, parse_ics, parse_timetable_text, import_file

sys.path.insert(0, str(Path(__file__).resolve().parent / "fixtures"))
from pdfgen import make_pdf  # noqa: E402

GRID = ["Horario docente 2026-2027 (ficticio)", "",
        "Hora           Lunes              Martes             Miércoles          Jueves",
        "08:00-10:00    Redes Neuronales                      Redes Neuronales",
        "               Aula 3.2                              Aula 3.2",
        "10:00-12:00                       Visión Artificial                     Seminario TFM",
        "                                  Lab 1.04                              Sala B", "",
        "Coordinación: Departamento ficticio"]
ICS = """BEGIN:VCALENDAR
BEGIN:VEVENT
DTSTART;TZID=Europe/Madrid:20260907T080000
DTEND;TZID=Europe/Madrid:20260907T100000
RRULE:FREQ=WEEKLY;BYDAY=MO,WE;UNTIL=20270115T000000Z
SUMMARY:Redes Neuronales
LOCATION:Aula 3.2
END:VEVENT
BEGIN:VEVENT
DTSTART:20260908T100000
DTEND:20260908T120000
SUMMARY:Visión
  Artificial
LOCATION:Lab 1.04
END:VEVENT
BEGIN:VEVENT
DTSTART:20260915T100000
DTEND:20260915T120000
SUMMARY:Visión Artificial
LOCATION:Lab 1.04
END:VEVENT
BEGIN:VEVENT
DTSTART:20261012T090000
DTEND:20261012T100000
SUMMARY:Reunión única
END:VEVENT
END:VCALENDAR
"""


def office(path, files):
    with zipfile.ZipFile(path, "w") as archive:
        for name, text in files.items():
            archive.writestr(name, text)


class ProfileSandbox(Sandbox):
    def setUp(self):
        super().setUp()
        for path in ("Cursos/Redes/semana3", "Investigacion"):
            (self.home / path).mkdir(parents=True)
        self.settings.update({"folders": [
            {"id": "tesis", "path": str(self.home / "Tesis"), "label": "Tesis", "write": True},
            {"id": "redes", "path": str(self.home / "Cursos/Redes"), "label": "Redes Neuronales", "write": True, "kind": "curso"},
            {"id": "inv", "path": str(self.home / "Investigacion"), "label": "Investigación", "write": False,
             "kind": "investigacion"}]})


class FolderKindTests(ProfileSandbox):
    def test_course_folders_only_change_inside_leo_borradores(self):
        result, _ = self.files.create_draft("Cursos/Redes/semana3", "sesion-8.md", "# Sesión 8")
        self.assertEqual(result["created"], str(self.home / "Cursos/Redes/Leo-borradores/sesion-8.md"))
        (self.home / "Cursos/Redes/semana3/apuntes.md").write_text("del profesor")
        for call in (lambda: self.files.rename("Cursos/Redes/semana3/apuntes.md", "x.md"),
                     lambda: self.files.trash("Cursos/Redes/semana3/apuntes.md"),
                     lambda: self.files.move("Cursos/Redes/semana3/apuntes.md", "Tesis"),
                     lambda: self.files.trash("Cursos/Redes/Leo-borradores")):
            with self.assertRaises(PermissionError): call()
        self.files.copy("Cursos/Redes/semana3/apuntes.md", "Cursos/Redes")  # Lands in Leo-borradores.
        self.assertTrue((self.home / "Cursos/Redes/Leo-borradores/apuntes.md").exists())
        self.files.rename("Cursos/Redes/Leo-borradores/sesion-8.md", "sesion-8-v2.md")
        self.assertEqual((self.home / "Cursos/Redes/semana3/apuntes.md").read_text(), "del profesor")
        with self.assertRaises(PermissionError): self.files.create_draft("Investigacion", "x.md", "x")
        self.files.create_draft("Tesis", "libre.md", "general folders keep F2 rules")
        with self.assertRaises(ValueError):
            self.settings.update({"folders": [{"id": "x", "path": str(self.home / "Tesis"), "label": "x",
                                               "write": False, "kind": "secreto"}]})


class MemoryTests(ProfileSandbox):
    def test_memory_proposals_undo_prompt_and_panel(self):
        memory = Memory()
        actions = Actions(None, files=self.files, journal=self.journal, memory=memory)
        fact = actions("memory.remember", {"text": "Prefiere respuestas cortas <b>"})
        self.assertEqual(fact["text"], "Prefiere respuestas cortas ‹b›")
        with self.assertRaises(ValueError): memory.add("prefiere respuestas cortas ‹b›")
        actions("undo.last", {})
        self.assertEqual(memory.list(), {"facts": []})
        kept = memory.add("Es profesor de Redes Neuronales", "usuario")[0]
        actions("memory.forget", {"id": kept["id"]})
        actions("undo.last", {})
        self.assertEqual([f["text"] for f in Memory().list()["facts"]], ["Es profesor de Redes Neuronales"])
        self.assertEqual(memory.path.stat().st_mode & 0o777, 0o600)
        memory.dispatch("update", {"id": kept["id"], "text": "Es profesor e investigador"})
        for i in range(40):
            memory.add(f"Hecho de prueba número {i} con algo de texto para llenar el tope")
        prompt = memory.prompt()
        self.assertLessEqual(len(prompt), PROMPT_CHARS + 200)
        self.assertIn("número 39", prompt)
        self.assertNotIn("Es profesor e investigador", prompt)  # Oldest facts give way first.
        for bad in (("add", {"text": ""}), ("delete", {"id": "1"}), ("drop", {}), ("add", {"text": "x" * 201})):
            with self.assertRaises(ValueError): memory.dispatch(*bad)


class ScheduleTests(ProfileSandbox):
    def schedule(self, now):
        self.clock = [now.timestamp()]
        return Schedule(Events(self.sent.append), self.settings, clock=lambda: self.clock[0])

    def test_imports_grid_pdf_list_text_and_ics(self):
        make_pdf(self.root / "horario.pdf", [GRID])
        grid = import_file(self.root / "horario.pdf")["courses"]
        self.assertEqual([(c["name"], c["day"], c["start"], c["room"]) for c in grid],
                         [("Redes Neuronales", 0, "08:00", "Aula 3.2"), ("Visión Artificial", 1, "10:00", "Lab 1.04"),
                          ("Redes Neuronales", 2, "08:00", "Aula 3.2"), ("Seminario TFM", 3, "10:00", "Sala B")])
        listed = parse_timetable_text("Lunes\n  08:00 - 10:00  Redes Neuronales (Aula 3.2)\nMiércoles 9.00-11.00 Visión Artificial Lab 1.04\n")
        self.assertEqual([(c["name"], c["day"], c["start"], c["end"], c["room"]) for c in listed["courses"]],
                         [("Redes Neuronales", 0, "08:00", "10:00", "Aula 3.2"), ("Visión Artificial", 2, "09:00", "11:00", "Lab 1.04")])
        ics = parse_ics(ICS)
        self.assertEqual([(c["name"], c["day"], c["start"]) for c in ics["courses"]],
                         [("Redes Neuronales", 0, "08:00"), ("Visión Artificial", 1, "10:00"), ("Redes Neuronales", 2, "08:00")])
        self.assertEqual(ics["term"], {"start": "2026-09-07", "end": "2027-01-15"})
        (self.root / "notas.txt").write_text("x")
        with self.assertRaises(ValueError): import_file(self.root / "notas.txt")

    def test_alerts_once_respecting_term_holidays_and_setting(self):
        monday = datetime(2026, 10, 26, 7, 50)  # A Monday inside the term.
        schedule = self.schedule(monday)
        schedule.set([{"name": "Redes Neuronales", "day": 0, "start": "08:00", "end": "10:00", "room": "Aula 3.2",
                       "folder": "redes"}], {"start": "2026-09-07", "end": "2027-01-15"}, ["2026-11-02"])
        self.assertEqual(schedule.check(), 1)
        self.assertEqual(self.sent[-1]["title"], "En 10 min tienes Redes Neuronales en el salón Aula 3.2. ¿Abro los materiales?")
        self.assertEqual(schedule.check(), 0)  # Once per class and day, also after restarting.
        self.assertEqual(self.schedule(monday).check(), 0)
        holiday = self.schedule(datetime(2026, 11, 2, 7, 50))
        self.assertEqual(holiday.check(), 0)
        self.assertEqual(holiday.day("hoy")["nota"], "festivo")
        late = self.schedule(datetime(2027, 2, 1, 7, 50))
        self.assertEqual(late.check(), 0)
        self.settings.update({"class_alert_minutes": 0})
        self.assertEqual(self.schedule(datetime(2026, 11, 9, 7, 55)).check(), 0)
        sunday = self.schedule(datetime(2026, 11, 8, 20, 0)).day("mañana")
        self.assertEqual(sunday["clases"], [{"curso": "Redes Neuronales", "inicio": "08:00", "fin": "10:00",
                                             "salon": "Aula 3.2", "carpeta": str(self.home / "Cursos/Redes")}])
        for bad in ([{"name": "x", "day": 7, "start": "08:00", "end": "09:00"}],
                    [{"name": "x", "day": 0, "start": "10:00", "end": "09:00"}],
                    [{"name": "x", "day": 0, "start": "08:00", "end": "09:00", "folder": "otra"}]):
            with self.assertRaises(ValueError): schedule.set(bad)
        with self.assertRaises(ValueError): schedule.set([], {"start": "2027-01-01", "end": "2026-01-01"})


class DocumentTests(ProfileSandbox):
    def test_pages_from_pdf_and_office_formats(self):
        paper = self.home / "Investigacion/paper-atencion.pdf"
        make_pdf(paper, [["Attention for small datasets (ficticio)", "Abstract: we study attention."],
                         ["Method: a tiny transformer."], ["Results: accuracy 0.91 on synthetic data."]])
        self.assertEqual([p["page"] for p in pages(paper)], [1, 2, 3])
        self.assertIn("accuracy 0.91", pages(paper)[2]["text"])
        read = self.files.read_text("Investigacion/paper-atencion.pdf")
        self.assertEqual(read["pages"], 3)
        self.assertIn("[p. 3]\nResults", read["text"])
        w = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
        office(self.root / "a.docx", {"word/document.xml": f'<w:document {w}><w:body><w:p><w:r><w:t>Hola </w:t></w:r>'
                                                             '<w:r><w:t>mundo</w:t></w:r></w:p><w:p><w:r><w:t>Dos</w:t></w:r></w:p>'
                                                             '</w:body></w:document>'})
        self.assertEqual(pages(self.root / "a.docx"), [{"page": 1, "text": "Hola mundo\nDos"}])
        a = 'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"'
        office(self.root / "s.pptx", {f"ppt/slides/slide{n}.xml": f'<p:sld {a} xmlns:p="p"><a:p><a:r><a:t>Diapositiva {n}</a:t></a:r></a:p></p:sld>'
                                      for n in (1, 2, 10)})
        self.assertEqual([p["text"] for p in pages(self.root / "s.pptx")], ["Diapositiva 1", "Diapositiva 2", "Diapositiva 10"])
        ns = 'xmlns:office="o" xmlns:draw="d" xmlns:text="t"'
        office(self.root / "c.odp", {"content.xml": f'<office:document-content {ns}><draw:page><text:p><text:span>Uno</text:span></text:p>'
                                                    '</draw:page><draw:page><text:p>Dos</text:p></draw:page></office:document-content>'})
        self.assertEqual([p["text"] for p in pages(self.root / "c.odp")], ["Uno", "Dos"])
        office(self.root / "d.odt", {"content.xml": f'<office:document-content {ns}><text:h>Título</text:h><text:p>Cuerpo</text:p>'
                                                    '</office:document-content>'})
        self.assertEqual(pages(self.root / "d.odt"), [{"page": 1, "text": "Título\nCuerpo"}])
        (self.root / "t.tex").write_text("\\section{Intro} texto % comentario oculto\n50\\% real\n")
        self.assertNotIn("comentario", pages(self.root / "t.tex")[0]["text"])
        self.assertIn("50\\% real", pages(self.root / "t.tex")[0]["text"])
        office(self.root / "roto.docx", {"otro.xml": "<x/>"})
        with self.assertRaises(ValueError): pages(self.root / "roto.docx")

    def test_summary_by_page_groups_keeps_citations(self):
        document = [{"page": n, "text": f"Página {n}. " + "dato " * 900} for n in range(1, 13)]
        prompts = []

        def converse(prompt):
            prompts.append(prompt)
            pages_seen = [int(p) for p in __import__("re").findall(r"\[p\. (\d+)\]", prompt)]
            return f"- Nota (p. {pages_seen[0]})" if pages_seen else "Resumen final (p. 1) (p. 5)"
        result = summarize(converse, document, "resultados", "paper.pdf")
        self.assertEqual(result["pages"], 12)
        self.assertGreater(len(prompts), 2)
        self.assertTrue(prompts[0].startswith("Resume en 4 a 8 viñetas"))
        self.assertIn("(p. 5)", result["summary"])
        self.assertIn("resultados", prompts[-1])
        text, cut = marked(document, 100)
        self.assertTrue(cut and len(text) <= 100)
        with self.assertRaises(ValueError): summarize(converse, [], "", "vacio.pdf")

    def test_knowledge_results_stay_inside_course_and_research_folders(self):
        self.settings.update({"files": True})
        (self.home / "Investigacion/paper.pdf").write_text("fixture")
        knowledge = Knowledge(self.settings)
        with self.assertRaises(Unavailable): knowledge.search("atención")

        class Backend:
            def search(self, query, roots, limit):
                self.roots = roots
                return [{"path": f"{roots[1]}/paper.pdf", "page": 3, "snippet": "attention"},
                        {"path": "/etc/passwd", "page": 1}, {"path": f"{roots[0]}/x.md", "page": "2"}]
        knowledge.backend = Backend()
        found = knowledge.search("atención")["results"]
        self.assertEqual(found, [{"path": str(self.home / "Investigacion/paper.pdf"), "page": 3, "snippet": "attention"}])
        self.assertEqual(knowledge.backend.roots, [str(self.home / "Cursos/Redes"), str(self.home / "Investigacion")])


if __name__ == "__main__":
    unittest.main()
