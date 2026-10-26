"""Casos del profesor, investigador y desarrollador de IA (LEO_SPEC F6.8). Solo lectura y modelos locales.

- Ecuación → LaTeX (imagen, página de PDF o pantalla), comprobada compilándola con `pdflatex` en un directorio
  temporal (sin shell-escape, con tiempo límite) antes de dársela al usuario.
- Revisión de diapositivas: texto pequeño, exceso de texto y bajo contraste por diapositiva, e imágenes sin texto
  alternativo con una propuesta del modelo de visión pensada para estudiantes con discapacidad visual. PPTX, ODP y
  ODP plano se leen de su XML (tamaño declarado, colores del texto y del fondo con los del tema, contraste WCAG);
  un PDF, con las cajas de `pdftotext -bbox-layout` y los píxeles de `pdftoppm`. No hace falta LibreOffice Impress
  y el original no se toca.
- Tabla de un PDF → Markdown y CSV (texto con `pdftotext -layout`; visión si la página no tiene texto), con aviso.
- Curvas de entrenamiento: la imagen más reciente de la carpeta del run → diagnóstico (sobreajuste, divergencia,
  estancamiento, NaN).
Nada se guarda: los temporales se borran al terminar. Lo leído es dato, no instrucciones.
"""
import csv
import io
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unicodedata
import xml.etree.ElementTree as ET
import zipfile

ENV = {"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"}
MIN_POINTS = 18       # texto de diapositiva más pequeño que esto se señala
MAX_WORDS = 90        # palabras por diapositiva
MIN_CONTRAST = 4.5    # WCAG AA para texto normal
MAX_IMAGES = 8
IMAGES = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif"}
CURVE_NAMES = re.compile(r"loss|curve|train|valid|metric|acc|perdida|pérdida|curva", re.I)
LATEX = ("Transcribe a LaTeX la ecuación que se ve en la imagen, exactamente como aparece (símbolos, índices, "
         "fracciones). Responde solo con el código dentro de \\begin{equation} … \\end{equation} (usa align* si son "
         "varias líneas), sin explicaciones.")
ALT = ("Escribe un texto alternativo en español para esta imagen de una diapositiva, pensado para un estudiante con "
       "discapacidad visual: qué muestra y para qué sirve en la clase, en una o dos frases y sin empezar por «imagen de».")
# The vision model only reads how each curve ends; Leo decides (measured: asked for a verdict, qwen2.5vl:7b said
# «sobreajuste» for all three synthetic runs; reading the trends it got all three right).
TRENDS = ("Mira el gráfico de curvas de entrenamiento. Para cada curva de la leyenda di cómo termina en las últimas "
          "épocas comparado con su punto más bajo: «baja» (sigue bajando o se mantiene cerca de su mínimo) o «sube» "
          "(después de bajar, termina claramente por encima de su mínimo). Responde solo JSON: "
          '[{"curva": "<nombre en la leyenda>", "final": "baja" | "sube"}]')
CURVES = ("Son curvas de entrenamiento de un modelo. Describe en español cómo evolucionan y si ves sobreajuste, "
          "divergencia, estancamiento o valores NaN.")
VERDICTS = {
    "divergencia": "La pérdida de entrenamiento vuelve a subir: el entrenamiento diverge. Baja la tasa de aprendizaje, "
                   "recorta gradientes o revisa los datos y la inicialización.",
    "sobreajuste": "La validación empeora mientras el entrenamiento sigue mejorando: hay sobreajuste. Quédate con la "
                   "mejor época (parada temprana), añade regularización (dropout, weight decay) o más datos.",
    "sin problemas": "Las curvas bajan y se estabilizan: no veo sobreajuste ni divergencia.",
}
TABLE = ("Transcribe la tabla de la imagen a una tabla Markdown, celda por celda, sin inventar ni completar valores. "
         "Responde solo con la tabla.")


