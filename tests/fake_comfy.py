"""ComfyUI simulado en loopback para el contrato de F5 (spike, generación, descarga y liberación de VRAM).

No genera nada: valida que el workflow enlace nodos existentes de su /object_info y devuelve una hoja
sintética (fondo verde plano con dos vistas rectangulares). Las imágenes son ficticias, no fotos.
"""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading
from urllib.parse import urlparse

import gi
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf  # noqa: E402


def synthetic_sheet(width=1024, height=512):
    sheet = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8, width, height)
    sheet.fill(0x00B140FF)
    for left in (.12, .6):  # Two «views» of a copper dog, each with a dark muzzle.
        x, y, w, h = int(width * left), int(height * .28), int(width * .25), int(height * .58)
        sheet.new_subpixbuf(x, y, w, h).fill(0xB5733DFF)
        sheet.new_subpixbuf(x + int(w * .58), y + int(h * .07), int(w * .35), int(h * .23)).fill(0x2F2C28FF)
    ok, data = sheet.save_to_bufferv("png", [], [])
    return data


def object_info(edit=False):
    def node(**required):
        return {"input": {"required": {k: v for k, v in required.items()}}, "output": ["X"]}
    info = {"UnetLoaderGGUF": node(unet_name=[["qwen-image-2.1-Q4_K_M.gguf"] + (["qwen-image-edit-Q4_K_M.gguf"] if edit else [])]),
            "CLIPLoader": node(clip_name=[["qwen_2.5_vl_7b_fp8_scaled.safetensors"]], type=[["stable_diffusion", "qwen_image"]]),
            "VAELoader": node(vae_name=[["qwen_image_vae.safetensors"]]), "ModelSamplingAuraFlow": node(model=["MODEL"]),
            "CLIPTextEncode": node(text=["STRING"]), "EmptySD3LatentImage": node(width=["INT"]), "KSampler": node(seed=["INT"]),
            "VAEDecode": node(samples=["LATENT"]), "SaveImage": node(images=["IMAGE"]), "LoadImage": node(image=[["x.png"]])}
    if edit:
        info["TextEncodeQwenImageEdit"] = {"input": {"required": {"clip": ["CLIP"], "prompt": ["STRING"]},
                                                     "optional": {"vae": ["VAE"], "image": ["IMAGE"]}}, "output": ["CONDITIONING"]}
    return info


class FakeComfy:
    def __init__(self, edit=False):
        self.info, self.workflows, self.uploads, self.freed = object_info(edit), [], [], 0
        self.sheet = synthetic_sheet()
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def _send(self, body, kind="application/json", code=200):
                body = body if isinstance(body, bytes) else json.dumps(body).encode()
                self.send_response(code)
                self.send_header("Content-Type", kind)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                path = urlparse(self.path).path
                if path == "/object_info":
                    self._send(owner.info)
                elif path == "/system_stats":
                    self._send({"devices": [{"name": "cuda:0 simulada", "vram_total": 8 * 2 ** 30, "vram_free": 6 * 2 ** 30}]})
                elif path.startswith("/history/"):
                    pid = path.rsplit("/", 1)[1]
                    self._send({pid: {"status": {"completed": True, "status_str": "success"},
                                      "outputs": {"11": {"images": [{"filename": f"leo-sheet_{pid}.png", "subfolder": "",
                                                                     "type": "output"}]}}}})
                elif path == "/view":
                    self._send(owner.sheet, "image/png")
                else:
                    self._send({"error": "no"}, code=404)

            def do_POST(self):
                body = self.rfile.read(int(self.headers["Content-Length"]))
                path = urlparse(self.path).path
                if path == "/prompt":
                    workflow = json.loads(body)["prompt"]
                    for ident, item in workflow.items():
                        if item["class_type"] not in owner.info:
                            return self._send({"error": f"unknown node {item['class_type']}"}, code=400)
                        for value in item["inputs"].values():
                            if isinstance(value, list) and len(value) == 2 and isinstance(value[1], int) and value[0] not in workflow:
                                return self._send({"error": f"broken link in {ident}"}, code=400)
                    owner.workflows.append(workflow)
                    self._send({"prompt_id": f"p{len(owner.workflows)}", "number": len(owner.workflows), "node_errors": {}})
                elif path == "/upload/image":
                    owner.uploads.append(len(body))
                    self._send({"name": "leo-reference.png", "subfolder": "", "type": "input"})
                elif path == "/free":
                    owner.freed += 1
                    self._send(b"", "text/plain")
                else:
                    self._send({"error": "no"}, code=404)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *_):
        self.server.shutdown()
        self.server.server_close()
