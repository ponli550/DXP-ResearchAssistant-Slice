"use strict";
/* The research desk. Nothing here builds HTML from text: every node is made with el(), so report text and questions can
   never become markup. The progress lines come from the server's real steps; a draft is never shown before its check. */

const $ = (id) => document.getElementById(id);
const REDUCED = matchMedia("(prefers-reduced-motion: reduce)").matches;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const ICONS = {
  mark: ["M6 3h8.5L19 7.5V21H6z", "m9.5 14 2 2 4-4.5"],
  check: ["M20 6 9 17l-5-5"],
  none: ["M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18", "M8 12h8"],
  alert: ["M12 4 2.5 20h19z", "M12 10v4", "M12 17h.01"],
  cross: ["M18 6 6 18", "M6 6l12 12"],
  chevron: ["m9 6 6 6-6 6"],
  left: ["m15 6-6 6 6 6"],
  right: ["m9 6 6 6-6 6"],
  thumb: ["M7 10v11H3V10z", "m7 10 4-7a3 3 0 0 1 3 3v3h5a2 2 0 0 1 2 2.4l-1.5 7a2 2 0 0 1-2 1.6H7"],
  flag: ["M5 21V4h13l-2 4 2 4H5"],
  copy: ["M9 9h11v11H9z", "M5 15H4V4h11v1"],
  table: ["M3 5h18v14H3z", "M3 10h18", "M9 5v14", "M15 5v14"],
  page: ["M6 3h8.5L19 7.5V21H6z"],
  back: ["M9 14 4 9l5-5", "M4 9h10a6 6 0 0 1 0 12h-3"],
  stop: ["M6 6h12v12H6z"],
  trash: ["M4 7h16", "M9 7V4h6v3", "M6 7l1 13h10l1-13"],
  menu: ["M4 7h16", "M4 12h16", "M4 17h16"],
  clock: ["M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18", "M12 7v5l3 2"],
  plus: ["M12 5v14", "M5 12h14"],
  send: ["M12 19V5", "m5 12 7-7 7 7"],
  down: ["m6 9 6 6 6-6"],
  upload: ["M12 16V5", "m7 10 5-5 5 5", "M5 20h14"],
};

function icon(name, cls = "") {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("class", ("icon " + cls).trim());
  svg.setAttribute("aria-hidden", "true");
  for (const d of ICONS[name]) {
    const p = document.createElementNS("http://www.w3.org/2000/svg", "path");
    p.setAttribute("d", d);
    svg.append(p);
  }
  return svg;
}

function el(tag, props = {}, ...kids) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(props)) {
    if (k === "class") e.className = v;
    else if (k.startsWith("on") || k in e) e[k] = v;
    else e.setAttribute(k, v);
  }
  e.append(...kids.flat().filter((k) => k !== null && k !== undefined && k !== false));
  return e;
}

async function api(path, opts) {
  const r = await fetch(path, opts);
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(j.detail || r.statusText);
  return j;
}
const send = (method, path, body) => api(path, { method, headers: { "Content-Type": "application/json" }, body: body && JSON.stringify(body) });
const post = (path, body) => send("POST", path, body);

async function* events(path, body, signal) {
  const r = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body), signal });
  if (!r.ok) {
    const j = await r.json().catch(() => ({}));
    throw new Error(j.detail || r.statusText);
  }
  const reader = r.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) return;
    buffer += decoder.decode(value, { stream: true });
    let cut;
    while ((cut = buffer.indexOf("\n\n")) >= 0) {
      const chunk = buffer.slice(0, cut);
      buffer = buffer.slice(cut + 2);
      if (chunk.startsWith("data: ")) yield JSON.parse(chunk.slice(6));
    }
  }
}

const state = {
  user: "", model: "", docs: [], ticked: new Set(), mode: "ask", running: null, history: [], tables: [],
  saved: "history", page: { doc: null, n: 1, seq: 0 }, proof: null, tableNodes: new Map(), openedPage: false,
};
const isDemo = () => state.model.startsWith("extractive");
const stored = (k) => localStorage.getItem("ra-" + k);
const keep = (k, v) => localStorage.setItem("ra-" + k, v);
const say = (text) => { $("live").textContent = text; };
const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;
const seconds = (ms) => (ms < 1000 ? `${Math.round(ms)} ms` : `${(ms / 1000).toFixed(1)} s`);
const docById = (id) => state.docs.find((d) => d.id === id);
const editionName = (title) => { const d = state.docs.find((x) => x.title === title); return d && d.edition[2] ? d.edition[2] : title; };
const shortName = (title) => {
  const name = editionName(title);
  const m = name.match(/^([A-Z][a-z]{2})[a-z]* (\d{4})$/);
  return m ? `${m[1]} ${m[2]}` : name;
};
const dayOf = (at) => new Date(at).toLocaleDateString("en-GB", { day: "numeric", month: "short", timeZone: "UTC" });
const fullDay = (at) => new Date(at).toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric", timeZone: "UTC" });
const uniquePages = (rows) => [...new Map(rows.map((r) => [`${r.doc_id}:${r.page}`, r])).values()];

/* Reports: the same ticks appear in the question box's list and on the rail's timeline. */
const tickBox = (d) => el("input", {
  type: "checkbox", checked: state.ticked.has(d.id), "data-tick": String(d.id), "aria-label": `Search ${d.title}`,
  onchange: (e) => setTick(d.id, e.target.checked),
});
function setTick(id, on) {
  if (on) state.ticked.add(id); else state.ticked.delete(id);
  saveTicks();
  syncTicks();
}
function syncTicks() {
  document.querySelectorAll("[data-tick]").forEach((box) => {
    const on = state.ticked.has(Number(box.dataset.tick));
    box.checked = on;
    box.closest(".report")?.classList.toggle("is-off", !on);
  });
  renderScope();
}
function reportRow(d) {
  const open = el("button", { type: "button", class: "report-open", "data-doc": String(d.id), onclick: async () => { d.firstReadable ??= await firstPageWithText(d.id); openPage({ doc: d.id, n: d.firstReadable }); } },
    el("span", { class: "report-name", title: d.title }, d.edition[2] || d.title), el("span", { class: "report-pages" }, `${d.pages}p`));
  return el("li", { class: "report" + (state.ticked.has(d.id) ? "" : " is-off") }, tickBox(d), open, d.private ? deleteButton(d) : null);
}
function deleteButton(d) {
  return el("button", {
    type: "button", class: "report-delete", "aria-label": `Delete ${d.title}`, title: "Delete this upload",
    onclick: async () => {
      const yes = await confirmDelete(`Delete “${d.title}”?`, "This removes the report, its search index, and the saved answers and table cells that used it. Your questions stay in your history. This cannot be undone.");
      if (yes) removeReport(d);
    },
  }, icon("trash"));
}
function renderReports() {
  const rows = [];
  let previous = null;
  let others = false;
  for (const d of state.docs.filter((x) => !x.private)) {
    const year = d.edition[0];
    if (!year && !others && state.docs.some((x) => x.edition[0])) {
      others = true;
      rows.push(el("li", { class: "report-group" }, "Other documents"));
    }
    if (year && previous && previous - year > 2) rows.push(el("li", { class: "report-gap" }, `${previous - year} years earlier`));
    if (year) previous = year;
    rows.push(reportRow(d));
  }
  const mine = state.docs.filter((x) => x.private);
  if (mine.length) rows.push(el("li", { class: "report-group" }, `Your uploads, only ${state.user} can see these`), ...mine.map(reportRow));
  $("reports").replaceChildren(...rows);
  $("scopeList").replaceChildren(...state.docs.map((d) => el("li", {}, el("label", { class: "scope-row" }, tickBox(d),
    el("span", { class: "scope-name", title: d.title }, d.edition[2] || d.title), el("span", { class: "scope-pages" }, d.private ? "Private" : `${d.pages} pages`)))));
  markOpenReport();
}
function markOpenReport() {
  document.querySelectorAll(".report-open").forEach((b) => b.setAttribute("aria-current", String(Number(b.dataset.doc) === state.page.doc)));
}
const saveTicks = () => keep("ticks-" + state.user, [...state.ticked].join(","));
function renderScope() {
  const all = state.docs.length, n = state.ticked.size;
  const toggleLabel = n === all ? "None" : "All";
  $("scopeCount").textContent = `${n} of ${all}`;
  $("scopeAll").textContent = toggleLabel;
  $("scopeAllPop").textContent = toggleLabel;
  $("scopeLabel").textContent = n === all ? (all === 1 ? "1 report" : `All ${all} reports`) : n ? `${n} of ${all} reports` : "No reports";
  $("barNote").textContent = n === all ? "" : "Only the ticked reports are searched";
}
$("scopeAll").onclick = $("scopeAllPop").onclick = () => {
  state.ticked = state.ticked.size === state.docs.length ? new Set() : new Set(state.docs.map((d) => d.id));
  saveTicks();
  syncTicks();
};

