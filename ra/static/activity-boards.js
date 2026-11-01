"use strict";
/* Dashboards and reports, saved inside the app. A dashboard is live: its blocks redraw from the person's current work.
   A report is a document of checked facts: its words come from the stored answer on the server, and the only text a
   person adds is marked as their own note. */

const BLOCK_TITLE = { topics: "What each report talks about", pages: "Where your answers came from", days: "Days you asked", findings: "What you've found", questions: "Your questions", finding: "Checked fact" };

async function topicsFor(block) {
  if (!block.terms?.length) return state.topics.filter((t) => t.group !== "mine");
  const list = await Promise.all(block.terms.map((term) => state.topics.find((t) => t.term.toLowerCase() === term.toLowerCase()) || fetchTopic(term).catch(() => null)));
  return list.filter(Boolean);
}
const openTopic = (query, id) => navigate(here({ topic: query, ...(id ? { report: id } : {}), tab: "mentions" }));
const openReport = (id) => navigate(here({ report: id }));

async function blockBody(block) {
  const scope = { topic: block.topic || null, report: block.report || null, outcome: block.outcome || "any" };
  if (block.type === "topics") return el("div", { class: "scroll" }, topicTable(await topicsFor(block), { selected: {}, onCell: openTopic, onTopic: (q) => openTopic(q), onColumn: openReport, grouped: false }));
  if (block.type === "pages") {
    const hits = block.topic ? (await api("/api/find?" + new URLSearchParams({ user: USER, q: `"${block.topic}"`, limit: 200 })).catch(() => ({ hits: [] }))).hits : null;
    return el("div", { class: "strips" }, pageStrips(rowsWhere({ ...scope, report: null }), { term: block.topic, hits, onName: openReport }));
  }
  if (block.type === "days") return dayBars(rowsWhere(scope), { onPick: (day) => navigate(here({ day })) });
  if (block.type === "findings") {
    const list = findingsWhere(scope);
    return el("div", { class: "scroll" }, list.length ? list.map((f) => findingItem(f)) : el("div", { class: "empty" }, el("strong", {}, "No checked facts here yet"), `Ask ${block.topic ? "about " + block.topic + " " : ""}in the research desk and they collect here.`));
  }
  if (block.type === "questions") {
    const groups = groupQuestions(rowsWhere(scope));
    return el("div", { class: "scroll" }, groups.length ? groups.map(questionItem) : el("div", { class: "empty" }, el("strong", {}, "No questions here yet"), block.topic ? el("a", { href: toDesk({ q: `What do the reports say about ${block.topic}?` }) }, "Ask the first one") : null));
  }
  if (block.type === "finding") return block.missing ? el("div", { class: "empty" }, "The answer behind this fact has been removed.") : findingItem(block, { actions: false });
  return el("div");
}

async function saveBoard(board) {
  const saved = await send(`/api/me/boards/${board.id}`, "PUT", { title: board.title, blocks: board.blocks });
  board.blocks = saved.blocks;
  board.updated_at = saved.updated_at;
}
function titleInput(board, onSaved) {
  return el("input", {
    class: "board-title", value: board.title, maxLength: 80, "aria-label": "Title",
    onchange: async (e) => {
      const title = e.target.value.trim();
      if (!title) { e.target.value = board.title; return; }
      board.title = title;
      await saveBoard(board);
      onSaved?.();
    },
  });
}
const boardsOf = async (kind) => (await api(`/api/me/boards?${who}`)).boards.filter((b) => b.kind === kind);
async function removeBoard(board, word, viewName) {
  const ok = await confirmDelete(`Delete “${board.title}”?`, `This ${word} goes for good. Your questions and answers stay.`);
  if (!ok) return;
  await api(`/api/me/boards/${board.id}?${who}`, { method: "DELETE" });
  navigate(here({ view: viewName }));
}
function boardTabs(list, current, viewName) {
  return el("div", { class: "tabs-row board-tabs", role: "tablist" }, list.map((b) => link(here({ view: viewName, board: b.id }), { role: "tab", "aria-selected": String(b.id === current) }, b.title)));
}

/* ---- dashboards ---- */
const STARTERS = [
  ["My overview", [{ type: "topics" }, { type: "pages" }, { type: "findings" }, { type: "days" }]],
  ["Questions that need another look", [{ type: "questions", outcome: "none" }, { type: "days", outcome: "none" }]],
  ["Evidence so far", [{ type: "findings" }, { type: "pages" }]],
];
async function createFromAssistant(kind, title, blocks) {
  const b = await send("/api/me/boards", "POST", { kind, title, blocks });
  navigate(here({ view: kind === "report" ? "reports" : "dashboards", board: b.id }));
  return b;
}

