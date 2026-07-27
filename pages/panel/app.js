const API = "/api/plugins/extensions/astrbot_plugin_romantic_memory";
const state = { memories: [], personas: [], selectedMemoryIds: new Set(), memoryPage: 1 };
const deleteConfirmation = { ids: [], label: "", step: 0 };
const deleteAllConfirmation = { requiredText: "" };
let uploadToastTimer = null;
const $ = (id) => document.getElementById(id);
const esc = (value) => String(value ?? "").replace(/[&<>\"]/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "\"": "&quot;" }[char]));

function parseApiPath(path) {
  const url = new URL(path, "http://romantic-memory.local");
  return {
    endpoint: url.pathname.replace(/^\/+/, ""),
    params: Object.fromEntries(url.searchParams.entries()),
  };
}

async function api(path, options = {}) {
  const method = String(options.method || "GET").toUpperCase();
  const parsed = parseApiPath(path);
  const bridge = window.AstrBotPluginPage;
  if (bridge) {
    if (method === "GET") return bridge.apiGet(parsed.endpoint, parsed.params);
    let body = options.body || {};
    if (typeof body === "string") body = JSON.parse(body);
    return bridge.apiPost(parsed.endpoint, body);
  }
  const response = await fetch(`${API}${path}`, { credentials: "same-origin", ...options });
  const type = response.headers.get("content-type") || "";
  const data = type.includes("json") ? await response.json() : await response.text();
  if (!response.ok) throw new Error(data?.message || data || `Request failed (${response.status})`);
  return data;
}

function message(id, text, error = false) {
  const node = $(id);
  if (!node) return;
  node.textContent = text;
  node.className = `message ${error ? "error" : "success"}`;
}

function jsonView(id, data) {
  const node = $(id);
  if (node) node.textContent = JSON.stringify(data, null, 2);
}

function ensureOverviewDiagnostics() {
  const monitorBlock = document.querySelector("#page-dashboard .archive-block");
  const monitorJson = $("monitor-json");
  if (monitorJson) monitorJson.remove();
  const monitorDescription = document.querySelector("#page-dashboard .section-head p");
  if (monitorDescription) monitorDescription.textContent = "实验性功能。具体运行情况请查看 AstrBot 日志。";
  const metrics = document.querySelector(".metrics");
  if (metrics && !$("status-long-term")) {
    const article = document.createElement("article");
    article.className = "metric";
    article.innerHTML = '<span class="label">LONG-TERM</span><strong id="status-long-term">—</strong>';
    metrics.appendChild(article);
  }
  const details = document.querySelector(".monitor-details");
  if (details && !$("monitor-personality")) {
    [
      ["monitor-personality", "ACTIVE PERSONA"],
      ["monitor-session", "ACTIVE SESSION"],
      ["monitor-last-recall", "LAST RECALL"],
    ].forEach(([id, label]) => {
      const item = document.createElement("div");
      item.className = "monitor-item";
      item.innerHTML = `<span class="label">${label}</span><strong id="${id}">—</strong>`;
      details.appendChild(item);
    });
  }
  if (monitorBlock && !$("monitor-recall-detail")) {
    const detail = document.createElement("div");
    detail.id = "monitor-recall-detail";
    detail.className = "monitor-recall-detail";
    detail.setAttribute("role", "status");
    monitorBlock.insertBefore(detail, $("monitor-error"));
  }
}

function showUploadToast(text, persistent = false) {
  const node = $("upload-toast");
  if (!node) return;
  node.textContent = text;
  node.classList.add("visible");
  if (uploadToastTimer) clearTimeout(uploadToastTimer);
  uploadToastTimer = null;
  if (persistent) return;
  uploadToastTimer = setTimeout(() => node.classList.remove("visible"), 3200);
}

function formatMonitorTime(timestamp) {
  const value = Number(timestamp || 0);
  return value > 0 ? new Date(value * 1000).toLocaleString() : "暂无";
}

