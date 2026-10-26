"""F7.3: el AI Usage Widget dentro de Leo: cuotas de Claude en vivo (G18), vista del widget y blanco y negro."""
import json
import time
from pathlib import Path
from unittest.mock import Mock, patch

from test_actions import Sandbox
from leo.usage.hub import Hub
from leo.widget.collect import Collector, ansi
from leo.widget.grayscale import Grayscale
from leo.widget.util import HttpError

USAGE = {"five_hour": {"utilization": 34, "resets_at": "2026-10-24T10:00:00+00:00"},
         "seven_day": {"utilization": 71, "resets_at": "2026-10-30T06:00:00+00:00"},
         "promo_credit": {"limit_dollars": 100, "used_dollars": 18, "resets_at": "2026-11-05T02:59:00+00:00"}}


class Keyring:
    def list(self):
        return []


class ClaudeLiveTests(Sandbox):
    def setUp(self):
        super().setUp()
        self.profile = self.home / ".claude-trabajo"
        self.profile.mkdir()
        self.credentials(time.time() + 3600)
        (self.profile / ".claude.json").write_text(json.dumps({"oauthAccount": {"emailAddress": "ana@example.com"}}))
        self.live = True
        self.hub = Hub(home=self.home, directory=self.home / ".config/leo", keyring=Keyring(), live=lambda: self.live)
        self.ident = next(a["id"] for a in self.hub.accounts()["accounts"] if a["provider"] == "claude")
        self.calls = []

    def credentials(self, expires):
        (self.profile / ".credentials.json").write_text(json.dumps({"claudeAiOauth": {
            "accessToken": "tok-secreto-123", "expiresAt": int(expires * 1000), "subscriptionType": "claude_max",
            "rateLimitTier": "default_claude_max_5x"}}))

    def fake(self, response):
        def http_json(url, headers=None, **_kwargs):
            self.calls.append((url, headers))
            if isinstance(response, Exception):
                raise response
            return response
        return patch("leo.widget.providers.claude.http_json", http_json)

    def test_live_quotas_with_the_session_token_only_in_the_request(self):
        with self.fake(USAGE):
            account = self.hub.refresh(self.ident)["account"]
        [(url, headers)] = self.calls
        self.assertEqual(url, "https://api.anthropic.com/api/oauth/usage")
        self.assertEqual(headers["Authorization"], "Bearer tok-secreto-123")
        self.assertEqual((account["source"], account["email"], account["plan"], account["error"]),
                         ("live", "ana@example.com", "Max 5x", ""))
        windows = {w["label"]: w for w in account["windows"]}
        self.assertEqual((windows["5h"]["used"], windows["7d"]["used"]), (34, 71))
        credit = next(w for w in account["windows"] if "limit_usd" in w)
        self.assertEqual((credit["used_usd"], credit["limit_usd"]), (18, 100))
        cache = (self.home / ".config/leo/ai-usage.json").read_text()
        self.assertNotIn("tok-secreto", cache)  # The token is never stored.
        self.assertEqual(self.hub.accounts()["accounts"][0]["note"], "")  # Fresh: no «last reading» note.

    def test_rate_limit_waits_and_old_quotas_survive(self):
        with self.fake(USAGE):
            self.hub.refresh(self.ident)
        with self.fake(HttpError(429)):
            account = self.hub.refresh(self.ident)["account"]
        self.assertIn("429", account["error"])
        self.assertEqual(len(account["windows"]), 3)
        with self.fake(USAGE):
            self.hub.refresh(self.ident)
        self.assertEqual(len(self.calls), 2)  # The third refresh waited (15 min backoff).

    def test_live_off_and_expired_sessions_do_not_call_anthropic(self):
        self.live = False
        with self.fake(USAGE):
            account = self.hub.refresh(self.ident)["account"]
        self.assertIn("desactivadas", account["error"])
        self.live = True
        self.credentials(time.time() - 60)
        with self.fake(USAGE):
            account = self.hub.refresh(self.ident)["account"]
        self.assertEqual(self.calls, [])
        self.assertEqual(account["auth"], "expired")
        self.assertIn("renovará", account["error"])


class ViewTests(Sandbox):
    def test_snapshot_draws_the_widget_sections_without_waiting(self):
        now = time.time()
        hub = Mock()
        hub.accounts.return_value = {"accounts": [
            {"id": "claude:/x", "provider": "claude", "name": "claude", "kind": "profile", "email": "ana@example.com",
             "plan": "Max 5x", "fetched_at": now, "windows": [{"label": "5h", "used": 34, "resets_at": now + 9000}]},
            {"id": "codex:/y", "provider": "codex", "name": "oculta", "kind": "profile", "hidden": True, "windows": []}]}
        with patch("leo.widget.collect.fetch_ollama", side_effect=OSError), \
                patch("leo.widget.collect.fetch_comfyui", side_effect=OSError):
            collector = Collector(hub, live=lambda: False)
            collector.snapshot(66)  # The first view only starts the background reading.
            for _ in range(50):
                if not collector.working:
                    break
                time.sleep(.05)
            view = collector.snapshot(66)
        text = "\n".join("".join(t for t, _ in line) for line in view["lines"])
        self.assertIn("SISTEMA", text)
        self.assertIn("CLAUDE", text)
        self.assertIn("34%", text)
        self.assertNotIn("oculta", text)
        self.assertTrue(all(len("".join(t for t, _ in line)) <= 66 for line in view["lines"]))
        self.assertIn("\x1b[", ansi(view["lines"]))
        hub.dispatch.assert_not_called()  # Live off: no refresh of any account.


class FakeSettings:
    def __init__(self, values):
        self.values = values

    def get_boolean(self, key):
        return self.values[key]

    def set_boolean(self, key, value):
        self.values[key] = value

    get_double = get_string = get_boolean
    set_double = set_string = set_boolean


class GrayscaleTests(Sandbox):
    def test_black_and_white_uses_the_magnifier_at_1x_and_restores_it(self):
        mag = FakeSettings({"mag-factor": 2.0, "color-saturation": 1.0, "screen-position": "full-screen"})
        apps = FakeSettings({"screen-magnifier-enabled": False})
        state, saved = {}, []
        gray = Grayscale(state, lambda: saved.append(dict(state)),
                         settings_factory=lambda schema: mag if "magnifier" in schema else apps)
        gray.set(True)
        self.assertTrue(gray.is_on())
        self.assertEqual((mag.values["mag-factor"], apps.values["screen-magnifier-enabled"]), (1.0, True))
        gray.set(False)
        self.assertFalse(gray.is_on())
        self.assertEqual((mag.values["mag-factor"], mag.values["color-saturation"], apps.values["screen-magnifier-enabled"]),
                         (2.0, 1.0, False))
        self.assertEqual(len(saved), 2)
