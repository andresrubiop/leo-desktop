"""Intenciones explícitas de apertura; nunca ejecuta texto generado por el LLM."""
import re
import unicodedata

from gi.repository import Gio

# Targets that name documents or folders («los materiales de Redes», «paper.pdf»): the agent opens them as files.
DOCUMENTS = re.compile(r"\b(?:materiales?|carpetas?|documentos?|apuntes|diapositivas|presentacion\w*|papers?|"
                       r"sesion\w*|clases?|cursos?|notas de|archivos? de)\b|\.\w{2,4}$")


def normalize(text):
    return "".join(c for c in unicodedata.normalize("NFKD", text.casefold()) if not unicodedata.combining(c)).strip()


def catalog():
    return {app.get_id(): app for app in Gio.AppInfo.get_all()
            if isinstance(app, Gio.DesktopAppInfo) and app.should_show() and app.get_id()}


def opening(prompt):
    text = normalize(prompt).rstrip(".!?")
    text = re.sub(r"^(?:hola[ ,]+)?leo[ ,:]+", "", text)
    text = re.sub(r"^(?:por favor[ ,]+|puedes |podrias )", "", text)
    text = re.sub(r"[ ,]+por favor$", "", text)
    match = re.fullmatch(r"(?:abre|abrir|abreme|inicia|lanzar|lanza) (.+)", text)
    if not match:
        return None
    target = re.sub(r"^(?:(?:la|el|los|las|un|una) )?(?:aplicacion )?", "", match[1]).strip()
    if DOCUMENTS.search(target):  # «Abre los materiales de Redes»: files for the agent, not an app name.
        return None
    if target in {"terminal", "terminal nueva", "nueva terminal", "consola"}:
        return "terminal.open", {}
    aliases = {"archivos": "files", "explorador de archivos": "files", "calculadora": "calculator",
               "configuracion": "settings", "ajustes": "settings", "spotify": "spotify"}
    if target in aliases:
        return "app.open", {"app": aliases[target]}
    apps = catalog()
    if target in {"navegador", "navegador web"}:
        default = Gio.AppInfo.get_default_for_type("text/html", False)
        if default and default.get_id() in apps:
            return "app.launch", {"desktop_id": default.get_id()}
    matches = [ident for ident, app in apps.items()
               if target in {normalize(app.get_display_name()), normalize(app.get_name()), normalize(ident.removesuffix(".desktop"))}]
    if len(matches) == 1:
        return "app.launch", {"desktop_id": matches[0]}
    return "unmatched", {}
