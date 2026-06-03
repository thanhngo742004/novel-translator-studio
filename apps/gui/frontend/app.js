const pages = [
  ["home", "Trang chủ"],
  ["projects", "Dự án truyện"],
  ["translate", "Dịch truyện"],
  ["review", "Kiểm tra bản dịch"],
  ["export", "Xuất file"],
  ["settings", "Cài đặt"],
  ["manga", "Manga / Ảnh — Sắp ra mắt"],
];

const actionMap = {
  "home.create_project": () => showPage("projects"),
  "home.continue_project": async () => {
    await loadProjects();
    if (!state.projects.length) {
      showPage("projects");
      showToast("Bạn chưa có dự án nào. Hãy tạo dự án truyện đầu tiên.");
      return;
    }
    state.selectedProject = state.projects[state.projects.length - 1].slug;
    showPage("translate");
  },
  "home.review_queue": async () => {
    await loadReviewQueue(true);
    showPage("review");
  },
  "home.check_ltp": async () => loadLtpStatus(true),
  "home.open_settings": () => showPage("settings"),
  "projects.open": () => openProjectFolder(),
  "projects.translate": () => {
    renderProjectSelect();
    showPage("translate");
  },
  "projects.review": async () => {
    await loadReviewQueue(false);
    showPage("review");
  },
  "projects.export": () => showPage("export"),
  "projects.technical": () => openProjectDetails(),
  "wizard.choose_file": () => showToast("Chọn file: nhập đường dẫn trong bước tạo dự án."),
  "wizard.scan_chapters": () => projectAction("/scan-chapters", "Đã ghi nhận yêu cầu quét chương."),
  "wizard.nlp_detect": () => projectAction("/nlp/cache-build", "Đã ghi nhận yêu cầu nhận diện tự động."),
  "wizard.next": () => advanceWizard(),
  "wizard.create": () => createProject(),
  "translate.trial_1": () => selectPreset("trial", 1, "Dịch thử 1 chương"),
  "translate.trial_3": () => selectPreset("trial", 3, "Dịch thử 3 chương"),
  "translate.trial_10": () => selectPreset("trial", 10, "Dịch thử 10 chương"),
  "translate.batch_20": () => selectPreset("batch", 20, "Dịch 20 chương"),
  "translate.batch_50": () => selectPreset("batch", 50, "Dịch 50 chương"),
  "translate.resume": () => selectPreset("resume", 0, "Tiếp tục từ chỗ dừng"),
  "translate.start": () => translateSelectedPreset(),
  "translate.pause": () => showToast("Tạm dừng: Sắp hỗ trợ."),
  "translate.stop_after_current": () => showToast("Dừng sau chương hiện tại: Sắp hỗ trợ khi core hỗ trợ graceful stop."),
  "translate.technical": () => openTechnical(),
  "job.details": () => showJobDetails(),
  "job.open_artifacts": () => openJobArtifacts(),
  "job.resume": () => selectPreset("resume", 0, "Tiếp tục từ chỗ dừng"),
  "job.stop_after_current": () => showToast("Dừng sau chương hiện tại: Sắp hỗ trợ khi core hỗ trợ graceful stop."),
  "review.open_item": () => loadReviewQueue(),
  "review.save": () => saveReview(false),
  "review.learn": () => saveReview(true),
  "review.save_only": () => saveReview(false),
  "review.mark_reviewed": () => markReviewed(),
  "review.skip": () => skipReviewItem(),
  "review.toggle_source": () => toggleSource(),
  "review.technical": () => openTechnical(),
  "export.txt": () => exportProject("txt"),
  "export.epub": () => showToast("Xuất EPUB: Sắp hỗ trợ."),
  "export.review_package": () => exportProject("review_package"),
  "export.copy_path": () => copyOutputPath(),
  "settings.check_api": () => testProviderSettings(),
  "settings.edit_provider": () => enableProviderEditing(),
  "settings.save_provider": () => saveProviderSettings(),
  "settings.clear_api_key": () => clearProviderApiKey(),
  "settings.cancel_provider": () => loadProviderSettings(true),
  "settings.api_help": () => openTechnical("GUI lưu provider trong workspace/config/gui_provider.local.json. GUI-saved config ưu tiên cho lệnh chạy từ GUI; CLI vẫn dùng env/config truyền thống. API key không hiển thị lại trong response."),
  "settings.check_ltp": () => loadLtpStatus(true),
  "settings.start_ltp": () => startLtp(),
  "settings.open_workspace": () => openWorkspaceFolder(),
  "settings.choose_workspace": () => validateWorkspacePath(),
  "settings.refresh_status": () => refreshAllStatus(),
  "manga.plan": () => openTechnical("Manga / Ảnh là placeholder Phase 7. Không có OCR, xử lý ảnh, nhập ảnh hay production manga action."),
  "manga.close": () => showPage("home"),
};