async function loadMonitoring() {
  try {
    ensureOverviewDiagnostics();
    const raw = await api("/monitoring");
    const data = raw?.data && typeof raw.data === "object" && !("chroma_connected" in raw) ? raw.data : raw;
    const metrics = data.metrics || {};
    $("status-chroma").textContent = data.chroma_connected ? "READY" : "OFFLINE";
    $("status-embedding").textContent = data.embedding_provider_available ? "READY" : "OFFLINE";
    $("status-sessions").textContent = data.session_count ?? "—";
    $("status-cache").textContent = data.short_term_messages ?? "—";
    $("status-long-term") && ($("status-long-term").textContent = data.long_term_memory_count ?? "—");
    $("monitor-path").textContent = data.chroma_path || "—";
    $("monitor-last-summary").textContent = formatMonitorTime(metrics.last_summary_at);
    $("monitor-summary-result").textContent = "成功 " + (metrics.summaries_success ?? 0) + " / 失败 " + (metrics.summaries_failed ?? 0);
    $("monitor-recalls").textContent = String(metrics.recalls ?? 0);
    $("monitor-error").textContent = metrics.last_error || "";
    const recall = metrics.last_recall || {};
    const statusLabels = { hit: "HIT", miss: "MISS", skipped: "SKIPPED", error: "ERROR", running: "RUNNING", not_run: "NOT RUN" };
    $("monitor-personality") && ($("monitor-personality").textContent = recall.personality_id || "—");
    $("monitor-session") && ($("monitor-session").textContent = recall.session_id || "—");
    $("monitor-last-recall") && ($("monitor-last-recall").textContent = statusLabels[recall.status] || recall.status || "—");
    if ($("monitor-recall-detail")) {
      const selected = (recall.selected || []).map((item) => `${item.date || "unknown date"} | ${item.content || ""}`).join("\n");
      $("monitor-recall-detail").textContent = [
        `原因: ${recall.reason || "暂无"}`,
        `候选 ${recall.candidate_count ?? 0} / 通过筛选 ${recall.ranked_count ?? 0} / 实际注入 ${recall.selected_count ?? 0}`,
        selected ? `本次命中记忆:\n${selected}` : "本次没有注入记忆。",
      ].join("\n");
    }
  } catch (error) {
    $("monitor-error").textContent = error.message;
  }
}

function clearEditor() {
  $("edit-id").value = "";
  $("edit-session").value = "";
  $("edit-date").value = "";
  $("edit-content").value = "";
  $("edit-personality").value = "default";
  $("editor-title").textContent = "新增记忆";
}

function editMemory(memory) {
  $("edit-id").value = memory.id || "";
  $("edit-session").value = memory.session_id || "";
  $("edit-date").value = memory.date || "";
  $("edit-content").value = memory.content || "";
  $("edit-personality").value = memory.personality_id || "default";
  $("editor-title").textContent = "修改记忆";
  document.querySelector(".editor-block").scrollIntoView({ behavior: "smooth", block: "start" });
}

function renderMemories() {
  const pageSize = Number($("memory-page-size").value || 0);
  const pageCount = pageSize > 0 ? Math.max(1, Math.ceil(state.memories.length / pageSize)) : 1;
  state.memoryPage = Math.min(Math.max(1, state.memoryPage), pageCount);
  const start = pageSize > 0 ? (state.memoryPage - 1) * pageSize : 0;
  const visible = pageSize > 0 ? state.memories.slice(start, start + pageSize) : state.memories;
  $("memory-body").innerHTML = visible.map((memory, index) => {
    const fullIndex = start + index;
    const checked = state.selectedMemoryIds.has(memory.id) ? " checked" : "";
    return `<tr><td class="select-cell"><input class="memory-check" data-index="${fullIndex}" type="checkbox"${checked} aria-label="Select memory"></td><td>${esc(memory.date)}</td><td>${esc(memory.personality_id || "default")}</td><td>${esc(memory.session_id)}</td><td class="memory-cell">${esc(memory.content)}</td><td><button class="row-button" data-index="${fullIndex}" type="button">EDIT</button></td></tr>`;
  }).join("") || `<tr><td colspan="6" class="empty">NO MEMORIES</td></tr>`;
  $("memory-count").textContent = state.memories.length + " MEMORIES";
  $("memory-page-info").textContent = state.memories.length ? "第 " + state.memoryPage + " / " + pageCount + " 页" : "暂无记忆";
  $("memory-prev").disabled = state.memoryPage <= 1;
  $("memory-next").disabled = state.memoryPage >= pageCount;
  updateSelectAllState();
  document.querySelectorAll("[data-index]").forEach((button) => {
    if (button.classList.contains("memory-check")) {
      button.addEventListener("change", (event) => {
        const memory = state.memories[Number(event.target.dataset.index)];
        if (!memory) return;
        if (event.target.checked) state.selectedMemoryIds.add(memory.id);
        else state.selectedMemoryIds.delete(memory.id);
        updateSelectAllState();
      });
    } else {
      button.addEventListener("click", () => editMemory(state.memories[Number(button.dataset.index)]));
    }
  });
}

