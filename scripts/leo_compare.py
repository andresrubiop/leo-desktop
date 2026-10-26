#!/usr/bin/env python3
"""Página local para comparar candidatos con las fotos de Leo y elegir (LEO_SPEC F5.2; G8: la ve el usuario).

Genera `assets/leo/generated/compare.html` (fotos, avatar actual y candidatos lado a lado con sus
puntuaciones locales). Ábrela tú con `xdg-open`; el agente no la abre ni la lee. La elección se guarda con
`--choose ARCHIVO` en `assets/leo/generated/chosen.json`. Nada de esto se publica.
"""
import argparse
from html import escape
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STYLE = """body{font-family:system-ui,sans-serif;margin:24px;background:#f6f5f4;color:#241f31}
h1{font-size:22px}section{display:flex;flex-wrap:wrap;gap:16px;margin-bottom:28px}
figure{margin:0;background:#fff;border-radius:12px;padding:10px;box-shadow:0 1px 3px #0002;width:300px}
img{width:100%;height:280px;object-fit:contain;background:#eee;border-radius:8px}
figcaption{font-size:13px;margin-top:6px}code{background:#eee;padding:2px 4px;border-radius:4px;font-size:12px}
.best{outline:3px solid #2ec27e}"""


def page(generated, photos, scores):
    def rel(path):
        return escape(os.path.relpath(path, generated))
    ranked = {c["file"]: c for c in scores.get("candidates", [])}
    best = scores["candidates"][0]["file"] if scores.get("candidates") else None
    baseline = scores.get("baseline") or {}
    parts = [f"<!doctype html><html lang='es'><meta charset='utf-8'><title>Leo · candidatos</title><style>{STYLE}</style>",
             "<h1>Leo · elige la hoja de modelo</h1><p>Puntuación local 0–10 (media de color, orejas, hocico, pliegues y "
             f"mismo perro) con <code>{escape(str(scores.get('model', '—')))}</code>. Avatar actual: "
             f"<b>{escape(str(baseline.get('total', '—')))}</b>.</p><h2>Fotos</h2><section>"]
    parts += [f"<figure><img src='{rel(p)}' alt='foto'><figcaption>{escape(p.name)}</figcaption></figure>" for p in photos]
    parts.append("</section><h2>Candidatos</h2><section>")
    for candidate in sorted((generated / "candidates").glob("*.png"), key=lambda p: -(ranked.get(p.name, {}).get("total") or 0)):
        info = ranked.get(candidate.name, {})
        detail = ", ".join(f"{k} {v}" for k, v in (info.get("scores") or {}).items())
        parts.append(f"<figure class='{'best' if candidate.name == best else ''}'><img src='{rel(candidate)}' alt='candidato'>"
                     f"<figcaption><b>{escape(str(info.get('total', '—')))}</b> · {escape(detail)}<br>"
                     f"<code>python3 scripts/leo_compare.py --choose {escape(candidate.name)}</code></figcaption></figure>")
    parts.append("</section></html>")
    return "".join(parts)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--generated", type=Path, default=ROOT / "assets/leo/generated")
    parser.add_argument("--photos", type=Path, default=ROOT / "avatar")
    parser.add_argument("--choose", default="")
    args = parser.parse_args(argv)
    if args.choose:
        chosen = args.generated / "candidates" / Path(args.choose).name
        if not chosen.is_file():
            raise SystemExit(f"No existe {chosen}")
        (args.generated / "chosen.json").write_text(json.dumps({"file": chosen.name}) + "\n")
        print(f"Elegido {chosen.name}. Siguiente: python3 scripts/leo_sprites.py --views")
        return
    scores_file = args.generated / "scores.json"
    scores = json.loads(scores_file.read_text()) if scores_file.exists() else {}
    photos = sorted(p for p in args.photos.glob("*") if p.suffix.lower() in {".jpg", ".jpeg", ".png"})
    output = args.generated / "compare.html"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(page(args.generated, photos, scores))
    print(f"Ábrela tú: xdg-open {output}")


if __name__ == "__main__":
    main()
