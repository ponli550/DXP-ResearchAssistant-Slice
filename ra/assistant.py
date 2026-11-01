"""The small assistant in the workspace. It arranges what is already here: it explains the page, filters it, and starts dashboards and reports.

It never states what a report says. That goes to the research desk, where every claim is checked against its page.
It is plug and play: with no model it follows the fixed plan below, and with a model key set (see default_llm) the model
chooses from the same actions. A model's reply is untrusted: unknown actions are dropped, and a sentence holding a number
the page does not hold is replaced, the same rule the claim check applies to an answer.
"""
import json
import re

from .retrieve import EDITION_RE

SUGGESTIONS = ["What am I looking at?", "Build me a dashboard", "Draft a report from what I've found", "Show tariff across the editions"]
ACTIONS = {"filter", "add_topic", "show", "open_flow", "create_dashboard", "create_report", "handoff"}
TABS = {"found", "questions", "tables", "topics", "pages", "history"}
SYSTEM = (
    "You are the assistant inside a research workspace. You help a person see and arrange their own work. "
    "You NEVER state what a report says, and you do not use outside knowledge: a question about the content of a report gets the handoff action, "
    "which sends it to the research desk where every claim is checked against its page. Numbers you mention must come from the context. "
    'Reply with JSON only: {"say":"one or two plain sentences","actions":[...]}. Actions you may use: '
    '{"do":"filter","period":"all|30|7","outcome":"any|answered|none|review|down","report":<report id>}; '
    '{"do":"add_topic","term":"..."} to compare a topic across the reports; '
    '{"do":"show","tab":"found|questions|tables|topics|pages|history"}; '
    '{"do":"open_flow","audit_id":<id>} to show how an answer was reached; '
    '{"do":"create_dashboard","title":"...","topic":"optional"}; {"do":"create_report","title":"...","topic":"optional"}; '
    '{"do":"handoff","question":"..."}.'
)


def _summary(activity, reports):
    used = {r["id"]: set() for r in reports}
    cites = {r["id"]: 0 for r in reports}
    for row in activity["rows"]:
        for d, p in row["cited"]:
            if d in used:
                used[d].add(p)
                cites[d] += 1
    return {
        "reports": [{"id": r["id"], "title": r["title"], "pages": r["pages"], "pages_used": len(used[r["id"]]), "citations": cites[r["id"]]} for r in reports],
        "questions": len(activity["questions"]), "times_asked": len(activity["rows"]), "checked_facts": len(activity["findings"]),
        "topics": [{"term": t["term"], "pages_by_report": t["counts"]} for t in activity["topics"]],
        "next": [s["title"] for s in activity["next"]],
        "stored_answers": [{"audit_id": r["id"], "question": activity["questions"][r["q"]]} for r in activity["rows"] if r["saved"]][:12],
    }


def _term(text):
    """The subject in "a dashboard about tariffs" or "compare inflation across the editions"."""
    m = re.search(r"\b(?:about|on|for|of|compare|show(?: me)?|chart|track|visuali[sz]e|graph)\s+(.+)", text, re.I)
    if not m:
        return None
    term = re.split(r"\b(?:across|between|over|in|by|from)\b", m.group(1), flags=re.I)[0]
    term = re.sub(r"^(?:the|my|a|an|what i've found|what i have found)\b\s*", "", term.strip(" ?.!\"'"), flags=re.I).strip()
    return term if term and term.lower() not in {"this", "it", "me", "data", "everything", "editions", "reports", "my work"} else None


def build_dashboard(title, topic=None):
    title = (title or (f"{topic.capitalize()} across the reports" if topic else "My overview")).strip()[:80]
    if topic:
        blocks = [{"type": "topics", "terms": [topic]}, {"type": "pages", "topic": topic}, {"type": "findings", "topic": topic}, {"type": "questions", "topic": topic}]
    else:
        blocks = [{"type": "topics"}, {"type": "pages"}, {"type": "findings"}, {"type": "days"}]
    return {"do": "create_dashboard", "title": title, "blocks": blocks}


def build_report(activity, reports, title, topic=None):
    """A report is made of checked facts and nothing else, grouped by report. Returns the action, or None if there is nothing checked to use."""
    words = re.findall(r"[a-z0-9]+", (topic or "").lower())
    chosen = [f for f in activity["findings"] if all(w in (f["text"] + " " + f["question"]).lower() for w in words)]
    if not chosen:
        return None
    blocks = []
    for r in reports:
        mine = [f for f in chosen if f["doc_id"] == r["id"]]
        if mine:
            blocks.append({"type": "heading", "text": r["title"]})
            blocks += [{"type": "finding", "audit_id": f["audit_id"], "claim": f["claim"]} for f in mine]
    if topic:
        blocks.insert(0, {"type": "topics", "terms": [topic]})
    title = (title or (f"What the reports say about {topic}" if topic else "What I've found so far")).strip()[:80]
    return {"do": "create_report", "title": title, "blocks": blocks}


def _explain(summary):
    reports = summary["reports"]
    if not reports:
        return "This is your workspace, but you have no reports to search yet."
    used = sum(r["pages_used"] for r in reports)
    pages = sum(r["pages"] for r in reports)
    return (f"This is your workspace for {len(reports)} report{'s' if len(reports) != 1 else ''} ({pages} pages). "
            f"You have asked {summary['questions']} questions, your answers have used {used} of those pages, and you hold {summary['checked_facts']} checked facts. "
            "Start with What to do next. I can filter this page, build a dashboard or draft a report.")


