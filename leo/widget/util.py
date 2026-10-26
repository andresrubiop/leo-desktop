"""Small shared helpers: paths, JSON, JWT, HTTP and formatting."""

from __future__ import annotations

import base64
import json
import os
import ssl
import time
import unicodedata
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from . import APP_NAME, __version__

USER_AGENT = f"{APP_NAME}/{__version__}"


# ── paths ─────────────────────────────────────────────────────────────────────

def home() -> Path:
    return Path(os.path.expanduser("~"))


def xdg_dir(env: str, fallback: str) -> Path:
    value = os.environ.get(env, "")
    base = Path(value) if value and os.path.isabs(value) else home() / fallback
    return base / APP_NAME


def config_dir() -> Path:
    return xdg_dir("XDG_CONFIG_HOME", ".config")


def cache_dir() -> Path:
    return xdg_dir("XDG_CACHE_HOME", ".cache")


def state_dir() -> Path:
    return xdg_dir("XDG_STATE_HOME", ".local/state")


def expand_path(raw: str) -> Path | None:
    """Expand a path written in a shell file (quotes, ~, $HOME, ${VAR}).

    Returns None when it still contains unresolved variables or globs."""
    value = raw.strip().strip("'\"")
    if not value:
        return None
    value = value.replace("${HOME}", str(home())).replace("$HOME", str(home()))
    value = os.path.expanduser(os.path.expandvars(value))
    if "$" in value or any(c in value for c in "*?[]`") or not os.path.isabs(value):
        return None
    return Path(os.path.normpath(value))


# ── files ─────────────────────────────────────────────────────────────────────

def read_json(path: Path | str) -> dict | None:
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def write_json_atomic(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=1, ensure_ascii=False)
    os.replace(tmp, path)


def jwt_claims(token: str | None) -> dict:
    """Decode the payload of a JWT *without* verifying it (display only)."""
    if not token or token.count(".") < 2:
        return {}
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        data = json.loads(base64.urlsafe_b64decode(payload))
        return data if isinstance(data, dict) else {}
    except (ValueError, UnicodeDecodeError):
        return {}


# ── HTTP ──────────────────────────────────────────────────────────────────────

class HttpError(Exception):
    def __init__(self, status: int | None, message: str = ""):
        super().__init__(message or f"HTTP {status}")
        self.status = status


_direct_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def http_get(url: str, headers: dict | None = None, body=None, timeout: float = 10,
             local: bool = False, insecure: bool = False, max_bytes: int | None = None) -> bytes:
    """GET (or POST when ``body`` is given) and return the raw response body.

    ``local`` bypasses proxies (for 127.0.0.1 services); ``insecure`` skips TLS
    verification, which is only ever used for self-signed localhost servers."""
    data = json.dumps(body).encode() if body is not None else None
    hdrs = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    if data is not None:
        hdrs["Content-Type"] = "application/json"
    hdrs.update(headers or {})
    req = urllib.request.Request(url, data=data, headers=hdrs)
    ctx = ssl._create_unverified_context() if insecure else None  # noqa: S323 (localhost only)
    opener = _direct_opener if local else urllib.request.build_opener()
    if ctx is not None:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}),
                                             urllib.request.HTTPSHandler(context=ctx))
    try:
        with opener.open(req, timeout=timeout) as resp:
            raw = resp.read(max_bytes + 1) if max_bytes else resp.read()
    except urllib.error.HTTPError as exc:
        raise HttpError(exc.code) from None
    except (urllib.error.URLError, OSError, ValueError) as exc:
        reason = getattr(exc, "reason", exc)
        raise HttpError(None, str(reason)) from None
    if max_bytes and len(raw) > max_bytes:
        raise HttpError(None, "response too large")
    return raw


def http_json(url: str, headers: dict | None = None, body=None, timeout: float = 10,
              local: bool = False, insecure: bool = False, max_bytes: int | None = None):
    """Like :func:`http_get`, decoding a JSON response."""
    raw = http_get(url, headers, body, timeout, local, insecure, max_bytes)
    try:
        return json.loads(raw.decode("utf-8"))
    except ValueError:
        raise HttpError(None, "invalid JSON") from None


# ── time & formatting ─────────────────────────────────────────────────────────

def now() -> float:
    return time.time()


def parse_iso(value) -> float | None:
    """ISO-8601 string → epoch seconds (None on failure)."""
    if not value or not isinstance(value, str):
        return None
    text = value.strip().replace("Z", "+00:00")
    # Python < 3.11 cannot parse more than 6 fractional digits.
    if "." in text:
        head, _, rest = text.partition(".")
        digits = len(rest) - len(rest.lstrip("0123456789"))
        text = f"{head}.{rest[:min(digits, 6)]}{rest[digits:]}"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)  # APIs speak UTC
    return parsed.timestamp()


def fmt_duration(seconds: float) -> str:
    seconds = max(0, int(seconds))
    if seconds < 60:
        return "<1m"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes}m"
    hours, minutes = divmod(minutes, 60)
    if hours < 24:
        return f"{hours}h{minutes:02d}m"
    days, hours = divmod(hours, 24)
    return f"{days}d {hours}h"


def fmt_bytes(n: float, unit: str = "GB") -> str:
    """Decimal sizes like Ollama prints them (GB = 1e9)."""
    if n is None:
        return "?"
    if unit == "GiB":
        return f"{n / 2**30:.1f}"
    if n >= 1e9:
        return f"{n / 1e9:.1f} GB"
    if n >= 1e6:
        return f"{n / 1e6:.0f} MB"
    return f"{n / 1e3:.0f} KB"


def char_width(ch: str) -> int:
    if unicodedata.combining(ch):
        return 0
    return 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1


def text_width(text: str) -> int:
    return sum(char_width(c) for c in text)


def truncate(text: str, width: int) -> str:
    """Cut ``text`` to ``width`` display columns, ending with an ellipsis."""
    if width <= 0:
        return ""
    if text_width(text) <= width:
        return text
    out, used = [], 0
    for ch in text:
        w = char_width(ch)
        if used + w > width - 1:
            break
        out.append(ch)
        used += w
    return "".join(out) + "…"
