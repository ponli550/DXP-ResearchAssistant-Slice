"""Reports a person adds for themselves.

Each user has one private library, granted to them and nobody else. Everything downstream already goes through the
grant, so search, the page route, the PDF route and the model never see an upload from another user. The admin views
leave these libraries out, and nothing can grant one to someone else.
"""
import json
import os
import re
import subprocess

from .ingest import MIN_PAGE_CHARS, extract_pages, store_pages

PRIVATE_PREFIX = "private:"
PRIVATE_NAME = "Your uploads"
MAX_UPLOAD_BYTES = 50 * 1024 * 1024
MAX_UPLOAD_PAGES = 500
MAX_UPLOADS = 20


class UploadRefused(ValueError):
    """The file cannot be added. The message is written for the person who chose it."""


def private_library(user):
    return PRIVATE_PREFIX + user


def is_private(library_id):
    return library_id.startswith(PRIVATE_PREFIX)


def clean_title(filename):
    """A title from a file name. Only ever shown as text; the stored file has a name of its own."""
    name = re.split(r"[\\/]", filename or "")[-1]
    name = re.sub(r"\.pdf$", "", name, flags=re.I)
    name = re.sub(r"[\x00-\x1f\x7f]", " ", name)
    name = re.sub(r"\s+", " ", name.replace("_", " ")).strip()
    return name[:120] or "Untitled report"


def add_upload(con, user, filename, path):
    """Read the PDF at `path` and add it to the user's private library. Returns the ingest report, or raises UploadRefused."""
    library = private_library(user)
    held = con.execute("SELECT COUNT(*) FROM documents WHERE library_id=?", (library,)).fetchone()[0]
    if held >= MAX_UPLOADS:
        raise UploadRefused(f"You can keep up to {MAX_UPLOADS} uploads. Delete one to add another.")
    try:
        pages = extract_pages(path)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, ValueError) as e:
        raise UploadRefused("This PDF could not be read. It may be damaged or password protected.") from e
    if len(pages) > MAX_UPLOAD_PAGES:
        raise UploadRefused(f"This PDF has {len(pages)} pages. The limit is {MAX_UPLOAD_PAGES}.")
    if not any(len(t.strip()) >= MIN_PAGE_CHARS for t in pages):
        raise UploadRefused("No text could be read from this PDF. It is probably a scan, and scans cannot be searched yet.")
    con.execute("INSERT OR IGNORE INTO libraries(id, name) VALUES (?, ?)", (library, PRIVATE_NAME))
    con.execute("INSERT OR IGNORE INTO grants(user, library_id) VALUES (?, ?)", (user, library))
    return store_pages(con, library, clean_title(filename), path, pages)


def _mentions(answer, doc_id):
    j = json.loads(answer)
    return any(x.get("doc_id") == doc_id for key in ("retrieved", "claims", "rejected_claims", "closest") for x in j.get(key, []))


def delete_upload(con, user, doc_id):
    """Remove an upload and everything made from it: its pages, its search index, its file, and the stored answers and table
    cells that used its text. The questions stay in the person's history, because they wrote those. Returns False if it is not theirs."""
    row = con.execute("SELECT source FROM documents WHERE id=? AND library_id=?", (doc_id, private_library(user))).fetchone()
    if not row:
        return False
    for r in con.execute("SELECT id, text FROM passages WHERE doc_id=?", (doc_id,)).fetchall():
        con.execute("INSERT INTO passages_fts(passages_fts, rowid, text) VALUES ('delete', ?, ?)", (r["id"], r["text"]))
    con.execute("DELETE FROM passages WHERE doc_id=?", (doc_id,))
    con.execute("DELETE FROM pages WHERE doc_id=?", (doc_id,))
    for r in con.execute("SELECT id, answer, cited FROM audit WHERE user=? AND COALESCE(answer, '') != ''", (user,)).fetchall():
        if _mentions(r["answer"], doc_id):
            cited = ";".join(c for c in r["cited"].split(";") if c and not c.startswith(f"{doc_id}:"))
            con.execute("UPDATE audit SET answer=NULL, cited=? WHERE id=?", (cited, r["id"]))
    for a in con.execute("SELECT id, doc_ids FROM analyses WHERE user=?", (user,)).fetchall():
        kept = [d for d in json.loads(a["doc_ids"]) if d != doc_id]
        con.execute("DELETE FROM analysis_cells WHERE analysis_id=? AND doc_id=?", (a["id"], doc_id))
        if kept:
            con.execute("UPDATE analyses SET doc_ids=? WHERE id=?", (json.dumps(kept), a["id"]))
        else:
            con.execute("DELETE FROM analysis_cells WHERE analysis_id=?", (a["id"],))
            con.execute("DELETE FROM analyses WHERE id=?", (a["id"],))
    con.execute("DELETE FROM documents WHERE id=?", (doc_id,))
    con.commit()
    if row["source"] and os.path.exists(row["source"]):
        os.remove(row["source"])
    return True
