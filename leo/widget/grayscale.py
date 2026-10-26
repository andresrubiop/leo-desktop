"""One-click black & white for every screen (GNOME).

GNOME has no plain "grayscale" switch, but its accessibility zoom can
desaturate the whole desktop: at 1× magnification, full-screen, with colour
saturation 0 it shows every monitor unchanged — only without colour. The
previous zoom settings are saved and restored, so people who already use the
magnifier keep their own setup."""

from __future__ import annotations

MAG = "org.gnome.desktop.a11y.magnifier"
APPS = "org.gnome.desktop.a11y.applications"
ENABLED = "screen-magnifier-enabled"


def _gio_settings(schema: str):
    from gi.repository import Gio
    source = Gio.SettingsSchemaSource.get_default()
    if source is None or source.lookup(schema, True) is None:
        return None
    return Gio.Settings.new(schema)


class Grayscale:
    def __init__(self, state: dict, save_state, settings_factory=_gio_settings):
        self.state, self.save_state = state, save_state
        self.mag = settings_factory(MAG)
        self.apps = settings_factory(APPS)

    @property
    def available(self) -> bool:
        return self.mag is not None and self.apps is not None

    def is_on(self) -> bool:
        return (self.available and self.apps.get_boolean(ENABLED)
                and self.mag.get_double("color-saturation") < 0.01)

    def set(self, on: bool) -> None:
        if not self.available or on == self.is_on():
            return
        if on:
            prev = {"enabled": self.apps.get_boolean(ENABLED),
                    "factor": self.mag.get_double("mag-factor"),
                    "saturation": self.mag.get_double("color-saturation"),
                    "position": self.mag.get_string("screen-position")}
            self.state["grayscale_prev"] = prev
            self.mag.set_double("color-saturation", 0.0)
            if not prev["enabled"]:  # zoom was off: turn it on at 1×, i.e. no zoom at all
                self.mag.set_double("mag-factor", 1.0)
                self.mag.set_string("screen-position", "full-screen")
                self.apps.set_boolean(ENABLED, True)
        else:
            prev = self.state.pop("grayscale_prev", None)
            if prev is None:  # switched on elsewhere: undo what our 1× mode looks like
                prev = {"enabled": self.mag.get_double("mag-factor") > 1.0, "factor": 2.0,
                        "saturation": 1.0, "position": "full-screen"}
            self.mag.set_double("color-saturation", prev["saturation"] if prev["saturation"] >= 0.01 else 1.0)
            if not prev["enabled"]:
                self.apps.set_boolean(ENABLED, False)
                self.mag.set_double("mag-factor", prev["factor"])
                self.mag.set_string("screen-position", prev["position"])
        self.save_state()
        try:
            from gi.repository import Gio
            Gio.Settings.sync()
        except ImportError:
            pass

    def toggle(self) -> bool:
        self.set(not self.is_on())
        return self.is_on()

    def connect(self, callback) -> None:
        """Call ``callback()`` when the state changes (also from GNOME Settings)."""
        if not self.available:
            return
        self.apps.connect(f"changed::{ENABLED}", lambda *_: callback())
        self.mag.connect("changed::color-saturation", lambda *_: callback())
