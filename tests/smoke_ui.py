#!/usr/bin/env python3
"""Real GTK smoke test; private bus and temporary settings. Opens a test window."""
import os
from pathlib import Path
import sys
import tempfile
import traceback

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from leo.ui import LeoApp
from gi.repository import GLib


def main():
    session_home = os.environ.get("HOME", "")  # In the full shell suite the daemon already runs with this HOME.
    with tempfile.TemporaryDirectory(prefix="leo-ui-") as directory:
        os.environ["XDG_CONFIG_HOME"] = directory + "/config"
        os.environ["XDG_DATA_HOME"] = directory + "/data"
        os.environ["HOME"] = directory + "/home"
        Path(directory, "home/Proyectos").mkdir(parents=True)
        from leo.usage.storage import write
        from leo.config import config_dir
        write(config_dir() / "terminal.json", {"theme": "tango", "font": "Monospace 11", "opacity": 0.96})
        app = LeoApp()
        state = {"ok": False, "steps": 0, "closed": False}
        def check():
            try:
                state["steps"] += 1
                if not hasattr(app, "model"):
                    if state["steps"] < 30: return GLib.SOURCE_CONTINUE
                    raise RuntimeError("GTK failed to connect daemon or build permissions")
                assert app.window.get_visible()
                assert app.stack.get_page(app.stack.get_child_by_name("actions"))
                for name in ("usage", "skills", "accounts", "mail"):
                    assert app.stack.get_child_by_name(name), name
                assert app.stack.get_child_by_name("chat").has_css_class("terminal")
                assert app.stack.get_child_by_name("seen") and app.seen_list, "Página «Lo que vi» (F7.2)"
                assert app.nav["companion"].get_label().startswith("› "), "Navegación tipo ls"
                app._append("Prueba GTK real: español, ñ, permisos y texto sin markup <script>.")
                assert "roam" in app.controls and "roam_scope" in app.controls
                assert not hasattr(app, "provider"), "Remote provider selector must not exist"
                assert "codex" not in app.controls and "claude" not in app.controls
                assert "desktop" in app.controls and "jobs" in app.controls and "memory" in app.controls and app.folders_card
                assert app.memory_card and app.memory_card.get_first_child()
                assert app.knowledge_status and app.knowledge_index
                assert app.stack.get_child_by_name("schedule") and app.schedule_ui.minutes
                assert "accessibility" in app.controls and app.vision_model
                app.stack.set_visible_child_name("companion")
                folder = {"id": "proyectos", "path": directory + "/home/Proyectos", "label": "Proyectos", "write": False}

                def folders_saved(response):
                    if not response.get("ok") and "leo-shell-runtime" in session_home and not state.get("retried"):
                        # A shared daemon (full suite) only accepts folders inside its own isolated HOME.
                        state["retried"] = True
                        shared = Path(session_home) / "Proyectos"
                        shared.mkdir(exist_ok=True)
                        app.client.call_async({"op": "settings", "changes": {"folders": [{**folder, "path": str(shared)}]}},
                                              folders_saved)
                        return
                    app._render_folders(response["settings"]["folders"])
                    app.client.call_async({"op": "tool", "name": "system.info", "args": {}}, finished)
                app.client.call_async({"op": "settings", "changes": {"folders": [folder]}}, folders_saved)
                return GLib.SOURCE_REMOVE
            except Exception:
                traceback.print_exc()
                app.quit()
                return GLib.SOURCE_REMOVE
        def finished(response):
            state["ok"] = response.get("ok", False) and [f["id"] for f in app.folders] == ["proyectos"]
            if state["ok"]: print("LEO_GTK_PASS: ventana GTK4 real, permisos, carpetas autorizadas, conexión al daemon y acción de metadatos")
            else: print(response)
            if state["ok"]:  # F7.3: Uso de IA draws the AI Usage Widget's view, refreshed while it is shown.
                app.stack.set_visible_child_name("usage")
                state["waits"] = 0
                GLib.timeout_add(500, usage_view)
            else:
                close_window()

        def usage_view():
            state["waits"] += 1
            text = app.usage_ui.view.get_text()
            if "SISTEMA" in text and "CPU" in text:
                print("LEO_USAGE_VIEW_PASS: «Uso de IA» muestra la vista del widget (sistema, cuotas y modelos)")
                after_usage()
                return GLib.SOURCE_REMOVE
            if state["waits"] > 40:
                state["ok"] = False
                print("La vista de uso no se dibujó: " + text[:200])
                close_window()
                return GLib.SOURCE_REMOVE
            return GLib.SOURCE_CONTINUE

        def after_usage():
            if state["ok"] and os.environ.get("LEO_UI_PREVIEW_PATH"):
                Path(os.environ["XDG_RUNTIME_DIR"], "leo-ui-ready").write_text("ready")
                GLib.timeout_add(100, preview_pages)
            else:
                close_window()
        def preview_pages():
            runtime = Path(os.environ["XDG_RUNTIME_DIR"])
            request = runtime / "leo-ui-page"
            if not request.exists():
                return GLib.SOURCE_CONTINUE
            page = request.read_text()
            request.unlink()
            if page == "close":
                return close_window()
            if page in app.nav:
                app.stack.set_visible_child_name(page)
                (runtime / "leo-ui-page-ready").write_text(page)
            return GLib.SOURCE_CONTINUE
        def close_window():
            # Test the actual close-request path, including a child dialog and a
            # pending request, instead of masking lifecycle errors with app.quit().
            app.usage_ui.dialog("Prueba de cierre")
            app.client.call_async({"op": "status"}, lambda _: None)
            app.close_button.emit("clicked")
            state["closed"] = app.window is None and not app.dialogs and app.client.closed
            return GLib.SOURCE_REMOVE
        GLib.timeout_add(300, check)
        app.run(["leo-smoke"])
        if not state["ok"] or not state["closed"]: raise SystemExit(1)
        print("LEO_GTK_CLOSE_PASS: botón Cerrar destruye ventana, diálogos y conexiones pendientes")


if __name__ == "__main__":
    main()