const state = {
  projects: [],
  selectedProject: null,
  selectedReviewItem: null,
  reviewItems: [],
  wizardStep: 0,
  lastOutputPath: "artifacts/exports",
  safeDefaults: {},
  selectedPreset: { mode: "batch", chapterCount: 20, label: "Dịch 20 chương" },
  activeJobId: null,
  jobPollTimer: null,
  currentAction: null,
  lastProviderTestOk: false,
  providerStatusLockedUntil: 0,
};

document.addEventListener("DOMContentLoaded", () => {
  renderNavigation();
  renderWizard();
  bindActions();
  document.getElementById("close-technical").addEventListener("click", () => document.getElementById("technical-dialog").close());
  showPage("home");
  loadStatus(false);
  loadProjects();
  loadProviderSettings();
  loadWorkspaceStatus();
  loadGuiVersion();
  document.getElementById("chapter-start").addEventListener("input", updateRangeMessage);
  document.getElementById("chapter-end").addEventListener("input", updateRangeMessage);
  updateRangeMessage();
});

function renderNavigation() {
  const nav = document.getElementById("nav-list");
  nav.innerHTML = "";
  for (const [id, label] of pages) {
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = label;
    button.dataset.page = id;
    button.addEventListener("click", () => showPage(id));
    nav.appendChild(button);
  }
}

function renderWizard() {
  const labels = ["Chọn file truyện", "Kiểm tra chương", "Nhận diện tên riêng & thuật ngữ", "Dịch thử", "Kiểm tra chất lượng", "Dịch hàng loạt", "Xuất file"];
  const list = document.getElementById("wizard-steps");
  list.innerHTML = "";
  labels.forEach((label, index) => {
    const item = document.createElement("li");
    item.textContent = label;
    item.className = index === state.wizardStep ? "active-step" : "";
    list.appendChild(item);
  });
}

function bindActions() {
  document.addEventListener("click", async (event) => {
    const projectButton = event.target.closest("[data-project-action]");
    if (projectButton) {
      state.selectedProject = projectButton.dataset.project;
      await runAction(projectButton.dataset.projectAction, projectButton);
      return;
    }
    const actionButton = event.target.closest("[data-action]");
    if (!actionButton) {
      return;
    }
    if (actionButton.disabled && actionMap[actionButton.dataset.action]) {
      showToast(actionButton.textContent.includes("Sắp hỗ trợ") ? actionButton.textContent : "Nút này hiện chưa hỗ trợ.");
      return;
    }
    await runAction(actionButton.dataset.action, actionButton);
  });
}

async function runAction(action, element) {
  const handler = actionMap[action];
  if (!handler) {
    showToast(`Chưa có wiring cho ${action}.`);
    return;
  }
  const original = element.textContent;
  const previousAction = state.currentAction;
  state.currentAction = {
    buttonId: element.id || element.dataset.action || element.dataset.projectAction || element.textContent.trim(),
    actionName: action,
    endpoint: "frontend-state",
    payloadSummary: "—",
    responseStatus: "running",
    responseMessage: "Đang xử lý…",
    timestamp: new Date().toLocaleString(),
  };
  renderActionLog(state.currentAction);
  try {
    setButtonState(element, "running");
    element.textContent = "Đang xử lý…";
    await handler();
    setButtonState(element, "success");
    if (state.currentAction && state.currentAction.actionName === action && state.currentAction.responseStatus === "running") {
      state.currentAction.responseStatus = "success";
      state.currentAction.responseMessage = "Hoàn tất thao tác frontend.";
      state.currentAction.timestamp = new Date().toLocaleString();
      renderActionLog(state.currentAction);
    }
  } catch (error) {
    setButtonState(element, error.name === "BlockedActionError" ? "blocked" : "error");
    element.dataset.retryAvailable = "true";
    if (state.currentAction && state.currentAction.actionName === action) {
      state.currentAction.responseStatus = "error";
      state.currentAction.responseMessage = error.message || "Có lỗi xảy ra.";
      state.currentAction.timestamp = new Date().toLocaleString();
      renderActionLog(state.currentAction);
    }
    showToast(error.message || "Có lỗi xảy ra. Hãy thử lại.");
  } finally {
    state.currentAction = previousAction;
    element.textContent = original;
    if (element.dataset.state === "success") {
      window.setTimeout(() => setButtonState(element, "idle"), 900);
    }
  }
}

