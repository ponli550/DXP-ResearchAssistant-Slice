from ra.evalset import check_questions, score
from ra.ground import Claim
from ra.llm import ScriptedLLM

TITLE = "Sector Outlook 2025"
ANCHOR = "Palm oil output rose 4.2 percent in 2024"
Q_OK = {"id": "A1", "q": "How did palm oil output change?", "facts": [{"doc": TITLE, "anchor": ANCHOR, "must": "4.2 percent"}]}
Q_NONE = {"id": "U1", "q": "What did the board decide about palm oil output?", "facts": []}


def doc(db):
    return db.execute("SELECT id FROM documents WHERE title=?", (TITLE,)).fetchone()["id"]


def good(db):
    return Claim("Palm oil output rose 4.2 percent in 2024.", doc(db), 1, ANCHOR)


def run(db, q, *responses):
    return score(db, ScriptedLLM(*responses), [q], "alice")


def test_correct_answer_is_counted_correct(db):
    s = run(db, Q_OK, [good(db)])
    assert s["correct"] == 1 and s["wrong_and_confident"] == 0 and s["retrieval_hit_rate"] == 1.0


def test_refusing_an_answerable_question_is_timid(db):
    s = run(db, Q_OK, [], [])
    assert s["timid"] == 1 and s["correct"] == 0


def test_answering_with_the_wrong_fact_is_wrong_and_confident(db):
    d = doc(db)
    other = Claim("Export volumes were flat.", d, 1, "Export volumes were flat over the same period")
    s = run(db, Q_OK, [other])
    assert s["wrong_and_confident"] == 1 and s["correct"] == 0


def test_right_quote_from_the_wrong_page_is_not_correct(db):
    d = doc(db)
    page2 = Claim("Currency volatility could reduce margins by 1.5 percent.", d, 2, "Currency volatility could reduce margins by 1.5 percent")
    assert run(db, Q_OK, [page2])["correct"] == 0


def test_refusing_an_unanswerable_question_is_correct_refusal(db):
    s = run(db, Q_NONE, [], [])
    assert s["correct_refusal"] == 1 and s["wrong_and_confident"] == 0


def test_answering_an_unanswerable_question_is_wrong_and_confident(db):
    s = run(db, Q_NONE, [good(db)])
    assert s["wrong_and_confident"] == 1 and s["correct_refusal"] == 0


def test_gate_rejections_are_counted(db):
    bad = Claim("Output doubled.", doc(db), 1, "Palm oil output doubled in 2024")
    s = run(db, Q_OK, [bad], [good(db)])
    assert s["claims_rejected_by_gate"] == 1 and s["claims_drafted"] == 2 and s["correct"] == 1


def test_check_questions_flags_a_missing_anchor(db):
    broken = {"id": "X", "q": "?", "facts": [{"doc": TITLE, "anchor": "this sentence is not in the report", "must": "1"}]}
    assert check_questions(db, [Q_OK]) == [] and len(check_questions(db, [broken])) == 1


def test_a_comparison_needs_both_reports(db, tmp_path):
    from ra.ingest import ingest_pdf
    from .pdfmaker import make_pdf

    b = make_pdf(tmp_path / "b.pdf", [["Palm oil output fell 1.1 percent in 2025, with prices falling as well."]])
    ingest_pdf(db, "public", "Sector Outlook 2026", b)
    two = {"id": "C1", "q": "Compare palm oil output in 2024 and 2025", "facts": [
        {"doc": TITLE, "anchor": ANCHOR, "must": "4.2 percent"},
        {"doc": "Sector Outlook 2026", "anchor": "Palm oil output fell 1.1 percent in 2025", "must": "1.1 percent"}]}
    d2 = db.execute("SELECT id FROM documents WHERE title='Sector Outlook 2026'").fetchone()["id"]
    second = Claim("Palm oil output fell 1.1 percent in 2025.", d2, 1, "Palm oil output fell 1.1 percent in 2025")
    assert run(db, two, [good(db)])["correct"] == 0
    assert run(db, two, [good(db), second])["correct"] == 1


def test_right_number_on_an_unrelated_page_is_not_correct(db, tmp_path):
    from ra.ingest import ingest_pdf
    from .pdfmaker import make_pdf

    p = make_pdf(tmp_path / "t.pdf", [["Palm oil output rose 4.2 percent in 2024, driven by yields."], ["Palm oil output employed many workers, and the unemployment rate was 4.2 percent in the same year."]])
    ingest_pdf(db, "public", "Two Page Report", p)
    d = db.execute("SELECT id FROM documents WHERE title='Two Page Report'").fetchone()["id"]
    q = {"id": "T", "q": "How did palm oil output change in the two page report?", "facts": [
        {"doc": "Two Page Report", "anchor": "Palm oil output rose 4.2 percent in 2024", "must": "4.2 percent"}]}
    wrong_page = Claim("The rate was 4.2 percent.", d, 2, "the unemployment rate was 4.2 percent in the same year")
    right_page = Claim("Output rose 4.2 percent.", d, 1, "Palm oil output rose 4.2 percent in 2024")
    from ra.retrieve import retrieve

    assert (d, 2) in {(h.doc_id, h.page) for h in retrieve(db, "alice", q["q"])}
    assert run(db, q, [wrong_page])["correct"] == 0
    assert run(db, q, [right_page])["correct"] == 1
