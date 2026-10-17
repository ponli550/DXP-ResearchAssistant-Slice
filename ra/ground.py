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
    return Verdict(True)
