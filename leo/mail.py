"""Correo opt-in: GOA OAuth / IMAP TLS, solo lectura, avisos por cuenta.

No envía, borra ni marca mensajes como leídos. Secretos fuera de los JSON.
"""
import email
import email.policy
import imaplib
import json
import re
import ssl
import threading
import time
import uuid
from urllib.parse import urlsplit

from gi.repository import Gio, GLib

from .config import config_dir
from .usage.net import http_json
from .usage.secrets import Keyring, APP
from .usage.storage import read, write

GOA = "org.gnome.OnlineAccounts"


class MailSecrets(Keyring):
    def schema(self, app=APP):
        return self.S.Schema.new(APP + ".Mail", self.S.SchemaFlags.NONE,
                                 {"id": self.S.SchemaAttributeType.STRING})

    def save_password(self, ident, password):
        if not self.call(self.S.password_store_sync, self.schema(), {"id": ident}, self.S.COLLECTION_DEFAULT,
                         "Leo · correo IMAP", password):
            raise RuntimeError("El llavero no guardó la contraseña")

    def password(self, ident):
        value = self.call(self.S.password_lookup_sync, self.schema(), {"id": ident})
        if not value:
            raise RuntimeError("Contraseña no disponible; vuelve a configurar la cuenta")
        return value

    def delete(self, ident):
        self.call(self.S.password_clear_sync, self.schema(), {"id": ident})


def imap_read(account, secret, previous, oauth=False, context=None):
    context = context or ssl.create_default_context()
    if account.get("starttls"):
        server = imaplib.IMAP4(account["host"], account["port"], timeout=12)
        try:
            server.starttls(context)
        except Exception:
            server.shutdown()
            raise
    else:
        server = imaplib.IMAP4_SSL(account["host"], account["port"], ssl_context=context, timeout=12)
    try:
        if oauth:
            auth = f"user={account['username']}\x01auth=Bearer {secret}\x01\x01".encode()
            sent = False
            def authenticate(_challenge):
                nonlocal sent
                if sent:
                    return b""
                sent = True
                return auth
            server.authenticate("XOAUTH2", authenticate)
        else:
            server.login(account["username"], secret)
        status, _ = server.select("INBOX", readonly=True)
        if status != "OK":
            raise RuntimeError("No se pudo abrir INBOX")
        validity = (server.response("UIDVALIDITY")[1] or [b""])[0].decode()
        # Snapshot UIDNEXT before SEARCH so an arrival between the two calls is
        # not accidentally acknowledged without appearing in a notification.
        status, next_data = server.status("INBOX", "(UIDNEXT)")
        match = re.search(rb"UIDNEXT (\d+)", next_data[0] or b"") if status == "OK" else None
        if not match:
            raise RuntimeError("El servidor no informó el marcador del buzón")
        marker = int(match[1]) - 1
        status, data = server.uid("search", None, "UNSEEN")
        if status != "OK":
            raise RuntimeError("No se pudo consultar el buzón")
        uids = [int(v) for v in (data[0] or b"").split()]
        baseline = previous.get("uidvalidity") != validity or "marker" not in previous
        new = [] if baseline else [uid for uid in uids if previous["marker"] < uid <= marker]
        messages = []
        # Headers only and BODY.PEEK keep unread flags untouched; cap returned bytes.
        for uid in sorted(uids, reverse=True)[:10]:
            status, parts = server.uid("fetch", str(uid), "(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE)]<0.8192>)")
            if status != "OK":
                continue
            raw = next((p[1] for p in parts if isinstance(p, tuple)), b"")[:8192]
            parsed = email.message_from_bytes(raw, policy=email.policy.default)
            messages.append({"id": str(uid), "from": str(parsed.get("From", ""))[:300],
                             "subject": str(parsed.get("Subject", "Sin asunto"))[:500], "date": str(parsed.get("Date", ""))[:100]})
        return {"unread": len(uids), "new": len(new), "messages": messages, "uidvalidity": validity, "marker": marker}
    finally:
        try:
            server.logout()
        except Exception:
            server.shutdown()