function setButtonState(element, stateName) {
  element.dataset.state = stateName;
  element.dataset.retryAvailable = stateName === "warning" || stateName === "blocked" || stateName === "error" ? "true" : "false";
  element.setAttribute("aria-busy", stateName === "running" ? "true" : "false");
}

function showPage(id) {
  document.querySelectorAll(".page").forEach((page) => page.classList.toggle("active", page.id === `page-${id}`));
  document.querySelectorAll(".nav-list button").forEach((button) => {
    const current = button.dataset.page === id;
    button.setAttribute("aria-current", current ? "page" : "false");
  });
  const page = pages.find(([pageId]) => pageId === id);
  document.getElementById("page-title").textContent = page ? page[1] : "NTS Studio";
  if (id === "translate") {
    renderProjectSelect();
  }
  document.getElementById("main").focus({ preventScroll: true });
}

async function api(path, options = {}) {
  const method = options.method || "GET";
  const payloadSummary = summarizeRequestBody(options.body);
  updateActiveAction({ endpoint: `${method} ${path}`, payloadSummary, responseStatus: "running", responseMessage: "Đang gọi backend…" });
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const payload = await response.json();
  if (!payload.ok) {
    updateActiveAction({ responseStatus: `${response.status}`, responseMessage: payload.error?.message || "Backend request failed." });
    throw new Error(payload.error?.message || "Backend request failed.");
  }
  updateActiveAction({ responseStatus: `${response.status}`, responseMessage: responseMessage(payload.data) });
  return payload.data;
}

function updateActiveAction(patch) {
  if (!state.currentAction) {
    return;
  }
  state.currentAction = { ...state.currentAction, ...patch, timestamp: new Date().toLocaleString() };
  renderActionLog(state.currentAction);
}

function renderActionLog(entry) {
  const log = document.getElementById("debug-action-log");
  if (!log || !entry) {
    return;
  }
  const rows = [
    ["button id", entry.buttonId || "—"],
    ["action", entry.actionName || "—"],
    ["endpoint", entry.endpoint || "—"],
    ["payload", entry.payloadSummary || "—"],
    ["response", `${entry.responseStatus || "—"}: ${entry.responseMessage || "—"}`],
    ["timestamp", entry.timestamp || "—"],
  ];
  log.innerHTML = rows.map(([key, value]) => `<div><dt>${escapeHtml(key)}</dt><dd>${escapeHtml(value)}</dd></div>`).join("");
}

function summarizeRequestBody(body) {
  if (!body) {
    return "—";
  }
  try {
    return JSON.stringify(redactSecrets(JSON.parse(body)));
  } catch {
    return "[unparsed body]";
  }
}

function redactSecrets(value) {
  if (Array.isArray(value)) {
    return value.map(redactSecrets);
  }
  if (value && typeof value === "object") {
    return Object.fromEntries(Object.entries(value).map(([key, item]) => [key, /api[_-]?key|secret|token|password/i.test(key) ? "********" : redactSecrets(item)]));
  }
  return value;
}

function responseMessage(data) {
  if (!data) {
    return "OK";
  }
  return data.message || data.data?.message || data.status || data.data?.status || data.phase_label || data.job_id || data.data?.job_id || "OK";
}

async function apiText(path, success) {
  await api(path);
  return success;
}

async function loadStatus(showMessage) {
  const data = await api("/api/system/status");
  state.safeDefaults = data.readiness || {};
  document.getElementById("readiness-pill").textContent = data.readiness?.label || "Hệ thống sẵn sàng";
  document.getElementById("workspace-path").textContent = data.workspace?.path || "Chưa chọn workspace";
  if (Date.now() > state.providerStatusLockedUntil) {
    setProviderStatus(providerSummary(data.providers || []));
  }
  document.getElementById("ltp-status").textContent = ltpDisplayText(data.ltp);
  renderStatusGrid(data);
  if (showMessage) {
    showToast("Đã kiểm tra API mà không hiển thị raw key.");
  }
}

async function loadGuiVersion() {
  const data = await api("/api/gui/version");
  const frontendHash = data.app_js?.hash || data.frontend_asset_version || "unknown";
  const backendHash = data.backend_service?.hash || data.git_commit || "unknown";
  document.getElementById("gui-version-label").textContent = `GUI build: phase7.5 / ${frontendHash}`;
  document.getElementById("backend-version-label").textContent = `Backend: ${data.phase_label} / ${backendHash} / started ${data.server_start_time}`;
}