/* The side panel stays a slim strip until someone asks for it, and remembers if they prefer it open. */
function setRail(open) {
  $("desk").classList.toggle("rail-closed", !open);
  keep("rail", open ? "open" : "closed");
  queueProof();
}
function openSaved(tab) {
  setRail(true);
  state.saved = tab;
  renderSaved();
  $("savedFilter").focus();
}
$("railOpen").onclick = () => { setRail(true); $("railClose").focus(); };
$("railClose").onclick = () => { setRail(false); $("railOpen").focus(); };
$("stripHistory").onclick = () => openSaved("history");
$("stripTables").onclick = () => openSaved("tables");
$("stripUser").onclick = () => { setRail(true); $("user").focus(); };

/* The small menus in the question box open beside their button, above it when the box sits at the foot of the page. */
function placeMenu(button, menu) {
  menu.addEventListener("toggle", (e) => {
    const open = e.newState === "open";
    button.setAttribute("aria-expanded", String(open));
    if (!open) return;
    const b = button.getBoundingClientRect(), m = menu.getBoundingClientRect();
    const fitsBelow = b.bottom + 6 + m.height < innerHeight;
    menu.style.left = `${Math.max(8, Math.min(innerWidth - m.width - 8, b.left))}px`;
    menu.style.top = `${fitsBelow ? b.bottom + 6 : Math.max(8, b.top - m.height - 6)}px`;
  });
}
placeMenu($("plusBtn"), $("plusPop"));
placeMenu($("scopeBtn"), $("scopePop"));
placeMenu($("modeBtn"), $("modePop"));
const closeMenu = (id) => $(id).hidePopover?.();
$("plusUpload").onclick = () => { closeMenu("plusPop"); $("uploadInput").click(); };
$("plusReports").onclick = () => { closeMenu("plusPop"); setRail(true); document.querySelector(".report-open")?.focus(); };
$("plusHistory").onclick = () => { closeMenu("plusPop"); openSaved("history"); };
$("plusTables").onclick = () => { closeMenu("plusPop"); openSaved("tables"); };

/* Deleting always asks first, in a dialog that says what goes. Cancel holds the focus, so a stray Enter never deletes. */
function confirmDelete(title, body) {
  const dialog = $("confirm");
  $("confirmTitle").textContent = title;
  $("confirmBody").textContent = body;
  dialog.returnValue = "";
  dialog.showModal();
  $("confirmCancel").focus();
  return new Promise((resolve) => dialog.addEventListener("close", () => resolve(dialog.returnValue === "delete"), { once: true }));
}
$("confirmCancel").onclick = () => $("confirm").close("cancel");
$("confirmOk").onclick = () => $("confirm").close("delete");
$("confirm").addEventListener("click", (e) => { if (e.target === $("confirm")) $("confirm").close("cancel"); }); // the area outside the box

/* Uploads: a person's own reports, searchable by them and nobody else */
const MAX_UPLOAD_MB = 50;
function attachment(state_, text, ...actions) {
  const glyph = state_ === "working" ? el("span", { class: "spinner" }) : icon(state_ === "fail" ? "alert" : "check");
  const chip = el("div", { class: "attachment", "data-state": state_ }, glyph, el("span", { class: "attachment-text" }, text), ...actions);
  if (state_ !== "working") chip.append(el("button", { type: "button", class: "attachment-close", "aria-label": "Dismiss", title: "Dismiss", onclick: () => chip.remove() }, icon("cross")));
  $("attachments").append(chip);
  say(text);
  return chip;
}
async function addReports(files) {
  for (const file of files) {
    if (!/\.pdf$/i.test(file.name)) { attachment("fail", `${file.name}: only PDF files can be added.`); continue; }
    if (file.size > MAX_UPLOAD_MB * 1024 * 1024) { attachment("fail", `${file.name}: this file is over the ${MAX_UPLOAD_MB} MB limit.`); continue; }
    const chip = attachment("working", `Reading ${file.name}…`);
    try {
      const r = await api("/api/me/uploads?" + new URLSearchParams({ user: state.user, name: file.name }), { method: "POST", headers: { "Content-Type": "application/pdf" }, body: file });
      await loadLibrary();
      state.ticked.add(r.doc_id);
      saveTicks();
      renderReports();
      renderScope();
      chip.remove();
      const gaps = r.low_text_pages ? ` ${plural(r.low_text_pages, "page")} had no readable text.` : "";
      attachment("done", `${r.title} added, ${plural(r.pages, "page")}. Only ${state.user} can see it.${gaps}`,
        state.docs.length > 1 ? el("button", { type: "button", class: "attachment-action", onclick: (e) => { state.ticked = new Set([r.doc_id]); saveTicks(); syncTicks(); e.target.replaceWith("Searching only this report."); $("q").focus(); } }, "Search only this") : null);
    } catch (e) {
      chip.remove();
      attachment("fail", `${file.name}: ${e.message}`);
    }
  }
}
async function removeReport(d) {
  try {
    await send("DELETE", `/api/me/uploads/${d.id}?user=${encodeURIComponent(state.user)}`);
  } catch (e) { attachment("fail", `${d.title}: ${e.message}`); return; }
  state.ticked.delete(d.id);
  await loadLibrary();
  if (!state.ticked.size) state.ticked = new Set(state.docs.map((x) => x.id)); // it was the only one ticked
  saveTicks();
  renderReports();
  renderScope();
  resetDesk(); // what is on screen may quote it
  loadHistory();
  loadTables();
  document.querySelectorAll('.attachment:not([data-state="working"])').forEach((chip) => chip.remove());
  attachment("done", `${d.title} deleted, with the answers that used it.`);
}
$("uploadInput").onchange = (e) => { const files = [...e.target.files]; e.target.value = ""; addReports(files); };
const carriesFiles = (e) => [...(e.dataTransfer?.types || [])].includes("Files");
document.addEventListener("dragover", (e) => { if (carriesFiles(e)) { e.preventDefault(); $("work").classList.add("is-dropping"); } });
document.addEventListener("dragleave", (e) => { if (!e.relatedTarget) $("work").classList.remove("is-dropping"); });
document.addEventListener("drop", (e) => { if (!carriesFiles(e)) return; e.preventDefault(); $("work").classList.remove("is-dropping"); addReports([...e.dataTransfer.files]); });