def _tool(name):
    executable = shutil.which(name, path="/usr/bin:/bin")
    if not executable:
        raise RuntimeError(f"Falta {name} en el sistema")
    return executable


# ── Ecuación → LaTeX ──────────────────────────────────────────────────────────

def latex_body(answer):
    """The model's LaTeX without fences or prose, inside one math environment."""
    text = re.sub(r"```(?:latex|tex)?", "", answer or "").strip()
    match = re.search(r"\\begin\{(equation\*?|align\*?|gather\*?)\}.*?\\end\{\1\}", text, re.S)
    if match:
        return match.group(0).strip()
    text = text.strip().strip("$").strip()
    text = re.sub(r"^\\\[|\\\]$", "", text).strip()
    return "\\begin{equation}\n" + text + "\n\\end{equation}"


def compiles(body, run=subprocess.run):
    """(True, "") if a minimal amsmath document with `body` compiles; else (False, the first error line)."""
    document = ("\\documentclass{article}\n\\usepackage{amsmath,amssymb}\n\\begin{document}\n" + body +
                "\n\\end{document}\n")
    with tempfile.TemporaryDirectory(prefix="leo-latex-") as directory:
        (Path(directory) / "eq.tex").write_text(document)
        try:
            result = run([_tool("pdflatex"), "-interaction=nonstopmode", "-halt-on-error", "-no-shell-escape", "eq.tex"],
                         cwd=directory, capture_output=True, text=True, timeout=40, env={**ENV, "HOME": directory})
        except subprocess.TimeoutExpired:
            return False, "pdflatex no terminó a tiempo"
        if result.returncode == 0 and (Path(directory) / "eq.pdf").exists():
            return True, ""
        error = next((line for line in result.stdout.splitlines() if line.startswith("!")), "error de compilación")
        return False, error[:200]


def image_of(files, path, page, directory):
    """An authorized image, or one page of an authorized PDF rasterized into `directory`."""
    from .vision import pdf_page
    target = files.resolve(path, directory=False)
    suffix = target.suffix.lower()
    if suffix == ".pdf":
        if page < 1:
            raise ValueError("Indica la página del PDF (1, 2, …)")
        return pdf_page(target, page, directory), target
    if suffix not in IMAGES:
        raise ValueError("Una imagen (PNG, JPG…) o una página de PDF")
    return target, target


def equation(vision, pixbuf):
    from .vision import PREAMBLE
    answer, _ = vision._look(vision.model(), PREAMBLE + LATEX, pixbuf)
    body = latex_body(answer)
    ok, error = compiles(body)
    return {"latex": body, "compiles": ok, **({"error": error} if error else {}),
            "note": "Compilado con pdflatex: listo para Beamer." if ok else
            "No compila tal cual: revisa la línea indicada antes de usarlo."}


def file_equation(vision, files, path, page):
    from .vision import load
    with tempfile.TemporaryDirectory(prefix="leo-latex-img-") as directory:
        os.chmod(directory, 0o700)
        image, target = image_of(files, path, page, directory)
        return {**equation(vision, load(image)), "path": str(target), **({"page": page} if page else {})}


# ── Diapositivas ──────────────────────────────────────────────────────────────

A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
P = "{http://schemas.openxmlformats.org/presentationml/2006/main}"
ODF = {"office": "urn:oasis:names:tc:opendocument:xmlns:office:1.0", "style": "urn:oasis:names:tc:opendocument:xmlns:style:1.0",
       "text": "urn:oasis:names:tc:opendocument:xmlns:text:1.0", "draw": "urn:oasis:names:tc:opendocument:xmlns:drawing:1.0",
       "fo": "urn:oasis:names:tc:opendocument:xmlns:xsl-fo-compatible:1.0",
       "svg": "urn:oasis:names:tc:opendocument:xmlns:svg-compatible:1.0", "xlink": "http://www.w3.org/1999/xlink"}
