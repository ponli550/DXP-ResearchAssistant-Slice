"use strict";
/* How we got this: one answer, or one report, drawn as the steps that really happened. It is a record, so nothing here can
   be edited or dragged. Every branch comes from the stored trace: the reports searched, the attempts made, what was kept. */

const NODE_W = 212;
const NODE_H = 58;
const GAP_X = 20;
const ROW = 86;
const KIND_LABEL = { code: "Plain code", model: "Model step", source: "Your reports", ok: "Result", none: "Result", proof: "Proof", ask: "You" };
const KIND_ICON = { code: "search", model: "spark", source: "page", ok: "check", none: "none", proof: "mark", ask: "chat" };

function reportByTitle(title) { return state.reports.find((d) => d.title === title); }
const quoted = (text) => el("blockquote", { class: "flow-quote" }, text);
const fact = (label, value) => el("div", { class: "flow-fact" }, el("dt", {}, label), el("dd", {}, value));
// A page opens beside the flow. The address still points at the desk, so opening it in a new tab works too.
const pageLink = (doc, page, label, quote = "") => el("a", {
  class: label ? "" : "cite-link", href: toDesk({ doc, p: page }),
  onclick: (e) => { if (!e.ctrlKey && !e.metaKey) { e.preventDefault(); openReader(doc, page, quote); } },
}, label || `${report(doc)?.short || "Report"} p.${page}`);
const STEP_MS = 700;
const COARSE = matchMedia("(max-width: 62rem)");
let flowView = null;

