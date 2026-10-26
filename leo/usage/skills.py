"""Agent Skills: search them (skills.sh or any GitHub repo), install them into
each AI tool, list and remove them.

Installs exactly like the official ``npx skills`` CLI, so both tools see the
same state: a canonical copy in ``~/.agents/skills/<name>`` (which is also
Codex's skills folder), symlinks from the other agents' folders to it, and an
entry in ``~/.agents/.skill-lock.json``. Nothing is ever executed: a skill is
just files (SKILL.md plus optional references and scripts) copied to disk."""

from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
import urllib.parse
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from .profiles import discover
from .net import HttpError, http_get, http_json

def home():
    return Path.home()

SEARCH_URL = "https://skills.sh/api/search"
DOWNLOAD_URL = "https://skills.sh/api/download/{owner}/{repo}/{skill}"
GITHUB_TREE_URL = "https://api.github.com/repos/{source}/git/trees/HEAD?recursive=1"
GITHUB_RAW_URL = "https://raw.githubusercontent.com/{source}/HEAD/{path}"
SEARCH_LIMIT = 30
MAX_FILES = 500
MAX_BYTES = 20 * 1024 * 1024
LOCK_VERSION = 3
SKIP_DIRS = {".git", "__pycache__", "__pypackages__", "node_modules"}
REPO_RE = re.compile(r"^(?:https?://github\.com/)?([A-Za-z0-9][A-Za-z0-9-]{0,38})/([A-Za-z0-9._-]+?)"
                     r"(?:\.git)?(?:/tree/[^/]+(/.*)?)?/?$")


# ── data model ────────────────────────────────────────────────────────────────

@dataclass
class Target:
    """A folder an AI tool reads skills from."""
    key: str                 # stable id, e.g. "claude-code:/home/u/.claude-work"
    label: str               # shown in the UI: "claude-work", "codex", "antigravity"…
    path: Path               # the skills folder itself
    agent: str               # claude-code | codex | antigravity | antigravity-cli | gemini-cli | …
    universal: bool = False  # ~/.agents/skills: the canonical store
    note: str = ""           # extra detail for tooltips (e.g. which Codex profiles share it)
    readonly: bool = False


@dataclass
class SearchResult:
    id: str                  # "anthropics/skills/pdf"
    name: str
    source: str              # "owner/repo"
    skill_id: str            # folder / slug inside the repo
    installs: int = 0
    provider: str = "skills.sh"   # "skills.sh" | "github"
    folder: str = ""         # folder inside the repo (GitHub listings)

    @property
    def url(self) -> str:
        if self.provider == "skills.sh":
            return f"https://skills.sh/{self.id}"
        path = f"/tree/HEAD/{self.folder}" if self.folder else ""
        return f"https://github.com/{self.source}{path}"


@dataclass
class SkillPackage:
    result: SearchResult
    name: str                # install folder name (sanitized like the official CLI)
    description: str
    files: list[tuple[str, bytes]]
    skill_path: str = ""     # SKILL.md path inside the repo (lets `npx skills update` work)
    folder_hash: str = ""    # git tree sha of the skill folder


@dataclass
class InstalledSkill:
    name: str
    description: str = ""
    paths: dict[str, Path] = field(default_factory=dict)   # target key → folder
    source: str = ""         # "owner/repo" from the lock file
    readonly: bool = False   # synced from claude.ai: managed by Claude, not by us

    @property
    def targets(self) -> list[str]:
        return list(self.paths)


class SkillError(Exception):
    pass


# ── helpers ───────────────────────────────────────────────────────────────────

def sanitize_name(name: str) -> str:
    """Same rule as the official CLI (so both pick the same folder name)."""
    value = re.sub(r"[^a-z0-9._]+", "-", name.lower())
    value = re.sub(r"^[.\-]+|[.\-]+$", "", value)[:255]
    return value or "unnamed-skill"


