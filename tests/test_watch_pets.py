"""Leo atento (postura → ámbito, solo lo que cambió, avisos sin repetir) y mascotitas de agentes."""
import json
import os
from pathlib import Path
import time
from unittest.mock import Mock

from test_actions import Sandbox
from leo.cooking import Cooking, Processes, claude_key, pets, session_files
from leo.screen import Attachments
from leo.watch import MAX_OCR_ZONES, PER_HOUR, Watcher, finding, zones

import gi
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf  # noqa: E402


def touch(path, age=0):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{}")
    stamp = time.time() - age
    os.utime(path, (stamp, stamp))


def screen(width=1600, height=600, color=0x202020ff, patch=None):
    image = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8, width, height)
    image.fill(color)
    if patch:  # A bright rectangle where «something changed».
        x, y, w, h = patch
        image.new_subpixbuf(x, y, w, h).fill(0xf0f0f0ff)
    return image.save_to_bufferv("png", [], [])[1]


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class CookingTests(Sandbox):
    def test_recent_session_files_count_per_project_and_tool(self):
        home = self.root / "agents"
        touch(home / ".claude/projects/-home-ana-Tesis/s1.jsonl")
        touch(home / ".claude/projects/-home-ana-Tesis/s2.jsonl")
        touch(home / ".claude/projects/-home-ana-Web/s3.jsonl", age=400)  # Idle: not cooking.
        touch(home / f".codex2/sessions/{time.strftime('%Y/%m/%d')}/rollout-x.jsonl")
        touch(home / ".gemini/antigravity/conversations/abc.pb")
        found = session_files(str(home), time.time())
        result = pets(found, [])
        self.assertEqual([p["tool"] for p in result], ["antigravity", "claude", "claude", "codex"])
        self.assertEqual({p["project"] for p in result if p["tool"] == "claude"}, {"Tesis"})
        self.assertEqual(len({p["id"] for p in result}), 4)  # Stable, distinct ids for the avatar.

    def test_processes_and_files_of_one_session_count_once(self):
        files = [("claude", claude_key("/home/ana/Tesis"), "Tesis")]
        procs = [("claude", "/home/ana/Tesis")]
        self.assertEqual(len(pets(files, procs)), 1)
        # A long tool run: the file is quiet but the process works → still cooking; two processes → two.
        self.assertEqual(len(pets([], procs + [("claude", "/home/ana/Tesis")])), 2)
        self.assertEqual(len(pets([], [("codex", "/home/ana/x")] * 9)), 6)  # Capped per tool.

    def test_proc_sampling_needs_cpu_and_home_and_skips_the_browser_bridge(self):
        root, clock = self.root / "proc", Clock()

        def process(pid, comm, ticks, cwd, args=b"claude\0"):
            folder = root / str(pid)
            folder.mkdir(parents=True, exist_ok=True)
            (folder / "comm").write_text(comm + "\n")
            (folder / "cmdline").write_bytes(args)
            (folder / "stat").write_text(f"{pid} ({comm}) S " + " ".join(["0"] * 10) + f" {ticks} 0 0")
            if not (folder / "cwd").is_symlink():
                (folder / "cwd").symlink_to(cwd)
        home = str(self.root / "casa")
        os.makedirs(home + "/Tesis", exist_ok=True)
        process(10, "claude", 100, home + "/Tesis")
        process(11, "claude", 100, "/tmp")  # Outside the user's home.
        process(12, "claude", 100, home + "/Tesis", args=b"claude\0--chrome-native-host\0")
        process(13, "bash", 100, home + "/Tesis")
        sampler = Processes(root=str(root), clock=clock)
        self.assertEqual(sampler.sample(home), [])  # First sample: no previous CPU reading.
        clock.now += 5
        for pid in (10, 11, 12):
            (root / str(pid) / "stat").write_text(f"{pid} (claude) S " + " ".join(["0"] * 10) + " 160 0 0")
        self.assertEqual(sampler.sample(home), [("claude", home + "/Tesis")])
        clock.now += 5
        self.assertEqual(sampler.sample(home), [])  # No CPU since: idle at the prompt.

    def test_setting_turns_the_scan_off(self):
        home = self.root / "agents"
        touch(home / ".claude/projects/-home-ana-Tesis/s1.jsonl")
        cooking = Cooking(self.settings, home=home, processes=Mock(sample=Mock(return_value=[])))
        self.assertEqual(len(cooking.scan()), 1)
        self.settings.update({"agent_pets": False})
        self.assertEqual(cooking.scan(), [])


