import pytest

from ra.db import connect
from ra.ingest import ingest_pdf
from .pdfmaker import make_pdf

OUTLOOK = [
    ["Sector Outlook 2025", "", "Palm oil output rose 4.2 percent in 2024, driven by higher yields.", "Export volumes were flat over the same period."],
    ["Risks", "", "The main risk is a sharp fall in export demand from key markets.", "Currency volatility could reduce margins by 1.5 percent."],
    [],
]
MEMO = [["Confidential Strategy Memo", "", "The board approved a merger with Orion Holdings for 900 million."]]


@pytest.fixture
def db(tmp_path):
    con = connect()
    outlook = make_pdf(tmp_path / "outlook.pdf", OUTLOOK)
    memo = make_pdf(tmp_path / "memo.pdf", MEMO)
    con.report = ingest_pdf(con, "public", "Sector Outlook 2025", outlook)
    con.memo_report = ingest_pdf(con, "restricted", "Strategy Memo", memo)
    con.execute("INSERT INTO grants VALUES ('alice','public')")
    con.execute("INSERT INTO grants VALUES ('bob','public')")
    con.execute("INSERT INTO grants VALUES ('bob','restricted')")
    con.commit()
    return con
