"""Captura nativa y efímera: una imagen por petición explícita del usuario, sin selector.

Ámbito (F6.1): por defecto la ventana activa (sin las de Leo); «mi pantalla» el monitor de esa ventana;
«todas las pantallas» el escritorio completo. Las ventanas protegidas se ocultan en la captura.
"""
from contextlib import contextmanager
import contextvars
from dataclasses import dataclass
import base64
import json
import re
import threading
import time

from gi.repository import Gio, GLib

from .conversation import normalize

INTERFACE = "io.github.leo.Capture"
PATH = "/io/github/leo/Capture"
CURRENT = contextvars.ContextVar("leo_screen_request", default=None)
SCOPES = {"window", "monitor", "all"}
REMEMBER = 120  # G11: la última captura del globo vive solo en memoria, como mucho 2 minutos.
OUTDATED = ("La extensión cargada en GNOME es anterior: capturé todo el escritorio. "
            "Cierra e inicia sesión para que Leo capture solo la ventana o el monitor.")


PLACES = r"pantallas?|escritorios?|ventanas?|monitor(?:es)?"


def requested(text):
    """Solo el mensaje actual del usuario, nunca argumentos o contenidos devueltos por el modelo."""
    text = normalize(text).lstrip("¿¡ ")
    text = re.sub(r"^(?:hola[ ,]+)?leo[ ,:]+", "", text).lstrip("¿¡ ")
    text = re.sub(r"^(?:por favor[ ,]+|(?:puedes|podrias|quiero que|necesito que) )", "", text).lstrip("¿¡ ")
    return bool(re.match(r"^(?:mira|mirar|ve|ver|observa|analiza|revisa|captura|lee|describe|senala|localiza)\b"
                         r".{0,100}\b(?:" + PLACES + r"|esto|aqui)\b", text)
                or re.match(r"^(?:que (?:(?:error|mensaje|codigo|numero) )?(?:ves|hay|dice|pone|aparece)|"
                            r"donde (?:esta|se encuentra))\b.{0,100}\b(?:" + PLACES + r")\b", text)
                or re.match(r"^explica lo que (?:ves|aparece|hay) en (?:mi |la )?pantalla\b", text)
                or re.match(r"^(?:toma|haz) una captura de (?:la |mi )?pantalla\b", text))


ACTING = re.compile(r"\b(?:senala|senalame|localiza|donde|abre|abrir|cierra|borra|elimina|mueve|guarda|crea|"
                    r"apunta|recuerdame|pulsa|haz clic|clic|escribe|envia|instala|ejecuta|copia el archivo)\b")


def direct(text):
    """Petición solo de mirar (ni señalar ni actuar): Leo responde con el modelo de visión, sin agente."""
    return requested(text) and not ACTING.search(normalize(text))


def scope(text):
    """Qué capturar según las palabras del usuario: ventana (por defecto), monitor o todo."""
    text = normalize(text)
    if re.search(r"\b(?:todas las pantallas|las dos pantallas|ambas pantallas|todos los monitores|"
                 r"los dos monitores|ambos monitores|todo el escritorio)\b", text):
        return "all"
    if re.search(r"\b(?:pantalla|monitor|escritorio)\b", text) and not re.search(r"\bventana\b", text):
        return "monitor"
    return "window"


@dataclass
class Consent:
    allowed: bool
    cancelled: threading.Event
    used: bool = False
    scope: str = "window"
    rect: list | None = None


def region(rect):
    """Rectángulo elegido por el usuario en el escenario: cuatro enteros, al menos 8×8."""
    if (not isinstance(rect, list) or len(rect) != 4 or any(type(v) is not int for v in rect)
            or rect[2] < 8 or rect[3] < 8 or min(rect) < 0 or max(rect) > 100000):
        raise ValueError("Región inválida: [x, y, ancho, alto] en píxeles, al menos 8×8")
    return rect


@contextmanager
def screen_turn(allowed, cancelled=None, scope="window", rect=None):
    if scope not in SCOPES | {"region", "perched"} or (scope == "region") != (rect is not None):
        raise ValueError("Ámbito de captura: window, monitor, all o una región")
    token = CURRENT.set(Consent(allowed, cancelled or threading.Event(), scope=scope,
                                rect=region(rect) if rect is not None else None))
    try:
        yield
    finally:
        CURRENT.reset(token)


def allowed():
    consent = CURRENT.get()
    return bool(consent and consent.allowed and not consent.used and not consent.cancelled.is_set())