/* Saved work */
async function loadHistory() {
  try { state.history = (await api("/api/me/history?user=" + encodeURIComponent(state.user))).questions; }
  catch { state.history = []; }
  if (state.saved === "history") renderSaved();
}
async function loadTables() {
  try { state.tables = (await api("/api/me/analyses?user=" + encodeURIComponent(state.user))).analyses; }
  catch { state.tables = []; }
  if (state.saved === "tables") renderSaved();
}
function renderSaved() {
  const want = $("savedFilter").value.trim().toLowerCase();
  const list = $("savedList");
  $("tabHistory").setAttribute("aria-selected", String(state.saved === "history"));
  $("tabTables").setAttribute("aria-selected", String(state.saved === "tables"));
  if (state.saved === "history") {
    const rows = state.history.filter((h) => h.question.toLowerCase().includes(want));
    list.replaceChildren(...(rows.length ? rows.map(savedQuestion) : [el("p", { class: "saved-empty" }, want ? "Nothing matches that." : "Questions you ask appear here.")]));
  } else {
    const rows = state.tables.filter((t) => t.name.toLowerCase().includes(want));
    list.replaceChildren(...(rows.length ? rows.map(savedTable) : [el("p", { class: "saved-empty" }, want ? "Nothing matches that." : 'No tables yet. Ask a question, then choose "Ask across all reports".')]));
  }
}
function savedQuestion(h) {
  const answered = h.status === "answered";
  const sub = h.review ? "Sent for review" : null;
  return el("button", {
    type: "button", class: "saved-row" + (answered ? " is-answered" : ""), title: h.saved ? h.question : `${h.question}\n\nThis answer was not saved. Select to ask again.`,
    onclick: () => (h.saved ? replay(h.id) : prefill(h.question, "This answer was not saved. Press Enter to ask again.")),
  }, icon(answered ? "check" : "none"), el("span", { class: "sr-only" }, answered ? "Answered: " : "No answer: "),
  el("span", { class: "saved-q" }, h.question), el("span", { class: "saved-at" }, dayOf(h.at)), sub && el("span", { class: "saved-sub" }, sub));
}
function savedTable(t) {
  return el("button", { type: "button", class: "saved-row is-answered", title: t.name, onclick: () => openTable(t.id) },
    icon("table"), el("span", { class: "saved-q" }, t.name), el("span", { class: "saved-at" }, `${t.answered}/${t.total}`));
}
$("tabHistory").onclick = () => { state.saved = "history"; renderSaved(); };
$("tabTables").onclick = () => { state.saved = "tables"; renderSaved(); };
$("savedFilter").oninput = renderSaved;

/* A few real questions to start from, gone as soon as someone types */
function renderSuggestions() {
  const newest = state.docs.find((d) => d.edition[2]);
  const where = newest ? `the ${newest.edition[2]} report` : "your reports";
  // ponytail: these fixed questions suit economic reports; a library of something else would supply its own.
  const ideas = [
    ["Growth forecast", `What is the GDP growth forecast in ${where}?`, "ask"],
    ...(state.docs.length > 1 ? [["Compare editions", "What GDP growth does this report forecast?", "table"]] : []),
    ["Main risks", `What are the main risks to the outlook in ${where}?`, "ask"],
  ];
  $("suggestions").replaceChildren(...ideas.map(([label, question, mode]) => el("button", {
    type: "button", class: "suggestion", title: question,
    onclick: () => { setMode(mode); $("q").value = question; $("q").focus(); refreshSuggestions(); },
  }, label)));
  refreshSuggestions();
}
function refreshSuggestions() { $("suggestions").hidden = Boolean($("q").value.trim()); }
$("q").addEventListener("input", refreshSuggestions);
function prefill(text, note = "") {
  setMode("ask");
  const q = $("q");
  q.value = text;
  q.focus();
  q.setSelectionRange(text.length, text.length);
  refreshSuggestions();
  note ? composerNote(note) : composerNote("");
}
function composerNote(text) { const n = $("composerNote"); n.textContent = text; n.hidden = !text; }

async function leaveEmpty(fn) {
  const work = $("work");
  if (!work.classList.contains("is-empty")) { fn(); return; }
  const change = () => { work.classList.remove("is-empty"); $("thread").replaceChildren(); $("newThread").hidden = false; fn(); };
  if (document.startViewTransition && !REDUCED && document.visibilityState === "visible") {
    const moving = document.startViewTransition(change);
    moving.ready.catch(() => {});
    moving.finished.catch(() => {});
    await moving.updateCallbackDone;
  } else change();
}
// Only the thread moves: scrollIntoView would also scroll the page behind the panels.
function showInThread(node) { const thread = $("thread"); thread.scrollTop = node.offsetTop - parseFloat(getComputedStyle(thread).paddingTop); }
function resetDesk() {
  stopRunning();
  state.tableNodes.forEach((t) => clearTimeout(t.timer));
  state.tableNodes.clear();
  clearProof();
  $("thread").replaceChildren();
  $("work").classList.add("is-empty");
  $("newThread").hidden = true;
  showPane(false);
  $("showPane").hidden = true;
  state.page.doc = null;
  markOpenReport();
  refreshSuggestions();
}
$("newThread").onclick = () => { resetDesk(); $("q").focus(); };

/* The live record of a question: lines appear as the server reports each real step. */
function makeActivity() {
  const steps = el("ol", { class: "steps" });
  const label = el("span", {}, "Working");
  const summary = el("summary", {}, icon("chevron", "chev"), label);
  const root = el("details", { class: "activity is-running", open: true }, summary, steps);
  summary.addEventListener("click", (e) => { if (root.classList.contains("is-running")) e.preventDefault(); });
  let active = null, timer = null;
  const glyph = (kind) => (kind === "active" ? el("span", { class: "spinner" }) : icon(kind === "warn" ? "alert" : kind === "fail" ? "cross" : "check"));
  const line = (kind, text, time = "", ...more) => {
    const row = el("li", { class: "step", "data-state": kind }, el("span", { class: "step-glyph" }, glyph(kind)), el("span", { class: "step-text" }, text), el("span", { class: "step-time" }, time), ...more);
    steps.append(row);
    say(text);
    follow(row);
    return row;
  };
  const stopTimer = () => { clearInterval(timer); timer = null; };
  return {
    root,
    start(text, ...more) {
      this.finish();
      const row = line("active", text, "0.0 s", ...more);
      const t0 = performance.now();
      const time = row.querySelector(".step-time");
      timer = setInterval(() => { time.textContent = `${((performance.now() - t0) / 1000).toFixed(1)} s`; }, 100);
      active = row;
    },
    finish(kind, text, time, ...more) {
      stopTimer();
      if (active) active.remove();
      active = null;
      if (kind) line(kind, text, time, ...more);
    },
    line,
    done(text) {
      stopTimer();
      if (active) { active.remove(); active = null; }
      root.classList.remove("is-running");
      label.textContent = text;
      root.open = false;
    },
  };
}
function follow(node) {
  const thread = $("thread");
  const below = node.offsetTop + node.offsetHeight - thread.scrollTop - thread.clientHeight;
  if (below > 0) thread.scrollTop += below + 16;
}

function chipsFor(rows) {
  const pages = uniquePages(rows);
  const kids = pages.slice(0, 5).map((p) => el("button", {
    type: "button", class: "chip", onclick: () => openPage({ doc: p.doc_id, n: p.page, mentions: pages }),
  }, `${shortName(p.title)} p.${p.page}`));
  if (pages.length > 5) kids.push(el("span", { class: "chip chip-count" }, `+${pages.length - 5} more`));
  return el("div", { class: "step-chips" }, kids);
}
function searchText(s) {
  const nameEditions = s.editions && s.editions.length;
  if (nameEditions) return `Searched ${s.editions.map(editionName).join(" and ")}, which your question names`;
  const n = s.only ? s.only.length : state.docs.length;
  return `Searched ${plural(n, "report")}`;
}
/* One step of the pipeline, whether it is arriving live or being replayed from a stored trace. */
function showStep(a, s, retrieved) {
  if (s.kind === "search") {
    const rows = retrieved || s.passages || [];
    a.line(s.found ? "done" : "warn", s.found ? `${searchText(s)}, found ${plural(s.found, "passage")}` : `${searchText(s)}, nothing matched`, seconds(s.ms), s.found ? chipsFor(rows) : null, retrieved ? passageList(retrieved) : null);
  } else if (s.kind === "drafting") {
    a.start(isDemo() ? "Picking the closest sentence" : `Reading the passages and drafting${s.attempt > 1 ? " again" : ""}`, isDemo() ? null : el("span", { class: "step-more" }, "Usually about ten seconds"));
  } else if (s.kind === "draft") {
    const what = isDemo() ? "Picked" : "Drafted";
    a.finish(s.error ? "fail" : "done", s.error ? "The AI model did not answer" : `${what} ${plural(s.claims, "statement")}`, seconds(s.ms), s.error ? el("span", { class: "step-more" }, s.error) : null);
  } else if (s.kind === "checking") {
    a.start("Checking every quote against its page");
  } else if (s.kind === "check") {
    const ok = !s.removed;
    a.finish(ok ? "done" : "warn", `Checked: ${s.kept} kept, ${s.removed} removed`, seconds(s.ms), s.reasons && s.reasons.length ? el("span", { class: "step-more" }, s.reasons.join("; ")) : null);
  } else if (s.kind === "retry") {
    a.line("warn", "First draft failed the check, trying again", "");
  }
}
function passageList(rows) {
  return el("details", { class: "passages" }, el("summary", {}, `Show the ${plural(rows.length, "passage")}`),
    rows.map((p) => el("div", { class: "passage" },
      el("div", { class: "passage-top" }, el("span", {}, `${shortName(p.title)}, page ${p.page}`), el("button", { type: "button", class: "chip", onclick: () => openPage({ doc: p.doc_id, n: p.page, mentions: rows }) }, "Open page")),
      el("p", {}, reflow(p.text)))));
}
function summaryOf(j) {
  const total = (j.trace || []).reduce((sum, s) => sum + (s.ms || 0), 0);
  const search = (j.trace || []).find((s) => s.kind === "search");
  const found = search ? search.found : j.retrieved.length;
  return [`Checked in ${seconds(total)}`, plural(found, "passage"), `${plural(j.claims.length, "quote")} kept`].join(" · ");
}