class WatchTests(Sandbox):
    def setUp(self):
        super().setUp()
        self.settings.update({"watch": True, "screen": True})
        self.clock, self.events, self.memory = Clock(), Mock(), Attachments()
        self.capture = Mock()
        self.reads = []
        self.text = ""

        def read(pixbuf):
            self.reads.append((pixbuf.get_width(), pixbuf.get_height()))
            return self.text
        self.watcher = Watcher(self.settings, self.capture, self.events, self.memory, read=read, clock=self.clock)

    def shot(self, png, scope="all"):
        self.capture.capture.return_value = {"png": png, "scope": scope, "rect": [0, 0, 1600, 600], "app": "Terminal", "note": None}

    def test_posture_sets_the_scope_and_still_leo_watches_nothing(self):
        self.shot(screen())
        self.assertIsNone(self.watcher.tick(force=True))  # Nobody walking or perched yet.
        self.watcher.set_state(":1.9", "perched")
        self.watcher.tick(force=True)
        from leo.screen import CURRENT  # noqa: F401 - the capture ran inside a consent turn with this scope.
        self.assertEqual(self.capture.capture.call_count, 1)
        self.watcher.set_state(":1.9", "off")
        self.assertIsNone(self.watcher.tick(force=True))
        with self.assertRaises(ValueError):
            self.watcher.set_state(":1.9", "everything")

    def test_only_changed_zones_are_read_and_a_quiet_screen_costs_nothing(self):
        self.watcher.set_state(":1.9", "all")
        self.shot(screen())
        first = self.watcher.tick(force=True)
        self.assertEqual(first["changed"], min(MAX_OCR_ZONES, len(zones(1600, 600))))  # New target: read it all.
        self.reads.clear()
        while self.watcher.pending:
            self.watcher.tick(force=True)
        self.reads.clear()
        self.assertEqual(self.watcher.tick(force=True)["changed"], 0)
        self.assertEqual(self.reads, [])
        self.shot(screen(patch=(1210, 340, 300, 200)))  # Inside the bottom band only.
        self.assertEqual(self.watcher.tick(force=True)["changed"], 1)
        self.assertEqual(self.reads, [tuple(zones(1600, 600)[-1][2:])])

    def test_findings_are_announced_once_with_the_capture_kept_for_questions(self):
        self.watcher.set_state(":1.9", "perched")
        self.shot(screen(), scope="perched")
        self.text = "Traceback (most recent call last):\n  File \"x.py\", line 3\nKeyError: 'learning_rate'\n"
        entry = self.watcher.tick(force=True)
        self.assertEqual(entry["finding"], "excepcion")
        title = self.events.publish.call_args.args[2]
        self.assertEqual(title, "Veo un error en Terminal: KeyError: 'learning_rate'")
        self.assertIsNotNone(self.memory.get(":1.9"))  # «¿Qué pasó?» reuses this capture.
        self.shot(screen(patch=(0, 0, 400, 300)), scope="perched")
        self.watcher.tick(force=True)
        self.assertEqual(self.events.publish.call_count, 1)  # Same error: not repeated within 30 min.
        self.assertEqual(zones(700, 456)[0][2], 700)  # Full-width bands: a line is never cut sideways.
        for i in range(PER_HOUR + 2):
            self.text = f"ValueError: caso {i}"
            self.watcher.target = None  # Force a full re-read.
            self.watcher.tick(force=True)
        self.assertEqual(self.events.publish.call_count, PER_HOUR)  # Rate limit per hour.

    def test_pauses_without_capturing(self):
        self.watcher.set_state(":1.9", "all")
        self.shot(screen())
        for change, reason in (({"focus": True}, "concentración"), ({"focus": False, "watch": False}, "desactivados")):
            self.settings.update(change)
            self.assertIn(reason, self.watcher.tick(force=True)["paused"])
        self.settings.update({"watch": True})
        self.watcher.busy = lambda owner: True
        self.clock.now += 61
        self.assertEqual(self.watcher.tick()["paused"], "conversación en curso")
        self.assertNotIn("paused", self.watcher.tick(force=True) or {})  # Asked explicitly: not paused.
        self.capture.capture.reset_mock()
        self.watcher.busy, self.watcher.in_class = (lambda owner: False), (lambda: True)
        self.assertEqual(self.watcher.tick(force=True)["paused"], "clase en curso")
        self.assertEqual(self.capture.capture.call_count, 0)

    def test_interval_and_detectors(self):
        self.watcher.set_state(":1.9", "all")
        self.shot(screen())
        self.assertIsNone(self.watcher.tick())  # Due 2 s after Leo starts walking.
        self.clock.now += 2
        self.assertIsNotNone(self.watcher.tick())
        self.assertIsNone(self.watcher.tick())
        self.clock.now += 60
        self.assertIsNotNone(self.watcher.tick())
        for text, kind in (("FAILED tests/test_a.py::test_b - AssertionError", "tests"),
                           ("torch.OutOfMemoryError: CUDA out of memory.", "excepcion"),
                           ("RuntimeError: CUDA out of memory", "excepcion"), ("1 failed, 41 passed in 3.2s", "tests"),
                           ("npm ERR! code ELIFECYCLE", "npm"), ("error[E0382]: borrow of moved value", "rust"),
                           ("epoch 3 loss=nan", "nan"), ("Segmentation fault (core dumped)", "fallo"),
                           ("| KeyError: 'lr'", "excepcion"), ("Traceback (most recent call last):\n        KeyError: 'lr'", "excepcion"),
                           ("Traceback (most recent call last):\n  File \"a.py\", line 2\n    x()\nKeyErr0r 'lr'", "excepcion"),
                           ("todo bien", None)):
            self.assertEqual((finding(text) or (None,))[0], kind, text)

    def test_schedule_knows_when_a_class_is_running(self):
        from datetime import datetime
        from leo.schedule import Schedule
        now = datetime(2026, 10, 26, 10, 30)  # A Monday.
        schedule = Schedule(Mock(), self.settings, path=self.root / "schedule.json", clock=lambda: now.timestamp())
        schedule.set([{"name": "Redes", "day": 0, "start": "10:00", "end": "12:00"}])
        self.assertTrue(schedule.in_class())
        schedule.set([{"name": "Redes", "day": 0, "start": "12:00", "end": "13:00"}])
        self.assertFalse(schedule.in_class())

    def test_quiet_capture_is_requested_and_leo_is_masked_in_memory(self):
        from leo.screen import masked
        from leo.vision import load_bytes
        shot = {"png": screen(400, 300, color=0xffffffff), "rect": [100, 50, 400, 300], "scope": "perched", "app": "X",
                "note": None, "mask": [[150, 100, 50, 50], ["x"], [0, 0, 10, 10]]}
        pixbuf = load_bytes(masked(dict(shot))["png"])
        pixels, stride, channels = pixbuf.get_pixels(), pixbuf.get_rowstride(), pixbuf.get_n_channels()
        def at(x, y):
            return pixels[y * stride + x * channels]
        self.assertEqual((at(75, 75), at(10, 10), at(200, 200)), (0x80, 0xff, 0xff))  # Only Leo's area is grey.
        self.assertNotIn("mask", masked(dict(shot)))
        self.watcher.set_state(":1.9", "perched")
        self.shot(screen(), scope="perched")
        self.watcher.tick(force=True)
        self.assertEqual(self.capture.capture.call_args.kwargs, {"quiet": True})  # No flash, nothing hidden.

    def test_latest_observation_for_what_do_you_see(self):
        self.watcher.set_state(":1.9", "perched")
        self.shot(screen(), scope="perched")
        self.assertIsNone(self.watcher.recent())
        self.watcher.tick(force=True)
        self.assertIsNotNone(self.watcher.recent())
        self.assertEqual(self.watcher.headline(), "Estoy observando tu ventana de Terminal.")
        self.clock.now += 121
        self.assertIsNone(self.watcher.recent())  # Older than two minutes: gone.
        self.watcher.tick(force=True)
        self.watcher.set_state(":1.9", "off")
        self.assertIsNone(self.watcher.recent())  # Leo stopped watching: forgotten.

    def test_the_best_finding_of_all_bands_wins(self):
        texts = iter(["Traceback (most recent call last):\nKeyErr", "KeyError: 'learning_rate'\n$"])
        self.watcher.read = lambda pixbuf: next(texts, "")
        self.watcher.set_state(":1.9", "perched")
        self.shot(screen(700, 456), scope="perched")
        self.assertEqual(self.watcher.tick(force=True)["finding"], "excepcion")
        self.assertTrue(self.events.publish.call_args.args[2].endswith("KeyError: 'learning_rate'"))
