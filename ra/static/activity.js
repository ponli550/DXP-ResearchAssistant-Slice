"use strict";
/* The workspace. One set of rows, one set of filters: every panel redraws from the same slice, so the numbers always
   agree. Nothing here builds HTML from text: questions and report words only ever reach the page through el(). */

const $ = (id) => document.getElementById(id);
const REDUCED = matchMedia("(prefers-reduced-motion: reduce)").matches;
const ICONS = {
  mark: ["M6 3h8.5L19 7.5V21H6z", "m9.5 14 2 2 4-4.5"],
  cross: ["M18 6 6 18", "M6 6l12 12"],
  check: ["M20 6 9 17l-5-5"],
  none: ["M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18", "M8 12h8"],
  alert: ["M12 4 2.5 20h19z", "M12 10v4", "M12 17h.01"],
  thumb: ["M7 10v11H3V10z", "m7 10 4-7a3 3 0 0 1 3 3v3h5a2 2 0 0 1 2 2.4l-1.5 7a2 2 0 0 1-2 1.6H7"],
  flag: ["M5 21V4h13l-2 4 2 4H5"],
  clock: ["M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18", "M12 7v5l3 2"],
  plus: ["M12 5v14", "M5 12h14"],
  minus: ["M5 12h14"],
  left: ["m15 6-6 6 6 6"],
  right: ["m9 6 6 6-6 6"],
  stop: ["M6 6h12v12H6z"],
  upload: ["M12 16V5", "m7 10 5-5 5 5", "M5 20h14"],
  up: ["m6 15 6-6 6 6"],
  down: ["m6 9 6 6 6-6"],
  page: ["M6 3h8.5L19 7.5V21H6z"],
  table: ["M3 5h18v14H3z", "M3 10h18", "M9 5v14", "M15 5v14"],
  search: ["M11 4a7 7 0 1 0 0 14 7 7 0 0 0 0-14", "m20 20-4-4"],
  spark: ["M12 3v5", "M12 16v5", "M3 12h5", "M16 12h5", "m6 6 3 3", "m15 15 3 3", "m18 6-3 3", "m9 15-3 3"],
  chat: ["M4 5h16v11H9l-5 4z"],
  play: ["M7 4v16l13-8z"],
  pin: ["M9 3h6l-1 6 4 4H6l4-4z", "M12 13v8"],
  print: ["M7 8V3h10v5", "M5 8h14a1 1 0 0 1 1 1v7h-3v5H7v-5H4V9a1 1 0 0 1 1-1z"],
  trash: ["M4 7h16", "M9 7V4h6v3", "M6 7l1 13h10l1-13"],
  fit: ["M4 9V4h5", "M20 9V4h-5", "M4 15v5h5", "M20 15v5h-5"],
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
    if (v === undefined || v === null) continue;
    if (k === "class") e.className = v;
    else if (k.startsWith("on") || k in e) e[k] = v;
    else e.setAttribute(k, v);
  }
  e.append(...kids.flat().filter((x) => x !== null && x !== undefined && x !== false));
  return e;
}

const USER = new URLSearchParams(location.search).get("user") || "";
const who = `user=${encodeURIComponent(USER)}`;
const toDesk = (extra = {}) => "/?" + new URLSearchParams({ user: USER, ...extra });
const here = (extra = {}) => "/dashboard?" + new URLSearchParams({ user: USER, ...extra });
const plural = (n, one, many = one + "s") => `${n.toLocaleString()} ${n === 1 ? one : many}`;
const dayLabel = (day) => new Date(day + "T00:00:00Z").toLocaleDateString(undefined, { month: "short", day: "numeric", timeZone: "UTC" });

async function api(path, opts) {
  const r = await fetch(path, opts);
  if (!r.ok) {
    const body = await r.json().catch(() => ({}));
    throw new Error(body.detail || `The server answered ${r.status}.`);
  }
  return r.json();
}
const send = (path, method, body) => api(path, { method, headers: { "content-type": "application/json" }, body: JSON.stringify({ user: USER, ...body }) });

let toastTimer;
function toast(text, target) {
  const t = $("toast");
  t.replaceChildren(text, target ? link(target.href, {}, target.label) : "");
  t.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { t.hidden = true; }, 5000);
}

function confirmDelete(title, body) {
  const d = $("confirm");
  $("confirmTitle").textContent = title;
  $("confirmBody").textContent = body;
  d.showModal();
  return new Promise((resolve) => { d.addEventListener("close", () => resolve(d.returnValue === "ok"), { once: true }); });
}

const OUTCOMES = {
  any: ["Any", () => true],
  answered: ["Answered", (r) => r.status === "answered"],
  none: ["No source found", (r) => r.status !== "answered"],
  review: ["In review", (r) => r.review === "open"],
  down: ["Rated down", (r) => r.rating === "down"],
};
const PERIODS = [["all", "All time"], ["30", "30 days"], ["7", "7 days"]];
const WORK_TABS = ["found", "questions", "tables", "mentions"];
const EXPLORE_TABS = ["topics", "pages", "history"];

const state = {
  rows: [], questions: [], lower: [], reports: [], topics: [], tables: [], findings: [], next: [], now: "", model: "", connected: false,
  filter: { period: "all", day: null, outcome: "any", report: null, topic: null },
  work: "found", explore: "topics", hits: null, hitsKey: "",
};
const report = (id) => state.reports.find((d) => d.id === id);
const topicByQuery = (query) => state.topics.find((t) => t.query === query) || null;
const currentTopic = () => topicByQuery(state.filter.topic);
const wordsOf = (term) => (term || "").toLowerCase().match(/[a-z0-9]+/g) || [];
const defaultWork = () => (state.findings.length ? "found" : "questions");

