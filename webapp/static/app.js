// ── State ────────────────────────────────────────────────────────────────────
let messages = [];        // [{role, content}] — conversation history
let currentData = null;   // last scraped rows
let currentColumns = null;
let isRunning = false;

// ── Model selector ────────────────────────────────────────────────────────────
function getSelectedModel() {
  return $("model-select")?.value || "anthropic/claude-3.5-sonnet";
}

async function loadModels() {
  try {
    const resp = await fetch("/api/models");
    const { default: defaultModel, models } = await resp.json();

    const select = $("model-select");

    // Group by provider
    const byProvider = {};
    for (const m of models) {
      (byProvider[m.provider] ??= []).push(m);
    }

    for (const [provider, list] of Object.entries(byProvider)) {
      const group = document.createElement("optgroup");
      group.label = provider;
      for (const m of list) {
        const opt = document.createElement("option");
        opt.value = m.id;
        opt.textContent = m.label;
        if (m.id === defaultModel) opt.selected = true;
        group.appendChild(opt);
      }
      select.appendChild(group);
    }
  } catch {
    // non-fatal — selector stays empty, backend falls back to env var
  }
}

document.addEventListener("DOMContentLoaded", loadModels);

// ── SSE parser ────────────────────────────────────────────────────────────────
class SSEParser {
  constructor() { this.buf = ""; }
  feed(text) {
    this.buf += text;
    const events = [];
    const lines = this.buf.split("\n");
    this.buf = lines.pop() ?? "";
    for (const line of lines) {
      if (!line.startsWith("data: ")) continue;
      try { events.push(JSON.parse(line.slice(6))); } catch {}
    }
    return events;
  }
}

// ── DOM helpers ───────────────────────────────────────────────────────────────
const $ = id => document.getElementById(id);

function scrollBottom() {
  const el = $("messages");
  el.scrollTop = el.scrollHeight;
}

function appendUserMessage(text) {
  const el = document.createElement("div");
  el.className = "msg user";
  el.innerHTML = `
    <div class="avatar user-av">U</div>
    <div class="bubble"><div class="bubble-text">${escHtml(text)}</div></div>`;
  $("messages").appendChild(el);
  scrollBottom();
}

function createAgentBubble() {
  const el = document.createElement("div");
  el.className = "msg agent";
  el.innerHTML = `
    <div class="avatar agent">
      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
        <circle cx="11" cy="11" r="8"/><path d="m21 21-4.35-4.35"/>
      </svg>
    </div>
    <div class="bubble">
      <div class="bubble-text" data-text></div>
      <div class="tools-area"></div>
    </div>`;
  $("messages").appendChild(el);
  scrollBottom();
  return el;
}

function createToolCard(toolId, toolName) {
  const card = document.createElement("div");
  card.className = "tool-card";
  card.dataset.toolId = toolId;
  const label = formatToolName(toolName);
  card.innerHTML = `
    <div class="tool-card-header" onclick="toggleCard(this)">
      <div class="tool-status"><div class="spinner"></div></div>
      <span class="tool-name">${escHtml(label)}</span>
      <span class="badge pending">Running</span>
      <span class="tool-chevron">▼</span>
    </div>
    <div class="tool-body">
      <div class="tool-section-label">Arguments</div>
      <pre class="tool-code" data-args></pre>
    </div>`;
  return card;
}

function finishToolCard(card, result) {
  const header = card.querySelector(".tool-card-header");
  const statusEl = card.querySelector(".tool-status");
  const badgeEl = card.querySelector(".badge");

  const ok = !result?.error;
  statusEl.innerHTML = ok
    ? `<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#22c55e" stroke-width="2.5"><polyline points="20 6 9 17 4 12"/></svg>`
    : `<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#ef4444" stroke-width="2.5"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>`;

  badgeEl.className = ok ? "badge ok" : "badge err";
  badgeEl.textContent = ok ? "Done" : "Error";

  const body = card.querySelector(".tool-body");
  const resultEl = document.createElement("div");
  resultEl.innerHTML = `<div class="tool-section-label">Result</div>
    <pre class="tool-code ${ok ? 'tool-result-ok' : 'tool-result-err'}">${escHtml(JSON.stringify(result, null, 2))}</pre>`;
  body.appendChild(resultEl);
}

function toggleCard(header) {
  header.parentElement.classList.toggle("open");
}

function showDataPanel(data, columns, count) {
  currentData = data;
  currentColumns = columns;

  const panel = $("data-panel");
  panel.style.display = "block";

  $("data-count").textContent = `${count} row${count !== 1 ? "s" : ""}`;

  const table = $("data-table");
  table.innerHTML = "";

  const thead = document.createElement("thead");
  const headerRow = document.createElement("tr");
  for (const col of columns) {
    const th = document.createElement("th");
    th.textContent = col;
    headerRow.appendChild(th);
  }
  thead.appendChild(headerRow);
  table.appendChild(thead);

  const tbody = document.createElement("tbody");
  for (const row of data) {
    const tr = document.createElement("tr");
    for (const col of columns) {
      const td = document.createElement("td");
      const val = typeof row === "object" && row !== null ? (row[col] ?? "") : "";
      td.textContent = String(val);
      td.title = String(val);
      tr.appendChild(td);
    }
    tbody.appendChild(tr);
  }
  table.appendChild(tbody);

  panel.scrollIntoView({ behavior: "smooth", block: "nearest" });
}

