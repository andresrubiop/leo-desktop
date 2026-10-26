"""Fase 1: avisos, hooks de agentes, vigilancia de trabajos y carpetas autorizadas."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from leo.agents import parse_claude, parse_codex
from leo.broker import Broker, validate_args
from leo.config import Settings, folder_path
from leo.events import Events, plain
from leo.jobs import TAIL, Jobs, gpu_status

# Payloads copied from the Claude Code hooks reference (Notification and Stop input examples).
CLAUDE_NOTIFICATION = {"session_id": "abc123", "transcript_path": "/home/u/.claude/projects/x/00893aaf.jsonl",
                       "cwd": "/home/u/LeoAvatar", "hook_event_name": "Notification",
                       "message": "Claude needs your permission", "title": "Permission needed",
                       "notification_type": "permission_prompt"}
CLAUDE_STOP = {"session_id": "abc123", "transcript_path": "~/.claude/projects/x/00893aaf.jsonl",
               "cwd": "/home/u/LeoAvatar", "permission_mode": "default", "hook_event_name": "Stop",
               "stop_hook_active": False, "last_assistant_message": "I've completed the refactoring. SECRET",
               "background_tasks": [], "session_crons": []}
# Codex `notify` example (agent-turn-complete), with the cwd field of recent versions.
CODEX_TURN = {"type": "agent-turn-complete", "thread-id": "b5f6c1c2", "turn-id": "12345",
              "cwd": "/home/u/proyecto-tesis", "input-messages": ["Rename `foo` to `bar`"],
              "last-assistant-message": "Rename complete and verified `cargo build` succeeds."}


class EventTests(unittest.TestCase):
    def test_plain_text_without_markup_controls_or_excess(self):
        self.assertEqual(plain("  train\x1b[31m <b>listo</b>\n ", 120), "train [31m ‹b›listo‹/b›")
        for bad in ("", "   ", None, "x" * 121):
            with self.assertRaises(ValueError): plain(bad, 120)

    def test_rate_limit_per_origin_and_total(self):
        now = [0.0]
        sent = []
        events = Events(sent.append, clock=lambda: now[0])
        for _ in range(6):
            events.publish("notify:a", "done", "ok")
        with self.assertRaises(RuntimeError): events.publish("notify:a", "done", "ok")
        for i in range(14):
            events.publish(f"notify:{i}", "info", "ok")
        with self.assertRaises(RuntimeError): events.publish("notify:z", "info", "ok")
        now[0] = 61
        events.publish("notify:a", "failed", "de nuevo", "Mi proyecto (v2)")
        self.assertEqual(len(sent), 21)
        self.assertEqual(sent[-1]["project"], "Mi proyecto v2")
        with self.assertRaises(ValueError): events.publish("x", "shell", "ok")

    def test_agent_payloads_keep_only_type_and_folder(self):
        self.assertEqual(parse_claude(json.dumps(CLAUDE_NOTIFICATION)),
                         {"source": "claude", "kind": "permission", "project": "LeoAvatar"})
        stop = parse_claude(json.dumps(CLAUDE_STOP).encode())
        self.assertEqual(stop, {"source": "claude", "kind": "stop", "project": "LeoAvatar"})
        self.assertNotIn("SECRET", json.dumps(stop))
        self.assertEqual(parse_claude(json.dumps({**CLAUDE_NOTIFICATION, "notification_type": "idle_prompt"}))["kind"], "input")
        self.assertIsNone(parse_claude(json.dumps({**CLAUDE_NOTIFICATION, "notification_type": "auth_success"})))
        legacy = dict(CLAUDE_NOTIFICATION)
        legacy.pop("notification_type")
        self.assertEqual(parse_claude(json.dumps(legacy))["kind"], "attention")
        self.assertIsNone(parse_claude(json.dumps({**CLAUDE_STOP, "stop_hook_active": True})))
        self.assertIsNone(parse_claude(json.dumps({**CLAUDE_STOP, "hook_event_name": "PreToolUse"})))
        self.assertEqual(parse_codex(json.dumps(CODEX_TURN)),
                         {"source": "codex", "kind": "stop", "project": "proyecto-tesis"})
        self.assertEqual(parse_codex(json.dumps({"type": "agent-turn-complete"}))["project"], "")
        self.assertIsNone(parse_codex(json.dumps({"type": "approval-requested"})))
        for raw in ("[]", "x" * 1_000_001):
            with self.assertRaises(ValueError): parse_claude(raw)

    def test_agent_titles(self):
        sent = []
        events = Events(sent.append)
        events.agent("claude", "permission", "LeoAvatar")
        events.agent("codex", "stop", "")
        self.assertEqual([(e["event"], e["title"]) for e in sent],
                         [("attention", "Claude Code espera tu aprobación en LeoAvatar"), ("done", "Codex terminó")])
        with self.assertRaises(ValueError): events.agent("gemini", "stop")


class HomeTestCase(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.home = Path(self.directory.name) / "home"
        (self.home / "Cursos/redes").mkdir(parents=True)
        (self.home / ".config/leo").mkdir(parents=True)
        patcher = patch.dict(os.environ, {"HOME": str(self.home)})
        patcher.start()
        self.addCleanup(patcher.stop)

    def folder(self, path, ident="cursos"):
        return {"id": ident, "path": str(path), "label": "Cursos", "write": False}


class FolderTests(HomeTestCase):
    def test_folders_inside_home_without_hidden_or_private_parts(self):
        settings = Settings(self.home / ".config/leo/settings.json")
        settings.update({"folders": [self.folder(self.home / "Cursos")]})
        (self.home / "escape").symlink_to("/etc")
        for path in ("/etc", self.home, self.home / ".config/leo", self.home / "escape", "relativa/ruta"):
            with self.assertRaises(ValueError): folder_path(str(path))
        for bad in ([self.folder(self.home / "Cursos"), self.folder(self.home / "Cursos/redes")],
                    [{**self.folder(self.home / "Cursos"), "write": "yes"}], [self.folder(self.home / "Cursos", "Mal ID")]):
            with self.assertRaises(ValueError): settings.update({"folders": bad})
        # A stored folder that stops qualifying is dropped on load instead of breaking the daemon.
        data = json.loads(settings.path.read_text())
        data["folders"].append(self.folder(self.home / ".ssh", "ssh"))
        settings.path.write_text(json.dumps(data))
        self.assertEqual([f["id"] for f in Settings(settings.path).snapshot()["folders"]], ["cursos"])


class JobTests(HomeTestCase):
    def setUp(self):
        super().setUp()
        self.settings = Settings(self.home / ".config/leo/settings.json")
        self.settings.update({"jobs": True, "folders": [self.folder(self.home / "Cursos")]})
        self.sent = []
        self.jobs = Jobs(self.settings, Events(self.sent.append))
        self.log = self.home / "Cursos/redes/train.log"

    def wait(self, process):
        process.wait(timeout=20)
        for watch in list(self.jobs.watches.values()):
            self.jobs._check(watch)

    def test_process_that_ends_is_reported_once(self):
        process = subprocess.Popen(["sleep", "0.3"])
        result = self.jobs.watch({"pid": process.pid})
        self.assertEqual(result["name"], "sleep")
        self.assertEqual(self.jobs.summary(), {"active": 1})
        self.wait(process)
        self.assertEqual([(e["event"], e["title"]) for e in self.sent], [("done", "sleep terminó")])
        self.assertEqual(self.jobs.summary(), {"active": 0})

    def test_process_printing_nan_and_log_issues(self):
        script = ("import sys, time\nprint('epoch 1 step 10 loss: 0.734', flush=True)\n"
                  "print('epoch 1 step 20 loss: nan lr=3e-4', flush=True)\ntime.sleep(.3)\n")
        with self.log.open("w") as log:
            process = subprocess.Popen([sys.executable, "-c", script], stdout=log)
            self.jobs.watch({"pid": process.pid, "log": str(self.log)})
            self.wait(process)
        events = [(e["event"], e["title"]) for e in self.sent]
        self.assertTrue(any(e == "attention" and t.endswith(": métrica con NaN o infinito") for e, t in events), events)
        self.assertEqual(events[-1][0], "done")
        report = self.jobs.report()
        self.assertEqual(report["jobs"][0]["metrics"], {"epoch": "1", "step": "20", "loss": "nan", "lr": "3e-4"})
        self.assertNotIn("epoch 1 step", json.dumps(report))  # Numbers only, never log lines.

    def test_fatal_log_patterns_and_tail_only(self):
        self.log.write_text("x" * (TAIL * 3) + "\nTraceback (most recent call last):\n  File \"t.py\"\n"
                            "torch.OutOfMemoryError: CUDA out of memory. Tried to allocate 2.00 GiB\n"
                            "./run.sh: line 3: 4242 Killed  python train.py\n")
        result = self.jobs.watch({"log": str(self.log)})
        watch = self.jobs.watches[result["watch"]]
        self.assertEqual(watch["issues"], ["traceback", "cuda_oom", "killed"])
        self.assertEqual(watch["state"], "failed")
        self.assertLessEqual(watch["offset"] - (self.log.stat().st_size - TAIL), TAIL)
        self.assertEqual(len(self.sent), 3)

    def test_log_must_be_inside_an_authorized_folder(self):
        outside = Path(self.directory.name) / "train.log"
        outside.write_text("loss: 1\n")
        hidden = self.home / "Cursos/.cache/train.log"
        hidden.parent.mkdir()
        hidden.write_text("loss: 1\n")
        link = self.home / "Cursos/link.log"
        link.symlink_to(outside)
        for path in (outside, hidden, link):
            with self.assertRaises(PermissionError): self.jobs.watch({"log": str(path)})
        with self.assertRaises(ValueError): self.jobs.watch({"pid": 99999999})
        self.settings.update({"jobs": False})
        with self.assertRaises(PermissionError): self.jobs.watch({"log": str(self.log)})

    def test_gpu_status_parses_fixed_query_or_reports_absence(self):
        with patch("leo.jobs.shutil.which", return_value=None):
            self.assertFalse(gpu_status()["available"])
        fake = subprocess.CompletedProcess([], 0, "NVIDIA GeForce RTX 4070, 87, 9120, 12282, 71\n", "")
        with patch("leo.jobs.shutil.which", return_value="/usr/bin/nvidia-smi"):
            calls = []
            status = gpu_status(lambda argv, **kw: calls.append((argv, kw)) or fake)
        self.assertEqual(status["gpus"][0], {"name": "NVIDIA GeForce RTX 4070", "utilization_percent": 87.0,
                                             "memory_used_mib": 9120.0, "memory_total_mib": 12282.0, "temperature_c": 71.0})
        self.assertEqual(calls[0][0][0], "/usr/bin/nvidia-smi")
        self.assertNotIn("shell", calls[0][1])

    def test_broker_job_tools(self):
        validate_args("job.watch", {"pid": 10})
        validate_args("job.watch", {"log": "/home/u/a.log"})
        validate_args("job.watch", {"pid": 10, "log": "/home/u/a.log"})
        for bad in ({}, {"pid": "10"}, {"pid": 0}, {"pid": True}, {"cmd": "rm"}, {"pid": 1, "cmd": "x"}):
            with self.assertRaises(ValueError): validate_args("job.watch", bad)
        settings = Settings(self.home / ".config/leo/other.json")
        broker = Broker(settings, lambda name, args: "OK")
        with self.assertRaises(PermissionError): broker.request("owner", "gpu.status", {})
        settings.update({"jobs": True})
        self.assertEqual(broker.request("owner", "gpu.status", {}), {"result": "OK"})


if __name__ == "__main__":
    unittest.main()
