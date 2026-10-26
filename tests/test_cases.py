"""F6.8: ecuación → LaTeX comprobada, revisión de diapositivas, tablas de PDF y curvas (partes deterministas)."""
import os
from pathlib import Path
import shutil
import sys
import time
import unittest

from test_actions import Sandbox
from leo import cases

sys.path.insert(0, str(Path(__file__).parent / "fixtures"))
import cases as fixtures  # noqa: E402


class LatexTests(unittest.TestCase):
    def test_answers_become_one_environment(self):
        self.assertEqual(cases.latex_body("```latex\n\\begin{equation}\nE = mc^2\n\\end{equation}\n```"),
                         "\\begin{equation}\nE = mc^2\n\\end{equation}")
        self.assertEqual(cases.latex_body("$$ E = mc^2 $$"), "\\begin{equation}\nE = mc^2\n\\end{equation}")
        self.assertIn("\\begin{align*}", cases.latex_body("Aquí está:\n\\begin{align*}a&=b\\\\c&=d\\end{align*}"))

    @unittest.skipUnless(shutil.which("pdflatex"), "pdflatex")
    def test_compiling_tells_good_from_broken(self):
        self.assertEqual(cases.compiles("\\begin{equation}\\frac{a}{b}\\end{equation}"), (True, ""))
        ok, error = cases.compiles("\\begin{equation}\\frac{a}{b\\end{equation}")
        self.assertFalse(ok)
        self.assertTrue(error.startswith("!"))
        ok, _ = cases.compiles("\\begin{equation}\\write18{touch /tmp/leo-no}\\end{equation}")
        self.assertFalse(os.path.exists("/tmp/leo-no"))  # No shell escape.


class SlideTests(Sandbox):
    def test_small_text_low_contrast_and_missing_alt_in_every_format(self):
        made = fixtures.presentation(self.home / "Tesis")
        for kind in ("pptx", "odp", "fodp"):
            review = cases.review_slides(self.files, str(made[kind]))
            issues = {s["slide"]: " ".join(s["issues"]) for s in review["slides"]}
            self.assertEqual(issues[1], "", kind)
            self.assertIn("texto pequeño (9 pt", issues[2], kind)
            self.assertIn("poco contraste", issues[3], kind)
            self.assertEqual([a["slide"] for a in review["missing_alt"]], [3], kind)
            self.assertIn("Diapositiva 2: texto pequeño", review["report"])

    def test_contrast_ratio(self):
        self.assertAlmostEqual(cases.ratio("#000000", "#ffffff"), 21, places=1)
        self.assertLess(cases.ratio("#dedede", "#ffffff"), 1.5)


class TableTests(Sandbox):
    def test_text_tables_become_markdown(self):
        text = "Resultados\n\nModelo      Exactitud   Parámetros\nResNet-18   0.912       11.7M\nViT-B/16    0.947       86.0M\n\nFin"
        rows = cases.table_from_text(text)
        self.assertEqual(rows, [["Modelo", "Exactitud", "Parámetros"], ["ResNet-18", "0.912", "11.7M"], ["ViT-B/16", "0.947", "86.0M"]])
        self.assertTrue(cases.markdown(rows).startswith("| Modelo | Exactitud | Parámetros |\n|---|---|---|"))

    @unittest.skipUnless(shutil.which("pdflatex") and shutil.which("pdftotext"), "pdflatex y pdftotext")
    def test_a_pdf_table_is_read_exactly(self):
        pdf = fixtures.table_pdf(self.home / "Tesis")
        result = cases.pdf_table(self.files, str(pdf), 1)
        self.assertEqual(result["source"], "texto del PDF")
        self.assertIn("| ViT-B/16 | 0.947 | 86.0M | 60 |", result["markdown"])
        self.assertTrue(result["markdown"].startswith("| Modelo | Exactitud | Parámetros | Época |"))  # Composed accents.
        self.assertIn("Verifica", result["warning"])


class CurveTests(Sandbox):
    def test_the_newest_curve_of_the_run_is_chosen(self):
        run = self.home / "Tesis/runs/r1"
        run.mkdir(parents=True)
        made = fixtures.curves(run)
        (run / "foto.png").write_bytes(made["sana"].read_bytes())
        now = time.time()
        os.utime(made["sobreajuste"], (now, now))
        os.utime(made["sana"], (now - 100, now - 100))
        self.assertEqual(cases.newest_curve(self.home / "Tesis/runs"), made["sobreajuste"])

    def test_trends_become_a_verdict(self):
        self.assertEqual(cases.trend_verdict('[{"curva": "train", "final": "baja"}, {"curva": "val", "final": "sube"}]')[0],
                         "sobreajuste")
        self.assertEqual(cases.trend_verdict('```json [{"curva": "train", "final": "sube"}, {"curva": "val", "final": "sube"}]')[0],
                         "divergencia")
        self.assertEqual(cases.trend_verdict('[{"curva": "train loss", "final": "baja"}, {"curva": "valid", "final": "baja"}]')[0],
                         "sin problemas")
        self.assertIsNone(cases.trend_verdict("no sé")[0])