def parse_frontmatter(text: str) -> dict[str, str]:
    """Minimal YAML front matter reader: top-level ``key: value`` pairs, with
    quoted strings and folded/literal (``>``/``|``) blocks."""
    lines = text.lstrip("﻿").splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    out: dict[str, str] = {}
    i = 1
    while i < len(lines) and lines[i].strip() != "---":
        m = re.match(r"^([A-Za-z0-9_-]+):\s*(.*)$", lines[i])
        i += 1
        if not m:
            continue
        key, value = m.group(1), m.group(2).strip()
        if value[:1] in (">", "|"):
            block = []
            while i < len(lines) and (lines[i].startswith((" ", "\t")) or not lines[i].strip()):
                if lines[i].strip() == "---":
                    break
                block.append(lines[i].strip())
                i += 1
            value = (" " if value[0] == ">" else "\n").join(x for x in block if x)
        elif len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        out[key] = value
    return out


def read_skill_meta(folder: Path) -> dict[str, str]:
    for name in ("SKILL.md", "skill.md", "Skill.md"):
        f = folder / name
        try:
            with open(f, encoding="utf-8", errors="replace") as fh:
                return parse_frontmatter(fh.read(16384))
        except OSError:
            continue
    return {}


def has_skill_md(folder: Path) -> bool:
    return any((folder / n).is_file() for n in ("SKILL.md", "skill.md", "Skill.md"))


def safe_relpath(path: str) -> str | None:
    """Reject absolute paths, '..' and other ways out of the skill folder."""
    if not path or "\\" in path or "\0" in path:
        return None
    p = PurePosixPath(path)
    if p.is_absolute() or any(part in ("..", "") for part in p.parts):
        return None
    if any(part in SKIP_DIRS for part in p.parts[:-1]):
        return None
    return str(p)


def _github_headers() -> dict:
    headers = {"Accept": "application/vnd.github+json"}
    return headers


def parse_repo(text: str) -> tuple[str, str] | None:
    """'owner/repo', 'https://github.com/owner/repo[/tree/branch/sub/dir]' → (source, subdir)."""
    m = REPO_RE.match(text.strip())
    if not m:
        return None
    sub = (m.group(3) or "").strip("/")
    return f"{m.group(1)}/{m.group(2)}", sub


def format_installs(n: int) -> str:
    if not n:
        return ""
    if n >= 1_000_000:
        return f"{n / 1e6:.1f}M".replace(".0M", "M")
    if n >= 1000:
        return f"{n / 1e3:.1f}K".replace(".0K", "K")
    return str(n)


# ── targets ───────────────────────────────────────────────────────────────────

# (agent id, label, root that must exist, skills folder relative to home)
OTHER_AGENTS = (
    ("antigravity", "antigravity", ".gemini/antigravity", ".gemini/antigravity/skills"),
    ("antigravity-cli", "agy", ".gemini/antigravity-cli", ".gemini/antigravity-cli/skills"),
    ("gemini-cli", "gemini", ".gemini", ".gemini/skills"),
    ("cursor", "cursor", ".cursor", ".cursor/skills"),
    ("opencode", "opencode", ".config/opencode", ".config/opencode/skills"),
    ("github-copilot", "copilot", ".copilot", ".copilot/skills"),
)


def detect_targets(cfg: dict | None = None, h: Path | None = None) -> list[Target]:
    h = h or home()
    refs = discover(h=h)
    targets: list[Target] = []
    for ref in refs:
        if ref.provider == "codex":
            targets.append(Target(f"codex-profile:{ref.path}", ref.name + " · propias", ref.path / "skills", "codex"))
            system = ref.path / "skills/.system"
            if system.is_dir():
                targets.append(Target(f"system:{ref.path}", ref.name + " · sistema", system, "codex", readonly=True))
            for folder in sorted((ref.path / "plugins/cache").glob("*/*/*/skills"))[:100]:
                targets.append(Target(f"plugin:{folder}", folder.parent.parent.name + " · plugin", folder, "plugin", readonly=True))
        if ref.provider == "claude":
            targets.append(Target(f"claude-code:{ref.path}", ref.name, ref.path / "skills", "claude-code"))
    codex_names = [r.name for r in refs if r.provider == "codex"]
    universal = h / ".agents" / "skills"
    if codex_names or universal.is_dir():
        # one folder for every Codex profile (it does not depend on CODEX_HOME)
        targets.append(Target("codex", "codex", universal, "codex", True, ", ".join(codex_names)))
    for agent, label, root, folder in OTHER_AGENTS:
        if (h / root).is_dir():
            targets.append(Target(agent, label, h / folder, agent))
    return targets