/* The slice. `f` is a filter, and `skip` names the parts a panel must ignore, such as the day chart keeping its other days on show. */
function rowsWhere(f, ...skip) {
  const from = !f.period || f.period === "all" ? "" : new Date(Date.parse(state.now) - Number(f.period) * 864e5).toISOString().slice(0, 10);
  const pass = OUTCOMES[f.outcome || "any"][1];
  const words = wordsOf(f.topic ? topicByQuery(f.topic)?.term || f.topic : "");
  return state.rows.filter((r) => {
    if (r.day < from) return false;
    if (f.day && !skip.includes("day") && r.day !== f.day) return false;
    if (!skip.includes("outcome") && !pass(r)) return false;
    if (f.report && !skip.includes("report") && !r.cited.some(([d]) => d === f.report)) return false;
    if (words.length && !skip.includes("topic") && !words.every((w) => state.lower[r.q].includes(w))) return false;
    return true;
  });
}
const rowsFor = (...skip) => rowsWhere(state.filter, ...skip);
function findingsWhere(f) {
  const allowed = new Set(rowsWhere({ ...f, outcome: "any" }, "report").map((r) => r.id));
  return state.findings.filter((x) => allowed.has(x.audit_id) && (!f.report || x.doc_id === f.report));
}

/* The router: every view is a URL, and moving between them keeps the assistant's conversation. */
function navigate(url) {
  history.pushState(null, "", url);
  route();
}
function link(href, props, ...kids) {
  return el("a", { href, onclick: (e) => { if (!e.ctrlKey && !e.metaKey) { e.preventDefault(); navigate(href); } }, ...props }, ...kids);
}
function drawNav(view) {
  const item = (name, text, match) => link(here(name ? { view: name } : {}), { "aria-current": match ? "page" : null }, text);
  $("nav").replaceChildren(el("a", { href: toDesk() }, "Research desk"),
    item("", "Workspace", view === "workspace" || view === "flow"), item("dashboards", "Dashboards", view === "dashboards"), item("reports", "Reports", view === "reports"));
}
function route() {
  const u = new URLSearchParams(location.search);
  const view = ["dashboards", "reports", "flow"].includes(u.get("view")) ? u.get("view") : "workspace";
  drawNav(view);
  hideTip();
  if (view === "flow") return showFlow(u);
  if (view === "dashboards") return showDashboards(u);
  if (view === "reports") return showReports(u);
  const f = state.filter;
  f.period = PERIODS.some(([k]) => k === u.get("period")) ? u.get("period") : "all";
  f.day = u.get("day");
  f.outcome = OUTCOMES[u.get("outcome")] ? u.get("outcome") : "any";
  f.report = Number(u.get("report")) || null;
  f.topic = u.get("topic");
  state.work = WORK_TABS.includes(u.get("tab")) ? u.get("tab") : defaultWork();
  state.explore = EXPLORE_TABS.includes(u.get("explore")) ? u.get("explore") : "topics";
  showWorkspace();
  return loadHits();
}
function syncUrl() {
  const f = state.filter;
  const u = {};
  if (f.period !== "all") u.period = f.period;
  if (f.day) u.day = f.day;
  if (f.outcome !== "any") u.outcome = f.outcome;
  if (f.report) u.report = f.report;
  if (f.topic) u.topic = f.topic;
  if (state.work !== defaultWork()) u.tab = state.work;
  if (state.explore !== "topics") u.explore = state.explore;
  history.replaceState(null, "", here(u));
}

function setFilter(change) {
  Object.assign(state.filter, change);
  syncUrl();
  draw();
  loadHits();
}
const toggleReport = (id) => setFilter({ report: state.filter.report === id ? null : id });
function leaveMentions() { if (state.work === "mentions") state.work = defaultWork(); }
function selectTopic(query) {
  const off = state.filter.topic === query;
  if (off) leaveMentions();
  else state.work = "mentions";
  setFilter({ topic: off ? null : query });
}
function selectCell(query, id) {
  const f = state.filter;
  const off = f.topic === query && f.report === id;
  if (off) leaveMentions();
  else state.work = "mentions";
  setFilter({ topic: off ? null : query, report: off ? null : id });
}
function clearFilters() {
  leaveMentions();
  setFilter({ period: "all", day: null, outcome: "any", report: null, topic: null });
}

/* What the page is based on, in full, before anything else. */
function seriesLine() {
  const groups = new Map();
  for (const d of state.reports) {
    const name = d.private ? "Your uploads" : d.title.replace(/\s*\b[A-Z][a-z]+ \d{4}\b\s*/, " ").trim();
    groups.set(name, [...(groups.get(name) || []), d]);
  }
  const parts = [...groups].map(([name, docs]) => {
    const dated = docs.filter((d) => d.edition[0]);
    const span = dated.length > 1 ? `, ${dated[0].short} to ${dated.at(-1).short}` : "";
    return `${name}: ${plural(docs.length, dated.length ? "edition" : "report")}${span}`;
  });
  const pages = state.reports.reduce((a, d) => a + d.pages, 0);
  return `${parts.join(" · ")} · ${pages.toLocaleString()} pages`;
}
function drawSources() {
  const f = state.filter;
  const about = new Map(state.topics.filter((t) => t.group === "apart").map((t) => [t.doc, t.term]));
  const used = new Map(state.reports.map((d) => [d.id, new Set()]));
  for (const r of state.rows) for (const [d, p] of r.cited) used.get(d)?.add(p);
  $("sourcesLine").textContent = seriesLine();
  $("sourceCells").replaceChildren(...state.reports.map((d) => {
    const n = used.get(d.id).size;
    return el("div", { class: "source" + (f.report === d.id ? " is-on" : "") },
      el("div", { class: "source-top" },
        el("button", { type: "button", class: "source-name", "aria-pressed": String(f.report === d.id), title: d.title, "data-key": "source-" + d.id, onclick: () => toggleReport(d.id) }, d.short),
        el("span", { class: "source-acts" }, d.pdf ? el("a", { href: d.pdf, target: "_blank", rel: "noopener" }, "PDF") : null,
          el("a", { href: toDesk({ q: `What does the ${d.edition[2] ? d.edition[2] + " edition" : d.title} say about ` }) }, "Ask"))),
      el("p", { class: "source-about", title: about.has(d.id) ? "The phrase most particular to this report" : "" }, about.get(d.id) || d.title),
      el("div", { class: "meter", role: "img", "aria-label": `${n} of ${d.pages} pages used` }, el("i", { style: `width:${Math.min(100, (n / d.pages) * 100)}%` })),
      el("p", { class: "source-foot" }, el("span", {}, `${n} of ${d.pages} pages used`),
        link(here({ view: "flow", doc: d.id }), { title: "See which questions used this report" }, "How it's used")));
  }));
}

