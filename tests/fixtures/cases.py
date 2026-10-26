"""Synthetic fixtures for LEO_SPEC F6.8 (all content is fictional): equations rendered with pdflatex, a three-slide
presentation (fine, tiny text, pale text plus an image without alt text), a table PDF and training curves drawn with
cairo (overfitting, divergence, healthy)."""
import base64
import io
import math
from pathlib import Path
import subprocess
import zipfile

ENV = {"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"}
EQUATIONS = [
    ("einstein", r"E = mc^2"),
    ("fraccion", r"\frac{a+b}{c} = \sqrt{x^2 + 1}"),
    ("integral", r"\int_0^1 x^2 \, dx = \frac{1}{3}"),
    ("suma", r"\sum_{i=1}^{n} i = \frac{n(n+1)}{2}"),
    ("bayes", r"P(A \mid B) = \frac{P(B \mid A) P(A)}{P(B)}"),
    ("gauss", r"f(x) = \frac{1}{\sigma\sqrt{2\pi}} e^{-\frac{(x-\mu)^2}{2\sigma^2}}"),
    ("softmax", r"\sigma(z)_i = \frac{e^{z_i}}{\sum_{j=1}^{K} e^{z_j}}"),
    ("gradiente", r"\theta_{t+1} = \theta_t - \eta \nabla_\theta L(\theta_t)"),
    ("entropia", r"H(p, q) = -\sum_{x} p(x) \log q(x)"),
    ("atencion", r"\mathrm{Attention}(Q, K, V) = \mathrm{softmax}\left(\frac{QK^T}{\sqrt{d_k}}\right) V"),
]
TABLE = [["Modelo", "Exactitud", "Parámetros", "Época"],
         ["ResNet-18", "0.912", "11.7M", "30"],
         ["ResNet-50", "0.934", "25.6M", "45"],
         ["ViT-B/16", "0.947", "86.0M", "60"],
         ["MobileNetV3", "0.889", "5.4M", "25"]]


def run(argv, cwd):
    result = subprocess.run(argv, cwd=cwd, capture_output=True, text=True, timeout=180, env={**ENV, "HOME": str(cwd)})
    if result.returncode:
        raise RuntimeError(f"{argv[0]} falló: {result.stdout[-400:]}")


def equations(folder):
    """{name: png path} of each equation alone on a page (standalone, 300 dpi)."""
    folder = Path(folder)
    out = {}
    for name, body in EQUATIONS:
        (folder / f"{name}.tex").write_text("\\documentclass[border=8pt,varwidth]{standalone}\n\\usepackage{amsmath}\n"
                                            "\\begin{document}\n$\\displaystyle " + body + "$\n\\end{document}\n")
        run(["pdflatex", "-interaction=nonstopmode", "-halt-on-error", f"{name}.tex"], folder)
        run(["pdftoppm", "-r", "300", "-png", "-singlefile", f"{name}.pdf", name], folder)
        out[name] = folder / f"{name}.png"
    return out


def table_pdf(folder):
    folder = Path(folder)
    rows = " \\\\\n".join(" & ".join(cell.replace("%", "\\%") for cell in row) for row in TABLE)
    (folder / "tabla.tex").write_text("\\documentclass{article}\n\\usepackage[utf8]{inputenc}\n\\begin{document}\n"
                                      "Resultados ficticios del experimento.\n\n\\begin{tabular}{lrrr}\n" + rows +
                                      " \\\\\n\\end{tabular}\n\\end{document}\n")
    run(["pdflatex", "-interaction=nonstopmode", "-halt-on-error", "tabla.tex"], folder)
    return folder / "tabla.pdf"


def _chart_png():
    """A small bar chart (the slide's picture)."""
    import cairo
    surface = cairo.ImageSurface(cairo.FORMAT_RGB24, 320, 200)
    context = cairo.Context(surface)
    context.set_source_rgb(1, 1, 1)
    context.paint()
    for i, height in enumerate((60, 120, 90, 160)):
        context.set_source_rgb(0.2, 0.4, 0.8)
        context.rectangle(30 + i * 70, 190 - height, 45, height)
        context.fill()
    out = io.BytesIO()
    surface.write_to_png(out)
    return out.getvalue()


