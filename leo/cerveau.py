"""CLI local de Cerveau (antes ContextOS), limitada a proyectos con raíces autorizadas por Leo."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import threading
import unicodedata

from . import documents


def page_of(document_pages, snippet):
    """Page (slide) holding a snippet that Cerveau cites by lines of the extracted text, or None."""
    flat = lambda text: " ".join(unicodedata.normalize("NFC", text).split())  # noqa: E731
    first = next((flat(line) for line in snippet.splitlines() if len(flat(line)) >= 8), "")[:80]
    return next((page["page"] for page in document_pages if first and first in flat(page["text"])), None)


class Cerveau:
    def __init__(self, executable=None, workspace=None):
        self.executable = Path(executable or os.environ.get("LEO_CERVEAU_CLI") or
                               Path.home() / "Cerveau/.venv/bin/cerveau").expanduser()
        self.workspace = Path(workspace or os.environ.get("CERVEAU_HOME") or Path.home() / "Cerveau").expanduser().resolve()
        self.lock = threading.Lock()

    def available(self):
        return self.executable.is_file() and os.access(self.executable, os.X_OK)

    def initialized(self):
        return (self.workspace / ".cerveau/config.json").is_file() and (
            self.workspace / ".cerveau/cerveau.sqlite3").is_file()

    def call(self, *arguments, timeout=30):
        if not self.available():
            raise RuntimeError("No se encuentra la CLI local de Cerveau (~/Cerveau/.venv/bin/cerveau)")
        try:
            result = subprocess.run([str(self.executable), "--home", str(self.workspace), *arguments],
                                    capture_output=True, timeout=timeout, cwd=self.workspace,
                                    env={"HOME": str(Path.home()), "PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"})
        except subprocess.TimeoutExpired:
            raise RuntimeError("Cerveau agotó el tiempo; vuelve a intentarlo") from None
        if result.returncode:
            # stderr puede contener documentos: no devolverlo al modelo ni al log.
            raise RuntimeError("Cerveau no completó la operación; comprueba su configuración e índice local")
        if len(result.stdout) > 2_000_000:
            raise RuntimeError("Respuesta de Cerveau demasiado grande")
        try:
            return json.loads(result.stdout)
        except (ValueError, UnicodeError):
            raise RuntimeError("Cerveau devolvió una respuesta inválida") from None

    def projects(self, roots):
        if not self.initialized():
            raise RuntimeError("Cerveau no está inicializado; ejecuta cerveau --home RUTA init")
        allowed = {str(Path(root).resolve()) for root in roots}
        projects = self.call("project", "list")
        if not isinstance(projects, list):
            raise RuntimeError("Registro de proyectos de Cerveau inválido")
        # Coincidencia exacta: un índice de $HOME no permite leer sus otros proyectos.
        return [p for p in projects if isinstance(p, dict) and isinstance(p.get("path"), str)
                and p["path"] in allowed and isinstance(p.get("id"), str)]

    def status(self, roots):
        ready = self.available() and self.initialized()
        projects = self.projects(roots) if ready and roots else []
        return {"available": self.available(), "initialized": self.initialized(),
                "authorized_folders": len(roots), "registered_folders": len({p["path"] for p in projects}),
                "files": sum(p.get("files", 0) for p in projects),
                "text_files": sum(p.get("text_files", 0) for p in projects),
                "metadata_files": sum(p.get("metadata_files", 0) for p in projects)}

    def index(self, roots, authorized=None):
        with self.lock:
            projects = {p["path"]: p for p in self.projects(roots)}
            count = 0
            for root in roots:
                if authorized and not authorized(root):
                    raise PermissionError("La autorización de la carpeta cambió; indexación detenida")
                root = str(root)
                project = projects.get(root)
                if project is None:
                    identifier = "leo-" + hashlib.sha256(root.encode()).hexdigest()[:20]
                    project = self.call("project", "add", "Leo " + Path(root).name, root,
                                        "--id", identifier, "--kind", "directory")
                if authorized and not authorized(Path(root)):
                    raise PermissionError("La autorización de la carpeta cambió; indexación detenida")
                self.call("index", "--project", project["id"], timeout=180)
                count += 1
            return {"indexed_folders": count, **self.status(roots)}

    def search(self, query, roots, limit):
        projects = self.projects(roots)
        if not projects:
            raise RuntimeError("Estas carpetas aún no están registradas en Cerveau. Pulsa Actualizar índice en Privacidad.")
        results, paged = [], {}
        for project in projects:
            for source in ("file", "code"):
                reply = self.call("search", query, "--project", project["id"], "--source", source,
                                  "--max-tokens", "4096")
                for item in reply.get("results", []):
                    if not isinstance(item, dict) or item.get("source") != source or item.get("project") != project["id"]:
                        continue
                    relative = item.get("path")
                    if (not isinstance(relative, str) or Path(relative).is_absolute()
                            or any(p.startswith(".") for p in Path(relative).parts)):
                        continue
                    path = (Path(project["path"]) / relative).resolve()
                    if not path.is_relative_to(Path(project["path"])) or not path.is_file() or item.get("index_stale"):
                        continue
                    snippet = "" if item.get("metadata_only") else str(item.get("content", ""))[:500]
                    lines, page = (item.get("start_line"), item.get("end_line")), None
                    if path.suffix.lower() in documents.DOCUMENTS:
                        # Cerveau indexes the text of PDF and office files; its line numbers mean nothing to a reader.
                        lines = (None, None)
                        try:
                            paged.setdefault(path, documents.pages(path) if snippet else [])
                            page = page_of(paged[path], snippet)
                        except (OSError, ValueError, RuntimeError):
                            paged[path] = []
                    results.append({"path": str(path), "page": page, "snippet": snippet,
                                    "start_line": lines[0], "end_line": lines[1],
                                    "metadata_only": bool(item.get("metadata_only"))})
                    if len(results) >= limit:
                        return results
        return results
