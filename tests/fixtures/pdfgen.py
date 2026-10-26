"""Minimal synthetic PDFs for tests (Courier, WinAnsi): one text line per row, several pages.

Avoids depending on pdflatex; pdftotext reads them like any other text PDF. Content is fictional.
"""


def make_pdf(path, pages, size=10):
    objects = ["<< /Type /Catalog /Pages 2 0 R >>", None,
               "<< /Type /Font /Subtype /Type1 /BaseFont /Courier /Encoding /WinAnsiEncoding >>"]
    kids = []
    for lines in pages:
        text = "".join(
            "BT /F1 %d Tf 40 %d Td (%s) Tj ET\n" % (size, 800 - i * (size + 4),
                                                    line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)"))
            for i, line in enumerate(lines))
        stream = text.encode("cp1252")
        objects.append(b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream")
        objects.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 842 842] /Resources << /Font << /F1 3 0 R >> >> "
                       f"/Contents {len(objects)} 0 R >>")
        kids.append(len(objects))
    objects[1] = f"<< /Type /Pages /Kids [{' '.join(f'{k} 0 R' for k in kids)}] /Count {len(kids)} >>"
    out, offsets = bytearray(b"%PDF-1.4\n"), []
    for number, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + (body if isinstance(body, bytes) else body.encode("cp1252")) + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    out += "".join(f"{o:010d} 00000 n \n" for o in offsets).encode()
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    with open(path, "wb") as stream:
        stream.write(out)


def make_layout_pdf(path, pages, width=612, height=792):
    """Pages of positioned items, top-left coordinates in points like pdftotext -bbox-layout:
    ("text", x, y, size, line) and ("rect", x, y, w, h, gray) for figure graphics. Content is fictional."""
    objects = ["<< /Type /Catalog /Pages 2 0 R >>", None,
               "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>"]
    kids = []
    for items in pages:
        ops = []
        for item in items:
            if item[0] == "text":
                _, x, y, size, line = item
                escaped = line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
                ops.append("BT /F1 %d Tf %.1f %.1f Td (%s) Tj ET" % (size, x, height - y - size, escaped))
            else:
                _, x, y, w, h, gray = item
                ops.append("%.2f g %.1f %.1f %.1f %.1f re f 0 g" % (gray, x, height - y - h, w, h))
        stream = ("\n".join(ops) + "\n").encode("cp1252")
        objects.append(b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream")
        objects.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {width} {height}] "
                       f"/Resources << /Font << /F1 3 0 R >> >> /Contents {len(objects)} 0 R >>")
        kids.append(len(objects))
    objects[1] = f"<< /Type /Pages /Kids [{' '.join(f'{k} 0 R' for k in kids)}] /Count {len(kids)} >>"
    out, offsets = bytearray(b"%PDF-1.4\n"), []
    for number, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + (body if isinstance(body, bytes) else body.encode("cp1252")) + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    out += "".join(f"{o:010d} 00000 n \n" for o in offsets).encode()
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    with open(path, "wb") as stream:
        stream.write(out)


def paragraph(x, y, words=60, width_chars=52, size=9, start=0):
    """Body text block of fictional words starting at (x, y); returns the items and the next y."""
    vocabulary = ("modelo datos red capa entrenamiento pérdida validación resultado método atención "
                  "convolución gradiente optimizador conjunto muestra ejemplo").split()
    text = " ".join(vocabulary[(start + i) % len(vocabulary)] for i in range(words))
    lines, line = [], ""
    for word in text.split():
        if len(line) + len(word) + 1 > width_chars:
            lines.append(line)
            line = word
        else:
            line = f"{line} {word}".strip()
    lines.append(line)
    items = [("text", x, y + i * (size + 3), size, row) for i, row in enumerate(lines)]
    return items, y + len(lines) * (size + 3)


def two_column_paper(path):
    """Fictional two-column paper: page 1 has Figura 3 in the left column (caption below) and Tabla 1 in
    the right column (caption above); page 2 has a full-width Figure 4. Returns the true graphic boxes."""
    page1, truth = [("text", 50, 40, 14, "Un estudio ficticio de redes")], {}
    items, y = paragraph(50, 80, start=0)
    page1 += items
    page1.append(("text", 50, y + 6, 9, "Como muestra la Figura 3 las curvas bajan."))
    truth["figura 3"] = (50, y + 30, 220, 160)
    page1.append(("rect", 50, y + 30, 220, 160, 0.55))
    page1.append(("text", 50, y + 200, 9, "Figura 3: Curvas de perdida del modelo ficticio."))
    items, _ = paragraph(50, y + 230, start=3)
    page1 += items
    items, y2 = paragraph(320, 80, start=5)
    page1 += items
    page1.append(("text", 320, y2 + 14, 9, "Tabla 1: Resultados ficticios por modelo."))
    truth["tabla 1"] = (320, y2 + 34, 220, 120)
    page1.append(("rect", 320, y2 + 34, 220, 120, 0.8))
    items, _ = paragraph(320, y2 + 180, start=7)
    page1 += items
    page2 = []
    items, y = paragraph(50, 60, words=90, width_chars=110, start=2)
    page2 += items
    truth["figura 4"] = (60, y + 30, 440, 200)
    page2.append(("rect", 60, y + 30, 440, 200, 0.4))
    page2.append(("text", 200, y + 240, 9, "Figure 4. Arquitectura completa (ficticia)."))
    items, _ = paragraph(50, y + 270, words=90, width_chars=110, start=9)
    page2 += items
    make_layout_pdf(path, [page1, page2])
    return truth
