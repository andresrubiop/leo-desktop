#!/usr/bin/env python3
"""Hoja de modelo de Leo generada en local con ComfyUI (LEO_SPEC F5.2; puerta G7: solo lo instalado).

Construye el workflow de Qwen-Image con los nodos y archivos que detecta en tu ComfyUI (loopback), libera
antes la VRAM de Ollama (`keep_alive: 0`) y guarda los candidatos en `assets/leo/generated/candidates/`
(ignorado por git y por los paquetes: los derivados no se publican). Por defecto genera de texto con los
rasgos medidos en `traits.json`; `--reference` usa además tus fotos como referencia si el spike detectó
un modelo de edición. Estilo: ilustración de mascota limpia, nunca fotorrealista, fondo verde plano para
recortarlo. El agente remoto no ve los resultados (G8): valóralos en la página local de comparación.

Uso: python3 scripts/leo_sheet.py [--count 6] [--steps 20] [--size 1024] [--reference] [--dry-run]
"""
import argparse
import colorsys
from datetime import datetime
import json
from pathlib import Path
import random
import sys
import time

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from avatar_traits import load  # noqa: E402
from comfy import Comfy, detect  # noqa: E402

GREEN = "#00B140"
NEGATIVE = ("photo, photorealistic, realistic fur photo, 3d render, text, letters, watermark, logo, extra legs, "
            "extra ears, deformed, blurry, busy background, shadow on background")


def color_words(hex_color):
    r, g, b = (int(hex_color[i:i + 2], 16) / 255 for i in (1, 3, 5))
    hue, light, sat = colorsys.rgb_to_hls(r, g, b)
    hue *= 360
    if light < .18:
        return "near-black"
    if sat < .15:
        return "slate grey" if light < .55 else "pale cream"
    if 15 <= hue < 45:
        return "copper fawn" if light < .55 else "golden cream"
    if hue < 15 or hue >= 330:
        return "rusty red"
    return "warm brown"


def prompt(traits):
    palette, proportions = traits["palette"], traits["proportions"]
    wrinkles = "very deep" if proportions["fold_depth"] > .65 else "deep"
    return ("Character model sheet of Leo, a chubby Chinese Shar-Pei dog mascot, clean cel-shaded 2D illustration with "
            "soft shading and crisp outlines. Two views of the same dog side by side: a side view mid-walk facing right "
            "and a three-quarter view sitting. Compact rounded body, sturdy short legs, broad hippopotamus-like head, "
            f"small folded triangular ears, wide padded muzzle, {wrinkles} wrinkles on forehead, neck and body. "
            f"Short {color_words(palette['coat_mid'])} coat with {color_words(palette['coat_light'])} highlights and "
            f"{color_words(palette['coat_deep'])} shading, {color_words(palette['muzzle_dark'])} muzzle and nose, "
            f"blue-black tongue, small deep-set dark eyes. Plain flat solid green ({GREEN}) background, no text.")


def workflow(found, info, text, seed, steps, size, reference=None):
    model, loader = found["edit_model"] if reference else found["text_model"]
    nodes = {"1": {"class_type": loader, "inputs": {"unet_name": model, **({"weight_dtype": "default"} if loader == "UNETLoader" else {})}},
             "2": {"class_type": "CLIPLoader", "inputs": {"clip_name": found["clip"], "type": "qwen_image"}},
             "3": {"class_type": "VAELoader", "inputs": {"vae_name": found["vae"]}}}
    sampler_model = ["1", 0]
    if found["nodes"]["ModelSamplingAuraFlow"]:
        nodes["4"] = {"class_type": "ModelSamplingAuraFlow", "inputs": {"model": ["1", 0], "shift": 3.1}}
        sampler_model = ["4", 0]
    if reference:
        encoder = found["edit_encoder"]
        fields = {**info[encoder]["input"].get("required", {}), **info[encoder]["input"].get("optional", {})}
        nodes["10"] = {"class_type": "LoadImage", "inputs": {"image": reference}}
        image_field = "image1" if "image1" in fields else "image"
        for node, words in (("5", text), ("6", NEGATIVE)):
            nodes[node] = {"class_type": encoder, "inputs": {"clip": ["2", 0], "prompt": words, image_field: ["10", 0],
                                                            **({"vae": ["3", 0]} if "vae" in fields else {})}}
    else:
        nodes["5"] = {"class_type": "CLIPTextEncode", "inputs": {"text": text, "clip": ["2", 0]}}
        nodes["6"] = {"class_type": "CLIPTextEncode", "inputs": {"text": NEGATIVE, "clip": ["2", 0]}}
    nodes["7"] = {"class_type": "EmptySD3LatentImage", "inputs": {"width": size, "height": size, "batch_size": 1}}
    nodes["8"] = {"class_type": "KSampler", "inputs": {"model": sampler_model, "seed": seed, "steps": steps, "cfg": 2.5,
                                                       "sampler_name": "euler", "scheduler": "simple", "positive": ["5", 0],
                                                       "negative": ["6", 0], "latent_image": ["7", 0], "denoise": 1.0}}
    nodes["9"] = {"class_type": "VAEDecode", "inputs": {"samples": ["8", 0], "vae": ["3", 0]}}
    nodes["11"] = {"class_type": "SaveImage", "inputs": {"images": ["9", 0], "filename_prefix": "leo-sheet"}}
    return nodes