async function refreshAllStatus() {
  await loadGuiVersion();
  await loadStatus(false);
  await loadLtpStatus(false);
  await loadProviderSettings(false);
  await loadWorkspaceStatus();
  showToast("Đã làm mới trạng thái GUI/backend.");
}

async function loadProviderSettings(showMessage = false) {
  const data = await api("/api/settings/provider");
  fillProviderForm(data.settings || {});
  document.getElementById("provider-config-path").textContent = `Lưu tại: ${data.config_path}`;
  if (Date.now() > state.providerStatusLockedUntil) {
    setProviderStatus(data.settings?.api_key_configured ? "API key đã lưu và được ẩn." : "Chưa lưu API key cho GUI.");
  }
  enableProviderEditing(false);
  if (showMessage) {
    showToast("Đã hủy thay đổi và tải lại cấu hình đã lưu.");
  }
}

async function saveProviderSettings() {
  const form = document.getElementById("provider-form");
  const body = Object.fromEntries(new FormData(form).entries());
  const data = await api("/api/settings/provider", { method: "POST", body: JSON.stringify(body) });
  state.lastProviderTestOk = false;
  fillProviderForm(data.settings || {});
  document.getElementById("provider-config-path").textContent = `Lưu tại: ${data.config_path}`;
  setProviderStatus(data.settings?.api_key_configured ? "API key đã lưu và được ẩn." : "Đã lưu cấu hình không có API key.", { lockMs: 5000 });
  showToast("Đã lưu cấu hình provider cho GUI.");
  await loadStatus(false);
}

function enableProviderEditing(showMessage = true) {
  const form = document.getElementById("provider-form");
  Array.from(form.elements).forEach((field) => {
    field.disabled = false;
  });
  if (showMessage) {
    showToast("Có thể sửa cấu hình provider.");
  }
}

async function clearProviderApiKey() {
  const form = document.getElementById("provider-form");
  const body = Object.fromEntries(new FormData(form).entries());
  body.clear_api_key = true;
  body.api_key = "";
  const data = await api("/api/settings/provider", { method: "POST", body: JSON.stringify(body) });
  state.lastProviderTestOk = false;
  fillProviderForm(data.settings || {});
  setProviderStatus("Đã xóa API key đã lưu.", { lockMs: 5000 });
  showToast("Đã xóa API key khỏi cấu hình GUI.");
}

async function testProviderSettings() {
  const form = document.getElementById("provider-form");
  const body = Object.fromEntries(new FormData(form).entries());
  const data = await api("/api/settings/provider/test", { method: "POST", body: JSON.stringify(body) });
  state.lastProviderTestOk = Boolean(data.ok);
  fillProviderForm(data.settings || {});
  const message = data.message || (data.ok ? "Kiểm tra API thành công." : "Kiểm tra API thất bại.");
  setProviderStatus(message, { lockMs: 30000 });
  showToast(message);
}

function setProviderStatus(message, options = {}) {
  document.getElementById("provider-status").textContent = message;
  if (options.lockMs) {
    state.providerStatusLockedUntil = Date.now() + options.lockMs;
  }
}

function fillProviderForm(settings) {
  const form = document.getElementById("provider-form");
  if (!form) {
    return;
  }
  for (const [name, value] of Object.entries(settings)) {
    const field = form.elements.namedItem(name);
    if (!field) {
      continue;
    }
    field.value = name === "api_key" ? "" : value ?? "";
  }
  const keyField = form.elements.namedItem("api_key");
  if (keyField) {
    keyField.value = "";
    keyField.placeholder = settings.api_key_configured ? "•••••••• saved — nhập key mới để thay đổi" : "Nhập API key";
  }
}

async function loadWorkspaceStatus() {
  const data = await api("/api/workspace");
  document.getElementById("workspace-path").textContent = data.path;
  document.getElementById("workspace-input").value = data.path;
}

async function validateWorkspacePath() {
  const workspacePath = document.getElementById("workspace-input").value.trim();
  if (!workspacePath) {
    showToast("Nhập đường dẫn workspace trước.");
    return;
  }
  const data = await api("/api/workspace", {
    method: "POST",
    body: JSON.stringify({ workspace_path: workspacePath }),
  });
  document.getElementById("workspace-path").textContent = `Đã xác thực: ${data.path}`;
  showToast(data.message);
}

async function openWorkspaceFolder() {
  const data = await api("/api/workspace/open-folder", { method: "POST", body: JSON.stringify({ open: true }) });
  if (data.opened) {
    showToast("Đã mở workspace trong File Explorer.");
    return;
  }
  await copyText(data.path);
  showToast(`${data.message} Đã sao chép: ${data.path}`);
}

