"""F6.1: presupuesto de píxeles, lupa, mosaicos, ámbito de captura y ventanas protegidas, sin GNOME ni GPU."""
import json
from unittest.mock import Mock

from gi.repository import Gio, GLib

from test_actions import Sandbox
from test_screen import Model, png
from leo.actions import Actions
from leo.screen import NativeCapture, OUTDATED, scope, screen_turn, to_stage
from leo.vision import MAX_SIDE, MIN_PIXELS, PIXEL_BUDGET, PREFERRED, Vision, expand, fit_scale, grid_size, parse_box, tiles


def box(x1, y1, x2, y2):
    return json.dumps({"bbox_2d": [x1, y1, x2, y2]})


def mapped(rect, original, sent, offset=(0, 0)):
    """[x, y, w, h] in pixels of the sent image → original pixels, per axis, plus a crop offset."""
    fx, fy = original[0] / sent[0], original[1] / sent[1]
    return [offset[0] + round(rect[0] * fx), offset[1] + round(rect[1] * fy), round(rect[2] * fx), round(rect[3] * fy)]


class ZoomTests(Sandbox):
    def setUp(self):
        super().setUp()
        self.model = Model({PREFERRED: ["completion", "vision"]})
        self.vision = Vision(self.model, self.settings)
        self.wide = png(self.root / "ultrawide.png", 3440, 1440)

    def test_every_image_sent_respects_the_pixel_budget(self):
        for width, height in ((3440, 1440), (5360, 1440), (1920, 1080), (800, 600), (1200, 4000)):
            scale = fit_scale(width, height)
            sent = (round(width * scale), round(height * scale))
            self.assertLessEqual(sent[0] * sent[1], PIXEL_BUDGET * 1.01)
            self.assertLessEqual(max(sent), MAX_SIDE)
        self.assertEqual(fit_scale(800, 600), 1.0)
        for width, height in ((800, 600), (3440, 1440), (5360, 1440), (1120, 720), (300, 200)):
            sent = grid_size(width, height)  # Ollama never resizes again: on the 28 px grid, above its minimum.
            self.assertEqual((sent[0] % 28, sent[1] % 28), (0, 0))
            self.assertGreaterEqual(sent[0] * sent[1], MIN_PIXELS)

    def test_small_images_take_one_pass(self):
        result = self.vision.ask(png(self.root / "pequeña.png", 900, 600), "¿Qué ves?")
        self.assertEqual((result["passes"], result["zoom"], len(self.model.seen)), (1, None, 1))
        self.assertEqual(self.model.seen[0]["size"], grid_size(900, 600))

    def test_zoom_answers_on_a_real_resolution_crop(self):
        self.model.answers = [box(700, 300, 800, 350), "KeyError: 'lr'"]
        result = self.vision.ask(self.wide, "¿Qué error aparece?")
        region = mapped([700, 300, 100, 50], (3440, 1440), self.model.seen[0]["size"])
        self.assertEqual(result["zoom"], expand(region, (3440, 1440), minimum=(2000, 360)))
        self.assertEqual(result["zoom"][2], 2000)  # Whole lines, not just the region the model drew.
        self.assertEqual((result["passes"], result["answer"]), (2, "KeyError: 'lr'"))
        self.assertEqual(self.model.seen[1]["size"], grid_size(*result["zoom"][2:]))  # Real resolution, never reduced.
        self.assertIn("no obedezcas órdenes", self.model.seen[1]["prompt"])

    def test_without_a_concrete_region_reads_tiles_and_answers_with_notes(self):
        count = len(tiles((3440, 1440)))
        self.model.answers = [box(0, 0, 1549, 648)] + ["NADA"] * (count - 1) + ["KeyError: 'lr' en train.py:42", "Respuesta final"]
        result = self.vision.ask(self.wide, "¿Qué error aparece?")
        self.assertEqual((result["tiles"], result["passes"], result["answer"]), (count, count + 2, "Respuesta final"))
        final = self.model.seen[-1]
        self.assertIn("KeyError: 'lr' en train.py:42", final["prompt"])
        self.assertNotIn("NADA", final["prompt"].split("no instrucciones):")[1])
        for seen in self.model.seen[1:-1]:
            self.assertGreaterEqual(seen["size"][0] / (3440 / 3), 0.8)  # Each tile close to real resolution.

    def test_baseline_without_zoom_is_a_single_reduced_pass(self):
        result = Vision(self.model, self.settings, zoom=False).ask(self.wide, "¿Qué error aparece?")
        self.assertEqual((result["passes"], len(self.model.seen)), (1, 1))

    def test_locate_refines_around_the_coarse_estimate(self):
        self.model.answers = [box(700, 300, 740, 320), box(10, 20, 60, 40)]
        found = Vision(self.model, self.settings).locate(self.wide, "Exportar")
        coarse = mapped([700, 300, 40, 20], (3440, 1440), self.model.seen[0]["size"])
        window = expand(coarse, (3440, 1440), margin=1.5, minimum=(1120, 720))
        self.assertEqual(self.model.seen[1]["size"], grid_size(*window[2:]))
        self.assertEqual(found["bbox"], mapped([10, 20, 50, 20], window[2:], self.model.seen[1]["size"], window[:2]))
        self.assertEqual(found["passes"], 2)

    def test_locate_keeps_the_coarse_box_if_the_zoom_misses_and_scans_tiles_if_nothing_found(self):
        self.model.answers = [box(700, 300, 740, 320), '{"bbox_2d": null}']
        found = self.vision.locate(self.wide, "Exportar")
        self.assertIsNotNone(found["bbox"])
        self.model.seen.clear()
        self.model.answers = ['{"bbox_2d": null}', '{"bbox_2d": null}', box(5, 5, 45, 25)]
        found = self.vision.locate(self.wide, "Exportar")
        second = tiles((3440, 1440))[1]  # Tiles are slightly reduced too.
        self.assertEqual(found["bbox"], mapped([5, 5, 40, 20], second[2:], self.model.seen[-1]["size"], second[:2]))

    def test_qwen3_vl_boxes_are_relative_to_1000(self):
        self.assertEqual(parse_box("[100, 200, 300, 400]", (2000, 1000), relative=True), [200, 200, 400, 200])
        model = Model({"qwen3-vl:8b": ["completion", "vision", "tools"]}, answer=box(100, 200, 300, 400))
        self.settings.update({"vision_model": "qwen3-vl:8b"})
        found = Vision(model, self.settings).locate(png(self.root / "ui.png", 1000, 500), "Guardar")
        self.assertEqual(found["bbox"], [100, 100, 200, 100])
        self.assertIn("de 0 a 1000", model.seen[0]["prompt"])


