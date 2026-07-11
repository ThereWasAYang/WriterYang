    const $ = (id) => document.getElementById(id);
    const chapterCompareFileTypes = ["plan", "draft", "polished", "audit"];
    const inspirationPreviewPath = "memory/inspiration.md";
    const styleGuidePath = "memory/style_guide.md";
    const outlinePreviewEndpoints = new Set([
      "/api/session/start",
      "/api/session/revise-outline",
      "/api/session/approve-outline",
    ]);
    const chapterComparePreviewEndpoints = new Set([
      "/api/session/run",
      "/api/session/revise-content",
      "/api/session/revise-audit",
      "/api/session/retry-rewrite",
    ]);
    const sessionIdRequiredEndpoints = new Set([
      "/api/session/revise-outline",
      "/api/session/approve-outline",
      "/api/session/run",
      "/api/session/revise-content",
      "/api/session/revise-audit",
      "/api/session/retry-rewrite",
      "/api/session/undo-rewrite",
      "/api/session/accept",
      "/api/session/archive",
    ]);
    const rewriteEventRequiredEndpoints = new Set([
      "/api/session/revise-audit",
      "/api/session/retry-rewrite",
      "/api/session/undo-rewrite",
    ]);
    let defaultProjectParentPath = "~/WriterYang";
    const providerProfileNames = ["scribe", "architect", "loremaster", "clerk"];
    const providerTaskNames = [
      "writer", "polish", "revision", "plot", "audit", "inspiration", "style_guide",
      "canon", "state_update", "chapter_memory", "intent_router", "memory_repair", "setup",
    ];
    const providerFormFieldIds = [
      "providerProviderField", "providerModelField", "providerBaseUrlEnvField",
      "providerApiKeyEnvField", "providerMaxTokensField", "providerMaxContextTokensField",
      "providerTimeoutSecondsField", "providerMaxRetriesField",
    ];
    const jsonResponseFormatValues = ["auto", "json_object", "json_schema", "json_schema_strict"];
    const providerTaskFormFields = ["thinking", "reasoning", "temperature"];
    const embeddingFormIds = {
      setup: {
        provider: "setupEmbeddingProvider",
        model: "setupEmbeddingModel",
        dimensions: "setupEmbeddingDimensions",
        batchSize: "setupEmbeddingBatchSize",
      },
      config: {
        provider: "configEmbeddingProvider",
        model: "configEmbeddingModel",
        dimensions: "configEmbeddingDimensions",
        batchSize: "configEmbeddingBatchSize",
      },
    };
    let editorLoadedContent = "";
    let editorSourceFile = "";
    let providerConfigCache = null;
    let providerEffectiveProfilesCache = {};
    let providerEffectiveTasksCache = {};
    let providerConfigBackendMismatch = "";
    let backendMismatchAlertShown = false;
    let embeddingConfigEditing = false;
    let sessionProgressPoller = null;
    let sessionProgressTimer = null;
    let currentBusyStartedAt = null;
    let currentBusyLabel = "";
    let recentOperations = [];
    let latestCanonProposalSnapshotPath = "";
    let latestAuditAnnotations = null;
    let latestSelectedAuditIssue = null;
    let latestSettingChangeClarificationId = "";
    let latestHeaderMessageDetails = "";
    let runtimeSummary = {};
    let styleGuideDefaultTemplate = "";
    let styleGuideLoadedContent = "";
    let styleGuideLoaded = false;

    function syncWorkbenchStickyOffset() {
      const header = document.querySelector(".app-header");
      if (!header) return;
      const gap = 14;
      const headerOffset = Math.ceil(header.getBoundingClientRect().height + gap);
      const commandBar = $("workbenchCommandBar");
      const commandBarVisible = Boolean(commandBar && commandBar.offsetParent !== null);
      const commandBarOffset = commandBarVisible ? Math.ceil(commandBar.getBoundingClientRect().height + gap) : 0;
      document.documentElement.style.setProperty("--app-header-sticky-offset", `${headerOffset}px`);
      document.documentElement.style.setProperty("--workbench-secondary-sticky-offset", `${headerOffset + commandBarOffset}px`);
    }

    function syncProjectPrepDetails(data = {}) {
      const details = $("projectPrepDetails");
      if (!details) return;
      const session = data.session || {};
      const status = data.status || {};
      if (session.session_id) {
        details.open = false;
        return;
      }
      if (!status.title) return;
      details.open = !status.inspiration_exists
        || ((status.character_count || 0) === 0 && (status.location_count || 0) === 0);
    }

    function projectPath() {
      return $("projectPath").value.trim() || ".";
    }

    function projectParentPath() {
      return $("projectParentPath").value.trim() || defaultProjectParentPath;
    }

    function projectTitleValue() {
      return $("projectTitle").value.trim() || "未命名小说";
    }

    function safeProjectDirectoryName(title) {
      const fallback = "未命名小说";
      const raw = String(title || "").trim() || fallback;
      const normalized = raw
        .replace(/[\\/:*?"<>|\u0000-\u001f]+/g, "_")
        .replace(/\s+/g, " ")
        .trim();
      if (!normalized || /^\.+$/.test(normalized)) return fallback;
      return normalized;
    }

    function joinProjectPath(parent, child) {
      const base = String(parent || defaultProjectParentPath).trim() || defaultProjectParentPath;
      const directory = safeProjectDirectoryName(child);
      if (base === "/") return `/${directory}`;
      if (/^[A-Za-z]:$/.test(base)) return `${base}\\${directory}`;
      if (/^[A-Za-z]:[\\/]$/.test(base)) return `${base}${directory}`;
      const separator = base.includes("\\") && !base.includes("/") ? "\\" : "/";
      return `${base.replace(/[\\/]+$/, "")}${separator}${directory}`;
    }

    function newProjectRootPath() {
      return joinProjectPath(projectParentPath(), projectTitleValue());
    }

    function updateProjectInitPathPreview() {
      const preview = $("projectInitPathPreview");
      if (!preview) return;
      preview.textContent = `最终项目目录：${newProjectRootPath()}`;
    }

    function setProjectPathValue(path) {
      $("projectPath").value = path || "";
      localStorage.setItem("writeryang.projectPath", $("projectPath").value);
      resetStyleGuideState();
    }

    function recentSessionStorageKey(path = projectPath()) {
      return `writeryang.lastSession.${String(path || ".")}`;
    }

    function rememberSessionId(sessionId) {
      const value = String(sessionId || "").trim();
      if (!value) return;
      localStorage.setItem(recentSessionStorageKey(), value);
    }

    function recentSessionId() {
      return localStorage.getItem(recentSessionStorageKey()) || "";
    }

    function currentWebEndpointPayload() {
      const protocolDefaultPort = window.location.protocol === "https:" ? 443 : 80;
      const currentPort = Number(window.location.port || protocolDefaultPort);
      return {
        current_host: window.location.hostname || "127.0.0.1",
        current_port: currentPort,
      };
    }

    function chapterNumber() {
      return Number($("chapterNumber").value || "1");
    }

    function compareChapterNumber() {
      const selected = $("compareChapterSelect")?.value || "";
      return Number(selected || $("chapterNumber").value || "1");
    }

    function syncCompareChapterSelect(session = {}) {
      const select = $("compareChapterSelect");
      if (!select) return;
      const range = (session.chapter_range || [])
        .map((value) => Number(value))
        .filter((value) => Number.isInteger(value) && value > 0);
      const chapters = range.length ? range : [chapterNumber()];
      const current = chapterNumber();
      const selected = chapters.includes(current) ? current : chapters[0];
      select.innerHTML = chapters.map((chapter) => (
        `<option value="${chapter}">第 ${chapter} 章</option>`
      )).join("");
      select.value = String(selected);
      $("chapterNumber").value = String(selected);
    }

    function truncateText(value, maxLength = 120) {
      const text = String(value ?? "").trim();
      if (text.length <= maxLength) return text;
      return `${text.slice(0, Math.max(0, maxLength - 1)).trimEnd()}...`;
    }

    function summarizedMessage(text, detailSuffix = "点击查看详情") {
      const value = String(text ?? "");
      const lines = value.split(/\r?\n/);
      const hasMultipleLines = lines.length > 1;
      const isLong = hasMultipleLines || value.length > 180;
      if (!isLong) return { text: value, isLong: false };
      const firstLine = lines.find((line) => line.trim()) || value;
      const suffix = hasMultipleLines ? `${lines.length} 行` : `${value.length} 字`;
      return {
        text: `${truncateText(firstLine, 120)}（${suffix}，${detailSuffix}）`,
        isLong: true,
      };
    }

    function syncMessageDetailsButton() {
      const detailsButton = $("messageDetails");
      if (!detailsButton) return;
      const hasDetails = Boolean(latestHeaderMessageDetails);
      detailsButton.classList.toggle("hidden", !hasDetails);
      detailsButton.disabled = !hasDetails;
    }

    function setMessage(text, isError = false, detailsText = "") {
      const display = summarizedMessage(text);
      const explicitDetails = String(detailsText || "");
      const hasDetails = Boolean(explicitDetails) || display.isLong;
      const messageRow = document.querySelector(".header-message-row");
      if (messageRow) messageRow.classList.toggle("hidden", !display.text && !hasDetails);
      latestHeaderMessageDetails = hasDetails ? (explicitDetails || String(text ?? "")) : "";
      $("message").textContent = hasDetails && !display.isLong ? `${display.text}（点击查看详情）` : display.text;
      $("message").className = isError ? "message error" : "message";
      syncMessageDetailsButton();
    }

    function openLatestMessageDetails() {
      if (!latestHeaderMessageDetails) return;
      showMainPage("logsPage");
      showTab("singleFileView");
      $("fileViewer").textContent = latestHeaderMessageDetails;
    }

    function setBusyBanner(text) {
      const banner = $("busyBanner");
      if (!banner) return;
      if (!text) {
        banner.textContent = "";
        banner.classList.add("hidden");
        return;
      }
      banner.textContent = text;
      banner.classList.remove("hidden");
    }

    function hasResponseField(data, field) {
      return Boolean(data) && Object.prototype.hasOwnProperty.call(data, field);
    }

    function backendVersionMismatchMessage(endpoint, fields) {
      return `Web UI 后台版本不匹配：${endpoint} 响应缺少 ${fields.join(", ")}。请停止并重启 Web UI 后台进程，然后刷新页面。`;
    }

    function warnBackendVersionMismatch(message) {
      if (!message) return;
      setMessage(message, true);
      if (!backendMismatchAlertShown) {
        backendMismatchAlertShown = true;
        window.alert(message);
      }
    }

    function showMainPage(pageId) {
      document.querySelectorAll(".app-page").forEach((page) => page.classList.remove("active"));
      document.querySelectorAll(".nav-button").forEach((button) => button.classList.remove("active"));
      const page = $(pageId);
      const button = document.querySelector(`[data-page="${pageId}"]`);
      if (!page || !button) return;
      page.classList.add("active");
      button.classList.add("active");
      syncWorkbenchStickyOffset();
      window.requestAnimationFrame(syncWorkbenchStickyOffset);
      if (pageId === "workbenchPage" && ["chapterCompare", "chapterEditor", "auditLocate"].every((id) => $(id).classList.contains("hidden"))) {
        showTab("chapterCompare");
      }
      if (pageId === "workbenchPage") restoreRecentSessionIfEmpty({ silent: true });
      if (pageId === "logsPage" && ["projectSearch", "projectFiles", "runLogs", "usageStats", "singleFileView"].every((id) => $(id).classList.contains("hidden"))) {
        showTab("projectSearch");
      }
      if (pageId === "memoryPage") loadStateTimeline();
      if (pageId === "stylePage" && !styleGuideLoaded) loadStyleGuide({ silent: true });
      if (pageId === "configPage") loadProviderConfig();
      if (pageId === "logsPage" && !$("usageStats").classList.contains("hidden")) loadUsage();
      if (pageId === "logsPage" && !$("runLogs").classList.contains("hidden")) loadRuns();
    }

    function setSetupStatus(text, isError = false) {
      $("setupGuideStatus").textContent = text;
      $("setupGuideStatus").className = isError ? "message error" : "message";
    }

    function setEmbeddingConfigStatus(text, isError = false) {
      $("embeddingConfigStatus").textContent = text;
      $("embeddingConfigStatus").className = isError ? "message error" : "message";
    }

    function setStyleGuideStatus(text, isError = false) {
      $("styleGuideStatus").textContent = text;
      $("styleGuideStatus").className = isError ? "message error" : "message";
    }

    function resetStyleGuideState() {
      styleGuideLoaded = false;
      styleGuideLoadedContent = "";
      styleGuideDefaultTemplate = "";
      const editor = $("styleGuideEditor");
      if (editor) editor.value = "";
      const dirty = $("styleGuideDirty");
      if (dirty) dirty.textContent = "未加载";
      const meta = $("styleGuideMeta");
      if (meta) meta.textContent = `当前文件：${styleGuidePath}`;
      const status = $("styleGuideStatus");
      if (status) setStyleGuideStatus("文风设置：未加载");
    }

    function showSetupGuide(show = true) {
      $("setupGuidePanel").classList.toggle("hidden", !show);
    }

    function setProjectInitVisible(show = true) {
      $("projectInitPanel").classList.toggle("hidden", !show);
      $("toggleProjectInit").textContent = show ? "隐藏新建项目选项" : "显示新建项目选项";
    }

    function toggleProjectInit() {
      setProjectInitVisible($("projectInitPanel").classList.contains("hidden"));
    }

    async function openProject() {
      await refreshAll({ hideProjectInitOnSuccess: true, hideSetupGuideOnSuccess: true });
      await restoreRecentSessionIfEmpty({ silent: true });
    }

    async function loadRuntime() {
      try {
        const data = await apiGet("/api/runtime", {});
        runtimeSummary = data.runtime || {};
        const runtimeDefaultProjectParent = String(runtimeSummary.default_project_parent || "").trim();
        if (runtimeDefaultProjectParent) {
          defaultProjectParentPath = runtimeDefaultProjectParent;
          if ($("projectParentPath").dataset.usesRuntimeDefault === "1") {
            $("projectParentPath").value = defaultProjectParentPath;
            updateProjectInitPathPreview();
          }
        }
        if (!$("setupWebPort").value && runtimeSummary.launcher_config_port) {
          $("setupWebPort").value = runtimeSummary.launcher_config_port;
        }
        renderRuntime(runtimeSummary);
      } catch (error) {
        $("runtimePanel").className = "metric runtime-panel home-runtime-panel status-warn";
        $("runtimePanel").innerHTML = `<b>运行环境</b><div class="status-bad">${escapeHtml(error.message)}</div>`;
        syncWorkbenchStickyOffset();
      }
    }

    function renderRuntime(runtime) {
      const ok = Boolean(runtime.managed_install);
      $("runtimePanel").className = `metric runtime-panel home-runtime-panel ${ok ? "status-ok" : "status-warn"}`;
      $("runtimePanel").innerHTML = `
        <b>运行环境：${escapeHtml(runtime.environment || "未知")}</b>
        <div>版本：${escapeHtml(runtime.version || "")}</div>
        <div>Python：${escapeHtml(runtime.python || "")}</div>
        <div>当前 Web UI：${escapeHtml(runtime.current_web_host || window.location.hostname || "127.0.0.1")}:${escapeHtml(runtime.current_web_port || window.location.port || "")}</div>
        <div>下次启动器端口：${escapeHtml(runtime.launcher_config_host || "127.0.0.1")}:${escapeHtml(runtime.launcher_config_port || "未设置")}</div>
        ${runtime.launcher_port_fallback ? '<div class="status-bad">本次启动时配置端口被占用，已临时改用当前端口。建议重新保存一个可用端口。</div>' : ""}
        ${runtime.warning ? `<div class="status-bad">${escapeHtml(runtime.warning)}</div>` : "<div>已使用 WriterYang 专用环境。</div>"}
      `;
      syncWorkbenchStickyOffset();
    }

    function apiFailureError(errorPayload, fallbackMessage) {
      const error = new Error(errorPayload?.message || fallbackMessage || "API request failed");
      error.code = errorPayload?.code || "";
      error.requestId = errorPayload?.request_id || "";
      error.details = errorPayload?.details || {};
      error.detailText = apiErrorDetailText(error);
      return error;
    }

    function apiErrorDetailText(error) {
      const meta = {};
      if (error.code) meta.code = error.code;
      if (error.requestId) meta.request_id = error.requestId;
      if (error.details && Object.keys(error.details).length) meta.details = error.details;
      if (!Object.keys(meta).length) return error.message || "";
      return `${error.message || ""}\n\n${JSON.stringify(meta, null, 2)}`;
    }

    async function apiGet(path, params) {
      const query = new URLSearchParams(params);
      const response = await fetch(`${path}?${query}`);
      const payload = await response.json();
      if (!response.ok || payload.ok === false) {
        throw apiFailureError(payload.error, "API request failed");
      }
      return payload.data || {};
    }

    async function apiPost(path, payload) {
      const response = await fetch(path, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const data = await response.json();
      if (!response.ok || data.ok === false) {
        throw apiFailureError(data.error, "API request failed");
      }
      return data.data || {};
    }

    async function refreshAll(options = {}) {
      try {
        const path = projectPath();
        const [status, canon, chapters, files, management, searchStatus, canonApplied] = await Promise.all([
          apiGet("/api/project/status", { path }),
          apiGet("/api/canon", { path }),
          apiGet("/api/chapters", { path }),
          apiGet("/api/file-tree", { path }),
          apiGet("/api/management-events", { path, limit: 10 }),
          apiGet("/api/search-status", { path }),
          apiGet("/api/canon/applied-proposals", { path, limit: 5 }),
        ]);
        renderStatus(status.status);
        $("canonPanel").textContent = canon.summary || "无";
        renderChapters(chapters.chapters || []);
        renderFileTree(files.files || []);
        renderManagementEvents(management.events || []);
        renderSearchStatus(searchStatus.search || {});
        renderCanonAppliedProposals(canonApplied.applied_proposals || []);
        renderNextStep({ status: status.status, chapters: chapters.chapters || [] });
        if (options.hideProjectInitOnSuccess) setProjectInitVisible(false);
        if (options.hideSetupGuideOnSuccess) showSetupGuide(false);
        if (!options.silent) setMessage("项目已刷新");
      } catch (error) {
        if (!options.silent) setMessage(error.message, true);
        if (options.silent) throw error;
      }
    }

    function renderStatus(status) {
      $("currentProjectSummary").textContent = status.title
        ? `当前项目：${status.title} · ${projectPath()} · 最新章节 ${status.latest_chapter ?? 0}`
        : `当前项目：${projectPath()}`;
      const rows = [
        ["标题", status.title],
        ["最新章节", status.latest_chapter],
        ["灵感", status.inspiration_exists ? "存在" : "缺失"],
        ["角色", status.character_count],
        ["地点", status.location_count],
        ["物品", status.item_count],
        ["时间线事件", status.timeline_event_count],
        ["最近 run", status.latest_run_summary || "无"],
      ];
      $("statusPanel").innerHTML = rows.map(([label, value]) =>
        `<div class="metric"><span>${escapeHtml(label)}</span><b>${escapeHtml(value ?? "")}</b></div>`
      ).join("");
    }

    function renderManagementEvents(events) {
      const panel = $("managementEventsPanel");
      if (!panel) return;
      if (!events.length) {
        panel.innerHTML = "后台管理动态：暂无";
        return;
      }
      panel.innerHTML = `
        <b>后台管理动态</b>
        ${events.map((event) => `
          <div style="margin-top: 6px;">
            [${escapeHtml(event.status || "")}/${escapeHtml(event.event_type || "")}]
            ${escapeHtml(event.message || "")}
            ${(event.target_files || []).length ? `<div>涉及文件：${escapeHtml((event.target_files || []).join(", "))}</div>` : ""}
          </div>
        `).join("")}
      `;
    }

    function renderCanonAppliedProposals(records) {
      const panel = $("canonAppliedProposalPanel");
      const button = $("viewLatestCanonProposal");
      if (!panel || !button) return;
      if (!records.length) {
        latestCanonProposalSnapshotPath = "";
        panel.innerHTML = "已应用 Canon proposal：暂无";
        button.classList.add("hidden");
        return;
      }
      const latest = records[0];
      const counts = latest.proposal_counts || {};
      latestCanonProposalSnapshotPath = latest.proposal_snapshot_path || "";
      button.classList.toggle("hidden", !latestCanonProposalSnapshotPath);
      panel.innerHTML = `
        <b>已应用 Canon proposal</b>
        <div>最近应用：${escapeHtml(formatDateTime(latest.applied_at))}</div>
        <div>原始 proposal：${escapeHtml(latest.original_proposal_path || "")}</div>
        <div>内容快照：${escapeHtml(latest.proposal_snapshot_path || "")}</div>
        <div>变更数量：
          characters ${escapeHtml(counts.characters ?? 0)} /
          locations ${escapeHtml(counts.locations ?? 0)} /
          items ${escapeHtml(counts.items ?? 0)} /
          world_rules ${escapeHtml(counts.world_rules ?? 0)} /
          hidden_truths ${escapeHtml(counts.hidden_truths ?? 0)} /
          foreshadowing_threads ${escapeHtml(counts.foreshadowing_threads ?? 0)}
        </div>
        <div>validation warnings：${escapeHtml(latest.validation_warning_count ?? 0)}</div>
      `;
    }

    function renderSearchStatus(search) {
      const ftsClass = search.fts_status === "indexed" ? "status-ok" : (search.fts_status === "stale" ? "status-warn" : "status-bad");
      const embeddingOk = search.embedding_status === "indexed";
      const embeddingWarn = search.embedding_status === "stale" || search.embedding_status === "missing";
      const embeddingClass = embeddingOk ? "status-ok" : (embeddingWarn ? "status-warn" : "status-bad");
      const envText = (search.embedding_env_missing || []).length
        ? `<div>缺少环境变量：${escapeHtml((search.embedding_env_missing || []).join(", "))}</div>`
        : "";
      const embeddingHelp = embeddingOk
        ? "embedding 语义检索可用。"
        : "当前无法使用基于 embedding 的语义检索；普通关键词搜索仍可用。";
      $("searchStatusPanel").innerHTML = `
        <b>检索状态</b>
        <div>FTS：<span class="${ftsClass}">${escapeHtml(search.fts_status || "unknown")}</span></div>
        <div>Embedding：<span class="${embeddingClass}">${escapeHtml(search.embedding_status || "unknown")}</span></div>
        <div>文档数：${escapeHtml(search.document_count ?? 0)}</div>
        <div>Provider：${escapeHtml(search.embedding_provider || "未配置")} / ${escapeHtml(search.embedding_model || "未配置")}</div>
        ${envText}
        <div class="${embeddingOk ? "status-ok" : "status-bad"}">${escapeHtml(embeddingHelp)}</div>
      `;
    }

    function renderChapters(chapters) {
      if (!chapters.length) {
        $("chapterList").textContent = "暂无章节";
        return;
      }
      const body = chapters.map((chapter) => {
        const needsMemory = !chapter.has_chapter_memory || chapter.chapter_memory_stale;
        const memoryLabel = chapter.has_chapter_memory ? (chapter.chapter_memory_stale ? "memory(stale)" : "memory") : "";
        const memoryAction = needsMemory
          ? `<button data-chapter="${chapter.chapter_number}" class="chapter-memory-generate">${chapter.has_chapter_memory ? "刷新记忆" : "生成记忆"}</button>`
          : "";
        return `
          <tr>
            <td>${chapter.chapter_number}</td>
            <td>${escapeHtml(chapter.title || "")}</td>
            <td>${escapeHtml(chapter.status || "")}</td>
            <td>${chapter.has_plan ? "plan " : ""}${chapter.has_draft ? "draft " : ""}${chapter.has_polished ? "polished " : ""}${chapter.has_audit ? "audit " : ""}${memoryLabel}</td>
            <td>${escapeHtml(auditStatusLabel(chapter.audit_status))}</td>
            <td><button data-chapter="${chapter.chapter_number}" class="select-chapter">选择</button>${memoryAction}</td>
          </tr>
        `;
      }).join("");
      $("chapterList").innerHTML = `<table><thead><tr><th>#</th><th>标题</th><th>状态</th><th>文件</th><th>审核</th><th></th></tr></thead><tbody>${body}</tbody></table>`;
      document.querySelectorAll(".select-chapter").forEach((button) => {
        button.addEventListener("click", () => {
          $("chapterNumber").value = button.dataset.chapter;
          const compareSelect = $("compareChapterSelect");
          if (compareSelect && [...compareSelect.options].some((option) => option.value === button.dataset.chapter)) {
            compareSelect.value = button.dataset.chapter;
          }
          showMainPage("workbenchPage");
          showTab("chapterCompare");
          loadCompare();
        });
      });
      document.querySelectorAll(".chapter-memory-generate").forEach((button) => {
        button.addEventListener("click", () => generateChapterMemory(Number(button.dataset.chapter || "1")));
      });
    }

    function renderFileTree(files) {
      if (!files.length) {
        $("fileTree").textContent = "暂无文件";
        return;
      }
      $("fileTree").innerHTML = files.map((file) => {
        const indent = "&nbsp;".repeat(Math.max(file.path.split("/").length - 1, 0) * 2);
        const icon = file.type === "directory" ? "▸" : "·";
        return `<button class="file-row" data-path="${escapeAttr(file.path)}">${indent}${icon} ${escapeHtml(file.path)}</button>`;
      }).join("");
      document.querySelectorAll(".file-row").forEach((button) => {
        button.addEventListener("click", () => readWorkspaceFile(button.dataset.path));
      });
    }

    async function readWorkspaceFile(relPath) {
      try {
        const data = await apiGet("/api/read-file", { path: projectPath(), file: relPath });
        showMainPage("logsPage");
        showTab("projectFiles");
        $("projectFileCurrent").textContent = `当前文件：${data.path || relPath}`;
        $("projectFileViewer").textContent = data.content || "";
        setMessage(`已读取 ${data.path}，内容显示在“运行日志 / 项目文件”的“项目文件”页`);
      } catch (error) {
        setMessage(error.message, true);
      }
    }

    function updateStyleGuideDirtyState() {
      if (!styleGuideLoaded) {
        $("styleGuideDirty").textContent = "未加载";
        return;
      }
      $("styleGuideDirty").textContent =
        $("styleGuideEditor").value === styleGuideLoadedContent ? "无未保存修改" : "有未保存修改";
    }

    async function loadStyleGuide(options = {}) {
      try {
        const data = await apiGet("/api/style-guide", { path: projectPath() });
        styleGuideDefaultTemplate = data.default_template || "";
        styleGuideLoadedContent = data.content || "";
        styleGuideLoaded = true;
        $("styleGuideEditor").value = styleGuideLoadedContent;
        $("styleGuideMeta").textContent = `当前文件：${data.path || styleGuidePath}${data.exists ? "" : "（使用默认模板，保存后创建）"}`;
        updateStyleGuideDirtyState();
        setStyleGuideStatus(data.exists ? "文风设置：已加载" : "文风设置：文件缺失，已载入默认模板");
        if (!options.silent) setMessage("文风设置已加载");
      } catch (error) {
        setStyleGuideStatus(error.message, true);
        if (!options.silent) setMessage(error.message, true);
      }
    }

    async function saveStyleGuide() {
      try {
        const data = await apiPost("/api/style-guide", {
          path: projectPath(),
          content: $("styleGuideEditor").value,
        });
        styleGuideLoadedContent = data.content || $("styleGuideEditor").value;
        styleGuideLoaded = true;
        $("styleGuideEditor").value = styleGuideLoadedContent;
        $("styleGuideMeta").textContent = `当前文件：${data.path || styleGuidePath}`;
        updateStyleGuideDirtyState();
        const backupText = data.backup_path ? `备份：${data.backup_path}` : "首次创建，无旧文件备份";
        setStyleGuideStatus(`已保存 ${data.path || styleGuidePath}；${backupText}；后续生成会使用新文风设置。`);
        await refreshAll({ silent: true });
        setMessage(`已保存文风设置；${backupText}`);
      } catch (error) {
        setStyleGuideStatus(error.message, true);
        setMessage(error.message, true);
      }
    }

    async function restoreStyleGuideTemplate() {
      if (!styleGuideDefaultTemplate) {
        await loadStyleGuide({ silent: true });
      }
      if (!styleGuideDefaultTemplate) {
        setStyleGuideStatus("请先打开有效项目后再恢复默认模板。", true);
        return;
      }
      $("styleGuideEditor").value = styleGuideDefaultTemplate || "";
      styleGuideLoaded = true;
      updateStyleGuideDirtyState();
      setStyleGuideStatus("已恢复默认模板（未保存）");
      setMessage("已恢复默认文风模板，保存后才会写入文件。");
    }

    async function generateStyleGuideDraft() {
      const instruction = $("styleGuideGenerateInstruction").value.trim();
      if (!instruction) {
        setStyleGuideStatus("请先输入希望 AI 总结的文风方向。", true);
        setMessage("请先输入希望 AI 总结的文风方向。", true);
        return;
      }
      const editor = $("styleGuideEditor");
      const hasUnsavedEditorText = styleGuideLoaded
        ? editor.value !== styleGuideLoadedContent
        : Boolean(editor.value.trim());
      if (hasUnsavedEditorText && !window.confirm("生成文风草稿会替换当前编辑器内容，但不会保存到文件。确认继续吗？")) {
        return;
      }
      return withBusy("生成文风草稿", async () => {
        const data = await apiPost("/api/style-guide/generate", {
          path: projectPath(),
          instruction,
          provider: $("styleGuideGenerateProvider").value,
          include_project_context: true,
          include_existing_style: true,
        });
        if (!styleGuideLoaded) {
          styleGuideLoadedContent = "";
        }
        styleGuideLoaded = true;
        editor.value = data.content || "";
        $("styleGuideMeta").textContent = `当前文件：${data.path || styleGuidePath}（生成草稿未保存）`;
        updateStyleGuideDirtyState();
        const warnings = Array.isArray(data.warnings) && data.warnings.length
          ? `；警告：${data.warnings.join("；")}`
          : "";
        setStyleGuideStatus(`已生成文风草稿，保存后才会写入 ${data.path || styleGuidePath}${warnings}`);
        setMessage(`已生成文风草稿，保存后才会写入 ${data.path || styleGuidePath}${warnings}`);
      });
    }
