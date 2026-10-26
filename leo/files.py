"""Archivos dentro de carpetas autorizadas (LEO_SPEC F2.6). Nunca sobrescriben ni borran.

Las rutas se resuelven sin seguir el último enlace simbólico y deben quedar dentro de una carpeta
autorizada, sin componentes ocultos; escribir, mover o enviar a la papelera exige `write`.
La papelera sigue la especificación freedesktop en `$XDG_DATA_HOME/Trash`, de modo que el diario
sabe exactamente qué restaurar. Cada mutación devuelve `(resultado, deshacer)`.
"""
from datetime import datetime
import os
from pathlib import Path
import shutil
from urllib.parse import quote

from .config import DRAFTS, folder_path
from .documents import DOCUMENTS, marked, pages

READ_LIMIT = 256 * 1024
TEXT_LIMIT = 16000
DRAFT_LIMIT = 200_000
DRAFT_SUFFIXES = {".md", ".txt", ".tex", ".csv"}
SEARCH_RESULTS, SEARCH_SCAN = 50, 20000


def safe_name(name):
    if (not isinstance(name, str) or not name.strip() or len(name) > 128 or "/" in name or "\x00" in name
            or name.strip().startswith(".") or name.strip() in {".", ".."}):
        raise ValueError("Nombre inválido: sin barras, sin empezar por punto, hasta 128 caracteres")
    return name.strip()


def trash_dir():
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "Trash"


