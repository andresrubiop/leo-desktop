"""Centro de IA de Leo: metadatos, cuotas, cuentas y catálogo de skills.

Las operaciones de este módulo nunca solicitan respuestas a modelos.
F7.3 (G18, 2026-10-23): las cuotas de Claude se consultan en vivo como el AI Usage Widget (el token de sesión de
Claude Code, leído solo en memoria, para un GET de uso a api.anthropic.com; nunca se guarda, renueva ni reenvía) y
las de Antigravity desde su app local (127.0.0.1). `live()` (ajuste `usage_live`) apaga todas las consultas en vivo.
"""
from dataclasses import asdict
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import threading
import time
import uuid

from ..config import config_dir
from . import api, codex, skills
from .profiles import discover
from .secrets import Keyring, APP
from .storage import read, write


BACKOFF = 15 * 60  # s sin consultar una cuenta tras un HTTP 429, como el widget
FRESH = 600        # s: una lectura más reciente cuenta como «en vivo»


def clean_usage(value):
    """Allowlist the old cache; discard credentials, commands and unknown fields."""
    if not isinstance(value, dict):
        return {}
    out = {k: str(value[k])[:500] for k in ("email", "plan", "auth") if k in value}
    for k in ("fetched_at",):
        v = value.get(k)
        if type(v) in (int, float) and math.isfinite(v) and v > 0:
            out[k] = v
    out["windows"] = []
    for item in (value.get("windows") or [])[:40]:
        if not isinstance(item, dict):
            continue
        used = item.get("used")
        if type(used) not in (float, int) or not math.isfinite(used):
            continue
        window = {"label": str(item.get("label", "Cuota"))[:100], "used": max(0, min(100, used))}
        reset = item.get("resets_at")
        if type(reset) in (int, float) and math.isfinite(reset) and reset > 0:
            window["resets_at"] = reset
        for key in ("used_usd", "limit_usd"):  # Dollar credits of a Claude account.
            amount = item.get(key)
            if type(amount) in (int, float) and math.isfinite(amount) and amount >= 0:
                window[key] = amount
        out["windows"].append(window)
    out["balances"] = [[str(x)[:120] for x in item] for item in (value.get("balances") or [])[:20]
                       if isinstance(item, (list, tuple)) and len(item) == 2]
    out["notes"] = [str(note)[:200] for note in (value.get("notes") or [])[:5] if isinstance(note, str)]
    return out


def from_widget(account):
    """A reading of the widget's providers (`Account`) as Leo's cache entry."""
    value = clean_usage({"email": account.email, "plan": account.plan, "auth": account.auth,
                         "fetched_at": account.fetched_at or 0, "notes": account.notes,
                         "windows": [{"label": w.label, "used": w.used, "resets_at": w.resets_at,
                                      "used_usd": w.used_usd, "limit_usd": w.limit_usd} for w in account.windows]})
    return value | {"source": account.source}