/* What to do next: each one says why, and carries the button that does it. */
const STEP_ICONS = { review: "flag", stale: "clock", unreliable: "alert", refused: "none", blind: "search", start: "plus" };
function stepAction(a) {
  if (a.flow) return link(here({ view: "flow", a: a.flow }), { class: "step-act" }, a.label);
  if (a.topic) return el("button", { type: "button", class: "step-act", onclick: () => { if (state.filter.topic !== a.topic) selectTopic(a.topic); } }, a.label);
  return el("a", { class: "step-act", href: toDesk(a.desk) }, a.label);
}
function drawNext() {
  $("nextList").replaceChildren(...state.next.map((s) => el("article", { class: "step" },
    el("h3", {}, icon(STEP_ICONS[s.kind] || "page"), s.title),
    el("p", { title: s.why }, s.why),
    el("div", { class: "step-acts" }, s.actions.map(stepAction)))));
}

function drawFilters() {
  const f = state.filter;
  const base = rowsFor("outcome").filter((r) => !r.cell);
  const seg = (items, on, pick, label) => el("div", { class: "seg", role: "group", "aria-label": label }, items.map(([key, text, count]) => el("button", {
    type: "button", "aria-pressed": String(on === key), "data-key": label + "-" + key, onclick: () => pick(key),
  }, text, count === undefined ? null : el("b", {}, count.toLocaleString()))));
  const counted = (pass) => new Set(base.filter(pass).map((r) => r.q)).size;
  const chips = [];
  const chip = (text, label, clear) => chips.push(el("button", { type: "button", class: "chip", "aria-label": label, onclick: clear }, text, icon("cross")));
  if (f.report) chip(report(f.report)?.short || "Report", "Stop filtering to this report", () => setFilter({ report: null }));
  const topic = currentTopic();
  if (topic) chip(topic.term, `Stop filtering to ${topic.term}`, () => selectTopic(topic.query));
  if (f.day) chip(dayLabel(f.day), `Stop filtering to ${dayLabel(f.day)}`, () => setFilter({ day: null }));
  if (chips.length || f.period !== "all" || f.outcome !== "any") chips.push(el("button", { type: "button", class: "link-btn", onclick: clearFilters }, "Clear all"));
  $("filters").replaceChildren(
    el("div", { class: "filter" }, el("span", {}, "Period"), seg(PERIODS, f.period, (k) => setFilter({ period: k, day: null }), "Period")),
    el("div", { class: "filter" }, el("span", {}, "Your questions"), seg(Object.entries(OUTCOMES).map(([k, [text, pass]]) => [k, text, counted(pass)]), f.outcome, (k) => setFilter({ outcome: k }), "Outcome")),
    el("div", { class: "chips" }, chips));
}

/* The topic table: fixed shades so a number means the same thing in every row, and every number is printed. */
const shade = (n) => (n <= 0 ? 0 : n <= 2 ? 1 : n <= 7 ? 2 : n <= 15 ? 3 : n <= 30 ? 4 : 5);
function topicTable(topics, { selected = state.filter, onCell = selectCell, onTopic = selectTopic, onColumn = toggleReport, onDrop = null, grouped = true } = {}) {
  const head = el("tr", {}, el("th", { scope: "col" }, el("span", { class: "sr-only" }, "Topic")), state.reports.map((d) => el("th", { scope: "col", class: selected.report === d.id ? "is-on" : null },
    el("button", { type: "button", "aria-pressed": String(selected.report === d.id), "data-key": "col-" + d.id, "data-tip": `${d.pages}|pages in ${d.title}`, onclick: () => onColumn(d.id) }, el("b", {}, d.short)))));
  const row = (t) => {
    const cells = state.reports.map((d) => {
      const n = t.counts[d.id] || 0;
      const on = selected.topic === t.query && selected.report === d.id;
      if (!n) return el("td", {}, el("span", { class: "cell h0" }, "–"));
      return el("td", {}, el("button", {
        type: "button", class: `cell h${shade(n)}`, "aria-pressed": String(on), "data-key": `cell-${t.query}-${d.id}`,
        "aria-label": `${t.term}: ${plural(n, "page")} in ${d.name}`, "data-tip": `${n}|${n === 1 ? "page" : "pages"} of ${d.short} mention ${t.term}`, onclick: () => onCell(t.query, d.id),
      }, n.toLocaleString()));
    });
    return el("tr", {}, el("th", { scope: "row", class: "topic" }, el("div", { class: "topic-row" },
      el("button", { type: "button", class: "topic-name", "aria-pressed": String(selected.topic === t.query), "data-key": "topic-" + t.query, title: t.term, onclick: () => onTopic(t.query) }, t.term),
      t.group === "mine" && onDrop ? el("button", { type: "button", class: "topic-drop", "aria-label": `Remove ${t.term}`, onclick: () => onDrop(t.query) }, icon("cross")) : null)), cells);
  };
  const body = [];
  if (grouped) {
    for (const [group, label] of [["mine", "You added"], ["asked", "From your questions"], ["apart", "What sets each report apart"]]) {
      const list = topics.filter((t) => t.group === group);
      if (list.length) body.push(el("tr", { class: "group" }, el("th", { colSpan: state.reports.length + 1, scope: "colgroup" }, label)), ...list.map(row));
    }
  } else body.push(...topics.map(row));
  return el("table", { class: "heat" }, el("colgroup", {}, el("col", { class: "label" }), state.reports.map(() => el("col"))), el("thead", {}, head), el("tbody", {}, body));
}

