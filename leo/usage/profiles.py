"""Perfiles por metadatos y carpetas. Nunca abre los archivos de autenticación."""
from dataclasses import dataclass
import os
from pathlib import Path
import tomllib

from .storage import read


@dataclass
class Profile:
    provider: str
    name: str
    path: Path

    @property
    def key(self):
        return f"{self.provider}:{self.path}"


def discover(h=None):
    h = h or Path.home()
    config = Path(os.environ.get("XDG_CONFIG_HOME", h / ".config"))
    found = {}

    def add(provider, path, name=""):
        if provider not in {"codex", "claude", "antigravity"} or not isinstance(path, str):
            return
        p = Path(path).expanduser()
        if not p.is_absolute() or not p.is_dir():
            return
        p = p.resolve()
        item = Profile(provider, str(name or p.name.lstrip("."))[:100], p)
        found[item.key] = item

    for provider, variable in (("codex", "CODEX_HOME"), ("claude", "CLAUDE_CONFIG_DIR")):
        for p in sorted(h.glob(f".{provider}*")):
            # A directory is enough; neither parse auth files nor inspect shell aliases.
            if p.is_dir():
                add(provider, str(p))
        if os.environ.get(variable):
            add(provider, os.environ[variable])
    for p in sorted(config.glob("Antigravity*")):
        add("antigravity", str(p))
    for p in sorted(h.glob(".antigravity*")):
        add("antigravity", str(p))
    try:
        raw = (config / "ai-usage-widget/config.toml").read_bytes()
        cfg = tomllib.loads(raw.decode()) if len(raw) < 1024 * 1024 else {}
    except (OSError, ValueError):
        cfg = {}
    extra = cfg.get("accounts", {}).get("extra", [])
    for item in extra if isinstance(extra, list) else []:
        if isinstance(item, dict):
            add(item.get("provider"), item.get("path"), item.get("name"))
    for item in read(config / "leo/ai-accounts.json").get("profiles", []):
        if isinstance(item, dict):
            add(item.get("provider"), item.get("path"), item.get("name"))
    return sorted(found.values(), key=lambda item: (item.provider, item.name))
