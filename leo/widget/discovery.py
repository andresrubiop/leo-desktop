"""Find every Claude Code / Codex / Antigravity profile on this machine.

Sources, in priority order (the first name found for a folder wins):
  1. ``[[accounts.extra]]`` entries from the config file
  2. the default folders (~/.claude, ~/.codex, ~/.config/Antigravity*, ~/.gemini)
  3. shell aliases and functions that set CODEX_HOME / CLAUDE_CONFIG_DIR
     (or launch Antigravity with --user-data-dir)
  4. small wrapper scripts in ~/.local/bin and ~/bin doing the same
  5. leftover folders such as ~/.codex-work or ~/.claude-personal
"""

from __future__ import annotations

import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

from .util import expand_path, home

ENV_PROVIDER = {"CODEX_HOME": "codex", "CLAUDE_CONFIG_DIR": "claude"}

RC_FILES = (
    ".bashrc", ".bash_aliases", ".bash_profile", ".bash_login", ".profile",
    ".zshrc", ".zshenv", ".zprofile", ".zsh_aliases", ".aliases",
    ".config/fish/config.fish",
)
RC_GLOBS = (
    ".bashrc.d/*", ".config/fish/conf.d/*.fish", ".config/fish/functions/*.fish",
    ".oh-my-zsh/custom/*.zsh", ".config/zsh/*.zsh",
)
SCRIPT_DIRS = (".local/bin", "bin")

_NAME = r"[A-Za-z0-9_.@+:-]+"
_VALUE = r"""("[^"]*"|'[^']*'|[^\s;&|)'"`]+)"""
ALIAS_RE = re.compile(rf"^\s*alias\s+(?:-\w+\s+)*({_NAME})\s*(?:=|\s)\s*(.+?)\s*$")
FUNC_RE = re.compile(rf"^\s*(?:function\s+({_NAME})|({_NAME})\s*\(\s*\))")
ENV_RE = re.compile(rf"""(?:^|[\s;&|('"`])(?:export\s+)?(CODEX_HOME|CLAUDE_CONFIG_DIR)={_VALUE}""")
FISH_SET_RE = re.compile(rf"\bset\s+(?:-[a-zA-Z]+\s+)*(CODEX_HOME|CLAUDE_CONFIG_DIR)\s+{_VALUE}")
AGY_DIR_RE = re.compile(rf"--user-data-dir(?:=|\s+){_VALUE}")


@dataclass
class AccountRef:
    provider: str          # "codex" | "claude" | "antigravity"
    name: str              # what the user types / sees (alias, script, command)
    path: Path             # CODEX_HOME, CLAUDE_CONFIG_DIR or Antigravity data dir
    source: str            # where we found it (for --list-accounts)
    default: bool = False  # the tool's default profile
    kind: str = ""         # default | alias | function | script | export | folder | config
    origin: Path | None = None  # the shell file or script that defines it

    @property
    def runnable(self) -> bool:
        """There is a command the user can type to start this profile."""
        return self.kind not in ("folder", "config")

    @property
    def key(self) -> str:
        return f"{self.provider}:{self.path}"


def _read_text(path: Path, limit: int = 512 * 1024) -> str | None:
    try:
        if not path.is_file() or path.stat().st_size > limit:
            return None
        with open(path, "rb") as fh:
            data = fh.read()
        if b"\0" in data[:4096]:
            return None
        return data.decode("utf-8", errors="replace")
    except OSError:
        return None


def _short(path: Path) -> str:
    try:
        return "~/" + str(path.relative_to(home()))
    except ValueError:
        return str(path)


def _name_from_path(provider: str, path: Path) -> str:
    base = path.name.lstrip(".")
    return base if base.startswith(provider) else f"{provider}-{base}"


def parse_shell_text(text: str, origin: Path, script_name: str | None = None) -> list[AccountRef]:
    """Extract profile references from one shell file.

    ``script_name`` is set for executable wrappers: the script itself is the
    command that launches the profile."""
    found: list[AccountRef] = []
    current_func: str | None = None
    where = _short(origin)
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped in ("}", "end"):
            current_func = None
        alias = ALIAS_RE.match(line)
        func = None if alias else FUNC_RE.match(line)
        if func:
            current_func = func.group(1) or func.group(2)

        if alias:
            owner, kind = alias.group(1), "alias"
        elif current_func:
            owner, kind = current_func, "function"
        elif script_name:
            owner, kind = script_name, "script"
        else:
            owner, kind = None, "export"

        matches = [(m.group(1), m.group(2)) for m in ENV_RE.finditer(line)]
        matches += [(m.group(1), m.group(2)) for m in FISH_SET_RE.finditer(line)]
        for var, raw in matches:
            path = expand_path(raw)
            if not path:
                continue
            provider = ENV_PROVIDER[var]
            if owner is None:
                # top-level `export CODEX_HOME=...`: moves the default profile
                found.append(AccountRef(provider, provider, path, f"export in {where}", True, "export", origin))
            else:
                found.append(AccountRef(provider, owner, path, f"{kind} in {where}", False, kind, origin))

        if "antigravity" in line.lower() or re.search(r"\bagy\b", line):
            for m in AGY_DIR_RE.finditer(line):
                path = expand_path(m.group(1))
                if path and path.is_dir():
                    name = owner or _name_from_path("antigravity", path)
                    found.append(AccountRef("antigravity", name, path, f"{kind} in {where}", False, kind, origin))
    return found