def presentation(folder):
    """{"fodp", "odp", "pptx"}: slide 1 fine, slide 2 tiny text, slide 3 pale text and an image without alt text.
    The ODP and PPTX hold only the package parts that readers use (content, theme, slides, relations, media)."""
    folder = Path(folder)
    image = base64.b64encode(_chart_png()).decode()
    ns = ('xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
          'xmlns:style="urn:oasis:names:tc:opendocument:xmlns:style:1.0" '
          'xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0" '
          'xmlns:draw="urn:oasis:names:tc:opendocument:xmlns:drawing:1.0" '
          'xmlns:fo="urn:oasis:names:tc:opendocument:xmlns:xsl-fo-compatible:1.0" '
          'xmlns:svg="urn:oasis:names:tc:opendocument:xmlns:svg-compatible:1.0" '
          'xmlns:presentation="urn:oasis:names:tc:opendocument:xmlns:presentation:1.0" '
          'xmlns:xlink="http://www.w3.org/1999/xlink"')

    def box(style, text, y="3cm", height="4cm"):
        return (f'<draw:frame svg:x="2cm" svg:y="{y}" svg:width="24cm" svg:height="{height}"><draw:text-box>'
                f'<text:p text:style-name="{style}">{text}</text:p></draw:text-box></draw:frame>')
    tiny = " ".join(["Detalle técnico del protocolo de enrutamiento que nadie alcanza a leer desde el fondo del aula."] * 3)
    xml = f'''<?xml version="1.0" encoding="UTF-8"?>
<office:document {ns} office:version="1.3" office:mimetype="application/vnd.oasis.opendocument.presentation">
<office:automatic-styles>
<style:style style:name="big" style:family="paragraph"><style:text-properties fo:font-size="32pt" fo:color="#111111"/></style:style>
<style:style style:name="body" style:family="paragraph"><style:text-properties fo:font-size="24pt" fo:color="#111111"/></style:style>
<style:style style:name="tiny" style:family="paragraph"><style:text-properties fo:font-size="9pt" fo:color="#111111"/></style:style>
<style:style style:name="pale" style:family="paragraph"><style:text-properties fo:font-size="24pt" fo:color="#dedede"/></style:style>
</office:automatic-styles>
<office:body><office:presentation>
<draw:page draw:name="uno">{box("big", "Redes de computadores", "2cm", "3cm")}{box("body", "Clase 3: enrutamiento dinámico", "6cm", "3cm")}</draw:page>
<draw:page draw:name="dos">{box("big", "Detalles", "1.5cm", "2.5cm")}{box("tiny", tiny, "5cm", "6cm")}</draw:page>
<draw:page draw:name="tres">{box("pale", "Resultados del laboratorio en gris muy claro", "1.5cm", "3cm")}
<draw:frame svg:x="6cm" svg:y="6cm" svg:width="12cm" svg:height="7.5cm"><draw:image><office:binary-data>{image}</office:binary-data></draw:image></draw:frame></draw:page>
</office:presentation></office:body></office:document>
'''
    fodp = folder / "clase3.fodp"
    fodp.write_text(xml)
    # The same slides as an ODP package and as a PPTX package (only the parts a reader needs; no Impress required).
    content = xml.replace(f"<office:binary-data>{image}</office:binary-data>", "").replace(
        "<draw:image>", '<draw:image xlink:href="Pictures/grafico.png">')
    with zipfile.ZipFile(folder / "clase3.odp", "w") as odp:
        odp.writestr("mimetype", "application/vnd.oasis.opendocument.presentation")
        odp.writestr("content.xml", content)
        odp.writestr("Pictures/grafico.png", _chart_png())
    a = 'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"'
    pns = ('xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" '
           'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"')

    def run_xml(text, size, color):
        kind = "srgbClr" if len(color) == 6 and all(c in "0123456789ABCDEFabcdef" for c in color) else "schemeClr"
        fill = f'<a:solidFill><a:{kind} val="{color}"/></a:solidFill>'
        return f'<a:r><a:rPr lang="es-ES" sz="{size * 100}">{fill}</a:rPr><a:t>{text}</a:t></a:r>'

    def slide(*runs, picture=False):
        body = "".join(f'<p:sp><p:txBody><a:p>{r}</a:p></p:txBody></p:sp>' for r in runs)
        if picture:
            body += ('<p:pic><p:nvPicPr><p:cNvPr id="4" name="Imagen 3" descr=""/></p:nvPicPr>'
                     '<p:blipFill><a:blip r:embed="rId2"/></p:blipFill></p:pic>')
        return f'<?xml version="1.0" encoding="UTF-8"?><p:sld {a} {pns}><p:cSld><p:spTree>{body}</p:spTree></p:cSld></p:sld>'
    theme = (f'<?xml version="1.0" encoding="UTF-8"?><a:theme {a} name="Leo"><a:themeElements><a:clrScheme name="Leo">'
             '<a:dk1><a:sysClr val="windowText" lastClr="111111"/></a:dk1><a:lt1><a:sysClr val="window" lastClr="FFFFFF"/></a:lt1>'
             '<a:dk2><a:srgbClr val="1F3864"/></a:dk2><a:lt2><a:srgbClr val="E7E6E6"/></a:lt2></a:clrScheme></a:themeElements></a:theme>')
    with zipfile.ZipFile(folder / "clase3.pptx", "w") as pptx:
        pptx.writestr("ppt/theme/theme1.xml", theme)
        pptx.writestr("ppt/slides/slide1.xml", slide(run_xml("Redes de computadores", 32, "tx1"),
                                                     run_xml("Clase 3: enrutamiento dinámico", 24, "tx1")))
        pptx.writestr("ppt/slides/slide2.xml", slide(run_xml("Detalles", 32, "tx1"), run_xml(tiny, 9, "tx1")))
        pptx.writestr("ppt/slides/slide3.xml", slide(run_xml("Resultados del laboratorio en gris muy claro", 24, "DEDEDE"),
                                                     picture=True))
        pptx.writestr("ppt/slides/_rels/slide3.xml.rels",
                      '<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/'
                      'package/2006/relationships"><Relationship Id="rId2" Type="http://schemas.openxmlformats.org/'
                      'officeDocument/2006/relationships/image" Target="../media/image1.png"/></Relationships>')
        pptx.writestr("ppt/media/image1.png", _chart_png())
    return {"fodp": fodp, "odp": folder / "clase3.odp", "pptx": folder / "clase3.pptx"}


