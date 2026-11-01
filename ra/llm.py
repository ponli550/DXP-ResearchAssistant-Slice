"""Model adapters. The pipeline depends only on the LLM protocol in answer.py."""
import json
import os
import re
import time
import urllib.error

from .ground import Claim
from .retrieve import Passage

SYSTEM = (
    "You answer questions using ONLY the passages provided. Return JSON only, in the form "
    '{"claims":[{"text":"...","doc_id":0,"page":0,"quote":"..."}]}. '
    "Each claim is one statement. Its quote must be copied exactly, word for word, from the cited passage. "
    "Any number in a claim must appear in its quote. If the passages do not answer the question, return {\"claims\":[]}. "
    "Never use outside knowledge."
)


def transient(e):
    """A dropped or timed-out connection, or a server that is busy or down. A 4xx such as a bad key is not worth repeating."""
    if isinstance(e, urllib.error.HTTPError):
        return e.code == 429 or e.code >= 500
    return isinstance(e, (OSError, TimeoutError))


def with_retry(call, attempts=2, delay=1.0, on_error=None, sleep=time.sleep):
    """Run call(); on a transient error wait delay seconds (doubling each time) and try again, up to attempts calls in all.
    The last error is raised so the caller can record it. on_error(e) sees every failure."""
    for n in range(attempts):
        try:
            return call()
        except Exception as e:
            if on_error:
                on_error(e)
            if not transient(e) or n == attempts - 1:
                raise
            sleep(delay * 2**n)


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

    def complete(self, system, prompt):
        """Plain text back, for the workspace assistant."""
        msg = self.client.messages.create(model=self.model, max_tokens=800, system=system, messages=[{"role": "user", "content": prompt}])
        return "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")


class OpenAICompatLLM:
    """Adapter for any OpenAI-compatible chat endpoint (Nous Research Hermes, OpenRouter, a local server).

    Standard library only. Reads HERMES_API_KEY, HERMES_BASE_URL and HERMES_MODEL unless given explicitly.
    `post` can be replaced in tests so no network is needed.
    """

    def __init__(self, model=None, base_url=None, api_key=None, post=None):
        self.model = model or os.environ.get("HERMES_MODEL", "qwen/qwen3.8-27b")
        self.base_url = (base_url or os.environ.get("HERMES_BASE_URL", "https://inference-api.nousresearch.com/v1")).rstrip("/")
        self.api_key = api_key or os.environ.get("HERMES_API_KEY", "")
        self.name = self.model
        self.post = post or self._post

    def _post(self, body):
        import urllib.request

        req = urllib.request.Request(
            self.base_url + "/chat/completions",
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json", "Authorization": "Bearer " + self.api_key},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=120) as r:
            return json.load(r)

    def _note(self, e):
        self.last_error = f"{type(e).__name__}: {e}"

    def draft(self, question, passages, feedback=None):
        body = {
            "model": self.model,
            "max_tokens": 1500,
            "temperature": 0,
            "messages": [
                {"role": "system", "content": SYSTEM},
                {"role": "user", "content": build_prompt(question, passages, feedback)},
            ],
        }
        data = with_retry(lambda: self.post(body), on_error=self._note)
        text = data["choices"][0]["message"].get("content") or ""
        text = re.sub(r"<think>.*?</think>", "", text, flags=re.S)
        return parse_claims(text)

    def complete(self, system, prompt):
        """Plain text back, for the workspace assistant."""
        body = {"model": self.model, "max_tokens": 800, "temperature": 0,
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}]}
        data = with_retry(lambda: self.post(body), on_error=self._note)
        return data["choices"][0]["message"].get("content") or ""


class ScriptedLLM:
    """For tests and offline demos: returns queued claim lists, one per call."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def draft(self, question, passages, feedback=None):
        self.calls.append((question, [(p.doc_id, p.page) for p in passages], feedback))
        return self.responses.pop(0) if self.responses else []
