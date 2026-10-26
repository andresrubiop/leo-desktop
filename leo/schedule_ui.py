"""Horario docente en el panel: importar PDF/ICS con confirmación, avisos y carpeta de cada curso."""
from gi.repository import Gio, GLib, Gtk

from .ipc import Client
from .schedule import DAY_NAMES, _norm
from .ui import box, button, label
from .usage_ui import clear


def match_folder(name, folders):
    """Course folder whose label or name appears in the course name (or vice versa)."""
    wanted = _norm(name)
    for folder in folders:
        if folder.get("kind") != "curso":
            continue
        for candidate in (folder["label"], folder["path"].rstrip("/").rsplit("/", 1)[-1]):
            candidate = _norm(candidate or "")
            if len(candidate) >= 3 and (candidate in wanted or wanted in candidate):
                return folder["id"]
    return ""


class ScheduleUI:
    def __init__(self, app):
        self.app = app
        self.client = Client(private=True)
        self.data = {"courses": [], "term": None, "holidays": []}
        self.queue, self.waiting, self.loading = [], False, False
        self.page = app._page("schedule", "Horario")
        app._heading(self.page, "Tu semana, sin sorpresas.", "Leo te avisa antes de cada clase y, si se lo pides, abre sus materiales.")
        row = box(False)
        row.append(button("Importar PDF o ICS…", self.choose))
        row.append(button("Revisar horario", lambda: self.confirm([], self.data.get("term"))))
        self.page.append(row)
        self.page.append(label("Importar nunca guarda directamente: revisas cada clase antes. El PDF se lee en tu equipo con "
                               "pdftotext; el .ics, sin conexión. Los avisos respetan Concentración y la pantalla completa.", "hint"))
        card = app._group(self.page, "Avisos")
        self.minutes = Gtk.SpinButton.new_with_range(0, 120, 5)
        self.minutes.connect("value-changed", lambda spin: None if self.loading else
                             app._save_setting("class_alert_minutes", spin.get_value_as_int(), spin))
        app._row(card, "Minutos antes de cada clase", "0 desactiva los avisos de clase.", self.minutes)
        self.summary = label("", "muted")
        self.page.append(self.summary)
        self.card = app._group(self.page, "Clases de la semana")
        app.stack.connect("notify::visible-child-name", lambda *_: self.load() if app.stack.get_visible_child_name() == "schedule" else None)

    def close(self):
        self.client.close()

    def call(self, payload, callback):
        """One request at a time: the daemon serves one long request per client."""
        self.queue.append((payload, callback))
        self._next()

    def _next(self):
        if self.waiting or not self.queue:
            return
        self.waiting = True
        payload, callback = self.queue.pop(0)

        def done(response):
            self.waiting = False
            callback(response)
            self._next()
        self.client.call_async(payload, done)

    def _minutes(self, response):
        if response.get("ok"):
            self.loading = True
            self.minutes.set_value(response["settings"]["class_alert_minutes"])
            self.loading = False

    def load(self):
        self.call({"op": "status"}, self._minutes)
        self.call({"op": "schedule", "action": "get", "args": {}}, self.render)

    def render(self, response):
        if not response.get("ok") or not self.app.window:
            return
        self.data = {k: response[k] for k in ("courses", "term", "holidays")}
        clear(self.card)
        term = self.data["term"]
        self.summary.set_text((f"Periodo: {term['start']} → {term['end']}. " if term else "Sin periodo académico: los avisos valen todo el año. ")
                              + (f"Festivos: {', '.join(self.data['holidays'])}." if self.data["holidays"] else "Sin festivos."))
        if not self.data["courses"]:
            self.card.append(label("Todavía no hay clases. Importa tu horario en PDF o .ics.", "muted"))
        folders = {f["id"]: f["label"] for f in self.app.folders}
        for course in self.data["courses"]:
            where = f" · {course['room']}" if course["room"] else ""
            folder = f" · materiales: {folders.get(course['folder'], course['folder'])}" if course["folder"] else ""
            row = box(False, 12)
            row.add_css_class("setting-row")
            row.append(label(f"{DAY_NAMES[course['day']].capitalize()} {course['start']}–{course['end']}", "row-title"))
            text = label(course["name"] + where + folder, "muted")
            text.set_hexpand(True)
            row.append(text)
            self.card.append(row)

    def choose(self):
        dialog = Gtk.FileDialog(title="Importar horario (PDF o ICS)", modal=True)
        selection = Gtk.FileFilter(name="Horario PDF o ICS")
        for pattern in ("*.pdf", "*.PDF", "*.ics", "*.ICS"):
            selection.add_pattern(pattern)
        store = Gio.ListStore.new(Gtk.FileFilter)
        store.append(selection)
        dialog.set_filters(store)
        dialog.set_default_filter(selection)

        def chosen(source, result):
            try:
                path = source.open_finish(result).get_path()
            except GLib.Error:
                return
            self.import_path(path)
        dialog.open(self.app.window, None, chosen)

    def import_path(self, path):
        self.app.status_label.set_text("Leyendo el horario en tu equipo…")

        def parsed(response):
            if not response.get("ok"):
                self.app.status_label.set_text("No se pudo importar: " + response.get("error", "error desconocido"))
                return
            self.app.status_label.set_text(f"{len(response['courses'])} clases encontradas · revisa antes de guardar")
            self.confirm(response["courses"], response.get("term") or self.data.get("term"))
        self.call({"op": "schedule", "action": "import", "args": {"path": path}}, parsed)

    def confirm(self, candidates, term):
        """Confirmation screen: current classes plus imported candidates, each editable and optional."""
        dialog = Gtk.Window(title="Revisar horario", transient_for=self.app.window, modal=True,
                            default_width=860, default_height=560)
        self.app.dialogs.add(dialog)
        content = box(spacing=10)
        for side in ("top", "bottom", "start", "end"):
            getattr(content, "set_margin_" + side)(16)
        content.append(label("Marca las clases que quieres guardar y corrige lo que haga falta.", "section-title"))
        rows_box = box(spacing=6)
        scroll = Gtk.ScrolledWindow(vexpand=True)
        scroll.set_child(rows_box)
        content.append(scroll)
        folders = [f for f in self.app.folders if f.get("kind") == "curso"]
        folder_ids = [""] + [f["id"] for f in folders]
        rows = []
        for course in [*self.data["courses"], *candidates]:
            row = box(False, 6)
            check = Gtk.CheckButton(active=True)
            name = Gtk.Entry(text=course["name"], hexpand=True, max_length=80)
            name.set_size_request(260, -1)
            day = Gtk.DropDown.new_from_strings([d.capitalize() for d in DAY_NAMES])
            day.set_selected(course["day"])
            day.set_size_request(130, -1)
            start = Gtk.Entry(text=course["start"], max_length=5, width_chars=5, max_width_chars=5, hexpand=False)
            end = Gtk.Entry(text=course["end"], max_length=5, width_chars=5, max_width_chars=5, hexpand=False)
            room = Gtk.Entry(text=course.get("room", ""), max_length=40, width_chars=9, max_width_chars=9,
                             hexpand=False, placeholder_text="Salón")
            folder = Gtk.DropDown.new_from_strings(["Sin carpeta"] + [f["label"] or f["id"] for f in folders])
            current = course.get("folder") or match_folder(course["name"], folders)
            folder.set_selected(folder_ids.index(current) if current in folder_ids else 0)
            for widget in (check, day, name, start, end, room, folder):
                row.append(widget)
            rows_box.append(row)
            rows.append((check, name, day, start, end, room, folder))
        dates = box(False, 8)
        term_start = Gtk.Entry(text=(term or {}).get("start", ""), placeholder_text="Inicio AAAA-MM-DD", width_chars=12)
        term_end = Gtk.Entry(text=(term or {}).get("end", ""), placeholder_text="Fin AAAA-MM-DD", width_chars=12)
        holidays = Gtk.Entry(text=", ".join(self.data.get("holidays", [])), hexpand=True,
                             placeholder_text="Festivos: AAAA-MM-DD, AAAA-MM-DD…")
        for widget in (label("Periodo", "row-title"), term_start, term_end, holidays):
            dates.append(widget)
        content.append(dates)
        error = label("", "error")
        content.append(error)

        def close():
            self.app.dialogs.discard(dialog)
            dialog.destroy()

        def save():
            courses = [{"name": n.get_text(), "day": d.get_selected(), "start": s.get_text().strip(),
                        "end": e.get_text().strip(), "room": r.get_text().strip(), "folder": folder_ids[f.get_selected()]}
                       for c, n, d, s, e, r, f in rows if c.get_active()]
            args = {"courses": courses, "holidays": [h.strip() for h in holidays.get_text().split(",") if h.strip()]}
            if term_start.get_text().strip() or term_end.get_text().strip():
                args["term"] = {"start": term_start.get_text().strip(), "end": term_end.get_text().strip()}

            def saved(response):
                if response.get("ok"):
                    self.app.status_label.set_text(f"Horario guardado · {len(response['courses'])} clases")
                    close()
                    self.render(response)
                else:
                    error.set_text(response.get("error", "No se guardó"))
            self.call({"op": "schedule", "action": "set", "args": args}, saved)
        actions = box(False)
        actions.append(button("Cancelar", close))
        actions.append(button("Guardar horario", save))
        content.append(actions)
        dialog.set_child(content)
        dialog.connect("close-request", lambda _: (self.app.dialogs.discard(dialog), False)[1])
        dialog.present()
        self.dialog = dialog
        self.save_dialog = save
