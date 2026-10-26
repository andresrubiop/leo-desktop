import copy
import io
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from leo.broker import Broker, validate_args
from leo.config import Settings, DEFAULTS
from leo.providers import Ollama, NoRedirect, choose
from leo.actions import save_note
from leo.ipc import decode


class BrokerTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.settings = Settings(Path(self.directory.name) / "settings.json")
        self.executed = []
        self.now = 10
        self.broker = Broker(self.settings, lambda name, args: self.executed.append((name, args)) or "OK",
                             clock=lambda: self.now)

    def test_defaults_deny_sensitive_capabilities(self):
        self.assertFalse(any(self.settings.snapshot()[k] for k in ("screen", "files", "codex", "microphone", "apps")))
        for name, args in (("app.open", {"app": "files"}), ("screen.capture", {}),
                           ("remote.ask", {"provider": "codex", "prompt": "Hola"})):
            with self.assertRaises(PermissionError):
                self.broker.request("alice", name, args)
        self.assertEqual(self.executed, [])

    def test_approval_has_no_effect_until_confirmed_once(self):
        self.settings.update({"files": True})
        args = {"name": "test", "text": "original"}
        response = self.broker.request("alice", "note.save", args)
        args["text"] = "tampered"
        response["arguments"]["text"] = "tampered-again"
        self.assertFalse(self.executed)
        token = response["approval"]
        with self.assertRaises(PermissionError):
            self.broker.decide("mallory", token, True)
        self.broker.decide("alice", token, True)
        self.assertEqual(self.executed[0][1]["text"], "original")
        with self.assertRaises(PermissionError):
            self.broker.decide("alice", token, True)

    def test_expiry_cancellation_disconnect_and_revocation(self):
        self.settings.update({"screen": True})
        token = self.broker.request("a", "screen.capture", {})["approval"]
        self.now += 121
        with self.assertRaises(PermissionError):
            self.broker.decide("a", token, True)
        token = self.broker.request("a", "screen.capture", {})["approval"]
        self.broker.decide("a", token, False)
        token = self.broker.request("a", "screen.capture", {})["approval"]
        self.broker.disconnect("a")
        with self.assertRaises(PermissionError):
            self.broker.decide("a", token, True)
        token = self.broker.request("a", "screen.capture", {})["approval"]
        self.settings.update({"screen": False})
        with self.assertRaises(PermissionError):
            self.broker.decide("a", token, True)
        self.assertFalse(self.executed)

    def test_approval_cannot_be_replayed_concurrently(self):
        self.settings.update({"commands": True})
        token = self.broker.request("a", "system.command", {"command": "memory"})["approval"]
        def approve():
            try:
                self.broker.decide("a", token, True)
            except PermissionError:
                pass
        threads = [threading.Thread(target=approve) for _ in range(10)]
        for thread in threads: thread.start()
        for thread in threads: thread.join()
        self.assertEqual(len(self.executed), 1)

    def test_injection_unknown_fields_and_types_denied(self):
        attacks = [("shell", {"command": "ls"}), ("system.command", {"command": "uptime; touch /tmp/pwn"}),
                   ("note.save", {"name": "../secret", "text": "x"}),
                   ("app.open", {"app": "files", "args": ["--evil"]}),
                   ("pointer.move", {"dx": True, "dy": 1}),
                   ("pointer.move", {"dx": 301, "dy": 0}),
                   ("remote.ask", {"provider": "custom", "prompt": "x"}),
                   ("music.control", {"action": "Quit"})]
        for name, args in attacks:
            with self.subTest(name=name), self.assertRaises(ValueError):
                validate_args(name, args)

    def test_remote_request_denied_even_with_legacy_flags(self):
        self.settings.values.update(codex=True, claude=True)
        for provider in ("codex", "claude"):
            with self.assertRaisesRegex(PermissionError, "LLM local"):
                self.broker.request("a", "remote.ask", {"provider": provider, "prompt": "Hola"})
        self.assertFalse(self.executed)
        self.assertFalse(self.broker.pending)

    def test_capability_allows_only_low_risk_action(self):
        self.settings.update({"apps": True})
        self.assertEqual(self.broker.request("a", "terminal.open", {}), {"result": "OK"})