/* The answer */
function revealWords(node, text) {
  if (REDUCED) { node.textContent = text; return; }
  const words = text.split(" ");
  node.replaceChildren(...words.flatMap((w, i) => {
    const s = el("span", { class: "word" }, w);
    s.style.setProperty("--i", String(Math.min(i, 40)));
    return [s, " "];
  }));
  setTimeout(() => { node.textContent = text; }, 40 * 14 + 400);
}
function citeButton(c, retrieved) {
  const label = `${shortName(c.title)} · p.${c.page}`;
  const b = el("button", {
    type: "button", class: "cite", "aria-pressed": "false", "aria-label": `Open ${c.title}, page ${c.page}, with the quote marked`,
    onclick: () => { hidePeek(); openPage({ doc: c.doc_id, n: c.page, quote: c.quote, anchor: b, mentions: retrieved }); },
  }, icon("check"), label);
  const show = () => showPeek(b, c);
  b.addEventListener("pointerenter", show);
  b.addEventListener("focus", show);
  b.addEventListener("pointerleave", hidePeek);
  b.addEventListener("blur", hidePeek);
  return b;
}
function renderAnswer(host, j, q, animate) {
  host.replaceChildren();
  if (j.status === "answered") {
    const first = [];
    const claims = j.claims.map((c, i) => {
      const text = el("span", { class: "claim-text" }, c.text);
      const cite = citeButton(c, j.retrieved);
      first.push(cite);
      if (animate) setTimeout(() => revealWords(text, c.text), i * 180);
      return el("p", { class: "claim" }, text, " ", cite);
    });
    host.append(el("div", { class: "answer" }, claims),
      el("p", { class: "caveat" }, "Each quote was found on the page it cites. That confirms the source, not that the claim answers your question."),
      answerActions(j, q));
    say("Answered with " + plural(j.claims.length, "checked claim"));
    if (animate && first[0]) first[0].click();
    return;
  }
  const scoped = (j.trace || []).find((s) => s.kind === "search")?.only;
  const search = (j.trace || []).find((s) => s.kind === "search");
  const down = (j.trace || []).find((s) => s.kind === "draft" && s.error);
  const why = down ? "The AI model did not answer, so nothing could be checked. Ask again in a moment."
    : !j.retrieved.length ? "No passage matched the words in your question, so nothing was drafted."
      : `Related pages were found, but no quote passed the check${j.attempts > 1 ? " after two tries" : ""}, so there is no guess to show you.`;
  const pages = uniquePages(j.closest);
  host.append(
    el("h3", { class: "none-title" }, scoped && scoped.length < state.docs.length ? `No answer in these ${plural(scoped.length, "report")}` : "No answer in your reports"),
    el("p", { class: "none-why" }, why),
    pages.length ? el("div", { class: "none-chips" }, el("span", { class: "actions-note" }, "Closest pages:"), pages.map((p) => el("button", { type: "button", class: "chip", onclick: () => openPage({ doc: p.doc_id, n: p.page, mentions: j.retrieved }) }, `${shortName(p.title)} p.${p.page}`))) : null,
    answerActions(j, q, Boolean(scoped && search && scoped.length < state.docs.length)));
  say("No answer found");
  if (animate && pages[0]) openPage({ doc: pages[0].doc_id, n: pages[0].page, mentions: j.retrieved });
}
function answerActions(j, q, canWiden) {
  const row = el("div", { class: "actions" });
  if (j.status === "answered") {
    row.append(el("button", { type: "button", class: "ghost", onclick: () => askAcross(q) }, icon("table"), `Ask across all ${plural(state.docs.length, "report")}`));
    const copy = el("button", {
      type: "button", class: "ghost",
      onclick: async () => {
        await navigator.clipboard.writeText(j.claims.map((c) => `${c.text} (${c.title}, p. ${c.page})`).join("\n"));
        copy.replaceChildren(icon("check"), "Copied");
        setTimeout(() => copy.replaceChildren(icon("copy"), "Copy with source"), 1600);
      },
    }, icon("copy"), "Copy with source");
    row.append(copy);
  } else {
    if (canWiden) row.append(el("button", { type: "button", class: "ghost", onclick: () => { state.ticked = new Set(state.docs.map((d) => d.id)); saveTicks(); renderReports(); renderScope(); ask(q); } }, `Search all ${plural(state.docs.length, "report")}`));
    row.append(el("button", { type: "button", class: "ghost", onclick: () => { setMode("find"); $("q").value = q; $("q").focus(); } }, "Find the exact words"));
  }
  const fb = el("span", { class: "actions-note" });
  const up = el("button", { type: "button", class: "icon-btn", "aria-label": "Helpful", title: "Helpful", onclick: () => rate("up", "") }, icon("thumb"));
  const down = el("button", {
    type: "button", class: "icon-btn", "aria-label": "Not helpful", title: "Not helpful",
    onclick: () => {
      const input = el("input", { placeholder: "What was wrong? (optional)", maxLength: 300, "aria-label": "What was wrong?" });
      const form = el("form", { class: "inline-form", onsubmit: (e) => { e.preventDefault(); form.remove(); rate("down", input.value); } }, input, el("button", { class: "ghost" }, "Send"));
      row.append(form);
      input.focus();
    },
  }, icon("thumb", "flipped"));
  const rate = async (rating, reason) => {
    try {
      await post("/api/feedback", { user: state.user, audit_id: j.audit_id, rating, reason });
      up.hidden = down.hidden = true;
      fb.textContent = "Thanks, noted.";
    } catch (e) { fb.textContent = e.message; }
  };
  const flag = el("button", {
    type: "button", class: "ghost push",
    onclick: () => {
      flag.hidden = true;
      const input = el("input", { placeholder: "Why should someone look at this? (optional)", maxLength: 500, "aria-label": "Why should someone look at this?" });
      const form = el("form", {
        class: "inline-form",
        onsubmit: async (e) => {
          e.preventDefault();
          try {
            const r = await post("/api/review", { user: state.user, audit_id: j.audit_id, note: input.value });
            form.replaceWith(el("p", { class: "actions-note" }, `Review request #${r.id} recorded. Nobody is notified automatically; a person picks it up from the list.`));
            loadHistory();
          } catch (err) { form.replaceWith(el("p", { class: "turn-error" }, err.message)); }
        },
      }, input, el("button", { class: "primary" }, "Send for review"));
      row.append(form);
      input.focus();
    },
  }, icon("flag"), "Ask someone to review");
  row.append(up, down, fb, flag);
  return row;
}