def _rc_files(h: Path) -> list[Path]:
    files = [h / f for f in RC_FILES]
    for pattern in RC_GLOBS:
        files.extend(sorted(h.glob(pattern)))
    return [f for f in files if f.is_file()]


def _script_files(h: Path) -> list[Path]:
    out = []
    for d in SCRIPT_DIRS:
        folder = h / d
        if not folder.is_dir():
            continue
        for f in sorted(folder.iterdir()):
            try:
                if f.is_file() and os.access(f, os.X_OK) and f.stat().st_size < 64 * 1024:
                    with open(f, "rb") as fh:
                        if fh.read(2) == b"#!":
                            out.append(f)
            except OSError:
                continue
    return out


def default_refs(h: Path, env: dict) -> list[AccountRef]:
    refs = []
    codex = expand_path(env["CODEX_HOME"]) if env.get("CODEX_HOME") else h / ".codex"
    if codex and (codex.exists() or shutil.which("codex")):
        refs.append(AccountRef("codex", "codex", codex, "default", True, "default"))
    claude = expand_path(env["CLAUDE_CONFIG_DIR"]) if env.get("CLAUDE_CONFIG_DIR") else h / ".claude"
    if claude and (claude.exists() or (h / ".claude.json").exists() or shutil.which("claude")):
        refs.append(AccountRef("claude", "claude", claude, "default", True, "default"))
    cfg_root = Path(env.get("XDG_CONFIG_HOME") or h / ".config")
    if cfg_root.is_dir():
        for d in sorted(cfg_root.glob("Antigravity*")):
            if (d / "User" / "globalStorage" / "state.vscdb").exists():
                refs.append(AccountRef("antigravity", d.name.lower().replace(" ", "-"), d, "default", True, "default"))
    gemini = h / ".gemini"
    if (gemini / "google_accounts.json").exists() and (
            (gemini / "antigravity").is_dir() or shutil.which("agy")):
        refs.append(AccountRef("antigravity", "agy", gemini, "default", True, "default"))
    return refs


def leftover_refs(h: Path) -> list[AccountRef]:
    refs = []
    for d in sorted(h.glob(".codex*")):
        if d.is_dir() and (d / "auth.json").exists():
            refs.append(AccountRef("codex", d.name.lstrip("."), d, f"folder {_short(d)}", False, "folder"))
    for d in sorted(h.glob(".claude*")):
        # a login, or at least a config written by Claude Code (started, not signed in yet)
        if d.is_dir() and ((d / ".credentials.json").exists() or (d / ".claude.json").exists()):
            refs.append(AccountRef("claude", d.name.lstrip("."), d, f"folder {_short(d)}", False, "folder"))
    return refs


def discover(cfg: dict | None = None, h: Path | None = None, env: dict | None = None,
             include_hidden: bool = False) -> list[AccountRef]:
    h = h or home()
    env = dict(os.environ) if env is None else env
    acc_cfg = (cfg or {}).get("accounts", {})
    candidates: list[AccountRef] = []

    for extra in acc_cfg.get("extra", []):
        path = expand_path(str(extra.get("path", "")))
        if path:
            name = str(extra.get("name") or _name_from_path(extra["provider"], path))
            candidates.append(AccountRef(extra["provider"], name, path, "config file", False, "config"))

    if acc_cfg.get("auto_discover", True):
        shell_refs: list[AccountRef] = []
        for f in _rc_files(h):
            text = _read_text(f)
            if text:
                shell_refs.extend(parse_shell_text(text, f))
        # an `export CODEX_HOME=...` in a shell file relocates the default profile
        overrides = {r.provider: r for r in shell_refs if r.default}
        defaults = []
        for ref in default_refs(h, env):
            if ref.provider in overrides and not env.get(
                    "CODEX_HOME" if ref.provider == "codex" else "CLAUDE_CONFIG_DIR"):
                ref = overrides[ref.provider]
            defaults.append(ref)
        candidates += defaults
        candidates += [r for r in shell_refs if not r.default]
        for f in _script_files(h):
            text = _read_text(f, 64 * 1024)
            if text:
                candidates.extend(parse_shell_text(text, f, script_name=f.name))
        candidates += leftover_refs(h)

    hidden = set() if include_hidden else set(acc_cfg.get("hide", []))
    seen: dict[str, AccountRef] = {}
    for ref in candidates:
        try:
            real = Path(os.path.realpath(ref.path))
        except OSError:
            real = ref.path
        key = f"{ref.provider}:{real}"
        if key in seen or ref.name in hidden:
            continue
        seen[key] = ref
    return list(seen.values())