class StorageTests(unittest.TestCase):
    def test_avatar_corner_persists_and_rejects_invalid_positions(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            settings = Settings(path)
            self.assertEqual(settings.snapshot()["corner"], "right")
            settings.update({"corner": "left"})
            self.assertEqual(Settings(path).snapshot()["corner"], "left")
            for position in ("center", "", True, 300):
                with self.assertRaises(ValueError): settings.update({"corner": position})

    def test_movement_settings_and_local_only_migration(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            path.write_text(json.dumps({"codex": True, "claude": True}))
            settings = Settings(path)
            self.assertFalse(settings.snapshot()["codex"])
            self.assertFalse(settings.snapshot()["claude"])
            position = {"monitor": 1, "x": .3, "y": .8}
            settings.update({"roam": True, "roam_scope": "all", "pace": "slow", "position": position})
            self.assertEqual(Settings(path).snapshot()["position"], position)
            for changes in ({"codex": True}, {"claude": True}, {"roam_scope": "remote"},
                            {"pace": "fast"}, {"position": {"monitor": 0, "x": float("nan"), "y": 0}},
                            {"position": {"monitor": -1, "x": 0, "y": 0}}, {"position": "bad"}):
                with self.assertRaises(ValueError): settings.update(changes)
            settings.update({"position": None})
            self.assertIsNone(Settings(path).snapshot()["position"])

    def test_settings_persist_and_reject_wrong_types(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            settings = Settings(path)
            settings.update({"motion": False, "model": "qwen3:8b"})
            self.assertFalse(Settings(path).snapshot()["motion"])
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            settings.update({"num_ctx": 16384})
            self.assertEqual(Settings(path).snapshot()["num_ctx"], 16384)
            self.assertEqual(DEFAULTS["num_ctx"], 8192)
            for changes in ({"screen": "true"}, {"unknown": True}, {"model": "x;rm"}, {"num_ctx": True},
                            {"num_ctx": 1024}, {"num_ctx": "8192"}, {"num_ctx": 8192.0}):
                with self.assertRaises(ValueError): settings.update(changes)
            self.assertFalse(Settings(path).snapshot()["screen"])

    def test_note_cannot_overwrite_or_follow_symlink(self):
        with tempfile.TemporaryDirectory() as directory:
            notes = Path(directory) / "notes"
            save_note(notes, "a", "contenido")
            with self.assertRaises(FileExistsError): save_note(notes, "a", "otro")
            secret = Path(directory) / "secret"
            secret.write_text("no cambiar")
            (notes / "b.md").symlink_to(secret)
            with self.assertRaises(FileExistsError): save_note(notes, "b", "otro")
            self.assertEqual(secret.read_text(), "no cambiar")
            redirect = Path(directory) / "redirect"
            redirect.symlink_to(notes, target_is_directory=True)
            with self.assertRaises(OSError): save_note(redirect, "c", "otro")


class ProviderTests(unittest.TestCase):
    def test_automatic_model_favors_conversation_but_respects_explicit_choice(self):
        provider = Ollama()
        with patch.object(provider, "models", return_value=["qwen-coder:7b", "qwen:8b"]), patch.object(provider, "_request", return_value={"message": {"content": "Leo"}}) as call:
            provider.chat("cuotas", context="Datos comprobados")
            payload = call.call_args.args[1]
            self.assertEqual(payload["model"], "qwen:8b")
            self.assertEqual(payload["messages"][-2]["role"], "tool")
            provider.chat("hola", model="qwen-coder:7b")
            self.assertEqual(call.call_args.args[1]["model"], "qwen-coder:7b")

    def test_qwen3_is_the_automatic_default_and_context_is_configurable(self):
        self.assertEqual(choose(["qwen2.5-coder:7b", "llama3:8b", "qwen3:8b"]), "qwen3:8b")
        self.assertEqual(choose(["qwen2.5-coder:7b", "llama3:8b"]), "llama3:8b")
        self.assertEqual(choose(["qwen2.5-coder:7b"]), "qwen2.5-coder:7b")
        self.assertEqual(choose(["qwen3:8b", "llama3:8b"], "llama3:8b"), "llama3:8b")
        with self.assertRaises(ValueError): choose(["qwen3:8b"], "missing")
        provider = Ollama()
        with patch.object(provider, "models", return_value=["qwen3:8b"]), patch.object(provider, "_request", return_value={"message": {"content": "Leo"}}) as call:
            provider.chat("hola", num_ctx=4096)
            payload = call.call_args.args[1]
            self.assertEqual(payload["options"]["num_ctx"], 4096)
            self.assertFalse(payload["stream"])

    def test_stream_forwards_literal_deltas_and_returns_full_text(self):
        provider = Ollama()
        lines = [{"message": {"content": "<b>Hola</b>"}, "done": False}, {"message": {"content": ""}},
                 {"message": {"content": ", soy Leo"}, "done": False}, {"message": {"content": ""}, "done": True}]
        body = io.BytesIO(b"\n".join(json.dumps(line).encode() for line in lines) + b"\n")
        chunks = []
        with patch.object(provider, "models", return_value=["qwen3:8b"]), patch.object(provider, "_open", return_value=body) as opened:
            self.assertEqual(provider.chat("hola", on_chunk=chunks.append), "<b>Hola</b>, soy Leo")
        self.assertEqual(chunks, ["<b>Hola</b>", ", soy Leo"])
        self.assertTrue(opened.call_args.args[1]["stream"])
        for raw, message in ((b'{"error": "model not found"}\n', "Ollama: model not found"),
                             (b'{"message": {"content": "a"}}\n', "antes de terminar")):
            with patch.object(provider, "models", return_value=["qwen3:8b"]), patch.object(provider, "_open", return_value=io.BytesIO(raw)):
                with self.assertRaisesRegex(RuntimeError, message): provider.chat("hola", on_chunk=chunks.append)

    def test_ollama_port_override_is_loopback_only(self):
        with patch.dict(os.environ, {"LEO_OLLAMA_PORT": "11500"}):
            self.assertEqual(Ollama().base, "http://127.0.0.1:11500")
        for value in ("80", "example.com:11434", "99999"):
            with patch.dict(os.environ, {"LEO_OLLAMA_PORT": value}), self.assertRaises(ValueError):
                Ollama()

    def test_models_filter_cloud_and_nonlocal(self):
        provider = Ollama()
        with patch.object(provider, "_request", return_value={"models": [
            {"name": "local:8b", "details": {"format": "gguf"}},
            {"name": "remote:cloud", "details": {"format": "gguf"}},
            {"name": "remote", "remote_host": "https://example.com", "details": {"format": "gguf"}},
        ]}):
            self.assertEqual(provider.models(), ["local:8b"])

    def test_local_model_required_and_text_only(self):
        provider = Ollama()
        with patch.object(provider, "models", return_value=["local"]), patch.object(provider, "_request") as call:
            with self.assertRaises(ValueError): provider.chat("x", "remote")
            call.assert_not_called()
            call.return_value = {"message": {"content": "text", "tool_calls": [{"shell": "rm"}]}}
            self.assertEqual(provider.chat("x", "local"), "text")
            payload = call.call_args.args[1]
            self.assertNotIn("tools", payload)

    def test_redirects_are_denied(self):
        with self.assertRaises(ValueError):
            NoRedirect().redirect_request(None, None, 302, "", {}, "https://example.com")

    def test_local_failure_has_no_fallback(self):
        provider = Ollama()
        with patch.object(provider, "models", side_effect=RuntimeError("Ollama local no responde")):
            with self.assertRaisesRegex(RuntimeError, "Ollama local"):
                provider.chat("Hola")

    def test_ipc_rejects_large_or_nonobject_messages(self):
        for raw in ("[]", "null", "x" * 64001):
            with self.assertRaises(ValueError): decode(raw)


if __name__ == "__main__":
    unittest.main()