const PINNED = "ra-topics-" + USER;
const pinned = () => { try { return JSON.parse(localStorage.getItem(PINNED)) || []; } catch { return []; } };
function savePinned() {
  try { localStorage.setItem(PINNED, JSON.stringify(state.topics.filter((t) => t.group === "mine").map((t) => t.query))); } catch { /* private window: it lasts this visit */ }
}
function dropTopic(query) {
  state.topics = state.topics.filter((t) => t.query !== query);
  savePinned();
  if (state.filter.topic === query) selectTopic(query);
  else draw();
}
const fetchTopic = (text) => api(`/api/me/topic?${who}&q=${encodeURIComponent(text)}`);
/* Adds a topic to the table, or finds the one already there. Returns it. */
async function addTopic(text) {
  const clean = text.trim();
  const known = state.topics.find((t) => t.term.toLowerCase() === clean.toLowerCase().replace(/"/g, ""));
  if (known) return known;
  const t = await fetchTopic(clean);
  state.topics.unshift(t);
  savePinned();
  return t;
}
async function submitTopic(e) {
  e.preventDefault();
  const input = $("topicInput");
  const text = input.value.trim();
  if (!text) return;
  const note = $("topicNote");
  const go = $("topicGo");
  input.value = "";
  go.disabled = true;
  go.textContent = "Counting…";
  note.textContent = "";
  try {
    const t = await addTopic(text);
    if (!Object.keys(t.counts).length) note.textContent = `No page in your reports mentions “${t.term}”. Try a shorter word.`;
    draw();
  } catch (err) {
    note.textContent = err.message;
    input.value = text;
  } finally {
    go.disabled = false;
    go.textContent = "Add";
  }
}

/* Page strips. A page is one sliver of its report; pointing at any sliver names the page, and a click reads it. */
const cites = (n) => (n <= 1 ? 1 : n <= 3 ? 2 : n <= 7 ? 3 : n <= 15 ? 4 : 5);
function pageStrips(rows, { selectedReport = null, hits = null, term = null, onName = toggleReport } = {}) {
  return state.reports.map((d) => {
    const counts = new Map();
    for (const r of rows) for (const [doc, p] of r.cited) if (doc === d.id) counts.set(p, (counts.get(p) || 0) + 1);
    const total = [...counts.values()].reduce((a, b) => a + b, 0);
    const at = (p) => `${((p - 1) / d.pages) * 100}%`;
    const width = `${100 / d.pages}%`;
    const pageLink = (p) => toDesk({ doc: d.id, p, ...(term ? { term } : {}) });
    const pageAt = (e) => {
      const box = e.currentTarget.getBoundingClientRect();
      return Math.min(d.pages, Math.floor(((e.clientX - box.left) / box.width) * d.pages) + 1);
    };
    const strip = el("div", {
      class: "strip", role: "group", "aria-label": `${d.name}: ${plural(counts.size, "cited page")} of ${d.pages}`,
      onclick: (e) => { if (!e.target.closest("a")) location.href = pageLink(pageAt(e)); },
      onpointermove: (e) => {
        const p = pageAt(e);
        const n = counts.get(p) || 0;
        showTip(`${p}|${n ? `cited ${plural(n, "time")}` : "not cited yet"}, ${d.short}`, e.clientX, e.currentTarget.getBoundingClientRect().top);
      },
      onpointerleave: hideTip,
    }, [...counts].sort((a, b) => a[0] - b[0]).map(([p, n]) => el("a", {
      href: pageLink(p), class: `h${cites(n)}`, style: `left:${at(p)};width:${width}`, "aria-label": `Page ${p}, cited ${plural(n, "time")}`,
    })), hits ? hits.filter((h) => h.doc_id === d.id).map((h) => el("i", { class: "mention", style: `left:${at(h.page)};width:${width}` })) : []);
    return el("div", { class: "strip-row" + (selectedReport && selectedReport !== d.id ? " is-dim" : "") },
      el("button", { type: "button", class: "strip-name", "aria-pressed": String(selectedReport === d.id), "data-key": "strip-" + d.id, title: d.title, onclick: () => onName(d.id) }, d.short),
      strip,
      el("span", { class: "strip-sum" }, hits ? [el("b", {}, String(hits.filter((h) => h.doc_id === d.id).length)), " pages mention it"]
        : [el("b", {}, String(counts.size)), ` of ${d.pages} pp · `, el("b", {}, total.toLocaleString()), " cited"]));
  });
}

/* The days a person asked: only those days, so the columns can carry their own labels. Selecting one filters to it. */
function dayBars(rows, { selected = null, onPick = null } = {}) {
  const byDay = new Map();
  for (const r of rows.filter((x) => !x.cell)) {
    const d = byDay.get(r.day) || { yes: 0, no: 0 };
    d[r.status === "answered" ? "yes" : "no"]++;
    byDay.set(r.day, d);
  }
  const days = [...byDay.keys()].sort();
  if (!days.length) return el("p", { class: "day-none" }, "No questions match these filters");
  const peak = Math.max(...[...byDay.values()].map((d) => d.yes + d.no));
  // ponytail: labels stop at 14 columns and the columns narrow past that; bucket into weeks once anyone has a year of history.
  const labelled = days.length <= 14;
  return el("div", { class: "day-wrap" },
    el("div", { class: "legend" }, el("i", { class: "swatch swatch-answered" }), "Answered", el("i", { class: "swatch swatch-none" }), "No source found"),
    el("div", { class: "day-plot" }, days.map((day) => {
      const d = byDay.get(day);
      const n = d.yes + d.no;
      const tip = `${n.toLocaleString()}|${n === 1 ? "time" : "times"} on ${dayLabel(day)}: ${d.yes.toLocaleString()} answered, ${d.no.toLocaleString()} no source`;
      const seg = (cls, v) => (v ? el("i", { class: cls, style: `height:max(2px, ${(v / peak) * 100}%)` }) : null);
      return el("button", {
        type: "button", class: "day", "data-key": "day-" + day, "data-tip": tip, "aria-label": tip.replace("|", " "), "aria-pressed": String(selected === day), onclick: () => onPick?.(day),
      }, el("span", { class: "day-bars" }, seg("seg-none", d.no), seg("seg-answered", d.yes)), labelled ? el("span", { class: "day-label" }, el("b", {}, n.toLocaleString()), dayLabel(day)) : null);
    })),
    labelled ? null : el("div", { class: "day-axis" }, el("span", {}, dayLabel(days[0])), el("span", {}, dayLabel(days.at(-1)))));
}

/* Adding to a report or a dashboard goes to the one last worked on, and says where it went. */
async function addToBoard(kind, blocks) {
  try {
    const b = await send("/api/me/boards/add", "POST", { kind, blocks });
    toast(`Added to “${b.title}”. `, { href: here({ view: kind === "report" ? "reports" : "dashboards", board: b.id }), label: "Open" });
  } catch (e) { toast(e.message); }
}
const findingAdd = (f) => ({ type: "finding", audit_id: f.audit_id, claim: f.claim });

function findingItem(f, { actions = true } = {}) {
  const d = report(f.doc_id);
  return el("article", { class: "item" },
    el("p", { class: "finding-text" }, f.text),
    el("div", { class: "item-meta" },
      el("a", { class: "cite-link", href: toDesk({ doc: f.doc_id, p: f.page }), title: `Read page ${f.page} of ${d?.title || "this report"}` }, `${d?.short || "Report"} p.${f.page}`),
      el("span", { class: "from", title: f.question }, f.question)),
    actions ? el("div", { class: "item-acts" },
      link(here({ view: "flow", a: f.audit_id }), {}, "How we got this"),
      el("button", { type: "button", class: "link-btn", onclick: () => addToBoard("report", [findingAdd(f)]) }, "Add to report"),
      el("button", { type: "button", class: "link-btn", onclick: () => addToBoard("dashboard", [findingAdd(f)]) }, "Pin")) : null);
}
function modelNote() {
  return state.connected ? null : el("div", { class: "work-note is-warn" }, icon("alert"), el("span", {}, "No AI model is connected, so these quote the closest sentence and may not answer the question. Adding a model key fixes this."));
}

function groupQuestions(rows) {
  const groups = new Map();
  for (const r of rows.filter((x) => !x.cell)) {
    const g = groups.get(r.q) || { q: r.q, rows: [], yes: 0 };
    g.rows.push(r);
    if (r.status === "answered") g.yes++;
    groups.set(r.q, g);
  }
  return [...groups.values()];
}
function questionItem(g) {
  const text = state.questions[g.q];
  const n = g.rows.length;
  const open = g.rows.find((r) => r.saved && r.status === "answered") || g.rows.find((r) => r.saved);
  const latest = open || g.rows[0];
  const cited = [];
  for (const [d, p] of latest.cited) if (report(d) && !cited.some(([x, y]) => x === d && y === p)) cited.push([d, p]);
  const outcome = g.yes === n ? "Answered" : g.yes === 0 ? "No source found" : `Answered ${g.yes} of ${n}`;
  const pct = (v) => `${(v / n) * 100}%`;
  return el("article", { class: "item" },
    el("a", { class: "item-title", href: open ? toDesk({ a: open.id }) : toDesk({ q: text }), title: open ? "Open the stored answer" : "This answer was not stored. Opens the question to ask again." }, text),
    el("div", { class: "item-meta" },
      el("span", { class: "outcome" }, el("span", { class: "split-bar", "aria-hidden": "true" },
        g.yes ? el("i", { class: "seg-answered", style: `width:${pct(g.yes)}` }) : null,
        g.yes < n ? el("i", { class: "seg-none", style: `width:${pct(n - g.yes)}` }) : null), outcome),
      el("span", {}, dayLabel(g.rows[0].day)),
      n > 1 ? el("span", {}, `asked ${n} times`) : null,
      g.rows.some((r) => r.rating === "up") ? el("span", { class: "flag" }, icon("thumb"), "Rated up") : null,
      g.rows.some((r) => r.rating === "down") ? el("span", { class: "flag" }, icon("thumb", "down"), "Rated down") : null,
      g.rows.some((r) => r.review === "open") ? el("span", { class: "flag" }, icon("flag"), "In review") : null,
      cited.slice(0, 3).map(([d, p]) => el("a", { class: "cite-link", href: toDesk({ doc: d, p }), title: `Read page ${p} of ${report(d).title}` }, `${report(d).short} p.${p}`)),
      cited.length > 3 ? el("span", {}, `+${cited.length - 3}`) : null),
    latest.why ? el("p", { class: "why" }, `Why no answer: ${latest.why}.`) : null,
    el("div", { class: "item-acts" }, open ? link(here({ view: "flow", a: open.id }), {}, "How we got this")
      : el("button", { type: "button", class: "link-btn", title: "Not recorded for this older question. Asking again records it, and uses the model if one is connected.", onclick: (e) => runAgain(text, e.currentTarget) }, "Run again to see how")));
}

function drawWork() {
  const topic = currentTopic();
  const found = findingsWhere(state.filter);
  const groups = groupQuestions(rowsFor());
  const tabs = [["found", "What you've found", found.length], ["questions", "Questions", groups.length], ["tables", "Tables", state.tables.length]];
  if (topic) tabs.unshift(["mentions", `Mentions of ${topic.term}`, null]);
  if (state.work === "mentions" && !topic) state.work = defaultWork();
  $("tabs").replaceChildren(...tabs.map(([id, text, n]) => el("button", {
    type: "button", role: "tab", "aria-selected": String(state.work === id), "data-key": "tab-" + id, onclick: () => { state.work = id; syncUrl(); drawWork(); },
  }, text, n === null ? null : el("b", {}, n.toLocaleString()))));
  const list = $("workList");
  if (state.work === "found") {
    const note = modelNote();
    if (found.length) list.replaceChildren(...[note, ...found.map((f) => findingItem(f))].filter(Boolean));
    else list.replaceChildren(...[note, el("div", { class: "empty" }, el("strong", {}, state.findings.length ? "Nothing here matches these filters" : "Your checked facts will collect here"),
      state.findings.length ? "Clear a filter to see the rest." : "Each stored answer adds the facts it checked, with the page that proves them. Ask a question below to begin.")].filter(Boolean));
  } else if (state.work === "questions") {
    if (groups.length) list.replaceChildren(...groups.map(questionItem));
    else if (!state.rows.length) list.replaceChildren(el("div", { class: "empty" }, el("strong", {}, "Your questions will collect here"), "Each one keeps its outcome and the pages it came from. Ask one below to begin."));
    else if (topic) list.replaceChildren(el("div", { class: "empty" }, el("strong", {}, `You have not asked about ${topic.term} here`), "Ask below, or ", el("a", { href: toDesk({ q: `What do the reports say about ${topic.term}?` }) }, "start from this question"), "."));
    else list.replaceChildren(el("div", { class: "empty" }, el("strong", {}, "No questions match these filters"), state.filter.report ? "Questions with no source cite no report, so they drop out when a report is chosen. " : "", el("button", { type: "button", class: "link-btn", onclick: clearFilters }, "Clear all filters")));
  } else if (state.work === "tables") {
    list.replaceChildren(...state.tables.map((t) => el("article", { class: "item" },
      el("a", { class: "item-title", href: toDesk({ table: t.id }) }, t.name),
      el("div", { class: "item-meta" }, el("span", { class: "outcome" }, t.status === "done" ? "Finished" : t.status === "failed" ? "Failed" : "Running"),
        el("span", {}, `${plural(t.questions, "question")} across ${plural(t.reports, "report")}`), el("span", {}, `${t.answered} of ${t.total} cells answered`), el("span", {}, dayLabel(t.created_at.slice(0, 10)))))),
    el("div", { class: "work-note" }, state.tables.length ? "" : "A table asks the same questions of every report you choose. ", el("a", { href: toDesk({ tab: "table" }) }, "Start a new table")));
  } else drawMentions(topic);
}
function drawMentions(topic) {
  const f = state.filter;
  const list = $("workList");
  const key = topic.query + "|" + (f.report || "");
  if (state.hitsKey !== key || !state.hits) {
    list.replaceChildren(el("div", { class: "empty" }, "Finding the pages…"));
    return;
  }
  const where = f.report ? report(f.report)?.name : "your reports";
  if (!state.hits.length) {
    list.replaceChildren(el("div", { class: "empty" }, el("strong", {}, `No page in ${where} mentions ${topic.term}`), "Choose another report above, or clear the report filter."));
    return;
  }
  list.replaceChildren(
    el("div", { class: "work-note" }, `${plural(state.hits.length, "page")} in ${where}, closest match first. `, el("a", { href: toDesk({ q: `What do the reports say about ${topic.term}?` }) }, "Ask about this")),
    ...state.hits.map((h) => {
      const url = toDesk({ doc: h.doc_id, p: h.page, term: topic.term });
      return el("article", { class: "item" },
        el("div", { class: "item-meta" }, el("a", { class: "cite-link", href: url }, `${report(h.doc_id)?.short || h.title} p.${h.page}`)),
        el("a", { class: "snippet", href: url }, h.snippet.map(([text, marked]) => (marked ? el("mark", {}, text) : text))));
    }));
}
let hitsSeq = 0;
async function loadHits() {
  const topic = currentTopic();
  if (!topic) { state.hits = null; state.hitsKey = ""; return; }
  const doc = state.filter.report;
  const key = topic.query + "|" + (doc || "");
  if (state.hitsKey === key && state.hits) return;
  const seq = ++hitsSeq;
  try {
    const j = await api("/api/find?" + new URLSearchParams({ user: USER, q: topic.query, limit: 200, ...(doc ? { doc_id: doc } : {}) }));
    if (seq !== hitsSeq) return;
    state.hits = j.hits;
    state.hitsKey = key;
  } catch (e) {
    if (seq !== hitsSeq) return;
    state.hits = [];
    state.hitsKey = key;
    toast(e.message);
  }
  if ($("exploreBody")) { drawExplore(); drawWork(); }
}

/* Explore: the charts, for looking around once the top of the page has said what matters. */
function pinBlock() {
  const f = state.filter;
  const topic = currentTopic();
  if (state.explore === "pages") return { type: "pages", ...(topic ? { topic: topic.term } : {}) };
  if (state.explore === "history") return { type: "days" };
  return { type: "topics", ...(topic ? { terms: [topic.term] } : {}), ...(f.report ? { report: f.report } : {}) };
}
function drawExplore() {
  const f = state.filter;
  const topic = currentTopic();
  $("exploreTabs").replaceChildren(...[["topics", "What each report talks about"], ["pages", "Where your answers came from"], ["history", "Days you asked"]].map(([id, text]) => el("button", {
    type: "button", role: "tab", "aria-selected": String(state.explore === id), "data-key": "explore-" + id, onclick: () => { state.explore = id; syncUrl(); drawExplore(); },
  }, text)));
  const body = $("exploreBody");
  const hint = $("exploreHint");
  $("addTopic").hidden = state.explore !== "topics";
  $("pagesLegend").hidden = !(state.explore === "pages" && topic);
  if (state.explore === "topics") {
    hint.textContent = "Pages in each report that mention a topic. Select a number to read them.";
    body.replaceChildren(topicTable(state.topics, { onDrop: dropTopic }));
  } else if (state.explore === "pages") {
    hint.textContent = "Each mark is a page your answers cited, darker when cited more. Select any page to read it.";
    $("pagesLegendText").textContent = topic ? `Pages that mention ${topic.term}` : "";
    const key = topic ? topic.query + "|" + (f.report || "") : "";
    body.replaceChildren(el("div", { class: "strips" }, pageStrips(rowsFor("report"), { selectedReport: f.report, hits: key && state.hitsKey === key ? state.hits : null, term: topic?.term })));
  } else {
    hint.textContent = "How often you asked, and how often an answer came back. Select a day to filter to it.";
    body.replaceChildren(dayBars(rowsFor("day"), { selected: f.day, onPick: (day) => setFilter({ day: f.day === day ? null : day }) }));
  }
}

function draw() {
  // Redrawing replaces nodes, so keep the keyboard where it was.
  const key = document.activeElement?.dataset?.key;
  drawSources();
  drawFilters();
  drawExplore();
  drawWork();
  if (key) document.querySelector(`[data-key="${CSS.escape(key)}"]`)?.focus({ preventScroll: true });
}

function showWorkspace() {
  document.title = "Workspace · Research assistant";
  $("view").replaceChildren(el("div", { class: "workspace" },
    el("div", { class: "filters", id: "filters", role: "group", "aria-label": "Filter everything below" }),
    el("section", { class: "sources", id: "sources", "aria-label": "Your sources" },
      el("header", { class: "sources-head" }, el("h1", {}, "Your sources"), el("p", { id: "sourcesLine" }), el("button", { type: "button", class: "add-source", onclick: openAddDialog }, icon("plus"), "Add a report")),
      el("div", { class: "source-cells", id: "sourceCells" })),
    el("section", { class: "panel next", id: "next", "data-type": "next", "aria-labelledby": "nextHead" },
      el("header", { class: "panel-head" }, el("h2", { id: "nextHead" }, "What to do next")),
      el("div", { class: "next-list", id: "nextList" })),
    el("section", { class: "panel explore", id: "explore", "data-type": "topics", "aria-label": "Explore" },
      el("div", { class: "tabs explore-tabs" }, el("div", { class: "tabs-row", role: "tablist", "aria-label": "Explore", id: "exploreTabs" }),
        el("button", { type: "button", class: "link-btn pin", onclick: () => addToBoard("dashboard", [pinBlock()]) }, icon("pin"), "Pin to dashboard")),
      el("div", { class: "panel-head explore-head" }, el("p", { id: "exploreHint" }),
        el("span", { class: "legend", id: "pagesLegend", hidden: true }, el("i", { class: "swatch swatch-mention" }), el("span", { id: "pagesLegendText" })),
        el("form", { class: "add-topic", id: "addTopic", onsubmit: submitTopic },
          el("label", { class: "sr-only", htmlFor: "topicInput" }, "Add a topic to compare"),
          el("input", { id: "topicInput", type: "search", maxLength: 80, placeholder: "Add a topic, like tariff", autocomplete: "off" }),
          el("button", { type: "submit", class: "ghost", id: "topicGo" }, "Add"))),
      el("p", { class: "note", id: "topicNote", role: "status" }),
      el("div", { class: "scroll explore-body", id: "exploreBody" })),
    el("aside", { class: "panel work", id: "work", "data-type": "found", "aria-label": "Your work" },
      el("div", { class: "tabs", role: "tablist", "aria-label": "Your work" }, el("div", { class: "tabs-row", id: "tabs" })),
      el("div", { class: "scroll work-list", id: "workList", role: "tabpanel" }),
      el("form", { class: "ask", action: "/", method: "get" },
        el("input", { type: "hidden", name: "user", value: USER }), el("input", { type: "hidden", name: "run", value: "1" }),
        el("label", { class: "sr-only", htmlFor: "askQ" }, "Ask a new question"),
        el("input", { id: "askQ", name: "q", maxLength: 500, placeholder: "Ask a new question", autocomplete: "off", required: true }),
        el("button", { type: "submit", class: "primary" }, "Ask")))));
  drawNext();
  draw();
}

/* Adding a report happens here, in a dialog. The server decides what it accepts and keeps the file private to this person; the
   checks here only say so sooner. Dropping a file anywhere comes here too, because a browser would otherwise leave the page to open it. */
const MAX_UPLOAD_MB = 50;
let reading = 0;
function addRow(kind, text, ...kids) {
  const glyph = kind === "working" ? el("span", { class: "spinner" }) : icon(kind === "fail" ? "alert" : "check");
  const row = el("li", { class: `add-row is-${kind}` }, glyph, el("span", {}, text), ...kids);
  $("addList").append(row);
  return row;
}
function drawAddDone() {
  $("addDone").disabled = reading > 0;
  $("addDone").textContent = reading ? "Reading…" : "Done";
}
function openAddDialog() {
  const d = $("addDialog");
  if (d.open) return;
  if (!reading) $("addList").replaceChildren();
  $("addNote").textContent = `PDF, up to ${MAX_UPLOAD_MB} MB each. Only ${USER} can see them.`;
  drawAddDone();
  d.showModal();
}
async function addReports(files) {
  openAddDialog();
  for (const file of files) {
    if (!/\.pdf$/i.test(file.name)) { addRow("fail", `${file.name}: only PDF files can be added.`); continue; }
    if (file.size > MAX_UPLOAD_MB * 1024 * 1024) { addRow("fail", `${file.name}: this file is over the ${MAX_UPLOAD_MB} MB limit.`); continue; }
    const row = addRow("working", `Reading ${file.name}…`);
    reading++;
    drawAddDone();
    try {
      const r = await api("/api/me/uploads?" + new URLSearchParams({ user: USER, name: file.name }), { method: "POST", headers: { "Content-Type": "application/pdf" }, body: file });
      await loadWork();
      if ($("sources")) { drawNext(); draw(); }
      const gaps = r.low_text_pages ? ` ${plural(r.low_text_pages, "page")} had no readable text.` : "";
      row.remove();
      addRow("done", `${r.title} added, ${plural(r.pages, "page")}.${gaps}`, el("a", { href: toDesk({ q: `What does ${r.title} say about ` }) }, "Ask about it"));
    } catch (e) {
      row.remove();
      addRow("fail", `${file.name}: ${e instanceof TypeError ? "this could not be sent. Check the file still exists and try again." : e.message}`);
    } finally {
      reading--;
      drawAddDone();
    }
  }
}
$("dropIcon").append(icon("upload"));
$("addInput").onchange = (e) => { const files = [...e.target.files]; e.target.value = ""; addReports(files); };
$("addDone").onclick = () => $("addDialog").close();
$("addDialog").addEventListener("cancel", (e) => { if (reading) e.preventDefault(); });
$("addDialog").addEventListener("click", (e) => { if (e.target === $("addDialog") && !reading) $("addDialog").close(); }); // the area outside the box
const carriesFiles = (e) => [...(e.dataTransfer?.types || [])].includes("Files");
document.addEventListener("dragover", (e) => {
  if (!carriesFiles(e)) return;
  e.preventDefault();
  openAddDialog();
  $("dropzone").classList.add("is-over");
});
document.addEventListener("dragleave", (e) => { if (!e.relatedTarget) $("dropzone").classList.remove("is-over"); });
document.addEventListener("drop", (e) => {
  if (!carriesFiles(e)) return;
  e.preventDefault();
  $("dropzone").classList.remove("is-over");
  addReports([...e.dataTransfer.files]);
});

/* The one tooltip: the value first, then what it counts. It adds nothing that is not also printed or a click away. */
function showTip(text, x, top) {
  const tip = $("tip");
  const [value, label] = text.split("|");
  tip.replaceChildren(el("b", {}, value), label);
  tip.hidden = false;
  const w = tip.offsetWidth;
  tip.style.left = `${Math.max(8, Math.min(innerWidth - w - 8, x - w / 2))}px`;
  tip.style.top = `${Math.max(8, top - tip.offsetHeight - 6)}px`;
}
function hideTip() { $("tip").hidden = true; }
const tipFrom = (e) => {
  const t = e.target.closest?.("[data-tip]");
  if (!t) return;
  const box = t.getBoundingClientRect();
  showTip(t.dataset.tip, box.left + box.width / 2, box.top);
};
document.addEventListener("pointerover", tipFrom);
document.addEventListener("focusin", tipFrom);
document.addEventListener("pointerout", (e) => { if (e.target.closest?.("[data-tip]")) hideTip(); });
document.addEventListener("focusout", hideTip);
document.addEventListener("keydown", (e) => {
  if (e.key !== "Escape" || e.target.matches("input, textarea, select") || !$("sources")) return;
  const f = state.filter;
  if (f.day || f.report || f.topic) {
    leaveMentions();
    setFilter({ day: null, report: null, topic: null });
  }
});
addEventListener("popstate", route);
$("user").onchange = () => { location.href = "/dashboard?" + new URLSearchParams({ user: $("user").value }); };

/* The reports read left to right in time, so a row of the table reads as a story; uploads have no date and come last. */
function shapeReports(library) {
  const dated = (d) => d.edition[0] * 12 + d.edition[1];
  return library.map((d) => ({
    ...d,
    name: d.edition[2] || d.title,
    short: d.edition[2] ? `${d.edition[2].slice(0, 3)} ${d.edition[0]}` : d.title.length > 14 ? d.title.slice(0, 13) + "…" : d.title,
  })).sort((a, b) => (dated(a) === 0) - (dated(b) === 0) || dated(a) - dated(b) || a.title.localeCompare(b.title));
}

/* Everything the page draws from. Fetched at the start, and again when a new report changes what can be searched. */
async function loadWork() {
  const [activity, library, tables] = await Promise.all([api(`/api/me/activity?${who}`), api(`/api/me/widget/library?${who}`), api(`/api/me/analyses?${who}`)]);
  applyActivity(activity);
  Object.assign(state, { reports: shapeReports(library.library), tables: tables.analyses });
  const order = new Map(state.reports.map((d, i) => [d.id, i]));
  const mine = await Promise.all(pinned().map((q) => fetchTopic(q).catch(() => null)));
  state.topics = [...mine.filter(Boolean), ...activity.topics.filter((t) => t.group === "asked"),
    ...activity.topics.filter((t) => t.group === "apart").sort((a, b) => order.get(a.doc) - order.get(b.doc))];
}

function applyActivity(activity) {
  Object.assign(state, { now: activity.now, model: activity.model, connected: activity.connected, questions: activity.questions, findings: activity.findings, next: activity.next,
    lower: activity.questions.map((q) => q.toLowerCase()), rows: activity.rows.map((r) => ({ ...r, day: r.at.slice(0, 10) })) });
}

addEventListener("DOMContentLoaded", async () => {
  $("brandMark").append(icon("mark"));
  $("brand").href = toDesk();
  try {
    const [users] = await Promise.all([api("/api/users"), loadWork()]);
    $("user").replaceChildren(...users.users.map((name) => el("option", { value: name, selected: name === USER }, name)));
    $("view").setAttribute("aria-busy", "false");
    route();
    startAssistant();
  } catch (e) {
    $("view").setAttribute("aria-busy", "false");
    $("nav").replaceChildren(el("a", { href: "/" }, "Research desk"));
    $("view").replaceChildren(el("div", { class: "failed" }, el("strong", {}, `Nothing to show for ${USER || "this page"}`), e.message, " ", el("a", { href: "/" }, "Go to the research desk")));
  }
});
