"""Google Antigravity per-model quotas.

Live data: every running Antigravity process (the IDE's language server or the
``agy`` hub) serves a local API protected by a CSRF token passed on its command
line. We find those processes in /proc, map them to their listening ports and
ask for ``GetUserStatus`` — only ever on 127.0.0.1.

Offline data: Antigravity stores the last user status (protobuf, base64) in its
VS Code-style ``state.vscdb``; we decode the few fields we need from it."""

from __future__ import annotations

import base64
import json
import os
import re
import sqlite3
import struct
from dataclasses import dataclass, field
from pathlib import Path

from ..discovery import AccountRef
from ..util import HttpError, http_json, now, parse_iso, read_json
from .common import Account, Window

RPC_PATH = "/exa.language_server_pb.LanguageServerService/GetUserStatus"
RPC_BODY = {"metadata": {"ideName": "antigravity", "extensionName": "antigravity", "locale": "en"}}
PROCESS_HINTS = ("language_server", "antigravity", "agy")


# ── live: local language-server processes ─────────────────────────────────────

@dataclass
class Server:
    pid: int
    name: str
    csrf: str
    ports: list[int] = field(default_factory=list)


def _listening_inodes() -> dict[int, int]:
    """socket inode → port, for TCP sockets listening on loopback/any."""
    out = {}
    for table in ("/proc/net/tcp", "/proc/net/tcp6"):
        try:
            with open(table) as fh:
                next(fh)
                for line in fh:
                    parts = line.split()
                    if len(parts) > 9 and parts[3] == "0A":  # LISTEN
                        out[int(parts[9])] = int(parts[1].rsplit(":", 1)[1], 16)
        except (OSError, StopIteration, ValueError):
            continue
    return out


def _arg(args: list[str], name: str) -> str | None:
    for i, a in enumerate(args):
        if a.startswith(name + "="):
            return a.split("=", 1)[1]
        if a == name and i + 1 < len(args):
            return args[i + 1]
    return None


def _server_name(args: list[str]) -> str:
    exe = args[0]
    base = os.path.basename(exe)
    if base == "agy" or base.startswith("agy"):
        return "agy"
    low = exe.lower()
    if "antigravity-ide" in low or "antigravity ide" in low:
        return "antigravity-ide"
    return "antigravity"


def find_servers() -> list[Server]:
    uid = os.getuid()
    inodes = None
    servers = []
    for entry in os.scandir("/proc"):
        if not entry.name.isdigit():
            continue
        try:
            if entry.stat().st_uid != uid:
                continue
            with open(f"/proc/{entry.name}/cmdline", "rb") as fh:
                args = [a.decode(errors="replace") for a in fh.read().split(b"\0") if a]
        except OSError:
            continue
        if not args or not any(h in os.path.basename(args[0]).lower() for h in PROCESS_HINTS):
            continue
        csrf = _arg(args, "--csrf_token")
        if not csrf:
            continue
        if inodes is None:
            inodes = _listening_inodes()
        ports = []
        try:
            for fd in os.scandir(f"/proc/{entry.name}/fd"):
                target = os.readlink(fd.path)
                if target.startswith("socket:["):
                    port = inodes.get(int(target[8:-1]))
                    if port and port not in ports:
                        ports.append(port)
        except OSError:
            pass
        if ports:
            servers.append(Server(int(entry.name), _server_name(args), csrf, sorted(ports)))
    return servers


def query_server(server: Server) -> dict | None:
    headers = {"X-Codeium-Csrf-Token": server.csrf, "Connect-Protocol-Version": "1"}
    for port in server.ports:
        for scheme in ("https", "http"):
            try:
                data = http_json(f"{scheme}://127.0.0.1:{port}{RPC_PATH}", headers, RPC_BODY,
                                 timeout=3, local=True, insecure=(scheme == "https"))
            except HttpError:
                continue
            if isinstance(data, dict) and isinstance(data.get("userStatus"), dict):
                return data["userStatus"]
    return None


def _base_label(label: str) -> str:
    return re.sub(r"\s*\([^)]*\)\s*$", "", label).strip() or label


def group_models(models: list[tuple[str, float, float | None]]) -> list[Window]:
    """Merge variants sharing one quota: 'Gemini Flash (High)'/'(Low)' → 'Gemini Flash'."""
    windows: list[Window] = []
    seen = set()
    for label, remaining, reset in models:
        base = _base_label(label)
        key = (base, round(remaining, 4), reset)
        if key in seen:
            continue
        seen.add(key)
        windows.append(Window(base, round((1 - remaining) * 100, 1), reset))
    return sorted(windows, key=lambda w: w.label.lower())


def parse_user_status(us: dict) -> tuple[str, str, str, list[Window]]:
    """→ (email, display name, plan, windows) from the JSON GetUserStatus reply."""
    plan = ((us.get("userTier") or {}).get("name")
            or ((us.get("planStatus") or {}).get("planInfo") or {}).get("planName") or "")
    models = []
    for m in (us.get("cascadeModelConfigData") or {}).get("clientModelConfigs") or []:
        quota = m.get("quotaInfo")
        if m.get("disabled") or not isinstance(quota, dict) or not quota:
            continue
        # proto3 JSON omits zero values: a reset time without a fraction means 0 left
        remaining = float(quota.get("remainingFraction", 0.0))
        models.append((m.get("label") or "?", remaining, parse_iso(quota.get("resetTime"))))
    return us.get("email", ""), us.get("name", ""), plan, group_models(models)