async function startLtp() {
  const data = await api("/api/ltp/start", { method: "POST" });
  if (data.copyable_command) {
    await copyText(data.copyable_command);
  }
  document.getElementById("ltp-status").textContent = data.message;
  showToast(data.copyable_command ? `${data.message} Đã sao chép lệnh.` : data.message);
}

async function loadLtpStatus(showMessage) {
  const data = await api("/api/ltp/status?fresh=1");
  document.getElementById("ltp-status").textContent = ltpDisplayText(data);
  if (showMessage) {
    showToast(data.message || "Đã kiểm tra LTP.", data.healthy ? "success" : "warning");
  }
}

function ltpDisplayText(ltp) {
  if (!ltp) {
    return "LTP: unknown";
  }
  const provider = ltp.provider || "LTP";
  const message = ltp.message || ltp.status || "unknown";
  const suffix = ltp.healthy ? "healthy" : ltp.status || "unavailable";
  return `${provider}: ${message} (${suffix})`;
}

function providerSummary(providers) {
  if (!providers.length) {
    return "Không kết nối được API dịch. Kiểm tra lại cấu hình API trong Cài đặt.";
  }
  return providers.map((provider) => `${provider.key}: ${provider.api_key_configured ? "đã cấu hình env" : "chưa có env key"}`).join("; ");
}

function renderStatusGrid(data) {
  const cards = [
    ["Hệ thống sẵn sàng", data.readiness?.status || "ready", "emerald"],
    ["LTP", ltpDisplayText(data.ltp), data.ltp?.healthy ? "blue" : "amber"],
    ["API", providerSummary(data.providers || []), "amber"],
    ["Lần dịch gần nhất", "Mở dự án để xem tiến độ", "purple"],
  ];
  document.getElementById("status-grid").innerHTML = cards.map(([title, value]) => `<article class="status-card"><strong>${escapeHtml(title)}</strong><span>${escapeHtml(value)}</span></article>`).join("");
}

async function loadProjects() {
  const data = await api("/api/projects");
  state.projects = data.projects || [];
  if (!state.selectedProject && state.projects.length) {
    state.selectedProject = state.projects[0].slug;
  }
  renderProjects();
  renderProjectSelect();
}

function renderProjects() {
  const empty = "Bạn chưa có dự án nào. Hãy tạo dự án truyện đầu tiên.";
  const html = state.projects.length ? state.projects.map(projectCard).join("") : `<p class="muted">${empty}</p>`;
  document.getElementById("home-projects").innerHTML = html;
  document.getElementById("project-cards").innerHTML = html;
}

function projectCard(project) {
  return `<article class="project-card">
    <div><h4>${escapeHtml(project.name)}</h4><span>${escapeHtml(project.source_lang)} → ${escapeHtml(project.target_lang)}</span></div>
    <div class="project-meta"><span class="chip">${project.chapter_count} chương</span><span class="chip">${project.translated_count} đã dịch</span><span class="chip">${project.progress_percent}%</span><span class="chip">${escapeHtml(project.status)}</span></div>
    <p>Việc nên làm tiếp: ${escapeHtml(project.next_action)}</p>
    <div class="button-row">
      <button type="button" data-project="${escapeHtml(project.slug)}" data-project-action="projects.open">Mở dự án</button>
      <button type="button" data-project="${escapeHtml(project.slug)}" data-project-action="projects.translate">Dịch tiếp</button>
      <button type="button" data-project="${escapeHtml(project.slug)}" data-project-action="projects.review">Kiểm tra bản dịch</button>
      <button type="button" data-project="${escapeHtml(project.slug)}" data-project-action="projects.export">Xuất file</button>
      <button type="button" data-project="${escapeHtml(project.slug)}" data-project-action="projects.technical">Xem chi tiết kỹ thuật</button>
    </div>
  </article>`;
}

function renderProjectSelect() {
  const select = document.getElementById("project-select");
  select.innerHTML = state.projects.map((project) => `<option value="${escapeHtml(project.slug)}">${escapeHtml(project.name)}</option>`).join("");
  select.value = state.selectedProject || "";
  select.onchange = () => {
    state.selectedProject = select.value;
  };
}

function openProjectDetails() {
  const project = state.projects.find((item) => item.slug === state.selectedProject);
  if (!project) {
    showPage("projects");
    showToast("Chọn một dự án để xem chi tiết.");
    return;
  }
  openTechnical(JSON.stringify({
    title: "Chi tiết dự án",
    project: {
      slug: project.slug,
      name: project.name,
      language_pair: `${project.source_lang} → ${project.target_lang}`,
      chapter_count: project.chapter_count,
      translated_count: project.translated_count,
      progress_percent: project.progress_percent,
      next_action: project.next_action,
    },
  }, null, 2));
}

