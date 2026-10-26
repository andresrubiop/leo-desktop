"""Local image models in ComfyUI (Qwen-Image, Flux, SDXL…): installed and in use.

The generation models are read from ComfyUI's model folders, so they are listed
even while the server is stopped (ComfyUI is often started on demand). When the
server answers, it also shows the VRAM it holds and what is being generated."""

from __future__ import annotations

import json
import os
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from .util import HttpError, expand_path, home, http_json

DEFAULT_HOST = "http://127.0.0.1:8188"
# folders holding the models you pick to generate with (the rest are their parts)
MODEL_DIRS = ("diffusion_models", "unet", "checkpoints")
PART_DIRS = {"text_encoders": "text_encoders", "clip": "text_encoders", "vae": "vae", "loras": "loras"}
EXTENSIONS = (".safetensors", ".gguf", ".ckpt", ".pt", ".pth", ".sft", ".bin")
MAX_FILES = 2000  # a models tree is a few dozen files; don't crawl a mistaken path forever

_QUANT = re.compile(
    r"[-_.]((?:I?Q\d+(?:_K)?(?:_[A-Z0-9]{1,2})*)|BF16|FP?16|FP?32|NF4|INT[48]|"
    r"FP8(?:_E4M3FN|_E5M2)?(?:_SCALED)?|FP4(?:_MIXED)?|MXFP\d+|SVDQ_\w+)$", re.IGNORECASE)


@dataclass
class ImageModel:
    name: str
    file: str
    size: int = 0
    quant: str = ""
    kind: str = ""          # folder it was found in
    active: bool = False    # used by the job being generated right now


@dataclass
class ComfyStatus:
    installed: bool = False
    running: bool = False
    version: str = ""
    path: str = ""
    models: list[ImageModel] = field(default_factory=list)
    parts: dict[str, list[int]] = field(default_factory=dict)  # kind -> [count, bytes]
    vram_used: int | None = None   # memory ComfyUI's PyTorch holds on the GPU
    jobs_running: int = 0
    jobs_pending: int = 0
    error: str = ""

    @property
    def total_size(self) -> int:
        return sum(m.size for m in self.models) + sum(size for _, size in self.parts.values())


def split_quant(stem: str) -> tuple[str, str]:
    """"qwen-image-2.1-Q5_K_M" → ("qwen-image-2.1", "Q5_K_M")."""
    m = _QUANT.search(stem)
    if not m or m.start() == 0:
        return stem, ""
    return stem[:m.start()], m.group(1)


def _systemd_dirs() -> list[Path]:
    """WorkingDirectory of user services that run ComfyUI (how it's often started on demand)."""
    out = []
    unit_dir = home() / ".config" / "systemd" / "user"
    try:
        units = sorted(unit_dir.glob("*.service"))
    except OSError:
        return out
    for unit in units:
        if "comfy" not in unit.name.lower():
            continue
        try:
            text = unit.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        m = re.search(r"^\s*WorkingDirectory\s*=\s*-?(.+?)\s*$", text, re.MULTILINE)
        if m:
            out.append(Path(m.group(1).replace("%h", str(home())).replace("%u", os.environ.get("USER", ""))))
    return out


def _desktop_app_dir() -> list[Path]:
    """ComfyUI Desktop keeps its install folder in its own config file."""
    try:
        data = json.loads((home() / ".config" / "ComfyUI" / "config.json").read_text(encoding="utf-8"))
        return [Path(data["basePath"])] if data.get("basePath") else []
    except (OSError, ValueError, TypeError, KeyError):
        return []


def find_root(configured: str = "") -> Path | None:
    if configured:
        path = expand_path(configured)
        return path if path is not None and (path / "models").is_dir() else None
    h = home()
    candidates = _systemd_dirs() + _desktop_app_dir() + [
        h / "ComfyUI", h / "comfyui", h / "Documents" / "ComfyUI", h / "src" / "ComfyUI",
        h / "Projects" / "ComfyUI", h / "ai" / "ComfyUI", h / ".local" / "share" / "ComfyUI"]
    for path in candidates:
        if (path / "models").is_dir():
            return path
    return None


def _model_files(folder: Path):
    count = 0
    try:
        walker = os.walk(folder, followlinks=True)
        for dirpath, dirnames, filenames in walker:
            dirnames[:] = [d for d in dirnames if not d.startswith(".")]
            for name in sorted(filenames):
                if name.lower().endswith(EXTENSIONS):
                    path = Path(dirpath) / name
                    try:
                        size = path.stat().st_size
                    except OSError:
                        continue
                    yield path, size
                    count += 1
                    if count >= MAX_FILES:
                        return
    except OSError:
        return


def scan(root: Path) -> tuple[list[ImageModel], dict[str, list[int]]]:
    models_dir = root / "models"
    models: list[ImageModel] = []
    for kind in MODEL_DIRS:
        for path, size in _model_files(models_dir / kind):
            name, quant = split_quant(path.stem)
            models.append(ImageModel(name, str(path.relative_to(models_dir / kind)), size, quant, kind))
    parts: dict[str, list[int]] = {}
    for folder, kind in PART_DIRS.items():
        for _, size in _model_files(models_dir / folder):
            entry = parts.setdefault(kind, [0, 0])
            entry[0] += 1
            entry[1] += size
    models.sort(key=lambda m: m.name.lower())
    return models, parts


def base_url(configured: str = "") -> str:
    host = configured or DEFAULT_HOST
    if "://" not in host:
        host = "http://" + host
    return host.rstrip("/").replace("://0.0.0.0", "://127.0.0.1")


def _strings(value):
    """Every string inside a workflow (model file names are plain node inputs)."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for v in value.values():
            yield from _strings(v)
    elif isinstance(value, (list, tuple)):
        for v in value:
            yield from _strings(v)


def fetch(path: str = "", host: str = "") -> ComfyStatus:
    root = find_root(path)
    st = ComfyStatus(installed=root is not None or bool(shutil.which("comfy")))
    if root is not None:
        st.path = str(root)
        st.models, st.parts = scan(root)
    url = base_url(host)
    local = "127.0.0.1" in url or "localhost" in url
    try:
        stats = http_json(f"{url}/system_stats", timeout=3, local=local, max_bytes=1 << 20)
    except HttpError as exc:
        st.error = str(exc)
        return st
    st.running = st.installed = True
    system = stats.get("system") or {}
    st.version = str(system.get("comfyui_version") or "")
    devices = [d for d in stats.get("devices") or [] if d.get("type") != "cpu"]
    if devices:
        used = sum(int(d.get("torch_vram_total") or 0) for d in devices)
        st.vram_used = used
    try:
        queue = http_json(f"{url}/queue", timeout=3, local=local, max_bytes=8 << 20)
    except HttpError:
        return st
    running = queue.get("queue_running") or []
    st.jobs_running, st.jobs_pending = len(running), len(queue.get("queue_pending") or [])
    in_use = {Path(s).name for job in running for s in _strings(job)}
    for m in st.models:
        m.active = Path(m.file).name in in_use
    return st
