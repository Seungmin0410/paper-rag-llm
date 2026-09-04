// ---------- 탭 전환 (채팅 / 관리) ----------
const pageTabs = document.querySelectorAll(".page-tab");
const pagePanels = {
  chat: document.getElementById("panel-chat"),
  manage: document.getElementById("panel-manage"),
};

pageTabs.forEach((tab) => {
  tab.addEventListener("click", () => {
    pageTabs.forEach((t) => t.classList.remove("active"));
    tab.classList.add("active");
    Object.entries(pagePanels).forEach(([key, panel]) => {
      panel.classList.toggle("active", key === tab.dataset.panel);
    });
    if (tab.dataset.panel === "chat") chatInput.focus();
  });
});

// ---------- 공통: 프로젝트 상태 (헤더 칩 + 관리 탭이 같이 씀) ----------
const PROJECT_STORAGE_KEY = "bc_selected_project";

const projectSelect = document.getElementById("project-select");
const projectSelectTrigger = document.getElementById("project-select-trigger");
const projectSelectLabel = document.getElementById("project-select-label");
const projectSelectPanel = document.getElementById("project-select-panel");
const projectTabs = document.getElementById("project-tabs");
const addEntryLabel = document.getElementById("add-entry-label");
const logPanel = document.getElementById("log-panel");
const logSummary = document.getElementById("log-summary");
const logRaw = document.getElementById("log-raw");

let projects = [];
let selectedProjectId = localStorage.getItem(PROJECT_STORAGE_KEY) || "";

// crypto.randomUUID()는 "보안 컨텍스트"(HTTPS 또는 localhost)에서만 동작해서,
// Tailscale IP처럼 HTTP로 접속하는 다른 사람 브라우저에서는 여기서 에러가 나면서
// 이 파일의 나머지 코드가 전부 실행되지 않는 문제가 있었음 -> 의존 없는 방식으로 대체.
function generateId() {
  return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, (c) => {
    const r = (Math.random() * 16) | 0;
    const v = c === "x" ? r : (r & 0x3) | 0x8;
    return v.toString(16);
  });
}

// ---------- 채팅 기록용 세션 ID (사람/브라우저별로 분리 저장하기 위함, AI 프롬프트엔 안 들어감) ----------
const SESSION_STORAGE_KEY = "bc_chat_session_id";
let chatSessionId = localStorage.getItem(SESSION_STORAGE_KEY);
if (!chatSessionId) {
  chatSessionId = generateId();
  localStorage.setItem(SESSION_STORAGE_KEY, chatSessionId);
}

function countEntries(text) {
  return text.split(/\n\s*\n/).map((b) => b.trim()).filter(Boolean).length;
}

async function loadProjects() {
  try {
    const res = await fetch("/api/projects");
    const data = await res.json();
    projects = data.projects || [];
  } catch (err) {
    projects = [];
  }

  if (!selectedProjectId || !projects.some((p) => p.id === selectedProjectId)) {
    selectedProjectId = projects.length ? projects[0].id : "";
  }
  renderProjectUI();
  if (selectedProjectId) loadNotes();
}

// 헤더 칩 + 관리 탭, 두 군데 다 이 하나의 상태를 보고 다시 그림 (따로 동기화 안 해도 됨 - 한 페이지라서)
function selectProject(id) {
  selectedProjectId = id;
  localStorage.setItem(PROJECT_STORAGE_KEY, id);
  renderProjectUI();
  loadNotes();
  projectSelectPanel.classList.remove("open");
}

function renderProjectUI() {
  // 헤더 드롭다운
  const proj = projects.find((p) => p.id === selectedProjectId);
  projectSelectLabel.textContent = proj ? proj.name : "프로젝트 없음";

  projectSelectPanel.innerHTML = "";
  projects.forEach((p) => {
    const opt = document.createElement("div");
    opt.className = "project-select-option" + (p.id === selectedProjectId ? " active" : "");
    opt.textContent = p.name;
    opt.addEventListener("click", () => selectProject(p.id));
    projectSelectPanel.appendChild(opt);
  });

  // 관리 탭 (노하우 카드)
  projectTabs.innerHTML = "";
  projects.forEach((p) => {
    const btn = document.createElement("button");
    btn.className = "project-tab" + (p.id === selectedProjectId ? " active" : "");
    btn.type = "button";
    btn.textContent = p.name;
    btn.addEventListener("click", () => selectProject(p.id));
    projectTabs.appendChild(btn);
  });

  const addBtn = document.createElement("button");
  addBtn.className = "project-tab add-new";
  addBtn.type = "button";
  addBtn.textContent = "+ 새 프로젝트";
  addBtn.addEventListener("click", async () => {
    const name = window.prompt("새 프로젝트 이름을 입력하세요");
    if (!name || !name.trim()) return;
    const res = await fetch("/api/projects", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: name.trim() }),
    });
    const data = await res.json();
    if (data.project) {
      selectedProjectId = data.project.id;
      localStorage.setItem(PROJECT_STORAGE_KEY, selectedProjectId);
      await loadProjects();
    }
  });
  projectTabs.appendChild(addBtn);
}