class Mail:
    def __init__(self, bus, directory=None, secrets=None):
        self.bus = bus
        self.directory = directory or config_dir()
        self.path = self.directory / "mail.json"
        self.cache_path = self.directory / "mail-state.json"
        self.lock = threading.RLock()
        self.secrets = secrets
        self.stop = threading.Event()
        self.check_lock = threading.Lock()
        self.worker = None

    def store(self):
        if self.secrets is None:
            self.secrets = MailSecrets()
        return self.secrets

    def goa_call(self, path, interface, method, args=None):
        return self.bus.call_sync(GOA, path, GOA + "." + interface, method, args, None,
                                  Gio.DBusCallFlags.NONE, 20000, None).unpack()

    def discover(self):
        try:
            objects = self.bus.call_sync(GOA, "/org/gnome/OnlineAccounts", "org.freedesktop.DBus.ObjectManager",
                                         "GetManagedObjects", None, None, Gio.DBusCallFlags.NONE, 5000, None).unpack()[0]
        except GLib.Error:
            return []
        result = []
        for path, interfaces in objects.items():
            account = interfaces.get(GOA + ".Account", {})
            mail = interfaces.get(GOA + ".Mail", {})
            provider = account.get("ProviderType", "")
            if not mail or account.get("MailDisabled"):
                continue
            item = {"id": "goa:" + path, "kind": "goa", "path": path, "provider": provider,
                    "name": account.get("PresentationIdentity", "Correo GNOME"),
                    "username": mail.get("ImapUserName") or mail.get("EmailAddress"),
                    "oauth": GOA + ".OAuth2Based" in interfaces}
            if provider == "ms_graph" and item["oauth"]:
                item["mode"] = "graph"
            elif mail.get("ImapSupported") and (mail.get("ImapUseSsl") or mail.get("ImapUseTls")):
                parsed = urlsplit("//" + mail.get("ImapHost", ""))
                item.update(mode="imap", host=parsed.hostname, port=parsed.port or (993 if mail.get("ImapUseSsl") else 143),
                            starttls=not mail.get("ImapUseSsl"))
            else:
                item.update(mode="unsupported", note="Esta cuenta no expone IMAP seguro ni Microsoft Graph. Configura IMAP en Cuentas en línea.")
            result.append(item)
        return result

    def accounts(self):
        with self.lock:
            config = read(self.path)
            state = read(self.cache_path)
        manual = config.get("accounts", [])
        accounts = self.discover() + manual
        options = config.get("options", {})
        for account in accounts:
            account.update({"enabled": False, "notify": True, "interval": 120} | options.get(account["id"], {}))
            account["status"] = state.get(account["id"], {})
        return {"accounts": accounts}

    def find(self, ident):
        for account in self.accounts()["accounts"]:
            if account["id"] == ident:
                return account
        raise ValueError("Cuenta de correo no encontrada")

    def configure(self, ident, enabled, notify, interval):
        account = self.find(ident)
        if type(enabled) is not bool or type(notify) is not bool or type(interval) is not int or not 60 <= interval <= 3600:
            raise ValueError("Ajustes de correo inválidos; intervalo entre 60 y 3600 segundos")
        if enabled and account["mode"] == "unsupported":
            raise ValueError(account["note"])
        with self.lock:
            config = read(self.path)
            config.setdefault("options", {})[ident] = {"enabled": enabled, "notify": notify, "interval": interval}
            write(self.path, config)
        return {"message": "Correo configurado. La primera lectura establece el estado sin avisar por mensajes antiguos."}

    def add(self, name, host, port, username, password):
        if (not all(isinstance(v, str) and v.strip() for v in (name, host, username, password))
                or len(name) > 100 or len(username) > 300 or len(password) > 4096
                or not re.fullmatch(r"[a-zA-Z0-9.-]{1,253}", host) or type(port) is not int or not 1 <= port <= 65535
                or any(c in username for c in "\r\n\x00")):
            raise ValueError("Datos de correo inválidos")
        ident = uuid.uuid4().hex
        self.store().save_password(ident, password)
        account = {"id": ident, "kind": "imap", "mode": "imap", "name": name.strip(), "host": host,
                   "port": port, "username": username, "starttls": False, "oauth": False}
        with self.lock:
            config = read(self.path)
            config.setdefault("accounts", []).append(account)
            write(self.path, config)
        return {"message": "Cuenta guardada en el llavero. Activa Revisar correo para conectar y recibir avisos."}

    def remove(self, ident):
        account = self.find(ident)
        if account["kind"] == "goa":
            raise ValueError("Desactiva la revisión aquí o elimina la cuenta desde Cuentas en línea de GNOME")
        with self.check_lock:
            self.store().delete(ident)
            with self.lock:
                config, state = read(self.path), read(self.cache_path)
                config["accounts"] = [a for a in config.get("accounts", []) if a["id"] != ident]
                config.get("options", {}).pop(ident, None)
                state.pop(ident, None)
                write(self.path, config)
                write(self.cache_path, state)
        return {"message": "Cuenta retirada de Leo y contraseña eliminada del llavero"}

    def check(self, ident):
        with self.check_lock:
            account = self.find(ident)
            if not account["enabled"]:
                raise ValueError("Activa Revisar correo en esta cuenta antes de consultar")
            with self.lock:
                previous = read(self.cache_path).get(ident, {})
            try:
                if account["kind"] == "goa":
                    self.goa_call(account["path"], "Account", "EnsureCredentials")
                    secret = (self.goa_call(account["path"], "OAuth2Based", "GetAccessToken")[0] if account["oauth"]
                              else self.goa_call(account["path"], "PasswordBased", "GetPassword", GLib.Variant("(s)", ("imap-password",)))[0])
                else:
                    secret = self.store().password(ident)
                result = self.graph(secret, previous) if account["mode"] == "graph" else imap_read(account, secret, previous, account["oauth"])
                result.update(checked_at=time.time(), attempted_at=time.time(), error="")
            except Exception:
                result = previous | {"new": 0, "attempted_at": time.time(),
                                     "error": "No se pudo consultar el correo. Revisa conexión, permisos IMAP y acceso de la cuenta en su proveedor."}
            # A concurrent revocation wins over an in-flight read and suppresses notifications.
            with self.lock:
                config = read(self.path)
                opts = config.get("options", {}).get(ident, {})
                if not opts.get("enabled"):
                    return {"message": "Revisión desactivada"}
                state = read(self.cache_path)
                state[ident] = result
                write(self.cache_path, state)
            if result.get("new") and opts.get("notify") and not self.stop.is_set():
                self.notify(account["name"], result["new"])
            return {"status": result}

    @staticmethod
    def graph(token, previous):
        headers = {"Authorization": "Bearer " + token}
        base = "https://graph.microsoft.com/v1.0/me/mailFolders/inbox"
        count = http_json(base + "?$select=unreadItemCount", headers)
        # Graph requires orderby fields to appear first in filter as well.
        data = http_json(base + "/messages?$filter=receivedDateTime%20ge%201970-01-01T00:00:00Z%20and%20isRead%20eq%20false&$select=id,from,subject,receivedDateTime&$orderby=receivedDateTime%20desc&$top=100", headers)
        messages = [{"id": m["id"], "from": m.get("from", {}).get("emailAddress", {}).get("address", "")[:300],
                     "subject": m.get("subject", "")[:500], "date": m.get("receivedDateTime", "")}
                    for m in data.get("value", [])]
        marker = max([previous.get("graph_marker", "")] + [m["date"] for m in messages])
        seen = set(previous.get("graph_ids", []))
        new = sum(m["date"] >= previous["graph_marker"] and m["id"] not in seen for m in messages) if previous.get("graph_marker") else 0
        return {"unread": count.get("unreadItemCount", 0), "new": new, "messages": messages[:10],
                "graph_marker": marker or "1970-01-01T00:00:00Z", "graph_ids": [m["id"] for m in messages],
                "limited": bool(data.get("@odata.nextLink"))}

    def notify(self, name, count):
        try:
            self.bus.call_sync("org.freedesktop.Notifications", "/org/freedesktop/Notifications", "org.freedesktop.Notifications",
                               "Notify", GLib.Variant("(susssasa{sv}i)", ("Leo", 0, "mail-unread-symbolic", "Leo · correo nuevo",
                               f"{GLib.markup_escape_text(name)}: {count} mensaje(s) nuevo(s) sin leer.", [],
                               {"desktop-entry": GLib.Variant("s", "io.github.leo.Companion")}, -1)), None,
                               Gio.DBusCallFlags.NONE, 5000, None)
        except GLib.Error:
            pass

    def summary(self):
        with self.lock:
            options, state = read(self.path).get("options", {}), read(self.cache_path)
        statuses = [state[ident] for ident, opts in options.items() if opts.get("enabled") and ident in state]
        return {"unread": sum(s.get("unread", 0) for s in statuses), "errors": sum(bool(s.get("error")) for s in statuses),
                "connected": sum(bool(opts.get("enabled")) for opts in options.values())}

    def context(self):
        values = []
        for account in self.accounts()["accounts"]:
            if account["enabled"]:
                values.append({"cuenta": account["name"], **account["status"]})
        return json.dumps(values, ensure_ascii=False)[:16000] if values else "No hay correos conectados. El usuario puede añadirlos y activarlos en Leo → Correo."

    def refresh_enabled(self):
        deadline = time.monotonic() + 60
        for account in self.accounts()["accounts"]:
            if account["enabled"] and time.monotonic() < deadline:
                self.check(account["id"])

    def start(self):
        def work():
            while not self.stop.wait(10):
                with self.lock:
                    options = read(self.path).get("options", {})
                    state = read(self.cache_path)
                for ident, opts in options.items():
                    if self.stop.is_set():
                        return
                    if opts.get("enabled") and time.time() - state.get(ident, {}).get("attempted_at", 0) >= opts.get("interval", 120):
                        try:
                            self.check(ident)
                        except Exception:
                            pass
        self.worker = threading.Thread(target=work, daemon=True, name="leo-mail")
        self.worker.start()

    def close(self):
        self.stop.set()

    def dispatch(self, action, args):
        methods = {"accounts": (self.accounts, ()), "configure": (self.configure, ("id", "enabled", "notify", "interval")),
                   "add": (self.add, ("name", "host", "port", "username", "password")),
                   "remove": (self.remove, ("id",)), "check": (self.check, ("id",))}
        if action not in methods or not isinstance(args, dict):
            raise ValueError("Operación de correo inválida")
        method, keys = methods[action]
        if set(args) != set(keys):
            raise ValueError("Argumentos de correo inválidos")
        return method(*(args[k] for k in keys))
