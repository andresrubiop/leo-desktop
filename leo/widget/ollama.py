"""Local Ollama models: installed, loaded in memory, and where (GPU/CPU).

Uses the local Ollama API; if the service is stopped, the installed models are
still listed by reading Ollama's manifests on disk."""

from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from .util import HttpError, home, http_json, parse_iso

DEFAULT_HOST = "http://127.0.0.1:11434"


@dataclass
class Model:
    name: str
    size: int = 0
    params: str = ""
    quant: str = ""
    loaded: bool = False
    vram_pct: float | None = None    # share of the loaded model in VRAM
    expires_at: float | None = None


@dataclass
class OllamaStatus:
    installed: bool = False
    running: bool = False
    version: str = ""
    models: list[Model] = field(default_factory=list)
    error: str = ""

    @property
    def total_size(self) -> int:
        return sum(m.size for m in self.models)


def base_url(configured: str = "") -> str:
    host = configured or os.environ.get("OLLAMA_HOST", "") or DEFAULT_HOST
    if "://" not in host:
        host = "http://" + host
    host = host.rstrip("/")
    # "0.0.0.0" is a bind address; talk to it over loopback
    return host.replace("://0.0.0.0", "://127.0.0.1")


def models_dirs() -> list[Path]:
    dirs = []
    if os.environ.get("OLLAMA_MODELS"):
        dirs.append(Path(os.environ["OLLAMA_MODELS"]))
    dirs += [home() / ".ollama" / "models", Path("/usr/share/ollama/.ollama/models"),
             Path("/var/lib/ollama/models")]
    return [d for d in dirs if (d / "manifests").is_dir()]


def _disk_models(root: Path) -> list[Model]:
    out = []
    manifests = root / "manifests"
    for f in sorted(manifests.glob("*/*/*/*")):
        if not f.is_file():
            continue
        registry, namespace, model, tag = f.relative_to(manifests).parts
        if registry == "registry.ollama.ai":
            name = f"{model}:{tag}" if namespace == "library" else f"{namespace}/{model}:{tag}"
        else:
            name = f"{registry}/{namespace}/{model}:{tag}"
        try:
            manifest = json.loads(f.read_text())
        except (OSError, ValueError):
            continue
        size = sum(int(layer.get("size", 0)) for layer in manifest.get("layers", []))
        m = Model(name, size)
        digest = (manifest.get("config") or {}).get("digest", "")
        blob = root / "blobs" / digest.replace(":", "-")
        try:
            if blob.is_file() and blob.stat().st_size < 1 << 20:
                cfg = json.loads(blob.read_text())
                m.params, m.quant = cfg.get("model_type", ""), cfg.get("file_type", "")
        except (OSError, ValueError):
            pass
        out.append(m)
    return out


def fetch(host: str = "") -> OllamaStatus:
    st = OllamaStatus(installed=bool(shutil.which("ollama")) or bool(models_dirs()))
    url = base_url(host)
    local = "127.0.0.1" in url or "localhost" in url
    try:
        tags = http_json(f"{url}/api/tags", timeout=3, local=local)
        st.running = True
        st.installed = True
    except HttpError as exc:
        st.error = str(exc)
        for d in models_dirs():
            st.models += _disk_models(d)
        st.models.sort(key=lambda m: m.name)
        return st

    try:
        st.version = http_json(f"{url}/api/version", timeout=3, local=local).get("version", "")
    except HttpError:
        pass
    loaded = {}
    try:
        for m in http_json(f"{url}/api/ps", timeout=3, local=local).get("models", []):
            loaded[m.get("name") or m.get("model")] = m
    except HttpError:
        pass

    for m in tags.get("models", []):
        details = m.get("details") or {}
        model = Model(m.get("name") or m.get("model", "?"), int(m.get("size") or 0),
                      details.get("parameter_size", ""), details.get("quantization_level", ""))
        running = loaded.get(model.name)
        if running:
            model.loaded = True
            total = running.get("size") or 0
            model.vram_pct = 100.0 * (running.get("size_vram") or 0) / total if total else None
            model.expires_at = parse_iso(running.get("expires_at"))
        st.models.append(model)
    st.models.sort(key=lambda m: (not m.loaded, m.name))
    return st
