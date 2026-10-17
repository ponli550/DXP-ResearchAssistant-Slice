"""Models that are not models. They show what retrieval and the gate can and cannot do alone."""
import re

from .ground import Claim, normalise
from .retrieve import STOPWORDS

SENT_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z])")


def _tokens(question):
    return {t for t in re.findall(r"[a-z0-9]+", question.lower()) if t not in STOPWORDS and len(t) > 1}


def _prose(s):
    """Skip chart debris: strings that are mostly numbers."""
    words = s.split()
    return sum(w.strip(".,%()-").replace(".", "").isdigit() for w in words) / max(len(words), 1) < 0.3


def _best_sentence(question, passages, top=3):
    want = _tokens(question)
    best, best_cov, best_p = None, -1.0, None
    for p in passages[:top]:
        for s in SENT_SPLIT.split(re.sub(r"\s+", " ", p.text)):
            have = set(re.findall(r"[a-z0-9]+", normalise(s)))
            cov = len(want & have) / len(want) if want else 0.0
            if 25 <= len(s) <= 400 and _prose(s) and cov > best_cov:
                best, best_cov, best_p = s, cov, p
    return best, best_cov, best_p


class Extractive:
    """Quote the best-matching sentence of the retrieved passages. Always answers when anything was retrieved."""

    name = "extractive"

    def draft(self, question, passages, feedback=None):
        s, _, p = _best_sentence(question, passages)
        return [Claim(s, p.doc_id, p.page, s)] if s else []


class Threshold(Extractive):
    """Same, but refuses unless the sentence covers enough of the question's distinctive words."""

    name = "threshold"

    def __init__(self, floor=0.6):
        self.floor = floor

    def draft(self, question, passages, feedback=None):
        s, cov, p = _best_sentence(question, passages)
        return [Claim(s, p.doc_id, p.page, s)] if s and cov >= self.floor else []