class NativeCapture:
    def __init__(self, bus, settings):
        self.bus, self.settings = bus, settings

    def capture(self, quiet=False, own=False):
        """Una captura PNG en memoria: {"png", "scope", "rect" (escenario) o None, "app", "note", "mask"}.

        quiet (Leo atento): sin destello ni ocultar a Leo; la extensión devuelve sus zonas en `mask` y aquí se
        tapan en memoria antes de devolver la imagen."""
        if not self.settings.snapshot()["screen"]:
            raise PermissionError("Habilita Pantalla y puntero en Privacidad")
        if not allowed():
            raise PermissionError("Pide explícitamente «Leo, mira mi pantalla» para autorizar una captura en este mensaje")
        consent = CURRENT.get()
        consent.used = True  # Un fallo no autoriza reintentos ni capturas periódicas.
        options = json.dumps({"scope": consent.scope, "protected": self.settings.snapshot()["protected_apps"],
                              **({"rect": consent.rect} if consent.rect else {}), **({"quiet": True} if quiet else {}),
                              **({"own": True} if own else {})})
        try:
            reply = self.bus.call_sync("org.gnome.Shell", PATH, INTERFACE, "CaptureScope", GLib.Variant("(s)", (options,)),
                                       GLib.VariantType.new("(ays)"), Gio.DBusCallFlags.NO_AUTO_START, 15000, None)
            info = json.loads(reply.get_child_value(1).get_string())
            shot = {"scope": info.get("scope"), "rect": info.get("rect"), "app": info.get("app"), "note": info.get("note"),
                    "mask": info.get("mask") if isinstance(info.get("mask"), list) else []}
        except GLib.Error as error:
            remote = Gio.DBusError.get_remote_error(error) if Gio.DBusError.is_remote_error(error) else None
            if remote == "io.github.leo.Capture.Error":  # Negativa explícita de la extensión: sin respaldo.
                raise RuntimeError(re.sub(r"^GDBus\.Error:[\w.]+:\s*", "", error.message)) from None
            if remote not in {"org.freedesktop.DBus.Error.UnknownMethod", "org.freedesktop.DBus.Error.InvalidArgs"}:
                raise RuntimeError("La captura automática necesita la extensión LEO actualizada y activa. "
                                   "Cierra y vuelve a iniciar sesión si acabas de actualizarla.") from None
            try:  # Extensión anterior a F6.1, aún cargada hasta el próximo inicio de sesión.
                reply = self.bus.call_sync("org.gnome.Shell", PATH, INTERFACE, "Capture", None,
                                           GLib.VariantType.new("(ay)"), Gio.DBusCallFlags.NO_AUTO_START, 15000, None)
            except GLib.Error:
                raise RuntimeError("La captura automática necesita la extensión LEO actualizada y activa. "
                                   "Cierra y vuelve a iniciar sesión si acabas de actualizarla.") from None
            shot = {"scope": "all", "rect": None, "app": None, "note": OUTDATED, "mask": []}
        if consent.cancelled.is_set() or not self.settings.snapshot()["screen"]:
            raise PermissionError("Captura cancelada o permiso de pantalla revocado")
        data = reply.get_child_value(0).get_data_as_bytes().get_data()
        if len(data) > 30 * 1024 * 1024 or not data.startswith(b"\x89PNG\r\n\x1a\n"):
            raise ValueError("La extensión no devolvió una captura PNG válida (máximo 30 MB)")
        rect = shot["rect"]
        if not (isinstance(rect, list) and len(rect) == 4 and all(type(v) in (int, float) for v in rect) and rect[2] > 0 and rect[3] > 0):
            shot["rect"] = None
        return masked({**shot, "png": data})


def masked(shot):
    """Tapa en gris las zonas de Leo (avatar, globo, aviso, mascotitas, sus ventanas) de una captura silenciosa."""
    boxes, rect = shot.pop("mask", None) or [], shot.get("rect")
    if not boxes or not rect:
        return shot
    from .vision import load_bytes
    pixbuf = load_bytes(shot["png"])
    sx, sy = pixbuf.get_width() / rect[2], pixbuf.get_height() / rect[3]
    for box in boxes[:32]:
        if not (isinstance(box, list) and len(box) == 4 and all(type(v) in (int, float) for v in box)):
            continue
        x1 = max(0, round((box[0] - rect[0]) * sx))
        y1 = max(0, round((box[1] - rect[1]) * sy))
        x2 = min(pixbuf.get_width(), round((box[0] + box[2] - rect[0]) * sx))
        y2 = min(pixbuf.get_height(), round((box[1] + box[3] - rect[1]) * sy))
        if x2 > x1 and y2 > y1:
            pixbuf.new_subpixbuf(x1, y1, x2 - x1, y2 - y1).fill(0x808080ff)
    return {**shot, "png": pixbuf.save_to_bufferv("png", [], [])[1]}


def to_stage(box, image, rect):
    """Cuadro en píxeles de la captura → rectángulo en coordenadas del escenario de GNOME."""
    sx, sy = rect[2] / image[0], rect[3] / image[1]
    return [round(rect[0] + box[0] * sx), round(rect[1] + box[1] * sy), round(box[2] * sx), round(box[3] * sy)]


def thumbnail(png, side=240):
    """Miniatura PNG en base64 para el globo; también vive solo en memoria."""
    from .vision import load_bytes
    from gi.repository import GdkPixbuf
    pixbuf = load_bytes(png)
    scale = min(1.0, side / max(pixbuf.get_width(), pixbuf.get_height()))
    if scale < 1:
        pixbuf = pixbuf.scale_simple(max(1, round(pixbuf.get_width() * scale)), max(1, round(pixbuf.get_height() * scale)),
                                     GdkPixbuf.InterpType.BILINEAR)
    return base64.b64encode(pixbuf.save_to_bufferv("png", [], [])[1]).decode()


class Attachments:
    """Última captura por cliente para preguntas de seguimiento: memoria, caduca, nunca en disco ni historial."""

    def __init__(self, ttl=REMEMBER, clock=time.monotonic):
        self.ttl, self.clock = ttl, clock
        self.items, self.lock = {}, threading.Lock()

    def put(self, owner, shot):
        with self.lock:
            self.items[owner] = {"shot": shot, "expires": self.clock() + self.ttl}

    def get(self, owner):
        with self.lock:
            item = self.items.get(owner)
            if item and item["expires"] <= self.clock():
                del self.items[owner]
                item = None
            return item["shot"] if item else None

    def remaining(self, owner):
        with self.lock:
            item = self.items.get(owner)
            return max(0, round(item["expires"] - self.clock())) if item else 0

    def forget(self, owner):
        with self.lock:
            return self.items.pop(owner, None) is not None

    def describe(self, owner):
        """Lo que el globo muestra: qué se vio, cuánto queda y la miniatura; None si no hay captura."""
        shot = self.get(owner)
        if not shot:
            return None
        return {"scope": shot["scope"], "app": shot["app"], "expires_in": self.remaining(owner),
                "thumbnail": thumbnail(shot["png"])}
