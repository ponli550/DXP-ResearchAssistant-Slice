"""Write a small valid text PDF so the tests need no fixtures or extra packages."""


def make_pdf(path, pages):
    objs = []

    def add(body):
        objs.append(body)
        return len(objs)

    add("<< /Type /Catalog /Pages 2 0 R >>")
    add("PAGES_PLACEHOLDER")
    font = add("<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    kids = []
    for lines in pages:
        ops = ["BT", "/F1 10 Tf", "50 780 Td", "14 TL"]
        for ln in lines:
            esc = ln.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
            ops.append(f"({esc}) Tj T*")
        ops.append("ET")
        stream = "\n".join(ops)
        cid = add(f"<< /Length {len(stream)} >>\nstream\n{stream}\nendstream")
        pid = add(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents {cid} 0 R /Resources << /Font << /F1 {font} 0 R >> >> >>")
        kids.append(f"{pid} 0 R")
    objs[1] = f"<< /Type /Pages /Kids [{' '.join(kids)}] /Count {len(kids)} >>"
    out = "%PDF-1.4\n"
    offsets = []
    for i, body in enumerate(objs, 1):
        offsets.append(len(out.encode("latin-1")))
        out += f"{i} 0 obj\n{body}\nendobj\n"
    xref = len(out.encode("latin-1"))
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n"
    for off in offsets:
        out += f"{off:010d} 00000 n \n"
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n"
    with open(path, "wb") as f:
        f.write(out.encode("latin-1"))
    return path


def make_pdf_positioned(path, items):
    """One page. items is a list of (x, y, text), so a test can build a two-column page."""
    ops = ["BT", "/F1 10 Tf"]
    for x, y, text in items:
        esc = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        ops.append(f"1 0 0 1 {x} {y} Tm ({esc}) Tj")
    ops.append("ET")
    stream = "\n".join(ops)
    objs = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        "<< /Type /Pages /Kids [4 0 R] /Count 1 >>",
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents 5 0 R /Resources << /Font << /F1 3 0 R >> >> >>",
        f"<< /Length {len(stream)} >>\nstream\n{stream}\nendstream",
    ]
    out, offsets = "%PDF-1.4\n", []
    for i, body in enumerate(objs, 1):
        offsets.append(len(out.encode("latin-1")))
        out += f"{i} 0 obj\n{body}\nendobj\n"
    xref = len(out.encode("latin-1"))
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n" + "".join(f"{o:010d} 00000 n \n" for o in offsets)
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n"
    with open(path, "wb") as f:
        f.write(out.encode("latin-1"))
    return path
