"""Ollama simulado en loopback para pruebas de contrato (streaming, num_ctx, selección).

No es inferencia: responde texto fijo. Las pruebas que lo usan verifican el transporte
D-Bus/HTTP, nunca la calidad de un modelo; se documentan como simuladas.
"""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading
import time

ANSWER = ["<b>Hola</b>", ", soy ", "Leo", ". Respuesta ", "simulada ", "por ", "partes."]


class FakeOllama:
    """`script(payload)` may return {"content", "tool_calls"} to play a model that calls tools."""

    def __init__(self, models=("qwen2.5-coder:7b", "qwen3:8b"), delay=.15, script=None, tools=False, capabilities=None):
        self.models, self.delay, self.requests = list(models), delay, []
        self.script, self.tools = script, tools
        self.capabilities = capabilities or {}  # model -> list, e.g. {"qwen2.5vl:7b": ["completion", "vision"]}
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def _json(self, value):
                body = json.dumps(value).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                if self.path != "/api/tags":
                    self.send_error(404)
                    return
                self._json({"models": [{"name": name, "details": {"format": "gguf"}} for name in owner.models]})

            def do_POST(self):
                payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                if self.path == "/api/show" and (owner.tools or owner.capabilities):
                    self._json({"capabilities": owner.capabilities.get(payload.get("model"), ["completion", "tools"]
                                                                       if owner.tools else ["completion"])})
                    return
                owner.requests.append(payload)
                if self.path != "/api/chat":
                    self.send_error(404)
                    return
                scripted = owner.script(payload) if owner.script else None
                if scripted is not None:
                    message = {"role": "assistant", "content": scripted.get("content", ""),
                               **({"tool_calls": scripted["tool_calls"]} if scripted.get("tool_calls") else {})}
                    if not payload.get("stream"):
                        self._json({"message": message, "done": True})
                        return
                    self.send_response(200)
                    self.send_header("Content-Type", "application/x-ndjson")
                    self.end_headers()
                    self.wfile.write(json.dumps({"message": message, "done": False}).encode() + b"\n")
                    self.wfile.write(json.dumps({"message": {"role": "assistant", "content": ""}, "done": True}).encode() + b"\n")
                    return
                if not payload.get("stream"):
                    self._json({"message": {"role": "assistant", "content": "".join(ANSWER)}, "done": True})
                    return
                self.send_response(200)
                self.send_header("Content-Type", "application/x-ndjson")
                self.end_headers()
                for piece in ANSWER:
                    line = {"message": {"role": "assistant", "content": piece}, "done": False}
                    self.wfile.write(json.dumps(line).encode() + b"\n")
                    self.wfile.flush()
                    time.sleep(owner.delay)
                self.wfile.write(json.dumps({"message": {"role": "assistant", "content": ""}, "done": True}).encode() + b"\n")

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *_):
        self.server.shutdown()
        self.server.server_close()
