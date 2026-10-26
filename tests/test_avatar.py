import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import avatar_traits  # noqa: E402
from avatar_svg import sprite, walking_sprite  # noqa: E402

POSES = ("sit", "sleep", "alert", "blink", "happy", "curious-left", "curious-right", "watching", "point-left",
         "point-right", "celebrate", "reading", *(f"pet-{i}" for i in range(8)))
MISSING = ROOT / "tests/fixtures/no-traits.json"


def traits(data):
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "traits.json"
        path.write_text(json.dumps(data))
        return avatar_traits.load(path)


class AvatarTraitsTests(unittest.TestCase):
    def test_without_traits_sprites_match_committed_assets(self):
        default = avatar_traits.load(MISSING)
        self.assertFalse(default["measured"])
        for pose in POSES:
            self.assertEqual(sprite(pose, traits=default), (ROOT / f"assets/leo/{pose}.svg").read_text(), pose)
        for frame in range(24):
            self.assertEqual(walking_sprite(frame, traits=default),
                             (ROOT / f"assets/leo/walk-{frame}.svg").read_text(), frame)

    def test_invalid_values_are_ignored_or_clamped(self):
        loaded = traits({"version": 1, "palette": {"coat_mid": "red", "nose": "#ABCDEF", "extra": "#000000",
                                                   "tongue": "#12345"},
                         "proportions": {"head_width": 9, "ear_size": "big", "fold_depth": True}})
        self.assertTrue(loaded["measured"])
        self.assertEqual(loaded["palette"]["coat_mid"], avatar_traits.DEFAULT_PALETTE["coat_mid"])
        self.assertEqual(loaded["palette"]["tongue"], avatar_traits.DEFAULT_PALETTE["tongue"])
        self.assertEqual(loaded["palette"]["nose"], "#abcdef")
        self.assertNotIn("extra", loaded["palette"])
        self.assertEqual(loaded["proportions"], {"head_width": 1.12, "ear_size": 1.0, "fold_depth": .58})
        self.assertFalse(traits({"version": 2, "palette": {"nose": "#000000"}})["measured"])
        self.assertFalse(traits(["no", "dict"])["measured"])

    def test_measured_palette_and_proportions_reach_every_sprite(self):
        # coat_mid reuses another placeholder: a single substitution pass must not chain it.
        loaded = traits({"version": 1, "palette": {"coat_mid": "#985a32", "coat_deep": "#5a3520",
                                                   "tongue": "#2e2a3c"},
                         "proportions": {"head_width": 1.08, "ear_size": .8, "fold_depth": .7}})
        for svg in [sprite(pose, traits=loaded) for pose in POSES] + [walking_sprite(i, traits=loaded) for i in range(24)]:
            ET.fromstring(svg)
            self.assertIn("#985a32", svg)
            self.assertIn("#5a3520", svg)
            for placeholder in ("#d59a58", "#f0c58d", "#a48b67", "#a38a66"):
                self.assertNotIn(placeholder, svg)
        sit, walk = sprite("sit", traits=loaded), walking_sprite(0, traits=loaded)
        self.assertIn("scale(1.177 .88)", sit)
        self.assertIn("scale(0.8) translate(-100 -66)", sit)
        self.assertIn('opacity=".70"', sit)
        self.assertIn("translate(-41.75 22) scale(1.129 .9)", walk)
        self.assertIn("translate(197 91) scale(0.8)", walk)
        self.assertIn("#2e2a3c", sprite("happy", traits=loaded))

    def test_overrides_are_validated(self):
        import leo_traits
        with self.assertRaises(SystemExit):
            leo_traits.parse_overrides(["ear_size=3"])
        with self.assertRaises(SystemExit):
            leo_traits.parse_overrides(["shell=rm"])


