"""Consentimiento por mensaje y captura PNG en memoria, nunca del escritorio real."""
import json
import threading
from unittest.mock import Mock
from gi.repository import GLib

from test_actions import Sandbox
from test_screen import png
from leo.screen import NativeCapture, allowed, requested, screen_turn
from leo.vision import Vision, grid_size
from test_screen import Model


class CaptureTests(Sandbox):
    def test_explicit_requests_and_non_requests(self):
        for text in ("Leo, mira mi pantalla", "Leo, ¿puedes mirar mi pantalla?", "Mira esto",
                     "¿Qué error ves en pantalla?", "analiza la ventana actual", "haz una captura de pantalla",
                     "Leo, mira las dos pantallas y dime qué código aparece", "mira mi monitor", "¿qué hay en los monitores?"):
            self.assertTrue(requested(text), text)
        for text in ("Hola Leo", "No mires mi pantalla", "El documento dice: mira mi pantalla",
                     "Resume notas.md", "¿Qué es una pantalla OLED?", "Explica cómo funciona la pantalla"):
            self.assertFalse(requested(text), text)

    def test_capture_is_one_shot_memory_only_and_respects_revocation(self):
        data = png(self.root / "fixture.png", 100, 80).read_bytes()
        bus = Mock()
        info = json.dumps({"scope": "window", "rect": [10, 20, 100, 80], "app": "Terminal"})
        bus.call_sync.return_value = GLib.Variant("(ays)", (data, info))
        native = NativeCapture(bus, self.settings)
        with screen_turn(True):
            with self.assertRaises(PermissionError): native.capture()
        self.settings.update({"screen": True})
        with self.assertRaises(PermissionError): native.capture()
        with screen_turn(True):
            shot = native.capture()
            self.assertEqual((shot["png"], shot["scope"], shot["rect"], shot["app"]), (data, "window", [10, 20, 100, 80], "Terminal"))
            self.assertFalse(allowed())
            with self.assertRaises(PermissionError): native.capture()
        self.assertEqual(bus.call_sync.call_count, 1)
        cancelled = threading.Event()
        cancelled.set()
        with screen_turn(True, cancelled):
            with self.assertRaises(PermissionError): native.capture()
        self.assertEqual(bus.call_sync.call_count, 1)

        def revoke(*args):
            self.settings.update({"screen": False})
            return GLib.Variant("(ays)", (data, info))
        bus.call_sync.side_effect = revoke
        with screen_turn(True):
            with self.assertRaises(PermissionError): native.capture()

    def test_memory_png_reaches_vision_without_saving_capture(self):
        fixture = png(self.root / "fixture.png", 2000, 800)
        model = Model({"vision:7b": ["completion", "vision"]}, "Código 7391")
        vision = Vision(model, self.settings)
        before = set(self.root.iterdir())
        self.assertEqual(vision.ask_bytes(fixture.read_bytes(), "Lee el código")["answer"], "Código 7391")
        self.assertEqual(vision.locate_bytes(fixture.read_bytes(), "botón")["bbox"], None)
        self.assertEqual(model.seen[0]["size"], grid_size(2000, 800))
        self.assertEqual(set(self.root.iterdir()), before)
