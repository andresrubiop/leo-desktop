"""Herramientas tipadas, capacidades y aprobaciones de un solo uso."""
from dataclasses import dataclass
import contextvars
import copy
import secrets
import threading
import time
import re


@dataclass(frozen=True)
class Tool:
    capability: str | None
    confirm: bool
    fields: dict
    description: str


TOOLS = {
    "system.info": Tool(None, False, {}, "Consultar información del sistema"),
    "app.open": Tool("apps", False, {"app": str}, "Abrir una aplicación permitida"),
    "app.launch": Tool("apps", False, {"desktop_id": str}, "Abrir una aplicación instalada"),
    "terminal.open": Tool("apps", False, {}, "Abrir una terminal vacía"),
    "music.control": Tool("music", False, {"action": str}, "Controlar Spotify local por MPRIS"),
    "note.save": Tool("files", True, {"name": str, "text": str}, "Crear una nota local; no sobrescribir archivos"),
    "system.command": Tool("commands", True, {"command": str}, "Ejecutar un diagnóstico fijo del sistema"),
    "screen.capture": Tool("screen", True, {}, "Solicitar una captura interactiva al portal de GNOME"),
    "pointer.move": Tool("screen", True, {"dx": int, "dy": int}, "Mover el puntero una vez mediante el portal; cerrar sesión después"),
    "remote.ask": Tool(None, True, {"provider": str, "prompt": str}, "Enviar este texto al proveedor remoto seleccionado"),
    "job.watch": Tool("jobs", False, {"pid": int, "log": str}, "Vigilar un proceso o el log de un entrenamiento (solo lectura)"),
    "gpu.status": Tool("jobs", False, {}, "Consultar el estado de la GPU con nvidia-smi"),
    "files.search": Tool("files", False, {"query": str}, "Buscar archivos por nombre en las carpetas autorizadas"),
    "knowledge.search": Tool("files", False, {"query": str}, "Buscar contenido de cursos e investigación en Cerveau local"),
    "files.list": Tool("files", False, {"path": str}, "Listar una carpeta autorizada"),
    "files.read_text": Tool("files", False, {"path": str}, "Leer un archivo de texto de una carpeta autorizada"),
    "files.summarize": Tool("files", False, {"path": str, "focus": str}, "Resumir un documento con citas de página"),
    "files.create_draft": Tool("files", True, {"folder": str, "name": str, "text": str}, "Crear un archivo de texto nuevo (nunca sobrescribe)"),
    "files.move": Tool("files", True, {"source": str, "destination_folder": str}, "Mover a otra carpeta autorizada (nunca sobrescribe)"),
    "files.rename": Tool("files", True, {"path": str, "new_name": str}, "Cambiar el nombre (nunca sobrescribe)"),
    "files.copy": Tool("files", True, {"source": str, "destination_folder": str}, "Copiar a una carpeta autorizada (nunca sobrescribe)"),
    "files.trash": Tool("files", True, {"path": str}, "Enviar a la papelera; se puede recuperar o deshacer"),
    "reminder.create": Tool(None, False, {"text": str, "at": str}, "Crear un recordatorio"),
    "reminder.list": Tool(None, False, {}, "Listar recordatorios pendientes"),
    "reminder.cancel": Tool(None, False, {"id": int}, "Cancelar un recordatorio"),
    "timer.start": Tool(None, False, {"minutes": int, "label": str}, "Iniciar un temporizador"),
    "settings.set": Tool("desktop", True, {"key": str, "value": str}, "Cambiar un ajuste del escritorio"),
    "undo.last": Tool(None, True, {}, "Deshacer la última operación o plan de Leo"),
    "memory.remember": Tool("memory", True, {"text": str}, "Recordar este hecho sobre ti"),
    "memory.forget": Tool("memory", True, {"id": int}, "Olvidar este recuerdo"),
    "memory.list": Tool("memory", False, {}, "Ver lo que Leo recuerda"),
    "schedule.today": Tool(None, False, {"when": str}, "Consultar las clases de un día"),
    "files.open": Tool("files", False, {"path": str}, "Abrir con su aplicación predeterminada"),
    "screen.look": Tool("screen", False, {"question": str}, "Mirar la pantalla una vez sin selector, al pedírselo a Leo"),
    "screen.locate": Tool("screen", False, {"target": str}, "Buscar un elemento en una captura y señalarlo"),
    "files.look": Tool("files", False, {"path": str, "page": int, "question": str}, "Mirar una imagen o una página de PDF"),
    # F6.8: cases of the teacher and researcher (read only; local models).
    "files.latex": Tool("files", False, {"path": str, "page": int}, "Pasar a LaTeX la ecuación de una imagen o página de PDF"),
    "screen.latex": Tool("screen", False, {}, "Pasar a LaTeX la ecuación que se ve en pantalla"),
    "slides.review": Tool("files", False, {"path": str}, "Revisar una presentación y proponer textos alternativos"),
    "pdf.table": Tool("files", False, {"path": str, "page": int}, "Extraer una tabla de un PDF a Markdown y CSV"),
    "curves.review": Tool("files", False, {"path": str}, "Revisar las curvas de entrenamiento de un run"),
    "ui.read": Tool("accessibility", False, {}, "Leer la interfaz de la ventana activa"),
    "ui.point": Tool("accessibility", False, {"name": str, "say": str}, "Señalar este elemento de la ventana activa"),
    "ui.press": Tool("accessibility", True, {"name": str}, "Pulsar este elemento de la ventana activa"),
}
# Set while the broker runs a tool the user has just approved (per-application consent uses it).
APPROVED = contextvars.ContextVar("leo_approved", default=False)
# Tools whose fields are alternatives: at least one must be present.
ANY_OF = {"job.watch"}
# Fields that may be left out (F6.7: the instruction Leo shows while pointing during a guide).
OPTIONAL = {"ui.point": {"say"}}
APPS = {"files": "org.gnome.Nautilus.desktop", "calculator": "org.gnome.Calculator.desktop",
        "settings": "org.gnome.Settings.desktop", "spotify": "spotify.desktop"}