SCHEME = {"tx1": "dk1", "bg1": "lt1", "tx2": "dk2", "bg2": "lt2"}


def ratio(foreground, background):
    """WCAG contrast ratio of two #rrggbb colours."""
    def lum(color):
        color = color.lstrip("#")
        return luminance(*(int(color[i:i + 2], 16) for i in (0, 2, 4)))
    light, dark = sorted((lum(foreground), lum(background)), reverse=True)
    return (light + 0.05) / (dark + 0.05)


def pptx_runs(path):
    """[{"slide", "runs": [(text, size pt | None, colour | None)], "background"}] from the PPTX XML."""
    slides = []
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        theme = {}
        if "ppt/theme/theme1.xml" in names:
            for element in ET.fromstring(archive.read("ppt/theme/theme1.xml")).iter():
                if element.tag.startswith(A) and element.tag[len(A):] in {"dk1", "lt1", "dk2", "lt2"} | {f"accent{i}" for i in range(1, 7)}:
                    color = element.find(f"{A}srgbClr")
                    system = element.find(f"{A}sysClr")
                    value = color.get("val") if color is not None else system.get("lastClr") if system is not None else None
                    if value:
                        theme[element.tag[len(A):]] = "#" + value

        def colour(parent):
            fill = parent.find(f"{A}solidFill") if parent is not None else None
            if fill is None:
                return None
            rgb, scheme = fill.find(f"{A}srgbClr"), fill.find(f"{A}schemeClr")
            if rgb is not None:
                return "#" + rgb.get("val")
            if scheme is not None:
                return theme.get(SCHEME.get(scheme.get("val"), scheme.get("val")))
            return None
        slide_names = sorted((n for n in names if re.fullmatch(r"ppt/slides/slide\d+\.xml", n)),
                             key=lambda n: int(re.search(r"(\d+)", n).group(1)))
        for name in slide_names:
            root = ET.fromstring(archive.read(name))
            background = colour(root.find(f".//{P}bg/{P}bgPr"))
            runs = []
            for run_element in root.iter(f"{A}r"):
                props = run_element.find(f"{A}rPr")
                text = "".join(t.text or "" for t in run_element.iter(f"{A}t"))
                size = int(props.get("sz")) / 100 if props is not None and props.get("sz") else None
                runs.append((text, size, colour(props)))
            slides.append({"slide": int(re.search(r"(\d+)", name).group(1)), "runs": runs, "background": background})
    return slides


def odp_runs(path):
    """The same from an ODP (content.xml and styles.xml) or a flat ODP (.fodp)."""
    if path.suffix.lower() == ".fodp":
        roots = [ET.fromstring(path.read_bytes())]
    else:
        with zipfile.ZipFile(path) as archive:
            roots = [ET.fromstring(archive.read(n)) for n in ("styles.xml", "content.xml") if n in archive.namelist()]
    styles = {}
    for root in roots:
        for style in root.iter(f"{{{ODF['style']}}}style"):
            props = style.find(f"{{{ODF['style']}}}text-properties")
            if props is not None:
                size = props.get(f"{{{ODF['fo']}}}font-size", "")
                styles[style.get(f"{{{ODF['style']}}}name")] = (
                    float(size[:-2]) if size.endswith("pt") else None, props.get(f"{{{ODF['fo']}}}color"))
    slides = []
    for root in roots:
        for number, page in enumerate(root.iter(f"{{{ODF['draw']}}}page"), 1):
            runs = []
            for paragraph in page.iter(f"{{{ODF['text']}}}p"):
                size, color = styles.get(paragraph.get(f"{{{ODF['text']}}}style-name"), (None, None))
                runs.append(("".join(paragraph.itertext()), size, color))
            slides.append({"slide": number, "runs": runs, "background": None})
    return slides