def curves(folder):
    """{"sobreajuste", "divergencia", "sana"}: loss curves (train and validation) as PNG."""
    import cairo
    folder = Path(folder)
    epochs = list(range(1, 31))
    series = {
        "sobreajuste": ([2.3 * math.exp(-e / 6) + 0.05 for e in epochs],
                        [2.4 * math.exp(-e / 6) + 0.25 + (0.045 * (e - 10) ** 1.3 if e > 10 else 0) for e in epochs]),
        "divergencia": ([1.8 * math.exp(-e / 5) + 0.3 + (0.02 * math.exp((e - 12) / 3) if e > 12 else 0) for e in epochs],
                        [1.9 * math.exp(-e / 5) + 0.35 + (0.025 * math.exp((e - 12) / 3) if e > 12 else 0) for e in epochs]),
        "sana": ([2.2 * math.exp(-e / 7) + 0.1 for e in epochs], [2.3 * math.exp(-e / 7) + 0.15 for e in epochs]),
    }
    out = {}
    for name, (train, valid) in series.items():
        surface = cairo.ImageSurface(cairo.FORMAT_RGB24, 640, 420)
        c = cairo.Context(surface)
        c.set_source_rgb(1, 1, 1)
        c.paint()
        left, top, width, height = 70, 40, 540, 320
        top_value = max(max(train), max(valid)) * 1.05
        c.set_source_rgb(0, 0, 0)
        c.set_line_width(1.5)
        c.move_to(left, top)
        c.line_to(left, top + height)
        c.line_to(left + width, top + height)
        c.stroke()
        c.select_font_face("Sans")
        c.set_font_size(16)
        c.move_to(250, 28)
        c.show_text("loss por época")
        c.set_font_size(12)
        for e in (1, 10, 20, 30):
            c.move_to(left + (e - 1) / 29 * width - 6, top + height + 18)
            c.show_text(str(e))
        c.move_to(left + width / 2 - 20, top + height + 36)
        c.show_text("época")
        for values, color, label, y in ((train, (0.1, 0.3, 0.9), "train", 60), (valid, (0.9, 0.4, 0.1), "val", 80)):
            c.set_source_rgb(*color)
            c.set_line_width(2.5)
            for i, v in enumerate(values):
                x, yv = left + i / 29 * width, top + height - v / top_value * height
                c.line_to(x, yv) if i else c.move_to(x, yv)
            c.stroke()
            c.move_to(left + width - 90, y)
            c.line_to(left + width - 60, y)
            c.stroke()
            c.set_source_rgb(0, 0, 0)
            c.move_to(left + width - 55, y + 4)
            c.show_text(label)
        out[name] = folder / f"curvas-{name}.png"
        surface.write_to_png(str(out[name]))
    return out