async function openProjectFolder() {
  const project = selectedProject();
  const data = await api(`/api/projects/${encodeURIComponent(project)}/open-folder`, {
    method: "POST",
    body: JSON.stringify({ target: "preferred_output_path", open: true }),
  });
  state.lastOutputPath = data.path || data.preferred_output_path || data.output_path;
  if (data.opened) {
    showToast("Đã mở thư mục TXT của dự án.");
  } else {
    await copyText(state.lastOutputPath);
    showToast(`${data.message} Đã sao chép: ${state.lastOutputPath}`);
  }
}

async function createProject() {
  const form = document.getElementById("project-form");
  const data = Object.fromEntries(new FormData(form).entries());
  const result = await api("/api/projects/import", { method: "POST", body: JSON.stringify(data) });
  state.selectedProject = result.project.slug;
  await loadProjects();
  showToast("Đã tạo dự án truyện mới.");
}

async function projectAction(suffix, message) {
  const project = selectedProject();
  await api(`/api/projects/${encodeURIComponent(project)}${suffix}`, { method: "POST", body: JSON.stringify({ source: "gui" }) });
  showToast(message);
}

async function translate(mode, chapterCount) {
  const project = selectedProject();
  const range = currentChapterRange();
  if (!range.valid) {
    setJobStatus("error", range.message);
    throw new Error(range.message);
  }
  if (!state.lastProviderTestOk) {
    const providerTest = await api("/api/settings/provider/test", { method: "POST", body: JSON.stringify({}) });
    state.lastProviderTestOk = Boolean(providerTest.ok);
    setProviderStatus(providerTest.message || (providerTest.ok ? "Kiểm tra API thành công." : "Kiểm tra API thất bại."), { lockMs: 30000 });
    if (!providerTest.ok) {
      throw new Error("Chưa kiểm tra được API. Hãy vào Cài đặt và bấm Kiểm tra API.");
    }
  }
  const path = mode === "resume" ? "resume" : mode;
  setJobStatus("running", `Đang tạo tác vụ ${state.selectedPreset.label} cho chương ${range.start}–${range.end}…`);
  const result = await api(`/api/projects/${encodeURIComponent(project)}/translate/${path}`, {
    method: "POST",
    body: JSON.stringify({
      chapter_count: chapterCount,
      chapter_start: range.start,
      chapter_end: range.end,
      resumable: document.getElementById("resumable-option").checked,
    }),
  });
  state.lastOutputPath = result.txt_output_path || result.artifact_path || state.lastOutputPath;
  state.activeJobId = result.job_id || result.run_id;
  setJobStatus("running", `${result.message || "Đã bắt đầu tác vụ dịch an toàn"}. Job: ${state.activeJobId}`);
  showToast("Đã bắt đầu tác vụ dịch an toàn.");
  await pollJobStatus(state.activeJobId);
  startJobPolling(state.activeJobId);
}

function selectPreset(mode, chapterCount, label) {
  state.selectedPreset = { mode, chapterCount, label };
  const range = currentChapterRange(false);
  const start = range.start || 1;
  if (mode === "resume") {
    document.getElementById("resumable-option").checked = true;
  } else {
    document.getElementById("chapter-start").value = String(start);
    document.getElementById("chapter-end").value = String(start + chapterCount - 1);
  }
  updateRangeMessage();
  document.querySelectorAll(".preset-grid button").forEach((button) => {
    button.setAttribute("aria-pressed", button.textContent.trim() === label ? "true" : "false");
  });
  showToast(`Đã chọn: ${label}. Bấm Bắt đầu dịch để chạy.`);
}

async function translateSelectedPreset() {
  const preset = state.selectedPreset || { mode: "batch", chapterCount: 20, label: "Dịch 20 chương" };
  await translate(preset.mode, preset.chapterCount);
}

function currentChapterRange(strict = true) {
  const start = Number.parseInt(document.getElementById("chapter-start").value || "1", 10);
  const end = Number.parseInt(document.getElementById("chapter-end").value || String(start), 10);
  const valid = Number.isInteger(start) && Number.isInteger(end) && start > 0 && end >= start;
  const message = valid ? `Sẽ dịch chương ${start}–${end}` : "Khoảng chương không hợp lệ. Chương kết thúc phải >= chương bắt đầu.";
  if (strict && !valid) {
    return { start, end, valid, message };
  }
  return { start: Number.isInteger(start) && start > 0 ? start : 1, end: Number.isInteger(end) && end > 0 ? end : 1, valid, message };
}

