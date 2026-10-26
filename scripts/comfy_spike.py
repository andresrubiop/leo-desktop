#!/usr/bin/env python3
"""Spike de F5.2 (puerta G7: solo lo instalado): ¿el Qwen-Image de tu ComfyUI admite imagen de referencia?

Consulta ComfyUI en loopback (arráncalo con `python main.py --listen 127.0.0.1`), lista modelos y nodos
de Qwen-Image, VRAM disponible y si hay un modelo de edición con su codificador (referencia). No genera
imágenes ni lee fotos. Informe: build/comfy-spike.json (solo nombres de archivo y números).
"""
import argparse
from datetime import datetime
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
from comfy import Comfy, detect  # noqa: E402


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--port", type=int, default=8188)
    parser.add_argument("--output", type=Path, default=ROOT / "build/comfy-spike.json",
                        help="Informe; las pruebas usan un temporal para no pisar el del equipo real")
    args = parser.parse_args(argv)
    comfy = Comfy(args.port)
    found = detect(comfy.object_info())
    devices = comfy.stats().get("devices", [])
    found["vram_gib"] = [{"name": d.get("name"), "total": round(d.get("vram_total", 0) / 2 ** 30, 1),
                          "free": round(d.get("vram_free", 0) / 2 ** 30, 1)} for d in devices]
    if found["reference"]:
        found["recommendation"] = "Hay modelo de edición y codificador: usar las fotos como referencia (leo_sheet.py --reference)."
    elif found["text_to_image"]:
        found["recommendation"] = ("Solo texto a imagen: generar con el prompt de rasgos medidos (traits.json) y elegir por "
                                   "puntuación local; la referencia exigiría descargar un modelo de edición o una LoRA (G7).")
    else:
        found["recommendation"] = "No se encontró un Qwen-Image utilizable (modelo, codificador CLIP y VAE): revisa ComfyUI."
    found["date"] = datetime.now().isoformat(timespec="seconds")
    output = args.output
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(found, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({k: found[k] for k in ("qwen_models", "text_to_image", "reference", "vram_gib", "recommendation")},
                     ensure_ascii=False, indent=2))
    print(f"Informe: {output}")


if __name__ == "__main__":
    main()
