"use strict";
// Chain Research interface: new investigation, list, selected investigation with live events, settings.

const $ = (id) => document.getElementById(id);
const ANSWER_STATUSES = ["verified", "supported", "contested", "uncertain"];
const LABELS = { waiting: "waiting", in_progress: "in progress", finished: "finished", stopped: "stopped",
  interrupted: "interrupted", error: "error", answer: "found", not_found: "not found" };
let selected = null;     // id of the investigation shown
let stream = null;       // EventSource of the events
let reload = null;       // state reload, at most one per second
let settings = null;
let shownLimits = null;  // limits of the investigation shown

function make(tag, text, cssClass) {
  const e = document.createElement(tag);
  if (text !== undefined && text !== null) e.textContent = text;
  if (cssClass) e.className = cssClass;
  return e;
}

function notice(text, isError = false) {
  const box = $("notice");
  box.textContent = text;
  box.className = "notice" + (isError ? " error" : "");
  box.hidden = false;
  clearTimeout(notice.timer);
  notice.timer = setTimeout(() => { box.hidden = true; }, isError ? 8000 : 4000);
}

async function api(path, data) {
  const options = data === undefined ? {} : { method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data) };
  const response = await fetch(path, options);
  const json = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(json.error || `Error ${response.status}`);
  return json;
}

async function attempt(action, success) {
  try {
    const outcome = await action();
    if (success) notice(success);
    return outcome;
  } catch (e) {
    notice(e.message, true);
    return null;
  }
}

// Only **bold** from the answer text: everything else stays text (no HTML from the answers).
function withBold(text) {
  const p = make("div", null, "answer");
  String(text || "").split(/(\*\*[^*]+\*\*)/).forEach((piece) => {
    if (/^\*\*[^*]+\*\*$/.test(piece)) p.append(make("strong", piece.slice(2, -2)));
    else if (piece) p.append(document.createTextNode(piece));
  });
  return p;
}

function line(text, label) {
  const m = new RegExp(`\\*\\*${label}:?\\*\\*:?\\s*(.+)`).exec(text || "");
  return m ? m[1].trim() : "";
}

function dollars(n) { return `$${(n || 0).toFixed(2)}`; }
function duration(seconds) {
  const m = Math.round((seconds || 0) / 60);
  return m >= 60 ? `${Math.floor(m / 60)} h ${m % 60} min` : `${m} min`;
}
function date(iso) {
  try { return new Date(iso).toLocaleString("en-GB", { dateStyle: "medium", timeStyle: "short" }); }
  catch { return iso; }
}

// ---------- new investigation ----------
function updateLimits() {
  const unlimited = $("unlimited").checked;
  $("budget").disabled = unlimited;
  $("minutes").disabled = unlimited;
}
$("unlimited").addEventListener("change", updateLimits);

const UNLIMITED_WARNING = "Without limits the investigation can reach about 160 questions and last many hours. Continue?";

$("new").addEventListener("submit", async (e) => {
  e.preventDefault();
  if ($("unlimited").checked && !window.confirm(UNLIMITED_WARNING)) return;
  const outcome = await attempt(() => api("/api/investigations", {
    topic: $("topic").value, director_model: $("model").value, budget_usd: $("budget").value,
    max_minutes: $("minutes").value, without_limits: $("unlimited").checked,
  }), "Investigation started.");
  if (outcome) {
    $("topic").value = "";
    await loadList();
    show(outcome.id);
  }
});

// ---------- list ----------
async function loadList() {
  const items = await attempt(() => api("/api/investigations"));
  if (!items) return;
  $("list-empty").hidden = items.length > 0;
  $("list").replaceChildren(...items.map((v) => {
    const li = make("li");
    li.tabIndex = 0;
    li.setAttribute("aria-current", String(v.id === selected));
    li.append(make("span", v.title || v.topic, "title"));
    const meta = make("span", null, "meta");
    meta.append(make("span", LABELS[v.status] || v.status, `chip ${v.status}`), make("span", date(v.created)),
      make("span", `${v.found_items}/${v.questions} answers`), make("span", dollars(v.spent_usd)));
    li.append(meta);
    li.addEventListener("click", () => show(v.id));
    li.addEventListener("keydown", (ev) => { if (ev.key === "Enter") show(v.id); });
    return li;
  }));
}

// ---------- selected investigation ----------
function show(id) {
  selected = id;
  shownLimits = null;                 // those of the previous investigation do not apply
  $("b-resume").hidden = true;        // shown once the status of this investigation has arrived
  document.querySelectorAll("#list li").forEach((li) => li.setAttribute("aria-current", "false"));
  $("none").hidden = true;
  $("view").hidden = false;
  $("v-activity").replaceChildren();
  if (stream) stream.close();
  load();
  stream = new EventSource(`/api/investigations/${encodeURIComponent(id)}/events`);
  stream.onmessage = (m) => {
    let ev;
    try { ev = JSON.parse(m.data); } catch { return; }
    activity(ev);
    if (["plan", "question", "evaluation", "end", "start", "phase"].includes(ev.kind)) loadSoon();
    if (ev.kind === "end") loadList();
  };
  // Closed only when the server asks: the stream also contains the "end" events of past runs.
  stream.addEventListener("closed", (m) => { m.target.close(); });
}

