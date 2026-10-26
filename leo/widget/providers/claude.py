"""Claude Code (claude.ai subscription) usage limits.

Account info comes from ``.claude.json`` and the OAuth login from
``.credentials.json`` inside the profile folder. Usage is read from the same
endpoint Claude Code's ``/usage`` command uses; when that is not possible we fall
back to the copy Claude Code caches in ``.claude.json``. Tokens are never
refreshed or written: an expired login is reported so the user can open Claude
Code (which renews it)."""

from __future__ import annotations

from pathlib import Path

from ..discovery import AccountRef
from ..util import HttpError, home, http_json, now, parse_iso, read_json
from .common import Account, Window

USAGE_URL = "https://api.anthropic.com/api/oauth/usage"

LIMIT_LABELS = {
    "session": "5h",
    "five_hour": "5h",
    "weekly_all": "7d",
    "seven_day": "7d",
    "weekly_opus": "7d opus",
    "seven_day_opus": "7d opus",
    "weekly_sonnet": "7d sonnet",
    "seven_day_sonnet": "7d sonnet",
}


def config_file(ref: AccountRef) -> Path:
    """Without CLAUDE_CONFIG_DIR, Claude Code keeps its config in ~/.claude.json."""
    legacy = home() / ".claude.json"
    if ref.path == home() / ".claude" and legacy.exists():
        return legacy
    return ref.path / ".claude.json"


def plan_name(subscription: str | None, tier: str | None) -> str:
    sub = (subscription or "").lower().replace("claude_", "")
    tier = (tier or "").lower()
    if not sub:
        return ""
    name = sub.capitalize()
    for mult in ("20x", "5x"):
        if mult in tier:
            name += f" {mult}"
    return name


def _plan_windows(data: dict) -> list[Window]:
    windows: list[Window] = []
    limits = data.get("limits")
    if isinstance(limits, list) and limits:
        for item in limits:
            if not isinstance(item, dict) or item.get("percent") is None:
                continue
            kind = str(item.get("kind") or item.get("group") or "?")
            label = LIMIT_LABELS.get(kind) or kind.replace("weekly_", "7d ").replace("_", " ")
            scope = item.get("scope")
            if isinstance(scope, str) and scope and scope not in label:
                label = f"{label} {scope}"
            windows.append(Window(label, float(item["percent"]), parse_iso(item.get("resets_at"))))
        if windows:
            return windows
    for key, label in LIMIT_LABELS.items():
        item = data.get(key)
        if isinstance(item, dict) and item.get("utilization") is not None:
            if any(w.label == label for w in windows):
                continue
            windows.append(Window(label, float(item["utilization"]), parse_iso(item.get("resets_at"))))
    return windows


def _credit_windows(data: dict, label: str) -> list[Window]:
    """Dollar allowances (promotional credits…). Anthropic files them under
    internal code names that change, so match on the shape, not the key."""
    windows = []
    for key in sorted(data):
        item = data[key]
        if not isinstance(item, dict) or not item.get("limit_dollars"):
            continue
        limit = float(item["limit_dollars"])
        used = item.get("used_dollars")
        if used is None and item.get("remaining_dollars") is not None:
            used = limit - float(item["remaining_dollars"])
        used = float(used or 0.0)
        pct = item.get("utilization")
        pct = float(pct) if pct is not None else 100.0 * used / limit
        name = label if not windows else f"{label} {len(windows) + 1}"
        windows.append(Window(name, pct, parse_iso(item.get("resets_at")), used_usd=used, limit_usd=limit))
    return windows


def parse_usage(data: dict, credit_label: str = "credit") -> list[Window]:
    return _plan_windows(data) + _credit_windows(data, credit_label)


def extra_usage_note(data: dict, tr) -> str | None:
    extra = data.get("extra_usage")
    if isinstance(extra, dict) and extra.get("is_enabled") and extra.get("monthly_limit"):
        dec = int(extra.get("decimal_places") or 0)
        div = 10 ** dec if dec else 1
        used = (extra.get("used_credits") or 0) / div
        limit = extra["monthly_limit"] / div
        return tr("extra_usage", used=f"{used:.2f}", limit=f"{limit:.2f}")
    return None


def fetch(ref: AccountRef, tr, live: bool = True) -> Account:
    acc = Account("claude", ref.name, path=str(ref.path), key=ref.key)
    cfg = read_json(config_file(ref)) or {}
    account = cfg.get("oauthAccount") or {}
    creds = (read_json(ref.path / ".credentials.json") or {}).get("claudeAiOauth") or {}

    acc.email = account.get("emailAddress", "")
    acc.plan = plan_name(creds.get("subscriptionType") or account.get("organizationType"),
                         creds.get("rateLimitTier") or account.get("organizationRateLimitTier"))
    token = creds.get("accessToken")
    if not token:
        acc.auth = "nologin"
        return acc

    expired = (creds.get("expiresAt") or 0) / 1000 < now() + 30
    data = None
    if live and not expired:
        try:
            data = http_json(USAGE_URL, {"Authorization": f"Bearer {token}",
                                         "anthropic-beta": "oauth-2025-04-20"})
            acc.source, acc.fetched_at = "live", now()
        except HttpError as exc:
            if exc.status in (401, 403):
                expired = True
            elif exc.status == 429:
                acc.rate_limited = True
            else:
                acc.error = str(exc)

    if data is None:
        cached = cfg.get("cachedUsageUtilization") or {}
        same_account = cached.get("accountUuid") in (None, account.get("accountUuid"))
        if isinstance(cached.get("utilization"), dict) and same_account:
            data = cached["utilization"]
            acc.source = "cache"
            acc.fetched_at = (cached.get("fetchedAtMs") or 0) / 1000 or None

    if expired:
        acc.auth = "expired"
    if isinstance(data, dict):
        acc.windows = parse_usage(data, tr("credit"))
        note = extra_usage_note(data, tr)
        if note:
            acc.notes.append(note)
    return acc