async function showDashboards(u) {
  document.title = "Dashboards · Research assistant";
  const view = $("view");
  const list = await boardsOf("dashboard");
  const id = Number(u.get("board")) || list[0]?.id;
  if (!id) {
    view.replaceChildren(el("div", { class: "board-empty" }, el("h1", {}, "Build a dashboard from your work"),
      el("p", {}, "A dashboard keeps the views you come back to, and stays up to date as you ask more. Start from one of these, pin things from the workspace, or ask the assistant."),
      el("div", { class: "starter-list" }, STARTERS.map(([title, blocks]) => el("button", { type: "button", class: "starter", onclick: () => createFromAssistant("dashboard", title, blocks) }, el("b", {}, title), el("span", {}, blocks.map((b) => BLOCK_TITLE[b.type]).join(" · ")))))));
    return;
  }
  const board = await api(`/api/me/boards/${id}?${who}`);
  const redraw = () => showDashboards(new URLSearchParams(here({ view: "dashboards", board: id }).split("?")[1]));
  const blocks = await Promise.all(board.blocks.map(async (b, i) => {
    const move = (by) => async () => {
      const [x] = board.blocks.splice(i, 1);
      board.blocks.splice(i + by, 0, x);
      await saveBoard(board);
      redraw();
    };
    const drop = async () => { board.blocks.splice(i, 1); await saveBoard(board); redraw(); };
    return el("section", { class: `panel block block-${b.type}` },
      el("header", { class: "panel-head" }, el("h2", {}, BLOCK_TITLE[b.type] + (b.topic ? `: ${b.topic}` : "") + (b.outcome && b.outcome !== "any" ? ` (${OUTCOMES[b.outcome]?.[0]})` : "")),
        el("span", { class: "block-acts" },
          i > 0 ? el("button", { type: "button", class: "icon-btn", "aria-label": "Move earlier", onclick: move(-1) }, icon("up")) : null,
          i < board.blocks.length - 1 ? el("button", { type: "button", class: "icon-btn", "aria-label": "Move later", onclick: move(1) }, icon("down")) : null,
          el("button", { type: "button", class: "icon-btn", "aria-label": `Remove ${BLOCK_TITLE[b.type]}`, onclick: drop }, icon("cross")))),
      el("div", { class: "block-body" }, await blockBody(b)));
  }));
  view.replaceChildren(el("div", { class: "board" },
    el("header", { class: "board-head" }, boardTabs(list, id, "dashboards"),
      el("span", { class: "flow-acts" },
        el("button", { type: "button", class: "ghost", onclick: () => createFromAssistant("dashboard", "New dashboard", [{ type: "topics" }]) }, icon("plus"), "New"),
        el("button", { type: "button", class: "ghost", onclick: () => removeBoard(board, "dashboard", "dashboards") }, icon("trash"), "Delete"))),
    el("div", { class: "board-title-row" }, titleInput(board, redraw)),
    blocks.length ? el("div", { class: "block-grid" }, blocks)
      : el("div", { class: "empty" }, el("strong", {}, "This dashboard is empty"), "Pin a chart or a checked fact from the workspace, or ask the assistant.")));
}

