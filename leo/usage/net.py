"""GET acotado a servicios de cuotas y catálogos; sin redirecciones ni proxies."""
import json
import time
import urllib.error
import urllib.parse
import urllib.request

now = time.time
HOSTS = {
    "api.deepseek.com", "api.moonshot.ai", "api.moonshot.cn", "api.z.ai", "open.bigmodel.cn",
    "api.minimax.io", "api.minimaxi.com", "openrouter.ai", "api.siliconflow.cn", "api.siliconflow.com",
    "skills.sh", "api.github.com", "raw.githubusercontent.com",
    "graph.microsoft.com",
}


class HttpError(Exception):
    def __init__(self, status=None, message=""):
        self.status = status
        super().__init__(message or f"Consulta no disponible (HTTP {status})")


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def http_get(url, headers=None, timeout=12, max_bytes=2 * 1024 * 1024):
    parsed = urllib.parse.urlsplit(url)
    if (parsed.scheme != "https" or parsed.hostname not in HOSTS or parsed.port not in (None, 443)
            or parsed.username or parsed.password or parsed.fragment):
        raise HttpError(None, "Destino no permitido")
    request = urllib.request.Request(url, headers={"User-Agent": "Leo/0.4", **(headers or {})}, method="GET")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    try:
        with opener.open(request, timeout=timeout) as response:
            data = response.read(max_bytes + 1)
        if len(data) > max_bytes:
            raise HttpError(None, "Respuesta demasiado grande")
        return data
    except urllib.error.HTTPError as exc:
        raise HttpError(exc.code) from None
    except (OSError, ValueError, urllib.error.URLError):
        # Do not echo remote bodies, URLs, headers or credentials in errors.
        raise HttpError(None, "No se pudo conectar al servicio") from None


def http_json(url, headers=None, **kwargs):
    try:
        return json.loads(http_get(url, headers, **kwargs))
    except (ValueError, UnicodeDecodeError):
        raise HttpError(None, "Respuesta JSON inválida") from None
