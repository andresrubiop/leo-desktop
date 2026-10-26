#!/usr/bin/env python3
"""Batería de F6.8 con los modelos locales reales sobre fixtures sintéticos (LEO_SPEC 12.9).

Ecuaciones renderizadas con pdflatex → LaTeX que compila (y coincide al normalizar); presentación ficticia (PPTX,
ODP y ODP plano) → problemas por diapositiva y texto alternativo propuesto; tabla de un PDF → celdas exactas; curvas
dibujadas con cairo → diagnóstico (sobreajuste, divergencia, sin problemas). Todo en una HOME temporal; no lee nada
del usuario. Uso: python3 scripts/eval_cases.py [--only latex,slides,table,curves] [--output informe.json]
"""
import argparse
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests/fixtures"))


def normal(latex):
    """LaTeX compared loosely: no environments, spacing, \\left/\\right, \\mathrm or braces around single symbols."""
    text = re.sub(r"\\(begin|end)\{[^}]*\}|\\left|\\right|\\[,;!]|\\mathrm|\\text|\\operatorname|\\displaystyle", "", latex)
    text = re.sub(r"\{([A-Za-z0-9])\}", r"\1", text)
    return re.sub(r"[\s{}]", "", text)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--only", default="latex,slides,table,curves")
    parser.add_argument("--output")
    args = parser.parse_args()
    wanted = set(args.only.split(","))
    with tempfile.TemporaryDirectory(prefix="leo-eval-cases-") as directory:
        home = Path(directory) / "home"
        (home / "Clases").mkdir(parents=True)
        os.environ.update(HOME=str(home), XDG_CONFIG_HOME=str(home / ".config"), XDG_DATA_HOME=str(home / ".local/share"))
        import cases as fixtures
        from leo import cases
        from leo.config import Settings
        from leo.files import Files
        from leo.providers import Ollama
        from leo.vision import Vision, load
        settings = Settings()
        settings.update({"files": True, "folders": [{"id": "clases", "path": str(home / "Clases"), "label": "Clases",
                                                     "write": False}]})
        files, vision = Files(settings), Vision(Ollama(), settings)
        folder = home / "Clases"
        report = {}
        if "latex" in wanted:
            items = []
            for name, png in fixtures.equations(folder).items():
                started = time.monotonic()
                result = cases.file_equation(vision, files, str(png), 0)
                expected = dict(fixtures.EQUATIONS)[name]
                items.append({"name": name, "compiles": result["compiles"], "match": normal(result["latex"]) == normal(expected),
                              "latex": result["latex"], "expected": expected, "seconds": round(time.monotonic() - started, 1)})
            report["latex"] = {"compiles": sum(i["compiles"] for i in items), "match": sum(i["match"] for i in items),
                               "total": len(items), "items": items}
        if "slides" in wanted:
            made = fixtures.presentation(folder)
            out = {}
            for kind in ("pptx", "odp", "fodp"):
                review = cases.review_slides(files, str(made[kind]), vision if kind == "pptx" else None)
                issues = {s["slide"]: s["issues"] for s in review["slides"]}
                out[kind] = {"ok": not issues[1] and any("pequeño" in i for i in issues[2]) and
                             any("contraste" in i for i in issues[3]) and [a["slide"] for a in review["missing_alt"]] == [3],
                             "issues": issues, "alt": review["missing_alt"]}
            report["slides"] = out
        if "table" in wanted:
            result = cases.pdf_table(files, str(fixtures.table_pdf(folder)), 1, vision)
            expected = fixtures.TABLE
            rows = [[c.strip() for c in line.strip("|").split("|")] for line in result["markdown"].splitlines()
                    if line.startswith("|") and not set(line) <= set("|-: ")]
            report["table"] = {"exact": rows == expected, "source": result["source"], "rows": rows}
        if "curves" in wanted:
            items = []
            for name, png in fixtures.curves(folder).items():
                result = cases.review_curves(vision, files, str(png))
                verdict = result.get("verdict") or "?"
                items.append({"name": name, "verdict": verdict, "ok": verdict == ("sin problemas" if name == "sana" else name),
                              "diagnosis": result["diagnosis"][:400]})
            report["curves"] = {"right": sum(i["ok"] for i in items), "total": len(items), "items": items}
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        Path(args.output).write_text(text)
    print(text)


if __name__ == "__main__":
    main()