/* Asking */
const GO_LABELS = { ask: "Ask", table: "Build the table", find: "Find" };
function drawGo() {
  const stop = Boolean(state.running), label = stop ? "Stop" : GO_LABELS[state.mode];
  const go = $("go");
  go.replaceChildren(icon(stop ? "stop" : "send"));
  go.setAttribute("aria-label", label);
  go.title = label;
}
function setRunning(stop) {
  state.running = stop;
  drawGo();
}
function stopRunning() { if (state.running) state.running(); }
function turnFor(q, meta) {
  const turn = el("article", { class: "turn" }, meta ? el("p", { class: "turn-meta" }, meta) : null, el("h2", { class: "turn-q" }, q));
  $("thread").append(turn);
  return turn;
}
async function ask(q) {
  if (state.running) return;
  if (!state.ticked.size) { composerNote("Tick at least one report to search."); return; }
  composerNote("");
  $("q").value = "";
  refreshSuggestions();
  let turn, a, result;
  await leaveEmpty(() => {
    turn = turnFor(q);
    a = makeActivity();
    result = el("div", {});
    turn.append(a.root, result);
    showInThread(turn);
  });
  clearProof();
  const controller = new AbortController();
  let stopped = false;
  setRunning(() => { stopped = true; controller.abort(); });
  const doc_ids = state.ticked.size === state.docs.length ? null : [...state.ticked];
  // A fast step still gets a moment on screen, so the record can be read. The times shown are the real ones.
  const gap = REDUCED ? 0 : 280;
  try {
    let answered = false;
    for await (const event of events("/api/ask/stream", { user: state.user, question: q, doc_ids }, controller.signal)) {
      if (event.kind === "answer") {
        a.done(summaryOf(event.answer));
        renderAnswer(result, event.answer, q, true);
        answered = true;
      } else if (event.kind === "error") {
        throw new Error(event.detail);
      } else {
        showStep(a, event);
        await sleep(gap);
      }
    }
    if (!answered) throw new Error("The connection closed before the answer arrived.");
    loadHistory();
  } catch (e) {
    a.finish();
    a.root.classList.remove("is-running");
    a.root.querySelector("summary span").textContent = stopped ? "Stopped" : "Did not finish";
    if (stopped) {
      a.line("warn", "Stopped. Any AI call already started finishes on the server, and its answer stays in your history.", "");
    } else {
      a.line("fail", "That did not finish", "");
      result.replaceChildren(el("p", { class: "turn-error" }, e.message), el("div", { class: "actions" }, el("button", { type: "button", class: "ghost", onclick: () => { turn.remove(); ask(q); } }, "Try again")));
    }
    loadHistory();
  } finally {
    setRunning(null);
  }
}
async function replay(id) {
  stopRunning();
  try {
    const j = await api(`/api/answer/${id}?user=${encodeURIComponent(state.user)}`);
    await leaveEmpty(() => {
      const turn = turnFor(j.question, `Saved answer · ${fullDay(j.at)} · no new AI call`);
      const a = makeActivity();
      const result = el("div", {});
      turn.append(a.root, result);
      for (const s of j.trace) showStep(a, s, s.kind === "search" ? j.retrieved : undefined);
      a.done(summaryOf(j));
      renderAnswer(result, j, j.question, false);
      showInThread(turn);
      const first = j.claims[0];
      if (first) result.querySelector(".cite").click();
      else if (j.closest[0]) openPage({ doc: j.closest[0].doc_id, n: j.closest[0].page, mentions: j.retrieved });
    });
  } catch (e) { composerNote(e.message); }
}

/* Find exact words: plain search, no AI */
async function findWords(q) {
  if (!state.ticked.size) { composerNote("Tick at least one report to search."); return; }
  composerNote("");
  let result;
  await leaveEmpty(() => {
    const turn = turnFor(`Find: ${q}`);
    result = el("div", {}, el("p", { class: "turn-meta" }, "Searching…"));
    turn.append(result);
    showInThread(turn);
  });
  try {
    const j = await api("/api/find?" + new URLSearchParams({ user: state.user, q, limit: 200 }));
    const hits = j.hits.filter((h) => state.ticked.has(h.doc_id));
    const head = el("p", { class: "turn-meta" }, hits.length ? `${plural(hits.length, "page")} found · ${seconds(j.ms)} · no AI used` : `Nothing found · ${seconds(j.ms)} · no AI used`);
    if (!hits.length) {
      result.replaceChildren(head, el("p", { class: "none-why" }, "No ticked report has all of those words. Try fewer words, OR between alternatives, or a phrase in quotes."));
      return;
    }
    result.replaceChildren(head, ...hits.map((h) => {
      const terms = [...new Set(h.snippet.filter(([, m]) => m).map(([t]) => t))];
      return el("button", { type: "button", class: "hit", onclick: () => openPage({ doc: h.doc_id, n: h.page, terms, mentions: hits.filter((x) => x.doc_id === h.doc_id) }) },
        el("div", { class: "hit-where" }, `${shortName(h.title)}, page ${h.page}`),
        el("p", { class: "hit-text" }, h.snippet.map(([t, m]) => (m ? el("mark", {}, t) : t))));
    }));
    say(plural(hits.length, "page") + " found");
  } catch (e) { result.replaceChildren(el("p", { class: "turn-error" }, e.message)); }
}

/* Tables: one question, every report */
async function askAcross(q, doc_ids = state.docs.map((d) => d.id)) {
  try {
    const view = await post("/api/me/analyses", { user: state.user, name: q.slice(0, 80), questions: [q], doc_ids });
    await leaveEmpty(() => showTable(view));
    startTable(view.id);
    loadTables();
  } catch (e) { composerNote(e.message); }
}
async function openTable(id) {
  const open = state.tableNodes.get(id);
  if (open) { showInThread(open.node); return; }
  try {
    const view = await api(`/api/me/analyses/${id}?user=${encodeURIComponent(state.user)}`);
    await leaveEmpty(() => { showTable(view); });
    if (view.status === "running") pollTable(id);
  } catch (e) { composerNote(e.message); }
}
async function startTable(id) {
  const entry = state.tableNodes.get(id);
  try {
    await post(`/api/me/analyses/${id}/run?user=${encodeURIComponent(state.user)}`);
    pollTable(id);
  } catch (e) { if (entry) entry.state.textContent = e.message; }
}
async function pollTable(id) {
  const entry = state.tableNodes.get(id);
  if (!entry) return;
  try {
    const view = await api(`/api/me/analyses/${id}?user=${encodeURIComponent(state.user)}`);
    renderTable(entry, view);
    if (view.status === "running" || view.status === "new") entry.timer = setTimeout(() => pollTable(id), 900);
    else loadTables();
  } catch (e) { entry.state.textContent = e.message; }
}
function showTable(view) {
  const state_ = el("span", { class: "table-state" });
  const scroll = el("div", { class: "table-scroll" });
  const input = el("input", { placeholder: "Add another question to this table", maxLength: 300, "aria-label": "Add another question to this table" });
  const add = el("form", {
    class: "table-add",
    onsubmit: async (e) => {
      e.preventDefault();
      const question = input.value.trim();
      if (!question) return;
      try {
        const next = await post(`/api/me/analyses/${view.id}/questions`, { user: state.user, question });
        input.value = "";
        renderTable(entry, next);
        startTable(view.id);
      } catch (err) { state_.textContent = err.message; }
    },
  }, input, el("button", { class: "ghost" }, "Add row"));
  const remove = el("button", {
    type: "button", class: "icon-btn", "aria-label": "Delete this table", title: "Delete this table",
    onclick: async () => {
      if (!await confirmDelete(`Delete the table “${view.name}”?`, "The table leaves your saved tables. This cannot be undone.")) return;
      try {
        await send("DELETE", `/api/me/analyses/${view.id}?user=${encodeURIComponent(state.user)}`);
      } catch (err) { state_.textContent = err.message; return; }
      clearTimeout(entry.timer);
      state.tableNodes.delete(view.id);
      node.remove();
      loadTables();
    },
  }, icon("trash"));
  const node = el("article", { class: "turn" }, el("div", { class: "table-head" }, icon("table"), el("span", { class: "table-name" }, view.name), state_, remove), scroll, add);
  const entry = { node, scroll, state: state_, timer: null };
  state.tableNodes.set(view.id, entry);
  const thread = $("thread");
  thread.append(node);
  renderTable(entry, view);
  showInThread(node);
}
function figureFirst(text) {
  // The figure is what a table is read for, so it stands out; the words stay exactly as checked.
  const m = text.match(/-?\d+(?:\.\d+)?\s*(?:percent|%|billion|million)/i);
  if (!m) return [text];
  return [text.slice(0, m.index), el("strong", {}, m[0]), text.slice(m.index + m[0].length)];
}
function renderTable(entry, view) {
  const running = view.status === "running";
  let nextUp = running;
  entry.state.textContent = running || view.status === "new"
    ? `Asking ${view.done + 1} of ${view.total} · one AI call per report`
    : `${view.answered} of ${view.total} answered · select a cell to see its page`;
  const head = el("tr", {}, el("th", { scope: "col" }, "Question"), view.docs.map((d) => el("th", { scope: "col" }, d.short)));
  const rows = view.questions.map((q, qi) => el("tr", {}, el("th", { scope: "row" }, q), view.docs.map((d) => {
    const c = view.cells[`${qi}:${d.id}`];
    if (c && c.status === "answered") {
      const b = el("button", {
        type: "button", class: "cell", title: c.claim,
        onclick: () => openPage({ doc: d.id, n: c.page, quote: c.quote, anchor: b }),
      }, el("span", { class: "cell-claim" }, figureFirst(c.claim)), el("span", { class: "cell-page" }, `Page ${c.page}`));
      return el("td", {}, b);
    }
    if (c && c.status === "not_found") return el("td", {}, el("span", { class: "cell is-none" }, "Not in this report"));
    if (c && c.status === "error") return el("td", {}, el("span", { class: "cell is-none" }, "Could not run"));
    const current = nextUp;
    nextUp = false;
    return el("td", {}, el("span", { class: "cell is-wait" + (current ? " is-running" : "") }, current ? "Asking…" : "Waiting"));
  })));
  entry.scroll.replaceChildren(el("table", { class: "matrix" }, el("thead", {}, head), el("tbody", {}, rows)));
}

