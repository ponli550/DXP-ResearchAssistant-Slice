"""The grounding gate. No model is involved here: plain string checks only."""
import re
import unicodedata
from dataclasses import dataclass

MIN_QUOTE_CHARS = 12
NUMBER_RE = re.compile(r"\d[\d,]*(?:\.\d+)?")


@dataclass(frozen=True)
class Claim:
    text: str
    doc_id: int
    page: int
    quote: str


@dataclass(frozen=True)
class Verdict:
    ok: bool
    reason: str = ""


def normalise(s):
    s = unicodedata.normalize("NFKC", s)
    s = s.replace("­", "")
    s = re.sub(r"-\s*\n\s*", "", s)  # hyphenation across a line break
    s = s.translate(str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"', "–": "-", "—": "-"}))
    return re.sub(r"\s+", " ", s).strip().lower()


def numbers_in(s):
    return {m.group(0).replace(",", "").rstrip(".") for m in NUMBER_RE.finditer(s)}


WORD_RE = re.compile(r"[A-Za-z]{3,}")
MIN_WORDS_ON_LINE = 3


def numbers_in_prose(s):
    """Numbers that appear on a line with at least three words, so a figure's axis labels and tick values do not count.

    A chart's text comes through pdftotext as runs of bare numbers ("5.1 4.4 3.6 3.3 4.1 2025f"). A number read from there
    is exactly the confident wrong figure the gate exists to stop, so a claim's number must be stated in a sentence.
    """
    out = set()
    for line in s.splitlines():
        if len(WORD_RE.findall(line)) >= MIN_WORDS_ON_LINE:
            out |= numbers_in(line)
    return out


FIGURE_CUES = re.compile(
    r"\b(?:how (?:much|many|fast|high|low|big|large)|what (?:share|rate|growth|percentage|proportion|level|number|amount|price)"
    r"|growth|grow|grew|forecast|projection|projected|percent|share|rate|wage|average|total|sold)\b",
    re.I,
)
YEARISH_RE = re.compile(r"\b(?:19|20)\d\d(?:f|e)?\b|\b[QH][1-4]\b", re.I)


def asks_for_figure(question):
    """True when the question wants a quantity: a growth rate, a share, a count, a price. Plain word cues, no model."""
    return bool(FIGURE_CUES.search(question))


def figures_in(s):
    """Numbers in s that are figures, not dates: years such as 2026 or 2025f and quarter marks such as Q2 do not count."""
    return numbers_in(YEARISH_RE.sub(" ", s))


def states_a_figure(claims):
    """A set of claims answers a figure question only if at least one claim states a figure.

    A true sentence with no number ("growth is expected to ease in 2026") answers "what growth is projected for 2026"
    beside the point. The gate verifies quotes, not relevance, so this is the one relevance check plain code can make.
    """
    return any(figures_in(c.text) for c in claims)


def check_claim(claim, page_text, allowed_pages):
    """Accept a claim only if its quote is on the cited page and every number in the claim is in the quote.

    allowed_pages is the set of (doc_id, page) the model was actually given. A citation outside it is rejected.
    """
    if (claim.doc_id, claim.page) not in allowed_pages:
        return Verdict(False, "cited a page that was not in the retrieved passages")
    if page_text is None:
        return Verdict(False, "cited page does not exist")
    q = normalise(claim.quote)
    if len(q) < MIN_QUOTE_CHARS:
        return Verdict(False, "quote too short to verify")
    if q not in normalise(page_text):
        return Verdict(False, "quote is not on the cited page")
    missing = numbers_in(claim.text) - numbers_in(claim.quote)
    if missing:
        return Verdict(False, "number in the claim is not in the quote: " + ", ".join(sorted(missing)))
    labels_only = numbers_in(claim.text) - numbers_in_prose(claim.quote)
    if labels_only:
        return Verdict(False, "number appears in the quote only as a figure label, not in a sentence: " + ", ".join(sorted(labels_only)))
    return Verdict(True)
