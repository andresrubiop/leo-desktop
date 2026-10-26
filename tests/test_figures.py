"""F6.2: figuras y tablas de un PDF por su leyenda, sin indicar la página. PDF ficticio de dos columnas."""
from pathlib import Path
import sys

from test_actions import Sandbox
from test_screen import Model
from leo.vision import PDF_DPI, PREFERRED, Vision, figure_label, find_figure, grid_size, layout, look_file

sys.path.insert(0, str(Path(__file__).resolve().parent / "fixtures"))
from pdfgen import two_column_paper  # noqa: E402


def contains(region, box):
    x, y, w, h = region
    return x <= box[0] and y <= box[1] and box[0] + box[2] <= x + w and box[1] + box[3] <= y + h


class FigureTests(Sandbox):
    def setUp(self):
        super().setUp()
        self.paper = self.home / "Tesis/paper.pdf"
        self.truth = two_column_paper(self.paper)
        self.model = Model({PREFERRED: ["completion", "vision"]}, answer="Curvas de pérdida que bajan.")
        self.vision = Vision(self.model, self.settings)

    def test_labels_in_spanish_and_english(self):
        for text, expected in (("Explica la figura 3", ("figura", 3)), ("¿qué muestra la Fig. 12?", ("figura", 12)),
                               ("Resume la Tabla 2 del paper", ("tabla", 2)), ("explain Figure 4", ("figura", 4)),
                               ("Explica la página 2", None), ("configura la impresora", None)):
            self.assertEqual(figure_label(text), expected, text)

    def test_regions_contain_the_graphic_and_stay_in_their_column(self):
        pages = layout(self.paper)
        for name, page in (("figura 3", 1), ("tabla 1", 1), ("figura 4", 2)):
            found = find_figure(pages, *figure_label(name))
            self.assertEqual(found["page"], page, name)
            self.assertTrue(contains(found["region"], self.truth[name]), (name, found["region"], self.truth[name]))
        left = find_figure(pages, "figura", 3)["region"]
        self.assertLess(left[0] + left[2], 320)  # The right column (Tabla 1) is not part of Figura 3.
        self.assertIsNone(find_figure(pages, "figura", 9))
        self.assertIsNone(find_figure(pages, "tabla", 3))  # «Figura 3» in body text is not a caption.

    def test_look_finds_the_figure_without_a_page_and_sends_only_its_region(self):
        result = look_file(self.vision, self.files, "Tesis/paper.pdf", 0, "Explica la figura 3")
        self.assertEqual((result["page"], result["figure"]), (1, "figura 3"))
        region = find_figure(layout(self.paper), "figura", 3)["region"]
        scale = PDF_DPI / 72
        self.assertEqual(self.model.seen[-1]["size"], grid_size(round(region[2] * scale), round(region[3] * scale)))
        result = look_file(self.vision, self.files, "Tesis/paper.pdf", 0, "¿Qué compara la tabla 1?")
        self.assertEqual((result["page"], result["figure"]), (1, "tabla 1"))

    def test_without_page_or_caption_it_asks_for_one_and_a_given_page_still_works(self):
        with self.assertRaisesRegex(ValueError, "número de la figura"):
            look_file(self.vision, self.files, "Tesis/paper.pdf", 0, "Explica este paper")
        with self.assertRaisesRegex(ValueError, "número de la figura"):
            look_file(self.vision, self.files, "Tesis/paper.pdf", 0, "Explica la figura 9")
        result = look_file(self.vision, self.files, "Tesis/paper.pdf", 2, "Explica la figura 9")
        self.assertEqual(result["page"], 2)
        self.assertIn("No encontré la leyenda", result["note"])
