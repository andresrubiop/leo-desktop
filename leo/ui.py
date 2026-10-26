"""Interfaz GTK4. La conversación solo produce texto; acciones por controles explícitos.

Desde 0.17 (LEO_SPEC F7.2, petición del usuario) todo el panel se ve como una terminal, igual que el AI Usage
Widget: su paleta y fuente, prompt en la cabecera y la barra de estado, navegación tipo `ls` y encabezados `── … ──`.
"""
import json
from pathlib import Path
import re
import subprocess
import sys
import threading
import gi
gi.require_version("Gtk", "4.0")
from gi.repository import Gtk, Gdk, Gio, GLib, Pango

from .config import ROOT
from .ipc import Client, NAME
from .terminal import appearance, gtk_css, panel_css, prompt_markup



def label(text, css=None):
    item = Gtk.Label(label=text, xalign=0, wrap=True, selectable=True)
    if css:
        item.add_css_class(css)
    return item


def box(vertical=True, spacing=10):
    return Gtk.Box(orientation=Gtk.Orientation.VERTICAL if vertical else Gtk.Orientation.HORIZONTAL, spacing=spacing)


def button(text, callback):
    item = Gtk.Button(label=text)
    item.connect("clicked", lambda _: callback())
    return item


class LeoApp(Gtk.Application):
    def __init__(self):
        super().__init__(application_id="io.github.leo.Companion.UI", flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE)
        self.add_main_option("page", 0, GLib.OptionFlags.NONE, GLib.OptionArg.STRING, "Abrir sección de Leo", "SECCIÓN")
        self.client = None
        self.child = None
        self.window = None
        self.last_answer = ""
        self.record_held = False
        self.record_started = False
        self.dialogs = set()

    def do_command_line(self, command_line):
        page = command_line.get_options_dict().lookup_value("page", GLib.VariantType.new("s"))
        self.activate()
        if page and page.get_string() in self.nav:
            self.stack.set_visible_child_name(page.get_string())
        return 0

    def do_activate(self):
        if self.window:
            self.window.present()
            return
        provider = Gtk.CssProvider()
        self.theme = appearance()
        provider.load_from_data((panel_css(self.theme) + gtk_css(self.theme)).encode())
        Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        self.client = Client(private=True)
        # Activation is provided by the installer; development can spawn the daemon.
        owner = self.client.bus.call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
                                          "NameHasOwner", GLib.Variant("(s)", (NAME,)), None,
                                          Gio.DBusCallFlags.NONE, 3000, None).unpack()[0]
        if not owner and not (ROOT / ".leo-managed").is_file():
            self.child = subprocess.Popen([sys.executable, "-m", "leo.daemon"], cwd=ROOT)
        self.window = Gtk.ApplicationWindow(application=self, title="Leo · Tu compañero", default_width=1060, default_height=680)
        self.window.add_css_class("leo-window")
        self.window.connect("close-request", self._closing)
        header = Gtk.HeaderBar()
        header.add_css_class("leo-header")
        header_title = Gtk.Label(xalign=.5)
        header_title.set_markup(prompt_markup(self.theme, "leo --ajustes", "~"))
        header.set_title_widget(header_title)
        header.set_show_title_buttons(True)
        self.close_button = button("Cerrar", lambda: self.window.close() if self.window else None)
        header.pack_end(self.close_button)
        close_action = Gio.SimpleAction.new("close", None)
        close_action.connect("activate", lambda *_: self.window.close() if self.window else None)
        self.window.add_action(close_action)
        self.set_accels_for_action("win.close", ["<Control>w", "<Control>q"])
        self.window.set_titlebar(header)
        layout = box(False, 0)
        self.window.set_child(layout)
        sidebar = box(spacing=8)
        sidebar.add_css_class("sidebar")
        sidebar.set_size_request(178, -1)
        palette = self.theme["palette"]
        brand = Gtk.Label(xalign=0)
        brand.set_markup(f'<span foreground="{palette[10]}" weight="bold">leo</span>:'
                         f'<span foreground="{palette[12]}" weight="bold">~</span>$ ls')
        brand.add_css_class("brand")
        sidebar.append(brand)
        sidebar.append(label("# tu compañero", "eyebrow"))
        spacer = box(); spacer.set_size_request(-1, 10); sidebar.append(spacer)
        sidebar_scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER)
        sidebar_scroll.set_child(sidebar)
        sidebar_scroll.set_min_content_width(210)
        layout.append(sidebar_scroll)
        body = box(spacing=0)
        body.set_hexpand(True)
        layout.append(body)
        self.stack = Gtk.Stack(vexpand=True, hexpand=True)
        self.stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        body.append(self.stack)
        status = box(False, 0)
        status.add_css_class("statusbar")
        prompt = Gtk.Label(xalign=0)
        prompt.set_markup(f'<span foreground="{palette[10]}" weight="bold">leo</span>:'
                          f'<span foreground="{palette[12]}" weight="bold">~/leo</span>$ ')
        self.cursor = Gtk.Label(label="█")
        self.cursor.add_css_class("cursor")
        self.status_label = Gtk.Label(label="Conectando con Leo…", xalign=1, hexpand=True,
                                      ellipsize=Pango.EllipsizeMode.END)
        self.status_label.add_css_class("status-text")
        for item in (prompt, self.cursor, self.status_label):
            status.append(item)
        body.append(status)
        self._blink = GLib.timeout_add(530, self._blink_cursor)
        self.controls = {}
        self.folders, self.folders_card = [], None
        self.memory_card = None
        self.seen_list = None
        self._updating = False
        self._settings_pending = set()
        self.page_names = {}
        self.seen_focus = None
        self.companion_box = self._page("companion", "Mi compañero")
        self.seen_box = self._page("seen", "Lo que vi")
        self.permissions_box = self._page("permissions", "Privacidad")
        self.models_box = self._page("models", "Modelo y voz")
        self._conversation()
        self._actions()
        from .usage_ui import UsageUI
        self.usage_ui = UsageUI(self)
        from .mail_ui import MailUI
        self.mail_ui = MailUI(self)
        from .schedule_ui import ScheduleUI
        self.schedule_ui = ScheduleUI(self)
        self.nav = {}
        group = None
        for name, title in (("companion", "Mi compañero"), ("chat", "Conversar"), ("seen", "Lo que vi"),
                            ("usage", "Uso de IA"), ("skills", "Skills"), ("accounts", "Cuentas"),
                            ("mail", "Correo"), ("schedule", "Horario"),
                            ("actions", "Acciones"), ("permissions", "Privacidad"), ("models", "Modelo y voz")):
            nav = Gtk.ToggleButton(label="  " + title)
            nav.add_css_class("nav")
            nav.get_child().set_xalign(0)
            if group: nav.set_group(group)
            else: group = nav
            nav.connect("toggled", self._nav_toggled, name, title)
            sidebar.append(nav)
            self.nav[name] = nav
        self.stack.connect("notify::visible-child-name", lambda *_: self.nav[self.stack.get_visible_child_name()].set_active(True))
        self.nav["companion"].set_active(True)
        spring = box(); spring.set_vexpand(True); sidebar.append(spring)
        sidebar.append(label("# siempre cerca", "eyebrow"))
        sidebar.append(label("un pequeño compañero,\nun espacio tuyo", "muted"))
        self.window.present()
        self._attempts = 0
        GLib.timeout_add(300, self._connect_service)
        self._refresh_timer = GLib.timeout_add_seconds(3, self._refresh)

    def _nav_toggled(self, item, name, title):
        item.set_label(("› " if item.get_active() else "  ") + title)
        item.get_child().set_xalign(0)
        if item.get_active():
            self.stack.set_visible_child_name(name)

    def _blink_cursor(self):
        if not self.window:
            self._blink = 0
            return GLib.SOURCE_REMOVE
        if self.cursor.has_css_class("off"):
            self.cursor.remove_css_class("off")
        else:
            self.cursor.add_css_class("off")
        return GLib.SOURCE_CONTINUE

    def _page(self, name, title):
        page = box(spacing=12)
        page.add_css_class("page")
        self.page_names[page] = name
        scroll = Gtk.ScrolledWindow(vexpand=True, hexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
        scroll.set_child(page)
        self.stack.add_titled(scroll, name, title)
        return page

    def _heading(self, page, title, description):
        """`usuario@equipo:~/leo$ <sección>`, the title in bold and the description as a comment."""
        name = getattr(self, "page_names", {}).get(page)
        nav = {"companion": "compañero", "seen": "lo-que-vi", "usage": "uso-ia", "permissions": "privacidad",
               "models": "modelo-y-voz", "accounts": "cuentas", "mail": "correo", "schedule": "horario",
               "actions": "acciones", "skills": "skills"}.get(name)
        if nav:
            line = Gtk.Label(xalign=0, wrap=True)
            line.set_markup(prompt_markup(self.theme, "cat " + nav, "~/leo"))
            line.add_css_class("prompt-line")
            page.append(line)
        heading = label(title, "title")
        heading.set_selectable(False)
        page.append(heading)
        page.append(label("# " + description, "subtitle"))

    def _group(self, page, title):
        """A section header like the widget's: `── Título ───────`."""
        header = box(False, 6)
        header.set_margin_top(6)
        for text, css in (("──", "rule"), (title, "section-title")):
            item = label(text, css)
            item.set_selectable(False)
            item.set_wrap(False)
            header.append(item)
        line = Gtk.Separator(hexpand=True, valign=Gtk.Align.CENTER)
        line.add_css_class("rule-line")
        header.append(line)
        page.append(header)
        card = box(spacing=0)
        card.add_css_class("card")
        page.append(card)
        return card

    def _row(self, card, title, description, control):
        row = box(False, 20)
        row.add_css_class("setting-row")
        words = box(spacing=4)
        words.set_hexpand(True)
        title_label = label(title, "row-title")
        title_label.set_selectable(False)
        words.append(title_label)
        subtitle = label(description, "muted")
        subtitle.set_max_width_chars(44)
        words.append(subtitle)
        row.append(words)
        control.set_valign(Gtk.Align.CENTER)
        row.append(control)
        card.append(row)

    def _switch(self, card, settings, key, title, description):
        control = Gtk.Switch(active=settings[key])
        self.controls[key] = (control, None)
        control.connect("notify::active", self._toggle, key)
        self._row(card, title, description, control)

    def _choice(self, card, settings, key, title, description, choices):
        values, titles = zip(*choices)
        control = Gtk.DropDown.new_from_strings(titles)
        control.set_selected(values.index(settings[key]))
        self.controls[key] = (control, values)
        control.connect("notify::selected", lambda item, _: self._save_setting(key, values[item.get_selected()], item))
        self._row(card, title, description, control)

    def _save_setting(self, key, value, control):
        if self._updating or key in self._settings_pending:
            return
        self._settings_pending.add(key)
        control.set_sensitive(False)
        def changed(response):
            self._settings_pending.discard(key)
            control.set_sensitive(True)
            if response.get("ok"):
                self.status_label.set_text("Cambios guardados · Leo responde con tu modelo local")
                self._sync_controls(response["settings"])
            else:
                self.status_label.set_text("No se guardó el cambio: " + response.get("error", "Servicio no disponible"))
                self._refresh()
        self.client.call_async({"op": "settings", "changes": {key: value}}, changed)

    def _render_folders(self, folders):
        self.folders = [dict(f) for f in folders]
        card = self.folders_card
        while card.get_first_child():
            card.remove(card.get_first_child())
        if not folders:
            card.append(label("Ninguna carpeta todavía: los archivos quedan fuera del alcance de Leo.", "muted"))
        for folder in folders:
            controls = box(False, 8)
            kinds = [("general", "General"), ("curso", "Curso"), ("investigacion", "Investigación")]
            kind = Gtk.DropDown.new_from_strings([title for _, title in kinds])
            kind.set_valign(Gtk.Align.CENTER)
            kind.set_selected([k for k, _ in kinds].index(folder.get("kind", "general")))
            kind.connect("notify::selected", lambda control, _p, ident=folder["id"], kinds=kinds: self._save_folders(
                [{**f, "kind": kinds[control.get_selected()][0]} if f["id"] == ident else f for f in self.folders]))
            controls.append(kind)
            write = Gtk.Switch(active=folder["write"], tooltip_text="Permitir escritura (con aprobación)",
                               valign=Gtk.Align.CENTER)
            write.connect("notify::active", lambda control, _p, ident=folder["id"]: self._save_folders(
                [{**f, "write": control.get_active()} if f["id"] == ident else f for f in self.folders]))
            controls.append(write)
            controls.append(button("Quitar", lambda ident=folder["id"]: self._save_folders(
                [f for f in self.folders if f["id"] != ident])))
            where = " · escritura" if folder["write"] else " · solo lectura"
            if folder["write"] and folder.get("kind", "general") != "general":
                where = " · escribe solo en Leo-borradores/"
            self._row(card, folder["label"] or folder["id"], folder["path"] + " · incluye subcarpetas" + where, controls)

    def _load_memory(self):
        self.client.call_async({"op": "memory", "action": "list", "args": {}},
                               lambda response: self._render_memory(response.get("facts", [])) if response.get("ok") else None)

    def _knowledge(self, action="status"):
        self.knowledge_index.set_sensitive(False)
        self.knowledge_status.set_text("Actualizando índice local…" if action == "index" else "Comprobando Cerveau…")

        def loaded(response):
            self.knowledge_index.set_sensitive(True)
            if not response.get("ok"):
                text = response.get("error", "Cerveau no disponible")
            elif not response.get("available"):
                text = "No se encuentra Cerveau local."
            elif not response.get("initialized"):
                text = "Cerveau necesita inicializar su espacio de trabajo."
            else:
                text = (f"Cerveau conectado · {response['authorized_folders']} carpetas autorizadas · "
                        f"{response['registered_folders']} registradas · {response['text_files']} archivos con texto · "
                        f"{response['metadata_files']} solo catalogados.")
            self.knowledge_status.set_text(text)
        self.client.call_async({"op": "knowledge", "action": action, "args": {}}, loaded)

    def _memory_action(self, action, args, message):
        def done(response):
            self.status_label.set_text(message if response.get("ok") else "Memoria: " + response.get("error", "Servicio no disponible"))
            self._load_memory()
        self.client.call_async({"op": "memory", "action": action, "args": args}, done)

    def _remember(self):
        text = self.memory_entry.get_text().strip()
        if text:
            self.memory_entry.set_text("")
            self._memory_action("add", {"text": text}, "Recordado")

    def _render_memory(self, facts):
        card = self.memory_card
        if card is None or not self.window:
            return
        while card.get_first_child():
            card.remove(card.get_first_child())
        if not facts:
            card.append(label("Leo todavía no recuerda nada de ti.", "muted"))
        for fact in facts:
            entry = Gtk.Entry(text=fact["text"], hexpand=True, max_length=200)
            controls = box(False, 8)
            controls.append(button("Guardar", lambda entry=entry, ident=fact["id"]: self._memory_action(
                "update", {"id": ident, "text": entry.get_text()}, "Recuerdo actualizado")))
            controls.append(button("Borrar", lambda ident=fact["id"]: self._memory_action("delete", {"id": ident}, "Recuerdo borrado")))
            row = box(False, 12)
            row.add_css_class("setting-row")
            row.append(entry)
            row.append(controls)
            card.append(row)

    def _save_folders(self, folders):
        if "folders" in self._settings_pending:
            return
        self._settings_pending.add("folders")
        def saved(response):
            self._settings_pending.discard("folders")
            if response.get("ok"):
                self.status_label.set_text("Carpetas guardadas")
                self._render_folders(response["settings"]["folders"])
            else:
                self.status_label.set_text("No se guardó la carpeta: " + response.get("error", "Servicio no disponible"))
                self._render_folders(self.folders)
        self.client.call_async({"op": "settings", "changes": {"folders": folders}}, saved)

    def _add_folder(self):
        dialog = Gtk.FileDialog(title="Autorizar una carpeta para Leo", modal=True)
        def chosen(source, result):
            try:
                path = source.select_folder_finish(result).get_path()
            except GLib.Error:
                return  # Cancelled by the user.
            name = Path(path).name
            base = re.sub(r"[^a-z0-9_-]+", "-", name.lower()).strip("-")[:28] or "carpeta"
            ident, n = base, 2
            while any(f["id"] == ident for f in self.folders):
                ident, n = f"{base}-{n}", n + 1
            self._save_folders(self.folders + [{"id": ident, "path": path, "label": name[:64], "write": False}])
        dialog.select_folder(self.window, None, chosen)

    def _sync_controls(self, settings):
        if self.folders_card and "folders" not in self._settings_pending and settings["folders"] != self.folders:
            self._render_folders(settings["folders"])
        self._updating = True
        try:
            for key, (control, choices) in self.controls.items():
                if key in self._settings_pending:
                    continue
                if choices: control.set_selected(choices.index(settings[key]))
                else: control.set_active(settings[key])
        finally:
            self._updating = False

    def _refresh(self):
        if not self.window:
            return GLib.SOURCE_REMOVE
        self.client.call_async({"op": "status"}, lambda response: self._sync_controls(response["settings"]) if response.get("ok") else None)
        return GLib.SOURCE_CONTINUE

    def _connect_service(self):
        if not self.window:
            return GLib.SOURCE_REMOVE
        self._attempts += 1
        def loaded(response):
            if response.get("ok"):
                self.status_label.set_text("Conectado · respuestas del LLM local · sin escucha permanente")
                self._permissions(response["settings"])
                self.usage_ui.load()
            elif self._attempts < 15:
                GLib.timeout_add(300, self._connect_service)
            else:
                self.status_label.set_text("No se pudo iniciar el servicio. Ejecuta scripts/run.sh daemon para ver el error.")
        self.client.call_async({"op": "status"}, loaded)
        return GLib.SOURCE_REMOVE

    def _conversation(self):
        page = box()
        page.add_css_class("page")
        page.add_css_class("terminal")
        line = Gtk.Label(xalign=0)
        line.set_markup(prompt_markup(self.theme, "leo conversar", "~/leo"))
        page.append(line)
        page.append(label("# Ollama · sesión local", "subtitle"))
        self.history = box()
        self.history.append(label("[sistema] Modelo local listo para recibir tu mensaje.", "card"))
        self.chat_scroll = Gtk.ScrolledWindow(vexpand=True, min_content_height=240)
        self.chat_scroll.set_child(self.history)
        page.append(self.chat_scroll)
        chip = label("Responde tu LLM local · Ollama", "chip")
        chip.set_halign(Gtk.Align.START)
        page.append(chip)
        self.input = Gtk.Entry(placeholder_text="tú@local:~$ escribe a Leo…", max_length=16000, hexpand=True)
        self.input.connect("activate", lambda _: self.send())
        row = box(False)
        row.append(self.input)
        self.send_button = button("Enviar", self.send)
        self.send_button.add_css_class("suggested-action")
        row.append(self.send_button)
        page.append(row)
        audio = box(False)
        self.ptt = Gtk.Button(label="Mantén pulsado para dictar")
        gesture = Gtk.GestureClick(button=1)
        gesture.connect("pressed", self._record_press)
        gesture.connect("released", self._record_release)
        gesture.connect("cancel", lambda *_: self._record_release())
        self.ptt.add_controller(gesture)
        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._record_key_press)
        keys.connect("key-released", self._record_key_release)
        self.ptt.add_controller(keys)
        audio.append(self.ptt)
        audio.append(button("Leer respuesta", lambda: self.request({"op": "speak", "text": self.last_answer})))
        page.append(audio)
        page.append(label("El dictado usa whisper.cpp local. Revisa el texto antes de enviarlo.", "muted"))
        self.stack.add_titled(page, "chat", "Conversar")

    def _actions(self):
        page = box()
        page.add_css_class("page")
        page.append(label("Una mano con tu día.", "title"))
        page.append(label("Activa las capacidades en Permisos. Las acciones sensibles muestran una confirmación.", "muted"))
        apps = box(False)
        for key, title in (("files", "Archivos"), ("calculator", "Calculadora"), ("settings", "Ajustes"), ("spotify", "Spotify")):
            apps.append(button(title, lambda k=key: self.tool("app.open", {"app": k})))
        page.append(apps)
        row = box(False)
        row.append(button("Terminal vacía", lambda: self.tool("terminal.open")))
        row.append(button("Sistema", lambda: self.tool("system.info")))
        row.append(button("Memoria", lambda: self.tool("system.command", {"command": "memory"})))
        page.append(row)
        page.append(label("Spotify · reproductor local", "muted"))
        music = box(False)
        for action, title in (("Previous", "Anterior"), ("PlayPause", "Reproducir / pausa"), ("Next", "Siguiente")):
            music.append(button(title, lambda a=action: self.tool("music.control", {"action": a})))
        page.append(music)
        screen = box(False)
        screen.append(button("Captura con permiso", lambda: self.tool("screen.capture")))
        screen.append(button("Mover puntero +40 px", lambda: self.tool("pointer.move", {"dx": 40, "dy": 0})))
        page.append(screen)
        page.append(label("Guardar una nota nueva", "muted"))
        self.note_name = Gtk.Entry(placeholder_text="Nombre sin espacios", max_length=64)
        self.note_text = Gtk.Entry(placeholder_text="Texto de la nota", max_length=16000)
        page.append(self.note_name)
        page.append(self.note_text)
        page.append(button("Revisar y guardar", lambda: self.tool("note.save", {"name": self.note_name.get_text(), "text": self.note_text.get_text()})))
        page.append(label("Los resultados y errores se muestran en Conversar.", "muted"))
        scroll = Gtk.ScrolledWindow(vexpand=True)
        scroll.set_child(page)
        self.stack.add_titled(scroll, "actions", "Acciones")

    def _permissions(self, settings):
        if self.permissions_box.get_first_child():
            return
        page = self.companion_box
        self._heading(page, "A su aire. A tu lado.", "Decide cómo quieres que Leo te acompañe.")
        hero = box(False, 18)
        hero.add_css_class("hero")
        image = Gtk.Picture.new_for_filename(str(ROOT / "assets/leo/sit.svg"))
        image.set_size_request(150, 160)
        image.set_content_fit(Gtk.ContentFit.CONTAIN)
        hero.append(image)
        intro = box(spacing=9)
        intro.set_valign(Gtk.Align.CENTER)
        intro.set_hexpand(True)
        intro.append(label("Un escritorio con compañía.", "hero-title"))
        intro.append(label("Leo pasea, descansa y se queda\ndonde lo necesites.", "hero-caption"))
        chip = label("Siempre con tu LLM local", "chip")
        chip.set_halign(Gtk.Align.START)
        intro.append(chip)
        hero.append(intro)
        page.append(hero)
        card = self._group(page, "Paseos y movimiento")
        self._switch(card, settings, "roam", "Pasear libremente", "Explora, se detiene y descansa a su ritmo.")
        self._choice(card, settings, "roam_scope", "Dónde puede pasear", "Puedes llevarlo a otra pantalla arrastrándolo.",
                     [("all", "Todas las pantallas"), ("current", "Pantalla actual")])
        self._choice(card, settings, "pace", "Ritmo del paseo", "El paso de sus patas acompaña la velocidad.",
                     [("slow", "Tranquilo"), ("normal", "Natural"), ("lively", "Alegre")])
        self._switch(card, settings, "motion", "Animaciones", "Respeta también Reducir movimiento de GNOME.")
        page.append(label("Mueve el puntero sobre su cabeza o haz clic derecho para acariciarlo: inclina la cabeza y mueve la cola. Clic izquierdo para conversar. Arrastra a Leo para dejarlo quieto. Suéltalo cerca del borde superior de una ventana para que se apoye en ella. Activa Pasear libremente cuando quieras que vuelva a explorar.", "hint"))
        card = self._group(page, "Tu espacio")
        self._switch(card, settings, "visible", "Mostrar a Leo", "Un clic sobre él abre la conversación.")
        self._switch(card, settings, "focus", "Concentración", "Oculta a Leo hasta que desactives este modo.")

        page = self.permissions_box
        self._heading(page, "Tú tienes el control.", "Activa solo lo que quieras compartir con Leo.")
        card = self._group(page, "En este equipo")
        for key, title, description in [
            ("apps", "Aplicaciones", "Abrir aplicaciones y una terminal vacía."),
            ("music", "Música", "Controlar el reproductor local de Spotify."),
            ("files", "Archivos y notas", "Buscar y leer en carpetas autorizadas; crear, mover o enviar a la papelera con tu aprobación."),
            ("desktop", "Ajustes del escritorio", "Modo oscuro, no molestar y volumen, con tu aprobación y reversibles."),
            ("commands", "Diagnósticos", "Pedir permiso para consultar memoria, disco y actividad."),
            ("screen", "Pantalla y puntero", "Al pedir «mira mi pantalla», Leo captura una vez sin selector y analiza en local. El puntero requiere aprobación."),
            ("watch", "Leo atento", "Paseando por el escritorio observa todo; dentro de una ventana, solo esa; quieto o dormido, nada. "
             "Una captura por minuto como mucho, solo analiza lo que cambió (OCR local) y anota en su diario de hoy lo que vio "
             "(texto, nunca imágenes). Al hacer clic en Leo ves ese diario y puedes preguntarle. Necesita Pantalla."),
            ("usage_live", "Cuotas en vivo", "Uso de IA consulta Claude (con su sesión, solo lectura, a api.anthropic.com), "
             "Codex (su App Server oficial) y Antigravity (su app local). Apagado, Leo muestra la última lectura."),
            ("seen_notes", "Notas visuales en el diario", "Con la GPU libre (sin entrenamientos ni chat reciente), el modelo de "
             "visión resume en una frase lo que haces, como mucho cada 5 minutos."),
            ("agent_pets", "Mascotitas de agentes", "Una mascotita junto a Leo por cada sesión de Claude, Codex o Antigravity trabajando. "
             "Solo mira fechas de sus archivos de sesión y la CPU de sus procesos, nunca su contenido."),
            ("jobs", "Trabajos y GPU", "Vigilar procesos y logs de carpetas autorizadas; consultar nvidia-smi."),
            ("memory", "Memoria", "Recordar hechos breves que apruebes y tenerlos en cuenta al conversar."),
            ("accessibility", "Accesibilidad", "Leer botones y menús de la ventana activa para guiarte; pide permiso por aplicación."),
        ]:
            self._switch(card, settings, key, title, description)
        page.append(label("Las respuestas de Leo provienen únicamente de tu LLM local. No hay proveedores remotos ni historial de conversación guardado.", "hint"))
        card = self._group(page, "Ventanas protegidas")
        self.protected = Gtk.Entry(text=", ".join(settings.get("protected_apps", [])), hexpand=True)
        self._row(card, "Leo nunca las ve", "Se ocultan en cada captura; si una está activa, Leo no la captura. "
                  "Identificador de la aplicación o clase de ventana, separados por comas.", self.protected)
        save = button("Guardar ventanas protegidas", self._save_protected)
        save.set_halign(Gtk.Align.START)
        page.append(save)
        card = self._group(page, "Mirar con un gesto")
        self.look_shortcut = Gtk.Entry(text=settings.get("look_shortcut", ""), placeholder_text="Vacío: sin atajo")
        self._row(card, "Atajo «Leo, mira»", "Leo mira la ventana activa una vez. En el globo, 👁 hace lo mismo y ⬚ te deja "
                  "elegir una zona. Recuerda la captura 2 minutos solo en memoria; «Olvidar» o cerrar el globo la borra.",
                  self.look_shortcut)
        save = button("Guardar atajo", self._save_shortcut)
        save.set_halign(Gtk.Align.START)
        page.append(save)
        seen = self.seen_box
        self._heading(seen, "Lo que Leo vio hoy", "Lo que anotó mientras observaba. Elige una entrada y pregúntale por ella.")
        self.seen_list = self._group(seen, "Diario de hoy")
        row = box(False)
        row.append(button("Actualizar", self._load_watch_log))
        row.append(button("Borrar el diario de hoy", lambda: self.client.call_async(
            {"op": "watch", "action": "clear", "args": {}}, lambda _response: self._load_watch_log())))
        seen.append(row)
        seen.append(label("Solo texto de hoy, en un archivo privado que se borra al cambiar de día: hora, aplicación, título, "
                          "proyecto, documento abierto (solo en carpetas autorizadas), lo leído y notas breves. Nunca imágenes; "
                          "las ventanas protegidas y la navegación privada no se anotan. También puedes hacer clic en Leo: "
                          "«👁 lo que vi hoy».", "hint"))
        self._load_watch_log()
        self.stack.connect("notify::visible-child-name",
                           lambda *_: self._load_watch_log() if self.stack.get_visible_child_name() == "seen" else None)
        self.folders_card = self._group(page, "Carpetas autorizadas")
        self._render_folders(settings["folders"])
        add = button("Añadir carpeta…", self._add_folder)
        add.set_halign(Gtk.Align.START)
        page.append(add)
        card = self._group(page, "Cursos e investigación · Cerveau")
        self.knowledge_status = label("Comprobando Cerveau…", "muted")
        card.append(self.knowledge_status)
        card.append(label("Marca tus carpetas como Curso o Investigación y activa Archivos y notas. "
                          "Actualizar índice registra y lee únicamente esas carpetas en Cerveau local. "
                          "También indexa el texto de PDF y documentos de oficina; Leo los cita por página.", "hint"))
        self.knowledge_index = button("Actualizar índice", lambda: self._knowledge("index"))
        card.append(self.knowledge_index)
        self._knowledge()
        self.memory_card = self._group(page, "Lo que Leo recuerda de ti")
        self.memory_card.append(label("Cargando…", "muted"))
        row = box(False, 8)
        self.memory_entry = Gtk.Entry(placeholder_text="Por ejemplo: Prefiero respuestas cortas", hexpand=True, max_length=200)
        row.append(self.memory_entry)
        row.append(button("Recordar", self._remember))
        page.append(row)
        page.append(label("Leo solo propone recordar; nada se guarda sin tu aprobación. Nunca guarda conversaciones. "
                          "Se usa solo con la capacidad Memoria activa.", "hint"))
        self._load_memory()
        page.append(label("Cada carpeta autorizada incluye todas sus subcarpetas, sin pedir permiso por cada una. "
                          "Leo solo busca, lee o cambia archivos dentro de estas carpetas, nunca en carpetas ocultas ni de configuración. "
                          "Escritura permite crear borradores, mover, renombrar o enviar a la papelera, siempre con tu aprobación; "
                          "en carpetas de curso o investigación solo dentro de Leo-borradores/. "
                          "Pide «deshaz eso» para revertir el último plan.", "hint"))

        page = self.models_box
        self._heading(page, "Una voz propia. En tu equipo.", "Elige el modelo local con el que conversa Leo.")
        card = self._group(page, "Conversación local")
        self.model = Gtk.Entry(text=settings["model"], placeholder_text="Automático: qwen3:8b o el primer modelo local")
        self._row(card, "Modelo de Ollama", "Usa un modelo que ya esté instalado.", self.model)
        self.vision_model = Gtk.Entry(text=settings.get("vision_model", ""), placeholder_text="Automático: qwen2.5vl:7b si está instalado")
        self._row(card, "Modelo de visión", "Para «Leo, mira esto». Instálalo tú: ollama pull qwen2.5vl:7b", self.vision_model)
        self.num_ctx = Gtk.SpinButton.new_with_range(2048, 131072, 1024)
        self.num_ctx.set_value(settings.get("num_ctx", 8192))
        self._row(card, "Contexto (tokens)", "Más contexto usa más memoria de vídeo y tarda más.", self.num_ctx)
        row = box(False)
        row.append(button("Ver modelos instalados", self._load_models))
        save = button("Guardar modelo", self._save_models)
        save.add_css_class("suggested-action")
        row.append(save)
        page.append(row)
        self.models_list = label("", "muted")
        page.append(self.models_list)
        card = self._group(page, "Escuchar y hablar")
        self._switch(card, settings, "microphone", "Dictado", "Solo al mantener pulsado. Máximo 20 segundos.")
        self._switch(card, settings, "voice", "Leer respuestas", "Lectura en voz alta, cuando tú la solicites.")
        self.whisper_model = Gtk.Entry(text=settings["whisper_model"], placeholder_text="/ruta/al/modelo.bin")
        self._row(card, "Modelo para dictado", "Archivo local de whisper.cpp.", self.whisper_model)
        page.append(button("Guardar configuración de voz", self._save_models))
        page.append(label("Si Ollama no está disponible, Leo te lo indicará. Nunca cambiará a un servicio remoto.", "hint"))

    def _load_models(self):
        def loaded(response):
            self.models_list.set_text("Disponibles: " + ", ".join(response["models"]) if response.get("ok") else response.get("error", "Ollama no disponible"))
        self.client.call_async({"op": "models"}, loaded)

    def _save_models(self):
        def saved(response):
            self.status_label.set_text("Modelos guardados · todas las respuestas serán locales" if response.get("ok") else response.get("error", "No se guardó"))
        self.client.call_async({"op": "settings", "changes": {
            "model": self.model.get_text().strip(), "num_ctx": self.num_ctx.get_value_as_int(),
            "vision_model": self.vision_model.get_text().strip(),
            "whisper_model": self.whisper_model.get_text().strip()}}, saved)

    def _save_protected(self):
        apps = [item.lower() for item in re.split(r"[,\s]+", self.protected.get_text()) if item]
        def saved(response):
            self.status_label.set_text("Ventanas protegidas guardadas" if response.get("ok") else response.get("error", "No se guardó"))
        self.client.call_async({"op": "settings", "changes": {"protected_apps": apps}}, saved)

    def _load_watch_log(self):
        def loaded(response):
            card = self.seen_list
            if card is None or not self.window:
                return
            while card.get_first_child():
                card.remove(card.get_first_child())
            entries = response.get("entries") or [] if response.get("ok") else []
            if not entries:
                text = ("Sin observaciones hoy. Activa Leo atento y déjalo pasear o ponlo dentro de una ventana."
                        if response.get("ok") else response.get("error", "Leo no responde"))
                card.append(label(text, "muted"))
                return
            for entry in entries[::-1]:
                card.append(self._seen_row(entry))
        self.client.call_async({"op": "watch", "action": "seen", "args": {"limit": 120}}, loaded)

    def _seen_row(self, entry):
        at, until = entry["at"][11:16], entry.get("until", entry["at"])[11:16]
        document = entry.get("document") or {}
        what = " · ".join(part for part in (entry.get("app"), document.get("name") or entry.get("title"),
                                            entry.get("project") and "proyecto " + entry["project"]) if part)
        row = box(False, 12)
        row.add_css_class("setting-row")
        words = box(spacing=2)
        words.set_hexpand(True)
        top = box(False, 10)
        for text, css in ((at + ("–" + until if until != at else ""), "seen-time"), (what or "escritorio", "seen-what")):
            item = label(text, css)
            item.set_wrap(False)
            item.set_ellipsize(Pango.EllipsizeMode.END)
            top.append(item)
        words.append(top)
        if entry.get("finding"):
            words.append(label("⚠ " + entry["finding"], "seen-finding"))
        if entry.get("note"):
            words.append(label("✎ " + entry["note"], "seen-note"))
        if entry.get("lines"):
            words.append(label("› " + " / ".join(entry["lines"][:3])[:300], "seen-read"))
        if document.get("outside"):
            words.append(label("Documento fuera de tus carpetas autorizadas: añádela en Privacidad para que Leo lo lea.", "seen-read"))
        row.append(words)
        ask = button("Preguntar", lambda: self._ask_about(entry))
        ask.set_valign(Gtk.Align.START)
        row.append(ask)
        return row

    def _ask_about(self, entry):
        """The next message in Conversar is about this entry (its text and document go with the question)."""
        self.seen_focus = entry["id"]
        self.stack.set_visible_child_name("chat")
        self.input.set_placeholder_text(f"pregunta sobre lo de las {entry['at'][11:16]}…")
        self.input.grab_focus()

    def _save_shortcut(self):
        def saved(response):
            self.status_label.set_text("Atajo guardado · se aplica en unos segundos" if response.get("ok") else response.get("error", "No se guardó"))
        self.client.call_async({"op": "settings", "changes": {"look_shortcut": self.look_shortcut.get_text().strip()}}, saved)

    def _toggle(self, toggle, _pspec, key):
        self._save_setting(key, toggle.get_active(), toggle)

    def _append(self, text, error=False):
        self.history.append(label(text, "error" if error else "bubble"))
        # Bound in-memory UI history; no persistent conversation log.
        children = []
        item = self.history.get_first_child()
        while item:
            children.append(item)
            item = item.get_next_sibling()
        for old in children[:-60]:
            self.history.remove(old)
        self.stack.set_visible_child_name("chat")

    def request(self, payload, callback=None):
        self.status_label.set_text("Procesando…")
        def done(response):
            self.status_label.set_text("Listo · local por defecto")
            if callback:
                callback(response)
            else:
                self._response(response)
        self.client.call_async(payload, done)

    def _response(self, response):
        if not response.get("ok"):
            self._append(response.get("error", "Error desconocido"), error=True)
        elif response.get("approval"):
            self._approve(response)
        else:
            result = response.get("result", response.get("models", response.get("settings", "Listo")))
            text = result if isinstance(result, str) else json.dumps(result, ensure_ascii=False, indent=2)
            self.last_answer = text
            self._append(text)

    def tool(self, name, args=None):
        self.request({"op": "tool", "name": name, "args": args or {}})

    def send(self):
        prompt = self.input.get_text().strip()
        if not prompt or not self.send_button.get_sensitive():
            return
        self.input.set_text("")
        self._append("tú@local:~$ " + prompt)
        self.send_button.set_sensitive(False)
        payload = {"op": "chat", "prompt": prompt}
        if self.seen_focus:  # A question about an entry of «Lo que vi».
            payload["seen"], self.seen_focus = self.seen_focus, None
            self.input.set_placeholder_text("tú@local:~$ escribe a Leo…")
        def done(response):
            self.send_button.set_sensitive(True)
            if response.get("ok") and isinstance(response.get("result"), str):
                if response.get("tools"):
                    consulted = []
                    if any(t.startswith("usage.") for t in response["tools"]):
                        consulted.append("uso de IA")
                    if "skills.list" in response["tools"]:
                        consulted.append("skills instaladas")
                    self._append("[sistema] Consultados: " + ", ".join(consulted) + ". Respuesta del modelo local:")
                self.last_answer = response["result"]
                self._append("leo@local:~$ " + self.last_answer)
            else:
                self._response(response)
            if not response.get("ok") and response.get("action_status"):
                self._append("[sistema] " + response["action_status"])
        self.request(payload, done)

    def _approve(self, response):
        dialog = Gtk.Window(title="Autorizar acción", transient_for=self.window, modal=True,
                            default_width=560, default_height=420)
        self.dialogs.add(dialog)
        content = box()
        for side in ("top", "bottom", "start", "end"):
            getattr(content, "set_margin_" + side)(18)
        content.append(label(response["description"], "title"))
        view = Gtk.TextView(editable=False, wrap_mode=Gtk.WrapMode.WORD_CHAR)
        view.get_buffer().set_text(json.dumps(response["arguments"], ensure_ascii=False, indent=2))
        scroll = Gtk.ScrolledWindow(vexpand=True)
        scroll.set_child(view)
        content.append(scroll)
        content.append(label("Solo esta acción. La aprobación caduca en 120 segundos.", "muted"))
        decided = False
        def decide(allowed):
            nonlocal decided
            if decided:
                return
            decided = True
            self.dialogs.discard(dialog)
            dialog.destroy()
            self.request({"op": "decide", "token": response["approval"], "approved": allowed})
        row = box(False)
        row.append(button("Cancelar", lambda: decide(False)))
        row.append(button("Autorizar una vez", lambda: decide(True)))
        content.append(row)
        dialog.set_child(content)
        dialog.connect("close-request", lambda _: (decide(False), True)[1])
        dialog.present()

    def _record_press(self, *_args):
        if self.record_held or self.record_started:
            return
        self.record_held = True
        self.ptt.set_label("Solicitando micrófono…")
        def started(response):
            self.record_started = response.get("ok", False)
            if not self.record_started:
                self.record_held = False
                self.ptt.set_label("Mantén pulsado para dictar")
                self._response(response)
            elif not self.record_held:
                self._record_release()
            else:
                self.ptt.set_label("Grabando · suelta para transcribir")
        self.request({"op": "record_start"}, started)

    def _record_release(self, *_args):
        self.record_held = False
        if not self.record_started:
            return
        self.record_started = False
        self.ptt.set_sensitive(False)
        self.ptt.set_label("Transcribiendo localmente…")
        def stopped(response):
            self.ptt.set_sensitive(True)
            self.ptt.set_label("Mantén pulsado para dictar")
            if response.get("ok"):
                self.input.set_text(response.get("transcript", ""))
            else:
                self._response(response)
        self.request({"op": "record_stop"}, stopped)

    def _record_key_press(self, _controller, keyval, _keycode, _state):
        if keyval in (Gdk.KEY_space, Gdk.KEY_Return):
            self._record_press()
            return True
        return False

    def _record_key_release(self, _controller, keyval, _keycode, _state):
        if keyval in (Gdk.KEY_space, Gdk.KEY_Return):
            self._record_release()

    def _closing(self, *_args):
        # Closing the private connection cancels recording without transcription and
        # removes pending approvals. No callback can touch destroyed GTK widgets.
        if self.client:
            self.client.close()
        if hasattr(self, "usage_ui"):
            self.usage_ui.close()
        if hasattr(self, "mail_ui"):
            self.mail_ui.close()
        if hasattr(self, "schedule_ui"):
            self.schedule_ui.close()
        for dialog in tuple(self.dialogs):
            dialog.destroy()
        self.dialogs.clear()
        self.window = None
        return False

    def do_shutdown(self):
        if getattr(self, "_blink", 0):
            GLib.source_remove(self._blink)
            self._blink = 0
        if getattr(self, "_refresh_timer", 0):
            GLib.source_remove(self._refresh_timer)
            self._refresh_timer = 0
        if self.child:
            child = self.child
            child.terminate()
            def stop_child():
                try:
                    child.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait()
            # Never freeze GTK while a local inference finishes in the daemon.
            threading.Thread(target=stop_child, daemon=True).start()
        Gtk.Application.do_shutdown(self)


def main():
    return LeoApp().run(sys.argv)


if __name__ == "__main__":
    main()
