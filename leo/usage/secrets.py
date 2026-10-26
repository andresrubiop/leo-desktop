"""Claves exclusivamente en Secret Service. Listar no desbloquea ni lee secretos."""
import threading
import uuid

APP = "io.github.leo.Companion"
LEGACY = "io.github.andresrubiop.AiUsageWidget"


class Keyring:
    def __init__(self):
        import gi
        gi.require_version("Secret", "1")
        from gi.repository import Secret, Gio
        self.S, self.Gio = Secret, Gio

    def schema(self, app):
        return self.S.Schema.new(app + ".ApiKey", self.S.SchemaFlags.NONE,
                                 {k: self.S.SchemaAttributeType.STRING for k in ("app", "id", "provider", "region")})

    def call(self, method, *args):
        cancel = self.Gio.Cancellable()
        timer = threading.Timer(30, cancel.cancel)
        timer.daemon = True
        timer.start()
        try:
            return method(*args, cancel)
        except Exception:
            raise RuntimeError("Llavero no disponible o bloqueado; desbloquéalo para continuar.") from None
        finally:
            timer.cancel()

    def list(self):
        out = []
        for app in (APP, LEGACY):
            items = self.call(self.S.password_search_sync, self.schema(app), {"app": app}, self.S.SearchFlags.ALL)
            for item in items:
                attrs = item.get_attributes()
                out.append({k: attrs.get(k, "") for k in ("app", "id", "provider", "region")})
        return out

    def save(self, provider, region, key):
        attrs = {"app": APP, "id": uuid.uuid4().hex, "provider": provider, "region": region}
        if not self.call(self.S.password_store_sync, self.schema(APP), attrs, self.S.COLLECTION_DEFAULT,
                         f"Leo · {provider} · {region}", key):
            raise RuntimeError("El llavero no guardó la clave")
        return attrs

    def get(self, attrs):
        key = self.call(self.S.password_lookup_sync, self.schema(attrs["app"]), attrs)
        if not key:
            raise RuntimeError("Clave no disponible en el llavero")
        return key

    def remove(self, attrs):
        if attrs["app"] != APP:
            raise ValueError("Las claves del widget original se conservan. Puedes ocultar esta cuenta en Leo.")
        self.call(self.S.password_clear_sync, self.schema(APP), attrs)
