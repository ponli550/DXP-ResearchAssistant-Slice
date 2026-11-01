/* Research assistant helper: a separate overlay that tells a new user where to go and shows them around.
 *
 * It knows nothing about the app's code. It reads the page's DOM (a handful of element ids, listed in helper/README.md,
 * each one optional) and calls the app's public endpoints. If an id is missing the matching action simply does not appear.
 * Load it with one script tag, through helper/serve.py, or from a bookmarklet. No framework; it builds DOM nodes, never markup strings.
 */
(function () {
  "use strict";
  if (window.__raHelper) return;
  window.__raHelper = true;

  var script = document.currentScript;
  var BASE = script && script.src ? script.src.replace(/helper\.js(\?.*)?$/, "") : "/__helper/";
  var SEEN = "ra.helper.seen";
  var params = new URLSearchParams(location.search);
  var reduced = window.matchMedia && matchMedia("(prefers-reduced-motion: reduce)").matches;

  function $(sel) { return document.querySelector(sel); }
  function el(tag, cls, text) { var e = document.createElement(tag); if (cls) e.className = cls; if (text !== undefined) e.textContent = text; return e; }
  function enc(s) { return encodeURIComponent(s); }
  function getJSON(url) { return fetch(url).then(function (r) { return r.ok ? r.json() : null; }).catch(function () { return null; }); }
  function remembered() { try { return localStorage.getItem(SEEN) === "1"; } catch (e) { return false; } }
  function remember() { try { localStorage.setItem(SEEN, "1"); } catch (e) { /* private window: ask again next time */ } }

  if (!document.getElementById("rah-css")) {
    var link = el("link"); link.id = "rah-css"; link.rel = "stylesheet"; link.href = BASE + "helper.css";
    document.head.appendChild(link);
  }

  // Which page is this? The helper only needs three answers.
  function page() {
    if (location.pathname === "/dashboard") return params.get("user") ? "board" : "admin";
    if (location.pathname === "/") return "chat";
    return "other";
  }
  function currentUser() { var s = $("#user"); return (s && s.value) || params.get("user") || ""; }
  function click(sel) { var e = $(sel); if (e) { e.click(); return true; } return false; }
  function focusQ() { var q = $("#q"); if (q) { q.focus(); var n = q.value.length; try { q.setSelectionRange(n, n); } catch (e) { /* not a text field */ } } }
  function setQ(text) {
    var q = $("#q"); if (!q) return;
    click("#askMode");
    q.value = text; q.dispatchEvent(new Event("input", { bubbles: true })); focusQ();
  }
  function reveal(sel) {
    var t = $(sel); if (!t) return;
    t.scrollIntoView({ behavior: reduced ? "auto" : "smooth", block: "center" });
    t.classList.add("rah-flash"); setTimeout(function () { t.classList.remove("rah-flash"); }, 1800);
  }
  function go(url) { location.href = url; }

  // ---- what a person may want to do, per page ----
  function tasks() {
    var p = page(), u = currentUser(), out = [];
    if (p === "chat") {
      out.push({ label: "Find words you know are in a report", text: "Plain full-text search over the same pages. No model, no tokens, answers in milliseconds. A phrase in quotes is exact, OR widens, a minus excludes.",
        act: $("#findBtn") && { label: "Open Find", run: function () { click("#findBtn"); focusQ(); } } });
      out.push({ label: "Ask a question and get a cited answer", text: "A model drafts claims with exact quotes. Plain code checks each quote against its page before you see it. Takes about ten seconds.",
        act: $("#q") && { label: "Go to the question box", run: function () { click("#askMode"); focusQ(); } } });
      out.push({ label: "Check where a claim comes from", text: "Select the citation beside a claim. The page opens on the right, with the quote marked and a line from the claim to it." });
      var me = $("#melink");
      out.push({ label: "Browse the reports you may search", text: "The side panel lists every edition, newest first. Select a name to read it.",
        act: $("#plusReports") && { label: "Show the reports", run: function () { click("#plusReports"); } } });
      out.push({ label: "Compare across editions", text: "In the question box, choose Table across reports to run a question in every edition. Each cell is a checked claim or a refusal.",
        act: me && { label: "Open your board", run: function () { go(me.href); } } });
    } else if (p === "board") {
      var panels = [
        ["next", "See what to do next", "Each suggestion says why, and carries the button that does it."],
        ["topics", "See what each report talks about", "Pages that mention each topic, report by report. Select a number to read them."],
        ["found", "See what you've found", "Checked facts with their pages. How we got this shows every step behind one."]
      ];
      panels.forEach(function (x) {
        var sel = '[data-type="' + x[0] + '"]';
        out.push({ label: x[1], text: x[2], act: $(sel) && { label: "Show me", run: function () { reveal(sel); } } });
      });
      out.push({ label: "Ask a question", text: "The chat drafts a cited answer from your libraries.", act: { label: "Open the chat", run: function () { go("/?user=" + enc(params.get("user"))); } } });
      out.push({ label: "Find words in a report", text: "Plain search, no model.", act: { label: "Open Find", run: function () { go("/?user=" + enc(params.get("user")) + "&tab=find"); } } });
    } else if (p === "admin") {
      var tools = function (id) { return function () { click('.tabs button[data-v="tools"]'); setTimeout(function () { reveal(id); }, 80); }; };
      out.push({ label: "See what needs doing", text: "The To do panel lists each count with its reason. Click a row to open the rows behind it.", act: $('[data-type="todo"]') && { label: "Show me", run: function () { click('.tabs button[data-v="board"]'); reveal('[data-type="todo"]'); } } });
      out.push({ label: "Add a user", text: "A user is a name plus the libraries they may search. Nothing else is stored about them.", act: $("#newuser") && { label: "Show me", run: tools("#newuser") } });
      out.push({ label: "Create an API key", text: "A key stands for one user and inherits that user's grants, nothing more. It is shown once.", act: $("#newkey") && { label: "Show me", run: tools("#newkey") } });
      out.push({ label: "Try a question as any user", text: "Check an answer after an ingest or a model change, and see what the check kept and removed.", act: $("#try") && { label: "Show me", run: tools("#try") } });
      out.push({ label: "Rearrange a panel", text: "Each panel has an expand button. The layout is saved per owner as a JSON document." });
    }
    return out;
  }

  // ---- guided tour: highlight an element, say what it is, move on ----
  var TOURS = {
    chat: [
      ["#q", "Ask or find", "Type a question, or words from a report."],
      ["#go", "Ask", "A model drafts a cited answer; plain code checks every quote. About ten seconds."],
      ["#modeBtn", "What you get", "An answer, a table across reports, or exact words with no model and no cost."],
      ["#scopeBtn", "Which reports", "Everything you may search, or only the ones you tick."],
      ["#railOpen", "Side panel", "Browse reports, and reopen past questions and tables."],
      [".nav", "Pages", "Your activity, and the admin board."],
      ["#user", "Demo user", "The demo has no login. The real build uses your identity provider."]
    ],
    board: [
      ["#sources", "Your sources", "The reports all of this comes from. Select one to filter the page to it."],
      ['[data-type="next"]', "What to do next", "Each suggestion says why, and carries the button that does it."],
      ['[data-type="topics"]', "Explore", "What each report talks about, where your answers came from, and when you asked."],
      ['[data-type="found"]', "What you've found", "Checked facts with their pages, your questions and your tables. How we got this shows every step."],
      ["#assistantBtn", "Assistant", "Explains this page, filters it, and builds dashboards and reports."]
    ],
    admin: [
      ["#brief", "The brief", "What happened since this device last looked. Silent when nothing did."],
      ["#keyrow", "Key figures", "The numbers that matter first."],
      ['[data-type="todo"]', "To do", "Each count with its reason. Click a row for the rows behind it."],
      ['[data-type="timeline"]', "Reports by edition", "Dot size is how often answers cited the report."],
      [".tabs", "Tools", "Add a user, create an API key, try a question as any user."]
    ]
  };
  var tour = null;
  function endTour() {
    if (!tour) return;
    tour.spot.remove(); tour.tip.remove(); window.removeEventListener("resize", tour.place); window.removeEventListener("scroll", tour.place, true);
    tour = null; remember();
  }
  function startTour() {
    closePanel(); endTour();
    var steps = (TOURS[page()] || []).filter(function (s) { var t = $(s[0]); return t && t.offsetParent !== null; });
    if (!steps.length) { toast("Nothing to show on this page yet. Sign in first, then ask again.", true); return; }
    var spot = el("div", "rah-spot"), tip = el("div", "rah-tip");
    tip.setAttribute("role", "dialog"); tip.setAttribute("aria-label", "Tour");
    var count = el("div", "rah-count"), title = el("b", "rah-ttl"), body = el("p", "rah-body"), nav = el("div", "rah-nav");
    var back = el("button", "rah-btn2", "Back"), next = el("button", "rah-go", "Next"), end = el("button", "rah-link", "End tour");
    [back, next, end].forEach(function (b) { b.type = "button"; });
    nav.appendChild(back); nav.appendChild(next); nav.appendChild(end);
    [count, title, body, nav].forEach(function (n) { tip.appendChild(n); });
    document.body.appendChild(spot); document.body.appendChild(tip);
    var i = 0;
    function place() {
      var t = $(steps[i][0]); if (!t) return;
      var r = t.getBoundingClientRect(), pad = 6;
      spot.style.left = (r.left - pad) + "px"; spot.style.top = (r.top - pad) + "px"; spot.style.width = (r.width + pad * 2) + "px"; spot.style.height = (r.height + pad * 2) + "px";
      var tw = tip.offsetWidth || 320, th = tip.offsetHeight || 140, below = r.bottom + 14 + th < window.innerHeight;
      var left = Math.max(12, Math.min(r.left, window.innerWidth - tw - 12));
      tip.style.left = left + "px"; tip.style.top = (below ? r.bottom + 14 : Math.max(12, r.top - th - 14)) + "px";
    }
    function show() {
      var s = steps[i], t = $(s[0]);
      t.scrollIntoView({ behavior: "auto", block: "center" });
      count.textContent = (i + 1) + " of " + steps.length; title.textContent = s[1]; body.textContent = s[2];
      back.disabled = i === 0; next.textContent = i === steps.length - 1 ? "Done" : "Next";
      requestAnimationFrame(function () { place(); next.focus(); });
    }
    back.onclick = function () { if (i > 0) { i--; show(); } };
    next.onclick = function () { if (i < steps.length - 1) { i++; show(); } else endTour(); };
    end.onclick = endTour;
    tour = { spot: spot, tip: tip, place: place };
    window.addEventListener("resize", place); window.addEventListener("scroll", place, true);
    show();
  }

  // ---- the panel ----
  var panel, opener, launcher;
  function buildPanel() {
    panel = el("aside", "rah-panel"); panel.setAttribute("role", "dialog"); panel.setAttribute("aria-label", "Help");
    var head = el("header", "rah-head"); head.appendChild(el("strong", "", "Where to start"));
    var close = el("button", "rah-link", "Close"); close.type = "button"; close.onclick = closePanel; head.appendChild(close);
    panel.appendChild(head);
    var body = el("div", "rah-pbody"); body.id = "rah-body"; panel.appendChild(body);
    document.body.appendChild(panel);
  }
  function fill() {
    var body = $("#rah-body"); body.textContent = "";
    var tourBtn = el("button", "rah-go rah-wide", "Show me around this page"); tourBtn.type = "button"; tourBtn.onclick = startTour;
    body.appendChild(tourBtn);
    var box = el("div", "rah-ex"); body.appendChild(box);
    if (page() === "chat") examples(box);
    body.appendChild(el("h3", "rah-h", "What do you want to do?"));
    tasks().forEach(function (t) {
      var row = el("div", "rah-task"), txt = el("div", "rah-ttext");
      txt.appendChild(el("b", "", t.label)); txt.appendChild(el("span", "", t.text)); row.appendChild(txt);
      if (t.act) { var b = el("button", "rah-btn2", t.act.label); b.type = "button"; b.onclick = function () { closePanel(); t.act.run(); }; row.appendChild(b); }
      body.appendChild(row);
    });
    if (page() === "chat") {
      body.appendChild(el("h3", "rah-h", "Reading an answer"));
      [["The chip under a claim", "is the page it comes from. Click it to read the page."], ["Removed", "means the quote or a number failed the check. The reason is shown."], ["Not found", "means nothing in your libraries supported an answer. Nothing is guessed."]].forEach(function (x) {
        var p = el("p", "rah-note"); p.appendChild(el("b", "", x[0] + " ")); p.appendChild(document.createTextNode(x[1])); body.appendChild(p);
      });
    }
    body.appendChild(el("h3", "rah-h", "Keys"));
    body.appendChild(el("p", "rah-note", "? opens this panel. Esc closes it. Cmd or Ctrl and Enter asks."));
  }
  // Examples come from the person's own history and library, so they are questions that work for them.
  function examples(box) {
    var u = currentUser(); if (!u) return;
    Promise.all([getJSON("/api/me/widget/answers?user=" + enc(u)), getJSON("/api/me/widget/library?user=" + enc(u))]).then(function (r) {
      var answers = (r[0] && r[0].answers) || [], lib = (r[1] && r[1].library) || [], seen = {}, rows = [];
      answers.forEach(function (a) {
        // only finished questions: a half-typed test fragment is not an example
        if (a.status === "answered" && /\?\s*$/.test(a.question) && !seen[a.question] && rows.length < 3) {
          seen[a.question] = 1;
          rows.push({ tag: "Answered before", q: a.question, hint: "Opens the stored answer. No model call.", run: function () { go("/?user=" + enc(u) + "&a=" + a.audit_id); } });
        }
      });
      lib.filter(function (l) { return l.edition && l.edition[2]; }).slice(0, 2).forEach(function (l) {
        var stub = "What does the " + l.title + " say about ";
        rows.push({ tag: "Finish the sentence", q: stub, hint: "Fills the box. You press Ask.", run: function () { setQ(stub); } });
      });
      if (!rows.length) return;
      box.appendChild(el("h3", "rah-h", "Try one"));
      rows.forEach(function (x) {
        var b = el("button", "rah-ex1"); b.type = "button";
        b.appendChild(el("span", "rah-tag", x.tag)); b.appendChild(el("span", "rah-q", x.q)); b.appendChild(el("span", "rah-hint", x.hint));
        b.onclick = function () { closePanel(); x.run(); };
        box.appendChild(b);
      });
    });
  }
  function openPanel(from) {
    if (!panel) buildPanel();
    opener = from || document.activeElement; fill(); panel.classList.add("open");
    var first = panel.querySelector(".rah-go") || panel.querySelector("button"); if (first) first.focus();
  }
  function closePanel() { if (panel && panel.classList.contains("open")) { panel.classList.remove("open"); if (opener && opener.focus) opener.focus(); } }

  var toastEl;
  function toast(text, plain) {
    if (toastEl) toastEl.remove();
    toastEl = el("div", "rah-toast"); toastEl.setAttribute("role", "status"); toastEl.appendChild(el("span", "", text));
    if (!plain) {
      var yes = el("button", "rah-go", "Show me around"), no = el("button", "rah-link", "Not now");
      yes.type = "button"; no.type = "button";
      yes.onclick = function () { toastEl.remove(); startTour(); }; no.onclick = function () { remember(); toastEl.remove(); };
      toastEl.appendChild(yes); toastEl.appendChild(no);
    } else setTimeout(function () { if (toastEl) toastEl.remove(); }, 4000);
    document.body.appendChild(toastEl);
  }

  function boot() {
    launcher = el("button", "rah-launch", "Help"); launcher.type = "button"; launcher.setAttribute("aria-label", "Help and tour");
    launcher.onclick = function () { panel && panel.classList.contains("open") ? closePanel() : openPanel(launcher); };
    document.body.appendChild(launcher);
    document.addEventListener("keydown", function (e) {
      var t = e.target, typing = t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.tagName === "SELECT" || t.isContentEditable);
      if (e.key === "Escape") { if (tour) endTour(); else closePanel(); }
      else if (e.key === "?" && !typing && !e.metaKey && !e.ctrlKey) { e.preventDefault(); panel && panel.classList.contains("open") ? closePanel() : openPanel(launcher); }
    });
    // deep links the board uses to reach the chat's Find tab
    if (page() === "chat" && params.get("tab") === "find" && !params.get("q")) { click("#findBtn"); focusQ(); }
    var mode = (location.hash.match(/helper=(\w+)/) || [])[1];
    if (mode === "open") openPanel(launcher);
    else if (mode === "tour") setTimeout(startTour, 400);
    else if (!remembered() && page() !== "other") setTimeout(function () { toast("New here? A one-minute look at where things are."); }, 1200);
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot); else boot();
})();