function loadSoon() {
  if (reload) return;
  reload = setTimeout(() => { reload = null; load(); }, 800);
}

function activity(ev) {
  const texts = {
    start: () => `Investigation started (director: ${ev.director_model})`,
    phase: () => ev.text,
    plan: () => `Plan ready: ${ev.plan.sections.length} sections, ${ev.questions.length} questions`,
    question: () => ev.status === "in_progress" ? `${ev.id} to the chain: ${ev.question}` :
      `${ev.id} ${LABELS[ev.status] || ev.status}${ev.answer ? ": " + ev.answer.replace(/\*\*/g, "") : ""}`,
    question_phase: () => `${ev.id} · ${ev.text}`,
    question_page: () => `${ev.id} · read: ${ev.title || ev.url}`,
    evaluation: () => `Evaluation of round ${ev.round}: ${ev.solid_knowledge ? "solid knowledge" : `${ev.questions.length} new questions`} — ${ev.reason}`,
    document: () => ev.text,
    end: () => `End (${LABELS[ev.status] || ev.status}): ${ev.reason || ""}`,
  };
  const text = texts[ev.kind]?.();
  if (!text) return;
  const li = make("li", `${(ev.when || "").slice(11, 16)}  ${text}`);
  const list = $("v-activity");
  list.prepend(li);
  while (list.children.length > 300) list.lastChild.remove();
}

async function load() {
  if (!selected) return;
  const id = selected;
  const s = await attempt(() => api(`/api/investigations/${encodeURIComponent(id)}`));
  if (!s || id !== selected) return;
  const questions = s.questions || [];
  const found = questions.filter((d) => d.status === "answer").length;
  const spent = (s.director_spent_usd || 0) + questions.reduce((t, d) => t + (d.cost_usd || 0), 0);
  $("v-date").textContent = `${date(s.created)} · director ${s.director_model}`;
  $("v-title").textContent = s.plan?.title || "Preparing the plan…";
  $("v-topic").textContent = s.topic;
  $("v-status").textContent = LABELS[s.status] || s.status;
  $("v-status").className = `chip ${s.status}`;
  const limit = s.limits.without_limits ? "no limit" : `${dollars(s.limits.budget_usd)} · ${s.limits.max_minutes} min`;
  $("v-figures").replaceChildren(...[[s.round || 0, "round"], [`${found}/${questions.length}`, "answers found"],
    [dollars(spent), "Claude spend"], [duration(s.seconds), "duration"], [limit, "limits"]].map(([v, t]) => {
    const d = make("div");
    d.append(make("b", String(v)), make("span", t));
    return d;
  }));
  $("v-phase").textContent = s.status === "in_progress" ? s.phase : (s.end_reason ? `Finished: ${s.end_reason}` : "");
  const active = s.status === "in_progress";
  $("b-open").hidden = !s.document?.pdf;
  $("b-stop").hidden = !active;
  $("b-resume").hidden = active || s.status === "finished";
  $("b-document").hidden = active || !questions.some((d) => d.status === "answer");
  $("b-clean").hidden = active;
  shownLimits = s.limits;
  sections(s);
  questionList(s);
}

function sections(s) {
  const evaluations = s.evaluations || [];
  const last = evaluations[evaluations.length - 1];
  const coverages = Object.fromEntries((last?.sections || []).map((x) => [x.id, x]));
  $("v-sections-box").hidden = !s.plan;
  if (!s.plan) return;
  $("v-sections").replaceChildren(...s.plan.sections.map((x) => {
    const li = make("li");
    const c = coverages[x.id];
    const bar = make("div", null, "coverage");
    bar.setAttribute("aria-label", c ? `coverage ${c.coverage} of 3` : "not evaluated yet");
    for (let i = 0; i < 3; i++) bar.append(make("i", null, c && i < c.coverage ? "full" : ""));
    li.append(make("span", x.title), bar);
    if (c?.note) li.append(make("span", c.note, "section-note"));
    return li;
  }));
  $("v-evaluation").textContent = last ? `Round ${last.round}: ${last.reason}` : "";
}

