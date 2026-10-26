"""Icons and accessible names of tests/marks_app.py, without importing GTK (importing Gtk 4 from PyGObject connects
to the display: a test compositor waiting for this process would deadlock, as it did on 2026-10-23)."""
import json
import random

ICONS = ["document-print", "user-trash", "system-search", "emblem-system", "go-home", "mail-unread", "starred",
         "camera-photo", "x-office-calendar", "audio-x-generic", "changes-prevent", "audio-input-microphone",
         "audio-volume-high", "folder", "battery-full", "network-wireless-signal-excellent", "list-add",
         "document-edit", "alarm", "avatar-default", "weather-clear", "preferences-system-notifications",
         "input-keyboard"]


def names():
    """Icon → accessible name («Acción N», seeded shuffle), the same in the app and in the test."""
    numbers = list(range(1, len(ICONS) + 1))
    random.Random(7).shuffle(numbers)
    return {icon: f"Acción {n}" for icon, n in zip(ICONS, numbers)}


if __name__ == "__main__":
    print(json.dumps(names()))
