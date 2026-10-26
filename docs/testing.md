# Testing

Run from the repository root:

```bash
python3 -m unittest discover -s tests -v
node tests/movement.mjs
```

Some tests use system PyGObject/GTK4. On Ubuntu, install `python3-gi`, `gir1.2-gtk-4.0` and `python3-pil`. The optional photo-analysis test requires NumPy and OpenCV and uses synthetic images. CI installs those dependencies too.

Installation tests redirect XDG directories into temporary storage. They do not enable an extension in the user's session.

Real D-Bus checks can run under a private bus:

```bash
dbus-run-session -- python3 tests/integration_dbus.py
```

Read `python3 scripts/test_shell.py --help` before testing GNOME. It starts an isolated shell. Do not replace, enable, restart or reload the user's running extension for automated testing. Hardware-dependent checks, microphone, vision-model quality and multi-monitor pointing must be reported separately.

Release preparation on 2026-10-26: Python 3.14 collected 195 unit tests, with 194 passing and one skipped because OpenCV was unavailable. This does not establish end-to-end desktop validation during release preparation.