projectSelectTrigger.addEventListener("click", () => {
  projectSelectPanel.classList.toggle("open");
});

document.addEventListener("click", (e) => {
  if (!projectSelect.contains(e.target)) {
    projectSelectPanel.classList.remove("open");
  }
});

async function loadNotes() {
  const proj = projects.find((p) => p.id === selectedProjectId);
  if (!proj) return;

  addEntryLabel.textContent = `📝 ${proj.name} 실험 기록 추가`;

  const res = await fetch(`/api/notes?project_id=${encodeURIComponent(selectedProjectId)}`);
  const data = await res.json();
  const text = data.text || "";

  logSummary.textContent = `${proj.name} 전체 노하우 보기 (${countEntries(text)}개 기록)`;
  logRaw.textContent = text.trim() ? text : "(아직 기록된 노하우가 없어요)";
}

// ---------- 노하우 기록 추가 ----------
const dateStamp = document.getElementById("date-stamp");
const entryTitle = document.getElementById("entry-title");
const entryBody = document.getElementById("entry-body");
const saveEntryBtn = document.getElementById("save-entry-btn");

const now = new Date();
dateStamp.textContent = `${now.getMonth() + 1}/${now.getDate()} (오늘)`;

saveEntryBtn.addEventListener("click", async () => {
  const title = entryTitle.value.trim();
  const body = entryBody.value.trim();
  if (!title && !body) return;

  saveEntryBtn.disabled = true;
  try {
    const res = await fetch("/api/notes", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ project_id: selectedProjectId, title, body }),
    });
    const data = await res.json();
    if (data.text !== undefined) {
      entryTitle.value = "";
      entryBody.value = "";
      const proj = projects.find((p) => p.id === selectedProjectId);
      logSummary.textContent = `${proj.name} 전체 노하우 보기 (${countEntries(data.text)}개 기록)`;
      logRaw.textContent = data.text;
      logPanel.open = true;
    }
  } finally {
    saveEntryBtn.disabled = false;
  }
});

// ---------- 논문 업로드 ----------
const dropzone = document.getElementById("dropzone");
const fileInput = document.getElementById("file-input");
const dupWarning = document.getElementById("dup-warning");
const uploadError = document.getElementById("upload-error");
const uploadLog = document.getElementById("upload-log");

dropzone.addEventListener("click", () => fileInput.click());

["dragover", "dragenter"].forEach((evt) =>
  dropzone.addEventListener(evt, (e) => {
    e.preventDefault();
    dropzone.classList.add("drag");
  })
);
["dragleave", "drop"].forEach((evt) =>
  dropzone.addEventListener(evt, (e) => {
    e.preventDefault();
    dropzone.classList.remove("drag");
  })
);
dropzone.addEventListener("drop", (e) => {
  uploadFiles(e.dataTransfer.files);
});
fileInput.addEventListener("change", () => {
  uploadFiles(fileInput.files);
  fileInput.value = "";
});

function renderSteps(steps, allDone = false) {
  uploadLog.classList.add("active");
  uploadLog.innerHTML = steps
    .map((s, i) => `<div class="step${(allDone || i !== steps.length - 1) ? " done" : ""}"><span class="dot"></span>${s}</div>`)
    .join("");
}

// 여러 파일을 한꺼번에 드래그해도, 서버 부하 때문에 동시에 안 돌리고 하나씩 순서대로 처리
async function uploadFiles(fileList) {
  const files = Array.from(fileList || []);
  for (let i = 0; i < files.length; i++) {
    const label = files.length > 1 ? `[${i + 1}/${files.length}] ${files[i].name}` : null;
    await uploadOneFile(files[i], label);
  }
}