async function loadMemories() {
  try {
    const query = new URLSearchParams({ session_id: $("memory-session").value.trim(), keyword: $("memory-keyword").value.trim(), personality_id: $("memory-personality").value });
    const result = await api(`/memories?${query}`);
    const payload = Array.isArray(result) ? { data: result, total: result.length } : result;
    state.memories = payload.data || [];
    state.selectedMemoryIds.clear();
    state.memoryPage = 1;
    renderMemories();
  } catch (error) {
    state.memories = [];
    state.selectedMemoryIds.clear();
    $("memory-body").innerHTML = `<tr><td colspan="6" class="empty">${esc(error.message)}</td></tr>`;
    $("memory-count").textContent = "读取失败";
    $("memory-page-info").textContent = "—";
  }
}

function changeMemoryPage(offset) {
  state.memoryPage += offset;
  renderMemories();
}

function changeMemoryPageSize() {
  state.memoryPage = 1;
  renderMemories();
}

async function saveMemory() {
  const id = $("edit-id").value.trim();
  const content = $("edit-content").value.trim();
  if (!content) return message("memory-message", "记忆内容不能为空", true);
  try {
    const payload = { session_id: $("edit-session").value.trim(), personality_id: $("edit-personality").value, date: $("edit-date").value || undefined, content };
    await api(id ? `/memories/${encodeURIComponent(id)}` : "/memories", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
    message("memory-message", id ? "已修改" : "已保存");
    clearEditor();
    await loadMemories();
  } catch (error) {
    message("memory-message", error.message, true);
  }
}

function shortTermContext(sessionId = "") {
  return {
    session_id: String(sessionId || "").trim(),
    personality_id: $("short-personality").value.trim() || "default",
  };
}

function renderShortTerm(payload) {
  const node = $("short-messages");
  if (!node) return;
  const messages = Array.isArray(payload?.messages) ? payload.messages : [];
  $("short-status").textContent = messages.length ? `${messages.length} messages` : "No pending messages";
  node.innerHTML = messages.map((message) => {
    const id = esc(message.id || "");
    const sessionId = esc(message.session_id || "");
    const role = esc(message.role || "unknown");
    const type = esc(message.type || "text");
    const timestamp = Number(message.timestamp || 0) > 0 ? new Date(Number(message.timestamp) * 1000).toLocaleString() : "";
    return `<article class="short-message-card" data-message-id="${id}" data-session-id="${sessionId}">
      <div class="short-message-head"><div><span class="short-readonly">role: ${role}</span><span class="short-readonly">type: ${type}</span><span class="short-readonly">session: ${sessionId}</span></div><time>${esc(timestamp)}</time></div>
      <textarea class="short-content" rows="5" readonly aria-label="Short-term message content">${esc(message.content || "")}</textarea>
      <div class="short-card-actions"><button class="row-button short-edit" type="button">EDIT</button><button class="row-button danger-button short-delete" type="button">DELETE</button></div>
    </article>`;
  }).join("") || `<div class="empty">NO PENDING MESSAGES</div>`;
  node.querySelectorAll(".short-edit").forEach((button) => button.addEventListener("click", () => void editShortMessage(button)));
  node.querySelectorAll(".short-delete").forEach((button) => button.addEventListener("click", () => void deleteShortMessage(button)));
}

async function loadShortTerm() {
  const context = shortTermContext();
  if (!context.personality_id) {
    $("short-status").textContent = "Please enter a persona ID";
    $("short-messages").innerHTML = "";
    return;
  }
  try {
    renderShortTerm(await api(`/short-term/personality/${encodeURIComponent(context.personality_id)}`));
  } catch (error) {
    $("short-status").textContent = error.message;
    $("short-messages").innerHTML = "";
  }
}

async function editShortMessage(button) {
  const card = button.closest(".short-message-card");
  const textarea = card?.querySelector(".short-content");
  const messageId = card?.dataset.messageId;
  if (!card || !textarea || !messageId) return;
  if (textarea.readOnly) {
    textarea.readOnly = false;
    button.textContent = "SAVE";
    textarea.focus();
    return;
  }
  const context = shortTermContext(card.dataset.sessionId);
  button.disabled = true;
  try {
    await api(`/short-term/${encodeURIComponent(messageId)}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ...context, content: textarea.value }),
    });
    await loadShortTerm();
  } catch (error) {
    $("short-status").textContent = error.message;
    button.disabled = false;
  }
}

async function deleteShortMessage(button) {
  const card = button.closest(".short-message-card");
  const messageId = card?.dataset.messageId;
  if (!card || !messageId || !window.confirm("Delete this short-term message?")) return;
  const context = shortTermContext(card.dataset.sessionId);
  button.disabled = true;
  try {
    await api(`/short-term/${encodeURIComponent(messageId)}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ...context, action: "delete" }),
    });
    await loadShortTerm();
  } catch (error) {
    $("short-status").textContent = error.message;
    button.disabled = false;
  }
}
async function importMemory() {
  const file = $("import-file").files[0];
  if (!file) return message("import-message", "请选择 TXT、Markdown 或 JSON 文件", true);
  const button = $("import-button");
  button.disabled = true;
  button.textContent = "UPLOADING...";
  message("import-message", "正在上传……");
  showUploadToast("正在上传……", true);
  let result;
  try {
    result = await api("/import_text", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        filename: file.name,
        content: await file.text(),
        session_id: $("import-session").value.trim(),
        personality_id: $("import-personality").value.trim(),
      }),
    });
  } catch (error) {
    button.disabled = false;
    button.textContent = "IMPORT";
    message("import-message", error.message, true);
    showUploadToast("上传失败：" + error.message);
    return;
  }
  button.disabled = false;
  button.textContent = "IMPORT";
  const imported = result?.inserted ?? result?.data?.inserted ?? 0;
  message("import-message", `已导入 ${imported} 条`);
  showUploadToast(`上传成功，已导入 ${imported} 条记忆`);
  try {
    await loadMemories();
  } catch (refreshError) {
    console.warn("Memory list refresh after import failed:", refreshError);
  }
}