function formatToolName(name) {
  return { check_url: "🔍 Check URL", fetch_and_extract: "⚙ Fetch & Extract" }[name] ?? name;
}

function escHtml(str) {
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

// ── Main send logic ───────────────────────────────────────────────────────────
async function sendMessage() {
  if (isRunning) return;
  const input = $("input");
  const text = input.value.trim();
  if (!text) return;

  isRunning = true;
  $("send-btn").disabled = true;
  input.value = "";
  autoResize(input);

  // Hide welcome screen on first message
  const welcome = document.querySelector(".welcome");
  if (welcome) welcome.remove();

  appendUserMessage(text);
  messages.push({ role: "user", content: text });

  const agentEl = createAgentBubble();
  const textEl = agentEl.querySelector("[data-text]");
  const toolsArea = agentEl.querySelector(".tools-area");

  let agentText = "";
  let activeToolCard = null;
  let activeToolId = null;
  let argsStr = "";
  const toolCards = {}; // toolId -> card element

  // Streaming cursor
  const cursor = document.createElement("span");
  cursor.className = "cursor";
  textEl.appendChild(cursor);

  try {
    const resp = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ messages, model: getSelectedModel() }),
    });

    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);

    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    const parser = new SSEParser();

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      const events = parser.feed(decoder.decode(value, { stream: true }));

      for (const ev of events) {
        switch (ev.type) {
          case "TEXT_MESSAGE_CONTENT":
            agentText += ev.delta;
            cursor.remove();
            textEl.textContent = agentText;
            textEl.appendChild(cursor);
            scrollBottom();
            break;

          case "TOOL_CALL_START": {
            const card = createToolCard(ev.toolCallId, ev.toolCallName);
            toolCards[ev.toolCallId] = card;
            toolsArea.appendChild(card);
            activeToolCard = card;
            activeToolId = ev.toolCallId;
            argsStr = "";
            scrollBottom();
            break;
          }

          case "TOOL_CALL_ARGS_DELTA":
            argsStr += ev.delta;
            if (toolCards[ev.toolCallId]) {
              const argsEl = toolCards[ev.toolCallId].querySelector("[data-args]");
              if (argsEl) {
                try {
                  argsEl.textContent = JSON.stringify(JSON.parse(argsStr), null, 2);
                } catch {
                  argsEl.textContent = argsStr;
                }
              }
            }
            break;

          case "TOOL_CALL_END":
            if (toolCards[ev.toolCallId]) {
              finishToolCard(toolCards[ev.toolCallId], ev.result);
            }
            break;

          case "DATA_UPDATE":
            showDataPanel(ev.data, ev.columns, ev.count ?? ev.data.length);
            break;

          case "RUN_FINISHED":
            if (agentText) {
              messages.push({ role: "assistant", content: agentText });
            }
            break;

          case "RUN_ERROR":
            textEl.innerHTML = `<span class="error-msg">Error: ${escHtml(ev.error)}</span>`;
            break;
        }
      }
    }
  } catch (err) {
    textEl.innerHTML = `<span class="error-msg">Connection error: ${escHtml(err.message)}</span>`;
  } finally {
    cursor.remove();
    isRunning = false;
    $("send-btn").disabled = false;
    scrollBottom();
  }
}

// ── Export ────────────────────────────────────────────────────────────────────
async function exportToExcel() {
  if (!currentData || !currentColumns) return;
  const btn = $("export-btn");
  btn.disabled = true;
  btn.textContent = "Exporting…";

  try {
    const resp = await fetch("/api/export", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ data: currentData, columns: currentColumns }),
    });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const blob = await resp.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "scraped_data.xlsx";
    a.click();
    setTimeout(() => URL.revokeObjectURL(url), 5000);
  } catch (err) {
    alert("Export failed: " + err.message);
  } finally {
    btn.disabled = false;
    btn.innerHTML = `<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
      <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/>
    </svg> Export to Excel`;
  }
}

// ── Utilities ─────────────────────────────────────────────────────────────────
function newChat() {
  messages = [];
  currentData = null;
  currentColumns = null;
  $("data-panel").style.display = "none";
  $("data-table").innerHTML = "";
  const msgs = $("messages");
  msgs.innerHTML = `<div class="welcome">
    <div class="welcome-icon">
      <svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
        <circle cx="11" cy="11" r="8"/><path d="m21 21-4.35-4.35"/>
      </svg>
    </div>
    <h1>Web Scraping Agent</h1>
    <p>Tell me a website URL and what data you'd like to extract. I'll fetch and structure it for you — then you can export it to Excel.</p>
    <div class="welcome-chips">
      <div class="chip"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/></svg>Bot-detection bypass</div>
      <div class="chip"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="3" width="18" height="18" rx="2"/><path d="M3 9h18M9 21V9"/></svg>Excel export</div>
      <div class="chip"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 2L2 7l10 5 10-5-10-5zM2 17l10 5 10-5M2 12l10 5 10-5"/></svg>AI-powered extraction</div>
    </div>
  </div>`;
}

function useExample(text) {
  const input = $("input");
  input.value = text;
  autoResize(input);
  input.focus();
}

function handleKey(e) {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    sendMessage();
  }
}

function autoResize(el) {
  el.style.height = "auto";
  el.style.height = Math.min(el.scrollHeight, 160) + "px";
}
