"""F6.2: ruta directa de «mira…», streaming de la pasada que responde y OCR opcional."""
import subprocess
from unittest.mock import patch

from test_actions import Sandbox
from test_screen import Model, png
from test_vision_zoom import box
from leo import ocr
from leo.screen import direct
from leo.vision import PREFERRED, STREAM, Vision


class DirectTests(Sandbox):
    def test_only_pure_look_requests_skip_the_agent(self):
        for text in ("Leo, mira esta ventana y dime qué error hay", "mira las dos pantallas", "¿Qué error ves en pantalla?",
                     "Leo, lee mi pantalla y copia literalmente la traza"):
            self.assertTrue(direct(text), text)
        for text in ("Leo, mira mi pantalla y abre la calculadora", "mira la ventana y señala el botón Guardar",
                     "¿Dónde está el botón Exportar en la pantalla?", "Hola Leo", "Explica cómo funciona la pantalla",
                     "mira esto y borra el archivo"):
            self.assertFalse(direct(text), text)

    def test_only_the_answering_pass_is_streamed(self):
        model = Model({PREFERRED: ["completion", "vision"]}, answers=[box(700, 300, 800, 350), "KeyError: 'lr'"])
        chunks = []
        token = STREAM.set(chunks.append)
        try:
            result = Vision(model, self.settings).ask(png(self.root / "w.png", 3440, 1440), "¿Qué error hay?")
        finally:
            STREAM.reset(token)
        self.assertEqual([seen["streamed"] for seen in model.seen], [False, True])  # Region pass, then answer.
        self.assertEqual((chunks, result["answer"]), (["KeyError: 'lr'"], "KeyError: 'lr'"))

    def test_ocr_text_of_the_same_pixels_is_added_as_data(self):
        model = Model({PREFERRED: ["completion", "vision"]}, answer="KeyError")
        with patch.object(ocr, "read", return_value="KeyError: 'learning_rate'") as read:
            Vision(model, self.settings, ocr=True).ask(png(self.root / "t.png", 800, 600), "¿Qué error hay?")
        self.assertEqual(read.call_args.args[0].get_width(), 800)
        self.assertIn("Texto reconocido por OCR", model.seen[-1]["prompt"])
        self.assertIn("KeyError: 'learning_rate'", model.seen[-1]["prompt"])
        Vision(model, self.settings).ask(png(self.root / "t.png", 800, 600), "¿Qué error hay?")
        self.assertNotIn("OCR", model.seen[-1]["prompt"])


class OcrTests(Sandbox):
    def setUp(self):
        super().setUp()
        ocr._languages = None
        self.addCleanup(setattr, ocr, "_languages", None)

    def test_languages_prefer_spanish_and_never_fail(self):
        calls = []

        def run(args, **kwargs):
            calls.append(args)
            if "--list-langs" in args:
                return subprocess.CompletedProcess(args, 0, "List of available languages (3):\neng\nosd\nspa\n", "")
            return subprocess.CompletedProcess(args, 0, b"  KeyError: 'lr'  \n\n$\n", b"")
        image = __import__("leo.vision", fromlist=["load"]).load(png(self.root / "o.png", 200, 100))
        with patch.object(ocr, "executable", return_value="/usr/bin/tesseract"):
            self.assertEqual(ocr.read(image, run=run), "  KeyError: 'lr'\n$")
            self.assertEqual(ocr.languages(run), "spa+eng")
            self.assertEqual(calls[-1][:5], ["/usr/bin/tesseract", "stdin", "stdout", "-l", "spa+eng"])
            failing = lambda args, **kwargs: subprocess.CompletedProcess(args, 1, b"", b"error")  # noqa: E731
            self.assertEqual(ocr.read(image, run=failing), "")
        with patch.object(ocr, "executable", return_value=None):
            self.assertEqual(ocr.read(image), "")
