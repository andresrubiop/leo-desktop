"""Texto visible por OCR local (Tesseract) de la misma imagen que mira Leo (LEO_SPEC F6.2, puerta G9).

Solo lee píxeles ya capturados por petición del usuario: no amplía lo que Leo ve, a diferencia de
leer el texto por accesibilidad (que expondría historial de terminales o contenido no visible).
La imagen viaja por stdin; no se escriben archivos. Si Tesseract falta, no hay texto y nada falla.
Tesseract 5 lee igual el texto claro sobre fondo oscuro (medido en terminales sintéticas: mismo
error con y sin invertir), así que la imagen se envía tal cual.
"""
import shutil
import subprocess

LIMIT = 6000
ENV = {"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "OMP_THREAD_LIMIT": "4"}
_languages = None


def executable():
    return shutil.which("tesseract", path="/usr/bin:/bin")


def languages(run=subprocess.run):
    """«spa+eng» si el paquete de español está instalado; si no, «eng»; "" sin Tesseract."""
    global _languages
    if _languages is None:
        found = set()
        if executable():
            try:
                result = run([executable(), "--list-langs"], capture_output=True, text=True, timeout=10, env=ENV)
                found = {line.strip() for line in result.stdout.splitlines()[1:]}
            except (OSError, subprocess.TimeoutExpired):
                pass
        _languages = "+".join(lang for lang in ("spa", "eng") if lang in found)
    return _languages


def read(pixbuf, run=subprocess.run, timeout=30):
    """Texto reconocido, o "" si no hay Tesseract o falla. Nunca lanza por el OCR."""
    if not executable() or not languages(run):
        return ""
    ok, data = pixbuf.save_to_bufferv("png", [], [])
    if not ok:
        return ""
    try:
        result = run([executable(), "stdin", "stdout", "-l", languages(run), "--psm", "6"], input=data,
                     capture_output=True, timeout=timeout, env=ENV)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    if result.returncode:
        return ""
    text = "\n".join(line.rstrip() for line in result.stdout.decode("utf-8", "replace").splitlines() if line.strip())
    return text[:LIMIT]
