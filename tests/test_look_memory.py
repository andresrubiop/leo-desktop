"""F6.3: captura recordada solo en memoria (G11), región elegida y atajo (G12)."""
import json
from unittest.mock import Mock

from gi.repository import GLib

from test_actions import Sandbox
from test_screen import png
from leo.screen import Attachments, NativeCapture, region, screen_turn


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class MemoryTests(Sandbox):
    def shot(self):
        return {"png": png(self.root / "c.png", 400, 300).read_bytes(), "scope": "window", "rect": [0, 0, 400, 300],
                "app": "Terminal", "note": None}

    def test_capture_expires_after_two_minutes_and_is_per_client(self):
        clock = Clock()
        memory = Attachments(clock=clock)
        memory.put(":1.5", self.shot())
        self.assertIsNotNone(memory.get(":1.5"))
        self.assertIsNone(memory.get(":1.6"))
        described = memory.describe(":1.5")
        self.assertEqual((described["scope"], described["app"], described["expires_in"]), ("window", "Terminal", 120))
        self.assertTrue(described["thumbnail"])
        clock.now += 119
        self.assertEqual(memory.remaining(":1.5"), 1)
        clock.now += 1
        self.assertIsNone(memory.get(":1.5"))  # Expired: dropped, not just hidden.
        self.assertEqual(memory.items, {})
        memory.put(":1.5", self.shot())
        self.assertTrue(memory.forget(":1.5"))
        self.assertFalse(memory.forget(":1.5"))
        self.assertIsNone(memory.describe(":1.5"))

    def test_regions_are_validated_and_reach_the_extension(self):
        self.assertEqual(region([10, 20, 300, 200]), [10, 20, 300, 200])
        for bad in ([10, 20, 4, 200], [10, 20, 300], [10.5, 20, 300, 200], "x", [-1, 0, 100, 100]):
            with self.assertRaises(ValueError):
                region(bad)
        with self.assertRaises(ValueError):
            with screen_turn(True, scope="region"):  # A region needs its rectangle.
                pass
        with self.assertRaises(ValueError):
            with screen_turn(True, scope="window", rect=[0, 0, 100, 100]):
                pass
        bus = Mock()
        data = png(self.root / "c.png", 300, 200).read_bytes()
        bus.call_sync.return_value = GLib.Variant("(ays)", (data, json.dumps({"scope": "region", "rect": [10, 20, 300, 200], "app": None})))
        self.settings.update({"screen": True})
        with screen_turn(True, scope="region", rect=[10, 20, 300, 200]):
            NativeCapture(bus, self.settings).capture()
        options = json.loads(bus.call_sync.call_args.args[4].unpack()[0])
        self.assertEqual((options["scope"], options["rect"]), ("region", [10, 20, 300, 200]))

    def test_shortcut_setting(self):
        self.assertEqual(self.settings.snapshot()["look_shortcut"], "<Super><Shift>l")
        for good in ("<Super><Shift>l", "<Control><Alt>v", ""):
            self.settings.update({"look_shortcut": good})
        for bad in ("l", "<Super>", "<Super><Shift>l; rm", "<Hyper>l"):
            with self.assertRaises(ValueError):
                self.settings.update({"look_shortcut": bad})