function updateRangeMessage() {
  const range = currentChapterRange(false);
  const message = document.getElementById("chapter-range-message");
  message.textContent = range.message;
  message.dataset.state = range.valid ? "success" : "error";
}

function setJobStatus(stateName, message) {
  const status = document.getElementById("translation-job-status");
  status.dataset.state = stateName;
  status.textContent = message;
}

function startJobPolling(jobId) {
  if (state.jobPollTimer) {
    window.clearInterval(state.jobPollTimer);
  }
  state.jobPollTimer = window.setInterval(() => pollJobStatus(jobId), 3000);
}

async function pollJobStatus(jobId) {
  const job = await api(`/api/jobs/${encodeURIComponent(jobId)}`);
  renderJobProgress(job);
  if (["completed", "blocked", "error", "cancelled"].includes(job.status) && state.jobPollTimer) {
    window.clearInterval(state.jobPollTimer);
    state.jobPollTimer = null;
  }
}

function renderJobProgress(job) {
  const panel = document.getElementById("translation-progress-panel");
  panel.hidden = false;
  panel.dataset.state = job.status;
  const progress = document.getElementById("translation-progress");
  if ((job.chunks_total == null || job.chunks_total === 0) && job.status === "running") {
    progress.removeAttribute("value");
  } else {
    progress.value = job.percent || 0;
  }
  document.getElementById("progress-project").textContent = job.project_name || job.project;
  document.getElementById("progress-range").textContent = `Chương ${job.chapter_start}–${job.chapter_end}`;
  document.getElementById("progress-status").textContent = job.status;
  document.getElementById("progress-current-chapter").textContent = job.current_chapter || "—";
  document.getElementById("progress-current-chunk").textContent = job.current_chunk || "—";
  document.getElementById("progress-chapters").textContent = `${job.chapters_completed || 0} / ${job.chapters_total || 0}`;
  document.getElementById("progress-chunks").textContent = `${job.chunks_completed || 0} / ${job.chunks_total || "—"}`;
  document.getElementById("progress-percent").textContent = `${job.percent || 0}%`;
  document.getElementById("progress-elapsed").textContent = formatSeconds(job.elapsed_seconds || 0);
  document.getElementById("progress-eta").textContent = job.eta_seconds == null ? "—" : formatSeconds(job.eta_seconds);
  document.getElementById("progress-run-id").textContent = job.job_id;
  document.getElementById("progress-message").textContent = job.latest_message || "Đang cập nhật...";
  document.getElementById("progress-artifact").textContent = `TXT: ${job.txt_output_path || "—"} | Artifact: ${job.artifact_path || "—"}`;
  setJobStatus(job.status === "completed" ? "success" : job.status, job.latest_message || job.status);
}

async function showJobDetails() {
  if (!state.activeJobId) {
    showToast("Chưa có job nào để xem chi tiết.");
    return;
  }
  const job = await api(`/api/jobs/${encodeURIComponent(state.activeJobId)}`);
  openTechnical(JSON.stringify(job, null, 2));
}

async function openJobArtifacts() {
  if (!state.activeJobId) {
    showToast("Chưa có tác vụ dịch nào để mở kết quả.", "warning");
    return;
  }
  const job = await api(`/api/jobs/${encodeURIComponent(state.activeJobId)}`);
  const outputPath = job.txt_output_path || job.artifact_path;
  if (!outputPath) {
    showToast("Tác vụ chưa có thư mục kết quả.", "warning");
    return;
  }
  state.lastOutputPath = outputPath;
  const opened = await api("/api/system/open-path", {
    method: "POST",
    body: JSON.stringify({ path: outputPath, open: true }),
  });
  if (opened.opened) {
    showToast("Đã mở thư mục kết quả.");
    return;
  }
  await copyText(opened.path || outputPath);
  showToast(`Mở thư mục không được hỗ trợ. Đã sao chép đường dẫn: ${opened.path || outputPath}`, "warning");
}

function formatSeconds(value) {
  const seconds = Number(value || 0);
  if (seconds < 60) {
    return `${seconds}s`;
  }
  const minutes = Math.floor(seconds / 60);
  return `${minutes}m ${seconds % 60}s`;
}

