"""Herramientas que el LLM local puede proponer (LEO_SPEC F2.1), como JSON Schema para Ollama.

El modelo solo ve nombres con guion bajo; el broker valida y ejecuta los nombres internos.
Pantalla, puntero, lanzadores arbitrarios y proveedores remotos no se ofrecen al modelo.
"""

def _string(description, **extra):
    return {"type": "string", "description": description, **extra}


def _integer(description, **extra):
    return {"type": "integer", "description": description, **extra}


SPECS = {
    "knowledge.search": ("Buscar por contenido o tema en cursos e investigación con Cerveau local. Cita el archivo "
                         "y sus líneas; metadata_only no prueba contenido. Para PDF usa después files_read_text.",
                         {"query": _string("Palabras clave o tema a buscar", minLength=2, maxLength=200)}, ["query"]),
    "app.open": ("Abrir una aplicación conocida del escritorio.",
                 {"app": _string("Aplicación", enum=["files", "calculator", "settings", "spotify"])}, ["app"]),
    "terminal.open": ("Abrir una terminal vacía (no ejecuta ningún comando).", {}, []),
    "music.control": ("Controlar la música en Spotify: reproducir o pausar, siguiente o anterior canción, detener.",
                      {"action": _string("Acción", enum=["PlayPause", "Next", "Previous", "Stop"])}, ["action"]),
    "system.info": ("Consultar sistema operativo, kernel y sesión gráfica.", {}, []),
    "system.command": ("Diagnóstico fijo: memoria libre, espacio en disco o tiempo encendido.",
                       {"command": _string("Diagnóstico", enum=["memory", "disk", "uptime"])}, ["command"]),
    "gpu.status": ("Estado de la GPU NVIDIA: uso, memoria y temperatura.", {}, []),
    "job.watch": ("Vigilar un proceso por PID y/o el log de un entrenamiento y avisar al terminar o fallar.",
                  {"pid": _integer("PID del proceso"), "log": _string("Ruta del archivo de log")}, []),
    "note.save": ("Guardar una nota en las notas de Leo («anota…», «apunta en pendientes…»); no es un recordatorio con hora.",
                  {"name": _string("Nombre corto: letras, números, guion"), "text": _string("Contenido")}, ["name", "text"]),
    "files.search": ("Buscar archivos por nombre o tema del nombre (p. ej. papers sobre atención) en las carpetas autorizadas.",
                     {"query": _string("Parte del nombre del archivo")}, ["query"]),
    "files.list": ("Listar el contenido de una carpeta autorizada.",
                   {"path": _string("Ruta de la carpeta")}, ["path"]),
    "files.read_text": ("Leer un archivo de texto de una carpeta autorizada.",
                        {"path": _string("Ruta del archivo")}, ["path"]),
    "files.summarize": ("Resumir un documento largo (PDF, DOCX, PPTX, ODP) por partes, con citas de página.",
                        {"path": _string("Ruta del documento"), "focus": _string("Aspecto en el que fijarse (o vacío)")},
                        ["path", "focus"]),
    "files.create_draft": ("Crear un archivo de texto nuevo (.md, .txt, .tex, .csv); nunca sobrescribe.",
                           {"folder": _string("Carpeta autorizada con escritura"),
                            "name": _string("Nombre del archivo nuevo"), "text": _string("Contenido")},
                           ["folder", "name", "text"]),
    "files.move": ("Mover un archivo o carpeta a otra carpeta autorizada; nunca sobrescribe.",
                   {"source": _string("Ruta a mover"), "destination_folder": _string("Carpeta de destino")},
                   ["source", "destination_folder"]),
    "files.rename": ("Cambiar el nombre de un archivo o carpeta; nunca sobrescribe.",
                     {"path": _string("Ruta actual"), "new_name": _string("Nombre nuevo, sin carpeta")}, ["path", "new_name"]),
    "files.copy": ("Copiar un archivo a una carpeta autorizada; nunca sobrescribe.",
                   {"source": _string("Archivo a copiar"), "destination_folder": _string("Carpeta de destino")},
                   ["source", "destination_folder"]),
    "files.trash": ("Enviar un archivo o carpeta a la papelera (recuperable). Nunca borra definitivamente.",
                    {"path": _string("Ruta a enviar a la papelera")}, ["path"]),
    "reminder.create": ("Crear un recordatorio («recuérdame…», «ponme un recordatorio…») para una fecha y hora locales; "
                        "«en 20 minutos» se convierte a esa hora.",
                        {"text": _string("Qué recordar"), "at": _string("Fecha y hora locales AAAA-MM-DDTHH:MM")},
                        ["text", "at"]),
    "reminder.list": ("Listar los recordatorios y temporizadores pendientes, con su id.", {}, []),
    "reminder.cancel": ("Cancelar un recordatorio o temporizador por su id (consulta antes reminder_list).",
                        {"id": _integer("Id del recordatorio")}, ["id"]),
    "timer.start": ("Iniciar un temporizador (cuenta atrás) en minutos cuando se pide un temporizador.",
                    {"minutes": _integer("Duración en minutos", minimum=1, maximum=1440),
                     "label": _string("Para qué es")}, ["minutes", "label"]),
    "settings.set": ("Cambiar un ajuste del escritorio: modo oscuro, no molestar o volumen.",
                     {"key": _string("Ajuste", enum=["dark_mode", "do_not_disturb", "volume"]),
                      "value": _string("on/off para modo oscuro y no molestar; 0-100 para volumen")}, ["key", "value"]),
    "memory.remember": ("Proponer recordar un hecho breve y estable sobre el usuario («recuerda que…», «tenlo en cuenta»: preferencia, rol, contexto); "
                        "solo se guarda si el usuario lo aprueba. Nunca conversaciones, contraseñas ni datos de terceros.",
                        {"text": _string("Hecho en una frase, en tercera persona")}, ["text"]),
    "memory.forget": ("Olvidar un recuerdo («olvida que…») por su id (consulta antes memory_list).",
                      {"id": _integer("Id del recuerdo")}, ["id"]),
    "memory.list": ("Listar lo que Leo recuerda del usuario («¿qué recuerdas de mí?»), con su id.", {}, []),
    "schedule.today": ("Clases de un día según el horario importado (hoy, mañana o AAAA-MM-DD), con salón y carpeta.",
                       {"when": _string("hoy, mañana o AAAA-MM-DD")}, ["when"]),
    "files.open": ("Abrir un archivo o carpeta autorizada con su aplicación predeterminada (p. ej. los materiales de una clase).",
                   {"path": _string("Ruta del archivo o carpeta")}, ["path"]),
    "screen.look": ("Mirar la pantalla una vez cuando el usuario lo pide: captura automática sin selector, "
                    "ocultando Leo y su chat; el modelo visual local explica lo que ve.",
                    {"question": _string("Qué hay que mirar o responder")}, ["question"]),
    "files.look": ("Mirar una imagen o una página de un PDF autorizado (figuras de papers, fotos de pizarra o apuntes).",
                   {"path": _string("Ruta de la imagen o del PDF"),
                    "page": _integer("Página del PDF; 0 si es una imagen o para buscar la figura o tabla que nombra la pregunta"),
                    "question": _string("Qué hay que mirar o responder")}, ["path", "page", "question"]),
    "ui.read": ("Leer los elementos (botones, menús, pestañas) de la ventana activa mediante accesibilidad.", {}, []),
    "ui.point": ("Señalar en pantalla un elemento de la ventana activa por su nombre visible o por su aspecto "
                 "(«el icono de la impresora»): Leo camina hasta él.",
                 {"name": _string("Nombre visible o descripción del elemento"),
                  "say": _string("Opcional: instrucción breve que Leo muestra mientras señala")}, ["name"]),
    "ui.press": ("Pulsar o activar un elemento de la ventana activa por su nombre (requiere aprobación).",
                 {"name": _string("Nombre visible del elemento")}, ["name"]),
    "screen.locate": ("Si el usuario pide buscar algo en pantalla, capturar una vez sin selector y señalarlo con duda.",
                      {"target": _string("Elemento a buscar")}, ["target"]),
    "undo.last": ("Deshacer la última operación o el último plan de Leo (archivos, ajustes, recordatorios).", {}, []),
    "files.latex": ("Pasar a LaTeX una ecuación de una imagen o de una página de un PDF autorizado (foto de pizarra, captura, "
                    "apuntes); Leo comprueba que compila.",
                    {"path": _string("Ruta de la imagen o del PDF"), "page": _integer("Página del PDF; 0 si es una imagen")},
                    ["path", "page"]),
    "screen.latex": ("Pasar a LaTeX una ecuación que el usuario tiene en pantalla, cuando lo pide: una captura sin selector.",
                     {}, []),
    "slides.review": ("Revisar una presentación (PPTX, ODP o PDF) de una carpeta autorizada: texto pequeño, demasiado texto, "
                      "poco contraste e imágenes sin texto alternativo, con propuestas de texto alternativo.",
                      {"path": _string("Ruta de la presentación")}, ["path"]),
    "pdf.table": ("Extraer una tabla de una página de un PDF autorizado a Markdown y CSV.",
                  {"path": _string("Ruta del PDF"), "page": _integer("Página (1, 2, …)")}, ["path", "page"]),
    "curves.review": ("Revisar curvas de entrenamiento: una imagen o la más reciente de la carpeta de un run; diagnostica "
                      "sobreajuste, divergencia o estancamiento.", {"path": _string("Imagen o carpeta del run")}, ["path"]),
}
# Results that bring external, untrusted text into the conversation.
TAINTING = {"knowledge.search", "files.read_text", "files.list", "files.search", "files.summarize", "files.look", "screen.look",
            "screen.locate", "ui.read", "files.latex", "screen.latex", "slides.review", "pdf.table", "curves.review"}
# Tools that neither change anything nor act on the desktop.
READ_ONLY = {"screen.look", "screen.locate", "knowledge.search", "system.info", "system.command", "gpu.status", "files.search", "files.list", "files.read_text",
             "reminder.list", "memory.list", "schedule.today", "files.summarize", "files.look", "ui.read", "ui.point",
             "files.latex", "screen.latex", "slides.review", "pdf.table", "curves.review"}


def model_name(name):
    return name.replace(".", "_")


def internal_name(name):
    return {model_name(n): n for n in SPECS}.get(name)


def ollama_tools(exclude=()):
    return [{"type": "function", "function": {
        "name": model_name(name), "description": description,
        "parameters": {"type": "object", "properties": properties, "required": required}}}
        for name, (description, properties, required) in SPECS.items() if name not in exclude]