function selectedMemoryIds() {
  return Array.from(state.selectedMemoryIds);
}

function updateSelectAllState() {
  const boxes = Array.from(document.querySelectorAll(".memory-check"));
  const selected = boxes.filter((checkbox) => state.selectedMemoryIds.has(state.memories[Number(checkbox.dataset.index)]?.id)).length;
  $("memory-select-all").checked = boxes.length > 0 && selected === boxes.length;
  $("memory-select-all").indeterminate = selected > 0 && selected < boxes.length;
}

function toggleAllMemories(event) {
  document.querySelectorAll(".memory-check").forEach((checkbox) => {
    const memory = state.memories[Number(checkbox.dataset.index)];
    if (!memory) return;
    checkbox.checked = event.target.checked;
    if (event.target.checked) state.selectedMemoryIds.add(memory.id);
    else state.selectedMemoryIds.delete(memory.id);
  });
  updateSelectAllState();
}

function closeDeleteConfirmation() {
  const modal = $("delete-confirm-modal");
  if (!modal) return;
  modal.hidden = true;
  modal.setAttribute("aria-hidden", "true");
  deleteConfirmation.ids = [];
  deleteConfirmation.label = "";
  deleteConfirmation.step = 0;
}

function openDeleteConfirmation(ids, label) {
  if (!ids.length) return message("memory-action-message", "请先勾选要删除的记忆", true);
  const modal = $("delete-confirm-modal");
  if (!modal) return message("memory-action-message", "删除确认窗口加载失败", true);
  deleteConfirmation.ids = ids;
  deleteConfirmation.label = label;
  deleteConfirmation.step = 1;
  $("delete-confirm-title").textContent = "确认删除";
  $("delete-confirm-message").textContent = `确认删除${label}中的 ${ids.length} 条长期记忆吗？`;
  $("delete-confirm-continue").textContent = "继续";
  modal.hidden = false;
  modal.setAttribute("aria-hidden", "false");
  $("delete-confirm-cancel").focus();
}

