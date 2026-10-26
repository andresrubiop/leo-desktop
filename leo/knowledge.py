"""Búsqueda con citas en cursos e investigación (LEO_SPEC F3.2). Backend: Cerveau, antes ContextOS (puerta G4).

Cerveau aporta el índice compartido local. Contrato de un backend:
`search(query, roots, limit) -> [{"path", "page", "snippet"}]`.
Leo revalida las rutas, permisos y existencia antes de devolver cada resultado.
"""
from .config import folder_path

KINDS = {"curso", "investigacion"}


class Unavailable(RuntimeError):
    pass


class Knowledge:
    def __init__(self, settings, backend=None):
        self.settings, self.backend = settings, backend

    def available(self):
        return self.backend is not None and (not hasattr(self.backend, "available") or self.backend.available())

    def status(self):
        roots = self.roots() if self.settings.snapshot()["files"] else []
        if not self.available():
            return {"available": False, "authorized_folders": len(roots)}
        return self.backend.status(roots)

    def index(self):
        if not self.settings.snapshot()["files"]:
            raise PermissionError("Habilita Archivos y notas en Privacidad")
        roots = self.roots()
        if not roots:
            raise PermissionError("Añade carpetas de tipo Curso o Investigación en Privacidad")
        if not self.available():
            raise Unavailable("No se encuentra Cerveau local")
        return self.backend.index(roots, authorized=lambda root: self.settings.snapshot()["files"] and root in self.roots())

    def roots(self):
        roots = []
        for folder in self.settings.snapshot()["folders"]:
            if folder.get("kind") in KINDS:
                try:
                    roots.append(folder_path(folder["path"]))
                except ValueError:
                    pass
        return roots

    def search(self, query, limit=8):
        if not isinstance(query, str) or not 2 <= len(query.strip()) <= 200:
            raise ValueError("Búsqueda de 2 a 200 caracteres")
        if not self.available():
            raise Unavailable("No se encuentra Cerveau local para buscar en cursos e investigación")
        if not self.settings.snapshot()["files"]:
            raise PermissionError("Habilita Archivos y notas en Privacidad")
        roots = self.roots()
        if not roots:
            raise PermissionError("No hay carpetas de curso o investigación autorizadas")
        results = []
        for item in self.backend.search(query.strip(), [str(r) for r in roots], limit) or []:
            try:
                path = folder_path(item["path"])
                page = item.get("page")
                if (not self.settings.snapshot()["files"] or not path.is_file()
                        or not any(path.is_relative_to(root) for root in self.roots())
                        or not (page is None or type(page) is int and page > 0)):
                    continue
                result = {"path": str(path), "page": page, "snippet": str(item.get("snippet", ""))[:500]}
                for key in ("start_line", "end_line"):
                    if type(item.get(key)) is int and item[key] > 0:
                        result[key] = item[key]
                if "metadata_only" in item:
                    result["metadata_only"] = bool(item["metadata_only"])
                results.append(result)
            except (KeyError, TypeError, ValueError, OSError):
                continue
            if len(results) >= limit:
                break
        return {"results": results, "note": "Cita archivo y página o líneas cuando existan. Un resultado metadata_only "
                "solo cataloga el archivo: no demuestra su contenido. Usa files_read_text para leer documentos; "
                "no inventes páginas ni evidencia si la búsqueda no encuentra resultados. Solo se consultan carpetas "
                "registradas en Cerveau; Actualizar índice incorpora carpetas y cambios nuevos."}