COMMANDS = {"uptime": ["/usr/bin/uptime"], "memory": ["/usr/bin/free", "-h"],
            "disk": ["/usr/bin/df", "-h", "/"]}


def validate_args(name, args):
    if name not in TOOLS:
        raise ValueError("Herramienta no permitida")
    tool = TOOLS[name]
    required = set(tool.fields) - OPTIONAL.get(name, set())
    if not isinstance(args, dict) or not (required <= set(args) <= set(tool.fields) or
                                          (name in ANY_OF and args and set(args) <= set(tool.fields))):
        raise ValueError("Argumentos desconocidos o incompletos")
    for key, kind in tool.fields.items():
        if key not in args:
            continue
        if type(args[key]) is not kind:
            raise ValueError(f"Tipo incorrecto: {key}")
        if kind is str and ((not args[key].strip() and (name, key) != ("files.summarize", "focus"))
                            or len(args[key]) > 16000 or "\x00" in args[key]):
            raise ValueError(f"Texto inválido: {key}")
    if name == "ui.point" and len(args.get("say", "")) > 200:
        raise ValueError("La instrucción de la guía admite hasta 200 caracteres")
    if name == "app.open" and args["app"] not in APPS:
        raise ValueError("Aplicación no permitida")
    if name == "app.launch":
        from .conversation import catalog
        if args["desktop_id"] not in catalog():
            raise ValueError("La aplicación no figura entre los lanzadores instalados")
    if name == "music.control" and args["action"] not in {"PlayPause", "Next", "Previous", "Stop"}:
        raise ValueError("Acción musical no permitida")
    if name == "system.command" and args["command"] not in COMMANDS:
        raise ValueError("Solo se permiten uptime, memory y disk; nunca comandos libres")
    if name == "note.save" and not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", args["name"]):
        raise ValueError("Nombre de nota: letras, números, guion o guion bajo")
    if name == "job.watch" and "pid" in args and not 1 <= args["pid"] <= 4194304:
        raise ValueError("PID fuera de rango")
    if name == "settings.set":
        from .desktop import validate
        validate(args["key"], args["value"])
    if name == "files.look" and not 0 <= args["page"] <= 100000:
        raise ValueError("Página inválida")
    if name == "timer.start" and not 1 <= args["minutes"] <= 1440:
        raise ValueError("Temporizador de 1 a 1440 minutos")
    if name == "reminder.cancel" and args["id"] < 1:
        raise ValueError("Id de recordatorio inválido")
    if name == "pointer.move" and any(abs(args[k]) > 300 for k in ("dx", "dy")):
        raise ValueError("Movimiento máximo: 300 píxeles por eje")
    if name == "remote.ask" and args["provider"] not in {"codex", "claude"}:
        raise ValueError("Proveedor no permitido")
    return tool


class Broker:
    def __init__(self, settings, executor, clock=time.monotonic, previews=None, consents=None):
        self.settings, self.executor, self.clock = settings, executor, clock
        self.previews = previews or {}  # name -> callable(args): what exactly will happen.
        # name -> callable(args): consent text when it is still needed (e.g. first read of an app).
        self.consents = consents or {}
        self.pending = {}
        self.lock = threading.Lock()

    def _check(self, name, args):
        tool = validate_args(name, args)
        if name == "remote.ask":
            raise PermissionError("Leo responde únicamente con el LLM local; consultas remotas deshabilitadas")
        capability = args["provider"] if name == "remote.ask" else tool.capability
        if capability and not self.settings.snapshot()[capability]:
            raise PermissionError(f"Habilita la capacidad «{capability}» en Permisos")
        return tool

    def request(self, owner, name, args, force_confirm=False):
        """force_confirm: after external content was read in this turn, every action needs approval."""
        args = copy.deepcopy(args)
        tool = self._check(name, args)
        consent = self.consents[name](args) if name in self.consents else None
        if not tool.confirm and not force_confirm and not consent:
            return {"result": self.executor(name, args)}
        with self.lock:
            self.pending = {t: p for t, p in self.pending.items() if p[0] > self.clock()}
            if len(self.pending) >= 64:
                raise RuntimeError("Demasiadas aprobaciones pendientes")
            token = secrets.token_urlsafe(24)
            self.pending[token] = (self.clock() + 120, owner, name, args)
        details = "; ".join(filter(None, [consent, self.previews[name](args) if name in self.previews else ""]))
        return {"approval": token, "description": tool.description, "tool": name,
                "arguments": copy.deepcopy(args), "details": details, "expires_in": 120}

    def decide(self, owner, token, approved):
        if type(approved) is not bool:
            raise ValueError("La decisión debe ser booleana")
        with self.lock:
            pending = self.pending.get(token)
            if pending is None or pending[1] != owner:
                raise PermissionError("Aprobación inexistente o de otro cliente")
            del self.pending[token]
        expiry, _, name, args = pending
        if not approved:
            return {"result": "Acción cancelada"}
        if expiry <= self.clock():
            raise PermissionError("La aprobación expiró; solicita la acción nuevamente")
        self._check(name, args)  # Revocation applies even to already shown previews.
        approved = APPROVED.set(True)
        try:
            return {"result": self.executor(name, args)}
        finally:
            APPROVED.reset(approved)

    def disconnect(self, owner):
        with self.lock:
            self.pending = {t: p for t, p in self.pending.items() if p[1] != owner}
