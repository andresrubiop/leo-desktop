#!/usr/bin/env python3
"""Run under dbus-run-session: real Secret Service with a disposable keyring."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    with tempfile.TemporaryDirectory(prefix="leo-keyring-") as directory:
        root = Path(directory)
        os.environ.update(XDG_DATA_HOME=str(root / "data"), XDG_CONFIG_HOME=str(root / "config"),
                          XDG_CACHE_HOME=str(root / "cache"), XDG_RUNTIME_DIR=str(root / "runtime"))
        (root / "runtime").mkdir(mode=0o700)
        daemon = subprocess.Popen(["gnome-keyring-daemon", "--foreground", "--components=secrets", "--unlock",
                                   "--control-directory", str(root / "runtime/keyring")], stdin=subprocess.PIPE,
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        daemon.stdin.write(b"temporary-test-keyring\n")
        daemon.stdin.close()
        try:
            from leo.usage.secrets import Keyring
            from leo.mail import MailSecrets
            store = Keyring()
            for _ in range(50):
                try:
                    store.list()
                    break
                except RuntimeError:
                    time.sleep(.1)
            attrs = store.save("deepseek", "global", "test-value-never-sent-to-provider")
            listed = store.list()
            assert attrs in listed and all("key" not in item for item in listed)
            assert store.get(attrs) == "test-value-never-sent-to-provider"
            store.remove(attrs)
            assert attrs not in store.list()
            mail = MailSecrets()
            mail.save_password("isolated-mail-test", "temporary-mail-test-value")
            assert mail.password("isolated-mail-test") == "temporary-mail-test-value"
            mail.delete("isolated-mail-test")
            print("PASS: Secret Service real aislado; guardar, listar metadatos, leer y eliminar claves API y contraseña IMAP")
        finally:
            daemon.terminate()
            daemon.wait(timeout=5)


if __name__ == "__main__":
    main()
