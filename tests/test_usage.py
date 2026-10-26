import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from leo.conversation import opening
from leo.usage import api, skills
from leo.usage.hub import Hub, clean_usage
from leo.usage.net import http_get, HttpError, NoRedirect
from leo.usage.storage import write
from leo.terminal import appearance


class EmptyKeyring:
    def list(self):
        return []


class UsageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.env = patch.dict(os.environ, XDG_CONFIG_HOME=str(self.root / "config"),
                              XDG_CACHE_HOME=str(self.root / "cache"), XDG_STATE_HOME=str(self.root / "state"))
        self.env.start()
        self.hub = Hub(self.root, self.root / "config/leo", EmptyKeyring())

    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()

    def test_migrate_cache_without_credentials_and_preserve_expired_usage(self):
        profile = self.root / ".codex-test"
        profile.mkdir()
        ident = "codex:" + str(profile)
        old = {"accounts": {ident: {"fetched_at": 100, "windows": [{"label": "5h", "used": 93, "resets_at": 150}],
                                   "token": "must-not-survive", "command": "unsafe", "secret": "never"}}}
        write(self.root / "cache/ai-usage-widget/usage.json", old)
        account = self.hub.accounts()["accounts"][0]
        self.assertEqual(account["source"], "cache")
        self.assertEqual(account["windows"][0]["used"], 93)
        self.assertNotIn("token", account)
        self.assertNotIn("command", account)
        with patch("leo.usage.codex.read_account", side_effect=RuntimeError("Consulta fallida")):
            result = self.hub.refresh(ident)["account"]
        self.assertEqual(result["fetched_at"], 100)
        self.assertEqual(result["windows"][0]["used"], 93)
        self.assertEqual(self.hub.cache_path.stat().st_mode & 0o777, 0o600)

    def test_profile_rename_hide_and_no_overwrite(self):
        self.hub.profile("codex", "trabajo", "")
        account = self.hub.accounts()["accounts"][0]
        self.hub.rename(account["id"], "Mi cuenta", True)
        new = self.hub.accounts()["accounts"][0]
        self.assertTrue(new["hidden"])
        self.assertEqual(new["name"], "Mi cuenta")
        with self.assertRaises(FileExistsError):
            self.hub.profile("codex", "trabajo", "")
        with self.assertRaises(ValueError):
            self.hub.profile("codex", "../escape", "")

    def test_skill_review_install_is_scoped_and_does_not_overwrite(self):
        (self.root / ".agents/skills").mkdir(parents=True)
        result = skills.SearchResult("owner/repo/demo", "demo", "owner/repo", "demo")
        package = skills._package(result, [("SKILL.md", b"---\nname: demo\n---\nread me"), ("refs/a.txt", b"reference")])
        self.hub.previews["approved"] = (time.monotonic() + 30, package)
        self.hub.install("approved", "codex")
        self.assertEqual((self.root / ".agents/skills/demo/refs/a.txt").read_text(), "reference")
        with self.assertRaises(ValueError):
            self.hub.install("approved", "codex")
        self.hub.previews["another"] = (time.monotonic() + 30, package)
        with self.assertRaises(ValueError):
            self.hub.install("another", "codex")
        with self.assertRaises(skills.SkillError):
            skills._check_files([("SKILL.md", b"ok"), ("../escape", b"bad")])

    def test_provider_region_is_explicit_and_errors_do_not_echo_secrets(self):
        with patch.dict(api.FETCHERS, {"kimi": lambda *_: self.fail("must not call network")}):
            with self.assertRaises(ValueError):
                api.query("kimi", "", "secret-test")
        with patch.object(api, "http_json", return_value={"success": False, "msg": "private secret"}):
            with self.assertRaisesRegex(HttpError, "proveedor") as caught:
                api.fetch_zai("https://api.z.ai", "test", lambda k: k)
        self.assertNotIn("private", str(caught.exception))

    def test_network_rejects_other_hosts_and_redirects(self):
        for url in ("http://api.deepseek.com/user/balance", "https://evil.invalid", "https://key@api.deepseek.com/a"):
            with self.assertRaises(HttpError):
                http_get(url)
        self.assertIsNone(NoRedirect().redirect_request(None, None, 302, "", {}, "https://evil.invalid"))

    def test_missing_balance_is_not_presented_as_zero(self):
        for fetch in (api.fetch_kimi, api.fetch_siliconflow):
            with patch.object(api, "http_json", return_value={"data": {}}):
                with self.assertRaises(HttpError):
                    fetch("https://api.moonshot.ai", "fake", lambda key: key)
        with patch.object(api, "http_json", return_value={"data": {"available_balance": 0}}):
            result = api.fetch_kimi("https://api.moonshot.ai", "fake", lambda key: key)
            self.assertIn("0.00", result.balances[0][1])

    def test_widget_theme_is_migrated_once(self):
        legacy = self.root / "config/ai-usage-widget/config.toml"
        legacy.parent.mkdir(parents=True)
        legacy.write_text('[appearance]\ntheme="tango"\nfont="Monospace 12"\n')
        self.assertEqual(appearance()["bg"], "#2E3436")
        legacy.write_text('[appearance]\ntheme="matrix"\n')
        self.assertEqual(appearance()["name"], "tango")

    def test_opening_intent_never_interprets_shell_or_model_output(self):
        self.assertEqual(opening("Leo, abre una terminal por favor"), ("terminal.open", {}))
        self.assertEqual(opening("Abre la calculadora"), ("app.open", {"app": "calculator"}))
        self.assertEqual(opening("Abre los ajustes, por favor"), ("app.open", {"app": "settings"}))
        self.assertIsNone(opening("Abre los materiales de Redes Neuronales"))  # Files: the agent decides.
        self.assertIsNone(opening("Abre paper-atencion.pdf"))
        self.assertIsNone(opening("No abras una terminal"))
        self.assertIsNone(opening('{"tool": "terminal.open"}'))
        with patch("leo.conversation.catalog", return_value={}):
            self.assertEqual(opening("abre terminal; rm -rf /"), ("unmatched", {}))

    def test_invalid_usage_values_are_not_rendered_as_real_quotas(self):
        result = clean_usage({"windows": [{"used": float("nan")}, {"used": "unknown"}], "key": "secret"})
        self.assertEqual(result["windows"], [])
        self.assertNotIn("key", result)


if __name__ == "__main__":
    unittest.main()
