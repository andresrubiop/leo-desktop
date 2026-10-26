"""Hooks de Claude Code y Codex reducidos a lo mínimo: origen, tipo de evento y carpeta.

Del JSON del hook solo se conservan el tipo de evento, el tipo de notificación y el nombre de la
carpeta del proyecto. Mensajes, prompts, transcripciones, código y rutas completas se descartan
sin guardarse ni enviarse al daemon.
"""
import json
from pathlib import PurePath

from .events import project_name

LIMIT = 1_000_000
CLAUDE_NOTIFICATIONS = {"permission_prompt": "permission", "idle_prompt": "input",
                        "elicitation_dialog": "input", "agent_needs_input": "input"}


def _load(raw):
    if isinstance(raw, str):
        raw = raw.encode()
    if len(raw) > LIMIT:
        raise ValueError("Payload del hook demasiado grande")
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("Payload del hook inválido")
    return data


def _project(cwd):
    return project_name(PurePath(cwd).name) if isinstance(cwd, str) and cwd else ""


def parse_claude(raw):
    """Hook de Claude Code (JSON por stdin). None si el evento no merece aviso."""
    data = _load(raw)
    name = data.get("hook_event_name")
    if name == "Notification":
        kind = data.get("notification_type")
        if kind is None:
            kind = "attention"  # Older versions omit the type; never inspect the message text.
        elif kind in CLAUDE_NOTIFICATIONS:
            kind = CLAUDE_NOTIFICATIONS[kind]
        else:
            return None  # auth_success and future informative types.
    elif name == "Stop":
        if data.get("stop_hook_active") is True:
            return None  # Claude continues because of another stop hook.
        kind = "stop"
    else:
        return None
    return {"source": "claude", "kind": kind, "project": _project(data.get("cwd"))}


def parse_codex(raw):
    """`notify` de Codex (JSON como último argumento). Solo agent-turn-complete."""
    data = _load(raw)
    if data.get("type") != "agent-turn-complete":
        return None
    return {"source": "codex", "kind": "stop", "project": _project(data.get("cwd"))}
