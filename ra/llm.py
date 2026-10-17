"""Model adapters. The pipeline depends only on the LLM protocol in answer.py."""
import json
import os
import re

from .ground import Claim
from .retrieve import Passage

SYSTEM = (
    "You answer questions using ONLY the passages provided. Return JSON only, in the form "
    '{"claims":[{"text":"...","doc_id":0,"page":0,"quote":"..."}]}. '
    "Each claim is one statement. Its quote must be copied exactly, word for word, from the cited passage. "
    "Any number in a claim must appear in its quote. If the passages do not answer the question, return {\"claims\":[]}. "
    "Never use outside knowledge."
)


def build_prompt(question, passages, feedback=None):
    parts = [f"Question: {question}", "", "Passages:"]
    for p in passages:
        parts.append(f"[doc_id={p.doc_id} title={p.title!r} page={p.page}]\n{p.text}")
    if feedback:
        parts += ["", "Your previous answer had problems. Fix them or drop those claims:"] + feedback
    return "\n".join(parts)


def parse_claims(text):
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return []
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        return []
    out = []
    for c in data.get("claims", []):
        try:
            out.append(Claim(str(c["text"]), int(c["doc_id"]), int(c["page"]), str(c["quote"])))
        except (KeyError, TypeError, ValueError):
            continue
    return out


class ClaudeLLM:
    """Real model adapter. Needs the anthropic package and ANTHROPIC_API_KEY. Not exercised in the tests."""

    def __init__(self, model="claude-sonnet-5-5", client=None):
        if client is None:
            import anthropic

            client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
        self.client, self.model = client, model

    def draft(self, question, passages, feedback=None):
        msg = self.client.messages.create(
            model=self.model,
            max_tokens=1500,
            system=SYSTEM,
            messages=[{"role": "user", "content": build_prompt(question, passages, feedback)}],
        )
        return parse_claims("".join(b.text for b in msg.content if getattr(b, "type", "") == "text"))


class ScriptedLLM:
    """For tests and offline demos: returns queued claim lists, one per call."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def draft(self, question, passages, feedback=None):
        self.calls.append((question, [(p.doc_id, p.page) for p in passages], feedback))
        return self.responses.pop(0) if self.responses else []