class Hub:
    def __init__(self, home=None, directory=None, keyring=None, live=None):
        self.home = home or Path.home()
        self.live = live or (lambda: True)
        self.backoff = {}
        self.directory = directory or config_dir()
        self.meta_path = self.directory / "ai-accounts.json"
        self.cache_path = self.directory / "ai-usage.json"
        self.lock = threading.RLock()
        self.keyring = keyring
        self.key_refs = {}
        self.previews = {}
        self.results = {}

    def store(self):
        if self.keyring is None:
            self.keyring = Keyring()
        return self.keyring

    def accounts(self):
        config = Path(os.environ.get("XDG_CONFIG_HOME", self.home / ".config"))
        cache = Path(os.environ.get("XDG_CACHE_HOME", self.home / ".cache"))
        old = read(cache / "ai-usage-widget/usage.json").get("accounts", {})
        own = read(self.cache_path)
        meta = read(self.meta_path)
        names = read(config / "ai-usage-widget/accounts.json").get("labels", {}) | meta.get("labels", {})
        hidden = meta.get("hidden", [])
        profiles = discover(self.home)
        accounts = [{"id": p.key, "provider": p.provider, "name": p.name, "path": str(p.path), "kind": "profile"}
                    for p in profiles]
        warning = ""
        try:
            refs = self.store().list()
        except Exception:
            refs = []
            warning = "El llavero no está disponible. Las claves se guardan únicamente en Secret Service."
        self.key_refs = {}
        for item in refs:
            provider = item.get("provider")
            if provider not in api.PROVIDERS or not item.get("id"):
                continue
            ident = f"key:{item['app']}:{provider}:{item['id']}"
            self.key_refs[ident] = item
            accounts.append({"id": ident, "provider": provider, "name": api.PROVIDERS[provider].title,
                             "path": "Llavero de GNOME", "kind": "key", "region": item.get("region", ""),
                             "managed": item["app"] == APP, "legacy_id": f"api:{provider}:{item['id']}"})
        for account in accounts:
            ident = account["id"]
            account["name"] = names.get(ident, account["name"])
            account["hidden"] = ident in hidden
            legacy = old.get(account.get("legacy_id", ident), {})
            stored = own.get(ident, {})
            if legacy.get("fetched_at", 0) and legacy.get("fetched_at", 0) > (stored.get("fetched_at") or 0):
                stored = legacy
            account.update(clean_usage(stored))
            account["source"] = "cache" if account.get("fetched_at") else "none"
            if isinstance(own.get(ident), dict) and own[ident].get("error"):
                account["error"] = own[ident]["error"]
            fresh = account.get("fetched_at") and time.time() - account["fetched_at"] < FRESH
            account["note"] = "" if fresh or account["provider"] not in {"claude", "antigravity"} else (
                "Cuotas en vivo apagadas en Privacidad: se muestra la última lectura." if not self.live() else
                "Última lectura guardada; Consultar uso la actualiza en vivo.")
        return {"accounts": accounts, "warning": warning,
                "providers": [{"id": p.id, "title": p.title, "regions": [r[0] for r in p.regions]} for p in api.PROVIDERS.values()]}

    def find(self, ident):
        for account in self.accounts()["accounts"]:
            if account["id"] == ident:
                return account
        raise ValueError("Cuenta no encontrada; actualiza la lista")

    def refresh(self, ident):
        account = self.find(ident)
        value = clean_usage(account)
        error = ""
        try:
            if account["kind"] == "key":
                attrs = self.key_refs[ident]
                if attrs["region"] not in dict(api.PROVIDERS[account["provider"]].regions):
                    raise ValueError("Esta clave antigua no tiene región. Añádela de nuevo eligiendo Global o China.")
                result = api.query(account["provider"], attrs["region"], self.store().get(attrs))
                value = clean_usage(asdict(result)) | {"auth": "ok", "fetched_at": time.time()}
            elif account["provider"] == "codex":
                result = codex.read_account(account["path"])
                if result.get("error"):
                    value.update({k: result[k] for k in ("auth", "email", "plan")})
                    error = result["error"]
                else:
                    value = clean_usage(result) | {"fetched_at": time.time()}
            elif not self.live():
                error = "Cuotas en vivo desactivadas en Privacidad; se conserva la última lectura."
            elif time.time() < self.backoff.get(ident, 0):
                error = "El proveedor pidió esperar (HTTP 429); Leo vuelve a consultar en unos minutos."
            elif account["provider"] == "claude":
                reading = self._claude(account)
                if reading.rate_limited:
                    self.backoff[ident] = time.time() + BACKOFF
                value = from_widget(reading) if reading.windows else value  # No quotas read: keep the last ones.
                value.update(auth=reading.auth, email=reading.email or value.get("email", ""),
                             plan=reading.plan or value.get("plan", ""))
                error = ("Sesión caducada: abre Claude Code una vez y la renovará." if reading.auth == "expired" else
                         "Sin sesión: inicia sesión desde Cuentas." if reading.auth == "nologin" else
                         "Anthropic pidió esperar (HTTP 429); se conserva la última lectura." if reading.rate_limited else
                         "No se pudo consultar el uso; se conserva la última lectura." if reading.error else "")
            else:
                reading = self._antigravity(account)
                if reading is None:
                    error = "Abre Antigravity para ver las cuotas en vivo; se conserva la última lectura."
                else:
                    value = from_widget(reading)
        except Exception as exc:
            # Never reflect response bodies or subprocess stderr (could contain credentials).
            error = str(exc) if isinstance(exc, (ValueError, RuntimeError, api.HttpError)) else "No se pudo consultar la cuenta"
            if len(error) > 250:
                error = "No se pudo consultar la cuenta"
        value["error"] = error
        source = value.pop("source", None) or ("live" if not error else account["source"])
        cache = read(self.cache_path)
        cache[ident] = value
        write(self.cache_path, cache)
        return {"account": account | value | {"source": source}}

    @staticmethod
    def _claude(account):
        """Live reading with the widget's reader: the Claude Code session token stays in memory (G18)."""
        from ..widget.discovery import AccountRef
        from ..widget.i18n import Translator
        from ..widget.providers import claude
        ref = AccountRef("claude", account["name"], Path(account["path"]), "leo", kind="config")
        return claude.fetch(ref, Translator("es"), live=True)

    @staticmethod
    def _antigravity(account):
        """Quotas served by a running Antigravity on 127.0.0.1, or the last ones it saved in its own folder."""
        from ..widget.discovery import AccountRef
        from ..widget.i18n import Translator
        from ..widget.providers import antigravity
        ref = AccountRef("antigravity", account["name"], Path(account["path"]), "leo", kind="config")
        readings = antigravity.fetch_all([ref], Translator("es"), live=True)
        mine = [r for r in readings if r.path == account["path"]] or [r for r in readings if r.source == "live"]
        email = account.get("email")
        mine = [r for r in mine if not email or r.email == email] or mine
        return mine[0] if mine and (mine[0].windows or mine[0].source == "live") else None

    def rename(self, ident, name, hidden):
        self.find(ident)
        if not isinstance(name, str) or not name.strip() or len(name) > 100 or type(hidden) is not bool:
            raise ValueError("Nombre o visibilidad inválidos")
        meta = read(self.meta_path)
        meta.setdefault("labels", {})[ident] = name.strip()
        values = set(meta.get("hidden", []))
        values.add(ident) if hidden else values.discard(ident)
        meta["hidden"] = sorted(values)
        write(self.meta_path, meta)
        return {"message": "Cuenta guardada"}

    def profile(self, provider, name, path):
        if provider not in {"codex", "claude", "antigravity"} or not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,30}", name):
            raise ValueError("Usa un nombre corto con letras minúsculas, números y guiones")
        if path:
            folder = Path(path).expanduser()
            if not folder.is_absolute() or not folder.is_dir():
                raise ValueError("Selecciona una carpeta de perfil existente")
            folder = folder.resolve()
        else:
            folder = self.home / f".{provider}-{name}"
            folder.mkdir(mode=0o700)  # Never overwrite an existing profile.
        meta = read(self.meta_path)
        profiles = meta.setdefault("profiles", [])
        profiles[:] = [p for p in profiles if (p.get("provider"), p.get("path")) != (provider, str(folder))]
        profiles.append({"provider": provider, "name": name, "path": str(folder)})
        write(self.meta_path, meta)
        return {"message": "Perfil añadido. Usa Iniciar sesión para conectar la cuenta."}

    def login(self, ident, action):
        account = self.find(ident)
        if account["kind"] != "profile" or action not in {"login", "logout", "open"}:
            raise ValueError("Acción inválida")
        provider = account["provider"]
        env = dict(os.environ)
        if provider == "antigravity":
            if action == "logout":
                raise ValueError("Cierra la sesión desde el menú de cuentas del IDE")
            executable = shutil.which("antigravity-ide") or shutil.which("antigravity")
            argv = [executable, "--user-data-dir", account["path"]]
        else:
            executable = shutil.which(provider)
            argv = [executable]
            env["CODEX_HOME" if provider == "codex" else "CLAUDE_CONFIG_DIR"] = account["path"]
            if action != "open":
                argv += ["auth", action] if provider == "claude" else [action]
            terminal = shutil.which("gnome-terminal")
            if not terminal:
                raise ValueError("Se necesita gnome-terminal para abrir el flujo oficial")
            # --wait keeps a tracked child; env is passed after the terminal server boundary.
            variable = "CODEX_HOME" if provider == "codex" else "CLAUDE_CONFIG_DIR"
            argv = [terminal, "--wait", "--", "/usr/bin/env", f"{variable}={account['path']}", *argv]
        if not executable:
            raise ValueError(f"{provider} no está instalado")
        child = subprocess.Popen(argv, env=env, cwd=self.home, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        threading.Thread(target=child.wait, daemon=True).start()
        return {"message": "Flujo oficial abierto. Al terminar, pulsa Consultar uso para actualizar la cuenta."}

    def key_add(self, provider, region, key, name):
        if provider not in api.PROVIDERS or region not in dict(api.PROVIDERS[provider].regions):
            raise ValueError("Proveedor o región inválidos")
        if not isinstance(key, str) or not 8 <= len(key) <= 4096 or any(c.isspace() for c in key):
            raise ValueError("Clave inválida")
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 100:
            raise ValueError("Nombre inválido")
        # Save without spending quota; a distinct explicit button performs a balance GET.
        attrs = self.store().save(provider, region, key)
        ident = f"key:{APP}:{provider}:{attrs['id']}"
        meta = read(self.meta_path)
        meta.setdefault("labels", {})[ident] = name.strip()
        write(self.meta_path, meta)
        return {"message": "Clave guardada en el llavero. Consulta su saldo desde Uso de IA."}

    def key_remove(self, ident):
        self.find(ident)
        if ident not in self.key_refs:
            raise ValueError("Clave no encontrada")
        self.store().remove(self.key_refs[ident])
        meta, cache = read(self.meta_path), read(self.cache_path)
        meta.get("labels", {}).pop(ident, None)
        meta["hidden"] = [v for v in meta.get("hidden", []) if v != ident]
        cache.pop(ident, None)
        write(self.meta_path, meta)
        write(self.cache_path, cache)
        return {"message": "Clave eliminada del llavero de Leo"}

    def inventory(self):
        targets = skills.detect_targets(h=self.home)
        installed = skills.list_installed(targets, h=self.home)
        return {"targets": [{"id": t.key, "name": t.label, "path": str(t.path), "readonly": t.readonly} for t in targets],
                "skills": [{"name": s.name, "description": s.description, "source": s.source,
                            "paths": {k: str(v) for k, v in s.paths.items()}, "readonly": s.readonly} for s in installed]}

    def search(self, query):
        if not isinstance(query, str) or not 2 <= len(query.strip()) <= 200:
            raise ValueError("Escribe entre 2 y 200 caracteres")
        results = skills.search(query)[:60]
        self.results = {uuid.uuid4().hex: r for r in results}
        return {"results": [{"id": key, "name": r.name, "source": r.source, "installs": r.installs}
                            for key, r in self.results.items()]}

    def preview(self, ident):
        if ident not in self.results:
            raise ValueError("Repite la búsqueda")
        package = skills.fetch(self.results[ident])
        token = uuid.uuid4().hex
        self.previews = {token: (time.monotonic() + 600, package)}
        return {"token": token, "name": package.name, "description": package.description,
                "source": package.result.url, "files": [name for name, _ in package.files],
                "text": next(raw for name, raw in package.files if name.lower() == "skill.md").decode(errors="replace")[:32000]}

    def install(self, token, target):
        entry = self.previews.pop(token, None)
        if not entry or entry[0] < time.monotonic():
            raise ValueError("La revisión caducó; vuelve a abrir la vista previa")
        package = entry[1]
        targets = {t.key: t for t in skills.detect_targets(h=self.home) if not t.readonly}
        if target not in targets:
            raise ValueError("Destino no encontrado")
        folder = targets[target].path
        folder.mkdir(parents=True, exist_ok=True)
        dest = folder / package.name
        if dest.exists() or dest.is_symlink():
            raise ValueError("La skill ya existe en este destino; no se sobrescribió")
        staging = Path(tempfile.mkdtemp(prefix=".leo-skill-", dir=folder))
        try:
            for name, raw in skills._check_files(package.files):
                file = staging / name
                file.parent.mkdir(parents=True, exist_ok=True)
                file.write_bytes(raw)
            # Exclusively create destination; do not replace a directory created concurrently.
            dest.mkdir()
            try:
                for file in staging.iterdir():
                    file.rename(dest / file.name)
            except Exception:
                shutil.rmtree(dest)
                raise
        finally:
            shutil.rmtree(staging)
        return {"message": f"{package.name} instalada en {folder}. Sus archivos no se ejecutaron."}

    def dispatch(self, action, args):
        methods = {"accounts": (self.accounts, ()), "refresh": (self.refresh, ("id",)),
                   "rename": (self.rename, ("id", "name", "hidden")),
                   "profile": (self.profile, ("provider", "name", "path")),
                   "login": (self.login, ("id", "action")), "key_add": (self.key_add, ("provider", "region", "key", "name")),
                   "key_remove": (self.key_remove, ("id",)), "skills": (self.inventory, ()),
                   "search": (self.search, ("query",)), "preview": (self.preview, ("id",)),
                   "install": (self.install, ("token", "target"))}
        if action not in methods or not isinstance(args, dict):
            raise ValueError("Operación de IA inválida")
        method, keys = methods[action]
        if set(args) != set(keys):
            raise ValueError("Argumentos de IA inválidos")
        with self.lock:
            return method(*(args[k] for k in keys))
