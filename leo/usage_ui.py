"""Vistas nativas GTK para las funciones absorbidas de AI Usage Widget."""
from datetime import datetime
import time

from gi.repository import GLib, Gtk

from .ipc import Client
from .ui import box, button, label


def clear(widget):
    while widget.get_first_child():
        widget.remove(widget.get_first_child())


def date(value):
    try:
        return datetime.fromtimestamp(value).strftime("%d/%m %H:%M")
    except (TypeError, ValueError, OSError, OverflowError):
        return "sin lectura"


class UsageUI:
    def __init__(self, app):
        self.app = app
        self.client = Client(private=True)
        self.busy = False
        self.accounts = []
        self.targets = []
        self.installed = []
        self.providers = []
        self.loaded = False
        self.feedback = None
        self.usage = app._page("usage", "Uso de IA")
        app._heading(self.usage, "Uso de IA", "Cuotas, CPU/RAM/GPU, Ollama y ComfyUI, como el AI Usage Widget · "
                     "conversación siempre local")
        # F7.3: the widget's own view, drawn by its renderer in the panel's palette; refreshed every 2 s while shown.
        row = box(False)
        row.append(button("⟳ Actualizar", lambda: self.load_view(refresh=True)))
        self.gray = Gtk.ToggleButton(label="◐ Blanco y negro", tooltip_text="Todas las pantallas sin color (lupa de GNOME a 1×)")
        self.gray.connect("toggled", self._grayscale)
        self._gray_sync = False
        row.append(self.gray)
        row.append(button("Configurar cuentas", lambda: app.stack.set_visible_child_name("accounts")))
        self.usage.append(row)
        self.view = Gtk.Label(xalign=0, yalign=0, selectable=True, wrap=False)
        self.view.add_css_class("usage-view")
        self.usage.append(self.view)
        self.view_status = label("", "muted")
        self.usage.append(self.view_status)
        self.viewing = False
        self.view_timer = GLib.timeout_add_seconds(2, self._tick_view)
        details = app._group(self.usage, "Detalle por cuenta")
        details.set_visible(False)  # Only the header of the group is used; the cards follow.
        row = box(False)
        row.append(button("Recargar cuentas", self.load))
        self.usage.append(row)
        self.status = label("Leyendo metadatos locales…", "muted")
        self.usage.append(self.status)
        self.usage_list = box(spacing=14)
        self.usage.append(self.usage_list)

        self.accounts_page = app._page("accounts", "Cuentas")
        app._heading(self.accounts_page, "Tus cuentas de IA", "Perfiles de Codex, Claude y Antigravity; claves en el llavero de GNOME.")
        self.accounts_page.append(label("Estas cuentas consultan uso y abren los flujos oficiales de acceso. Leo responde únicamente con Ollama local.", "hint"))
        row = box(False)
        row.append(button("Añadir perfil", self.add_profile))
        row.append(button("Añadir clave API", self.add_key))
        row.append(button("Recargar", self.load))
        self.accounts_page.append(row)
        self.account_list = box(spacing=14)
        self.accounts_page.append(self.account_list)

        self.skills_page = app._page("skills", "Skills")
        app._heading(self.skills_page, "Tu biblioteca de skills", "Las skills pertenecen a sus herramientas; no cambian el modelo local de Leo.")
        self.query = Gtk.SearchEntry(placeholder_text="Filtrar instaladas por nombre, descripción o herramienta…")
        self.query.connect("search-changed", lambda *_: self.render_skills())
        self.skills_page.append(self.query)
        row = box(False)
        self.search_entry = Gtk.Entry(placeholder_text="Buscar en skills.sh o escribir owner/repo", hexpand=True, max_length=200)
        row.append(self.search_entry)
        row.append(button("Buscar en línea", self.search))
        row.append(button("Recargar", self.load_skills))
        self.skills_page.append(row)
        self.skills_status = label("", "muted")
        self.skills_page.append(self.skills_status)
        self.search_results = box()
        self.skills_page.append(self.search_results)
        self.skill_list = box()
        self.skills_page.append(self.skill_list)

    def close(self):
        if self.view_timer:
            GLib.source_remove(self.view_timer)
            self.view_timer = 0
        self.client.close()

    def _tick_view(self):
        if not self.app.window:
            self.view_timer = 0
            return GLib.SOURCE_REMOVE
        if self.app.stack.get_visible_child_name() == "usage" and not self.viewing:
            self.load_view()
        return GLib.SOURCE_CONTINUE

    def load_view(self, refresh=False):
        """Ask the daemon for the widget's view (instant: it never waits for the network)."""
        from .widget.render import segments_to_pango
        self.viewing = True

        def shown(response):
            self.viewing = False
            if not self.app.window:
                return
            if not response.get("ok"):
                self.view_status.set_text(response.get("error", "Leo no responde"))
                return
            theme = self.app.theme
            self.view.set_markup("\n".join(segments_to_pango([tuple(seg) for seg in line], theme)
                                           for line in response["lines"]))
            status = "".join(text for text, _ in response["status"])
            self.view_status.set_text(status + ("" if response.get("live") else " · cuotas en vivo apagadas en Privacidad"))
        self.client.call_async({"op": "ai", "action": "snapshot", "args": {"width": 66, "refresh": refresh}}, shown)
        if refresh or not getattr(self, "_gray_checked", False):
            self._gray_checked = True
            self.client.call_async({"op": "ai", "action": "grayscale", "args": {}}, self._gray_state)

    def _gray_state(self, response):
        if not response.get("ok") or not self.app.window:
            return
        self.gray.set_sensitive(response["available"])
        self._gray_sync = True
        self.gray.set_active(response["grayscale"])
        self._gray_sync = False

    def _grayscale(self, toggle):
        if not self._gray_sync:
            self.client.call_async({"op": "ai", "action": "grayscale", "args": {"on": toggle.get_active()}}, self._gray_state)

    def call(self, action, args, callback=None):
        if self.busy:
            self.app.status_label.set_text("Hay una consulta de IA en curso. Espera a que termine.")
            return False
        self.busy = True
        self.app.status_label.set_text("Consultando… puedes cerrar el panel en cualquier momento.")
        def done(response):
            self.busy = False
            if not response.get("ok"):
                self.app.status_label.set_text(response.get("error", "No se completó la operación"))
                if self.feedback:
                    self.feedback.set_text(response.get("error", "No se completó la operación"))
                if action in {"search", "preview", "skills", "install"}:
                    self.skills_status.set_text(response.get("error", "Consulta fallida"))
                return
            self.app.status_label.set_text(response.get("message", "Listo · las respuestas de Leo siguen siendo locales"))
            if callback:
                callback(response)
        self.client.call_async({"op": "ai", "action": action, "args": args}, done)
        return True

    def load(self):
        def loaded(response):
            self.accounts = response["accounts"]
            self.providers = response["providers"]
            self.status.set_text(response["warning"] or "Pulsa Consultar uso en una cuenta para obtener una lectura nueva. Las cuotas antiguas conservan su fecha.")
            self.render_accounts()
            if not self.loaded:
                self.loaded = True
                self.load_skills()
        self.call("accounts", {}, loaded)

    def render_accounts(self):
        clear(self.usage_list)
        clear(self.account_list)
        for account in self.accounts:
            if not account["hidden"]:
                card = box(spacing=8)
                card.add_css_class("card")
                card.append(label(f"{account['provider']} / {account['name']}", "section-title"))
                details = " · ".join(v for v in (account.get("email"), account.get("plan")) if v)
                if details:
                    card.append(label(details, "muted"))
                card.append(label(("Lectura consultada" if account.get("source") == "live" else "Última lectura guardada") + " · " + date(account.get("fetched_at")), "muted"))
                for window in account.get("windows", []):
                    expired = bool(window.get("resets_at") and window["resets_at"] <= time.time())
                    progress = Gtk.ProgressBar(show_text=True, fraction=window["used"] / 100)
                    progress.set_text(f"{window['label']}  {window['used']:.0f}% usado" + (" · período vencido" if expired else ""))
                    card.append(progress)
                    if window.get("resets_at"):
                        card.append(label("Reinicio informado: " + date(window["resets_at"]), "muted"))
                for name, value in account.get("balances", []):
                    card.append(label(f"{name}: {value}"))
                if not account.get("windows") and not account.get("balances"):
                    card.append(label("Sin cuotas disponibles. No equivale a consumo cero.", "muted"))
                if account.get("auth") == "nologin":
                    card.append(label("Sin sesión iniciada", "error"))
                for key in ("error", "note"):
                    if account.get(key):
                        card.append(label(account[key], "muted"))
                card.append(button("Consultar uso" if account["provider"] != "claude" else "Comprobar sesión", lambda a=account: self.refresh(a)))
                self.usage_list.append(card)
            card = box(spacing=8)
            card.add_css_class("card")
            card.append(label(f"{account['name']} · {account['provider']}" + (" · oculta" if account["hidden"] else ""), "section-title"))
            card.append(label(account["path"] + (" · " + account.get("region", "") if account["kind"] == "key" else ""), "muted"))
            row = box(False)
            row.append(button("Editar", lambda a=account: self.edit(a)))
            if account["kind"] == "profile":
                row.append(button("Iniciar sesión", lambda a=account: self.call("login", {"id": a["id"], "action": "login"})))
                row.append(button("Abrir", lambda a=account: self.call("login", {"id": a["id"], "action": "open"})))
                row.append(button("Cerrar sesión", lambda a=account: self.confirm("Cerrar sesión", f"Cerrar la sesión de {a['name']} usando su herramienta oficial.",
                                                                                 lambda: self.call("login", {"id": a["id"], "action": "logout"}))))
            elif account.get("managed"):
                row.append(button("Eliminar clave", lambda a=account: self.confirm("Eliminar clave", f"Eliminar del llavero la clave de {a['name']}.",
                                                                                    lambda: self.call("key_remove", {"id": a["id"]}, lambda _: self.load()))))
            card.append(row)
            self.account_list.append(card)
        if not self.accounts:
            self.account_list.append(label("Añade un perfil o una clave API para empezar.", "hint"))
        if not any(not a["hidden"] for a in self.accounts):
            self.usage_list.append(label("No hay cuentas visibles. Abre Cuentas para añadirlas o mostrarlas.", "hint"))

    def refresh(self, account):
        def loaded(response):
            self.accounts = [response["account"] if a["id"] == account["id"] else a for a in self.accounts]
            self.render_accounts()
        self.call("refresh", {"id": account["id"]}, loaded)

    def dialog(self, title):
        dialog = Gtk.Window(title=title, transient_for=self.app.window, modal=True, default_width=620, default_height=440)
        self.app.dialogs.add(dialog)
        content = box(spacing=14)
        for side in ("top", "bottom", "start", "end"):
            getattr(content, "set_margin_" + side)(22)
        content.append(label(title, "section-title"))
        feedback = label("", "error")
        content.append(feedback)
        self.feedback = feedback
        scroll = Gtk.ScrolledWindow(vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
        scroll.set_child(content)
        dialog.set_child(scroll)
        def close(*_):
            if self.feedback is feedback:
                self.feedback = None
            self.app.dialogs.discard(dialog)
            dialog.destroy()
            return True
        dialog.connect("close-request", close)
        dialog.present()
        return content, close

    def confirm(self, title, description, callback):
        content, close = self.dialog(title)
        content.append(label(description))
        row = box(False)
        row.append(button("Cancelar", close))
        row.append(button("Confirmar", lambda: (close(), callback())))
        content.append(row)

    def edit(self, account):
        content, close = self.dialog("Editar cuenta")
        name = Gtk.Entry(text=account["name"], max_length=100)
        content.append(name)
        hidden = Gtk.CheckButton(label="Ocultar de Uso de IA", active=account["hidden"])
        content.append(hidden)
        content.append(button("Guardar", lambda: self.call("rename", {"id": account["id"], "name": name.get_text(), "hidden": hidden.get_active()},
                                                           lambda _: (close(), self.load()))))
        content.append(button("Cancelar", close))

    def add_profile(self):
        content, close = self.dialog("Añadir cuenta / perfil")
        provider = Gtk.DropDown.new_from_strings(["Codex", "Claude Code", "Antigravity"])
        name = Gtk.Entry(placeholder_text="Nombre, por ejemplo trabajo", max_length=31)
        path = Gtk.Entry(placeholder_text="Carpeta existente (vacío para crear un perfil nuevo)")
        for widget in (provider, name, path):
            content.append(widget)
        content.append(label("Un perfil nuevo tiene su propia sesión. Después pulsa Iniciar sesión; Leo no accede a los archivos de credenciales.", "hint"))
        content.append(button("Añadir perfil", lambda: self.call("profile", {"provider": ("codex", "claude", "antigravity")[provider.get_selected()],
                                                                                "name": name.get_text(), "path": path.get_text()}, lambda _: (close(), self.load()))))
        content.append(button("Cancelar", close))

    def add_key(self):
        if not self.providers:
            self.app.status_label.set_text("Espera a que se cargue la lista de cuentas")
            return
        content, close = self.dialog("Añadir clave API")
        provider = Gtk.DropDown.new_from_strings([p["title"] for p in self.providers])
        region = Gtk.DropDown()
        def regions(*_):
            region.set_model(Gtk.StringList.new(self.providers[provider.get_selected()]["regions"]))
            region.set_selected(0)
        provider.connect("notify::selected", regions)
        regions()
        name = Gtk.Entry(placeholder_text="Nombre de esta cuenta", max_length=100)
        key = Gtk.PasswordEntry(show_peek_icon=True)
        for text, widget in (("Proveedor", provider), ("Región de tu clave", region), ("Nombre", name), ("Clave API", key)):
            content.append(label(text, "muted"))
            content.append(widget)
        content.append(label("Solo se guarda en el llavero de GNOME. Consultar uso enviará la clave al proveedor y región elegidos para leer saldo o cuota.", "hint"))
        def save():
            p = self.providers[provider.get_selected()]
            args = {"provider": p["id"], "region": p["regions"][region.get_selected()], "key": key.get_text(), "name": name.get_text()}
            if self.call("key_add", args, lambda _: (close(), self.load())):
                key.set_text("")
        content.append(button("Guardar en el llavero", save))
        content.append(button("Cancelar", lambda: (key.set_text(""), close())))

    def load_skills(self):
        def loaded(response):
            self.targets, self.installed = response["targets"], response["skills"]
            self.render_skills()
        self.call("skills", {}, loaded)

    def render_skills(self):
        clear(self.skill_list)
        query = self.query.get_text().casefold()
        targets = {t["id"]: t["name"] for t in self.targets}
        visible = [s for s in self.installed if query in (s["name"] + " " + s["description"] + " " + " ".join(s["paths"])).casefold()]
        self.skills_status.set_text(f"{len(visible)} de {len(self.installed)} skills instaladas · {len(self.targets)} destinos")
        for skill in visible:
            card = box(spacing=5)
            card.add_css_class("card")
            card.append(label(skill["name"], "section-title"))
            card.append(label(skill["description"][:600], "muted"))
            card.append(label(" · ".join(targets.get(k, k) for k in skill["paths"]), "muted"))
            card.append(button("Ver ubicación", lambda s=skill: self.show_locations(s)))
            self.skill_list.append(card)

    def show_locations(self, skill):
        content, close = self.dialog(skill["name"])
        content.append(label(skill["description"]))
        for path in skill["paths"].values():
            content.append(label(path, "muted"))
        content.append(label("Origen: " + (skill["source"] or "carpeta local"), "muted"))
        content.append(button("Cerrar", close))

    def search(self):
        def loaded(response):
            clear(self.search_results)
            self.skills_status.set_text(f"{len(response['results'])} resultados en línea")
            for result in response["results"]:
                self.search_results.append(button(f"Revisar {result['name']} · {result['source']}",
                                                  lambda r=result: self.call("preview", {"id": r["id"]}, self.preview)))
        self.call("search", {"query": self.search_entry.get_text()}, loaded)

    def preview(self, response):
        content, close = self.dialog("Revisar skill · " + response["name"])
        content.append(label(response["source"], "muted"))
        content.append(label(f"{len(response['files'])} archivos · revisa las instrucciones antes de instalar", "hint"))
        view = Gtk.TextView(editable=False, monospace=True, wrap_mode=Gtk.WrapMode.WORD_CHAR)
        view.get_buffer().set_text(response["text"] + "\n\nARCHIVOS\n" + "\n".join(response["files"]))
        scroll = Gtk.ScrolledWindow(min_content_height=220)
        scroll.set_child(view)
        content.append(scroll)
        writable = [t for t in self.targets if not t.get("readonly")]
        if writable:
            target = Gtk.DropDown.new_from_strings([t["name"] + " · " + t["path"] for t in writable])
            content.append(target)
            content.append(button("Instalar en este destino", lambda: self.call("install", {"token": response["token"], "target": writable[target.get_selected()]["id"]},
                                                                                  lambda _: (close(), self.load_skills()))))
        content.append(button("Cancelar", close))