/* The page */
function reflow(text) {
  // Extracted lines keep the PDF's line breaks. Long lines are one paragraph; short ones (headings) stay apart, and the
  // numbers read off a chart run together rather than filling a column. Only whitespace changes, so quotes still match.
  const isChartValue = (line) => /^[-–\d.,%()]+[a-z]?$/.test(line) || /^Q\d-\d{4}$/.test(line);
  return text.split("\n").reduce((out, raw, i, all) => {
    const line = raw.trim();
    if (!line) return out;
    const before = (all[i - 1] || "").trim();
    const joins = (before.length > 38 && !/^(FIGURE|TABLE|BOX)\b/.test(line)) || (isChartValue(before) && isChartValue(line));
    return out + (joins ? " " : "\n") + line;
  }, "").trim();
}
const escapeRe = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
function markedText(text, pattern, cls) {
  if (!pattern) return [text];
  const out = [];
  let at = 0;
  for (const m of text.matchAll(pattern)) {
    if (!m[0]) continue;
    out.push(text.slice(at, m.index), el("mark", { class: cls || "" }, m[0]));
    at = m.index + m[0].length;
  }
  out.push(text.slice(at));
  return out;
}
async function openPage({ doc, n, quote = "", terms = [], anchor = null, mentions }) {
  const d = docById(doc);
  const seq = ++state.page.seq;
  showPane(true);
  if (mentions !== undefined || state.page.doc !== doc) setMentions(doc, mentions || [], mentions ? "Pages found for this question" : "");
  state.page.doc = doc;
  state.page.n = n;
  markOpenReport();
  clearProof();
  document.querySelectorAll(".cell.is-active").forEach((x) => x.classList.remove("is-active"));
  document.querySelectorAll(".claim.is-active").forEach((x) => x.classList.remove("is-active"));
  document.querySelectorAll('.cite[aria-pressed="true"]').forEach((x) => x.setAttribute("aria-pressed", "false"));
  if (anchor) {
    if (anchor.classList.contains("cell")) anchor.classList.add("is-active");
    else { anchor.setAttribute("aria-pressed", "true"); anchor.closest(".claim")?.classList.add("is-active"); }
  }
  $("phead").textContent = d ? d.title : "Report";
  $("pat").textContent = d ? `${n} / ${d.pages}` : String(n);
  $("pprev").disabled = n <= 1;
  $("pnext").disabled = Boolean(d) && n >= d.pages;
  $("sheet").classList.add("is-loading");
  drawMentions();
  try {
    const j = await api("/api/page?" + new URLSearchParams({ user: state.user, doc_id: doc, p: n, quote }));
    if (seq !== state.page.seq) return;
    const pdf = $("ppdf");
    pdf.hidden = !j.pdf;
    if (j.pdf) pdf.href = j.pdf;
    const text = reflow(j.text);
    let pattern = null;
    if (quote) pattern = new RegExp(quote.trim().split(/\s+/).map(escapeRe).join("\\s+"), "gi");
    else if (terms.length) pattern = new RegExp(terms.map((t) => t.trim().split(/\s+/).map(escapeRe).join("\\s+")).join("|"), "gi");
    $("ptext").replaceChildren(...(text ? markedText(text, pattern, quote ? "is-proof" : "") : ["This page has no text to show. It may be a chart or a scanned image."]));
    const mark = $("ptext").querySelector("mark");
    const missed = quote && !mark;
    $("pnote").textContent = `Extracted text of page ${n}${quote && !missed ? ", with the quote marked" : ""}.${missed ? " The check found the quote here, but its spacing stops it being marked in this view." : ""}${j.pdf ? "" : " The original PDF is not on this server."}`;
    $("sheet").classList.remove("is-loading");
    const wrap = $("sheetWrap");
    if (mark) {
      wrap.scrollTop = Math.max(0, mark.offsetTop - wrap.clientHeight / 3);
      if (quote && anchor) setProof(anchor, mark);
    } else wrap.scrollTop = 0;
    drawMentions();
  } catch (e) {
    if (seq !== state.page.seq) return;
    $("ptext").replaceChildren(e.message);
    $("pnote").textContent = "";
    $("sheet").classList.remove("is-loading");
  }
}
function showPane(on) {
  const desk = $("desk");
  desk.classList.toggle("no-pane", !on);
  desk.classList.toggle("pane-up", on);
  $("showPane").hidden = on;
  if (!on) clearProof();
}
$("pane").addEventListener("animationend", () => queueProof()); // the page slides in, so its marks settle after the line is drawn
$("pclose").onclick = () => showPane(false);
$("showPane").onclick = () => showPane(true);
$("pprev").onclick = () => turnPage(-1);
$("pnext").onclick = () => turnPage(1);
function turnPage(by) {
  const d = docById(state.page.doc);
  if (!d) return;
  const n = Math.min(d.pages, Math.max(1, state.page.n + by));
  if (n !== state.page.n) openPage({ doc: d.id, n, terms: state.page.terms || [] });
}