def plan_locally(message, activity, reports):
    """The assistant with no model: plain rules, so it works for everyone and is what a failing model falls back to."""
    text = message.strip()
    low = text.lower()
    summary = _summary(activity, reports)
    term = _term(text)
    if re.search(r"what am i|what is this|what's this|explain|what can you|^help\b|looking at", low):
        return {"say": _explain(summary), "actions": []}
    if "dashboard" in low:
        action = build_dashboard(None, term)
        return {"say": f"I built “{action['title']}” from your work.", "actions": [action]}
    if re.search(r"\b(report|brief|write.?up)\b", low):
        action = build_report(activity, reports, None, term)
        if not action:
            return {"say": f"You have no checked facts{' on ' + term if term else ''} to put in a report yet. Ask in the research desk first.",
                    "actions": [{"do": "handoff", "question": f"What do the reports say about {term}?" if term else ""}]}
        return {"say": f"I started “{action['title']}”. It holds only checked facts with their pages, so you can edit it and print it.", "actions": [action]}
    edition = EDITION_RE.search(text)
    filters = {}
    if edition:
        named = next((r for r in reports if edition.group(0).lower() in r["title"].lower()), None)
        if named:
            filters["report"] = named["id"]
    if re.search(r"last 7|this week|past week", low):
        filters["period"] = "7"
    elif re.search(r"last 30|this month|past month", low):
        filters["period"] = "30"
    if re.search(r"no (answer|source)|not found|refus", low):
        filters["outcome"] = "none"
    elif "in review" in low or "review request" in low:
        filters["outcome"] = "review"
    elif "rated down" in low:
        filters["outcome"] = "down"
    if filters and not re.search(r"\b(chart|compare|track|graph)\b", low):
        return {"say": "I filtered the page to that.", "actions": [{"do": "filter", **filters}]}
    if term and re.search(r"\b(chart|compare|show|track|visuali[sz]e|graph|across)\b", low):
        return {"say": f"I added {term} to the table, so you can compare it across the reports. Select a number to read the pages.",
                "actions": [{"do": "add_topic", "term": term}, {"do": "show", "tab": "topics"}]}
    if re.search(r"\bhow (did|was|were|do) (you|we|it|this|that)\b|\bhow we got\b|\bwhy did\b", low):
        latest = next(iter(summary["stored_answers"]), None)
        if latest:
            return {"say": f"Here is how your latest recorded answer was reached: “{latest['question']}”.", "actions": [{"do": "open_flow", "audit_id": latest["audit_id"]}]}
    return {"say": "That is a question for the research desk, where each claim is checked against its page. I can send it there for you.",
            "actions": [{"do": "handoff", "question": text}]}


def _clean_actions(actions, activity, reports):
    ids = {r["id"] for r in reports}
    stored = {r["id"] for r in activity["rows"] if r["saved"]}
    out = []
    for a in actions if isinstance(actions, list) else []:
        if not isinstance(a, dict) or a.get("do") not in ACTIONS:
            continue
        kind = a["do"]
        if kind == "filter":
            kept = {"do": kind}
            if a.get("period") in ("all", "30", "7"):
                kept["period"] = a["period"]
            if a.get("outcome") in ("any", "answered", "none", "review", "down"):
                kept["outcome"] = a["outcome"]
            if a.get("report") in ids:
                kept["report"] = a["report"]
            if len(kept) > 1:
                out.append(kept)
        elif kind == "add_topic" and isinstance(a.get("term"), str) and a["term"].strip():
            out.append({"do": kind, "term": a["term"].strip()[:80]})
        elif kind == "show" and a.get("tab") in TABS:
            out.append({"do": kind, "tab": a["tab"]})
        elif kind == "open_flow" and a.get("audit_id") in stored:
            out.append({"do": kind, "audit_id": a["audit_id"]})
        elif kind == "handoff" and isinstance(a.get("question"), str):
            out.append({"do": kind, "question": a["question"].strip()[:500]})
        elif kind == "create_dashboard":
            out.append(build_dashboard(a.get("title") if isinstance(a.get("title"), str) else None, a["topic"].strip()[:80] if isinstance(a.get("topic"), str) and a["topic"].strip() else None))
        elif kind == "create_report":
            action = build_report(activity, reports, a.get("title") if isinstance(a.get("title"), str) else None, a["topic"].strip()[:80] if isinstance(a.get("topic"), str) and a["topic"].strip() else None)
            if action:
                out.append(action)
    return out


def _numbers_are_known(say, context):
    return all(n.replace(",", "") in context.replace(",", "") for n in re.findall(r"\d[\d,.]*\d|\d", say))


def reply(llm, message, history, activity, reports):
    """The assistant's turn: {"say", "actions", "suggestions", "mode"}. Falls back to the plain rules whenever a model is absent or misbehaves."""
    local = plan_locally(message, activity, reports)
    complete = getattr(llm, "complete", None)
    if not complete:
        return {**local, "suggestions": SUGGESTIONS, "mode": "guided"}
    context = json.dumps(_summary(activity, reports))
    prompt = f"Context:\n{context}\n\nConversation so far:\n" + "\n".join(f"{t['role']}: {t['text']}" for t in history) + f"\n\nuser: {message}"
    try:
        text = re.sub(r"<think>.*?</think>", "", complete(SYSTEM, prompt), flags=re.S)
        data = json.loads(re.search(r"\{.*\}", text, re.S).group(0))
        say, actions = str(data.get("say") or "").strip()[:600], _clean_actions(data.get("actions"), activity, reports)
    except Exception:  # noqa: BLE001  a model that is down or talks nonsense must not take the page down with it
        return {**local, "suggestions": SUGGESTIONS, "mode": "guided"}
    if not say and not actions:
        return {**local, "suggestions": SUGGESTIONS, "mode": "guided"}
    if not say or not _numbers_are_known(say, context):
        say = local["say"] if not actions else "Done."
    return {"say": say, "actions": actions, "suggestions": [], "mode": "model"}
