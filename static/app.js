/* SOP Desk front end — plain JavaScript, no build step. */
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
function h(tag, attrs = {}, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null || v === false) continue;
    if (k === "class") el.className = v;
    else if (k === "html") el.innerHTML = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const kid of kids.flat()) if (kid != null && kid !== false) el.append(kid.nodeType ? kid : String(kid));
  return el;
}
const store = {
  get(k) { try { return localStorage.getItem(k); } catch { return null; } },
  set(k, v) { try { v == null ? localStorage.removeItem(k) : localStorage.setItem(k, v); } catch {} },
};
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const wide = () => window.matchMedia("(min-width: 961px)").matches;

const S = { config: null, docs: [], history: [], busy: false, code: store.get("sopdesk-code") || "", currentDoc: null };

/* ---------- API ---------- */
async function api(path, opts = {}) {
  const headers = { ...(opts.headers || {}) };
  if (S.code) headers["X-Access-Code"] = S.code;
  if (opts.json !== undefined) { headers["Content-Type"] = "application/json"; opts.body = JSON.stringify(opts.json); }
  const res = await fetch(path, { ...opts, headers });
  if (res.status === 401) { showGate(true); throw new Error("Access code required."); }
  if (!res.ok) {
    let msg = `Request failed (${res.status}).`;
    try { const j = await res.json(); msg = typeof j.detail === "string" ? j.detail : (j.detail?.[0]?.msg || msg); } catch {}
    throw new Error(msg);
  }
  return opts.raw ? res : res.json();
}
function toast(msg) {
  const t = $("#toast"); t.textContent = msg; t.hidden = false;
  clearTimeout(toast.t); toast.t = setTimeout(() => (t.hidden = true), 3200);
}