function questionList(s) {
  const byRound = {};
  for (const d of s.questions || []) (byRound[d.round] ||= []).push(d);
  const titles = Object.fromEntries((s.plan?.sections || []).map((x) => [x.id, x.title]));
  $("v-questions").replaceChildren(...Object.keys(byRound).sort((a, b) => b - a).map((round) => {
    const box = make("div", null, "round");
    box.append(make("h3", `Round ${round}`));
    for (const d of byRound[round]) {
      const r = make("div", null, "question");
      // The answer status is decided by the program's checks; without it (older investigations) the outcome stays.
      const status = d.status === "answer" && ANSWER_STATUSES.includes(d.answer_status) ? d.answer_status : "";
      r.append(make("span", d.question, "text"),
        make("span", status || LABELS[d.status] || d.status, `chip ${status || d.status}`));
      const answer = line(d.answer, "Answer");
      if (answer) r.append(withBold(answer));
      const info = [titles[d.section], d.cost_usd ? dollars(d.cost_usd) : "", d.seconds ? duration(d.seconds) : "",
        status ? (d.status_reasons || []).join("; ")
          : line(d.answer, "Confidence") ? `confidence ${line(d.answer, "Confidence").split(/[,.—]/)[0].trim()}` : "",
        d.error || ""].filter(Boolean).join(" · ");
      if (info) r.append(make("div", info, "info"));
      box.append(r);
    }
    return box;
  }));
}

for (const [button, action, message] of [["b-stop", "stop", "Stopping the investigation…"],
  ["b-document", "document", "Writing the document…"], ["b-open", "open", null], ["b-folder", "folder", null],
  ["b-clean", "clean", "Page pieces removed."]]) {
  $(button).addEventListener("click", async () => {
    if (!selected) return;
    if (action === "clean" && !window.confirm("Remove the page pieces read by this investigation?")) return;
    const outcome = await attempt(() => api(`/api/investigations/${encodeURIComponent(selected)}/${action}`, {}),
      message);
    if (outcome && action === "document") show(selected);  // reopens the event stream
    loadSoon();
    loadList();
  });
}

$("b-resume").addEventListener("click", () => {
  if (!selected || !shownLimits) return;
  $("r-budget").value = shownLimits.budget_usd;
  $("r-minutes").value = shownLimits.max_minutes;
  $("r-unlimited").checked = shownLimits.without_limits;
  $("resume").showModal();
});

$("form-resume").addEventListener("submit", async (e) => {
  if (e.submitter?.value !== "resume") return;
  e.preventDefault();
  if ($("r-unlimited").checked && !window.confirm(UNLIMITED_WARNING)) return;
  const outcome = await attempt(() => api(`/api/investigations/${encodeURIComponent(selected)}/resume`, {
    budget_usd: $("r-budget").value, max_minutes: $("r-minutes").value, without_limits: $("r-unlimited").checked,
  }), "Investigation resumed.");
  if (outcome) {
    $("resume").close();
    show(selected);  // reopens the event stream
    loadList();
  }
});

// ---------- settings ----------
async function loadSettings() {
  settings = await attempt(() => api("/api/settings"));
  if (!settings) return;
  $("model").value = settings.director_model;
  $("budget").value = settings.budget_usd;
  $("minutes").value = settings.max_minutes;
  $("unlimited").checked = settings.without_limits;
  updateLimits();
}

$("open-settings").addEventListener("click", async () => {
  await loadSettings();
  if (!settings) return;
  const w = settings.wikipedia;
  $("s-model").value = settings.director_model;
  $("s-budget").value = settings.budget_usd;
  $("s-minutes").value = settings.max_minutes;
  $("s-unlimited").checked = settings.without_limits;
  $("s-parallel").value = settings.parallel_questions;
  $("s-chain-model").value = settings.chain_model;
  $("s-review-model").value = settings.review_model;
  $("s-question-budget").value = settings.question_budget_usd;
  $("s-question-pages").value = settings.question_pages;
  $("s-question-minutes").value = settings.question_minutes;
  $("s-wiki-active").checked = w.active;
  $("s-wiki-contact").value = w.contact;
  $("s-wiki-contact").placeholder = w.origin === "account" ? w.in_use : "";
  $("settings").showModal();
});

$("form-settings").addEventListener("submit", async (e) => {
  if (e.submitter?.value !== "save") return;
  e.preventDefault();
  const outcome = await attempt(() => api("/api/settings", {
    director_model: $("s-model").value, budget_usd: $("s-budget").value, max_minutes: $("s-minutes").value,
    without_limits: $("s-unlimited").checked, parallel_questions: $("s-parallel").value,
    chain_model: $("s-chain-model").value, review_model: $("s-review-model").value,
    question_budget_usd: $("s-question-budget").value,
    question_pages: $("s-question-pages").value, question_minutes: $("s-question-minutes").value,
    wikipedia: { active: $("s-wiki-active").checked, contact: $("s-wiki-contact").value },
  }), "Settings saved.");
  if (outcome) {
    $("settings").close();
    loadSettings();
  }
});

loadSettings();
loadList().then(() => {
  const first = document.querySelector("#list li");
  if (first) first.click();
});
setInterval(loadList, 15000);
