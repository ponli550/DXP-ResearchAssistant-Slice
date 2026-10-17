"""A small web demo over the same pipeline. Pick a user, ask a question, click a citation to see the page.

The login is a demo picker, not authentication. Access is still enforced in retrieval and on the page route.
"""
import os
import re

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from .answer import ask
from .baselines import Extractive
from .db import connect


def default_llm():
    """Claude when a key is set, otherwise the extractive baseline, and say which one."""
    if os.environ.get("ANTHROPIC_API_KEY"):
        from .llm import ClaudeLLM

        return ClaudeLLM(), "claude"
    return Extractive(), "extractive baseline (no model key set)"


class AskBody(BaseModel):
    user: str
    question: str


def quote_span(text, quote):
    """Offsets of the quote inside the page text, ignoring differences in whitespace and case. None if not found."""
    words = quote.split()
    if not words:
        return None
    m = re.search(r"\s+".join(re.escape(w) for w in words), text, re.I)
    return [m.start(), m.end()] if m else None


def create_app(db_path, llm_factory=default_llm):
    app = FastAPI(title="Research assistant demo")
    llm, model_name = llm_factory()

    def db():
        return connect(db_path)

    def allowed_libraries(con, user):
        return [r["library_id"] for r in con.execute("SELECT library_id FROM grants WHERE user=?", (user,))]

    @app.get("/", response_class=HTMLResponse)
    def index():
        return INDEX_HTML

    @app.get("/api/users")
    def users():
        con = db()
        return {"users": [r["user"] for r in con.execute("SELECT DISTINCT user FROM grants ORDER BY user")], "model": model_name}

    @app.get("/api/library")
    def library(user: str):
        con = db()
        rows = con.execute(
            "SELECT d.id, d.title, d.pages, d.needs_ocr, l.name AS library FROM documents d "
            "JOIN libraries l ON l.id = d.library_id JOIN grants g ON g.library_id = d.library_id AND g.user = ? ORDER BY d.title",
            (user,),
        ).fetchall()
        return {"documents": [dict(r) for r in rows]}

    @app.post("/api/ask")
    def ask_route(body: AskBody):
        question = body.question.strip()
        if not question or len(question) > 500:
            raise HTTPException(400, "Ask a question of up to 500 characters.")
        con = db()
        if not allowed_libraries(con, body.user):
            raise HTTPException(403, "This user has no library access.")
        res = ask(con, llm, body.user, question)
        titles = {r["id"]: r["title"] for r in con.execute("SELECT id, title FROM documents")}
        return {
            "status": res.status,
            "model": model_name,
            "attempts": res.attempts,
            "claims": [{"text": c.text, "doc_id": c.doc_id, "title": titles[c.doc_id], "page": c.page, "quote": c.quote} for c in res.claims],
            "rejected": len(res.rejected),
            "closest": [{"title": p.title, "doc_id": p.doc_id, "page": p.page} for p in res.closest[:3]],
        }

    @app.get("/api/page")
    def page(user: str, doc_id: int, p: int, quote: str = ""):
        con = db()
        row = con.execute(
            "SELECT d.title, pg.text FROM pages pg JOIN documents d ON d.id = pg.doc_id "
            "JOIN grants g ON g.library_id = d.library_id AND g.user = ? WHERE pg.doc_id = ? AND pg.page = ?",
            (user, doc_id, p),
        ).fetchone()
        if not row:
            raise HTTPException(403, "No access to this page, or it does not exist.")
        return {"title": row["title"], "page": p, "text": row["text"], "span": quote_span(row["text"], quote) if quote else None}

    @app.get("/api/audit")
    def audit(user: str):
        con = db()
        rows = con.execute("SELECT at, question, status, cited FROM audit WHERE user=? ORDER BY id DESC LIMIT 20", (user,)).fetchall()
        return {"entries": [dict(r) for r in rows]}

    return app