async function loadReviewQueue(aggregate = false) {
  const project = selectedProject(false);
  const path = aggregate || !project ? "/api/review-queue" : `/api/projects/${encodeURIComponent(project)}/review-queue`;
  const data = await api(path);
  const items = data.items || [];
  state.reviewItems = items;
  const queue = document.getElementById("review-queue");
  if (!items.length) {
    queue.textContent = "Không có bản dịch nào cần kiểm tra.";
    return;
  }
  state.selectedReviewItem = items[0].id;
  queue.innerHTML = items.map((item) => `<button type="button" data-review-id="${escapeHtml(item.id)}">${escapeHtml(item.title || item.id)}</button>`).join("");
  queue.querySelectorAll("button").forEach((button) => button.addEventListener("click", () => openReviewItem(button.dataset.reviewId)));
  await openReviewItem(state.selectedReviewItem);
}

async function openReviewItem(itemId) {
  const data = await api(`/api/review/${encodeURIComponent(itemId)}`);
  state.selectedReviewItem = itemId;
  document.getElementById("source-text").value = data.item.source_text || "Không có bản gốc trong mục này.";
  document.getElementById("current-translation").value = data.item.text || "";
  document.getElementById("reviewed-output").value = data.item.text || "";
}

async function saveReview(learn) {
  if (!state.selectedReviewItem) {
    showToast("Không có bản dịch nào cần kiểm tra.");
    return;
  }
  const reviewed = document.getElementById("reviewed-output").value;
  const path = learn ? "learn" : "save";
  await api(`/api/review/${encodeURIComponent(state.selectedReviewItem)}/${path}`, {
    method: "POST",
    body: JSON.stringify({ reviewed_text: reviewed }),
  });
  showToast(learn ? "Đã tạo candidate học có audit theo scope dự án." : "Đã lưu bản dịch, không học.");
}

async function markReviewed() {
  if (!state.selectedReviewItem) {
    showToast("Không có bản dịch nào cần kiểm tra.");
    return;
  }
  await api(`/api/review/${encodeURIComponent(state.selectedReviewItem)}/mark-reviewed`, { method: "POST" });
  showToast("Đã đánh dấu đã kiểm tra.");
}

function skipReviewItem() {
  if (!state.reviewItems.length || !state.selectedReviewItem) {
    showToast("Không có mục tiếp theo để bỏ qua.");
    return;
  }
  const index = state.reviewItems.findIndex((item) => item.id === state.selectedReviewItem);
  const next = state.reviewItems[(index + 1) % state.reviewItems.length];
  state.selectedReviewItem = next.id;
  openReviewItem(next.id);
  showToast("Đã chuyển sang mục tiếp theo.");
}

async function exportProject(format) {
  const project = selectedProject();
  const data = await api(`/api/projects/${encodeURIComponent(project)}/export`, { method: "POST", body: JSON.stringify({ format }) });
  if (data.status === "unsupported") {
    showToast(`${data.format}: ${data.label}`);
    return;
  }
  state.lastOutputPath = data.txt_output_path || data.artifact_path || state.lastOutputPath;
  const count = Number.isInteger(data.file_count) ? ` (${data.file_count} file)` : "";
  showToast(`${data.message || "Đã xuất file."}${count}`);
}

async function copyOutputPath() {
  await copyText(state.lastOutputPath);
  showToast(`Đường dẫn kết quả: ${state.lastOutputPath}`);
}

async function copyText(value) {
  if (navigator.clipboard) {
    await navigator.clipboard.writeText(value);
    return true;
  }
  return false;
}

function selectedProject(required = true) {
  const select = document.getElementById("project-select");
  state.selectedProject = select.value || state.selectedProject;
  if (!state.selectedProject && required) {
    throw new Error("Bạn chưa chọn dự án truyện.");
  }
  return state.selectedProject;
}

function advanceWizard() {
  state.wizardStep = Math.min(state.wizardStep + 1, 6);
  renderWizard();
  showToast("Đã chuyển sang bước tiếp theo.");
}

function toggleSource() {
  const source = document.getElementById("source-text");
  source.parentElement.hidden = !source.parentElement.hidden;
}

function openTechnical(customText) {
  const text = customText || JSON.stringify({
    safe_profile: true,
    use_approved_dictionary: true,
    use_approved_memory: true,
    emit_prompt_artifacts: true,
    resumable: true,
    use_approved_rules: false,
    inject_raw_nlp_cache: false,
    note: "Approved rules are verifier-only in the current production profile.",
  }, null, 2);
  document.getElementById("technical-content").textContent = text;
  document.getElementById("technical-dialog").showModal();
}

function showToast(message) {
  const toast = document.getElementById("toast");
  toast.textContent = message;
  toast.classList.add("visible");
  window.setTimeout(() => toast.classList.remove("visible"), 3200);
}

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"]/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[char]));
}