/* ---- reports ---- */
async function showReports(u) {
  document.title = "Reports · Research assistant";
  const view = $("view");
  const list = await boardsOf("report");
  const id = Number(u.get("board")) || list[0]?.id;
  if (!id) {
    view.replaceChildren(el("div", { class: "board-empty" }, el("h1", {}, "Turn what you've found into a report"),
      el("p", {}, `A report holds only checked facts, each with its page, plus any notes you add. You hold ${plural(state.findings.length, "checked fact")} so far.`),
      el("div", { class: "starter-list" }, el("button", { type: "button", class: "starter", onclick: () => createFromAssistant("report", "What I've found so far", state.findings.map(findingAdd)) }, el("b", {}, "Start from everything I've found"), el("span", {}, plural(state.findings.length, "checked fact"))),
        el("button", { type: "button", class: "starter", onclick: () => createFromAssistant("report", "New report", [{ type: "heading", text: "Summary" }]) }, el("b", {}, "Start empty"), el("span", {}, "Add facts from the list beside it")))));
    return;
  }
  const board = await api(`/api/me/boards/${id}?${who}`);
  const redraw = () => showReports(new URLSearchParams(here({ view: "reports", board: id }).split("?")[1]));
  const change = async () => { await saveBoard(board); redraw(); };
  const paper = await Promise.all(board.blocks.map(async (b, i) => {
    const tools = el("span", { class: "block-acts no-print" },
      i > 0 ? el("button", { type: "button", class: "icon-btn", "aria-label": "Move up", onclick: async () => { const [x] = board.blocks.splice(i, 1); board.blocks.splice(i - 1, 0, x); change(); } }, icon("up")) : null,
      i < board.blocks.length - 1 ? el("button", { type: "button", class: "icon-btn", "aria-label": "Move down", onclick: async () => { const [x] = board.blocks.splice(i, 1); board.blocks.splice(i + 1, 0, x); change(); } }, icon("down")) : null,
      el("button", { type: "button", class: "icon-btn", "aria-label": "Remove", onclick: () => { board.blocks.splice(i, 1); change(); } }, icon("cross")));
    const edit = (cls, tag) => el(tag, {
      class: cls, value: b.text, "aria-label": b.type === "heading" ? "Heading" : "Your note",
      onchange: async (e) => {
        if (!e.target.value.trim()) { toast("It needs some text. Remove it with the cross instead."); e.target.value = b.text; return; }
        b.text = e.target.value;
        await saveBoard(board);
      },
    });
    if (b.type === "heading") return el("div", { class: "doc-row" }, edit("doc-heading", "input"), tools);
    if (b.type === "note") return el("div", { class: "doc-row" }, el("div", { class: "doc-note" }, el("span", {}, "Your note"), edit("doc-note-text", "textarea")), tools);
    if (b.type === "finding") {
      if (b.missing) return el("div", { class: "doc-row" }, el("p", { class: "doc-missing" }, "The answer behind this fact has been removed."), tools);
      return el("div", { class: "doc-row" }, el("div", { class: "doc-fact" }, el("p", {}, b.text), b.quote === b.text ? null : el("blockquote", {}, b.quote),
        el("p", { class: "doc-cite" }, el("a", { href: toDesk({ doc: b.doc_id, p: b.page }) }, `${b.title}, page ${b.page}`), " ", link(here({ view: "flow", a: b.audit_id }), { class: "no-print" }, "How we got this"))), tools);
    }
    return el("div", { class: "doc-row" }, el("section", { class: "doc-chart" }, el("h3", {}, BLOCK_TITLE[b.type]), await blockBody(b)), tools);
  }));
  const held = new Set(board.blocks.filter((b) => b.type === "finding").map((b) => `${b.audit_id}:${b.claim}`));
  const rest = state.findings.filter((f) => !held.has(`${f.audit_id}:${f.claim}`));
  const sources = [...new Map(board.blocks.filter((b) => b.type === "finding" && !b.missing).map((b) => [b.doc_id, b.title])).values()];
  view.replaceChildren(el("div", { class: "report" },
    el("header", { class: "board-head no-print" }, boardTabs(list, id, "reports"),
      el("span", { class: "flow-acts" },
        el("button", { type: "button", class: "ghost", onclick: () => createFromAssistant("report", "New report", [{ type: "heading", text: "Summary" }]) }, icon("plus"), "New"),
        el("button", { type: "button", class: "ghost", onclick: () => removeBoard(board, "report", "reports") }, icon("trash"), "Delete"),
        el("button", { type: "button", class: "primary", onclick: () => print() }, icon("print"), "Print or save as PDF"))),
    el("div", { class: "report-body" },
      el("article", { class: "paper" }, titleInput(board, redraw), paper,
        paper.length ? null : el("div", { class: "empty no-print" }, "Add a checked fact from the list beside this page."),
        sources.length ? el("footer", { class: "doc-sources" }, el("h3", {}, "Sources"), el("ul", {}, sources.map((s) => el("li", {}, s)))) : null),
      el("aside", { class: "report-side no-print" },
        el("h2", {}, "Add to this report"),
        el("div", { class: "report-add" },
          el("button", { type: "button", class: "ghost", onclick: () => { board.blocks.push({ type: "heading", text: "New section" }); change(); } }, "Heading"),
          el("button", { type: "button", class: "ghost", onclick: () => { board.blocks.push({ type: "note", text: "Your note" }); change(); } }, "Your note"),
          el("button", { type: "button", class: "ghost", onclick: () => { board.blocks.push({ type: "topics" }); change(); } }, "Topic table")),
        state.connected ? null : modelNote(),
        el("h3", {}, `Checked facts not in it (${rest.length})`),
        el("div", { class: "scroll report-rest" }, rest.length ? rest.map((f) => el("article", { class: "item" }, el("p", { class: "finding-text" }, f.text),
          el("div", { class: "item-acts" }, el("span", { class: "cite-link" }, `${report(f.doc_id)?.short || "Report"} p.${f.page}`), el("button", { type: "button", class: "link-btn", onclick: () => { board.blocks.push(findingAdd(f)); change(); } }, "Add"))))
          : el("div", { class: "empty" }, "Every checked fact you hold is in this report."))))));
}