/* ---------- theme ---------- */
function applyTheme(t) { if (t) document.documentElement.setAttribute("data-theme", t); else document.documentElement.removeAttribute("data-theme"); }
applyTheme(store.get("sopdesk-theme"));
$("#theme-btn").addEventListener("click", () => {
  const cur = document.documentElement.getAttribute("data-theme") || (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
  const next = cur === "dark" ? "light" : "dark";
  applyTheme(next); store.set("sopdesk-theme", next);
});

/* ---------- views and panes ---------- */
function showView(v) {
  $$(".views button").forEach(b => b.setAttribute("aria-selected", String(b.dataset.view === v)));
  ["desk", "queue", "insights"].forEach(x => ($("#view-" + x).hidden = x !== v));
  if (v === "queue") loadTickets();
  if (v === "insights") loadStats();
}
$$(".views button").forEach(b => b.addEventListener("click", () => showView(b.dataset.view)));
function showPane(p) {
  $$(".desk-switch button").forEach(b => b.setAttribute("aria-selected", String(b.dataset.pane === p)));
  ["library", "chat", "evidence"].forEach(x => $("#pane-" + x).classList.toggle("show", x === p));
}
$$(".desk-switch button").forEach(b => b.addEventListener("click", () => showPane(b.dataset.pane)));
showPane("chat");

/* ---------- access gate ---------- */
function showGate(err) {
  $("#gate").hidden = false; $("#gate-err").hidden = !err || !S.code; setTimeout(() => $("#gate-code").focus(), 50);
}
$("#gate-form").addEventListener("submit", async e => {
  e.preventDefault();
  S.code = $("#gate-code").value.trim();
  try { await api("/api/access", { method: "POST" }); store.set("sopdesk-code", S.code); $("#gate").hidden = true; boot(); }
  catch { $("#gate-err").hidden = false; }
});

/* ---------- library ---------- */
const KIND = { upload: "Uploaded", sample: "Sample", sme: "Expert" };
async function loadDocs() {
  const { documents } = await api("/api/documents");
  S.docs = documents;
  const sections = documents.reduce((n, d) => n + (d.section_count || 0), 0);
  $("#lib-meta").textContent = `${documents.length} docs · ${sections} sections`;
  renderDocs();
}
function renderDocs() {
  const f = $("#doc-filter").value.trim().toLowerCase();
  const list = $("#doc-list"); list.replaceChildren();
  const docs = S.docs.filter(d => !f || `${d.doc_id} ${d.title} ${d.summary}`.toLowerCase().includes(f));
  if (!docs.length) list.append(h("li", { class: "muted", style: "font-size:13.5px;padding:8px 0" }, f ? "No documents match." : "No documents yet. Drop a file above."));
  for (const d of docs) {
    const del = h("button", { class: "del", title: "Remove document", "aria-label": `Remove ${d.title}` }, "Remove");
    del.addEventListener("click", async ev => {
      ev.stopPropagation();
      if (!del.classList.contains("confirm")) { del.classList.add("confirm"); del.textContent = "Sure?"; setTimeout(() => { del.classList.remove("confirm"); del.textContent = "Remove"; }, 3000); return; }
      try { await api(`/api/documents/${encodeURIComponent(d.doc_id)}`, { method: "DELETE" }); toast(`Removed ${d.doc_id}`); loadDocs(); loadSuggestions(); }
      catch (e) { toast(e.message); }
    });
    const li = h("li", { class: `doc k-${d.kind}${S.currentDoc === d.doc_id ? " cur" : ""}`, tabindex: "0", role: "button", "aria-label": `Open ${d.title}` },
      h("span", { class: "spine" }),
      h("span", { class: "id" }, d.doc_id, "  ", h("span", { class: `badge ${d.kind}` }, KIND[d.kind] || d.kind), `  ${d.section_count} §`),
      del,
      h("span", { class: "t" }, d.title),
      d.summary ? h("span", { class: "s" }, d.summary) : null);
    li.addEventListener("click", () => openDoc(d.doc_id));
    li.addEventListener("keydown", e => { if (e.key === "Enter") openDoc(d.doc_id); });
    list.append(li);
  }
}
$("#doc-filter").addEventListener("input", renderDocs);

const tray = $("#tray");
["dragenter", "dragover"].forEach(ev => tray.addEventListener(ev, e => { e.preventDefault(); tray.classList.add("drag"); }));
["dragleave", "drop"].forEach(ev => tray.addEventListener(ev, e => { e.preventDefault(); tray.classList.remove("drag"); }));
tray.addEventListener("drop", e => uploadFiles([...e.dataTransfer.files]));
$("#file-input").addEventListener("change", e => { uploadFiles([...e.target.files]); e.target.value = ""; });

async function uploadFiles(files) {
  if (!files.length) return;
  const ul = $("#uploads");
  const rows = files.map(f => { const li = h("li", {}, h("span", { class: "spinner" }), `Reading ${f.name}…`); ul.prepend(li); return [f.name, li]; });
  const fd = new FormData(); files.forEach(f => fd.append("files", f));
  try {
    const res = await api("/api/documents", { method: "POST", body: fd });
    const added = Object.fromEntries(res.added.map(d => [d.source, d]));
    for (const [name, li] of rows) {
      const d = added[name];
      if (d) { li.className = "ok"; li.replaceChildren(`✓ ${name} → ${d.section_count} sections`); }
      else { li.className = "err"; li.replaceChildren(res.errors.find(e => e.includes(name)) || `${name} couldn't be added.`); }
    }
    await loadDocs();
    if (res.added.length) {
      openDoc(res.added[0].doc_id);
      setTimeout(() => { loadDocs(); loadSuggestions(); }, 6000);       // the LLM adds a summary + sample questions
    }
  } catch (e) { rows.forEach(([n, li]) => { li.className = "err"; li.replaceChildren(`${n}: ${e.message}`); }); }
  setTimeout(() => rows.forEach(([, li]) => li.classList.contains("ok") && li.remove()), 9000);
}
$("#reset-lib").addEventListener("click", async e => {
  const b = e.currentTarget;
  if (!b.dataset.armed) { b.dataset.armed = "1"; b.textContent = "Click again: removes uploads, restores the 8 samples"; setTimeout(() => { delete b.dataset.armed; b.textContent = "Restore sample library"; }, 4000); return; }
  delete b.dataset.armed; b.textContent = "Restore sample library";
  try { await api("/api/documents/reset", { method: "POST" }); await loadDocs(); loadSuggestions(); toast("Sample library restored"); } catch (err) { toast(err.message); }
});

/* ---------- evidence ---------- */
async function openDoc(docId, sectionId) {
  try {
    const d = await api(`/api/documents/${encodeURIComponent(docId)}`);
    S.currentDoc = docId; renderDocs();
    const art = $("#ev-doc");
    const meta = [d.owner && `Owner: ${d.owner}`, d.version, d.updated && `Updated ${d.updated}`, d.source].filter(Boolean);
    art.replaceChildren(...[
      h("div", { class: "ev-top" }, h("span", { class: "id" }, d.doc_id), h("span", { class: `badge ${d.kind}` }, KIND[d.kind] || d.kind)),
      h("h3", {}, d.title),
      h("div", { class: "ev-meta" }, ...meta.map(m => h("span", {}, m)), h("span", {}, `${d.section_count} sections · ${d.word_count} words`)),
      d.summary ? h("p", { class: "ev-summary" }, d.summary) : null,
      ...d.sections.map(s => h("div", { class: `ev-sec${s.id === sectionId ? " hl" : ""}`, id: `sec-${s.id}` },
        h("h4", {}, s.section, h("span", { class: "mono" }, s.id)), h("p", {}, s.text)))].filter(Boolean));
    art.hidden = false; $("#ev-empty").hidden = true;
    if (!wide()) showPane("evidence");
    const target = sectionId && document.getElementById(`sec-${sectionId}`);
    requestAnimationFrame(() => target ? target.scrollIntoView({ block: "start", behavior: "smooth" }) : ($("#pane-evidence").scrollTop = 0));
  } catch (e) { toast(e.message); }
}

/* ---------- chat ---------- */
const ICON = {
  search_sops: '<svg viewBox="0 0 24 24"><circle cx="10.5" cy="10.5" r="6"/><path d="M15 15l5 5"/></svg>',
  read_section: '<svg viewBox="0 0 24 24"><path d="M6 3h9l4 4v14H6z"/><path d="M9 11h7M9 15h7"/></svg>',
  escalate_to_sme: '<svg viewBox="0 0 24 24"><path d="M5 21V4h11l-2 4 2 4H5"/></svg>',
  done: '<svg viewBox="0 0 24 24"><path d="M5 12.5l4.5 4.5L19 7"/></svg>',
  error: '<svg viewBox="0 0 24 24"><path d="M6 6l12 12M18 6L6 18"/></svg>',
};
const CLS = { search_sops: "", read_section: "read", escalate_to_sme: "esc" };
function stop(kind, label, detail, extra = "") {
  return h("span", { class: `stop ${extra}` }, h("span", { class: "ic", html: ICON[kind] || "" }),
    h("span", { class: "lb", title: label }, label), detail ? h("span", { class: "dt" }, detail) : null);
}

async function loadSuggestions() {
  try {
    const { suggestions } = await api("/api/suggestions");
    $("#suggest").replaceChildren(...suggestions.map(s => h("button", { type: "button", onclick: () => ask(s.question) }, s.question)));
  } catch {}
}

const box = $("#q");
box.addEventListener("input", () => { box.style.height = "auto"; box.style.height = Math.min(box.scrollHeight, 160) + "px"; });
box.addEventListener("keydown", e => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); $("#composer").requestSubmit(); } });
$("#composer").addEventListener("submit", e => { e.preventDefault(); const q = box.value.trim(); if (q.length > 1) { box.value = ""; box.style.height = "auto"; ask(q); } });