# ── lock file (shared with the official CLI) ──────────────────────────────────

def lock_path(h: Path | None = None) -> Path:
    state = os.environ.get("XDG_STATE_HOME")
    if state:
        return Path(state) / "skills" / ".skill-lock.json"
    return (h or home()) / ".agents" / ".skill-lock.json"


def read_lock(h: Path | None = None) -> dict:
    try:
        data = json.loads(lock_path(h).read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("skills"), dict):
            return data
    except (OSError, ValueError):
        pass
    return {"version": LOCK_VERSION, "skills": {}, "dismissed": {}}


def _write_lock(lock: dict, h: Path | None = None) -> None:
    if lock.get("version") != LOCK_VERSION:
        return  # a format we don't know: leave it to the official CLI
    path = lock_path(h)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(lock, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _lock_add(pkg: SkillPackage, h: Path | None = None) -> None:
    lock = read_lock(h)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
    old = lock["skills"].get(pkg.name) or {}
    entry = {"source": pkg.result.source, "sourceType": "github",
             "sourceUrl": f"https://github.com/{pkg.result.source}.git"}
    if pkg.skill_path:
        entry["skillPath"] = pkg.skill_path
    entry["skillFolderHash"] = pkg.folder_hash
    entry["installedAt"] = old.get("installedAt", now)
    entry["updatedAt"] = now
    lock["skills"][pkg.name] = entry
    _write_lock(lock, h)


def _lock_remove(name: str, h: Path | None = None) -> None:
    lock = read_lock(h)
    if lock["skills"].pop(name, None) is not None:
        _write_lock(lock, h)


# ── search ────────────────────────────────────────────────────────────────────

def search(query: str) -> list[SearchResult]:
    """skills.sh search, or the skills inside a GitHub repo if given 'owner/repo'."""
    query = query.strip()
    repo = parse_repo(query) if "/" in query else None
    if repo:
        return github_list(*repo)
    if len(query) < 2:
        return []
    params = urllib.parse.urlencode({"q": query, "limit": SEARCH_LIMIT})
    try:
        data = http_json(f"{SEARCH_URL}?{params}", timeout=10)
    except HttpError as exc:
        raise SkillError(str(exc)) from None
    results = []
    for s in data.get("skills") or []:
        if not isinstance(s, dict) or not s.get("source"):
            continue
        skill_id = s.get("skillId") or str(s.get("id", "")).rsplit("/", 1)[-1]
        results.append(SearchResult(str(s.get("id") or f"{s['source']}/{skill_id}"), str(s.get("name") or skill_id),
                                    str(s["source"]), str(skill_id), int(s.get("installs") or 0)))
    return sorted(results, key=lambda r: -r.installs)


def _github_tree(source: str) -> dict:
    try:
        return http_json(GITHUB_TREE_URL.format(source=source), _github_headers(), timeout=15)
    except HttpError as exc:
        if exc.status == 404:
            raise SkillError(f"{source}: repo not found") from None
        if exc.status in (403, 429):
            raise SkillError("Límite de GitHub; vuelve a intentar más tarde") from None
        raise SkillError(str(exc)) from None


def _skill_folders(tree: dict) -> list[str]:
    folders = []
    for entry in tree.get("tree") or []:
        path = entry.get("path", "")
        if entry.get("type") == "blob" and PurePosixPath(path).name.lower() == "skill.md":
            parts = PurePosixPath(path).parts
            if not any(p in SKIP_DIRS for p in parts):
                folders.append(str(PurePosixPath(path).parent) if len(parts) > 1 else "")
    return folders


def github_list(source: str, subdir: str = "") -> list[SearchResult]:
    tree = _github_tree(source)
    repo_name = source.split("/")[1]
    results = []
    for folder in _skill_folders(tree):
        if subdir and not (folder == subdir or folder.startswith(subdir + "/")):
            continue
        skill_id = PurePosixPath(folder).name if folder else repo_name
        results.append(SearchResult(f"{source}/{skill_id}", skill_id, source, skill_id,
                                    provider="github", folder=folder))
    return sorted(results, key=lambda r: r.name)


# ── fetching a skill ──────────────────────────────────────────────────────────

def _check_files(files: list[tuple[str, bytes]]) -> list[tuple[str, bytes]]:
    clean, total = [], 0
    for path, data in files:
        rel = safe_relpath(path)
        if rel is None or rel == ".":
            raise SkillError("Ruta de archivo no permitida")
        if PurePosixPath(rel).name == "metadata.json":
            continue
        total += len(data)
        clean.append((rel, data))
    if len(clean) > MAX_FILES or total > MAX_BYTES:
        raise SkillError("skill too large")
    if not any(PurePosixPath(p).name.lower() == "skill.md" and "/" not in p for p, _ in clean):
        raise SkillError("no SKILL.md in this skill")
    return clean


def _package(result: SearchResult, files: list[tuple[str, bytes]], skill_path="", folder_hash="") -> SkillPackage:
    files = _check_files(files)
    skill_md = next(d for p, d in files if p.lower() == "skill.md")
    meta = parse_frontmatter(skill_md.decode("utf-8", errors="replace"))
    name = sanitize_name(meta.get("name") or result.name or result.skill_id)
    return SkillPackage(result, name, meta.get("description", ""), files, skill_path, folder_hash)


def fetch(result: SearchResult) -> SkillPackage:
    """Download a skill's files (nothing is written to disk)."""
    if result.provider == "github":
        return _fetch_github(result)
    owner, repo = result.source.split("/", 1)
    url = DOWNLOAD_URL.format(owner=urllib.parse.quote(owner), repo=urllib.parse.quote(repo),
                              skill=urllib.parse.quote(result.skill_id))
    try:
        data = http_json(url, timeout=20, max_bytes=MAX_BYTES * 2)
    except HttpError as exc:
        if exc.status == 404:  # not mirrored by skills.sh: read the repo itself
            return _fetch_github(_locate_in_repo(result))
        raise SkillError(str(exc)) from None
    files = []
    for f in data.get("files") or []:
        if isinstance(f, dict) and isinstance(f.get("path"), str) and isinstance(f.get("contents"), str):
            files.append((f["path"], f["contents"].encode("utf-8")))
    return _package(result, files)


def _locate_in_repo(result: SearchResult, tree: dict | None = None) -> SearchResult:
    tree = tree or _github_tree(result.source)
    folders = _skill_folders(tree)
    matches = [f for f in folders if PurePosixPath(f).name.lower() == result.skill_id.lower()]
    if not matches and len(folders) == 1:
        matches = folders
    if not matches:
        raise SkillError(f"{result.skill_id} not found in {result.source}")
    folder = min(matches, key=len)
    return SearchResult(result.id, result.name, result.source, result.skill_id, result.installs, "github", folder)


def _fetch_github(result: SearchResult) -> SkillPackage:
    tree = _github_tree(result.source)
    folder = result.folder
    prefix = f"{folder}/" if folder else ""
    blobs = [e for e in tree.get("tree") or [] if e.get("type") == "blob" and e.get("path", "").startswith(prefix)]
    if folder == "":  # skill at the repo root: don't pull in nested skills
        nested = {f + "/" for f in _skill_folders(tree) if f}
        blobs = [e for e in blobs if not any(e["path"].startswith(n) for n in nested)]
    if len(blobs) > MAX_FILES:
        raise SkillError("skill too large")
    files = []
    total = 0
    for e in blobs:
        rel = e["path"][len(prefix):]
        if safe_relpath(rel) is None:
            continue
        url = GITHUB_RAW_URL.format(source=result.source, path=urllib.parse.quote(e["path"]))
        try:
            raw = http_get(url, timeout=20, max_bytes=MAX_BYTES - total)
            total += len(raw)
            files.append((rel, raw))
        except HttpError as exc:
            raise SkillError(f"{rel}: {exc}") from None
    folder_hash = next((e.get("sha", "") for e in tree.get("tree") or []
                        if e.get("type") == "tree" and e.get("path") == folder), "") if folder else ""
    skill_path = f"{prefix}SKILL.md"
    return _package(result, files, skill_path, folder_hash)


def resolve_repo_info(pkg: SkillPackage) -> None:
    """Best effort: fill skillPath / skillFolderHash so `npx skills update` can track
    a skill installed from skills.sh. Silently gives up (rate limits, private repos)."""
    if pkg.skill_path:
        return
    try:
        tree = _github_tree(pkg.result.source)
        located = _locate_in_repo(pkg.result, tree)
    except SkillError:
        return
    pkg.skill_path = f"{located.folder}/SKILL.md" if located.folder else "SKILL.md"
    pkg.folder_hash = next((e.get("sha", "") for e in tree.get("tree") or []
                            if e.get("type") == "tree" and e.get("path") == located.folder), "")


def result_for_installed(skill: InstalledSkill, h: Path | None = None) -> SearchResult | None:
    """Where an installed skill came from (lock file), to update it."""
    entry = read_lock(h).get("skills", {}).get(skill.name) or {}
    source = entry.get("source", "")
    if not source or entry.get("sourceType", "github") != "github" or parse_repo(source) is None:
        return None
    skill_path = entry.get("skillPath") or ""
    if skill_path:
        folder = str(PurePosixPath(skill_path).parent)
        folder = "" if folder == "." else folder
        skill_id = PurePosixPath(folder).name if folder else source.split("/")[1]
        return SearchResult(f"{source}/{skill_id}", skill.name, source, skill_id, provider="github", folder=folder)
    return SearchResult(f"{source}/{skill.name}", skill.name, source, skill.name)


def list_installed(targets: list[Target], h: Path | None = None) -> list[InstalledSkill]:
    lock = read_lock(h).get("skills", {})
    found: dict[str, InstalledSkill] = {}
    for t in targets:
        if not t.path.is_dir():
            continue
        for entry in sorted(t.path.iterdir()):
            if entry.name.startswith(".") or not entry.is_dir():
                continue
            if t.agent == "claude-code" and entry.name == "synced":
                _add_synced(found, t, entry)
                continue
            if not has_skill_md(entry):
                continue
            skill = found.get(entry.name)
            if skill is None:
                meta = read_skill_meta(entry)
                source = (lock.get(entry.name) or {}).get("source", "")
                skill = found[entry.name] = InstalledSkill(entry.name, meta.get("description", ""), source=source, readonly=t.readonly)
            skill.paths[t.key] = entry
    return sorted(found.values(), key=lambda s: (s.readonly, s.name))


def _add_synced(found: dict[str, InstalledSkill], target: Target, synced: Path) -> None:
    """Skills synced from a claude.ai account live in skills/synced/<org_user>/<skill>."""
    for bucket in synced.iterdir():
        if not bucket.is_dir():
            continue
        for entry in sorted(bucket.iterdir()):
            if entry.is_dir() and has_skill_md(entry):
                key = f"claude.ai:{entry.name}"
                skill = found.setdefault(key, InstalledSkill(entry.name, read_skill_meta(entry).get("description", ""),
                                                             source="claude.ai", readonly=True))
                skill.paths[target.key] = entry

