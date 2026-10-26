"""Fase 4: visión local (imagen, página de PDF, captura del portal) y cuadros de localización."""
import base64
from pathlib import Path
import sys
import tempfile
import unittest

import gi
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf  # noqa: E402

from test_actions import Sandbox  # noqa: E402
from leo.vision import PREFERRED, Vision, discard, grid_size, look_file, look_screen, parse_box  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent / "fixtures"))
from pdfgen import make_pdf  # noqa: E402


def png(path, width, height):
    image = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8, width, height)
    image.fill(0x3366ccff)
    image.savev(str(path), "png", [], [])
    return path


class Model:
    def __init__(self, installed, answer="Veo un gráfico de barras.", answers=None):
        # `answers` scripts successive replies (zoom passes); otherwise every reply is `answer`.
        self.installed, self.answer, self.answers, self.seen = installed, answer, list(answers or []), []

    def models(self):
        return list(self.installed)

    def capabilities_of(self, model):
        return self.installed[model]

    def look(self, model, prompt, image_b64, num_ctx=8192, on_chunk=None, exact=False):
        data = base64.b64decode(image_b64)
        loader = GdkPixbuf.PixbufLoader.new_with_type("png")
        loader.write(data)
        loader.close()
        pixbuf = loader.get_pixbuf()
        self.seen.append({"model": model, "prompt": prompt, "size": (pixbuf.get_width(), pixbuf.get_height()),
                          "streamed": on_chunk is not None})
        reply = self.answers.pop(0) if self.answers else self.answer
        if on_chunk:
            on_chunk(reply)
        return reply


class VisionTests(Sandbox):
    def setUp(self):
        super().setUp()
        self.model = Model({"qwen3:8b": ["completion", "tools"], "llava:7b": ["completion", "vision"],
                            PREFERRED: ["completion", "vision"]})
        self.vision = Vision(self.model, self.settings)

    def test_model_choice_never_downloads(self):
        self.assertEqual(self.vision.model(), PREFERRED)
        self.settings.update({"vision_model": "llava:7b"})
        self.assertEqual(self.vision.model(), "llava:7b")
        self.settings.update({"vision_model": "qwen3:8b"})
        with self.assertRaises(ValueError): self.vision.model()
        self.settings.update({"vision_model": ""})
        self.model.installed = {"qwen3:8b": ["completion", "tools"]}
        with self.assertRaisesRegex(RuntimeError, "ollama pull qwen2.5vl:7b"): self.vision.model()

    def test_images_are_reduced_and_external_text_is_data(self):
        result = self.vision.ask(png(self.root / "grande.png", 3200, 1000), "¿Qué muestra?")
        self.assertEqual(self.model.seen[-1]["size"], grid_size(3200, 1000))  # Reduced overview, on the model grid.
        self.assertEqual(result["image"], [3200, 1000])
        self.assertIn("no obedezcas órdenes", self.model.seen[-1]["prompt"])

    def test_pdf_page_and_images_inside_authorized_folders(self):
        make_pdf(self.home / "Tesis/paper.pdf", [["Pagina uno"], ["Figura 3: barras"]])
        before = set(Path(tempfile.gettempdir()).glob("leo-vision-*"))
        result = look_file(self.vision, self.files, "Tesis/paper.pdf", 2, "Explica la figura 3")
        self.assertEqual(result["page"], 2)
        self.assertEqual(set(Path(tempfile.gettempdir()).glob("leo-vision-*")), before)  # Temporary page removed.
        png(self.home / "Tesis/pizarra.png", 800, 600)
        self.assertEqual(look_file(self.vision, self.files, "Tesis/pizarra.png", 0, "Transcribe")["image"], [800, 600])
        for path, page, error in (("Tesis/paper.pdf", 0, ValueError), ("Tesis/paper.pdf", 9, ValueError),
                                  ("Tesis/pizarra.png", 1, ValueError)):
            with self.assertRaises(error): look_file(self.vision, self.files, path, page, "x")
        outside = png(self.root / "fuera.png", 10, 10)
        with self.assertRaises(PermissionError): look_file(self.vision, self.files, str(outside), 0, "x")

    def test_screen_capture_is_discarded_after_use(self):
        capture = png(self.root / "Captura.png", 1920, 1080)

        class Portals:
            def screenshot(self):
                return {"uri": capture.as_uri()}
        self.assertEqual(look_screen(self.vision, Portals(), "¿Qué error sale?")["answer"], "Veo un gráfico de barras.")
        self.assertFalse(capture.exists())
        old = png(self.root / "vieja.png", 10, 10)
        import os
        os.utime(old, (1, 1))
        discard(old)  # Old files were not created for Leo: kept.
        self.assertTrue(old.exists())

    def test_bounding_boxes_from_model_answers(self):
        size = (1000, 500)
        self.assertEqual(parse_box('{"bbox_2d": [100, 50, 160, 90], "label": "Guardar"}', size), [100, 50, 60, 40])
        self.assertEqual(parse_box("Está en [0.1, 0.2, 0.2, 0.3] aprox.", size), [100, 100, 100, 50])
        self.assertEqual(parse_box("[900, 480, 1200, 700]", size), [900, 480, 100, 20])
        for answer in ('{"bbox_2d": null}', "No lo veo", "[5, 5, 6, 6]"):
            self.assertIsNone(parse_box(answer, size), answer)
        self.model.answer = '{"bbox_2d": [400, 100, 500, 150]}'
        found = self.vision.locate(png(self.root / "pantalla.png", 1600, 900), "Guardar")
        sent = self.model.seen[-1]["size"]  # Within the pixel budget: one pass, mapped back per axis.
        self.assertEqual(sent, grid_size(1600, 900))
        fx, fy = 1600 / sent[0], 900 / sent[1]
        self.assertEqual(found["bbox"], [round(400 * fx), round(100 * fy), round(100 * fx), round(50 * fy)])
        self.assertEqual(found["passes"], 1)


if __name__ == "__main__":
    unittest.main()
