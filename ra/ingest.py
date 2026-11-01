import re
import subprocess

MIN_PAGE_CHARS = 40
TARGET_PASSAGE_CHARS = 900
PDF_TIMEOUT_SECONDS = 120


def extract_pages(pdf_path):
    """Return a list of page texts, index 0 is page 1. Page numbers stay attached to the text.

    Reading-order mode, not layout mode: layout mode interleaves the lines of two-column pages,
    which would break any quote that runs across a line. pdftotext ends every page with a form feed.
    The timeout is there because uploads are read too, and a hostile file must not hold a worker for ever.
    """
    out = subprocess.run(
        ["pdftotext", str(pdf_path), "-"], capture_output=True, encoding="utf-8", errors="replace", check=True, timeout=PDF_TIMEOUT_SECONDS,
    ).stdout
    return out.split("\f")[:-1]


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
    """Read a PDF page by page, store pages and passages, and return a quality report."""
    con.execute("INSERT OR IGNORE INTO libraries(id, name) VALUES (?, ?)", (library_id, library_name or library_id))
    return store_pages(con, library_id, title, pdf_path, extract_pages(pdf_path))


def store_pages(con, library_id, title, pdf_path, pages):
    """Store a report's pages and passages. Pages with almost no text are flagged, not silently trusted: they probably need OCR."""
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