/* One stored answer: the question, the search and the reports it reached, the attempts, and what came out. */
function answerGraph(a) {
  const nodes = [];
  const edges = [];
  const add = (id, level, kind, title, sub, detail, mark) => { nodes.push({ id, level, kind, title, sub, detail, mark }); return id; };
  const join = (from, to, cls = "") => edges.push({ from, to, cls });
  const trace = a.trace || [];
  const search = trace.find((t) => t.kind === "search");
  const drafts = trace.filter((t) => t.kind === "draft");
  const checks = trace.filter((t) => t.kind === "check");
  const retries = trace.filter((t) => t.kind === "retry");
  const answered = a.status === "answered";

  add("q", 0, "ask", "Your question", a.question, () => [el("p", { class: "flow-lead" }, a.question), el("dl", {}, fact("Asked", a.at.replace("T", " ").slice(0, 16)))]);
  let last = "q";
  if (search) {
    add("search", 1, "code", "Searched your reports", `${plural(search.found, "passage")} found`, () => [
      el("p", { class: "flow-lead" }, "Plain search, with no model. Only reports you may read can be found."),
      el("dl", {}, fact("Words searched", search.query.replace(/"/g, "").replace(/ OR /g, ", ")), fact("Passages found", String(search.found)), fact("Time", `${search.ms} ms`),
        search.only?.length ? fact("Only in", search.only.join(", ")) : null)]);
    join(last, "search");
    last = "search";
    const docs = Object.entries(search.documents || {}).map(([title, n]) => ({ title, n, d: reportByTitle(title) }))
      .sort((x, y) => state.reports.indexOf(x.d) - state.reports.indexOf(y.d));
    docs.forEach(({ title, n, d }, i) => {
      const id = "doc" + i;
      add(id, 2, "source", d?.short || title, `${plural(n, "passage")}`, () => {
        const mine = (a.retrieved || []).filter((p) => p.title === title);
        return [el("p", { class: "flow-lead" }, title), el("ul", { class: "flow-pages" }, mine.map((p) => el("li", {}, pageLink(p.doc_id, p.page), el("p", {}, `${p.text.slice(0, 180).trim()}…`))))];
      });
      nodes.at(-1).together = `Found passages in ${plural(docs.length, "report")}`;
      join("search", id);
    });
    if (docs.length) {
      add("used", 3, "code", `${plural(search.found, "passage")} used`, "All the next step may read", () => [el("p", { class: "flow-lead" }, "These passages are all the next step was allowed to read. It cannot use anything else.")], "page");
      docs.forEach((_, i) => join("doc" + i, "used"));
      last = "used";
    }
  }
  let removedSoFar = 0;
  drafts.forEach((step, i) => {
    const check = checks[i];
    const baseline = step.model.startsWith("extractive");
    const draftId = "draft" + i;
    add(draftId, 4, "model", baseline ? `Closest sentence${drafts.length > 1 ? `, try ${i + 1}` : ""}` : `Draft${drafts.length > 1 ? `, try ${i + 1}` : ""}`, step.claims ? plural(step.claims, "claim") : "No claims", () => [
      el("p", { class: "flow-lead" }, baseline ? "No AI model is connected, so this step only picks the sentence closest to the question. A model key replaces it." : "The model drafted claims, each with an exact quote."),
      el("dl", {}, fact("Done by", step.model), fact("Claims", String(step.claims)), fact("Time", `${step.ms} ms`)),
      i > 0 && retries[i - 1]?.feedback ? el("div", {}, el("h4", {}, "Sent back because"), retries[i - 1].feedback.map((x) => el("p", {}, x))) : null].filter(Boolean));
    join(last, draftId);
    if (i > 0) join("check" + (i - 1), draftId, "is-retry");
    if (check) {
      const removed = (a.rejected_claims || []).slice(removedSoFar, removedSoFar + check.removed);
      removedSoFar += check.removed;
      const id = "check" + i;
      add(id, 5, "code", "Checked the quotes", `Kept ${check.kept}, removed ${check.removed}`, () => [
        el("p", { class: "flow-lead" }, "Plain code compares each quote with its page, and every number in a claim must be in its quote."),
        el("dl", {}, fact("Kept", String(check.kept)), fact("Removed", String(check.removed)), fact("Time", `${check.ms} ms`)),
        check.reasons?.length ? el("div", {}, el("h4", {}, "Why it was removed"), check.reasons.map((r) => el("p", {}, r))) : null,
        removed.length ? el("div", {}, el("h4", {}, "What it rejected"), removed.map((c) => el("div", {}, quoted(c.text), pageLink(c.doc_id, c.page, null, c.quote)))) : null].filter(Boolean), check.kept ? "check" : "cross");
      join(draftId, id);
    }
  });
  const claims = a.claims || [];
  add("out", 6, answered ? "ok" : "none", answered ? "Answered" : "No source found", answered ? `${plural(claims.length, "checked claim")}` : "Nothing passed the check", () => answered
    ? [el("p", { class: "flow-lead" }, `${plural(claims.length, "claim")} passed. That confirms the source. It does not prove the claim answers your question, so read it.`)]
    : [el("p", { class: "flow-lead" }, "Nothing was shown rather than something unchecked."),
      (a.rejected_claims?.[0]?.reason) ? el("div", {}, el("h4", {}, "Why"), el("p", {}, a.rejected_claims[0].reason)) : null,
      a.closest?.length ? el("div", {}, el("h4", {}, "Closest pages found"), el("p", { class: "flow-links" }, a.closest.map((p) => pageLink(p.doc_id, p.page)))) : null].filter(Boolean));
  (checks.length ? checks.map((_, i) => "check" + i) : [last]).forEach((id) => join(id, "out"));
  claims.slice(0, 5).forEach((c, i) => {
    const id = "claim" + i;
    add(id, 7, "proof", report(c.doc_id)?.short || c.title, `Page ${c.page}`, () => [
      c.text === c.quote ? null : el("p", { class: "flow-lead" }, c.text), quoted(el("mark", {}, c.quote)),
      el("button", { type: "button", class: "primary flow-open", onclick: () => openReader(c.doc_id, c.page, c.quote) }, icon("page"), `Open page ${c.page}`)].filter(Boolean));
    nodes.at(-1).together = `${plural(Math.min(claims.length, 5), "checked claim")}, each on its page`;
    join("out", id);
  });
  return { nodes, edges, start: "out" };
}

/* One report, the other way round: the pages used, and the questions that used them. Trimmed so it stays readable. */
function paperGraph(d) {
  const nodes = [];
  const edges = [];
  const add = (id, level, kind, title, sub, detail) => { nodes.push({ id, level, kind, title, sub, detail }); return id; };
  const pages = new Map();
  for (const r of state.rows) {
    for (const [doc, p] of r.cited) {
      if (doc !== d.id) continue;
      const set = pages.get(p) || new Map();
      set.set(r.q, r);
      pages.set(p, set);
    }
  }
  const counts = (p) => state.rows.reduce((n, r) => n + r.cited.filter(([x, y]) => x === d.id && y === p).length, 0);
  const top = [...pages.keys()].sort((a, b) => counts(b) - counts(a)).slice(0, 6).sort((a, b) => a - b);
  add("doc", 0, "source", d.short, `${pages.size} of ${d.pages} pages used`, () => [
    el("p", { class: "flow-lead" }, d.title),
    el("dl", {}, fact("Pages", String(d.pages)), fact("Pages your answers used", String(pages.size))),
    pages.size > top.length ? el("p", { class: "flow-note" }, `Showing the ${top.length} most cited pages.`) : null,
    el("p", { class: "flow-links" }, el("a", { href: toDesk({ q: `What does the ${d.edition[2] ? d.edition[2] + " edition" : d.title} say about ` }) }, "Ask about this report"), d.pdf ? el("a", { href: d.pdf, target: "_blank", rel: "noopener" }, "Open the PDF") : null)].filter(Boolean));
  top.forEach((p) => {
    const id = "p" + p;
    const asked = [...pages.get(p).values()];
    add(id, 1, "code", `Page ${p}`, `cited ${plural(counts(p), "time")}`, () => [
      el("p", { class: "flow-lead" }, `Page ${p} of ${d.title}.`),
      el("h4", {}, plural(asked.length, "question") + " used it"), el("ul", { class: "flow-pages" }, asked.map((r) => el("li", {}, state.questions[r.q]))),
      el("p", { class: "flow-links" }, pageLink(d.id, p, "Read this page"))]);
    edges.push({ from: "doc", to: id });
    asked.slice(0, 1).forEach((r, i) => {
      const qid = `${id}q${i}`;
      add(qid, 2, r.status === "answered" ? "ok" : "none", state.questions[r.q].slice(0, 42) + (state.questions[r.q].length > 42 ? "…" : ""), asked.length > 1 ? `and ${asked.length - 1} more` : "One question", () => [
        el("p", { class: "flow-lead" }, state.questions[r.q]),
        r.saved ? el("p", { class: "flow-links" }, link(here({ view: "flow", a: r.id }), {}, "How we got this")) : el("p", { class: "flow-note" }, "This older answer was not recorded.")].filter(Boolean));
      edges.push({ from: id, to: qid });
    });
  });
  for (const n of nodes) if (n.level) n.together = n.level === 1 ? "The pages your answers used" : "The questions that used them";
  return { nodes, edges, start: "doc", byLevel: true };
}

function layout(graph) {
  const levels = new Map();
  for (const n of graph.nodes) levels.set(n.level, [...(levels.get(n.level) || []), n]);
  const widest = Math.max(...[...levels.values()].map((l) => l.length));
  const width = widest * NODE_W + (widest - 1) * GAP_X;
  const used = [...levels.keys()].sort((a, b) => a - b);
  used.forEach((level, row) => {
    const list = levels.get(level);
    const span = list.length * NODE_W + (list.length - 1) * GAP_X;
    list.forEach((n, i) => { n.x = (width - span) / 2 + i * (NODE_W + GAP_X); n.y = row * ROW; });
  });
  return { width, height: used.length * ROW - (ROW - NODE_H) };
}
function edgePath(a, b, cls) {
  if (cls === "is-retry") {
    const x1 = a.x + NODE_W;
    const y1 = a.y + NODE_H / 2;
    const x2 = b.x;
    const y2 = b.y + NODE_H / 2;
    const mid = (x1 + x2) / 2;
    return `M${x1} ${y1} C${mid} ${y1},${mid} ${y2},${x2} ${y2}`;
  }
  const x1 = a.x + NODE_W / 2;
  const y1 = a.y + NODE_H;
  const x2 = b.x + NODE_W / 2;
  const y2 = b.y;
  const mid = (y1 + y2) / 2;
  return `M${x1} ${y1} C${x1} ${mid},${x2} ${mid},${x2} ${y2}`;
}

function flowCanvas(graph, details) {
  const size = layout(graph);
  const byId = new Map(graph.nodes.map((n) => [n.id, n]));
  const world = el("div", { class: "flow-world", style: `width:${size.width}px;height:${size.height}px` });
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("class", "flow-edges");
  svg.setAttribute("width", size.width);
  svg.setAttribute("height", size.height);
  svg.setAttribute("aria-hidden", "true");
  const paths = graph.edges.map((e) => {
    const p = document.createElementNS("http://www.w3.org/2000/svg", "path");
    p.setAttribute("d", edgePath(byId.get(e.from), byId.get(e.to), e.cls));
    p.setAttribute("class", e.cls || "");
    p.setAttribute("pathLength", "1");
    p.dataset.to = e.to;
    svg.append(p);
    return p;
  });
  const buttons = new Map();
  let selected = null;
  const select = (n) => {
    selected = n;
    details.classList.remove("is-reader");
    details.closest(".flow")?.classList.remove("is-reading");
    buttons.forEach((b, id) => b.setAttribute("aria-pressed", String(id === n.id)));
    details.replaceChildren(el("p", { class: `flow-tag kind-${n.kind}` }, icon(KIND_ICON[n.kind]), KIND_LABEL[n.kind]), el("h2", {}, n.title), ...n.detail());
  };
  const notes = graph.edges.filter((e) => e.cls === "is-retry").map((e) => {
    const a = byId.get(e.from);
    const b = byId.get(e.to);
    const note = el("span", { class: "flow-sent", style: `left:${(a.x + NODE_W + b.x) / 2}px;top:${(a.y + b.y + NODE_H) / 2}px` }, "Sent back");
    note.dataset.to = e.to;
    return note;
  });
  world.append(svg, ...notes, ...graph.nodes.map((n) => {
    const b = el("button", { type: "button", class: `flow-node kind-${n.kind}`, style: `left:${n.x}px;top:${n.y}px;width:${NODE_W}px;height:${NODE_H}px`, "aria-pressed": "false", title: n.title, onclick: () => { stop(); select(n); } },
      el("span", { class: "flow-icon" }, icon(n.mark || KIND_ICON[n.kind])), el("span", { class: "flow-text" }, el("b", {}, n.title), el("small", {}, n.sub)));
    buttons.set(n.id, b);
    return b;
  }));

  let scale = 1;
  let x = 0;
  let y = 0;
  const canvas = el("div", { class: "flow-canvas" }, world);
  const apply = () => { world.style.transform = `translate(${x}px, ${y}px) scale(${scale})`; };
  const fit = () => {
    const pad = 32;
    const w = canvas.clientWidth - pad * 2;
    const h = canvas.clientHeight - pad * 2;
    scale = Math.min(1.15, w / size.width, h / size.height);
    x = (canvas.clientWidth - size.width * scale) / 2;
    y = (canvas.clientHeight - size.height * scale) / 2;
    apply();
  };
  const zoom = (by) => {
    const next = Math.max(0.4, Math.min(1.6, scale * by));
    const cx = canvas.clientWidth / 2;
    const cy = canvas.clientHeight / 2;
    x = cx - ((cx - x) * next) / scale;
    y = cy - ((cy - y) * next) / scale;
    scale = next;
    apply();
  };
  canvas.addEventListener("wheel", (e) => { if (e.ctrlKey || e.metaKey) { e.preventDefault(); zoom(e.deltaY < 0 ? 1.1 : 0.9); } else { e.preventDefault(); x -= e.deltaX; y -= e.deltaY; apply(); } }, { passive: false });
  canvas.addEventListener("pointerdown", (e) => {
    if (e.target.closest("button")) return;
    const sx = e.clientX - x;
    const sy = e.clientY - y;
    canvas.setPointerCapture(e.pointerId);
    canvas.classList.add("is-panning");
    const move = (m) => { x = m.clientX - sx; y = m.clientY - sy; apply(); };
    const up = () => { canvas.removeEventListener("pointermove", move); canvas.removeEventListener("pointerup", up); canvas.classList.remove("is-panning"); };
    canvas.addEventListener("pointermove", move);
    canvas.addEventListener("pointerup", up);
  });
  const controls = el("div", { class: "flow-controls" },
    el("button", { type: "button", class: "icon-btn", "aria-label": "Zoom in", onclick: () => zoom(1.2) }, icon("plus")),
    el("button", { type: "button", class: "icon-btn", "aria-label": "Zoom out", onclick: () => zoom(1 / 1.2) }, icon("minus")),
    el("button", { type: "button", class: "icon-btn", "aria-label": "Fit to the screen", onclick: fit }, icon("fit")));
  canvas.append(controls);

  /* The sequence plays when a person arrives, and again on Replay. It follows the order things happened, so a retry comes after
     the check that sent it back; several reports found by one search arrive together. A step is paced so it can be read. */
  const groups = [];
  for (const n of graph.nodes) {
    const last = groups.at(-1);
    const same = last && (graph.byLevel ? last[0].level === n.level : last[0].level === n.level && last[0].together && n.together);
    if (same) last.push(n);
    else if (graph.byLevel && groups.some((g) => g[0].level === n.level)) groups.find((g) => g[0].level === n.level).push(n);
    else groups.push([n]);
  }
  const caption = el("p", { class: "flow-caption", role: "status", hidden: true });
  canvas.append(caption);
  let timers = [];
  const playing = (on) => {
    replayButton.replaceChildren(icon(on ? "stop" : "play"), on ? "Stop" : "Replay");
    caption.hidden = !on;
  };
  const marks = () => [...buttons.values(), ...paths, ...notes];
  function stop() {
    timers.forEach(clearTimeout);
    timers = [];
    marks().forEach((m) => m.classList.remove("is-waiting"));
    playing(false);
  }
  function play() {
    stop();
    if (REDUCED) return;
    marks().forEach((m) => m.classList.add("is-waiting"));
    playing(true);
    groups.forEach((group, i) => timers.push(setTimeout(() => {
      const ids = new Set(group.map((n) => n.id));
      group.forEach((n) => buttons.get(n.id).classList.remove("is-waiting"));
      [...paths, ...notes].filter((m) => ids.has(m.dataset.to)).forEach((m) => m.classList.remove("is-waiting"));
      caption.textContent = `Step ${i + 1} of ${groups.length}: ${group[0].together || `${group[0].title}, ${group[0].sub}`}`;
      select(group[0]);
    }, i * STEP_MS)));
    timers.push(setTimeout(stop, groups.length * STEP_MS));
  }
  const replayButton = el("button", { type: "button", class: "ghost flow-replay", onclick: () => (timers.length ? stop() : play()) });
  playing(false);
  flowView = { details, stop, back: () => select(selected) };
  select(byId.get(graph.start));
  requestAnimationFrame(() => { fit(); play(); });
  new ResizeObserver(fit).observe(canvas);
  return { canvas, replayButton };
}

/* The page behind a step, beside the flow: the original PDF when the file is on this server, and otherwise the words as they were read, with the quote marked. */
async function openReader(doc, start, quote = "") {
  const { details, stop, back } = flowView;
  stop();
  const d = report(doc);
  details.classList.add("is-reader");
  details.closest(".flow")?.classList.add("is-reading");
  let page = start;
  let mode = null;
  let seq = 0;
  const draw = async () => {
    const mine = ++seq;
    const wanted = page === start ? quote : "";
    const head = el("header", { class: "reader-head" },
      el("button", { type: "button", class: "flow-back", onclick: back }, icon("left"), "Back to this step"),
      el("h2", {}, `${d?.name || "Report"}, page ${page}`),
      el("span", { class: "block-acts" },
        el("button", { type: "button", class: "icon-btn", "aria-label": "Previous page", disabled: page <= 1, onclick: () => { page -= 1; draw(); } }, icon("left")),
        el("button", { type: "button", class: "icon-btn", "aria-label": "Next page", disabled: Boolean(d) && page >= d.pages, onclick: () => { page += 1; draw(); } }, icon("right"))));
    let j;
    try {
      j = await api("/api/page?" + new URLSearchParams({ user: USER, doc_id: doc, p: page, quote: wanted }));
    } catch (e) {
      if (mine === seq) details.replaceChildren(head, el("p", { class: "reader-note" }, e.message));
      return;
    }
    if (mine !== seq) return;
    if (!j.pdf) mode = "text";
    else if (!mode) mode = COARSE.matches ? "text" : "pdf";
    const tab = (id, text) => el("button", { type: "button", "aria-pressed": String(mode === id), onclick: () => { mode = id; draw(); } }, text);
    const tools = el("div", { class: "reader-tools" },
      j.pdf ? el("div", { class: "seg", role: "group", "aria-label": "How to show the page" }, tab("pdf", "Original PDF"), tab("text", "Text with the quote marked")) : null,
      el("span", { class: "flow-links" }, j.pdf ? el("a", { href: j.pdf, target: "_blank", rel: "noopener" }, "New tab") : null, el("a", { href: toDesk({ doc, p: page }) }, "Open in the research desk")));
    const notes = [];
    if (!j.pdf) notes.push(el("p", { class: "reader-note" }, "The original PDF is not on this server, so this is the page's text as it was read."));
    if (mode === "pdf" && wanted) notes.push(el("p", { class: "reader-quote" }, el("mark", {}, wanted)));
    let body;
    if (mode === "pdf") body = el("iframe", { class: "reader-pdf", src: `${j.pdf}&navpanes=0&view=FitH`, title: `${j.title}, page ${page}` });
    else if (!j.text.trim()) body = el("p", { class: "reader-note" }, "This page has no text to show. It may be a chart or a picture.");
    else {
      const [from, to] = j.span || [0, 0];
      body = el("div", { class: "reader-text" }, j.text.slice(0, from), j.span ? el("mark", {}, j.text.slice(from, to)) : null, j.text.slice(to));
      if (wanted && !j.span) notes.push(el("p", { class: "reader-note" }, "The check found the quote on this page, but line breaks stop it being marked here."));
    }
    details.replaceChildren(head, tools, ...notes, body);
    body.querySelector?.("mark")?.scrollIntoView({ block: "center" });
  };
  await draw();
}

/* Run again stays here: it asks, records the new run, and plays it. Say that it uses the model, because it costs. */
async function runAgain(question, button) {
  button.disabled = true;
  button.textContent = "Running…";
  try {
    const j = await send("/api/ask", "POST", { question });
    applyActivity(await api(`/api/me/activity?${who}`));
    navigate(here({ view: "flow", a: j.audit_id }));
  } catch (e) {
    toast(e.message);
    button.disabled = false;
    button.textContent = "Run again";
  }
}

async function showFlow(u) {
  document.title = "How we got this · Research assistant";
  const back = () => link(here(), { class: "flow-back" }, icon("left"), "Workspace");
  const doc = Number(u.get("doc"));
  const view = $("view");
  if (doc) {
    const d = report(doc);
    if (!d) { view.replaceChildren(el("div", { class: "failed" }, el("strong", {}, "That report is not in your library."), back())); return; }
    const details = el("aside", { class: "flow-details", "aria-live": "polite" });
    const { canvas, replayButton } = flowCanvas(paperGraph(d), details);
    view.replaceChildren(el("div", { class: "flow" }, el("header", { class: "flow-head" }, back(), el("h1", {}, `How ${d.name} was used`),
      el("span", { class: "flow-acts" }, replayButton)), canvas, details));
    return;
  }
  const id = Number(u.get("a"));
  view.replaceChildren(el("div", { class: "loading" }, el("span", { class: "skeleton skeleton-long" }), el("span", { class: "skeleton" })));
  let a;
  try {
    a = await api(`/api/answer/${id}?${who}`);
  } catch {
    const row = state.rows.find((r) => r.id === id);
    view.replaceChildren(el("div", { class: "failed" }, el("strong", {}, "This run was not recorded"), "Older questions were asked before each step was saved. Asking again records it, and uses the model if one is connected. ",
      row ? el("button", { type: "button", class: "link-btn", onclick: (e) => runAgain(state.questions[row.q], e.currentTarget) }, "Run again") : null, " ", back()));
    return;
  }
  const details = el("aside", { class: "flow-details", "aria-live": "polite" });
  const { canvas, replayButton } = flowCanvas(answerGraph(a), details);
  const claims = (a.claims || []).map((_, i) => ({ type: "finding", audit_id: id, claim: i }));
  view.replaceChildren(el("div", { class: "flow" },
    el("header", { class: "flow-head" }, back(), el("h1", { title: a.question }, a.question),
      el("span", { class: `flow-status is-${a.status === "answered" ? "ok" : "none"}` }, icon(a.status === "answered" ? "check" : "none"), a.status === "answered" ? "Answered" : "No source found"),
      el("span", { class: "flow-acts" },
        replayButton,
        el("button", { type: "button", class: "ghost", title: "Asks again and records a new run. Uses the model if one is connected.", onclick: (e) => runAgain(a.question, e.currentTarget) }, "Run again"),
        claims.length ? el("button", { type: "button", class: "primary", onclick: () => addToBoard("report", claims) }, "Add to report") : null)),
    canvas, details));
}