function uploadOneFile(file, label) {
  dupWarning.classList.remove("active");
  uploadError.classList.remove("active");
  uploadLog.classList.remove("active");
  uploadLog.innerHTML = "";

  const prefix = label ? `${label} — ` : "";

  if (!file.name.toLowerCase().endsWith(".pdf")) {
    uploadError.classList.add("active");
    uploadError.textContent = `${prefix}PDF 파일만 업로드할 수 있어요.`;
    return Promise.resolve();
  }

  const formData = new FormData();
  formData.append("file", file);

  return fetch("/api/papers/upload", { method: "POST", body: formData })
    .then(async (res) => {
      const data = await res.json();
      if (!res.ok || data.error) {
        uploadError.classList.add("active");
        uploadError.textContent = `${prefix}${data.error || "업로드에 실패했어요."}`;
        return;
      }
      return pollUploadStatus(data.job_id, label);
    })
    .catch(() => {
      uploadError.classList.add("active");
      uploadError.textContent = `${prefix}서버와 연결할 수 없어요.`;
    });
}

function pollUploadStatus(jobId, label) {
  return new Promise((resolve) => {
    const prefix = label ? `${label} — ` : "";
    const check = async () => {
      const res = await fetch(`/api/papers/upload/status/${jobId}`);
      const job = await res.json();
      const steps = label ? [label, ...(job.steps || [])] : (job.steps || []);

      if (job.steps && job.steps.length) renderSteps(steps);

      if (job.status === "running") {
        setTimeout(check, 800);
      } else if (job.status === "duplicate") {
        dupWarning.classList.add("active");
        dupWarning.innerHTML = `⚠️&nbsp; ${prefix}${job.message}`;
        resolve();
      } else if (job.status === "error") {
        uploadError.classList.add("active");
        uploadError.textContent = `${prefix}${job.message || "처리 중 오류가 발생했어요."}`;
        resolve();
      } else if (job.status === "done") {
        renderSteps([...steps, `✅ ${job.message}`], true);
        resolve();
      }
    };
    check();
  });
}

// ---------- 채팅 ----------
const chatArea = document.getElementById("chat-area");
const chatForm = document.getElementById("chat-form");
const chatInput = document.getElementById("chat-input");
const sendBtn = document.getElementById("send-btn");
const statusPill = document.getElementById("status-pill");

const pinTrigger = document.getElementById("pin-trigger");
const pinChip = document.getElementById("pin-chip");
const pinTitle = document.getElementById("pin-title");
const pinRemove = document.getElementById("pin-remove");
const pinMode = document.getElementById("pin-mode");
const pinPanel = document.getElementById("pin-panel");
const pinSearch = document.getElementById("pin-search");
const pinList = document.getElementById("pin-list");

let papers = null; // 처음 패널 열 때 한 번만 /api/papers로 불러옴
let pinnedPaperId = null;
let pinModeValue = "broad";

function scrollToBottom() {
  chatArea.scrollTop = chatArea.scrollHeight;
}

function escapeHtml(str) {
  const div = document.createElement("div");
  div.textContent = str;
  return div.innerHTML;
}

// 한 줄 안에서: [논문DB: paper_id] / [웹서치] 태그 강조 + **bold** 렌더링 (이스케이프 후 처리)
function formatInline(line) {
  return escapeHtml(line)
    .replace(
      /\[지정논문:\s*([^\]]+)\]/g,
      '<a class="source-tag source-tag-pinned" href="/api/papers/$1/pdf" target="_blank" rel="noopener">[지정논문: $1]</a>'
    )
    .replace(
      /\[논문DB:\s*([^\]]+)\]/g,
      '<a class="source-tag" href="/api/papers/$1/pdf" target="_blank" rel="noopener">[논문DB: $1]</a>'
    )
    .replace(/\[웹서치\]/g, '<span class="source-tag">[웹서치]</span>')
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
}

function isTableRow(line) {
  return /^\|.*\|$/.test(line);
}

function isTableSeparatorRow(line) {
  return isTableRow(line) && /^\|[\s:-]+\|([\s:|-]+\|)*$/.test(line);
}

function parseTableRow(line) {
  return line.replace(/^\|/, "").replace(/\|$/, "").split("|").map((c) => c.trim());
}

