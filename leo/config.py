"""Preferencias pequeñas, validadas y persistidas de forma atómica."""
import json
import math
import os
from pathlib import Path
import re
import tempfile
import threading

ROOT = Path(__file__).resolve().parent.parent
DEFAULTS = {
    "motion": True, "focus": False, "visible": True,
    "apps": False, "music": False, "microphone": False, "voice": False,
    "screen": False, "files": False, "commands": False, "jobs": False, "desktop": False, "memory": False, "accessibility": False,
    "codex": False, "claude": False,
    "model": "", "vision_model": "", "num_ctx": 8192, "class_alert_minutes": 15, "whisper_model": "", "corner": "right",
    "roam": False, "roam_scope": "all", "pace": "normal", "position": None,
    # Authorized folders (LEO_SPEC F2.5 schema); F1 uses them for job logs.
    "folders": [],
    # Windows hidden from every capture (F6.1): app id, sandboxed id or WM class, lowercase.
    # Atajo global de «Leo, mira» (F6.3, G12); vacío lo desactiva. Formato de aceleradores de GTK.
    "look_shortcut": "<Super><Shift>l",
    # Leo atento (2026-10-23): paseando observa todo; sobre una ventana, solo esa. Apagado por defecto.
    "watch": False,
    # Mascotitas de Claude, Codex y Antigravity cocinando (solo metadatos de sus sesiones).
    "agent_pets": True,
    # Diario de lo que Leo vio hoy (F7.1): notas cortas del modelo de visión, solo con la GPU libre.
    "seen_notes": True,
    # Cuotas en vivo (F7.3, G18): Claude (su sesión, solo lectura), Codex (App Server) y Antigravity (app local).
    "usage_live": True,
    "protected_apps": ["org.keepassxc.keepassxc", "keepassxc", "com.bitwarden.desktop", "bitwarden", "1password",
                       "org.gnome.seahorse.application", "seahorse", "org.gnome.world.secrets", "io.ente.auth"],
}
FOLDER_KINDS = {"general", "curso", "investigacion"}
DRAFTS = "Leo-borradores"  # The only place Leo writes inside course and research folders (G1).
PRIVATE = {".ssh", ".gnupg", ".config", ".local", ".password-store", ".mozilla", ".pki", ".kube", ".aws"}


def config_dir():
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "leo"


def data_dir():
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "leo"


def folder_path(path):
    """Resolved folder inside $HOME, without hidden components (.ssh, .config, keyrings…)."""
    if not isinstance(path, str) or not path.startswith("/") or "\x00" in path or len(path) > 1024:
        raise ValueError("La carpeta debe ser una ruta absoluta")
    home = Path.home().resolve()
    real = Path(path).resolve()
    try:
        relative = real.relative_to(home)
    except ValueError:
        raise ValueError("La carpeta debe estar dentro de tu carpeta personal") from None
    if not relative.parts:
        raise ValueError("No se autoriza la carpeta personal completa")
    if any(part.startswith(".") or part in PRIVATE for part in relative.parts):
        raise ValueError("No se autorizan carpetas ocultas ni de configuración o credenciales")
    return real


def validate_folders(value):
    if not isinstance(value, list) or len(value) > 32:
        raise ValueError("folders: lista de hasta 32 carpetas")
    seen = set()
    for folder in value:
        if (not isinstance(folder, dict) or not {"id", "path", "label", "write"} <= set(folder) <= {"id", "path", "label", "write", "kind"}
                or folder.get("kind", "general") not in FOLDER_KINDS
                or not isinstance(folder["id"], str) or not re.fullmatch(r"[a-z0-9_-]{1,32}", folder["id"])
                or folder["id"] in seen or not isinstance(folder["label"], str) or len(folder["label"]) > 64
                or type(folder["write"]) is not bool):
            raise ValueError("Carpeta inválida: {id, path, label, write, kind?}")
        seen.add(folder["id"])
        folder_path(folder["path"])