async function ask(question) {
  if (S.busy) return;
  S.busy = true; $("#send").disabled = true;
  if (!wide()) showPane("chat");
  $("#welcome").hidden = true;
  const thread = $("#thread");
  thread.append(h("div", { class: "msg-q" }, question));
  const trail = h("div", { class: "trail", "aria-label": "What the agent did" });
  const think = stop("", "Thinking…", "", "think");
  trail.append(think);
  const wrap = h("div", { class: "msg-a" }, trail);
  thread.append(wrap);
  wrap.scrollIntoView({ block: "end" });
  const addStop = el => { if (think.parentNode === trail) trail.insertBefore(el, think); else trail.append(el); };

  try {
    const res = await api("/api/chat", { method: "POST", json: { question, history: S.history.slice(-2) }, raw: true });
    const reader = res.body.getReader(), dec = new TextDecoder();
    let buf = "", final = null;
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      let i;
      while ((i = buf.indexOf("\n\n")) >= 0) {
        const chunk = buf.slice(0, i); buf = buf.slice(i + 2);
        for (const line of chunk.split("\n")) {
          if (!line.startsWith("data: ")) continue;
          const ev = JSON.parse(line.slice(6));
          if (ev.type === "step") { addStop(stop(ev.tool, ev.label, ev.detail, CLS[ev.tool] || "")); wrap.scrollIntoView({ block: "end" }); }
          else if (ev.type === "final") final = ev.result;
          else if (ev.type === "error") throw new Error(ev.message);
        }
      }
    }
    think.remove();
    if (!final) throw new Error("The answer was cut off. Please try again.");
    if (final.status === "answered") addStop(stop("done", "Answered", `${final.seconds}s`, "done"));
    else if (final.status === "error") addStop(stop("error", "Stopped", "", "esc"));
    wrap.append(answerCard(final));
    S.history.push({ question, answer: final.answer || "" });
    if (final.status === "escalated") refreshQueueCount();
    if (final.citations?.length && wide()) openDoc(final.citations[0].doc_id, final.citations[0].section_id);
  } catch (e) {
    think.remove();
    wrap.append(h("div", { class: "answer-card err" }, h("span", { class: "status err" }, "Couldn't answer"), h("div", { class: "answer" }, e.message)));
  } finally {
    S.busy = false; $("#send").disabled = false; wrap.scrollIntoView({ block: "end", behavior: "smooth" }); box.focus();
  }
}

