#!/usr/bin/env python3
"""Hooks de Claude Code y Codex que avisan a Leo (LEO_SPEC F1.3). Instalación real: puerta G6.

--install añade los hooks `Notification` y `Stop` a ~/.claude/settings.json y `notify` a
$CODEX_HOME/config.toml (por defecto ~/.codex), sin pisar lo existente y con copia de seguridad.
--uninstall retira solo lo añadido por LEO. --dry-run muestra el resumen sin escribir.
Nunca imprime el contenido de esos archivos: pueden contener credenciales.
"""
import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import shlex
import shutil
import tempfile
import tomllib

MARK = "# LEO: avisos de Codex a Leo (scripts/agent_hooks.py)"


def launcher():
    data = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share"))
    return data / "leo/app/launch.py"


def claude_command():
    return f"/usr/bin/python3 {shlex.quote(str(launcher()))} ctl agent-event --source claude"


def codex_argv():
    return ["/usr/bin/python3", str(launcher()), "ctl", "agent-event", "--source", "codex"]


def ours(hook):
    return isinstance(hook, dict) and str(hook.get("command", "")).endswith("ctl agent-event --source claude")


def claude_update(text, install):
    """Returns (new_text, summary) for settings.json; raises ValueError on content we must not touch."""
    data = json.loads(text) if text.strip() else {}
    if not isinstance(data, dict) or not isinstance(data.get("hooks", {}), dict):
        raise ValueError("settings.json no tiene la estructura esperada; no se modifica")
    hooks = data.setdefault("hooks", {})
    summary = []
    for event in ("Notification", "Stop"):
        groups = hooks.get(event, [])
        if not isinstance(groups, list) or not all(isinstance(g, dict) and isinstance(g.get("hooks", []), list) for g in groups):
            raise ValueError(f"hooks.{event} no tiene la estructura esperada; no se modifica")
        present = any(ours(h) for g in groups for h in g.get("hooks", []))
        if install and not present:
            hooks[event] = [*groups, {"hooks": [{"type": "command", "command": claude_command(), "timeout": 10}]}]
            summary.append(f"añadir hook {event}")
        elif not install and present:
            kept = [{**g, "hooks": [h for h in g["hooks"] if not ours(h)]} for g in groups]
            kept = [g for g in kept if g["hooks"]]
            if kept:
                hooks[event] = kept
            else:
                hooks.pop(event)
            summary.append(f"retirar hook {event}")
    if not summary:
        return text, []  # Never reformat a file we do not change.
    if not hooks:
        data.pop("hooks")
    return json.dumps(data, ensure_ascii=False, indent=2) + "\n", summary


def codex_update(text, install):
    data = tomllib.loads(text)
    line = "notify = [" + ", ".join(json.dumps(part, ensure_ascii=False) for part in codex_argv()) + "]"
    current = data.get("notify")
    if install:
        if current == codex_argv():
            return text, []
        if current is not None:
            return text, ["notify ya existe y no es de LEO: no se reemplaza"]
        new = f"{MARK}\n{line}\n" + text  # Top-level key: must precede every table.
    else:
        lines = text.splitlines(keepends=True)
        new = "".join(l for l in lines if l.rstrip("\n") not in (MARK, line))
        if new == text:
            return text, []
    parsed = tomllib.loads(new)
    expected = {**data, "notify": codex_argv()} if install else {k: v for k, v in data.items() if k != "notify"}
    if parsed != expected:
        raise ValueError("config.toml no se pudo editar de forma segura; no se modifica")
    return new, ["añadir notify" if install else "retirar notify"]


def apply(path, update, install, dry_run):
    if path.is_symlink():
        raise ValueError(f"{path} es un enlace simbólico; edítalo manualmente")
    text = path.read_text() if path.exists() else ""
    new, summary = update(text, install)
    if new == text or dry_run:
        return summary
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.exists():
        backup = path.with_name(f"{path.name}.leo-backup-{datetime.now():%Y%m%d-%H%M%S}")
        shutil.copy2(path, backup)
        summary.append(f"copia de seguridad: {backup.name}")
    mode = path.stat().st_mode & 0o777 if path.exists() else 0o600
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            stream.write(new)
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--install", action="store_true")
    group.add_argument("--uninstall", action="store_true")
    group.add_argument("--dry-run", action="store_true", help="vista previa de --install")
    parser.add_argument("--preview", action="store_true", help="con --uninstall: no escribir")
    args = parser.parse_args(argv)
    install, dry_run = not args.uninstall, args.dry_run or args.preview
    codex = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")) / "config.toml"
    status = 0
    for name, path, update in (("Claude Code", Path.home() / ".claude/settings.json", claude_update),
                               ("Codex", codex, codex_update)):
        try:
            summary = apply(path, update, install, dry_run)
            print(f"{name} ({path}): {', '.join(summary) or 'sin cambios'}{' [vista previa]' if dry_run else ''}")
        except (ValueError, OSError, tomllib.TOMLDecodeError) as exc:
            status = 1
            print(f"{name} ({path}): no modificado — {exc}")
    if install and not dry_run and not launcher().is_file():
        print(f"Aviso: {launcher()} no existe; instala LEO con scripts/install.py para que los hooks funcionen.")
    raise SystemExit(status)


if __name__ == "__main__":
    main()