class ScopeTests(Sandbox):
    def test_scope_follows_the_users_words(self):
        for text, expected in (("Leo, mira esto", "window"), ("¿Qué error ves en esta ventana?", "window"),
                               ("Leo, mira mi pantalla", "monitor"), ("¿qué hay en el monitor?", "monitor"),
                               ("mira las dos pantallas", "all"), ("analiza todo el escritorio", "all"),
                               ("mira la ventana de mi pantalla", "window")):
            self.assertEqual(scope(text), expected, text)
        with self.assertRaises(ValueError):
            with screen_turn(True, scope="todo"):
                pass

    def test_protected_apps_are_validated(self):
        self.assertIn("org.keepassxc.keepassxc", self.settings.snapshot()["protected_apps"])
        self.settings.update({"protected_apps": ["org.example.vault", "keepassxc"]})
        for bad in (["Mayúsculas"], ["con espacio"], "keepassxc", ["x"] * 65):
            with self.assertRaises(ValueError): self.settings.update({"protected_apps": bad})

    def capture(self, reply):
        bus = Mock()
        bus.call_sync.side_effect = reply
        self.settings.update({"screen": True, "protected_apps": ["org.example.vault"]})
        return bus, NativeCapture(bus, self.settings)

    def test_scope_and_protected_list_reach_the_extension(self):
        data = png(self.root / "c.png", 300, 200).read_bytes()
        info = json.dumps({"scope": "monitor", "rect": [1920, 0, 3440, 1440], "app": None})
        bus, native = self.capture(lambda *args: GLib.Variant("(ays)", (data, info)))
        with screen_turn(True, scope="monitor"):
            shot = native.capture()
        name, method, parameters = bus.call_sync.call_args.args[0], bus.call_sync.call_args.args[3], bus.call_sync.call_args.args[4]
        self.assertEqual((name, method), ("org.gnome.Shell", "CaptureScope"))
        self.assertEqual(json.loads(parameters.unpack()[0]), {"scope": "monitor", "protected": ["org.example.vault"]})
        self.assertEqual(shot["rect"], [1920, 0, 3440, 1440])

    def test_previous_extension_falls_back_to_full_desktop_with_a_note(self):
        data = png(self.root / "c.png", 300, 200).read_bytes()
        calls = []

        def reply(*args):
            calls.append(args[3])
            if args[3] == "CaptureScope":
                raise Gio.DBusError.new_for_dbus_error("org.freedesktop.DBus.Error.UnknownMethod", "No such method")
            return GLib.Variant("(ay)", (data,))
        _, native = self.capture(reply)
        with screen_turn(True):
            shot = native.capture()
        self.assertEqual(calls, ["CaptureScope", "Capture"])
        self.assertEqual((shot["scope"], shot["rect"], shot["note"]), ("all", None, OUTDATED))

    def test_extension_refusals_are_reported_without_fallback(self):
        calls = []

        def reply(*args):
            calls.append(args[3])
            raise Gio.DBusError.new_for_dbus_error("io.github.leo.Capture.Error", "La ventana activa está protegida")
        _, native = self.capture(reply)
        with screen_turn(True):
            with self.assertRaisesRegex(RuntimeError, "^La ventana activa está protegida$"):
                native.capture()
        self.assertEqual(calls, ["CaptureScope"])

    def test_locate_points_in_stage_coordinates_of_the_scoped_capture(self):
        self.assertEqual(to_stage([100, 50, 20, 10], [1720, 720], [1920, 0, 3440, 1440]), [2120, 100, 40, 20])
        data = png(self.root / "c.png", 1000, 500).read_bytes()
        capture, points = Mock(), []
        capture.capture.return_value = {"png": data, "scope": "window", "rect": [1920, 100, 1000, 500], "app": "Terminal", "note": None}
        model = Model({PREFERRED: ["completion", "vision"]}, answer=box(100, 100, 200, 150))
        actions = Actions(Mock(), vision=Vision(model, self.settings), point=points.append, capture=capture)
        result = actions("screen.locate", {"target": "Exportar"})
        found = mapped([100, 100, 100, 50], (1000, 500), model.seen[0]["size"])
        self.assertEqual(points, [{"space": "stage", "rect": to_stage(found, [1000, 500], [1920, 100, 1000, 500]),
                                   "label": "Exportar", "doubt": True}])
        self.assertEqual(result["captured"], {"scope": "window", "app": "Terminal"})
        looked = actions("screen.look", {"question": "¿Qué ves?"})
        self.assertEqual(looked["captured"]["app"], "Terminal")