# ── offline: cached protobuf in state.vscdb ───────────────────────────────────

def _varint(buf: bytes, i: int) -> tuple[int, int]:
    result = shift = 0
    while True:
        b = buf[i]
        i += 1
        result |= (b & 0x7F) << shift
        shift += 7
        if b < 0x80:
            return result, i


def pb_fields(buf: bytes) -> list[tuple[int, int, object]]:
    """Decode one protobuf message level into (field, wire type, value)."""
    out, i = [], 0
    while i < len(buf):
        key, i = _varint(buf, i)
        num, wt = key >> 3, key & 7
        if wt == 0:
            val, i = _varint(buf, i)
        elif wt == 1:
            val = struct.unpack_from("<d", buf, i)[0]
            i += 8
        elif wt == 5:
            val = struct.unpack_from("<f", buf, i)[0]
            i += 4
        elif wt == 2:
            length, i = _varint(buf, i)
            val = buf[i:i + length]
            i += length
        else:
            raise ValueError(f"unsupported wire type {wt}")
        if num == 0 or i > len(buf):
            raise ValueError("truncated message")
        out.append((num, wt, val))
    return out


def _first(fields, num, wt=None):
    for n, w, v in fields:
        if n == num and (wt is None or w == wt):
            return v
    return None


def decode_cached_status(raw: bytes) -> tuple[str, str, str, list[Window]]:
    """Field numbers observed in Antigravity's UserStatus message:
    3 name · 7 email · 13.1.2 plan name · 33.1[] model configs with
    1 label and 15 quota {1 remaining fraction (float), 2 reset Timestamp}."""
    top = pb_fields(raw)

    def text(v):
        return v.decode("utf-8", errors="replace") if isinstance(v, bytes) else ""

    name, email = text(_first(top, 3, 2)), text(_first(top, 7, 2))
    plan = ""
    plan_status = _first(top, 13, 2)
    if plan_status:
        info = _first(pb_fields(plan_status), 1, 2)
        if info:
            plan = text(_first(pb_fields(info), 2, 2))
    models = []
    cfg = _first(top, 33, 2)
    for n, w, v in pb_fields(cfg) if cfg else []:
        if n != 1 or w != 2:
            continue
        m = pb_fields(v)
        quota = _first(m, 15, 2)
        if not quota:
            continue
        q = pb_fields(quota)
        remaining = _first(q, 1, 5) or 0.0
        ts = _first(q, 2, 2)
        reset = _first(pb_fields(ts), 1, 0) if ts else None
        models.append((text(_first(m, 1, 2)) or "?", float(remaining), reset))
    return email, name, plan, group_models(models)


def read_state_db(path: Path) -> dict | None:
    db = path / "User" / "globalStorage" / "state.vscdb"
    if not db.exists():
        return None
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=1)
        try:
            row = con.execute("SELECT value FROM ItemTable WHERE key = 'antigravityAuthStatus'").fetchone()
        finally:
            con.close()
    except sqlite3.Error:
        return None
    if not row:
        return None
    value = row[0].decode("utf-8", "replace") if isinstance(row[0], bytes) else row[0]
    try:
        data = json.loads(value)
    except (TypeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    data["_mtime"] = db.stat().st_mtime
    return data


# ── public API ────────────────────────────────────────────────────────────────

def fetch_all(refs: list[AccountRef], tr, live: bool = True) -> list[Account]:
    """One Account per Google account (quotas are per account, not per app)."""
    accounts: dict[str, Account] = {}

    if live:
        for server in find_servers():
            us = query_server(server)
            if not us:
                continue
            email, _, plan, windows = parse_user_status(us)
            if not email or email in accounts:
                continue
            accounts[email] = Account("antigravity", server.name, email=email, plan=plan,
                                      windows=windows, source="live", fetched_at=now(),
                                      key=f"antigravity:{email}")

    for ref in refs:
        if ref.path.name == ".gemini":
            email = (read_json(ref.path / "google_accounts.json") or {}).get("active") or ""
            if email and email not in accounts:
                accounts[email] = Account("antigravity", ref.name, path=str(ref.path), email=email,
                                          key=f"antigravity:{email}", notes=[tr("open_app")])
            continue
        data = read_state_db(ref.path)
        if not data:
            continue
        email, plan, windows = data.get("email", ""), "", []
        blob = data.get("userStatusProtoBinaryBase64")
        if blob:
            try:
                email2, _, plan, windows = decode_cached_status(base64.b64decode(blob))
                email = email or email2
            except (ValueError, IndexError, struct.error):
                pass
        if not email:
            continue
        existing = accounts.get(email)
        if existing and existing.source == "live":
            continue
        if existing and not windows:
            continue
        accounts[email] = Account("antigravity", ref.name, path=str(ref.path), email=email,
                                  plan=plan, windows=windows, source="cache" if windows else "none",
                                  fetched_at=data["_mtime"], key=f"antigravity:{email}",
                                  notes=[] if windows else [tr("open_app")])
    return list(accounts.values())
