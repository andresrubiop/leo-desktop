"""Launch the real daemon alongside the isolated compositor on its private bus."""
from pathlib import Path
import os
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    from leo.config import Settings
    from leo.usage.storage import write
    from leo.config import config_dir
    write(config_dir() / "terminal.json", {"theme": "tango", "font": "Monospace 11", "opacity": 0.96})
    Settings().update({"model": os.environ.get("LEO_TEST_MODEL", "")})  # Empty: automatic (qwen3:8b).
    applications = Path(os.environ["XDG_DATA_HOME"]) / "applications"
    applications.mkdir(parents=True, exist_ok=True)
    (applications / "io.github.leo.TestOpen.desktop").write_text(
        f'[Desktop Entry]\nType=Application\nName=Leo TestOpen\nExec=/usr/bin/python3 "{ROOT / "tests/open_app.py"}"\nTerminal=false\n')
    daemon = subprocess.Popen([sys.executable, "-m", "leo.daemon"], cwd=ROOT,
                              env={**os.environ, "WAYLAND_DISPLAY": "gnome-shell-test-display"})
    try:
        result = subprocess.run(["gnome-shell-test-tool", "--headless", "--wrap",
                                 f"{sys.executable} {ROOT / 'tests/shell_wrapper.py'}",
                                 "--extra-filter", "io.github.leo.Companion.UI",
                                 "--extra-filter", "io.github.leo.TestOpen",
                                 "--extra-filter", "io.github.leo.TestWindow",
                                 "--extra-filter", "org.gnome.Ptyxis", "--extension",
                                 str(ROOT / "dist/leo@local.desktop.shell-extension.zip"),
                                 os.environ.get("LEO_TEST_SCRIPT") or  # Diagnostics: any script in the same isolated session.
                                 str(ROOT / ("tests/shell_guide.js" if os.environ.get("LEO_TEST_GUIDE") == "1"
                                             else "tests/shell_capture.js" if os.environ.get("LEO_TEST_VISION_ONLY") == "1"
                                             else "tests/shell_ui.js" if os.environ.get("LEO_TEST_UI_ONLY") == "1"
                                             else "tests/shell.js"))], cwd=ROOT)
        return result.returncode
    finally:
        daemon.terminate()
        try:
            daemon.wait(timeout=5)
        except subprocess.TimeoutExpired:
            daemon.kill()
            daemon.wait()


if __name__ == "__main__":
    raise SystemExit(main())
