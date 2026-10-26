"""Conexiones de correo configurables por cuenta y avisos opt-in."""
import shutil
import subprocess
import threading

from gi.repository import Gtk

from .ipc import Client
from .ui import box, button, label
from .usage_ui import clear, date


class MailUI:
    def __init__(self, app):
        self.app = app
        self.client = Client(private=True)
        self.busy = False
        self.page = app._page("mail", "Correo")
        app._heading(self.page, "Leo cuida tu buzón", "Gmail, Outlook / Microsoft 365 y otros servidores IMAP · cuentas independientes")
        self.page.append(label("Conecta tus cuentas cuando quieras. Activa la revisión y los avisos por cuenta. Leo consulta la bandeja de entrada sin marcar mensajes como leídos.", "hint"))
        row = box(False)
        row.append(button("Conectar Google / Microsoft", self.online_accounts))
        row.append(button("Añadir IMAP", self.add))
        row.append(button("Recargar cuentas", self.load))
        self.page.append(row)
        self.page.append(label("Google y Microsoft usan el acceso oficial de Cuentas en línea de GNOME. Añade la cuenta, habilita Correo allí y vuelve a Recargar cuentas. También puedes conectar IMAP con una contraseña de aplicación.", "muted"))
        self.list = box(spacing=14)
        self.page.append(self.list)
        app.stack.connect("notify::visible-child-name", lambda *_: self.load() if app.stack.get_visible_child_name() == "mail" else None)

    def close(self):
        self.client.close()

    def call(self, action, args, callback=None):
        if self.busy:
            self.app.status_label.set_text("La consulta de correo sigue en curso")
            return False
        self.busy = True
        self.app.status_label.set_text("Consultando correo…")
        def done(response):
            self.busy = False
            self.app.status_label.set_text(response.get("message", "Correo actualizado") if response.get("ok") else response.get("error", "No se pudo completar"))
            if not response.get("ok") and self.app.usage_ui.feedback:
                self.app.usage_ui.feedback.set_text(response.get("error", "No se pudo completar"))
            if response.get("ok") and callback:
                callback(response)
        self.client.call_async({"op": "mail", "action": action, "args": args}, done)
        return True

    def load(self):
        self.call("accounts", {}, self.render)

    def online_accounts(self):
        executable = shutil.which("gnome-control-center")
        if not executable:
            self.app.status_label.set_text("No se encontró Configuración de GNOME; puedes añadir una cuenta IMAP")
            return
        child = subprocess.Popen([executable, "online-accounts"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        threading.Thread(target=child.wait, daemon=True).start()

    def render(self, response):
        clear(self.list)
        if not response["accounts"]:
            self.list.append(label("Todavía no hay cuentas conectadas. Puedes añadir tantas como necesites.", "hint"))
        for account in response["accounts"]:
            card = box(spacing=10)
            card.add_css_class("card")
            card.append(label(account["name"], "section-title"))
            card.append(label(account.get("host") or account.get("provider", ""), "muted"))
            enabled = Gtk.CheckButton(label="Revisar correo", active=account["enabled"])
            notify = Gtk.CheckButton(label="Avisarme de mensajes nuevos", active=account["notify"])
            interval = Gtk.SpinButton.new_with_range(1, 60, 1)
            interval.set_value(account["interval"] / 60)
            row = box(False)
            for widget in (enabled, notify, label("Cada (min):"), interval):
                row.append(widget)
            card.append(row)
            row = box(False)
            row.append(button("Guardar", lambda a=account, e=enabled, n=notify, i=interval: self.call("configure",
                              {"id": a["id"], "enabled": e.get_active(), "notify": n.get_active(), "interval": i.get_value_as_int() * 60}, lambda _: self.load())))
            row.append(button("Consultar ahora", lambda a=account: self.call("check", {"id": a["id"]}, lambda _: self.load())))
            if account["kind"] != "goa":
                row.append(button("Eliminar", lambda a=account: self.app.usage_ui.confirm("Retirar correo", f"Quitar {a['name']} de Leo y borrar su contraseña del llavero.",
                                   lambda: self.call("remove", {"id": a["id"]}, lambda _: self.load()))))
            card.append(row)
            status = account.get("status", {})
            card.append(label(f"{status.get('unread', '—')} sin leer · última lectura: {date(status.get('checked_at'))}", "muted"))
            if status.get("error") or account.get("note"):
                card.append(label(status.get("error") or account["note"], "error"))
            for message in status.get("messages", []):
                card.append(label(message["from"] + "\n" + message["subject"], "muted"))
            self.list.append(card)

    def add(self):
        content, close = self.app.usage_ui.dialog("Añadir correo IMAP")
        name = Gtk.Entry(placeholder_text="Nombre de la cuenta", max_length=100)
        username = Gtk.Entry(placeholder_text="Correo / usuario IMAP", max_length=300)
        host = Gtk.Entry(placeholder_text="Servidor, por ejemplo imap.gmail.com", max_length=253)
        port = Gtk.SpinButton.new_with_range(1, 65535, 1)
        port.set_value(993)
        password = Gtk.PasswordEntry(show_peek_icon=True)
        for title, widget in (("Nombre", name), ("Usuario", username), ("Servidor TLS", host), ("Puerto", port), ("Contraseña de aplicación / IMAP", password)):
            content.append(label(title, "muted"))
            content.append(widget)
        content.append(label("La contraseña se guarda en el llavero de GNOME. Se verifica el certificado TLS. Para Microsoft usa Conectar Google / Microsoft, que permite OAuth.", "hint"))
        def save():
            args = {"name": name.get_text(), "username": username.get_text(), "host": host.get_text(),
                    "port": port.get_value_as_int(), "password": password.get_text()}
            if self.call("add", args, lambda _: (close(), self.load())):
                password.set_text("")
        content.append(button("Guardar cuenta", save))
        content.append(button("Cancelar", lambda: (password.set_text(""), close())))