/* Jump marks across the report */
let mentionPages = [], mentionDoc = null;
function setMentions(doc, rows, note) {
  mentionDoc = doc;
  mentionPages = [...new Set(rows.filter((r) => r.doc_id === doc).map((r) => r.page))].sort((a, b) => a - b);
  $("mentionsNote").textContent = mentionPages.length && note ? `${note}: ${plural(mentionPages.length, "page")}` : "";
}
function drawMentions() {
  const d = docById(state.page.doc);
  const bar = $("mentions");
  if (!d) { bar.replaceChildren(); return; }
  const at = (p) => `${((p - 0.5) / d.pages) * 100}%`;
  const here = el("span", { class: "here" });
  here.style.setProperty("--at", at(state.page.n));
  const ticks = mentionDoc === d.id ? mentionPages.map((p) => {
    const b = el("button", {
      type: "button", class: "tick", "aria-label": `Go to page ${p}`, title: `Page ${p}`,
      onclick: () => openPage({ doc: d.id, n: p, terms: state.page.terms || [] }),
      onkeydown: (e) => {
        if (e.key !== "ArrowRight" && e.key !== "ArrowLeft") return;
        e.preventDefault();
        const all = [...bar.querySelectorAll(".tick")];
        all[(all.indexOf(e.target) + (e.key === "ArrowRight" ? 1 : -1) + all.length) % all.length]?.focus();
      },
    });
    b.style.setProperty("--at", at(p));
    return b;
  }) : [];
  const focused = document.activeElement?.title;
  bar.replaceChildren(here, ...ticks);
  if (focused) bar.querySelector(`.tick[title="${focused}"]`)?.focus();
}
let findTimer = null;
async function findInReport(jump) {
  const q = $("pfind").value.trim();
  const doc = state.page.doc;
  if (!doc) return;
  if (!q) { state.page.terms = []; setMentions(doc, [], ""); drawMentions(); return; }
  try {
    const j = await api("/api/find?" + new URLSearchParams({ user: state.user, q, limit: 200, doc_id: doc }));
    const hits = j.hits.filter((h) => h.doc_id === doc);
    const terms = [...new Set(hits.flatMap((h) => h.snippet.filter(([, m]) => m).map(([t]) => t)))];
    state.page.terms = terms;
    setMentions(doc, hits, "");
    $("mentionsNote").textContent = hits.length ? `${plural(hits.length, "page")} with those words` : "No page in this report has those words";
    drawMentions();
    if (jump && hits.length) {
      const pages = hits.map((h) => h.page).sort((a, b) => a - b);
      openPage({ doc, n: pages.find((p) => p > state.page.n) || pages[0], terms });
    } else if (hits.length) openPage({ doc, n: state.page.n, terms });
  } catch (e) { $("mentionsNote").textContent = e.message; }
}
$("pfind").oninput = () => { clearTimeout(findTimer); findTimer = setTimeout(() => findInReport(false), 300); };
$("pfindForm").onsubmit = (e) => { e.preventDefault(); clearTimeout(findTimer); findInReport(true); };

/* The proof line links a claim to its highlighted words. It is a gesture, not a fixture: it draws when a citation is selected
   or when either end is hovered, then fades, and scrolling dismisses it. The yellow on both ends is what stays. */
const proofNode = $("proof");
const PROOF_LINGERS = 1800;
const PROOF_SETTLES = 200; // opening a page scrolls it to the quote, and that scroll must not dismiss the line it just drew
let proofFade = null, proofShownAt = 0;
function setProof(from, to) {
  state.proof = { from, to };
  showProof();
  proofFade = setTimeout(hideProof, PROOF_LINGERS);
}
function showProof() {
  clearTimeout(proofFade);
  if (!drawProof()) { hideProof(); return; }
  proofShownAt = performance.now();
  proofNode.classList.add("is-on");
  proofNode.classList.remove("is-drawing");
  void proofNode.getBoundingClientRect();
  proofNode.classList.add("is-drawing");
}
function hideProof() {
  clearTimeout(proofFade);
  proofNode.classList.remove("is-on", "is-drawing");
}
function clearProof() {
  state.proof = null;
  hideProof();
}
function drawProof() {
  const p = state.proof;
  if (!p || !p.from.isConnected || !p.to.isConnected) return false;
  const a = p.from.getBoundingClientRect();
  const m = p.to.getClientRects()[0];
  if (!m) return false;
  const thread = $("thread").getBoundingClientRect();
  const sheet = $("sheetWrap").getBoundingClientRect();
  const y1 = a.top + a.height / 2, y2 = m.top + m.height / 2;
  // With an end out of view there is nothing to connect, so there is no line.
  if (y1 < thread.top || y1 > thread.bottom || a.right > thread.right || y2 < sheet.top || y2 > sheet.bottom) return false;
  // Straight to the panel's edge, then it bends only in the gap between the panels, ending in the page's margin: it never crosses text.
  const x1 = a.right + 3, edge = thread.right, x2 = Math.max($("ptext").getBoundingClientRect().left - 8, sheet.left);
  const bend = Math.max(12, (x2 - edge) * 0.5);
  $("proofPath").setAttribute("d", `M${x1} ${y1} L${edge} ${y1} C${edge + bend} ${y1} ${x2 - bend} ${y2} ${x2} ${y2}`);
  $("proofA").setAttribute("cx", x1); $("proofA").setAttribute("cy", y1);
  $("proofB").setAttribute("cx", x2); $("proofB").setAttribute("cy", y2);
  return true;
}
const proofIsOn = () => proofNode.classList.contains("is-on");
let proofFrame = 0;
const queueProof = () => {
  cancelAnimationFrame(proofFrame);
  proofFrame = requestAnimationFrame(() => { if (proofIsOn() && !drawProof()) hideProof(); });
};
function proofOnScroll() {
  if (performance.now() - proofShownAt < PROOF_SETTLES) queueProof(); else if (proofIsOn()) hideProof();
}
$("thread").addEventListener("scroll", proofOnScroll, { passive: true });
$("sheetWrap").addEventListener("scroll", proofOnScroll, { passive: true });
addEventListener("resize", queueProof);
const isProofEnd = (node) => Boolean(state.proof && node && node.closest && (state.proof.from.contains(node) || node.closest("mark.is-proof") === state.proof.to));
document.addEventListener("pointerover", (e) => {
  if (!isProofEnd(e.target)) return;
  if (proofIsOn()) clearTimeout(proofFade); else showProof();
});
document.addEventListener("pointerout", (e) => {
  if (isProofEnd(e.target) && !isProofEnd(e.relatedTarget)) { clearTimeout(proofFade); proofFade = setTimeout(hideProof, 300); }
});

/* Hover preview of a citation */
let peekTimer = null;
function showPeek(anchor, c) {
  clearTimeout(peekTimer);
  peekTimer = setTimeout(() => {
    const peek = $("peek");
    peek.replaceChildren(
      el("div", { class: "peek-where" }, `${c.title}, page ${c.page}`),
      el("p", { class: "peek-quote" }, el("mark", {}, c.quote)),
      el("div", { class: "peek-ok" }, icon("check"), "Quote found on this page"));
    peek.hidden = false;
    const a = anchor.getBoundingClientRect(), box = peek.getBoundingClientRect();
    peek.style.left = `${Math.min(innerWidth - box.width - 8, Math.max(8, a.left))}px`;
    peek.style.top = `${a.top - box.height - 6 > 8 ? a.top - box.height - 6 : a.bottom + 6}px`;
  }, 120);
}
function hidePeek() { clearTimeout(peekTimer); $("peek").hidden = true; }

/* Selecting words on the page */
function placeSelectionButton() {
  const btn = $("askSel");
  const sel = getSelection();
  const text = sel && !sel.isCollapsed ? sel.toString().trim() : "";
  if (text.length < 4 || !$("ptext").contains(sel.anchorNode)) { btn.hidden = true; return; }
  const box = sel.getRangeAt(0).getBoundingClientRect();
  btn.hidden = false;
  btn.style.left = `${box.left}px`;
  btn.style.top = `${Math.max(8, box.top - btn.offsetHeight - 6)}px`;
  btn.onclick = () => {
    const d = docById(state.page.doc);
    const name = d ? d.edition[2] || d.title : "this";
    prefill(`In the ${name} report, what does this mean: "${text.replace(/\s+/g, " ").slice(0, 400)}"?`);
    btn.hidden = true;
  };
}
document.addEventListener("selectionchange", () => { clearTimeout(placeSelectionButton.t); placeSelectionButton.t = setTimeout(placeSelectionButton, 120); });