async function confirmDelete() {
  if (deleteConfirmation.step === 1) {
    deleteConfirmation.step = 2;
    $("delete-confirm-title").textContent = "最后确认";
    $("delete-confirm-message").textContent = "该操作不可撤销，确定继续删除吗？";
    $("delete-confirm-continue").textContent = "确认删除";
    $("delete-confirm-continue").focus();
    return;
  }
  if (deleteConfirmation.step !== 2) return;
  const ids = [...deleteConfirmation.ids];
  closeDeleteConfirmation();
  message("memory-action-message", "正在删除……");
  try {
    const result = await api("/memories/bulk-delete", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ ids }) });
    message("memory-action-message", `已删除 ${result.deleted ?? ids.length} 条记忆`);
    await loadMemories();
  } catch (error) {
    message("memory-action-message", error.message, true);
  }
}

function deleteMemoryIds(ids, label) {
  openDeleteConfirmation(ids, label);
}

function deleteSelectedMemories() {
  void deleteMemoryIds(selectedMemoryIds(), "选中的记忆");
}

function closeDeleteAllConfirmation() {
  const modal = $("delete-all-modal");
  if (!modal) return;
  modal.hidden = true;
  modal.setAttribute("aria-hidden", "true");
  deleteAllConfirmation.requiredText = "";
}

function openDeleteAllConfirmation() {
  const session = $("memory-session").value.trim();
  if (!session) {
    message("memory-action-message", "为防止误删，请先填写当前 SESSION，再执行删除所有记忆", true);
    return;
  }
  const modal = $("delete-all-modal");
  if (!modal) return message("memory-action-message", "删除确认窗口加载失败", true);
  deleteAllConfirmation.requiredText = session + "删除所有记忆";
  $("delete-all-confirm-text").textContent = deleteAllConfirmation.requiredText;
  $("delete-all-confirm-input").value = "";
  $("delete-all-confirm-error").textContent = "";
  modal.hidden = false;
  modal.setAttribute("aria-hidden", "false");
  $("delete-all-confirm-input").focus();
}

async function confirmDeleteAllMemories() {
  const input = $("delete-all-confirm-input").value.trim();
  if (input !== deleteAllConfirmation.requiredText) {
    $("delete-all-confirm-error").textContent = "确认文本不匹配，请完整复制上面的内容。";
    $("delete-all-confirm-input").focus();
    return;
  }
  closeDeleteAllConfirmation();
  message("memory-action-message", "正在删除所有记忆……");
  try {
    const raw = await api("/memories");
    const payload = Array.isArray(raw) ? { data: raw } : raw;
    const ids = (payload.data || []).map((memory) => memory.id).filter(Boolean);
    if (!ids.length) {
      message("memory-action-message", "当前没有可删除的长期记忆");
      return;
    }
    const result = await api("/memories/bulk-delete", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ ids }) });
    state.selectedMemoryIds.clear();
    message("memory-action-message", "已删除全部 " + (result.deleted ?? ids.length) + " 条长期记忆");
    await loadMemories();
  } catch (error) {
    message("memory-action-message", error.message, true);
  }
}
const LOVE_PROFILE_STORAGE_KEY = "romantic-memory-profile";
const DEFAULT_LOVE_PROFILE = { userName: "User", charName: "Char", startDate: "", userAvatarSrc: "", charAvatarSrc: "", userSignature: "", charSignature: "" };
let loveMemoryProfile = null;

