/* ── DataScribe Pro — WebSocket Chat Client ─────────────────────────────── */

const API_BASE = `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}`;

let ws = null;
let sessionId = sessionStorage.getItem("datascribe_session") || crypto.randomUUID();
let isProcessing = false;
let currentAiBubble = null;
let currentAiText = "";
let activePlanSteps = [];

// ── Connection ────────────────────────────────────────────────────────────────
function connect() {
  ws = new WebSocket(`${API_BASE}/ws/${sessionId}`);

  ws.onopen = () => {
    setConnected(true);
    document.getElementById("dbStatus").textContent = "Connected";
  };

  ws.onclose = () => {
    setConnected(false);
    document.getElementById("dbStatus").textContent = "Disconnected — reconnecting…";
    setTimeout(connect, 2000);
  };

  ws.onerror = () => setConnected(false);

  ws.onmessage = (ev) => {
    try { handleServerMessage(JSON.parse(ev.data)); }
    catch (e) { console.error("Parse error:", e); }
  };
}

function setConnected(ok) {
  document.getElementById("connDot").classList.toggle("connected", ok);
}

// ── Message dispatcher ────────────────────────────────────────────────────────
function handleServerMessage(msg) {
  switch (msg.type) {

    case "session_info":
      sessionId = msg.session_id;
      sessionStorage.setItem("datascribe_session", sessionId);
      if (msg.provider) {
        document.getElementById("providerBadge").textContent = msg.provider.replace("_", " ");
      }
      break;

    case "status":
      showStatus(msg.text);
      break;

    case "schema_loaded":
      updateSidebarSchema(msg.tables, msg.cached);
      removeStatus();
      if (msg.text) showStatus(msg.text, false);
      break;

    case "plan":
      activePlanSteps = msg.steps;
      showPlan(msg.steps);
      break;

    case "step_start":
      markStepActive(msg.index);
      break;

    case "chunk":
      appendChunk(msg.text);
      break;

    case "done":
      finaliseAiBubble();
      showMeta(msg.model, msg.latency_ms);
      removeStatus();
      removePlan();
      isProcessing = false;
      updateSendBtn();
      break;

    case "error":
      removeStatus();
      removePlan();
      appendAiError(msg.message);
      isProcessing = false;
      updateSendBtn();
      break;
  }
}

// ── Sending ───────────────────────────────────────────────────────────────────
function sendQuestion() {
  const input = document.getElementById("questionInput");
  const question = input.value.trim();
  if (!question || isProcessing || !ws || ws.readyState !== WebSocket.OPEN) return;

  hideWelcome();
  appendUserBubble(question);
  startAiBubble();

  ws.send(JSON.stringify({ type: "query", question }));
  input.value = "";
  input.style.height = "auto";
  isProcessing = true;
  updateSendBtn();
  scrollBottom();
}

function resetSession() {
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify({ type: "reset" }));
  }
  // Clear UI
  const messages = document.getElementById("messages");
  messages.innerHTML = "";
  showWelcome();
  document.getElementById("tableList").innerHTML = '<div class="no-tables">Ask a question to discover the schema.</div>';
  document.getElementById("tableCount").textContent = "0";
  activePlanSteps = [];
}

function suggest(btn) {
  document.getElementById("questionInput").value = btn.textContent;
  sendQuestion();
}

// ── DOM helpers ───────────────────────────────────────────────────────────────
function appendUserBubble(text) {
  const row = el("div", "msg-row user");
  row.innerHTML = `
    <div class="avatar user-av">👤</div>
    <div class="bubble user">${escHtml(text)}</div>
  `;
  document.getElementById("messages").appendChild(row);
  scrollBottom();
}

function startAiBubble() {
  currentAiText = "";
  const row = el("div", "msg-row");
  row.id = "currentAiRow";
  row.innerHTML = `
    <div class="avatar ai-av">🤖</div>
    <div class="bubble ai" id="currentAiBubble">
      <div class="typing-dots"><span></span><span></span><span></span></div>
    </div>
  `;
  document.getElementById("messages").appendChild(row);
  currentAiBubble = document.getElementById("currentAiBubble");
  scrollBottom();
}

function appendChunk(text) {
  currentAiText += text;
  if (currentAiBubble) {
    currentAiBubble.innerHTML = renderMarkdown(currentAiText);
    hljs.highlightAll();
  }
  scrollBottom();
}

function finaliseAiBubble() {
  if (currentAiBubble && currentAiText) {
    currentAiBubble.innerHTML = renderMarkdown(currentAiText);
    hljs.highlightAll();
  }
  currentAiBubble = null;
  currentAiText = "";
  scrollBottom();
}