def validate(changes):
    if not isinstance(changes, dict) or set(changes) - DEFAULTS.keys():
        raise ValueError("Preferencia desconocida")
    for key, value in changes.items():
        if key == "folders":
            validate_folders(value)
        elif key == "protected_apps":
            if (not isinstance(value, list) or len(value) > 64
                    or any(not isinstance(v, str) or not re.fullmatch(r"[a-z0-9._-]{1,128}", v) for v in value)):
                raise ValueError("Ventanas protegidas: hasta 64 identificadores en minúsculas (letras, números, . _ -)")
        elif key == "position":
            if value is None:
                continue
            if (not isinstance(value, dict) or set(value) != {"monitor", "x", "y"}
                    or type(value["monitor"]) is not int or not 0 <= value["monitor"] < 64
                    or any(type(value[k]) not in (int, float) or not math.isfinite(value[k])
                           or not 0 <= value[k] <= 1 for k in ("x", "y"))):
                raise ValueError("Posición de Leo inválida")
        elif key == "class_alert_minutes":
            if type(value) is not int or not 0 <= value <= 120:
                raise ValueError("Aviso de clase: de 0 (desactivado) a 120 minutos")
        elif key == "num_ctx":
            # Context window for the local model; larger values cost VRAM and latency.
            if type(value) is not int or not 2048 <= value <= 131072:
                raise ValueError("num_ctx debe ser un entero entre 2048 y 131072")
        elif isinstance(DEFAULTS[key], bool):
            if type(value) is not bool:
                raise ValueError(f"{key} requiere un booleano")
            if key in {"codex", "claude"} and value:
                raise ValueError("Leo responde únicamente con el LLM local")
        elif not isinstance(value, str) or len(value) > 1024:
            raise ValueError(f"Valor inválido: {key}")
        elif key in {"model", "vision_model"} and value and not re.fullmatch(r"[a-zA-Z0-9_./:-]{1,128}", value):
            raise ValueError("Nombre de modelo inválido")
        elif key == "look_shortcut" and value and not re.fullmatch(r"(?:<(?:Super|Shift|Control|Ctrl|Alt|Primary|Meta)>){1,4}[A-Za-z0-9]{1,12}", value):
            raise ValueError("Atajo inválido: por ejemplo <Super><Shift>l, con al menos un modificador")
        elif key == "corner" and value not in {"left", "right"}:
            raise ValueError("La esquina debe ser left o right")
        elif key == "roam_scope" and value not in {"current", "all"}:
            raise ValueError("El paseo debe ser current o all")
        elif key == "pace" and value not in {"slow", "normal", "lively"}:
            raise ValueError("Ritmo de paseo inválido")


class Settings:
    def __init__(self, path=None):
        self.path = path or config_dir() / "settings.json"
        self.lock = threading.RLock()
        self.values = dict(DEFAULTS)
        if self.path.exists():
            values = json.loads(self.path.read_text())
            # Retain compatibility with older files, but never restore remote access.
            values.update(codex=False, claude=False)
            # A folder that stopped qualifying (moved, symlinked away) is dropped, not fatal.
            kept = []
            for folder in values.get("folders", []) if isinstance(values.get("folders"), list) else []:
                try:
                    validate_folders(kept + [folder])
                    kept.append(folder)
                except ValueError:
                    pass
            values["folders"] = kept
            validate(values)
            self.values.update(values)

    def snapshot(self):
        with self.lock:
            return dict(self.values)

    def update(self, changes):
        validate(changes)
        with self.lock:
            new = self.values | changes
            self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            fd, filename = tempfile.mkstemp(prefix=".settings-", dir=self.path.parent)
            try:
                with os.fdopen(fd, "w") as stream:
                    json.dump(new, stream, ensure_ascii=False, indent=2)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(filename, self.path)
                self.values = new
            finally:
                Path(filename).unlink(missing_ok=True)
            return dict(new)