function answerCard(r) {
  const nums = Object.fromEntries((r.citations || []).map((c, i) => [c.section_id, i + 1]));
  const byId = Object.fromEntries((r.citations || []).map(c => [c.section_id, c]));
  const html = esc(r.answer)
    .replace(/\[([A-Z0-9][A-Z0-9-]*-S\d{2,})\]/g, (m, id) => nums[id] ? `<button class="cite" data-id="${id}" title="${esc(byId[id].doc_id + " › " + byId[id].heading)}">${nums[id]}</button>` : "")
    .split(/\n+/).filter(p => p.trim()).map(p => `<p>${p}</p>`).join("");
  const answer = h("div", { class: "answer", html });
  answer.addEventListener("click", e => { const b = e.target.closest(".cite"); if (b) { const c = byId[b.dataset.id]; openDoc(c.doc_id, c.section_id); } });

  const status = { answered: ["ok", "Answered from your documents"], escalated: ["esc", "Sent to an expert"], error: ["err", "Couldn't answer"] }[r.status] || ["err", r.status];
  const card = h("div", { class: `answer-card ${status[0] === "ok" ? "" : status[0]}` },
    h("span", { class: `status ${status[0]}` }, status[1]), answer);

  if (r.citations?.length) {
    card.append(h("div", { class: "sources" }, ...r.citations.map((c, i) =>
      h("button", { class: "src", type: "button", onclick: () => openDoc(c.doc_id, c.section_id) },
        h("span", { class: "n" }, i + 1), h("span", { class: "d" }, h("b", {}, c.doc_id), " ", h("span", {}, `${c.title} › ${c.heading}`))))));
  }
  if (r.ticket) card.append(h("div", { class: "ticket-note" }, `Ticket ${r.ticket} is in the expert queue.`,
    h("button", { class: "rate", type: "button", onclick: () => showView("queue") }, "Open queue")));
  if (r.note) card.append(h("div", { class: "note" }, r.note));

  const foot = h("div", { class: "foot" }, `${r.steps.length} step${r.steps.length === 1 ? "" : "s"} · ${r.seconds}s${r.mode === "retrieval-only" ? " · retrieval-only" : ""}`, h("span", { class: "sp" }));
  if (r.status === "answered") {
    const mk = (val, label) => h("button", { class: "rate", type: "button", "aria-pressed": "false", onclick: async e => {
      $$(".rate", foot).forEach(b => { b.classList.remove("on"); b.setAttribute("aria-pressed", "false"); });
      e.currentTarget.classList.add("on"); e.currentTarget.setAttribute("aria-pressed", "true");
      try { await api("/api/feedback", { method: "POST", json: { question: r.question, answer: r.answer, rating: val, citations: r.citations.map(c => c.section_id) } }); toast("Thanks for the feedback"); } catch (err) { toast(err.message); }
    } }, label);
    foot.append(mk("helpful", "Helpful"), mk("not_helpful", "Not helpful"));
  }
  card.append(foot);
  return card;
}