// LLM이 즐겨 쓰는 마크다운(#, ##, 리스트, ---, 표)을 실제 블록 엘리먼트로 변환.
// 이걸 안 하면 "# 제목", "- 항목", "| a | b |" 같은 문법이 그대로 텍스트로 노출돼서 답변이 지저분해짐.
function formatAnswer(text) {
  const lines = text.replace(/\r\n/g, "\n").split("\n");
  const blocks = [];
  let listBuffer = [];
  let paraBuffer = [];
  let tableBuffer = [];

  const flushList = () => {
    if (listBuffer.length) {
      blocks.push("<ul>" + listBuffer.map((item) => `<li>${formatInline(item)}</li>`).join("") + "</ul>");
      listBuffer = [];
    }
  };
  const flushPara = () => {
    if (paraBuffer.length) {
      blocks.push(`<p>${paraBuffer.map(formatInline).join("<br>")}</p>`);
      paraBuffer = [];
    }
  };
  const flushTable = () => {
    if (tableBuffer.length) {
      const rows = tableBuffer.filter((r) => !isTableSeparatorRow(r)).map(parseTableRow);
      if (rows.length) {
        const [headerRow, ...bodyRows] = rows;
        const thead = "<thead><tr>" + headerRow.map((c) => `<th>${formatInline(c)}</th>`).join("") + "</tr></thead>";
        const tbody =
          "<tbody>" +
          bodyRows.map((r) => "<tr>" + r.map((c) => `<td>${formatInline(c)}</td>`).join("") + "</tr>").join("") +
          "</tbody>";
        blocks.push(`<div class="ans-table-wrap"><table class="ans-table">${thead}${tbody}</table></div>`);
      }
      tableBuffer = [];
    }
  };

  for (const raw of lines) {
    const line = raw.trim();

    if (!line) {
      flushList();
      flushPara();
      flushTable();
      continue;
    }

    if (isTableRow(line)) {
      flushList();
      flushPara();
      tableBuffer.push(line);
      continue;
    }
    flushTable();

    const heading = line.match(/^(#{1,4})\s+(.*)$/);
    if (heading) {
      flushList();
      flushPara();
      blocks.push(`<div class="ans-heading">${formatInline(heading[2])}</div>`);
      continue;
    }

    if (/^(-{3,}|\*{3,})$/.test(line)) {
      flushList();
      flushPara();
      blocks.push('<hr class="ans-divider">');
      continue;
    }

    const listItem = line.match(/^[-*]\s+(.*)$/);
    if (listItem) {
      flushPara();
      listBuffer.push(listItem[1]);
      continue;
    }

    flushList();
    paraBuffer.push(line);
  }
  flushList();
  flushPara();
  flushTable();

  return blocks.join("");
}

function addMessage(role, html, { isError = false } = {}) {
  const msg = document.createElement("div");
  msg.className = `msg msg-${role === "user" ? "user" : "bot"}`;

  const avatar = document.createElement("div");
  avatar.className = "avatar";
  avatar.textContent = role === "user" ? "🧑‍🔬" : "🌱";

  const bubble = document.createElement("div");
  bubble.className = "bubble" + (isError ? " error" : "");
  bubble.innerHTML = html;

  msg.appendChild(avatar);
  msg.appendChild(bubble);
  chatArea.appendChild(msg);
  scrollToBottom();
  return bubble;
}

function addTypingBubble() {
  const bubble = addMessage("bot", `
    <span class="typing"><span></span><span></span><span></span></span>
    <span style="margin-left:4px;color:var(--ink-soft);font-size:13px;">논문을 찾아보고 있어요...</span>
  `);
  return bubble;
}

const SEND_ICON = `<svg viewBox="0 0 24 24" width="20" height="20" fill="none"><path d="M3 11.5L20.5 4 13 21l-2.2-6.8L3 11.5z" stroke="currentColor" stroke-width="1.6" stroke-linejoin="round" stroke-linecap="round"/></svg>`;
const STOP_ICON = `<svg viewBox="0 0 24 24" width="16" height="16" fill="currentColor"><rect x="5" y="5" width="14" height="14" rx="2"/></svg>`;

function setBusy(isBusy) {
  chatInput.disabled = isBusy;
  sendBtn.classList.toggle("cancel-mode", isBusy);
  sendBtn.innerHTML = isBusy ? STOP_ICON : SEND_ICON;
  sendBtn.setAttribute("aria-label", isBusy ? "중단" : "보내기");
  statusPill.innerHTML = isBusy
    ? `<span class="dot"></span> 검색 중`
    : `<span class="dot"></span> 연결됨`;
}

async function loadPapers() {
  if (papers) return papers;
  try {
    const res = await fetch("/api/papers");
    const data = await res.json();
    papers = data.papers || [];
  } catch (err) {
    papers = [];
  }
  return papers;
}

function renderPinList(query) {
  const q = (query || "").trim().toLowerCase();
  const filtered = (papers || []).filter(
    (p) => p.title.toLowerCase().includes(q) || p.id.toLowerCase().includes(q)
  );
  pinList.innerHTML = "";

  if (!filtered.length) {
    pinList.innerHTML = '<div class="pin-empty">일치하는 논문이 없어요</div>';
    return;
  }

  filtered.forEach((p) => {
    const el = document.createElement("div");
    el.className = "pin-option";
    el.innerHTML = `<span class="pid">${escapeHtml(p.id)}</span><span class="ptitle">${escapeHtml(p.title)}</span>`;
    el.addEventListener("click", () => selectPaper(p));
    pinList.appendChild(el);
  });
}

function selectPaper(p) {
  pinnedPaperId = p.id;
  pinTitle.textContent = p.title;
  pinChip.classList.add("active");
  pinTrigger.classList.add("hidden");
  pinMode.classList.add("active");
  pinPanel.classList.remove("open");
  pinSearch.value = "";
}

function clearPinnedPaper() {
  pinnedPaperId = null;
  pinChip.classList.remove("active");
  pinTrigger.classList.remove("hidden");
  pinMode.classList.remove("active");
}

pinTrigger.addEventListener("click", async () => {
  await loadPapers();
  renderPinList("");
  pinPanel.classList.toggle("open");
  if (pinPanel.classList.contains("open")) pinSearch.focus();
});

pinRemove.addEventListener("click", clearPinnedPaper);

pinSearch.addEventListener("input", () => renderPinList(pinSearch.value));

pinMode.querySelectorAll("button").forEach((btn) => {
  btn.addEventListener("click", () => {
    pinMode.querySelectorAll("button").forEach((b) => b.classList.remove("selected"));
    btn.classList.add("selected");
    pinModeValue = btn.dataset.mode;
  });
});

document.addEventListener("click", (e) => {
  if (!pinPanel.contains(e.target) && e.target !== pinTrigger && !pinTrigger.contains(e.target)) {
    pinPanel.classList.remove("open");
  }
});

let currentAbortController = null;
let currentRequestId = null;

function cancelCurrentRequest() {
  if (currentAbortController) currentAbortController.abort();
  if (currentRequestId) {
    fetch("/api/chat/cancel", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ request_id: currentRequestId }),
    }).catch(() => {});
  }
}

