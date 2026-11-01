"use strict";
/* The small assistant. It arranges what is here and sends anything about a report's content to the research desk. What it does
   comes back from the server as a short list of known actions, and each one is run here so the person sees it happen. */

const chat = { history: [], busy: false };

async function runAction(a) {
  if (a.do === "filter") {
    const { do: _, ...change } = a;
    navigate(here({ ...(change.period && change.period !== "all" ? { period: change.period } : {}), ...(change.outcome && change.outcome !== "any" ? { outcome: change.outcome } : {}), ...(change.report ? { report: change.report } : {}) }));
    return null;
  }
  if (a.do === "add_topic") {
    const t = await addTopic(a.term);
    navigate(here({ topic: t.query, tab: "mentions" }));
    return null;
  }
  if (a.do === "show") {
    navigate(here(EXPLORE_TABS.includes(a.tab) ? { explore: a.tab === "history" ? "history" : a.tab } : { tab: a.tab }));
    return null;
  }
  if (a.do === "open_flow") {
    navigate(here({ view: "flow", a: a.audit_id }));
    return null;
  }
  if (a.do === "create_dashboard" || a.do === "create_report") {
    const kind = a.do === "create_report" ? "report" : "dashboard";
    await createFromAssistant(kind, a.title, a.blocks);
    return null;
  }
  if (a.do === "handoff") {
    return el("a", { class: "ghost handoff", href: toDesk(a.question ? { q: a.question } : {}) }, a.question ? "Ask this in the research desk" : "Open the research desk");
  }
  return null;
}

function say(role, ...kids) {
  const m = el("div", { class: `msg msg-${role}` }, ...kids);
  $("assistantLog").append(m);
  $("assistantLog").scrollTop = $("assistantLog").scrollHeight;
  return m;
}
function drawSuggestions(list) {
  $("assistantSuggest").replaceChildren(...list.map((text) => el("button", { type: "button", onclick: () => converse(text) }, text)));
}

async function converse(text) {
  if (chat.busy) return;
  chat.busy = true;
  $("assistantGo").disabled = true;
  say("user", text);
  drawSuggestions([]);
  const pending = say("assistant", el("span", { class: "thinking" }, "Working on it…"));
  try {
    const r = await send("/api/me/assistant", "POST", { message: text, history: chat.history });
    chat.history.push({ role: "user", text }, { role: "assistant", text: r.say });
    pending.replaceChildren(r.say);
    for (const action of r.actions) {
      const extra = await runAction(action);
      if (extra) pending.append(extra);
    }
    drawSuggestions(r.suggestions || []);
  } catch (e) {
    pending.replaceChildren(`That did not work: ${e.message}`);
  } finally {
    chat.busy = false;
    $("assistantGo").disabled = false;
  }
}

function setAssistant(open) {
  $("assistant").hidden = !open;
  $("assistantBtn").setAttribute("aria-expanded", String(open));
  document.body.classList.toggle("has-assistant", open);
  if (open) $("assistantInput").focus({ preventScroll: true });
  try { sessionStorage.setItem("ra-assistant", open ? "1" : "0"); } catch { /* private window: it just starts closed */ }
}

function startAssistant() {
  $("assistantBtn").replaceChildren(icon("chat"), el("span", {}, "Assistant"));
  $("assistantClose").append(icon("cross"));
  $("assistantMode").textContent = state.connected ? `Using ${state.model}` : "Guided mode: no AI model is connected, so it follows fixed steps";
  $("assistantBtn").onclick = () => setAssistant($("assistant").hidden);
  $("assistantClose").onclick = () => { setAssistant(false); $("assistantBtn").focus(); };
  $("assistantForm").onsubmit = (e) => {
    e.preventDefault();
    const text = $("assistantInput").value.trim();
    if (!text) return;
    $("assistantInput").value = "";
    converse(text);
  };
  say("assistant", "I can explain this page, filter it, build a dashboard or draft a report. Questions about what the reports say go to the research desk, where every claim is checked.");
  drawSuggestions(["What am I looking at?", "Build me a dashboard", "Draft a report from what I've found", "Show tariff across the editions"]);
  let open = false;
  try { open = sessionStorage.getItem("ra-assistant") === "1"; } catch { /* private window */ }
  if (open) setAssistant(true);
}