INDEX_HTML = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Research assistant</title>
<style>
:root{color-scheme:light dark;--bg:#f7f6f2;--fg:#1d2327;--muted:#5f6b73;--card:#fff;--line:#d9d6cc;--accent:#0b5c6b;--warn:#8a4b00;--warnbg:#fff3e0;--mark:#ffe58a}
@media (prefers-color-scheme:dark){:root{--bg:#14181b;--fg:#e8ecee;--muted:#98a4ab;--card:#1d2327;--line:#323a3f;--accent:#5fc4d6;--warn:#ffb866;--warnbg:#3a2a12;--mark:#6b5b00}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:16px/1.5 system-ui,sans-serif}
main{max-width:1100px;margin:0 auto;padding:1rem 16px 3rem;display:grid;gap:1rem;grid-template-columns:minmax(0,1fr)}
@media(min-width:900px){main{grid-template-columns:minmax(0,1fr) minmax(0,1fr)}header,.banner{grid-column:1/-1}}
header{display:flex;flex-wrap:wrap;gap:.75rem 1.5rem;align-items:end;justify-content:space-between}
h1{font-size:1.4rem;margin:0}.sub{color:var(--muted);margin:0;font-size:.9rem}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:1rem}
.banner{background:var(--warnbg);color:var(--warn);border-radius:8px;padding:.6rem .9rem;font-size:.9rem}
label{font-size:.85rem;color:var(--muted);display:block;margin-bottom:.25rem}
select,textarea,button{font:inherit;color:inherit;border:1px solid var(--line);border-radius:8px;background:var(--card);padding:.5rem .7rem}
textarea{width:100%;min-height:4.5rem;resize:vertical}
button.primary{background:var(--accent);color:#fff;border-color:var(--accent);cursor:pointer}
button:disabled{opacity:.6;cursor:wait}
.row{display:flex;flex-wrap:wrap;gap:.5rem;align-items:center;margin-top:.6rem}
.chip{border:1px solid var(--accent);color:var(--accent);background:transparent;border-radius:999px;padding:.05rem .55rem;font-size:.85rem;cursor:pointer}
ol{padding-left:1.2rem;margin:.5rem 0}li{margin:.5rem 0}
.refuse{font-weight:600}.muted{color:var(--muted);font-size:.9rem}
pre{position:relative;white-space:pre-wrap;word-break:break-word;font:.85rem/1.5 ui-monospace,monospace;margin:0;max-height:60vh;overflow:auto}
mark{background:var(--mark);color:inherit;border-radius:3px}
ul.docs{margin:.25rem 0;padding-left:1.1rem;font-size:.9rem}
</style></head><body><main>
<header><div><h1>Research assistant</h1><p class="sub">Every claim is checked against the page it cites.</p></div>
<div><label for="user">Demo user (not real login)</label><select id="user"></select></div></header>
<div id="banner" class="banner" hidden></div>
<section class="card"><label for="q">Your question</label><textarea id="q" maxlength="500" placeholder="For example: What GDP growth is projected for Malaysia in 2026?"></textarea>
<div class="row"><button id="go" class="primary">Ask</button><span id="status" class="muted" role="status"></span></div>
<div id="answer" aria-live="polite" style="margin-top:1rem"></div>
<details style="margin-top:1rem"><summary class="muted">Libraries this user can search</summary><ul id="docs" class="docs"></ul></details></section>
<section class="card"><div id="pagehead" class="muted">Click a citation to see the page and the quote, highlighted.</div><pre id="pagetext"></pre></section>
</main><script>
const $=id=>document.getElementById(id);
const el=(t,props={},...kids)=>{const e=document.createElement(t);Object.assign(e,props);kids.forEach(k=>e.append(k));return e};
let model="";
async function api(path,opts){const r=await fetch(path,opts);const j=await r.json().catch(()=>({}));if(!r.ok)throw new Error(j.detail||r.statusText);return j}
async function loadUsers(){const j=await api("/api/users");model=j.model;
  j.users.forEach(u=>$("user").append(el("option",{value:u,textContent:u})));
  if(model.startsWith("extractive")){$("banner").hidden=false;$("banner").textContent="No model key is set, so answers come from an extractive baseline that quotes the best-matching sentence. It is often irrelevant. This is not the real assistant.";}
  await loadDocs()}
async function loadDocs(){const j=await api("/api/library?user="+encodeURIComponent($("user").value));$("docs").replaceChildren(...j.documents.map(d=>el("li",{textContent:d.title+" ("+d.pages+" pages, "+d.library+")"})));}
async function showPage(c){try{const u=new URLSearchParams({user:$("user").value,doc_id:c.doc_id,p:c.page,quote:c.quote||""});
  const j=await api("/api/page?"+u);$("pagehead").textContent=j.title+", page "+j.page;const pre=$("pagetext");pre.replaceChildren();
  if(j.span){pre.append(j.text.slice(0,j.span[0]),el("mark",{textContent:j.text.slice(j.span[0],j.span[1])}),j.text.slice(j.span[1]));const m=pre.querySelector("mark");pre.scrollTop=Math.max(0,m.offsetTop-pre.clientHeight/3)}else pre.textContent=j.text}
  catch(e){$("pagehead").textContent=e.message;$("pagetext").textContent=""}}
function chip(c){return el("button",{className:"chip",type:"button",textContent:c.title+", p. "+c.page,onclick:()=>showPage(c)})}
async function ask(){const q=$("q").value.trim();if(!q)return;$("go").disabled=true;$("status").textContent="Searching and checking quotes...";$("answer").replaceChildren();
  try{const j=await api("/api/ask",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({user:$("user").value,question:q})});
    if(j.status==="answered"){$("answer").append(el("ol",{},...j.claims.map(c=>el("li",{},c.text+" ",chip(c)))),el("div",{className:"muted",textContent:j.rejected?j.rejected+" drafted claim(s) were removed because their quote was not on the cited page.":"Each quote was found on the page it cites. That confirms the source, not that the claim answers your question."}));showPage(j.claims[0])}
    else{$("answer").append(el("div",{className:"refuse",textContent:"I could not find this in the selected library."}));
      if(j.closest.length){$("answer").append(el("div",{className:"muted",textContent:"Closest sources:"}),...j.closest.map(c=>el("div",{},chip(c))))}}
    $("status").textContent="Model: "+j.model}
  catch(e){$("status").textContent=e.message}finally{$("go").disabled=false}}
$("go").onclick=ask;$("user").onchange=loadDocs;$("q").addEventListener("keydown",e=>{if(e.key==="Enter"&&(e.metaKey||e.ctrlKey))ask()});
loadUsers().then(()=>{const u=new URLSearchParams(location.search);
  if([...$("user").options].some(o=>o.value===u.get("user"))){$("user").value=u.get("user");loadDocs()}
  if(u.get("q"))$("q").value=u.get("q").slice(0,500);
  if(u.get("run")==="1")ask()});
</script></body></html>
"""