def words(pdf, run=subprocess.run):
    """[(page width, page height, [(xmin, ymin, xmax, ymax, word)])] from pdftotext -bbox-layout (points)."""
    result = run([_tool("pdftotext"), "-bbox-layout", str(pdf), "-"], capture_output=True, text=True, timeout=120, env=ENV)
    if result.returncode:
        raise RuntimeError("No pude leer el texto del PDF")
    xml = re.sub(r"<!DOCTYPE[^>]*>", "", result.stdout)
    xml = re.sub(r'\sxmlns="[^"]+"', "", xml, count=1)
    root = ET.fromstring(xml)
    pages = []
    for page in root.iter("page"):
        boxes = [(float(w.get("xMin")), float(w.get("yMin")), float(w.get("xMax")), float(w.get("yMax")), w.text or "")
                 for w in page.iter("word")]
        pages.append((float(page.get("width")), float(page.get("height")), boxes))
    return pages


def luminance(r, g, b):
    def channel(c):
        c /= 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)


def contrast(pixbuf, box):
    """Contrast ratio between text and background inside a word box (darkest vs lightest tenth of pixels)."""
    x, y, w, h = (max(0, int(v)) for v in box)
    w, h = min(w, pixbuf.get_width() - x), min(h, pixbuf.get_height() - y)
    if w < 2 or h < 2:
        return None
    pixels, stride, channels = pixbuf.get_pixels(), pixbuf.get_rowstride(), pixbuf.get_n_channels()
    values = sorted(luminance(*pixels[(y + j) * stride + (x + i) * channels:(y + j) * stride + (x + i) * channels + 3])
                    for j in range(h) for i in range(w))
    tenth = max(1, len(values) // 10)
    dark, light = sum(values[:tenth]) / tenth, sum(values[-tenth:]) / tenth
    return (light + 0.05) / (dark + 0.05)


def missing_alt(path):
    """[(slide, image bytes, suffix)] of pictures without a description, from the PPTX or ODP package."""
    found = []
    if path.suffix.lower() == ".fodp":  # Flat ODP: pictures inline as base64.
        import base64
        xml = path.read_text(errors="replace")
        for number, page in enumerate(re.findall(r"<draw:page\b.*?</draw:page>", xml, re.S), 1):
            for frame in re.findall(r"<draw:frame\b.*?</draw:frame>", page, re.S):
                data = re.search(r"<office:binary-data>(.*?)</office:binary-data>", frame, re.S)
                if data and not re.search(r"<svg:(?:desc|title)>\s*\S", frame):
                    found.append((number, base64.b64decode(re.sub(r"\s", "", data.group(1))), ".png"))
        return found[:MAX_IMAGES]
    try:
        archive = zipfile.ZipFile(path)
    except (zipfile.BadZipFile, OSError):
        return found
    with archive:
        names = archive.namelist()
        if path.suffix.lower() == ".pptx":
            slides = sorted((n for n in names if re.fullmatch(r"ppt/slides/slide\d+\.xml", n)),
                            key=lambda n: int(re.search(r"(\d+)", n).group(1)))
            for slide in slides:
                number = int(re.search(r"(\d+)", slide).group(1))
                xml = archive.read(slide).decode(errors="replace")
                rels = f"ppt/slides/_rels/slide{number}.xml.rels"
                targets = dict(re.findall(r'Id="([^"]+)"[^>]*Target="([^"]+)"',
                                          archive.read(rels).decode(errors="replace"))) if rels in names else {}
                for pic in re.findall(r"<p:pic>.*?</p:pic>", xml, re.S):
                    described = re.search(r'<p:cNvPr[^>]*\sdescr="([^"]+)"', pic)
                    embed = re.search(r'r:embed="([^"]+)"', pic)
                    if described or not embed or embed.group(1) not in targets:
                        continue
                    media = os.path.normpath(os.path.join("ppt/slides", targets[embed.group(1)])).replace("\\", "/")
                    if media in names:
                        found.append((number, archive.read(media), Path(media).suffix.lower()))
        elif path.suffix.lower() == ".odp" and "content.xml" in names:
            xml = archive.read("content.xml").decode(errors="replace")
            for number, page in enumerate(re.findall(r"<draw:page\b.*?</draw:page>", xml, re.S), 1):
                for frame in re.findall(r"<draw:frame\b.*?</draw:frame>", page, re.S):
                    image = re.search(r'<draw:image[^>]*xlink:href="([^"]+)"', frame)
                    if not image or re.search(r"<svg:(?:desc|title)>\s*\S", frame):
                        continue
                    if image.group(1) in names:
                        found.append((number, archive.read(image.group(1)), Path(image.group(1)).suffix.lower()))
    return found[:MAX_IMAGES]


def review_slides(files, path, vision=None, run=subprocess.run):
    from .vision import load
    target = files.resolve(path, directory=False)
    suffix = target.suffix.lower()
    if suffix not in {".pptx", ".odp", ".fodp", ".pdf"}:
        raise ValueError("Presentaciones PPTX, ODP o PDF")
    slides = []
    if suffix == ".pdf":
        with tempfile.TemporaryDirectory(prefix="leo-slides-") as directory:
            os.chmod(directory, 0o700)
            for number, (width, height, boxes) in enumerate(words(target, run), 1):
                issues = []
                # A PDF keeps the slide's size in points: a word box is about 1.2 × its font size.
                sizes = [(y2 - y1) / 1.2 for x1, y1, x2, y2, text in boxes if text.strip()]
                small = [size for size in sizes if size < MIN_POINTS]
                if small:
                    issues.append(f"texto pequeño ({round(min(small))} pt; mínimo recomendado {MIN_POINTS} pt)")
                if len(sizes) > MAX_WORDS:
                    issues.append(f"mucho texto ({len(sizes)} palabras; mejor menos de {MAX_WORDS})")
                if boxes and number <= 40:
                    image = load(raster(target, number, directory, run))
                    factor = image.get_width() / width
                    ratios = [r for r in (contrast(image, (x1 * factor, y1 * factor, (x2 - x1) * factor, (y2 - y1) * factor))
                                          for x1, y1, x2, y2, text in boxes[:40] if len(text) > 2) if r]
                    low = [r for r in ratios if r < MIN_CONTRAST]
                    if ratios and len(low) / len(ratios) > 0.3:
                        issues.append(f"poco contraste entre texto y fondo ({min(low):.1f}:1; mínimo {MIN_CONTRAST}:1)")
                slides.append({"slide": number, "words": len(sizes), "issues": issues})
    else:
        for slide in (pptx_runs(target) if suffix == ".pptx" else odp_runs(target)):
            issues = []
            texts = [(text, size, color) for text, size, color in slide["runs"] if text.strip()]
            count = sum(len(text.split()) for text, _, _ in texts)
            small = [size for _, size, _ in texts if size and size < MIN_POINTS]
            if small:
                issues.append(f"texto pequeño ({round(min(small))} pt; mínimo recomendado {MIN_POINTS} pt)")
            if count > MAX_WORDS:
                issues.append(f"mucho texto ({count} palabras; mejor menos de {MAX_WORDS})")
            background = slide["background"] or "#ffffff"
            low = [ratio(color, background) for _, _, color in texts if color and re.fullmatch(r"#[0-9a-fA-F]{6}", color)]
            low = [r for r in low if r < MIN_CONTRAST]
            if low:
                issues.append(f"poco contraste entre texto y fondo ({min(low):.1f}:1; mínimo {MIN_CONTRAST}:1)")
            slides.append({"slide": slide["slide"], "words": count, "issues": issues})
    alts = []
    for number, data, suffix in missing_alt(target):
        item = {"slide": number}
        if vision is not None:
            try:
                answer, _ = vision._look(vision.model(), ALT, _pixbuf(data))
                item["suggested_alt"] = " ".join(answer.split())[:300]
            except Exception as exc:  # One image never stops the review.
                item["error"] = str(exc)[:120]
        alts.append(item)
    lines = [f"# Revisión de {target.name}", ""]
    for slide in slides:
        lines.append(f"- Diapositiva {slide['slide']}: " + ("; ".join(slide["issues"]) if slide["issues"] else "sin problemas"))
    if alts:
        lines += ["", "## Imágenes sin texto alternativo"]
        lines += [f"- Diapositiva {a['slide']}: " + (f"«{a['suggested_alt']}»" if a.get("suggested_alt") else "sin propuesta")
                  for a in alts]
    return {"path": str(target), "slides": slides, "missing_alt": alts, "report": "\n".join(lines)}


def raster(pdf, page, directory, run=subprocess.run, dpi=72):
    """One page at a low resolution: enough to measure contrast."""
    prefix = Path(directory) / f"contraste-{page}"
    result = run([_tool("pdftoppm"), "-f", str(page), "-l", str(page), "-r", str(dpi), "-png", "-singlefile", str(pdf),
                  str(prefix)], capture_output=True, timeout=60, env=ENV)
    image = prefix.with_suffix(".png")
    if result.returncode or not image.exists():
        raise RuntimeError(f"No pude rasterizar la diapositiva {page}")
    return image


def _pixbuf(data):
    import gi
    gi.require_version("GdkPixbuf", "2.0")
    from gi.repository import GdkPixbuf
    loader = GdkPixbuf.PixbufLoader()
    try:
        loader.write(data)
    finally:
        loader.close()
    return loader.get_pixbuf()


# ── Tablas ────────────────────────────────────────────────────────────────────

def table_from_text(text):
    """The longest block of ≥ 3 consecutive lines splitting into the same ≥ 2 columns (2+ spaces apart). The line
    just above it (a header whose words may be one space apart) is cut at the columns where the block's cells start."""
    lines = text.splitlines()
    spans = [[m.span() for m in re.finditer(r"\S+(?: \S+)*", line)] for line in lines]
    best, start, width = (0, 0), None, None
    for index, cells in enumerate(spans + [[]]):
        if len(cells) >= 2 and (width is None or len(cells) == width):
            start, width = (index if start is None else start), len(cells)
        else:
            if start is not None and index - start >= 3 and index - start > best[1] - best[0]:
                best = (start, index)
            start, width = (index, len(cells)) if len(cells) >= 2 else (None, None)
    if best[1] - best[0] < 3:
        return []
    rows = [[lines[i][a:b].strip() for a, b in spans[i]] for i in range(*best)]
    above = best[0] - 1
    while above >= 0 and not lines[above].strip():
        above -= 1
    if above >= 0 and best[0] - above <= 2 and len(spans[above]) >= 2:
        # Each header word goes to the column it overlaps most (ties: the nearest centre); this also works for
        # right-aligned numeric columns and for headers whose words are one space apart.
        columns = [(min(spans[i][j][0] for i in range(*best)), max(spans[i][j][1] for i in range(*best)))
                   for j in range(len(rows[0]))]
        cells = [[] for _ in columns]
        for word in re.finditer(r"\S+", lines[above]):
            a, b = word.span()
            j = max(range(len(columns)), key=lambda k: (max(0, min(b, columns[k][1]) - max(a, columns[k][0])),
                                                        -abs((a + b) / 2 - sum(columns[k]) / 2)))
            cells[j].append(word.group())
        header = [" ".join(cell) for cell in cells]
        if all(header):
            rows.insert(0, header)
    return rows


def markdown(rows):
    head, *body = rows
    lines = ["| " + " | ".join(head) + " |", "|" + "|".join("---" for _ in head) + "|"]
    return "\n".join(lines + ["| " + " | ".join(row) + " |" for row in body])


def pdf_table(files, path, page, vision=None, run=subprocess.run):
    from .vision import PREAMBLE, load, pdf_page
    target = files.resolve(path, directory=False)
    if target.suffix.lower() != ".pdf" or page < 1:
        raise ValueError("Un PDF y su página (1, 2, …)")
    result = run([_tool("pdftotext"), "-layout", "-f", str(page), "-l", str(page), str(target), "-"],
                 capture_output=True, text=True, timeout=60, env=ENV)
    # pdftotext may give LaTeX accents decomposed («a» + U+0301): compose them so cells compare and search as text.
    rows = table_from_text(unicodedata.normalize("NFC", result.stdout) if result.returncode == 0 else "")
    warning = "Verifica las cifras con el original antes de usarlas."
    if rows:
        out = io.StringIO()
        csv.writer(out).writerows(rows)
        return {"path": str(target), "page": page, "source": "texto del PDF", "rows": len(rows), "columns": len(rows[0]),
                "markdown": markdown(rows), "csv": out.getvalue(), "warning": warning}
    if vision is None:
        raise LookupError("No encontré una tabla en el texto de esa página")
    with tempfile.TemporaryDirectory(prefix="leo-table-") as directory:
        os.chmod(directory, 0o700)
        answer, _ = vision._look(vision.model(), PREAMBLE + TABLE, load(pdf_page(target, page, directory)))
    return {"path": str(target), "page": page, "source": "modelo de visión (la página no tiene texto)",
            "markdown": answer.strip(), "warning": "Leída de la imagen: " + warning}


# ── Curvas de entrenamiento ───────────────────────────────────────────────────

def newest_curve(folder, depth=2):
    """The most recently modified image in the run folder, preferring names that look like curves."""
    images = []
    for current, dirs, names in os.walk(folder):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        if len(Path(current).relative_to(folder).parts) >= depth:
            dirs[:] = []
        images += [Path(current) / n for n in names if Path(n).suffix.lower() in IMAGES and not n.startswith(".")]
    if not images:
        raise LookupError("No hay imágenes de curvas en esa carpeta")
    named = [p for p in images if CURVE_NAMES.search(p.name)] or images
    return max(named, key=lambda p: p.stat().st_mtime)


def review_curves(vision, files, path):
    from .vision import PREAMBLE, load
    target = files.resolve(path)
    image = newest_curve(target) if target.is_dir() else target
    image = files.resolve(str(image), directory=False)
    if image.suffix.lower() not in IMAGES:
        raise ValueError("Una imagen de curvas o la carpeta del run")
    pixbuf = load(image)
    answer, _ = vision._look(vision.model(), PREAMBLE + TRENDS, pixbuf)
    verdict, readings = trend_verdict(answer)
    if verdict:
        return {"image": str(image), "verdict": verdict, "readings": readings, "diagnosis": VERDICTS[verdict],
                "note": "Leído del gráfico por el modelo de visión local: compruébalo con los valores del log."}
    answer, _ = vision._look(vision.model(), PREAMBLE + CURVES, pixbuf)
    return {"image": str(image), "verdict": None, "diagnosis": answer.strip()}


def trend_verdict(answer):
    """(verdict, readings) from [{"curva", "final"}]: training rising → divergence; validation rising while training
    falls → overfitting; everything falling → fine. (None, readings) when the curves cannot be told apart."""
    import json
    match = re.search(r"\[.*\]", answer or "", re.S)
    try:
        readings = json.loads(match.group(0)) if match else []
    except ValueError:
        readings = []
    readings = [r for r in readings if isinstance(r, dict) and r.get("final") in {"baja", "sube"}]
    train = [r for r in readings if re.search(r"train|entren", str(r.get("curva", "")), re.I)]
    valid = [r for r in readings if re.search(r"val|test|eval|dev|prueba", str(r.get("curva", "")), re.I)]
    if not train and not valid:
        return None, readings
    if any(r["final"] == "sube" for r in train):
        return "divergencia", readings
    if any(r["final"] == "sube" for r in valid):
        return "sobreajuste", readings
    return "sin problemas", readings
