#!/usr/bin/env python3
"""Puntuación local de semejanza con el Leo real (LEO_SPEC F5.2): foto frente a ilustración.

Con el modelo de visión local (G5, `qwen2.5vl:7b`) puntúa de 0 a 10 color, orejas, hocico, pliegues y «mismo
perro» para cada candidato y para el avatar SVG actual (línea base de la aceptación). Las fotos solo viajan a
Ollama en 127.0.0.1; sus SHA256 se comprueban antes y después. Imprime y guarda únicamente números y
nombres de archivo (`assets/leo/generated/scores.json`): el agente remoto nunca ve las imágenes (G8).

Uso: python3 scripts/leo_score.py [--photos avatar] [--candidates assets/leo/generated/candidates] [--model M]
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
CRITERIA = ("color", "orejas", "hocico", "pliegues", "mismo_perro")
PROMPT = ("La primera imagen es una foto de un perro real (Leo, un Shar-Pei). La segunda es una ilustración. "
          "Puntúa de 0 a 10 cuánto se parece la ilustración al perro de la foto en: color del pelaje (color), forma y "
          "tamaño de las orejas (orejas), hocico y nariz (hocico), pliegues de la piel (pliegues) y si parece el mismo perro "
          "dibujado (mismo_perro). Ignora el estilo de dibujo y el fondo. Responde solo con JSON: "
          '{"color": n, "orejas": n, "hocico": n, "pliegues": n, "mismo_perro": n}')


def parse_scores(text):
    match = re.search(r"\{[^{}]*\}", text or "")
    try:
        data = json.loads(match[0]) if match else {}
    except ValueError:
        data = {}
    scores = {}
    for key in CRITERIA:
        value = data.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            scores[key] = max(0.0, min(10.0, float(value)))
    return scores if len(scores) == len(CRITERIA) else None


def digests(photos):
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in photos}


def score(ollama, model, photos_b64, image_b64):
    totals, valid = {k: 0.0 for k in CRITERIA}, 0
    for photo in photos_b64:
        reply = ollama._request("/api/chat", {"model": model, "stream": False, "keep_alive": "2m",
                                              "messages": [{"role": "user", "content": PROMPT, "images": [photo, image_b64]}],
                                              "options": {"temperature": 0, "num_predict": 200}})
        scores = parse_scores((reply.get("message") or {}).get("content"))
        if scores:
            valid += 1
            for key in CRITERIA:
                totals[key] += scores[key]
    if not valid:
        return None
    averages = {k: round(v / valid, 2) for k, v in totals.items()}
    return {"scores": averages, "total": round(sum(averages.values()) / len(CRITERIA), 2), "comparisons": valid}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--photos", type=Path, default=ROOT / "avatar")
    parser.add_argument("--candidates", type=Path, default=ROOT / "assets/leo/generated/candidates")
    parser.add_argument("--baseline", type=Path, default=ROOT / "assets/leo/sit.svg")
    parser.add_argument("--model", default="")
    parser.add_argument("--max-photos", type=int, default=3)
    parser.add_argument("--output", type=Path, default=ROOT / "assets/leo/generated/scores.json")
    args = parser.parse_args(argv)
    from leo.providers import Ollama
    from leo.vision import PREFERRED, encode
    ollama = Ollama()
    vision = [m for m in ollama.models() if "vision" in ollama.capabilities_of(m)]
    model = args.model or (PREFERRED if PREFERRED in vision else vision[0] if vision else "")
    if model not in vision:
        raise SystemExit("No hay modelo de visión local instalado (ollama pull qwen2.5vl:7b) o el indicado no admite imágenes")
    photos = sorted(p for p in args.photos.glob("*") if p.suffix.lower() in {".jpg", ".jpeg", ".png"})[:args.max_photos]
    if not photos:
        raise SystemExit(f"No hay fotos en {args.photos}")
    before = digests(photos)
    photos_b64 = [encode(p)[0] for p in photos]
    images = [("baseline:" + args.baseline.name, args.baseline)]
    images += [(p.name, p) for p in sorted(args.candidates.glob("*.png"))]
    results = []
    for name, path in images:
        result = score(ollama, model, photos_b64, encode(path)[0])
        results.append({"file": name, **(result or {"scores": None, "total": None, "comparisons": 0})})
        print(f"{name}: {result['total'] if result else 'sin puntuación válida'}", flush=True)
    if digests(photos) != before:
        raise SystemExit("Los SHA256 de las fotos cambiaron; no se guarda nada")
    baseline = results[0]
    ranked = sorted((r for r in results[1:] if r["total"] is not None), key=lambda r: -r["total"])
    report = {"model": model, "photos_used": len(photos), "baseline": baseline, "candidates": ranked,
              "better_than_baseline": [r["file"] for r in ranked if baseline["total"] is not None and r["total"] > baseline["total"]]}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"baseline": baseline["total"], "best": ranked[0]["total"] if ranked else None,
                      "better_than_baseline": len(report["better_than_baseline"])}, ensure_ascii=False))
    print(f"Puntuaciones: {args.output}. Abre la comparación: python3 scripts/leo_compare.py")


if __name__ == "__main__":
    main()