def free_ollama():
    """Frees Ollama's VRAM before generating (keep_alive 0); returns the unloaded models."""
    from leo.providers import Ollama
    ollama = Ollama()
    try:
        loaded = [m["name"] for m in ollama._request("/api/ps").get("models", [])]
    except Exception:
        return []
    for name in loaded:
        try:
            ollama._request("/api/generate", {"model": name, "keep_alive": 0})
        except Exception:
            pass
    return loaded


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--count", type=int, default=6)
    parser.add_argument("--steps", type=int, default=20)
    parser.add_argument("--size", type=int, default=1024)
    parser.add_argument("--port", type=int, default=8188)
    parser.add_argument("--reference", action="store_true", help="usar las fotos de avatar/ como referencia (local)")
    parser.add_argument("--photos", type=Path, default=ROOT / "avatar")
    parser.add_argument("--output", type=Path, default=ROOT / "assets/leo/generated")
    parser.add_argument("--dry-run", action="store_true", help="solo mostrar el workflow")
    args = parser.parse_args(argv)
    if not 1 <= args.count <= 24 or not 4 <= args.steps <= 60 or args.size not in (768, 1024, 1328):
        parser.error("count 1–24, steps 4–60, size 768/1024/1328")
    comfy = Comfy(args.port)
    info = comfy.object_info()
    found = detect(info)
    if args.reference and not found["reference"]:
        raise SystemExit("Tu ComfyUI no tiene un modelo de edición con referencia (ver comfy_spike.py). "
                         "Sin --reference se genera de texto con los rasgos medidos.")
    if not found["text_to_image"] and not found["reference"]:
        raise SystemExit("No hay un Qwen-Image utilizable en ComfyUI (modelo, codificador y VAE); ejecuta comfy_spike.py")
    traits = load()
    text = prompt(traits)
    reference = None
    if args.reference:
        photos = sorted(p for p in args.photos.glob("*") if p.suffix.lower() in {".jpg", ".jpeg", ".png"})
        if not photos:
            raise SystemExit(f"No hay fotos en {args.photos}")
        reference = "leo-reference.png" if args.dry_run else comfy.upload(photos[0], "leo-reference" + photos[0].suffix.lower())
    seeds = [random.SystemRandom().randrange(2 ** 31) for _ in range(args.count)]
    if args.dry_run:
        print(json.dumps(workflow(found, info, text, seeds[0], args.steps, args.size, reference), indent=2, ensure_ascii=False))
        return
    unloaded = free_ollama()
    candidates = args.output / "candidates"
    candidates.mkdir(parents=True, exist_ok=True)
    vram = lambda: [round(d.get("vram_free", 0) / 2 ** 30, 2) for d in comfy.stats().get("devices", [])]  # noqa: E731
    results, before = [], vram()
    for seed in seeds:
        started = time.monotonic()
        images = comfy.run(workflow(found, info, text, seed, args.steps, args.size, reference))
        for index, data in enumerate(images):
            name = f"cand-{seed}-{index}.png"
            (candidates / name).write_bytes(data)
            results.append({"file": name, "seed": seed, "seconds": round(time.monotonic() - started, 1)})
        print(f"{len(results)}/{args.count} candidatos ({results[-1]['seconds']} s)", flush=True)
    after = vram()
    comfy.free()
    manifest = {"date": datetime.now().isoformat(timespec="seconds"), "mode": "reference" if reference else "text",
                "model": (found["edit_model"] if reference else found["text_model"])[0], "steps": args.steps,
                "size": args.size, "prompt": text, "traits_measured": traits["measured"], "ollama_unloaded": unloaded,
                "vram_free_gib": {"before": before, "after": after}, "candidates": results}
    (args.output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    print(f"Candidatos en {candidates} (no se publican). Siguiente: scripts/leo_score.py y scripts/leo_compare.py")


if __name__ == "__main__":
    main()
