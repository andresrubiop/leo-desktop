"""Proyecto en el que trabaja el usuario (LEO_SPEC F6.4, con Leo atento).

Del título de la ventana que Leo observa (en la que vive, o la activa mientras pasea) se deduce una carpeta con
git dentro de HOME: una ruta en el título (terminales: «~/Tesis», «ana@pc: ~/Tesis/codigo») o el nombre de la
carpeta abierta en un editor («train.py — Tesis — Visual Studio Code»). La tarjeta de contexto lleva rama,
número de cambios (`git status --porcelain`, argv fijo) y el siguiente paso de su `docs/progress.md` si existe.
Nunca carpetas ocultas o de configuración; los títulos solo viven en memoria.
"""
import os
from pathlib import Path
import re
import shutil
import subprocess
import time

from .config import PRIVATE

TAIL = r"(?:/[^\s:'\"»)]*)?"
SEPARATOR = re.compile(r"\s+[—–-]\s+")
NEXT = re.compile(r"(?i)^\W*(?:próximos? pasos?|siguiente|pendiente)\b.*")
SEARCH_DEPTH = 3
CARD_TTL = 30


def _allowed(path, home):
    try:
        relative = path.resolve().relative_to(home)
    except (OSError, ValueError):
        return False
    return bool(relative.parts) and not any(part.startswith(".") or part in PRIVATE for part in relative.parts)


def repository(path, home):
    """The git repository containing `path` (walking up to HOME), or None."""
    path = Path(path)
    for candidate in (path, *path.parents):
        if candidate == home or home not in candidate.parents:
            return None
        if (candidate / ".git").exists() and _allowed(candidate, home):
            return candidate
    return None


def hints(title, home=""):
    """(paths, folder names) mentioned by a window title: «~/…», the real HOME or /home/<usuario>/…"""
    starts = ["~", r"/home/[^/\s:]+"] + ([re.escape(str(home))] if home else [])
    pattern = re.compile(r"(?:" + "|".join(starts) + r")" + TAIL)
    paths = [match.group(0).rstrip("/.,;") for match in pattern.finditer(title or "")]
    parts = [part.strip() for part in SEPARATOR.split(title or "") if part.strip()]
    names = [parts[-2]] if len(parts) >= 3 else parts[:1] if len(parts) == 2 else []
    return paths, [name for name in names if re.fullmatch(r"[\w .@+-]{1,80}", name)]


class Projects:
    def __init__(self, home=None, run=subprocess.run, clock=time.monotonic):
        self.home = Path(home or Path.home()).resolve()
        self.run, self.clock = run, clock
        self.index, self.indexed = {}, None
        self.cards = {}

    def _by_name(self, name):
        """Repositories under HOME (depth ≤ 3, no hidden folders) by folder name; rebuilt every 10 min."""
        if self.indexed is None or self.clock() - self.indexed > 600:
            self.index, self.indexed = {}, self.clock()
            for root, dirs, _files in os.walk(self.home):
                depth = len(Path(root).relative_to(self.home).parts)
                dirs[:] = [d for d in dirs if not d.startswith(".") and d not in PRIVATE and d not in {"node_modules", "__pycache__"}]
                if ".git" in os.listdir(root) and depth:
                    self.index.setdefault(Path(root).name.lower(), []).append(Path(root))
                if depth >= SEARCH_DEPTH:
                    dirs[:] = []
        found = self.index.get(name.lower(), [])
        return found[0] if len(found) == 1 else None  # Ambiguous names are not guessed.

    def resolve(self, title):
        paths, names = hints(title, self.home)
        for text in paths:
            path = Path(os.path.expanduser(text)) if text.startswith("~") else Path(text)
            path = path.with_name(path.name.split("…")[0]) if "…" in path.name else path
            while not path.exists() and path != self.home and self.home in path.parents:
                path = path.parent
            found = repository(path, self.home)
            if found:
                return found
        for name in names:
            found = self._by_name(name)
            if found:
                return found
        return None

    def card(self, path):
        cached = self.cards.get(path)
        if cached and self.clock() - cached[0] < CARD_TTL:
            return cached[1]
        card = {"name": path.name, "path": str(path), "branch": None, "changes": None, "next": None}
        git = shutil.which("git", path="/usr/bin:/bin")
        if git:
            try:
                result = self.run([git, "-C", str(path), "status", "--porcelain=v1", "--branch"], capture_output=True,
                                  text=True, timeout=5, env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "GIT_OPTIONAL_LOCKS": "0"})
                if result.returncode == 0:
                    lines = result.stdout.splitlines()
                    head = lines[0][3:] if lines and lines[0].startswith("## ") else ""
                    head = head.replace("No commits yet on ", "")
                    card["branch"] = head.split("...")[0].strip() or None
                    card["changes"] = len(lines) - (1 if head else 0)
            except (OSError, subprocess.TimeoutExpired):
                pass
        progress = path / "docs" / "progress.md"
        try:
            if progress.is_file() and progress.stat().st_size < 2_000_000:
                lines = progress.read_text(errors="replace").splitlines()
                last = max((i for i, line in enumerate(lines) if line.startswith("## ")), default=-1)
                for line in lines[last + 1:]:
                    if NEXT.match(line):
                        card["next"] = " ".join(line.split())[:160]
                        break
        except OSError:
            pass
        self.cards[path] = (self.clock(), card)
        return card
