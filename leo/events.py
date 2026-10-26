"""Avisos breves hacia el avatar: texto plano validado y límite de frecuencia por origen.

Los avisos llegan de `leoctl notify`, de los hooks de agentes de código y de la vigilancia de
trabajos. Nunca contienen markup ni se interpretan como órdenes; se publican con la señal D-Bus
`Event` para que la extensión reaccione (pose, globo breve y notificación de GNOME).
"""
from collections import deque
import itertools
import re
import threading
import time
import unicodedata

EVENTS = {"done", "failed", "info", "attention"}
TITLE_LIMIT = 120
AGENTS = {"claude": "Claude Code", "codex": "Codex"}
AGENT_KINDS = {"permission": "attention", "input": "attention", "attention": "attention", "stop": "done"}


def plain(text, limit, field="título"):
    """Una línea de texto sin controles ni etiquetas; rechaza vacíos y excesos de longitud."""
    if not isinstance(text, str):
        raise ValueError(f"El {field} debe ser texto")
    text = "".join(" " if unicodedata.category(c).startswith("C") else c for c in text)
    # GNOME notifications may parse markup; angle brackets never reach them.
    text = re.sub(r"\s+", " ", text.replace("<", "‹").replace(">", "›")).strip()
    if not text:
        raise ValueError(f"El {field} está vacío")
    if len(text) > limit:
        raise ValueError(f"El {field} admite como máximo {limit} caracteres")
    return text


def project_name(value):
    """Nombre de carpeta reducido a letras, números, espacio, punto, guion y guion bajo."""
    if not value:
        return ""
    if not isinstance(value, str):
        raise ValueError("El proyecto debe ser texto")
    name = re.sub(r"[^\w .-]", "", value.replace("\n", " ")).strip(" .")[:64]
    return name


def agent_title(source, kind, project):
    where = f" en {project}" if project else ""
    name = AGENTS[source]
    return {"permission": f"{name} espera tu aprobación{where}",
            "input": f"{name} espera tu respuesta{where}",
            "attention": f"{name} necesita tu atención{where}",
            "stop": f"{name} terminó{where}"}[kind]


class Events:
    def __init__(self, emit, clock=time.monotonic, per_origin=6, total=20, window=60):
        self.emit, self.clock = emit, clock
        self.per_origin, self.total, self.window = per_origin, total, window
        self.sent = deque()  # (time, origin)
        self.recent = deque(maxlen=20)
        self.ids = itertools.count(1)
        self.lock = threading.Lock()

    def publish(self, origin, event, title, project="", source="leoctl"):
        if event not in EVENTS:
            raise ValueError("Evento: done, failed, info o attention")
        item = {"event": event, "title": plain(title, TITLE_LIMIT), "project": project_name(project),
                "source": source}
        with self.lock:
            now = self.clock()
            while self.sent and now - self.sent[0][0] >= self.window:
                self.sent.popleft()
            if (len(self.sent) >= self.total
                    or sum(1 for _, o in self.sent if o == origin) >= self.per_origin):
                raise RuntimeError("Demasiados avisos seguidos de este origen; espera un minuto")
            self.sent.append((now, origin))
            item.update(id=next(self.ids), at=round(time.time()))
            self.recent.append(item)
        self.emit(dict(item))
        return item

    def agent(self, source, kind, project=""):
        if source not in AGENTS or kind not in AGENT_KINDS:
            raise ValueError("Origen o tipo de evento de agente inválido")
        project = project_name(project)
        return self.publish(f"{source}:{project}", AGENT_KINDS[kind], agent_title(source, kind, project),
                            project, source)
