#!/usr/bin/env python3
"""GNOME test tool with private runtime as well as private D-Bus and settings."""
import argparse
import os
from pathlib import Path
import signal
import subprocess
import tempfile
import threading

ROOT = Path(__file__).resolve().parent.parent


def run(argv, env, limit):
    """(exit code, output) of the isolated session. It runs in its own process group: at `limit` the whole group is
    stopped (2026-10-25: killing only the direct child left a hung GNOME session alive for days) and the output
    gathered so far is still returned, ending with a TIMEOUT line."""
    process = subprocess.Popen(argv, cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                               start_new_session=True)
    lines = []
    reader = threading.Thread(target=lambda: lines.extend(process.stdout), daemon=True)
    reader.start()
    try:
        code = process.wait(timeout=limit)
    except subprocess.TimeoutExpired:
        for sig in (signal.SIGTERM, signal.SIGKILL):
            try:
                os.killpg(process.pid, sig)
            except ProcessLookupError:
                break
            try:
                process.wait(timeout=10)
                break
            except subprocess.TimeoutExpired:
                continue
        lines.append(f"TIMEOUT: la sesión aislada superó {limit} s y se detuvo con todo su grupo de procesos\n")
        code = 124
    reader.join(timeout=10)
    return code, "".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--ui-only", action="store_true", help="Verificar solo extensión y panel GTK en el compositor aislado")
    modes.add_argument("--vision-only", action="store_true", help="Captura nativa y visión real de una escena sintética aislada")
    modes.add_argument("--guide", action="store_true", help="F6.7: guía de tres pasos, verificada tras cada clic real")
    modes.add_argument("--marks", action="store_true", help="F6.7: batería de marcas numeradas con el modelo de visión")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="leo-shell-runtime-", ignore_cleanup_errors=True) as runtime:
        env = dict(os.environ, XDG_RUNTIME_DIR=runtime, GIO_USE_VFS="local", GTK_A11Y="none")
        # A private HOME too: authorized folders must live inside HOME, never the user's real one.
        Path(runtime, "home").mkdir()
        env.update(HOME=runtime + '/home', XDG_CONFIG_HOME=runtime + '/config', XDG_DATA_HOME=runtime + '/data',
                   XDG_CACHE_HOME=runtime + '/cache', XDG_STATE_HOME=runtime + '/state',
                   LEO_PREVIEW_PATH=str(ROOT / 'assets/leo/desktop-preview.png'),
                   LEO_PROJECT_ROOT=str(ROOT), LEO_UI_PREVIEW_PATH=str(ROOT / 'assets/leo/settings-preview.png'))
        env["LEO_TEST_UI_ONLY"] = "1" if args.ui_only else "0"
        env["LEO_TEST_VISION_ONLY"] = "1" if args.vision_only else "0"
        env["LEO_TEST_GUIDE"] = "1" if args.guide or args.marks else "0"
        env["LEO_TEST_MARKS"] = "1" if args.marks else "0"
        # Do not connect the test compositor to the live display or user bus.
        for key in ("DISPLAY", "WAYLAND_DISPLAY", "DBUS_SESSION_BUS_ADDRESS", "SESSION_MANAGER"):
            env.pop(key, None)
        # The marks battery asks the vision model 20 times.
        code, output = run(["dbus-run-session", "--", "/usr/bin/python3", str(ROOT / "tests/shell_session.py")], env,
                           1500 if args.marks else 600)
        (ROOT / "build").mkdir(exist_ok=True)
        (ROOT / "build/shell-test.log").write_text(output)
        # The document portal's FUSE mount would make the runtime folder impossible to remove.
        if Path(runtime, "doc").is_mount():
            subprocess.run(["fusermount3", "-u", str(Path(runtime, "doc"))], capture_output=True, timeout=10)
        relevant = [line for line in output.splitlines()
                    if any(word in line for word in ("LEO_", "CRITICAL", "Script failed", "Extension leo", "TIMEOUT"))]
        print("\n".join(relevant) or output[-3000:])
        if code or "LEO_SHELL_PASS" not in output or "Gjs-CRITICAL" in output or "JS ERROR" in output:
            raise SystemExit(code or 1)


if __name__ == "__main__":
    main()