class PhotoAnalysisTests(unittest.TestCase):
    """Synthetic fixture only: the real photos are analysed by the user on their own machine."""

    def setUp(self):
        try:
            import cv2
            import numpy as np
        except ImportError:
            self.skipTest("numpy/OpenCV no instalados")
        self.cv2, self.np = cv2, np

    def synthetic_dog(self, path):
        cv2, np = self.cv2, self.np
        image = np.full((300, 400, 3), (235, 238, 240), np.uint8)
        cv2.ellipse(image, (200, 170), (130, 95), 0, 0, 360, (78, 138, 197), -1)  # BGR copper coat
        cv2.ellipse(image, (200, 150), (110, 70), 0, 0, 360, (138, 193, 237), -1)  # lit back
        cv2.ellipse(image, (200, 220), (40, 25), 0, 0, 360, (56, 66, 65), -1)  # dark muzzle
        for y in range(120, 200, 14):
            cv2.line(image, (120, y), (280, y + 6), (47, 81, 132), 2)  # folds
        cv2.imwrite(str(path), image)

    def test_end_to_end_writes_numbers_and_keeps_photos_intact(self):
        import leo_traits
        with tempfile.TemporaryDirectory() as directory:
            photos, output = Path(directory) / "avatar", Path(directory) / "traits.json"
            photos.mkdir()
            self.synthetic_dog(photos / "leo.png")
            before = hashlib.sha256((photos / "leo.png").read_bytes()).hexdigest()
            leo_traits.main(["--photos", str(photos), "--output", str(output), "--set", "tongue=#2e2a3c"])
            self.assertEqual(hashlib.sha256((photos / "leo.png").read_bytes()).hexdigest(), before)
            data = json.loads(output.read_text())
            loaded = avatar_traits.load(output)
        self.assertTrue(loaded["measured"])
        self.assertEqual(data["measurements"]["photos_used"], 1)
        self.assertEqual(data["palette"]["tongue"], "#2e2a3c")
        for role in ("coat_light", "coat_mid", "coat_deep", "muzzle_dark"):
            self.assertIn(role, data["palette"])
        coat = data["palette"]["coat_mid"]
        red, blue = int(coat[1:3], 16), int(coat[5:7], 16)
        self.assertGreater(red, blue + 40, coat)  # warm copper, not the grey background
        light = sum(int(data["palette"]["coat_light"][i:i + 2], 16) for i in (1, 3, 5))
        deep = sum(int(data["palette"]["coat_deep"][i:i + 2], 16) for i in (1, 3, 5))
        self.assertGreater(light, deep)
        self.assertTrue(.4 <= data["proportions"]["fold_depth"] <= .8)


if __name__ == "__main__":
    unittest.main()


class ThirdEyeTests(unittest.TestCase):
    """The third eye (2026-10-23) sits on Leo's forehead in every pose and walking frame. Checked with numbers only
    (G8): the anchor area is opaque head and never covers his dark eyes, nose or muzzle."""

    def test_anchors_match_the_sprites_and_lie_on_the_forehead(self):
        import math
        import third_eye
        import gi
        gi.require_version("GdkPixbuf", "2.0")
        from gi.repository import GdkPixbuf
        anchors = json.loads((ROOT / "assets/leo/third-eye.json").read_text())
        self.assertEqual(anchors, json.loads(json.dumps(third_eye.anchors())))  # Regenerate with scripts/third_eye.py.
        self.assertEqual(len(anchors), len(POSES) + 24)
        for pose, anchor in anchors.items():
            image = GdkPixbuf.Pixbuf.new_from_file_at_size(str(ROOT / f"assets/leo/{pose}.svg"), 320, 320)
            pixels, stride, channels = image.get_pixels(), image.get_rowstride(), image.get_n_channels()
            half_w, half_h = (4.5, 13) if anchor["profile"] else (6.5, 16)
            angle = math.radians(anchor["angle"])
            for v in range(-int(half_h), int(half_h) + 1, 2):
                for u in range(-int(half_w), int(half_w) + 1, 2):
                    x = anchor["x"] + u * math.cos(angle) - v * math.sin(angle)
                    y = anchor["y"] + u * math.sin(angle) + v * math.cos(angle)
                    i = int(y) * stride + int(x) * channels
                    self.assertGreater(pixels[i + 3], 230, pose)  # On the head.
                    self.assertGreaterEqual(sum(pixels[i:i + 3]) / 3, 70, pose)  # Not on an eye or the nose.