/* ---------- expert queue ---------- */
async function refreshQueueCount() {
  try {
    const { tickets } = await api("/api/tickets");
    const open = tickets.filter(t => t.status === "open").length;
    const c = $("#queue-count"); c.hidden = !open; c.textContent = open;
    return tickets;
  } catch { return []; }
}
async function loadTickets() {
  const tickets = await refreshQueueCount();
  const box = $("#tickets"); box.replaceChildren();
  if (!tickets.length) { box.append(h("div", { class: "empty-state" }, "No questions waiting. When the documents can't answer something, it lands here.")); return; }
  for (const t of tickets) {
    const when = new Date(t.asked_at).toLocaleString([], { dateStyle: "medium", timeStyle: "short" });
    const card = h("div", { class: "tk" }, h("div", { class: "tk-head" }, h("span", { class: "tk-q" }, t.question), h("span", { class: `pill ${t.status}` }, t.status)),
      h("span", { class: "tk-meta" }, `${t.ticket} · ${when} · ${t.reason}`));
    if (t.status === "open") {
      const ta = h("textarea", { id: `ans-${t.ticket}`, placeholder: "Write the expert answer. It will be added to the library and cited in future answers.", "aria-label": "Expert answer" });
      const btn = h("button", { class: "btn primary", type: "button" }, "Publish answer");
      btn.addEventListener("click", async () => {
        if (ta.value.trim().length < 3) { ta.focus(); return; }
        btn.disabled = true;
        try { const r = await api(`/api/tickets/${t.ticket}/answer`, { method: "POST", json: { answer: ta.value.trim() } }); toast(`Published as ${r.section_id}. It's now searchable.`); loadTickets(); loadDocs(); }
        catch (e) { toast(e.message); btn.disabled = false; }
      });
      card.append(ta, h("div", {}, btn));
    } else card.append(h("div", { class: "tk-answer" }, t.answer));
    box.append(card);
  }
}

