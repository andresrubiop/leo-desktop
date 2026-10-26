"""Ajustes del escritorio en lista blanca (LEO_SPEC F2.6): modo oscuro, no molestar y volumen.

Cada cambio devuelve el valor anterior para el diario de deshacer. El volumen usa `wpctl` con argv
fijo y sin shell; los demás, GSettings de GNOME.
"""
import re
import shutil
import subprocess

KEYS = {"dark_mode", "do_not_disturb", "volume"}


def validate(key, value):
    if key not in KEYS:
        raise ValueError("Ajustes permitidos: dark_mode, do_not_disturb, volume")
    if key == "volume":
        if not isinstance(value, str) or not re.fullmatch(r"\d{1,3}", value.strip()) or int(value) > 100:
            raise ValueError("Volumen: número entero de 0 a 100")
    elif value not in {"on", "off"}:
        raise ValueError("Valor: on u off")


class Desktop:
    def __init__(self, settings=None, run=subprocess.run):
        self._settings, self.run = settings, run

    def gsettings(self, schema):
        if self._settings:
            return self._settings(schema)
        from gi.repository import Gio
        return Gio.Settings.new(schema)

    def _wpctl(self, *args):
        executable = shutil.which("wpctl", path="/usr/bin:/bin")
        if not executable:
            raise RuntimeError("wpctl (PipeWire) no está instalado")
        result = self.run([executable, *args], capture_output=True, text=True, timeout=5,
                          env={"PATH": "/usr/bin:/bin", "LANG": "C"})
        if result.returncode:
            raise RuntimeError("wpctl no pudo completar la operación")
        return result.stdout

    def get(self, key):
        if key == "dark_mode":
            return "on" if self.gsettings("org.gnome.desktop.interface").get_string("color-scheme") == "prefer-dark" else "off"
        if key == "do_not_disturb":
            return "off" if self.gsettings("org.gnome.desktop.notifications").get_boolean("show-banners") else "on"
        match = re.search(r"Volume:\s*([\d.]+)", self._wpctl("get-volume", "@DEFAULT_AUDIO_SINK@"))
        if not match:
            raise RuntimeError("No se pudo leer el volumen actual")
        return str(round(float(match[1]) * 100))

    def _apply(self, key, value):
        if key == "dark_mode":
            self.gsettings("org.gnome.desktop.interface").set_string("color-scheme", "prefer-dark" if value == "on" else "default")
        elif key == "do_not_disturb":
            self.gsettings("org.gnome.desktop.notifications").set_boolean("show-banners", value == "off")
        else:
            self._wpctl("set-volume", "@DEFAULT_AUDIO_SINK@", f"{int(value) / 100:.2f}")

    def set(self, key, value):
        validate(key, value)
        previous = self.get(key)
        self._apply(key, value.strip())
        return {"setting": key, "value": value.strip(), "previous": previous}, {"op": "setting", "key": key, "value": previous}

    def revert(self, undo):
        validate(undo["key"], undo["value"])
        self._apply(undo["key"], undo["value"])
        return f"{undo['key']} restaurado a {undo['value']}"