/* Panels can be dragged wider or narrower, or moved with the arrow keys */
function resizer(id, varName, fromRight, min, max) {
  const bar = $(id), desk = $("desk");
  const saved = stored(varName);
  if (saved) desk.style.setProperty(varName, saved);
  const set = (px) => {
    const room = desk.clientWidth;
    const clamped = Math.round(Math.min(max(room), Math.max(min, px)));
    desk.style.setProperty(varName, clamped + "px");
    keep(varName, clamped + "px");
    bar.setAttribute("aria-valuenow", String(clamped));
    queueProof();
  };
  bar.addEventListener("pointerdown", (e) => {
    bar.setPointerCapture(e.pointerId);
    bar.classList.add("is-dragging");
    document.body.classList.add("is-resizing");
  });
  bar.addEventListener("pointermove", (e) => {
    if (bar.hasPointerCapture(e.pointerId)) set(fromRight ? innerWidth - e.clientX : e.clientX);
  });
  const end = () => { bar.classList.remove("is-dragging"); document.body.classList.remove("is-resizing"); };
  bar.addEventListener("pointerup", end);
  bar.addEventListener("pointercancel", end);
  bar.addEventListener("keydown", (e) => {
    if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
    e.preventDefault();
    const now = (fromRight ? $("pane") : $("rail")).getBoundingClientRect().width;
    set(now + ((e.key === "ArrowRight") === fromRight ? -16 : 16));
  });
}
resizer("splitRail", "--rail-w", false, 208, () => 360);
resizer("splitPane", "--pane-w", true, 320, (room) => room - 620);

/* Composer */
const MODES = {
  ask: { label: "Answer", placeholder: "Ask about your reports" },
  table: { label: "Table across reports", placeholder: "One question to run in every report you have chosen" },
  find: { label: "Exact words", placeholder: 'Words from a report. Put a phrase in "quotes"; OR widens; -word excludes.' },
};
function setMode(mode) {
  state.mode = mode;
  $("askMode").setAttribute("aria-pressed", String(mode === "ask"));
  $("tableMode").setAttribute("aria-pressed", String(mode === "table"));
  $("findBtn").setAttribute("aria-pressed", String(mode === "find"));
  $("modeLabel").textContent = MODES[mode].label;
  $("q").placeholder = MODES[mode].placeholder;
  drawGo();
}
const chooseMode = (mode) => () => { setMode(mode); closeMenu("modePop"); $("q").focus(); };
$("askMode").onclick = chooseMode("ask");
$("tableMode").onclick = chooseMode("table");
$("findBtn").onclick = chooseMode("find");
$("composer").onsubmit = async (e) => {
  e.preventDefault();
  if (state.running) { stopRunning(); return; }
  const q = $("q").value.trim();
  if (!q) { $("q").focus(); return; }
  document.querySelectorAll('.attachment:not([data-state="working"])').forEach((chip) => chip.remove());
  if (state.mode === "find") { findWords(q); return; }
  if (state.mode === "ask") { ask(q); return; }
  if (!state.ticked.size) { composerNote("Tick at least one report to search."); return; }
  composerNote("");
  $("q").value = "";
  refreshSuggestions();
  setMode("ask");
  await askAcross(q, [...state.ticked]);
};
$("q").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); $("composer").requestSubmit(); }
});

/* Keys */
document.addEventListener("keydown", (e) => {
  const typing = /^(INPUT|TEXTAREA|SELECT)$/.test(e.target.tagName);
  if (e.key === "Escape") { clearProof(); hidePeek(); $("askSel").hidden = true; document.querySelectorAll(".claim.is-active").forEach((x) => x.classList.remove("is-active")); return; }
  if (typing || e.ctrlKey || e.metaKey || e.altKey) return;
  if (e.key === "/") { e.preventDefault(); $("q").focus(); }
  if (e.key === "[") turnPage(-1);
  if (e.key === "]") turnPage(1);
});

/* Theme */
function setTheme(theme) {
  document.documentElement.dataset.theme = theme;
  keep("theme", theme);
  $("themeBtn").textContent = theme === "dark" ? "Light mode" : "Dark mode";
}
$("themeBtn").onclick = () => setTheme(document.documentElement.dataset.theme === "dark" ? "light" : "dark");

/* Loading a user */
async function loadLibrary() {
  state.docs = (await api("/api/me/widget/library?user=" + encodeURIComponent(state.user))).library;
  return state.docs;
}
async function loadUser(user) {
  state.user = user;
  $("melink").href = "/dashboard?user=" + encodeURIComponent(user);
  $("reports").replaceChildren(...Array.from({ length: 5 }, () => el("li", { class: "skeleton-row" })));
  const library = await loadLibrary();
  $("plusUploadNote").textContent = `PDF, up to ${MAX_UPLOAD_MB} MB. Only ${user} can see it`;
  const saved = stored("ticks-" + user);
  const known = new Set(library.map((d) => d.id));
  const ticks = saved === null ? [...known] : saved.split(",").filter(Boolean).map(Number).filter((d) => known.has(d));
  state.ticked = new Set(ticks);
  renderReports();
  renderScope();
  renderSuggestions();
  $("stripUser").textContent = user.slice(0, 1).toUpperCase();
  await Promise.all([loadHistory(), loadTables()]);
  renderSaved();
}
async function firstPageWithText(doc) {
  // A report opens on its first page of real text, not a cover or a blank.
  for (let p = 1; p <= 8; p++) {
    try {
      const j = await api("/api/page?" + new URLSearchParams({ user: state.user, doc_id: doc, p }));
      if (j.text.length > 1200 && !j.text.includes("©")) return p;
    } catch { return 1; }
  }
  return 1;
}
$("user").onchange = async () => {
  stopRunning();
  const url = new URL(location.href);
  url.searchParams.set("user", $("user").value);
  url.searchParams.delete("a");
  location.href = url;
};

(async function start() {
  $("brandMark").append(icon("mark"));
  $("pprev").append(icon("left"));
  $("pnext").append(icon("right"));
  $("pclose").append(icon("cross"));
  $("showPane").append(icon("page"), "Show the page");
  $("railOpen").append(icon("menu"));
  $("railClose").append(icon("left"));
  $("stripHistory").append(icon("clock"));
  $("stripTables").append(icon("table"));
  $("plusBtn").append(icon("plus"));
  $("plusUpload").querySelector(".menu-title").prepend(icon("upload"));
  $("scopeBtn").append(icon("down"));
  $("modeBtn").append(icon("down"));
  setRail(stored("rail") === "open");
  setTheme(document.documentElement.dataset.theme);
  setMode("ask");
  const u = new URLSearchParams(location.search);
  try {
    const j = await api("/api/users");
    state.model = j.model;
    $("user").replaceChildren(...j.users.map((name) => el("option", { value: name }, name)));
    if (u.get("user") && j.users.includes(u.get("user"))) $("user").value = u.get("user");
    if (isDemo()) {
      const note = $("demoNote");
      note.hidden = false;
      note.textContent = "Demo mode: no AI model is connected, so answers quote the closest sentence and are often off-topic.";
    }
    await loadUser($("user").value);
  } catch (e) {
    $("thread").replaceChildren(el("p", { class: "turn-error" }, `The desk could not load: ${e.message}`));
    return;
  }
  if (u.get("q")) { $("q").value = u.get("q").slice(0, 500); refreshSuggestions(); }
  if (u.get("tab") === "find" || u.get("find") === "1") setMode("find");
  if (u.get("tab") === "table") setMode("table");
  if (u.get("doc")) openPage({ doc: Number(u.get("doc")), n: Number(u.get("p")) || 1, terms: u.get("term") ? [u.get("term")] : [] });
  if (u.get("table")) openTable(Number(u.get("table")));
  else if (u.get("a")) replay(u.get("a"));
  else if (u.get("find") === "1" && u.get("q")) findWords(u.get("q"));
  else if (u.get("run") === "1" && u.get("q")) ask(u.get("q"));
  else $("q").focus({ preventScroll: true });
})();
