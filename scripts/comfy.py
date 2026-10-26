"""Cliente mínimo de la API HTTP de ComfyUI en loopback (LEO_SPEC F5.2). Sin dependencias.

ComfyUI se arranca en modo headless con `--listen 127.0.0.1`; nunca se contacta otro host.
"""
import json
import time
import uuid
from urllib import error, parse, request


class Comfy:
    def __init__(self, port=8188, timeout=30):
        if not 1024 <= int(port) <= 65535:
            raise ValueError("Puerto de ComfyUI inválido")
        self.base = f"http://127.0.0.1:{int(port)}"
        self.timeout = timeout
        self.opener = request.build_opener(request.ProxyHandler({}))
        self.client_id = uuid.uuid4().hex

    def _call(self, path, payload=None, raw=False, data=None, headers=None):
        body = data if data is not None else (json.dumps(payload).encode() if payload is not None else None)
        req = request.Request(self.base + path, data=body,
                              headers=headers or ({"Content-Type": "application/json"} if body else {}))
        try:
            with self.opener.open(req, timeout=self.timeout) as response:
                content = response.read(200 * 1024 * 1024)
        except error.HTTPError as exc:
            detail = exc.read(20000).decode(errors="replace")
            raise RuntimeError(f"ComfyUI rechazó {path}: {detail[:1500]}") from exc
        except (error.URLError, TimeoutError) as exc:
            raise RuntimeError(f"ComfyUI no responde en {self.base}; arráncalo con --listen 127.0.0.1") from exc
        return content if raw else json.loads(content or b"{}")

    def stats(self):
        return self._call("/system_stats")

    def object_info(self):
        return self._call("/object_info")

    def free(self):
        return self._call("/free", {"unload_models": True, "free_memory": True}, raw=True)

    def upload(self, path, name):
        """Uploads a local image to ComfyUI's input folder (same machine, loopback)."""
        boundary = uuid.uuid4().hex
        content = open(path, "rb").read()
        body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"image\"; filename=\"{name}\"\r\n"
                f"Content-Type: image/png\r\n\r\n").encode() + content + (
            f"\r\n--{boundary}\r\nContent-Disposition: form-data; name=\"overwrite\"\r\n\r\ntrue\r\n--{boundary}--\r\n").encode()
        return self._call("/upload/image", data=body, headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})["name"]

    def run(self, workflow, limit=900, poll=1.0):
        """Queues a workflow, waits for it and returns the bytes of every output image."""
        queued = self._call("/prompt", {"prompt": workflow, "client_id": self.client_id})
        prompt_id = queued["prompt_id"]
        end = time.monotonic() + limit
        while time.monotonic() < end:
            history = self._call(f"/history/{prompt_id}").get(prompt_id)
            if history and history.get("status", {}).get("completed"):
                images = []
                for output in history.get("outputs", {}).values():
                    for image in output.get("images", []):
                        query = parse.urlencode({k: image.get(k, "") for k in ("filename", "subfolder", "type")})
                        images.append(self._call(f"/view?{query}", raw=True))
                return images
            if history and history.get("status", {}).get("status_str") == "error":
                raise RuntimeError("ComfyUI terminó con error: " + json.dumps(history["status"])[:1500])
            time.sleep(poll)
        raise TimeoutError("ComfyUI no terminó a tiempo")


def options(info, node, field):
    """Choices offered by a loader node (model file names), or [] if the node is absent."""
    spec = (info.get(node) or {}).get("input", {})
    value = {**spec.get("required", {}), **spec.get("optional", {})}.get(field)
    return list(value[0]) if value and isinstance(value[0], list) else []


QWEN_NODES = ("UnetLoaderGGUF", "UNETLoader", "CLIPLoader", "VAELoader", "ModelSamplingAuraFlow", "CLIPTextEncode",
              "EmptySD3LatentImage", "KSampler", "VAEDecode", "SaveImage", "LoadImage", "TextEncodeQwenImageEdit",
              "TextEncodeQwenImageEditPlus", "ImageScaleToTotalPixels")


def detect(info):
    """What the local ComfyUI can do with Qwen-Image: files, nodes and whether a reference image is usable."""
    unets = [(n, "UnetLoaderGGUF") for n in options(info, "UnetLoaderGGUF", "unet_name")]
    unets += [(n, "UNETLoader") for n in options(info, "UNETLoader", "unet_name")]
    qwen = [(n, loader) for n, loader in unets if "qwen" in n.lower() and "image" in n.lower()]
    edit = [(n, loader) for n, loader in qwen if "edit" in n.lower()]
    text = [(n, loader) for n, loader in qwen if "edit" not in n.lower()]
    clips = [n for n in options(info, "CLIPLoader", "clip_name") if "qwen" in n.lower()]
    vaes = [n for n in options(info, "VAELoader", "vae_name") if "qwen" in n.lower()] or options(info, "VAELoader", "vae_name")
    encoder = next((n for n in ("TextEncodeQwenImageEditPlus", "TextEncodeQwenImageEdit") if n in info), None)
    nodes = {n: n in info for n in QWEN_NODES}
    base = bool(clips and vaes and nodes["CLIPTextEncode"] and nodes["KSampler"] and nodes["VAEDecode"]
                and nodes["SaveImage"] and nodes["EmptySD3LatentImage"])
    return {"qwen_models": [n for n, _ in qwen], "text_model": text[0] if text else None, "edit_model": edit[0] if edit else None,
            "clip": clips[0] if clips else None, "vae": vaes[0] if vaes else None, "edit_encoder": encoder,
            "qwen_image_type": "qwen_image" in options(info, "CLIPLoader", "type"), "nodes": nodes,
            "text_to_image": bool(base and text), "reference": bool(base and edit and encoder and nodes["LoadImage"])}