/* ---------- insights ---------- */
async function loadStats() {
  const s = await api("/api/stats");
  const v = (x, suf = "") => (x == null ? "–" : `${x}${suf}`);
  const kpi = (l, val, hint) => h("div", { class: "kpi" }, h("div", { class: "l" }, l), h("div", { class: "v" }, val), h("div", { class: "h" }, hint));
  $("#kpis").replaceChildren(
    kpi("Questions", v(s.questions), `${s.documents} documents in the library`),
    kpi("Answered from docs", v(s.answered_pct, "%"), "with a verified source"),
    kpi("Sent to experts", v(s.escalated), `${s.open_tickets} still open`),
    kpi("Rated helpful", v(s.helpful_pct, "%"), `${s.ratings} ratings`),
    kpi("Avg response", v(s.avg_seconds, "s"), `${v(s.avg_steps)} agent steps`),
    kpi("Tokens used", s.tokens ? s.tokens.toLocaleString() : "–", "LLM usage so far"));
  const max = Math.max(1, ...s.top_documents.map(d => d.count));
  $("#top-docs").replaceChildren(...(s.top_documents.length ? s.top_documents.map(d => h("li", {},
    h("span", {}, h("b", { class: "mono" }, d.doc_id), " ", d.title), h("span", { class: "c" }, d.count),
    h("span", { class: "bar" }, h("span", { style: `width:${(100 * d.count / max).toFixed(0)}%` })))) : [h("li", { class: "muted" }, "Citations will show here once questions are answered.")]));
  $("#recent").replaceChildren(...(s.recent.length ? s.recent.map(q => h("li", {}, h("span", {}, q.question),
    h("span", { class: `pill ${q.status === "answered" ? "answered" : "open"}` }, q.status))) : [h("li", { class: "muted" }, "No questions yet.")]));
}
async function showEval(promise) {
  const out = $("#eval-out"); out.replaceChildren(h("p", { class: "muted" }, "Running…"));
  try {
    const r = await promise, s = r.summary;
    const pct = x => (x == null ? "–" : x + "%");
    const mk = v => (v === null ? h("span", { class: "muted" }, "n/a") : h("span", { class: v ? "y" : "n" }, v ? "Yes" : "No"));
    out.replaceChildren(...[
      h("div", { class: "kpis", style: "margin-top:14px" },
        h("div", { class: "kpi" }, h("div", { class: "l" }, "Right doc first"), h("div", { class: "v" }, pct(s.top1)), h("div", { class: "h" }, `${s.in_scope} in-scope questions`)),
        h("div", { class: "kpi" }, h("div", { class: "l" }, "Right doc in top 3"), h("div", { class: "v" }, pct(s.top3)), h("div", { class: "h" }, "search recall")),
        h("div", { class: "kpi" }, h("div", { class: "l" }, "Correct refusals"), h("div", { class: "v" }, pct(s.refusals)), h("div", { class: "h" }, `${s.out_of_scope} out-of-scope`)),
        h("div", { class: "kpi" }, h("div", { class: "l" }, "Overall correct"), h("div", { class: "v" }, pct(s.overall)), h("div", { class: "h" }, `${s.questions} questions`))),
      r.missing_documents.length ? h("p", { class: "note" }, `Not in the library: ${r.missing_documents.join(", ")}. Those questions can't pass until the documents are added.`) : null,
      h("div", { class: "tablewrap" }, h("table", {},
        h("thead", {}, h("tr", {}, ...["Question", "Expected", "Top result", "Match", "Right first", "Correct"].map(x => h("th", {}, x)))),
        h("tbody", {}, ...r.rows.map(x => h("tr", { class: x.correct ? "" : "fail" }, h("td", {}, x.question), h("td", { class: "num" }, x.expected),
          h("td", {}, h("span", { class: "mono" }, x.top_doc || "none"), h("div", { class: "muted", style: "font-size:12.5px" }, x.top_section)),
          h("td", {}, x.match), h("td", {}, mk(x.top1)), h("td", {}, mk(x.correct)))))))].filter(Boolean));
  } catch (e) { out.replaceChildren(h("p", { class: "note" }, e.message)); }
}
$("#eval-run").addEventListener("click", () => showEval(api("/api/evaluate")));
$("#eval-file").addEventListener("change", e => {
  const f = e.target.files[0]; if (!f) return;
  const fd = new FormData(); fd.append("file", f); e.target.value = "";
  showEval(api("/api/evaluate", { method: "POST", body: fd }));
});

/* ---------- start ---------- */
async function boot() {
  await Promise.all([loadDocs(), loadSuggestions(), refreshQueueCount()]);
}
(async function init() {
  try {
    S.config = await (await fetch("/api/config")).json();
    const eng = $("#engine");
    if (S.config.llm_ready) { eng.classList.add("on"); $("#engine-text").textContent = `${S.config.provider} · ${S.config.model}`; }
    else { eng.classList.add("off"); $("#engine-text").textContent = "Retrieval-only · no LLM key"; }
    $("#tray-sub").textContent = `PDF, Word, Markdown or text · up to ${S.config.max_upload_mb} MB each`;
    if (S.config.access_required && !S.code) return showGate(false);
    await boot();
  } catch (e) { toast(e.message || "Couldn't reach the server."); }
})();
