#!/usr/bin/env python3
"""Auditoría repetible de metadatos; no lee credenciales ni genera inferencias."""
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path


def main():
    print("# Auditoría local de LEO\n")
    print(f"- Python: {platform.python_version()}")
    print(f"- Sistema: {platform.platform()}")
    print(f"- Sesión: {os.getenv('XDG_SESSION_TYPE', 'no declarada')}")
    print(f"- Escritorio: {os.getenv('XDG_CURRENT_DESKTOP', 'no declarado')}")
    for binary in ("gnome-shell", "gjs", "ollama", "codex", "claude", "pw-record", "whisper-cli", "espeak-ng"):
        print(f"- {binary}: {shutil.which(binary) or 'no instalado'}")
    for args in (["gnome-shell", "--version"], ["free", "-h"], ["lspci"],
                 ["nvidia-smi", "--query-gpu=name,memory.total,driver_version", "--format=csv,noheader"]):
        try:
            result = subprocess.run(args, capture_output=True, text=True, timeout=5)
            print(f"\n## {' '.join(args)}\n\n```text\n{result.stdout or result.stderr}```")
        except (OSError, subprocess.TimeoutExpired) as exc:
            print(f"- No disponible: {args[0]} ({type(exc).__name__})")
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from leo.providers import Ollama
    try:
        print("\nModelos locales:", ", ".join(Ollama().models()) or "ninguno")
    except Exception as exc:
        print("\nOllama:", exc)


if __name__ == "__main__":
    main()