function appendAiError(message) {
  if (currentAiBubble) {
    currentAiBubble.innerHTML = `<span style="color:var(--red)">⚠️ ${escHtml(message)}</span>`;
    currentAiBubble = null;
  } else {
    const row = el("div", "msg-row");
    row.innerHTML = `
      <div class="avatar ai-av">🤖</div>
      <div class="bubble ai"><span style="color:var(--red)">⚠️ ${escHtml(message)}</span></div>
    `;
    document.getElementById("messages").appendChild(row);
  }
  scrollBottom();
}

// ── Status / plan indicators ──────────────────────────────────────────────────
let statusEl = null;

function showStatus(text, spinner = true) {
  removeStatus();
  statusEl = el("div", "status-row");
  statusEl.id = "statusRow";
  statusEl.innerHTML = spinner
    ? `<div class="spinner"></div><span>${escHtml(text)}</span>`
    : `<span style="color:var(--green)">✓</span><span>${escHtml(text)}</span>`;
  document.getElementById("messages").appendChild(statusEl);
  scrollBottom();
}

function removeStatus() {
  const s = document.getElementById("statusRow");
  if (s) s.remove();
  statusEl = null;
}

let planRowEl = null;

function showPlan(steps) {
  removePlan();
  planRowEl = el("div", "plan-row");
  planRowEl.id = "planRow";
  planRowEl.innerHTML = `<span class="plan-label">Plan:</span>` +
    steps.map((s, i) => {
      const isInf = s.toLowerCase().startsWith("inference:");
      const label = s.split(":").slice(1).join(":").trim();
      return `<span class="plan-chip ${isInf ? "inference" : ""}" id="step-${i}">${isInf ? "⚡" : "💬"} ${escHtml(label)}</span>`;
    }).join("");
  document.getElementById("messages").appendChild(planRowEl);
  scrollBottom();
}

function markStepActive(index) {
  document.querySelectorAll(".plan-chip").forEach((c, i) => {
    if (i < index) c.classList.add("done");
    else if (i === index) c.classList.add("active");
  });
}

function removePlan() {
  const p = document.getElementById("planRow");
  if (p) p.remove();
  planRowEl = null;
}

function showMeta(model, latencyMs) {
  const meta = el("div", "meta-bar");
  meta.innerHTML = `
    <span>⏱️ ${latencyMs}ms</span>
    <span>🤖 ${escHtml(model || "")}</span>
    <span>🔒 read-only</span>
  `;
  document.getElementById("messages").appendChild(meta);
  scrollBottom();
}

// ── Schema sidebar ────────────────────────────────────────────────────────────
function updateSidebarSchema(tables, cached) {
  const list = document.getElementById("tableList");
  const count = document.getElementById("tableCount");
  if (!tables || tables.length === 0) return;

  count.textContent = tables.length;
  list.innerHTML = tables.map(t => `
    <div class="table-item" onclick="askAboutTable('${escHtml(t)}')">
      <span class="ti-icon">📋</span>${escHtml(t)}
    </div>
  `).join("");
}

function askAboutTable(tableName) {
  document.getElementById("questionInput").value = `What columns are in the ${tableName} table?`;
  document.getElementById("questionInput").focus();
}

// ── Utilities ─────────────────────────────────────────────────────────────────
function el(tag, cls) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  return e;
}

function escHtml(s) {
  return String(s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function renderMarkdown(text) {
  return marked.parse(text, { breaks: true, gfm: true });
}

function scrollBottom() {
  const m = document.getElementById("messages");
  m.scrollTop = m.scrollHeight;
}

function hideWelcome() {
  const w = document.getElementById("welcomeCard");
  if (w) w.remove();
}

function showWelcome() {
  const messages = document.getElementById("messages");
  const w = el("div", "welcome");
  w.id = "welcomeCard";
  w.innerHTML = `
    <div style="font-size:48px;">🔍</div>
    <h2>Ask anything about your database</h2>
    <p>I can explore the schema, run read-only queries, and explain relationships — all in plain English.</p>
    <div class="suggestions">
      <button class="suggestion-btn" onclick="suggest(this)">What tables are in this database?</button>
      <button class="suggestion-btn" onclick="suggest(this)">Who are the top 5 artists by track count?</button>
      <button class="suggestion-btn" onclick="suggest(this)">Show me total sales by country</button>
      <button class="suggestion-btn" onclick="suggest(this)">What genres are available?</button>
    </div>
  `;
  messages.appendChild(w);
}

function handleKey(e) {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    sendQuestion();
  }
}

function handleInput(ta) {
  ta.style.height = "auto";
  ta.style.height = Math.min(ta.scrollHeight, 160) + "px";
  updateSendBtn();
}

function updateSendBtn() {
  const btn = document.getElementById("sendBtn");
  const hasText = document.getElementById("questionInput").value.trim().length > 0;
  btn.classList.toggle("ready", hasText && !isProcessing);
}

// ── Init ──────────────────────────────────────────────────────────────────────
connect();