sendBtn.addEventListener("click", (e) => {
  if (sendBtn.classList.contains("cancel-mode")) {
    e.preventDefault();
    cancelCurrentRequest();
  }
});

async function sendMessage(text) {
  addMessage("user", escapeHtml(text));
  setBusy(true);
  const typingBubble = addTypingBubble();

  const requestId = generateId();
  currentRequestId = requestId;
  const controller = new AbortController();
  currentAbortController = controller;

  try {
    const res = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      signal: controller.signal,
      body: JSON.stringify({
        message: text,
        pinned_paper_id: pinnedPaperId,
        pin_mode: pinModeValue,
        project_id: selectedProjectId,
        session_id: chatSessionId,
        request_id: requestId,
      }),
    });
    const data = await res.json();

    if (data.cancelled) {
      typingBubble.innerHTML = "🛑 중단되었습니다.";
    } else if (!res.ok || data.error) {
      typingBubble.classList.add("error");
      typingBubble.innerHTML = `🍂 ${escapeHtml(data.error || "알 수 없는 오류가 발생했어요.")}`;
    } else {
      typingBubble.innerHTML = formatAnswer(data.answer || "(빈 답변)");
    }
  } catch (err) {
    if (err.name === "AbortError") {
      typingBubble.innerHTML = "🛑 중단되었습니다.";
    } else {
      typingBubble.classList.add("error");
      typingBubble.innerHTML = "🍂 서버와 연결할 수 없어요. 잠시 후 다시 시도해주세요.";
    }
  } finally {
    currentAbortController = null;
    currentRequestId = null;
    setBusy(false);
    scrollToBottom();
    chatInput.focus();
  }
}

chatForm.addEventListener("submit", (e) => {
  e.preventDefault();
  const text = chatInput.value.trim();
  if (!text) return;
  chatInput.value = "";
  chatInput.style.height = "auto";
  sendMessage(text);
});

chatInput.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    chatForm.requestSubmit();
  }
});

chatInput.addEventListener("input", () => {
  chatInput.style.height = "auto";
  chatInput.style.height = Math.min(chatInput.scrollHeight, 140) + "px";
});

chatInput.focus();

// ---------- 시작 ----------
loadProjects();