def trash(path):
    """freedesktop.org home trash. Returns the name inside Trash/files, or None if GIO had to do it."""
    path = Path(path)
    root = trash_dir()
    for sub in ("files", "info"):
        (root / sub).mkdir(parents=True, exist_ok=True, mode=0o700)
    if os.lstat(path).st_dev != os.stat(root / "files").st_dev:
        from gi.repository import Gio
        Gio.File.new_for_path(str(path)).trash(None)  # Other filesystem: its own .Trash-UID.
        return None
    stem, number = path.name, 1
    while True:
        name = stem if number == 1 else f"{stem}.{number}"
        try:
            fd = os.open(root / "info" / f"{name}.trashinfo", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            number += 1
            continue
        if os.path.lexists(root / "files" / name):
            os.close(fd)
            os.unlink(root / "info" / f"{name}.trashinfo")
            number += 1
            continue
        break
    with os.fdopen(fd, "w") as stream:
        stream.write(f"[Trash Info]\nPath={quote(str(path))}\nDeletionDate={datetime.now():%Y-%m-%dT%H:%M:%S}\n")
    try:
        os.rename(path, root / "files" / name)
    except OSError:
        os.unlink(root / "info" / f"{name}.trashinfo")
        raise
    return name


def restore(name, original):
    root, original = trash_dir(), Path(original)
    if os.path.lexists(original):
        raise FileExistsError(f"Ya existe {original}; no se sobrescribe")
    os.rename(root / "files" / name, original)
    (root / "info" / f"{name}.trashinfo").unlink(missing_ok=True)


class Files:
    def __init__(self, settings):
        self.settings = settings

    def roots(self, write=False):
        roots = []
        for folder in self.settings.snapshot()["folders"]:
            if write and not folder["write"]:
                continue
            try:
                roots.append((folder, folder_path(folder["path"])))
            except ValueError:
                pass  # Revalidated on every use: a folder may have been swapped for a link.
        return roots

    def resolve(self, path, write=False, exists=True, directory=None, drafts=False):
        """Authorized real path. In course/research folders writing is confined to Leo-borradores/;
        with drafts=True a destination folder there is redirected into it (created if needed)."""
        if not isinstance(path, str) or not path.strip() or "\x00" in path or len(path) > 4096:
            raise ValueError("Ruta inválida")
        raw = Path(os.path.expanduser(path.strip()))
        candidates = [raw] if raw.is_absolute() else [root / raw for _, root in self.roots()] + [Path.home() / raw]
        # The parent is resolved; the last component is kept, so links are never followed or moved.
        candidates = [c.parent.resolve() / c.name for c in candidates]
        found = [c for c in candidates if os.path.lexists(c)] if exists else candidates
        if not found:
            raise FileNotFoundError(f"No existe «{path}» en las carpetas autorizadas")
        real = found[0]
        if os.path.islink(real):
            raise PermissionError("No se opera sobre enlaces simbólicos")
        for folder, root in self.roots(write):
            try:
                relative = real.relative_to(root)
            except ValueError:
                continue
            if any(part.startswith(".") for part in relative.parts):
                raise PermissionError("No se accede a archivos ni carpetas ocultos")
            if directory is not None and exists and real.is_dir() != directory:
                raise ValueError(f"«{path}» {'no ' if directory else ''}es una carpeta")
            if write and folder.get("kind", "general") != "general" and relative.parts[:1] != (DRAFTS,):
                if not drafts:
                    raise PermissionError(f"En carpetas de curso o investigación Leo solo modifica {DRAFTS}/")
                real = root / DRAFTS
                real.mkdir(mode=0o755, exist_ok=True)
            elif write and folder.get("kind", "general") != "general" and relative.parts == (DRAFTS,) and not drafts:
                raise PermissionError(f"La carpeta {DRAFTS}/ no se mueve ni se renombra")
            return real
        raise PermissionError("La ruta no está en una carpeta autorizada" + (" con escritura" if write else ""))

    def search(self, query):
        if not isinstance(query, str) or not 1 <= len(query.strip()) <= 100:
            raise ValueError("Búsqueda de 1 a 100 caracteres")
        from .conversation import normalize
        needle, results, scanned = normalize(query), [], 0
        for _, root in self.roots():
            for current, dirs, files in os.walk(root):
                dirs[:] = sorted(d for d in dirs if not d.startswith(".") and not (Path(current) / d).is_symlink())
                for name in sorted([*dirs, *(f for f in files if not f.startswith("."))]):
                    scanned += 1
                    if scanned > SEARCH_SCAN:
                        return {"results": results, "truncated": True}
                    item = Path(current) / name
                    if item.is_symlink():
                        continue
                    if needle in normalize(name):
                        results.append({"path": str(item), "type": "carpeta" if item.is_dir() else "archivo"})
                    if len(results) >= SEARCH_RESULTS or scanned >= SEARCH_SCAN:
                        return {"results": results, "truncated": True}
        return {"results": results, "truncated": False}

    def list(self, path):
        folder = self.resolve(path, directory=True)
        entries = []
        for item in sorted(folder.iterdir())[:400]:
            if item.name.startswith(".") or item.is_symlink():
                continue
            stat = item.lstat()
            entries.append({"name": item.name, "type": "carpeta" if item.is_dir() else "archivo", "bytes": stat.st_size,
                            "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="minutes")})
        return {"path": str(folder), "entries": entries[:200], "truncated": len(entries) > 200}

    def read_text(self, path):
        target = self.resolve(path, directory=False)
        if target.suffix.lower() in DOCUMENTS:
            document = pages(target)
            text, cut = marked(document, TEXT_LIMIT)
            return {"path": str(target), "pages": len(document), "text": text, "truncated": cut}
        fd = os.open(target, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, "rb") as stream:
            data = stream.read(READ_LIMIT + 1)
        if len(data) > READ_LIMIT:
            raise ValueError("Archivo demasiado grande para leerlo (máximo 256 KiB)")
        if b"\x00" in data:
            raise ValueError("No es un archivo de texto")
        text = data.decode("utf-8", "replace")
        return {"path": str(target), "text": text[:TEXT_LIMIT], "truncated": len(text) > TEXT_LIMIT}

    def open(self, path):
        target = self.resolve(path)
        from gi.repository import Gio
        Gio.AppInfo.launch_default_for_uri(Gio.File.new_for_path(str(target)).get_uri(), None)
        return {"opened": str(target)}

    def create_draft(self, folder, name, text):
        directory = self.resolve(folder, write=True, directory=True, drafts=True)
        name = safe_name(name)
        if not Path(name).suffix:
            name += ".md"
        if Path(name).suffix.lower() not in DRAFT_SUFFIXES:
            raise ValueError("Borradores: .md, .txt, .tex o .csv")
        if not isinstance(text, str) or len(text) > DRAFT_LIMIT:
            raise ValueError("Contenido de hasta 200 000 caracteres")
        target = directory / name
        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644)
        with os.fdopen(fd, "w") as stream:
            stream.write(text)
        return {"created": str(target)}, {"op": "trash_created", "path": str(target)}

    def _destination(self, source, folder):
        directory = self.resolve(folder, write=True, directory=True, drafts=True)
        target = directory / source.name
        if os.path.lexists(target):
            raise FileExistsError(f"Ya existe {target}; no se sobrescribe")
        if source.is_dir() and (directory == source or source in directory.parents):
            raise ValueError("No se puede mover una carpeta dentro de sí misma")
        return target

    def move(self, source, destination_folder):
        origin = self.resolve(source, write=True)
        target = self._destination(origin, destination_folder)
        shutil.move(str(origin), str(target))
        return {"moved": str(origin), "to": str(target)}, {"op": "move", "from": str(target), "to": str(origin)}

    def rename(self, path, new_name):
        origin = self.resolve(path, write=True)
        target = origin.parent / safe_name(new_name)
        if os.path.lexists(target):
            raise FileExistsError(f"Ya existe {target}; no se sobrescribe")
        os.rename(origin, target)
        return {"renamed": str(origin), "to": str(target)}, {"op": "move", "from": str(target), "to": str(origin)}

    def copy(self, source, destination_folder):
        origin = self.resolve(source, directory=False)
        target = self._destination(origin, destination_folder)
        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644)
        with open(origin, "rb") as reader, os.fdopen(fd, "wb") as writer:
            shutil.copyfileobj(reader, writer)
        shutil.copystat(origin, target)
        return {"copied": str(origin), "to": str(target)}, {"op": "trash_created", "path": str(target)}

    def trash(self, path):
        origin = self.resolve(path, write=True)
        name = trash(origin)
        undo = {"op": "restore", "name": name, "path": str(origin)} if name else None
        return {"trashed": str(origin), "recoverable": True}, undo


def revert(undo):
    """Applies one journal undo record. Moves only; never deletes."""
    op = undo["op"]
    if op == "trash_created":
        if not os.path.lexists(undo["path"]):
            return f"{undo['path']} ya no existe"
        trash(undo["path"])
        return f"{undo['path']} enviado a la papelera"
    if op == "move":
        if os.path.lexists(undo["to"]):
            raise FileExistsError(f"Ya existe {undo['to']}; no se sobrescribe")
        shutil.move(undo["from"], undo["to"])
        return f"{undo['from']} → {undo['to']}"
    if op == "restore":
        restore(undo["name"], undo["path"])
        return f"{undo['path']} restaurado de la papelera"
    raise ValueError("Operación de deshacer desconocida")

