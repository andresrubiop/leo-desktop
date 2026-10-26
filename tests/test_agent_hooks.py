"""scripts/agent_hooks.py solo con HOME temporal; nunca toca ~/.claude ni ~/.codex reales."""
import importlib.util
import io
import json
import os
from contextlib import redirect_stdout
from pathlib import Path
import tempfile
import tomllib
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("agent_hooks", Path(__file__).resolve().parents[1] / "scripts/agent_hooks.py")
hooks = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hooks)

EXISTING = {"model": "opus", "env": {"TOKEN": "no-imprimir"},
            "hooks": {"Stop": [{"hooks": [{"type": "command", "command": "afplay done.wav"}]}],
                      "PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "check.sh"}]}]}}


class AgentHookTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.home = Path(directory.name)
        env = patch.dict(os.environ, {"HOME": str(self.home), "XDG_DATA_HOME": str(self.home / "data")})
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop("CODEX_HOME", None)
        self.claude = self.home / ".claude/settings.json"
        self.codex = self.home / ".codex/config.toml"

    def run_script(self, *args):
        output = io.StringIO()
        with redirect_stdout(output), self.assertRaises(SystemExit) as code:
            hooks.main(list(args))
        return code.exception.code, output.getvalue()

    def test_install_preserves_existing_hooks_is_idempotent_and_uninstall_restores(self):
        self.claude.parent.mkdir()
        self.claude.write_text(json.dumps(EXISTING))
        self.claude.chmod(0o600)
        self.codex.parent.mkdir()
        codex_text = 'model = "gpt-5"\n\n[profiles.fast]\nmodel = "mini"\n'
        self.codex.write_text(codex_text)
        status, output = self.run_script("--install")
        self.assertEqual(status, 0)
        self.assertNotIn("no-imprimir", output)
        data = json.loads(self.claude.read_text())
        self.assertEqual(data["model"], "opus")
        self.assertEqual(data["hooks"]["PreToolUse"], EXISTING["hooks"]["PreToolUse"])
        self.assertEqual(data["hooks"]["Stop"][0], EXISTING["hooks"]["Stop"][0])
        commands = [h["command"] for g in data["hooks"]["Stop"] + data["hooks"]["Notification"] for h in g["hooks"]]
        self.assertEqual(sum(c.endswith("ctl agent-event --source claude") for c in commands), 2)
        self.assertEqual(self.claude.stat().st_mode & 0o777, 0o600)
        parsed = tomllib.loads(self.codex.read_text())
        self.assertEqual(parsed["notify"][2:], ["ctl", "agent-event", "--source", "codex"])
        self.assertEqual(parsed["profiles"], {"fast": {"model": "mini"}})
        self.assertEqual(len(list(self.home.glob(".claude/settings.json.leo-backup-*"))), 1)
        self.assertEqual(len(list(self.home.glob(".codex/config.toml.leo-backup-*"))), 1)
        before = (self.claude.read_text(), self.codex.read_text())
        self.assertIn("sin cambios", self.run_script("--install")[1])
        self.assertEqual((self.claude.read_text(), self.codex.read_text()), before)
        self.run_script("--uninstall")
        self.assertEqual(json.loads(self.claude.read_text()), EXISTING)
        self.assertEqual(self.codex.read_text(), codex_text)

    def test_existing_codex_notify_is_never_replaced_and_dry_run_writes_nothing(self):
        self.codex.parent.mkdir()
        self.codex.write_text('notify = ["notify-send", "Codex"]\n')
        status, output = self.run_script("--dry-run")
        self.assertEqual(status, 0)
        self.assertIn("vista previa", output)
        self.assertFalse(self.claude.exists())
        self.run_script("--install")
        self.assertEqual(self.codex.read_text(), 'notify = ["notify-send", "Codex"]\n')
        self.assertTrue(self.claude.exists())  # Created fresh with only LEO's hooks.
        self.assertEqual(set(json.loads(self.claude.read_text())["hooks"]), {"Notification", "Stop"})

    def test_invalid_or_symlinked_settings_are_left_alone(self):
        self.claude.parent.mkdir()
        self.claude.write_text("{no es json")
        status, output = self.run_script("--install")
        self.assertEqual(status, 1)
        self.assertEqual(self.claude.read_text(), "{no es json")
        target = self.home / "dotfiles.json"
        target.write_text("{}")
        self.claude.unlink()
        self.claude.symlink_to(target)
        self.assertEqual(self.run_script("--install")[0], 1)
        self.assertEqual(target.read_text(), "{}")
        self.assertTrue(self.claude.is_symlink())


if __name__ == "__main__":
    unittest.main()