function loveMemoryToday() {
  const now = new Date();
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}-${String(now.getDate()).padStart(2, "0")}`;
}

function loveMemoryDate(value) {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(value || ""));
  if (!match) return null;
  const date = new Date(Number(match[1]), Number(match[2]) - 1, Number(match[3]));
  if (date.getFullYear() !== Number(match[1]) || date.getMonth() !== Number(match[2]) - 1 || date.getDate() !== Number(match[3])) return null;
  return date;
}

function normalizeLoveMemoryProfile(source = {}) {
  const saved = source && typeof source === "object" ? source : {};
  return {
    userName: String(saved.userName || DEFAULT_LOVE_PROFILE.userName),
    charName: String(saved.charName || DEFAULT_LOVE_PROFILE.charName),
    startDate: loveMemoryDate(saved.startDate) ? String(saved.startDate) : loveMemoryToday(),
    userAvatarSrc: String(saved.userAvatarSrc || ""),
    charAvatarSrc: String(saved.charAvatarSrc || ""),
    userSignature: String(saved.userSignature || ""),
    charSignature: String(saved.charSignature || ""),
  };
}

function readLocalLoveMemoryProfile() {
  let saved = {};
  try { saved = JSON.parse(window.localStorage.getItem(LOVE_PROFILE_STORAGE_KEY) || "{}") || {}; }
  catch { saved = {}; }
  return normalizeLoveMemoryProfile(saved);
}

function hasLocalLoveMemoryProfile() {
  try { return Boolean(window.localStorage.getItem(LOVE_PROFILE_STORAGE_KEY)); }
  catch { return false; }
}

function cacheLocalLoveMemoryProfile(profile) {
  try { window.localStorage.setItem(LOVE_PROFILE_STORAGE_KEY, JSON.stringify(profile)); }
  catch { /* Plugin storage remains the durable source of truth. */ }
}

function readLoveMemoryProfile() {
  if (loveMemoryProfile) return { ...loveMemoryProfile };
  loveMemoryProfile = readLocalLoveMemoryProfile();
  return { ...loveMemoryProfile };
}

async function loadLoveMemoryProfile() {
  const localFallback = readLoveMemoryProfile();
  try {
    const response = await api("/profile");
    const remoteProfile = response?.data && typeof response.data === "object" ? response.data : response;
    if (response?.stored === false && hasLocalLoveMemoryProfile()) {
      loveMemoryProfile = localFallback;
      await api("/profile", { method: "POST", body: JSON.stringify(loveMemoryProfile) });
    } else {
      loveMemoryProfile = normalizeLoveMemoryProfile(remoteProfile);
    }
    cacheLocalLoveMemoryProfile(loveMemoryProfile);
  } catch (error) {
    console.warn("Love Memory profile could not be loaded from the plugin store:", error);
    loveMemoryProfile = localFallback;
  }
  renderLoveMemory(loveMemoryProfile);
  return { ...loveMemoryProfile };
}
function loveMemoryDays(startDate) {
  const start = loveMemoryDate(startDate);
  const today = loveMemoryDate(loveMemoryToday());
  if (!start || !today || start > today) return 0;
  const startUtc = Date.UTC(start.getFullYear(), start.getMonth(), start.getDate());
  const todayUtc = Date.UTC(today.getFullYear(), today.getMonth(), today.getDate());
  return Math.max(0, Math.floor((todayUtc - startUtc) / 86400000));
}
function safeLoveAvatarSrc(value) {
  const src = String(value || "").trim();
  return /^(https?:\/\/|data:image\/)/i.test(src) ? src : "";
}

function renderLoveAvatar(id, src, fallback, alt) {
  const node = $(id);
  if (!node) return;
  const safeSrc = safeLoveAvatarSrc(src);
  node.innerHTML = safeSrc ? `<img src="${esc(safeSrc)}" alt="${esc(alt)}">` : `<span>${esc(fallback)}</span>`;
  const image = node.querySelector("img");
  image?.addEventListener("error", () => { node.innerHTML = `<span>${esc(fallback)}</span>`; });
}

function renderLoveMemory(profile = readLoveMemoryProfile()) {
  const days = loveMemoryDays(profile.startDate);
  const startDate = profile.startDate || loveMemoryToday();
  $("love-memory-couple") && ($("love-memory-couple").textContent = `${profile.userName} & ${profile.charName}`);
  $("love-memory-days") && ($("love-memory-days").textContent = String(days));
  $("love-user-display-name") && ($("love-user-display-name").textContent = profile.userName);
  $("love-char-display-name") && ($("love-char-display-name").textContent = profile.charName);
  $("love-user-signature") && ($("love-user-signature").textContent = profile.userSignature || "留下你的签名吧");
  $("love-char-signature") && ($("love-char-signature").textContent = profile.charSignature || "等待一段专属签名");
  $("love-memory-copy") && ($("love-memory-copy").innerHTML = `自从 <strong>${esc(startDate)}</strong> 开始，我们已经互相陪伴 <strong>${days}</strong> 天`);
  renderLoveAvatar("love-user-avatar", profile.userAvatarSrc, profile.userName.slice(0, 1) || "U", `${profile.userName} avatar`);
  renderLoveAvatar("love-char-avatar", profile.charAvatarSrc, "♡", `${profile.charName} avatar`);
}

function openLoveMemorySettings() {
  const profile = readLoveMemoryProfile();
  $("love-user-name").value = profile.userName;
  $("love-char-name").value = profile.charName;
  $("love-start-date").value = profile.startDate;
  $("love-user-avatar-url").value = profile.userAvatarSrc.startsWith("data:image/") ? "" : profile.userAvatarSrc;
  $("love-char-avatar-url").value = profile.charAvatarSrc.startsWith("data:image/") ? "" : profile.charAvatarSrc;
  $("love-user-signature-input").value = profile.userSignature;
  $("love-char-signature-input").value = profile.charSignature;
  const modal = $("love-memory-settings-modal");
  modal.hidden = false;
  modal.setAttribute("aria-hidden", "false");
}

function closeLoveMemorySettings() {
  const modal = $("love-memory-settings-modal");
  if (!modal) return;
  modal.hidden = true;
  modal.setAttribute("aria-hidden", "true");
}

async function saveLoveMemorySettings() {
  const previous = readLoveMemoryProfile();
  const startDate = $("love-start-date").value || loveMemoryToday();
  const profile = {
    userName: $("love-user-name").value.trim() || "User",
    charName: $("love-char-name").value.trim() || "Char",
    startDate: loveMemoryDate(startDate) ? startDate : previous.startDate,
    userAvatarSrc: safeLoveAvatarSrc($("love-user-avatar-url").value) || previous.userAvatarSrc,
    charAvatarSrc: safeLoveAvatarSrc($("love-char-avatar-url").value) || previous.charAvatarSrc,
    userSignature: $("love-user-signature-input").value.trim(),
    charSignature: $("love-char-signature-input").value.trim(),
  };
  loveMemoryProfile = { ...profile };
  cacheLocalLoveMemoryProfile(profile);
  try {
    await api("/profile", { method: "POST", body: JSON.stringify(profile) });
  } catch (error) {
    console.warn("Love Memory profile could not be saved to the plugin store:", error);
  }
  renderLoveMemory(profile);
  closeLoveMemorySettings();
}
function readLoveAvatarFile(fileInputId, urlInputId) {
  const file = $(fileInputId)?.files?.[0];
  if (!file) return;
  const reader = new FileReader();
  reader.addEventListener("load", () => {
    if (typeof reader.result === "string") $(urlInputId).value = reader.result;
  });
  reader.readAsDataURL(file);
}
function switchPage(name) {
  const titles = { "love-memory": ["LOVE MEMORY", "A quiet place for the moments we keep."], dashboard: ["OVERVIEW", "Important moments, kept quietly."], memories: ["MEMORIES", "The things worth remembering."], "short-term": ["SHORT-TERM", "Moments waiting to be remembered."], transfer: ["TRANSFER", "Move memories with care."] };
  const title = titles[name] || titles.dashboard;
  document.querySelectorAll(".page").forEach((page) => page.classList.toggle("active", page.id === `page-${name}`));
  document.querySelectorAll(".nav-item").forEach((item) => item.classList.toggle("active", item.dataset.page === name));
  $("page-title").textContent = title[0];
  $("page-description").textContent = title[1];
  const backButton = $("back-to-love-memory");
  if (backButton) {
    backButton.hidden = name === "love-memory";
    backButton.setAttribute("aria-hidden", String(name === "love-memory"));
  }
  if (name === "love-memory") { renderLoveMemory(); return; }
  if (name === "dashboard") void loadMonitoring();
  if (name === "memories") void loadMemories();
}

function readMode() {
  try { return window.localStorage.getItem("romantic-memory-mode"); }
  catch { return null; }
}

function saveMode(mode) {
  try { window.localStorage.setItem("romantic-memory-mode", mode); }
  catch { /* AstrBot sandbox iframe may disable localStorage. */ }
}

function toggleMode() {
  const dark = document.body.dataset.mode !== "dark";
  document.body.dataset.mode = dark ? "dark" : "light";
  $("mode-toggle").textContent = dark ? "LIGHTMODE" : "DARKMODE";
  saveMode(document.body.dataset.mode);
}

function bindEvent(id, eventName, handler) {
  const node = $(id);
  if (node) node.addEventListener(eventName, handler);
  else console.error(`Romantic memory page element missing: ${id}`);
}


async function downloadTransferFile(button) {
  const endpoint = button.dataset.downloadEndpoint;
  const params = JSON.parse(button.dataset.downloadParams || "{}");
  const filename = button.dataset.downloadFilename || "romantic_memory_export.bin";
  const bridge = window.AstrBotPluginPage;
  button.disabled = true;
  try {
    if (bridge && typeof bridge.download === "function") {
      await bridge.download(parseApiPath(endpoint).endpoint, params, filename);
      return;
    }
    const query = new URLSearchParams(params).toString();
    const response = await fetch(`${API}${endpoint}${query ? `?${query}` : ""}`, { credentials: "same-origin" });
    if (!response.ok) throw new Error(`Download failed (${response.status})`);
    const blobUrl = URL.createObjectURL(await response.blob());
    const anchor = document.createElement("a");
    anchor.href = blobUrl;
    anchor.download = filename;
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
    setTimeout(() => URL.revokeObjectURL(blobUrl), 0);
  } catch (error) {
    console.error("Romantic memory transfer download failed:", error);
    message("import-message", error.message, true);
  } finally {
    button.disabled = false;
  }
}

function bindEvents() {
  document.querySelectorAll(".nav-item").forEach((item) => item.addEventListener("click", () => switchPage(item.dataset.page)));
  bindEvent("mode-toggle", "click", toggleMode);
  bindEvent("back-to-love-memory", "click", () => switchPage("love-memory"));
  bindEvent("love-memory-settings", "click", openLoveMemorySettings);
  bindEvent("love-settings-cancel", "click", closeLoveMemorySettings);
  bindEvent("love-settings-save", "click", saveLoveMemorySettings);
  document.querySelector("[data-love-settings-cancel]")?.addEventListener("click", closeLoveMemorySettings);
  bindEvent("love-user-avatar-file", "change", () => readLoveAvatarFile("love-user-avatar-file", "love-user-avatar-url"));
  bindEvent("love-char-avatar-file", "change", () => readLoveAvatarFile("love-char-avatar-file", "love-char-avatar-url"));
  document.querySelectorAll(".love-action").forEach((button) => button.addEventListener("click", () => { button.classList.add("is-pressed"); setTimeout(() => button.classList.remove("is-pressed"), 220); switchPage(button.dataset.loveAction); }));
  bindEvent("refresh-monitor", "click", loadMonitoring);
  bindEvent("refresh-memories", "click", loadMemories);
  bindEvent("search-memories", "click", loadMemories);
  bindEvent("memory-personality", "change", loadMemories);
  bindEvent("memory-select-all", "change", toggleAllMemories);
  bindEvent("memory-page-size", "change", changeMemoryPageSize);
  bindEvent("memory-prev", "click", () => changeMemoryPage(-1));
  bindEvent("memory-next", "click", () => changeMemoryPage(1));
  bindEvent("delete-selected", "click", deleteSelectedMemories);
  bindEvent("clear-current", "click", openDeleteAllConfirmation);
  bindEvent("delete-confirm-cancel", "click", closeDeleteConfirmation);
  bindEvent("delete-confirm-continue", "click", () => void confirmDelete());
  document.querySelector("[data-confirm-cancel]")?.addEventListener("click", closeDeleteConfirmation);
  bindEvent("delete-all-cancel", "click", closeDeleteAllConfirmation);
  bindEvent("delete-all-confirm", "click", () => void confirmDeleteAllMemories());
  document.querySelector("[data-delete-all-cancel]")?.addEventListener("click", closeDeleteAllConfirmation);
  bindEvent("save-memory", "click", saveMemory);
  bindEvent("clear-editor", "click", clearEditor);
  bindEvent("load-short", "click", loadShortTerm);
  bindEvent("import-button", "click", importMemory);
  document.querySelectorAll(".transfer-download").forEach((button) => {
    button.addEventListener("click", () => void downloadTransferFile(button));
  });
}

async function init() {
  // Bind navigation before any sandbox-sensitive storage or network operation.
  bindEvents();
  switchPage("love-memory");

  await window.AstrBotPluginPage?.ready?.();
  await loadLoveMemoryProfile();
  setInterval(() => renderLoveMemory(), 60000);
  if (readMode() === "dark") {
    document.body.dataset.mode = "dark";
    $("mode-toggle").textContent = "LIGHTMODE";
  }
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", init, { once: true });
} else {
  void init();
}
