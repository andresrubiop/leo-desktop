"""Fase 5 de punta a punta con ComfyUI y Ollama simulados e imágenes sintéticas (nunca fotos reales):
spike → candidatos → puntuación local frente al SVG actual → página de comparación → vistas → sprites."""
import hashlib
import importlib
import io
import json
import os
from contextlib import redirect_stdout
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import gi
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from fake_comfy import FakeComfy, synthetic_sheet  # noqa: E402
from fake_ollama import FakeOllama  # noqa: E402


def quiet(function, *args):
    with redirect_stdout(io.StringIO()) as output:
        function(*args)
    return output.getvalue()


class SheetPipelineTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.generated = self.root / "generated"
        self.photos = self.root / "fotos-sinteticas"
        self.photos.mkdir()
        for name in ("a.png", "b.png"):
            (self.photos / name).write_bytes(synthetic_sheet(400, 300))
        self.sheet, self.score, self.compare, self.sprites, self.spike = (
            importlib.import_module(n) for n in ("leo_sheet", "leo_score", "leo_compare", "leo_sprites", "comfy_spike"))

    def test_spike_reports_reference_support_without_generating(self):
        for edit, reference in ((False, False), (True, True)):
            with FakeComfy(edit=edit) as comfy:
                quiet(self.spike.main, ["--port", str(comfy.port), "--output", str(self.root / "spike.json")])
                report = json.loads((self.root / "spike.json").read_text())
                self.assertTrue(report["text_to_image"])
                self.assertEqual(report["reference"], reference)
                self.assertEqual(report["vram_gib"][0]["total"], 8.0)
                self.assertEqual(comfy.workflows, [])

    def test_text_and_reference_generation_free_vram_and_stay_local(self):
        with FakeComfy() as comfy, patch.object(self.sheet, "free_ollama", return_value=["qwen3:8b"]) as freed:
            quiet(self.sheet.main, ["--port", str(comfy.port), "--count", "2", "--steps", "8", "--output", str(self.generated)])
            freed.assert_called_once()
            self.assertEqual(len(list((self.generated / "candidates").glob("cand-*.png"))), 2)
            manifest = json.loads((self.generated / "manifest.json").read_text())
            self.assertEqual((manifest["mode"], manifest["model"], manifest["ollama_unloaded"]),
                             ("text", "qwen-image-2.1-Q4_K_M.gguf", ["qwen3:8b"]))
            workflow = comfy.workflows[0]
            self.assertIn("Shar-Pei", workflow["5"]["inputs"]["text"])
            self.assertIn("photorealistic", workflow["6"]["inputs"]["text"])
            self.assertEqual((comfy.uploads, comfy.freed), ([], 1))
            with self.assertRaises(SystemExit):
                quiet(self.sheet.main, ["--port", str(comfy.port), "--reference", "--photos", str(self.photos)])
        with FakeComfy(edit=True) as comfy, patch.object(self.sheet, "free_ollama", return_value=[]):
            quiet(self.sheet.main, ["--port", str(comfy.port), "--count", "1", "--reference", "--photos", str(self.photos),
                                    "--output", str(self.root / "ref")])
            workflow = comfy.workflows[0]
            self.assertEqual(workflow["5"]["class_type"], "TextEncodeQwenImageEdit")
            self.assertEqual(workflow["5"]["inputs"]["image"], ["10", 0])
            self.assertEqual(workflow["1"]["inputs"]["unet_name"], "qwen-image-edit-Q4_K_M.gguf")
            self.assertEqual(len(comfy.uploads), 1)

    def test_score_compare_choose_views_and_sprites(self):
        candidates = self.generated / "candidates"
        candidates.mkdir(parents=True)
        for name in ("cand-1-0.png", "cand-2-0.png"):
            (candidates / name).write_bytes(synthetic_sheet())
        calls = []

        def script(payload):
            images = payload["messages"][-1].get("images", [])
            calls.append(len(images))
            low = len(calls) <= 2  # The SVG baseline is scored first, against both photos.
            n = 3 if low else 7
            return {"content": json.dumps({"color": n, "orejas": n, "hocico": n, "pliegues": n, "mismo_perro": n})}
        before = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in self.photos.iterdir()}
        with FakeOllama(models=("qwen2.5vl:7b",), capabilities={"qwen2.5vl:7b": ["completion", "vision"]},
                        script=script, delay=0) as ollama, patch.dict(os.environ, {"LEO_OLLAMA_PORT": str(ollama.port)}):
            output = quiet(self.score.main, ["--photos", str(self.photos), "--candidates", str(candidates),
                                             "--output", str(self.generated / "scores.json")])
        self.assertEqual(set(calls), {2})  # Photo and illustration together, every time.
        self.assertNotIn("base64", output)
        report = json.loads((self.generated / "scores.json").read_text())
        self.assertEqual(report["baseline"]["total"], 3.0)
        self.assertEqual([c["total"] for c in report["candidates"]], [7.0, 7.0])
        self.assertEqual(len(report["better_than_baseline"]), 2)
        self.assertEqual(before, {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in self.photos.iterdir()})
        self.assertIsNone(self.score.parse_scores('{"color": 5}'))

        quiet(self.compare.main, ["--generated", str(self.generated), "--photos", str(self.photos)])
        page = (self.generated / "compare.html").read_text()
        self.assertIn("candidates/cand-1-0.png", page)
        self.assertIn("class='best'", page)
        self.assertIn("../fotos-sinteticas/a.png", page)
        with self.assertRaises(SystemExit):
            quiet(self.compare.main, ["--generated", str(self.generated), "--choose", "otra.png"])
        quiet(self.compare.main, ["--generated", str(self.generated), "--choose", "cand-2-0.png"])
        self.assertEqual(json.loads((self.generated / "chosen.json").read_text()), {"file": "cand-2-0.png"})

        views = json.loads(quiet(self.sprites.main, ["--generated", str(self.generated), "--views"]).splitlines()[0])
        self.assertEqual(len(views["views"]), 2)
        quiet(self.sprites.main, ["--generated", str(self.generated), "--pose", "sit=0", "--pose", "curious-left=1:mirror"])
        for name, size in (("sit.png", 168), ("sit@2x.png", 336), ("curious-left.png", 168)):
            sprite = GdkPixbuf.Pixbuf.new_from_file(str(self.generated / "sprites" / name))
            self.assertEqual((sprite.get_width(), sprite.get_height(), sprite.get_has_alpha()), (size, size, True))
        sprite = GdkPixbuf.Pixbuf.new_from_file(str(self.generated / "sprites/sit.png"))
        data, stride = sprite.get_pixels(), sprite.get_rowstride()
        opaque = [y for y in range(168) if any(data[y * stride + x * 4 + 3] for x in range(168))]
        self.assertEqual(max(opaque), 157)  # Feet on the same line as the SVG avatar.
        self.assertEqual(data[3], 0)  # The green background is transparent.
        with self.assertRaises(SystemExit):
            quiet(self.sprites.main, ["--generated", str(self.generated), "--pose", "walk-3=0"])


if __name__ == "__main__":
    unittest.main()
