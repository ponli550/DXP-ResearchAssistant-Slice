import re
import subprocess

MIN_PAGE_CHARS = 40
TARGET_PASSAGE_CHARS = 900


def page_count(pdf_path):
    out = subprocess.run(["pdfinfo", str(pdf_path)], capture_output=True, text=True, check=True).stdout
    m = re.search(r"^Pages:\s+(\d+)", out, re.M)
    if not m:
        raise ValueError(f"cannot read page count: {pdf_path}")
    return int(m.group(1))


def extract_pages(pdf_path):
    """Return a list of page texts, index 0 is page 1. Page numbers stay attached to the text.

    Reading-order mode, not layout mode: layout mode interleaves the lines of two-column pages,
    which would break any quote that runs across a line.
    """
    n = page_count(pdf_path)
    pages = []
    for p in range(1, n + 1):
        out = subprocess.run(
            ["pdftotext", "-f", str(p), "-l", str(p), str(pdf_path), "-"],
            capture_output=True, text=True, check=True,
        ).stdout
        pages.append(out)
    return pages


def split_passages(page_text):
    """Split a page into passages along blank lines, merging short paragraphs up to a target size."""
    paras = []
    for block in re.split(r"\n\s*\n", page_text):
        block = re.sub(r"[ \t]+", " ", block).strip()
        if len(block) <= TARGET_PASSAGE_CHARS * 1.5:
            if block:
                paras.append(block)
            continue
        cur = ""  # a long block without blank lines: cut it on line breaks
        for line in block.split("\n"):
            if cur and len(cur) + len(line) > TARGET_PASSAGE_CHARS:
                paras.append(cur)
                cur = line
            else:
                cur = f"{cur}\n{line}" if cur else line
        if cur:
            paras.append(cur)
    out, cur = [], ""
    for p in paras:
        if cur and len(cur) + len(p) > TARGET_PASSAGE_CHARS:
            out.append(cur)
            cur = p
        else:
            cur = f"{cur}\n{p}" if cur else p
    if cur:
        out.append(cur)
    return out


def ingest_pdf(con, library_id, title, pdf_path, library_name=None):
    """Read a PDF page by page, store pages and passages, and return a quality report.

    Pages with almost no text are flagged, not silently trusted: they probably need OCR.
    """
    con.execute("INSERT OR IGNORE INTO libraries(id, name) VALUES (?, ?)", (library_id, library_name or library_id))
    pages = extract_pages(pdf_path)
    low = [i + 1 for i, t in enumerate(pages) if len(t.strip()) < MIN_PAGE_CHARS]
    cur = con.execute(
        "INSERT INTO documents(library_id, title, source, pages, needs_ocr) VALUES (?,?,?,?,?)",
        (library_id, title, str(pdf_path), len(pages), 1 if low else 0),
    )
    doc_id = cur.lastrowid
    n_passages = 0
    for i, text in enumerate(pages, start=1):
        con.execute("INSERT INTO pages(doc_id, page, text, low_text) VALUES (?,?,?,?)", (doc_id, i, text, 1 if i in low else 0))
        for ptxt in split_passages(text):
            pid = con.execute("INSERT INTO passages(doc_id, page, text) VALUES (?,?,?)", (doc_id, i, ptxt)).lastrowid
            con.execute("INSERT INTO passages_fts(rowid, text) VALUES (?, ?)", (pid, ptxt))
            n_passages += 1
    con.commit()
    return {"doc_id": doc_id, "title": title, "pages": len(pages), "low_text_pages": low, "passages": n_passages}
