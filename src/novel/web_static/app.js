    const $ = (id) => document.getElementById(id);
    const chapterFileTypes = ["plan", "draft", "polished", "audit", "chapter_memory"];
    const inspirationPreviewPath = "memory/inspiration.md";
    let defaultProjectParentPath = "~/WriterYang";
    const providerAgentNames = [
      "orchestrator", "inspiration", "canon", "plot", "writer",
      "polish", "audit", "state_update", "chapter_memory", "revision",
    ];
    const providerFormFieldIds = [
      "providerProviderField", "providerModelField", "providerBaseUrlEnvField",
      "providerApiKeyEnvField", "providerThinkingTypeField", "providerReasoningField",
      "providerTemperatureField", "providerMaxTokensField", "providerMaxContextTokensField",
      "providerTimeoutSecondsField", "providerMaxRetriesField",
    ];
    const jsonResponseFormatValues = ["auto", "json_object", "json_schema", "json_schema_strict"];
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
    let providerEffectiveCache = {};
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

    function syncWorkbenchStickyOffset() {
      const header = document.querySelector(".app-header");
      if (!header) return;
      const offset = Math.ceil(header.getBoundingClientRect().height + 14);
      document.documentElement.style.setProperty("--app-header-sticky-offset", `${offset}px`);
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
      if (pageId === "workbenchPage" && ["chapterCompare", "chapterEditor", "auditLocate"].every((id) => $(id).classList.contains("hidden"))) {
        showTab("chapterCompare");
      }
      if (pageId === "logsPage" && ["projectSearch", "projectFiles", "runLogs", "usageStats", "singleFileView"].every((id) => $(id).classList.contains("hidden"))) {
        showTab("projectSearch");
      }
      if (pageId === "memoryPage") loadStateTimeline();
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
        $("runtimePanel").innerHTML = `<b>运行环境</b><div class="status-bad">${escapeHtml(error.message)}</div>`;
      }
    }

    function renderRuntime(runtime) {
      const ok = Boolean(runtime.managed_install);
      $("runtimePanel").className = `metric runtime-panel ${ok ? "status-ok" : "status-warn"}`;
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
            ${(event.target_files || []).length ? `<div>files: ${escapeHtml((event.target_files || []).join(", "))}</div>` : ""}
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
            <td>${escapeHtml(chapter.audit_status || "")}</td>
            <td><button data-chapter="${chapter.chapter_number}" class="select-chapter">选择</button>${memoryAction}</td>
          </tr>
        `;
      }).join("");
      $("chapterList").innerHTML = `<table><thead><tr><th>#</th><th>标题</th><th>状态</th><th>文件</th><th>审核</th><th></th></tr></thead><tbody>${body}</tbody></table>`;
      document.querySelectorAll(".select-chapter").forEach((button) => {
        button.addEventListener("click", () => {
          $("chapterNumber").value = button.dataset.chapter;
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

    async function runAction(endpoint, label) {
      return withBusy(label, async () => {
        const payload = {
          path: projectPath(),
          chapter_number: chapterNumber(),
          instruction: $("instruction").value.trim(),
          provider: $("provider").value,
          force: $("forceWrites").checked,
          use_search_context: $("useSearchContext").checked,
          vector_context: $("vectorContextMode").value,
          use_vector_context: $("useVectorContext").checked,
          polish_mode: $("autoPolish").checked ? "auto" : "single_pass",
        };
        const data = await apiPost(endpoint, payload);
        $("fileViewer").textContent = JSON.stringify(data, null, 2);
        await refreshAll({ silent: true });
        setMessage(actionMessage(label, data));
      });
    }

    async function runSessionAction(endpoint, payload, label) {
      return withBusy(label, async () => {
        const shouldPollRewriteEvents = endpoint === "/api/session/run";
        let rewritePoller = null;
        let progressPolling = false;
        if (shouldPollRewriteEvents) rewritePoller = startRewriteEventPolling();
        if (shouldPollRewriteEvents) {
          progressPolling = true;
          renderSessionProgress({
            status: "running",
            current_stage: "queued",
            current_message: "Session 写作任务已提交，正在等待后台进入第一个阶段。",
            started_at: new Date().toISOString(),
          });
          startSessionProgressPolling();
        }
        try {
          const data = await apiPost(endpoint, payload);
          const session = data.session || {};
          if (session.session_id) $("sessionId").value = session.session_id;
          if (data.progress) renderSessionProgress(data.progress);
          renderSessionSummary(data);
          $("fileViewer").textContent = JSON.stringify(data, null, 2);
          await refreshAll({ silent: true });
          renderSessionSummary(data);
          renderNextStep(data);
          setMessage(actionMessage(label, data));
        } finally {
          if (rewritePoller) window.clearInterval(rewritePoller);
          if (progressPolling) {
            await loadSessionProgress({ quiet: true });
            stopSessionProgressPolling();
          }
        }
      });
    }

    function sessionPayload(options = {}) {
        const payload = {
          path: projectPath(),
          intent: $("instruction").value.trim(),
          instruction: $("instruction").value.trim(),
          chapters: $("sessionChapters").value.trim(),
          provider: $("provider").value,
          force: $("forceWrites").checked,
          vector_context: $("vectorContextMode").value,
          use_search_context: $("useSearchContext").checked,
          use_vector_context: $("useVectorContext").checked,
          polish_mode: $("autoPolish").checked ? "auto" : "single_pass",
        };
      if (options.includeSessionId !== false) payload.session_id = $("sessionId").value.trim();
      if (options.fromAudit) payload.from_audit = true;
      return payload;
    }

    function rewriteControlPayload(options = {}) {
      const payload = sessionPayload(options);
      payload.event_id = $("rewriteEventId").value.trim();
      return payload;
    }

    async function initProject() {
      return withBusy("初始化项目", async () => {
        const title = projectTitleValue();
        const targetPath = newProjectRootPath();
        const data = await apiPost("/api/init-project", {
          path: targetPath,
          title,
          genre: $("projectGenre").value.trim(),
        });
        setProjectPathValue(data.root || targetPath);
        $("fileViewer").textContent = JSON.stringify(data, null, 2);
        setProjectInitVisible(false);
        showSetupGuide(true);
        await recommendSetupPort();
        await refreshAll({ silent: true });
        setMessage(`项目已初始化：${projectPath()}。请完成项目初始引导，配置默认 API、可选 embedding 和下次启动器端口。`);
      });
    }

    async function recommendSetupPort() {
      try {
        const data = await apiGet("/api/setup/recommend-port", {
          start_port: $("setupWebPort").value || runtimeSummary.launcher_config_port || "8765",
          ...currentWebEndpointPayload(),
        });
        $("setupWebPort").value = data.selected_port || 8765;
        setSetupStatus(`推荐可保存端口：${data.selected_port}。下次通过 WriterYang_WebUI.command 启动时会使用该端口。`);
        return data;
      } catch (error) {
        setSetupStatus(error.message, true);
        throw error;
      }
    }

    async function setupDefaultProvider() {
      return withBusy("保存默认 API", async () => {
        const data = await apiPost("/api/setup/default-provider", {
          path: projectPath(),
          provider: "openai_compatible",
          base_url: $("setupBaseUrl").value.trim(),
          api_key: $("setupApiKey").value,
          model: $("setupModel").value.trim(),
          ping: true,
        });
        $("setupApiKey").value = "";
        setSetupStatus(data.message || "默认 API 已保存。");
        await loadProviderConfig();
        await refreshAll({ silent: true });
        setMessage("默认 API 连通性测试通过，已作为所有 Agent 的缺省配置。");
      });
    }

    function setupSkipProvider() {
      setSetupStatus("已暂时跳过默认 API 配置。真实创作前需要配置默认 API，否则 Agent 调用会失败。", true);
    }

    function embeddingProviderDefaults(provider, model) {
      const providerName = String(provider || "").toLowerCase();
      const modelName = String(model || "").toLowerCase();
      if (providerName === "dashscope") {
        if (modelName === "text-embedding-v3") {
          return { model: "text-embedding-v3", dimensions: 1024, batchSize: 10 };
        }
        return { model: "text-embedding-v4", dimensions: 2048, batchSize: 10 };
      }
      return { model: "", dimensions: "", batchSize: 16 };
    }

    function applyEmbeddingProviderDefaults(formName, force = false) {
      const ids = embeddingFormIds[formName];
      if (!ids) return;
      const provider = $(ids.provider).value || "dashscope";
      const defaults = embeddingProviderDefaults(provider, $(ids.model).value);
      if (force || !$(ids.model).value.trim()) $(ids.model).value = defaults.model;
      if (force || !$(ids.dimensions).value.trim()) $(ids.dimensions).value = defaults.dimensions;
      if (force || !$(ids.batchSize).value.trim()) $(ids.batchSize).value = defaults.batchSize;
    }

    function optionalPositiveIntFromField(id, label) {
      const value = $(id).value.trim();
      if (!value) return null;
      const parsed = Number(value);
      if (!Number.isInteger(parsed) || parsed <= 0) {
        throw new Error(`${label} 必须是正整数。`);
      }
      return parsed;
    }

    function embeddingFormPayload(formName) {
      const ids = embeddingFormIds[formName];
      applyEmbeddingProviderDefaults(formName, false);
      return {
        provider: $(ids.provider).value || "dashscope",
        model: $(ids.model).value.trim(),
        dimensions: optionalPositiveIntFromField(ids.dimensions, "Embedding dimensions"),
        batch_size: optionalPositiveIntFromField(ids.batchSize, "Embedding batch size"),
      };
    }

    async function setupEmbedding() {
      return withBusy("保存 embedding API", async () => {
        if (!$("setupEmbeddingEnabled").checked) {
          const skipped = await apiPost("/api/setup/embedding", { path: projectPath(), skip: true });
          setSetupStatus(skipped.message || "已跳过 embedding API 配置。");
          return;
        }
        const embedding = embeddingFormPayload("setup");
        const data = await apiPost("/api/setup/embedding", {
          path: projectPath(),
          provider: embedding.provider,
          provider_name: "configured",
          base_url: $("setupEmbeddingBaseUrl").value.trim(),
          api_key: $("setupEmbeddingApiKey").value,
          model: embedding.model,
          dimensions: embedding.dimensions,
          batch_size: embedding.batch_size,
          ping: true,
        });
        $("setupEmbeddingApiKey").value = "";
        setSetupStatus(`Embedding API 连通性测试通过：${data.provider} / ${data.model}，dimensions=${data.dimensions || "默认"}，batch_size=${data.batch_size || "默认"}`);
        await refreshAll({ silent: true });
      });
    }

    async function saveEmbeddingConfig() {
      return withBusy("保存 embedding API", async () => {
        const baseUrl = $("configEmbeddingBaseUrl").value.trim();
        const apiKey = $("configEmbeddingApiKey").value;
        const embedding = embeddingFormPayload("config");
        const model = embedding.model;
        if (!baseUrl || !apiKey || !model) {
          setEmbeddingConfigStatus("Embedding Base URL、API Key、provider、模型名和参数都必须填写。", true);
          throw new Error("Embedding Base URL、API Key、provider、模型名和参数都必须填写。");
        }
        const setup = await apiPost("/api/setup/embedding", {
          path: projectPath(),
          provider: embedding.provider,
          provider_name: "configured",
          base_url: baseUrl,
          api_key: apiKey,
          model,
          dimensions: embedding.dimensions,
          batch_size: embedding.batch_size,
          ping: true,
        });
        $("configEmbeddingApiKey").value = "";
        if (!hasResponseField(setup, "embedding_api")) {
          const message = backendVersionMismatchMessage("/api/setup/embedding", ["embedding_api"]);
          embeddingConfigEditing = true;
          renderEmbeddingConfigPanel({ status: "backend_mismatch", message });
          warnBackendVersionMismatch(message);
          return;
        }
        setEmbeddingConfigStatus(`Embedding API 已保存：${setup.provider} / ${setup.model}。正在刷新语义向量索引...`);
        try {
          const refresh = await apiPost("/api/index/refresh", {
            path: projectPath(),
            with_embeddings: true,
          });
          $("fileViewer").textContent = JSON.stringify({ embedding_setup: setup, index_refresh: refresh }, null, 2);
          renderSearchStatus(refresh.search || {});
          try { await refreshAll({ silent: true }); } catch {}
          $("configEmbeddingBaseUrl").value = "";
          $("configEmbeddingModel").value = "";
          embeddingConfigEditing = false;
          renderEmbeddingConfigPanel(setup.embedding_api || {});
          setEmbeddingConfigStatus("Embedding API 已保存，语义向量索引已刷新。");
          setMessage(actionMessage("保存 embedding API 并刷新语义向量索引", refresh));
        } catch (error) {
          $("fileViewer").textContent = JSON.stringify({ embedding_setup: setup, index_refresh_error: error.message }, null, 2);
          try { await refreshAll({ silent: true }); } catch {}
          embeddingConfigEditing = true;
          renderEmbeddingConfigPanel(setup.embedding_api || {});
          setEmbeddingConfigStatus(`Embedding API 已保存，但语义向量索引刷新失败：${error.message}`, true);
          setMessage(`Embedding API 已保存，但语义向量索引刷新失败：${error.message}`, true);
        }
      });
    }

    async function setupSavePort() {
      return withBusy("保存 Web UI 端口", async () => {
        const payload = {
          path: projectPath(),
          port: Number($("setupWebPort").value || "8765"),
          ...currentWebEndpointPayload(),
        };
        if (runtimeSummary.launcher_config_path) {
          payload.launcher_config_path = runtimeSummary.launcher_config_path;
        }
        const data = await apiPost("/api/setup/web-port", payload);
        $("setupWebPort").value = data.selected_port;
        setSetupStatus(data.message || `Web UI 启动器端口已保存：${data.url}`);
        await loadRuntime();
        await refreshAll({ silent: true });
      });
    }

    function inspirationPayload(force) {
      return {
        path: projectPath(),
        text: $("instruction").value.trim(),
        provider: $("provider").value,
        force,
        write_json: false,
        use_search_context: $("useSearchContext").checked,
        vector_context: $("vectorContextMode").value,
        use_vector_context: $("useVectorContext").checked,
      };
    }

    function renderInspirationPreview(data) {
      $("inspirationPreviewMeta").textContent = `当前文件：${data.path || inspirationPreviewPath}`;
      $("inspirationPreview").textContent = data.content || "memory/inspiration.md 为空。";
    }

    async function loadInspirationPreview(options = {}) {
      try {
        const data = await apiGet("/api/read-file", { path: projectPath(), file: inspirationPreviewPath });
        renderInspirationPreview(data);
        if (!options.silent) setMessage(`已读取灵感：${data.path || inspirationPreviewPath}`);
        return data;
      } catch (error) {
        $("inspirationPreviewMeta").textContent = `当前文件：${inspirationPreviewPath}`;
        $("inspirationPreview").textContent = `无法读取灵感文件：${error.message}`;
        if (!options.silent) setMessage(error.message, true);
        return null;
      }
    }

    async function generateInspiration(options = {}) {
      const label = options.label || "生成灵感";
      const force = Object.prototype.hasOwnProperty.call(options, "force") ? options.force : $("forceWrites").checked;
      return withBusy(label, async () => {
        const data = await apiPost("/api/inspire", inspirationPayload(force));
        $("fileViewer").textContent = JSON.stringify(data, null, 2);
        await refreshAll({ silent: true });
        const previewData = await loadInspirationPreview({ silent: true });
        setMessage(
          previewData ? actionMessage(label, data) : `${actionMessage(label, data)}；预览读取失败，请到“运行日志 / 项目文件”查看。`,
          !previewData
        );
      });
    }

    async function inspireProject() {
      return generateInspiration();
    }

    async function regenerateInspiration() {
      const confirmed = window.confirm("重新生成会覆盖 memory/inspiration.md。确认继续吗？");
      if (!confirmed) return;
      return generateInspiration({ label: "重新生成灵感", force: true });
    }

    async function canonSuggest() {
      return withBusy("Canon 建议", async () => {
        const data = await apiPost("/api/canon/suggest", {
          path: projectPath(),
          provider: $("provider").value,
          use_search_context: $("useSearchContext").checked,
          vector_context: $("vectorContextMode").value,
          use_vector_context: $("useVectorContext").checked,
        });
        if (data.relative_path) $("canonProposalPath").value = data.relative_path;
        $("fileViewer").textContent = JSON.stringify(data, null, 2);
        await refreshAll({ silent: true });
        setMessage(actionMessage("Canon 建议", data));
      });
    }

    async function canonApply() {
      return withBusy("应用 Canon proposal", async () => {
        const proposalFile = $("canonProposalPath").value.trim();
        if (!proposalFile) {
          throw new Error("请先点击“Canon 建议”，生成 Canon proposal 文件后再应用。");
        }
        const data = await apiPost("/api/canon/apply", {
          path: projectPath(),
          proposal_file: proposalFile,
        });
        $("fileViewer").textContent = JSON.stringify(data, null, 2);
        await refreshAll({ silent: true });
        setMessage(actionMessage("应用 Canon proposal", data));
      });
    }

    async function refreshIndex(withEmbeddings) {
      return withBusy(withEmbeddings ? "刷新语义向量索引" : "刷新关键词索引", async () => {
        const data = await apiPost("/api/index/refresh", {
          path: projectPath(),
          with_embeddings: withEmbeddings,
        });
        $("fileViewer").textContent = JSON.stringify(data, null, 2);
        renderSearchStatus(data.search || {});
        await refreshAll({ silent: true });
        setMessage(actionMessage(withEmbeddings ? "刷新语义向量索引" : "刷新关键词索引", data));
      });
    }

    async function memoryRepairSuggest() {
      return settingChangeSuggest("unknown", { preferMemoryInput: true });
    }

    async function memoryRepairApply() {
      return settingChangeApply({ syncSession: false });
    }

    async function memoryRepairAnswer() {
      return settingChangeAnswer({ preferMemoryInput: true });
    }

    async function settingChangeSuggest(sourceStage = "unknown", options = {}) {
      return withBusy("生成设定变更建议", async () => {
        latestSettingChangeClarificationId = "";
        clearSettingChangeClarification();
        const data = await apiPost("/api/settings/change/suggest", {
          path: projectPath(),
          request: settingChangeInstruction(options),
          provider: $("provider").value,
          session_id: $("sessionId").value.trim(),
          chapter_number: chapterNumber(),
          source_stage: sourceStage,
          audit_issue_ids: options.auditIssueIds || [],
        });
        handleSettingChangeSuggestion(data);
        await refreshAll({ silent: true });
        setMessage(settingChangeSuggestionMessage(data));
      });
    }

    async function settingChangeAnswer(options = {}) {
      return withBusy("继续生成设定变更建议", async () => {
        const answer = settingChangeClarificationAnswer(options);
        if (!latestSettingChangeClarificationId) {
          throw new Error("当前没有等待补充的设定变更问题。");
        }
        if (!answer) {
          throw new Error("请先填写补充说明。");
        }
        const data = await apiPost("/api/settings/change/answer", {
          path: projectPath(),
          clarification_id: latestSettingChangeClarificationId,
          answer,
          provider: $("provider").value,
        });
        handleSettingChangeSuggestion(data);
        await refreshAll({ silent: true });
        setMessage(settingChangeSuggestionMessage(data));
      });
    }

    function handleSettingChangeSuggestion(data) {
      if (data.status === "needs_clarification") {
        latestSettingChangeClarificationId = data.clarification_id || "";
        renderSettingChangeClarification(data);
      } else {
        latestSettingChangeClarificationId = "";
        clearSettingChangeClarification();
        syncSettingChangeProposalPath(data.proposal_relative_path || "");
        renderSettingChangeImpact(data.proposal || {});
      }
      $("fileViewer").textContent = JSON.stringify(data, null, 2);
      renderManagementEvents(data.management_events || []);
    }

    function settingChangeSuggestionMessage(data) {
      if (data.status === "needs_clarification") {
        return `设定变更需要补充信息：${(data.questions || []).join("；")}`;
      }
      return actionMessage("设定变更建议", data);
    }

    function settingChangeClarificationAnswer(options = {}) {
      const memoryAnswer = $("memoryRepairClarificationAnswer")?.value.trim() || "";
      const workbenchAnswer = $("settingChangeClarificationAnswer")?.value.trim() || "";
      if (options.preferMemoryInput) return memoryAnswer || workbenchAnswer;
      return workbenchAnswer || memoryAnswer;
    }

    async function settingChangeApply(options = {}) {
      return withBusy("应用设定变更", async () => {
        const proposalPath = settingChangeProposalPath();
        if (!proposalPath) {
          throw new Error("请先生成设定变更 proposal。");
        }
        const data = await apiPost("/api/settings/change/apply", {
          path: projectPath(),
          proposal_path: proposalPath,
          provider: $("provider").value,
          session_id: $("sessionId").value.trim(),
          sync_session: Boolean(options.syncSession),
          use_search_context: $("useSearchContext").checked,
          vector_context: $("vectorContextMode").value,
          use_vector_context: $("useVectorContext").checked,
          polish_mode: $("autoPolish").checked ? "auto" : "single_pass",
        });
        $("fileViewer").textContent = JSON.stringify(data, null, 2);
        renderSettingChangeImpact(data.proposal || {});
        renderManagementEvents(data.management_events || []);
        const syncedSession = data.sync_result?.session;
        if (syncedSession) renderSessionSummary(syncedSession);
        await refreshAll({ silent: true });
        setMessage(actionMessage("应用设定变更", data));
      });
    }

    function settingChangeInstruction(options = {}) {
      const memoryInput = $("memoryRepairInstruction")?.value.trim() || "";
      const workbenchInput = $("instruction")?.value.trim() || "";
      if (options.preferMemoryInput) return memoryInput || workbenchInput;
      return workbenchInput || memoryInput;
    }

    function settingChangeProposalPath() {
      return $("workbenchSettingChangeProposalPath")?.value.trim()
        || $("memoryRepairProposalPath")?.value.trim()
        || "";
    }

    function settingChangeProposalPathFor(source = "auto") {
      const memoryPath = $("memoryRepairProposalPath")?.value.trim() || "";
      const workbenchPath = $("workbenchSettingChangeProposalPath")?.value.trim() || "";
      if (source === "memory") return memoryPath || workbenchPath;
      if (source === "workbench") return workbenchPath || memoryPath;
      return workbenchPath || memoryPath;
    }

    function openSettingChangeProposalFile(source = "auto") {
      const proposalPath = settingChangeProposalPathFor(source);
      if (!proposalPath) {
        setMessage("请先生成设定变更 proposal。", true);
        return;
      }
      readWorkspaceFile(proposalPath);
    }

    function syncSettingChangeProposalPath(path) {
      if (!path) return;
      if ($("workbenchSettingChangeProposalPath")) $("workbenchSettingChangeProposalPath").value = path;
      if ($("memoryRepairProposalPath")) $("memoryRepairProposalPath").value = path;
    }

    function renderSettingChangeClarification(data) {
      const questions = data.questions || [];
      const turns = data.conversation_turns || [];
      const html = `
        <b>设定变更需要补充</b>
        <div>clarification: ${escapeHtml(data.clarification_id || "")}</div>
        ${questions.map((question) => `<div>问题：${escapeHtml(question)}</div>`).join("")}
        ${turns.length ? `<div style="margin-top: 6px;">对话：${escapeHtml(turns.map((turn) => `${turn.role}: ${turn.content}`).join(" / "))}</div>` : ""}
      `;
      ["settingChangeClarificationPanel", "memoryRepairClarificationPanel"].forEach((id) => {
        if ($(id)) {
          $(id).innerHTML = html;
          $(id).classList.remove("hidden");
        }
      });
      setSettingChangeClarificationControlsVisible(true);
    }

    function clearSettingChangeClarification() {
      ["settingChangeClarificationPanel", "memoryRepairClarificationPanel"].forEach((id) => {
        if ($(id)) {
          $(id).innerHTML = "设定变更澄清：暂无";
          $(id).classList.add("hidden");
        }
      });
      ["settingChangeClarificationAnswer", "memoryRepairClarificationAnswer"].forEach((id) => {
        if ($(id)) $(id).value = "";
      });
      setSettingChangeClarificationControlsVisible(false);
    }

    function setSettingChangeClarificationControlsVisible(visible) {
      ["settingChangeClarificationControls", "memoryRepairClarificationControls"].forEach((id) => {
        if ($(id)) $(id).classList.toggle("hidden", !visible);
      });
    }

    function resetSettingChangeState(focusId = "") {
      latestSettingChangeClarificationId = "";
      clearSettingChangeClarification();
      ["workbenchSettingChangeProposalPath", "memoryRepairProposalPath"].forEach((id) => {
        if ($(id)) $(id).value = "";
      });
      if ($("settingChangeImpactPanel")) $("settingChangeImpactPanel").textContent = "设定变更影响分析：暂无";
      if ($("memoryRepairImpactPanel")) $("memoryRepairImpactPanel").textContent = "设定变更影响分析：暂无";
      if ($("fileViewer")) $("fileViewer").textContent = "";
      setMessage("设定变更前端状态已重置。");
      if (focusId && $(focusId)) $(focusId).focus();
    }

    function renderSettingChangeImpact(proposal) {
      const impact = proposal.impact || {};
      const actions = proposal.followup_actions || [];
      const operations = proposal.operations || [];
      const notes = (proposal.notes || []).filter((note) => String(note).includes("批次") || String(note).includes("分批")).slice(0, 4);
      const html = `
        <b>设定变更影响分析</b>
        <div>operations: ${escapeHtml(String(operations.length || 0))}</div>
        <div>domains: ${escapeHtml((proposal.domains || impact.domains || []).join(", ") || "none")}</div>
        <div>risk: ${escapeHtml(proposal.risk_level || impact.risk_level || "")}</div>
        <div>entities: ${escapeHtml((impact.entity_ids || []).join(", ") || "none")}</div>
        <div>chapters: ${escapeHtml((impact.affected_chapters || []).join(", ") || "none")}</div>
        <div>sessions: ${escapeHtml((impact.affected_sessions || []).join(", ") || "none")}</div>
        <div>stale accepted: ${escapeHtml((impact.stale_chapters || []).join(", ") || "none")}</div>
        ${impact.summary ? `<div>${escapeHtml(impact.summary)}</div>` : ""}
        ${notes.length ? `<div style="margin-top: 6px;">${escapeHtml(notes.join(" / "))}</div>` : ""}
        ${actions.length ? `<div style="margin-top: 6px;">follow-up: ${escapeHtml(actions.map((item) => item.action).join(", "))}</div>` : ""}
      `;
      if ($("settingChangeImpactPanel")) $("settingChangeImpactPanel").innerHTML = html;
      if ($("memoryRepairImpactPanel")) $("memoryRepairImpactPanel").innerHTML = html;
    }

    function currentSettingChangeStage() {
      const text = $("sessionPanel")?.textContent || "";
      if (text.includes("needs_user_review") || text.includes("needs_revision")) return "content_review";
      if (text.includes("outline") || $("sessionId").value.trim()) return "outline_discussion";
      return "pre_creation";
    }

    async function validateProject() {
      return withBusy("项目检查", async () => {
        const data = await apiGet("/api/validate", { path: projectPath() });
        $("fileViewer").textContent = JSON.stringify(data, null, 2);
        renderValidationStatus(data);
        renderNextStep({ validation: data });
        setMessage(`项目检查完成：${data.error_count || 0} 个错误，${data.warning_count || 0} 个警告`);
      });
    }

    async function generateChapterMemory(chapter) {
      await withBusy("生成章节记忆", async () => {
        const data = await apiPost("/api/chapter-memory/generate", {
          path: projectPath(),
          chapter_number: chapter,
          provider: $("provider").value,
          force: true,
        });
        $("fileViewer").textContent = JSON.stringify(data, null, 2);
        await refreshAll({ silent: true });
        setMessage(actionMessage("生成章节记忆", data));
      });
    }

    async function rebuildChapterMemory() {
      await withBusy("补全 / 刷新章节记忆", async () => {
        const data = await apiPost("/api/chapter-memory/rebuild", {
          path: projectPath(),
          provider: $("provider").value,
          mode: "missing_or_stale",
        });
        $("fileViewer").textContent = JSON.stringify(data, null, 2);
        await refreshAll({ silent: true });
        setMessage(actionMessage("补全 / 刷新章节记忆", data));
      });
    }

    function exportPayload() {
      const payload = {
        path: projectPath(),
        include_unaccepted: $("exportIncludeUnaccepted").checked,
        force: $("exportForce").checked,
      };
      const chapters = $("exportChapters").value.trim();
      const title = $("exportTitle").value.trim();
      const fromChapter = $("exportFromChapter").value.trim();
      const toChapter = $("exportToChapter").value.trim();
      const output = $("exportOutput").value.trim();
      if (chapters) payload.chapters = chapters;
      if (title) payload.title = title;
      if (fromChapter) payload.from_chapter = Number(fromChapter);
      if (toChapter) payload.to_chapter = Number(toChapter);
      if (output) payload.output = output;
      return payload;
    }

    async function runExport(endpoint, label) {
      return withBusy(label, async () => {
        const data = await apiPost(endpoint, exportPayload());
        $("fileViewer").textContent = JSON.stringify(data, null, 2);
        await refreshAll({ silent: true });
        setMessage(actionMessage(label, data));
      });
    }

    async function viewFile() {
      try {
        const data = await apiGet("/api/chapter-file", {
          path: projectPath(),
          chapter: chapterNumber(),
          file: $("fileType").value,
        });
        $("fileViewer").textContent = data.exists ? data.content : `${data.relative_path || data.path} 不存在`;
        setMessage("文件已加载");
      } catch (error) {
        setMessage(error.message, true);
      }
    }

    async function loadCompare() {
      await Promise.all(chapterFileTypes.map(async (type) => {
        const target = $(`${type}Viewer`);
        try {
          const data = await apiGet("/api/chapter-file", {
            path: projectPath(),
            chapter: chapterNumber(),
            file: type,
          });
          target.textContent = data.exists ? data.content : `${data.relative_path} 不存在`;
        } catch (error) {
          target.textContent = error.message;
        }
      }));
      setMessage("章节对照已加载");
    }

    async function loadEditorFile() {
      try {
        const target = $("editorTarget").value;
        const source = $("editorSource").value.trim() || `${target}.md`;
        const relPath = `memory/chapters/${String(chapterNumber()).padStart(3, "0")}/${source}`;
        const data = await apiGet("/api/read-file", { path: projectPath(), file: relPath });
        editorLoadedContent = data.content || "";
        editorSourceFile = source;
        $("chapterEditorText").value = editorLoadedContent;
        $("editorSource").value = source;
        $("editorDirty").textContent = "已加载";
        $("editorSavedPath").textContent = relPath;
        setMessage(`已加载 ${relPath}`);
      } catch (error) {
        setMessage(error.message, true);
      }
    }

    async function saveEditorVersion() {
      try {
        const target = $("editorTarget").value;
        const data = await apiPost("/api/save-chapter-file", {
          path: projectPath(),
          chapter_number: chapterNumber(),
          target,
          source_file: editorSourceFile || `${target}.md`,
          content: $("chapterEditorText").value,
          instruction: "Web editor save",
        });
        editorLoadedContent = $("chapterEditorText").value;
        $("editorDirty").textContent = "已保存";
        $("editorSavedPath").textContent = data.relative_path || data.output_path || "";
        $("diffLeft").value = `memory/chapters/${String(chapterNumber()).padStart(3, "0")}/${editorSourceFile || `${target}.md`}`;
        $("diffRight").value = data.relative_path || "";
        await refreshAll({ silent: true });
        setMessage(`已保存 ${data.relative_path}`);
      } catch (error) {
        setMessage(error.message, true);
      }
    }

    async function loadAuditAnnotations() {
      try {
        const auditedFile = $("auditLocateFile").value;
        const [annotations, fileData] = await Promise.all([
          apiGet("/api/audit-annotations", { path: projectPath(), chapter: chapterNumber(), file: auditedFile }),
          apiGet("/api/read-file", {
            path: projectPath(),
            file: `memory/chapters/${String(chapterNumber()).padStart(3, "0")}/${auditedFile}`,
          }),
        ]);
        latestAuditAnnotations = annotations;
        latestSelectedAuditIssue = null;
        $("auditTextViewer").value = fileData.content || "";
        renderAuditIssues(annotations.issues || []);
        setMessage("Audit issue 定位已加载");
      } catch (error) {
        setMessage(error.message, true);
      }
    }

    function renderAuditIssues(issues) {
      if (!issues.length) {
        $("auditIssueList").textContent = "无 issue";
        return;
      }
      $("auditIssueList").innerHTML = issues.map((issue, index) => {
        const first = (issue.matches || [])[0] || {};
        const loc = first.matched ? `line ${first.line}, col ${first.column}` : "无法定位";
        return `<button class="issue-button" data-index="${index}"><b>${escapeHtml(issue.id)}</b> <span class="badge">${escapeHtml(issue.severity || "")}</span><br>${escapeHtml(issue.description || "")}<br><small>${escapeHtml(loc)}</small></button>`;
      }).join("");
      document.querySelectorAll(".issue-button").forEach((button) => {
        button.addEventListener("click", () => {
          const issue = issues[Number(button.dataset.index)];
          latestSelectedAuditIssue = issue;
          const match = (issue.matches || []).find((item) => item.matched);
          if (!match) {
            setMessage("该 issue 的 evidence 无法定位到正文", true);
            return;
          }
          const viewer = $("auditTextViewer");
          viewer.focus();
          viewer.setSelectionRange(match.start_offset, match.end_offset);
          const lineHeight = 20;
          viewer.scrollTop = Math.max(0, (match.line - 3) * lineHeight);
          setMessage(`已定位 ${issue.id}`);
        });
      });
    }

    async function auditIssueAsSettingChange() {
      const issue = latestSelectedAuditIssue || (latestAuditAnnotations?.issues || [])[0];
      if (!issue) {
        setMessage("请先加载并选择一个 Audit issue。", true);
        return;
      }
      const evidence = (issue.matches || [])[0] || {};
      const instruction = [
        `请作为设定变更处理 Audit issue ${issue.id || ""}。`,
        issue.description || "",
        issue.suggested_fix ? `建议修复：${issue.suggested_fix}` : "",
        evidence.quote ? `证据：${evidence.quote}` : "",
      ].filter(Boolean).join("\n");
      $("instruction").value = instruction;
      return settingChangeSuggest("content_review", { auditIssueIds: [issue.id].filter(Boolean) });
    }

    async function loadRuns() {
      try {
        const data = await apiGet("/api/runs", { path: projectPath() });
        const runRows = (data.run_logs || []).map((run) => `
          <tr><td>${escapeHtml(run.run_id || "")}</td><td>${escapeHtml(run.task || "")}</td><td>${escapeHtml(run.status || "")}</td><td>${escapeHtml(run.started_at || "")}</td><td>${escapeHtml(run.path || "")}</td></tr>
        `).join("");
        const callRows = (data.provider_calls || []).map((call) => `
          <tr><td>${escapeHtml(call.provider || "")}</td><td>${escapeHtml(call.model || "")}</td><td>${escapeHtml(call.status || "")}</td><td>${escapeHtml(call.started_at || "")}</td><td>${escapeHtml(call.error_type || "")}</td><td>${escapeHtml(call.model_io_path || "")}</td></tr>
        `).join("");
        const ioRows = (data.model_io_logs || []).map((log) => `
          <tr><td>${escapeHtml(log.agent_name || "")}</td><td>${escapeHtml(log.provider || "")}</td><td>${escapeHtml(log.model || "")}</td><td>${escapeHtml(log.status || "")}</td><td>${escapeHtml(log.started_at || "")}</td><td>${escapeHtml(log.model_io_path || "")}</td></tr>
        `).join("");
        $("runLogPanel").innerHTML = `
          <h3>Run logs</h3><table><thead><tr><th>run_id</th><th>task</th><th>status</th><th>started_at</th><th>path</th></tr></thead><tbody>${runRows || "<tr><td colspan='5'>无</td></tr>"}</tbody></table>
          <h3 style="margin-top: 16px;">Provider calls</h3><table><thead><tr><th>provider</th><th>model</th><th>status</th><th>started_at</th><th>error</th><th>model_io</th></tr></thead><tbody>${callRows || "<tr><td colspan='6'>无</td></tr>"}</tbody></table>
          <h3 style="margin-top: 16px;">Model I/O</h3><table><thead><tr><th>agent</th><th>provider</th><th>model</th><th>status</th><th>started_at</th><th>path</th></tr></thead><tbody>${ioRows || "<tr><td colspan='6'>无</td></tr>"}</tbody></table>
        `;
      } catch (error) {
        setMessage(error.message, true);
      }
    }

    async function searchProjectContent() {
      try {
        const query = $("searchQuery").value.trim();
        if (!query) {
          setMessage("请输入搜索内容", true);
          return;
        }
        const params = {
          path: projectPath(),
          query,
          type: $("searchType").value,
          limit: $("searchLimit").value || "10",
          highlight: $("searchHighlight").checked ? "1" : "0",
          use_vector: $("searchUseVector").checked ? "1" : "0",
        };
        const chapter = $("searchChapter").value.trim();
        if (chapter) params.chapter = chapter;
        const data = await apiGet("/api/search", params);
        renderSearchResults(data);
        setMessage(`搜索完成：${(data.results || []).length} 条结果`);
      } catch (error) {
        $("searchResults").innerHTML = `<div class="message error">${escapeHtml(error.message)}</div>`;
        setMessage(error.message, true);
      }
    }

    function renderSearchResults(data) {
      const results = data.results || [];
      if (!results.length) {
        $("searchResults").innerHTML = "未找到结果。";
        return;
      }
      $("searchResults").innerHTML = `
        <table>
          <thead><tr><th>类型</th><th>标题</th><th>score</th><th>来源</th><th>摘要</th></tr></thead>
          <tbody>
            ${results.map((result) => `
              <tr>
                <td>${escapeHtml(result.type || "")}</td>
                <td>${escapeHtml(result.title || result.id || "")}</td>
                <td>${escapeHtml(result.score ?? "")}</td>
                <td><button class="search-open-file" data-path="${escapeAttr(result.path || "")}">${escapeHtml(result.path || "")}</button></td>
                <td>${safeHighlightedExcerpt(result.highlighted_excerpt || result.excerpt || "")}</td>
              </tr>
            `).join("")}
          </tbody>
        </table>
      `;
      document.querySelectorAll(".search-open-file").forEach((button) => {
        button.addEventListener("click", () => readWorkspaceFile(button.dataset.path));
      });
    }

    async function loadUsage() {
      try {
        const data = await apiGet("/api/usage", { path: projectPath() });
        renderUsage(data.usage || {});
      } catch (error) {
        $("usagePanel").innerHTML = `<div class="message error">${escapeHtml(error.message)}</div>`;
        setMessage(error.message, true);
      }
    }

    function renderUsage(usage) {
      const total = usage.total || {};
      const byAgent = usage.by_agent || {};
      const byProvider = usage.by_provider || {};
      const byModel = usage.by_model || {};
      const tableFromObject = (items, firstHeader) => {
        const rows = Object.entries(items || {}).map(([name, item]) => `
          <tr>
            <td>${escapeHtml(name)}</td>
            <td>${escapeHtml(item.call_count ?? item.calls ?? 0)}</td>
            <td>${escapeHtml(item.success_count ?? item.successes ?? 0)}</td>
            <td>${escapeHtml(item.failed_count ?? item.failures ?? item.failure_count ?? 0)}</td>
            <td>${escapeHtml(item.prompt_tokens ?? "")}</td>
            <td>${escapeHtml(item.completion_tokens ?? "")}</td>
            <td>${escapeHtml(item.total_tokens ?? "")}</td>
          </tr>
        `).join("");
        return `<table><thead><tr><th>${firstHeader}</th><th>calls</th><th>success</th><th>failed</th><th>prompt</th><th>completion</th><th>total</th></tr></thead><tbody>${rows || "<tr><td colspan='7'>无</td></tr>"}</tbody></table>`;
      };
      $("usagePanel").innerHTML = `
        <div class="metric">
          <b>总用量</b>
          <div>calls: ${escapeHtml(total.call_count ?? total.calls ?? usage.total_calls ?? 0)}</div>
          <div>success: ${escapeHtml(total.success_count ?? total.successes ?? usage.success_count ?? 0)} / failed: ${escapeHtml(total.failed_count ?? total.failures ?? usage.failure_count ?? 0)}</div>
          <div>tokens: prompt=${escapeHtml(total.prompt_tokens ?? "")} completion=${escapeHtml(total.completion_tokens ?? "")} total=${escapeHtml(total.total_tokens ?? usage.total_tokens ?? "")}</div>
        </div>
        <h3>按 Agent</h3>${tableFromObject(byAgent, "agent")}
        <h3 style="margin-top: 16px;">按 Provider</h3>${tableFromObject(byProvider, "provider")}
        <h3 style="margin-top: 16px;">按 Model</h3>${tableFromObject(byModel, "model")}
        <details style="margin-top: 12px;"><summary>完整用量 JSON</summary><pre>${escapeHtml(JSON.stringify(usage, null, 2))}</pre></details>
      `;
    }

    async function loadProviderConfig() {
      try {
        const data = await apiGet("/api/provider-config", { path: projectPath() });
        const missingFields = [];
        if (!hasResponseField(data, "effective_agents")) missingFields.push("effective_agents");
        if (!hasResponseField(data, "embedding_api")) missingFields.push("embedding_api");
        providerConfigBackendMismatch = missingFields.length
          ? backendVersionMismatchMessage("/api/provider-config", missingFields)
          : "";
        if (providerConfigBackendMismatch) {
          warnBackendVersionMismatch(providerConfigBackendMismatch);
          embeddingConfigEditing = true;
          renderEmbeddingConfigPanel({ status: "backend_mismatch", message: providerConfigBackendMismatch });
        } else {
          renderEmbeddingConfigPanel(data.embedding_api || {});
        }
        providerConfigCache = data.agents?.content || null;
        providerEffectiveCache = data.effective_agents || {};
        const warnings = [
          ...(providerConfigBackendMismatch ? [providerConfigBackendMismatch] : []),
          ...(data.agents?.warnings || []),
        ];
        $("providerConfigWarnings").innerHTML = warnings.length
          ? `<span style="color:#b91c1c;">${warnings.map(escapeHtml).join("<br>")}</span>`
          : "Agent API 配置正常。";
        $("providerConfigPanel").textContent = JSON.stringify(data, null, 2);
        renderProviderEditor(providerConfigCache);
      } catch (error) {
        setMessage(error.message, true);
      }
    }

    function renderEmbeddingConfigPanel(summary = {}) {
      const status = summary.status || (summary.configured === true ? "configured" : "not_configured");
      const configured = summary.configured === true && status === "configured";
      const form = $("embeddingConfigForm");
      const summaryPanel = $("embeddingConfigSummary");
      if (!form || !summaryPanel) return;

      const showCollapsed = configured && !embeddingConfigEditing;
      form.classList.toggle("hidden", showCollapsed);

      if (configured) {
        const provider = summary.provider || summary.active_provider || "config";
        const effectiveProvider = summary.effective_provider && summary.effective_provider !== provider
          ? ` / effective: ${summary.effective_provider}`
          : "";
        const model = summary.model || "未设置";
        const apiKeyEnv = summary.api_key_env || "未设置";
        const baseUrlEnv = summary.base_url_env || "未设置";
        const dimensions = summary.effective_dimensions || summary.dimensions || "默认";
        const batchSize = summary.batch_size || "默认";
        const effectiveBatch = summary.effective_batch_size && summary.effective_batch_size !== summary.batch_size
          ? ` / effective: ${summary.effective_batch_size}`
          : "";
        const warnings = (summary.warnings || []).length
          ? `<div class="status-warn">${escapeHtml((summary.warnings || []).join("；"))}</div>`
          : "";
        summaryPanel.classList.remove("hidden");
        summaryPanel.innerHTML = `
          <b class="status-ok">Embedding API 已配置</b>
          <div>模型：${escapeHtml(model)}</div>
          <div>Provider：${escapeHtml(provider + effectiveProvider)}</div>
          <div>dimensions：${escapeHtml(dimensions)}</div>
          <div>batch_size：${escapeHtml(batchSize + effectiveBatch)}</div>
          <div>api_key_env：${escapeHtml(apiKeyEnv)}</div>
          <div>base_url_env：${escapeHtml(baseUrlEnv)}</div>
          ${warnings}
          ${showCollapsed ? '<button id="editEmbeddingConfig" style="width: 100%; margin-top: 8px;">修改配置</button>' : ""}
        `;
        const editButton = $("editEmbeddingConfig");
        if (editButton) {
          editButton.addEventListener("click", () => {
            embeddingConfigEditing = true;
            renderEmbeddingConfigPanel(summary);
          });
        }
        if (embeddingConfigEditing) {
          if ($("configEmbeddingProvider")) $("configEmbeddingProvider").value = summary.provider || "dashscope";
          if ($("configEmbeddingModel") && !$("configEmbeddingModel").value) $("configEmbeddingModel").value = summary.model || "";
          if ($("configEmbeddingDimensions") && !$("configEmbeddingDimensions").value) {
            $("configEmbeddingDimensions").value = summary.effective_dimensions || summary.dimensions || "";
          }
          if ($("configEmbeddingBatchSize") && !$("configEmbeddingBatchSize").value) {
            $("configEmbeddingBatchSize").value = summary.effective_batch_size || summary.batch_size || "";
          }
          setEmbeddingConfigStatus("请重新填写 Embedding Base URL、API Key、provider、模型名和参数后保存。");
        } else {
          setEmbeddingConfigStatus("Embedding API 已配置。点击“修改配置”可重新测试并保存。");
        }
        return;
      }

      summaryPanel.classList.toggle("hidden", status === "not_configured" || status === "backend_mismatch");
      if (status === "env_missing") {
        summaryPanel.innerHTML = `
          <b class="status-bad">Embedding API 未配置完整</b>
          <div>模型：${escapeHtml(summary.model || "未设置")}</div>
          <div>Provider：${escapeHtml(summary.provider || summary.active_provider || "未设置")}</div>
          <div>dimensions：${escapeHtml(summary.effective_dimensions || summary.dimensions || "默认")}</div>
          <div>batch_size：${escapeHtml(summary.effective_batch_size || summary.batch_size || "默认")}</div>
        `;
        setEmbeddingConfigStatus(`Embedding API 配置缺少环境变量：${(summary.env_missing || []).join(", ")}`, true);
        return;
      }
      if (status === "invalid_config") {
        summaryPanel.innerHTML = '<b class="status-bad">Embedding API 配置无效</b>';
        setEmbeddingConfigStatus(`Embedding API 配置无效：${summary.message || "请重新填写并保存。"}`, true);
        return;
      }
      if (status === "test_only") {
        summaryPanel.innerHTML = `
          <b class="status-warn">当前是测试 embedding 配置</b>
          <div>模型：${escapeHtml(summary.model || "未设置")}</div>
        `;
        setEmbeddingConfigStatus("当前是测试 embedding 配置；请填写真实 API 后保存。", true);
        return;
      }
      if (status === "backend_mismatch") {
        setEmbeddingConfigStatus(summary.message || providerConfigBackendMismatch || "Web UI 后台版本不匹配，请重启后台进程。", true);
        return;
      }
      setEmbeddingConfigStatus("请填写 Embedding Base URL、API Key、provider、模型名和参数。");
    }

    function providerParameterCapabilities(providerName, thinkingType) {
      const provider = String(providerName || "").toLowerCase();
      const thinking = thinkingType || "disabled";
      const caps = {};
      [
        "provider", "model", "base_url_env", "api_key_env", "max_context_tokens",
        "max_tokens", "timeout_seconds", "max_retries",
      ].forEach((field) => {
        caps[field] = { effective: true, editable: true };
      });
      caps.thinking = provider === "deepseek" || provider === "zai"
        ? { effective: true, editable: true }
        : { effective: false, editable: false, reason: "当前 provider 不发送 thinking 参数" };
      caps.reasoning = provider === "deepseek" && thinking === "enabled"
        ? { effective: true, editable: true }
        : { effective: false, editable: false, reason: "仅 DeepSeek 且 thinking enabled 时发送 reasoning_effort" };
      if (provider === "mock") {
        caps.temperature = { effective: false, editable: false, reason: "mock provider 不发送 temperature" };
      } else if (provider === "deepseek" && thinking === "enabled") {
        caps.temperature = { effective: false, editable: false, reason: "DeepSeek thinking enabled 时不会发送 temperature" };
      } else {
        caps.temperature = { effective: true, editable: true };
      }
      caps.json_response_format = {
        effective: true,
        editable: true,
        allowed_values: provider === "deepseek" || provider === "zai"
          ? ["auto", "json_object"]
          : jsonResponseFormatValues,
      };
      return caps;
    }

    function currentProviderCapabilities() {
      const provider = $("providerProviderField").value.trim();
      const rawThinking = $("providerThinkingTypeField").value;
      const thinking = rawThinking === "__na__" ? "disabled" : rawThinking || "disabled";
      return providerParameterCapabilities(provider, thinking);
    }

    function currentProviderJsonValues() {
      return currentProviderCapabilities().json_response_format?.allowed_values || jsonResponseFormatValues;
    }

    function setCapabilityNote(id, capability) {
      const note = $(id);
      if (!note) return;
      note.classList.toggle("na", capability?.effective === false);
      note.textContent = capability?.effective === false ? `NA：${capability.reason || "当前 provider 不发送此参数"}` : "";
    }

    function applyProviderCapabilityState(disableAll = false) {
      const inheritLocksCallFields = Boolean(disableAll);
      if ($("providerThinkingTypeField").value === "__na__") {
        $("providerThinkingTypeField").value = "disabled";
      }
      let caps = currentProviderCapabilities();
      if (!caps.thinking.effective) {
        $("providerThinkingTypeField").value = "__na__";
      }
      caps = currentProviderCapabilities();
      const fieldStates = [
        ["thinking", "providerThinkingTypeField", "providerThinkingTypeStatus", "__na__"],
        ["reasoning", "providerReasoningField", "providerReasoningStatus", "NA"],
        ["temperature", "providerTemperatureField", "providerTemperatureStatus", "NA"],
      ];
      fieldStates.forEach(([field, inputId, noteId, naValue]) => {
        const capability = caps[field] || { effective: true, editable: true };
        const input = $(inputId);
        if (capability.effective === false) {
          input.value = naValue;
          input.disabled = true;
        } else {
          if (input.value === "NA" || input.value === "__na__") {
            input.value = field === "thinking" ? "disabled" : "";
          }
          input.disabled = false;
        }
        setCapabilityNote(noteId, capability);
      });
      [
        "providerProviderField", "providerModelField", "providerBaseUrlEnvField",
        "providerApiKeyEnvField", "providerMaxTokensField", "providerMaxContextTokensField",
        "providerTimeoutSecondsField", "providerMaxRetriesField",
      ].forEach((id) => {
        $(id).disabled = inheritLocksCallFields;
      });
      $("providerFieldEditor").disabled = false;
      renderProviderAdvancedStatus();
      return caps;
    }

    function renderProviderAdvancedStatus() {
      const allowed = currentProviderJsonValues();
      const note = $("providerAdvancedStatus");
      if (!note) return;
      note.classList.remove("na");
      note.textContent = `json_response_format 可用值：${allowed.join(", ")}`;
    }

    function providerPatchAllowedByCapabilities(agent, capabilities) {
      const editable = {};
      [
        "provider", "model", "base_url_env", "api_key_env",
        "max_context_tokens", "max_tokens", "timeout_seconds", "max_retries",
      ].forEach((key) => {
        if (Object.prototype.hasOwnProperty.call(agent, key)) editable[key] = agent[key];
      });
      if (capabilities.reasoning?.effective !== false && Object.prototype.hasOwnProperty.call(agent, "reasoning")) {
        editable.reasoning = agent.reasoning;
      }
      if (capabilities.temperature?.effective !== false && Object.prototype.hasOwnProperty.call(agent, "temperature")) {
        editable.temperature = agent.temperature;
      }
      if (capabilities.thinking?.effective !== false && agent.thinking?.type) {
        editable.thinking = { type: agent.thinking.type };
      }
      if (Object.prototype.hasOwnProperty.call(agent, "json_response_format")) {
        editable.json_response_format = agent.json_response_format;
      }
      return editable;
    }

    function providerBusinessPatchAllowedByCapabilities(agent, capabilities) {
      const editable = {};
      if (capabilities.reasoning?.effective !== false && Object.prototype.hasOwnProperty.call(agent, "reasoning")) {
        editable.reasoning = agent.reasoning;
      }
      if (capabilities.temperature?.effective !== false && Object.prototype.hasOwnProperty.call(agent, "temperature")) {
        editable.temperature = agent.temperature;
      }
      if (capabilities.thinking?.effective !== false && agent.thinking?.type) {
        editable.thinking = { type: agent.thinking.type };
      }
      return editable;
    }

    function validateProviderJsonResponseFormat(patch) {
      if (!Object.prototype.hasOwnProperty.call(patch, "json_response_format")) return;
      const value = patch.json_response_format;
      const allowed = currentProviderJsonValues();
      if (!allowed.includes(value)) {
        throw new Error(`当前 provider 不支持 json_response_format=${value}；可用值：${allowed.join(", ")}`);
      }
    }

    function renderProviderEditor(config) {
      const agents = config?.agents || {};
      const names = ["default", ...Array.from(new Set([...providerAgentNames, ...Object.keys(agents)]))];
      const previousName = $("providerAgentSelect").value;
      $("providerAgentSelect").innerHTML = names.map((name) => `<option value="${escapeAttr(name)}">${escapeHtml(name)}</option>`).join("");
      if (names.length) {
        $("providerAgentSelect").value = names.includes(previousName) ? previousName : names[0];
        renderProviderAgentFields();
      } else {
        $("providerFieldEditor").value = "";
      }
    }

    function renderProviderAgentFields() {
      const name = $("providerAgentSelect").value;
      const inheritsDefault = providerAgentInheritsDefault(name);
      const agent = providerAgentDisplayConfig(name, inheritsDefault);
      $("providerInheritDefaultRow").classList.toggle("hidden", name === "default");
      $("providerInheritDefaultField").checked = name !== "default" && inheritsDefault;
      $("providerInheritDefaultField").disabled = name === "default";
      $("providerProviderField").value = agent.provider || "";
      $("providerModelField").value = agent.model || "";
      $("providerBaseUrlEnvField").value = agent.base_url_env || "";
      $("providerApiKeyEnvField").value = agent.api_key_env || "";
      $("providerThinkingTypeField").value = agent.thinking?.type || "disabled";
      $("providerReasoningField").value = agent.reasoning || "";
      $("providerTemperatureField").value = agent.temperature ?? "";
      $("providerMaxTokensField").value = agent.max_tokens ?? "";
      $("providerMaxContextTokensField").value = agent.max_context_tokens ?? "";
      $("providerTimeoutSecondsField").value = agent.timeout_seconds ?? "";
      $("providerMaxRetriesField").value = agent.max_retries ?? "";
      const caps = applyProviderCapabilityState(name !== "default" && inheritsDefault);
      $("providerFieldEditor").value = JSON.stringify(
        name !== "default" && inheritsDefault
          ? providerBusinessPatchAllowedByCapabilities(agent, caps)
          : providerEditablePatch(agent, caps),
        null,
        2,
      );
      renderProviderEffectivePanel();
    }

    function providerAgentInheritsDefault(name) {
      if (name === "default") return false;
      const effective = providerEffectiveCache?.[name] || {};
      if (effective.inherit_default === true || effective.inherits_default === true) return true;
      const raw = providerConfigCache?.agents?.[name] || {};
      return raw.inherit_default === true;
    }

    function providerDefaultDisplayConfig() {
      return providerEffectiveCache?.default?.config || providerConfigCache?.default || {};
    }

    function providerAgentDisplayConfig(name, inheritsDefault = providerAgentInheritsDefault(name)) {
      if (name === "default") return providerDefaultDisplayConfig();
      if (inheritsDefault) {
        const current = providerEffectiveCache?.[name]?.config || providerConfigCache?.agents?.[name] || {};
        return { ...providerDefaultDisplayConfig(), ...providerBusinessFields(current) };
      }
      return providerEffectiveCache?.[name]?.config || providerConfigCache?.agents?.[name] || providerDefaultDisplayConfig();
    }

    function providerBusinessFields(config) {
      const business = {};
      if (Object.prototype.hasOwnProperty.call(config, "reasoning")) business.reasoning = config.reasoning;
      if (Object.prototype.hasOwnProperty.call(config, "temperature")) business.temperature = config.temperature;
      if (config.thinking?.type) business.thinking = { type: config.thinking.type };
      return business;
    }

    function fillProviderForm(agent) {
      $("providerProviderField").value = agent.provider || "";
      $("providerModelField").value = agent.model || "";
      $("providerBaseUrlEnvField").value = agent.base_url_env || "";
      $("providerApiKeyEnvField").value = agent.api_key_env || "";
      $("providerThinkingTypeField").value = agent.thinking?.type || "disabled";
      $("providerReasoningField").value = agent.reasoning || "";
      $("providerTemperatureField").value = agent.temperature ?? "";
      $("providerMaxTokensField").value = agent.max_tokens ?? "";
      $("providerMaxContextTokensField").value = agent.max_context_tokens ?? "";
      $("providerTimeoutSecondsField").value = agent.timeout_seconds ?? "";
      $("providerMaxRetriesField").value = agent.max_retries ?? "";
      const caps = applyProviderCapabilityState(false);
      $("providerFieldEditor").value = JSON.stringify(providerEditablePatch(agent, caps), null, 2);
    }

    function toggleProviderDefaultInheritance() {
      const name = $("providerAgentSelect").value;
      if (name === "default") return;
      const inheritsDefault = $("providerInheritDefaultField").checked;
      fillProviderForm(providerAgentDisplayConfig(name, inheritsDefault));
      applyProviderCapabilityState(inheritsDefault);
      renderProviderEffectivePanel();
    }

    function providerEditablePatch(agent, capabilities = currentProviderCapabilities()) {
      return providerPatchAllowedByCapabilities(agent, capabilities);
    }

    function buildProviderPatchFromForm() {
      const patch = {};
      const caps = currentProviderCapabilities();
      [
        ["provider", "providerProviderField"],
        ["model", "providerModelField"],
        ["api_key_env", "providerApiKeyEnvField"],
      ].forEach(([key, id]) => {
        const value = $(id).value.trim();
        if (!value) throw new Error(`${key} 必须设置`);
        patch[key] = value;
      });
      [
        ["base_url_env", "providerBaseUrlEnvField"],
      ].forEach(([key, id]) => {
        const value = $(id).value.trim();
        if (value) patch[key] = value;
      });
      if (caps.reasoning?.effective !== false) {
        const reasoning = $("providerReasoningField").value.trim();
        if (reasoning) patch.reasoning = reasoning;
      }
      const thinkingType = $("providerThinkingTypeField").value.trim();
      if (caps.thinking?.effective !== false) patch.thinking = { type: thinkingType || "disabled" };
      [
        ["max_tokens", "providerMaxTokensField"],
        ["max_context_tokens", "providerMaxContextTokensField"],
        ["timeout_seconds", "providerTimeoutSecondsField"],
        ["max_retries", "providerMaxRetriesField"],
      ].forEach(([key, id]) => {
        const raw = $(id).value.trim();
        if (!raw) return;
        const value = Number(raw);
        if (Number.isNaN(value)) throw new Error(`${key} 必须是数字`);
        patch[key] = key === "temperature" || key === "timeout_seconds" ? value : Math.trunc(value);
      });
      if (caps.temperature?.effective !== false) {
        const raw = $("providerTemperatureField").value.trim();
        if (raw) {
          const value = Number(raw);
          if (Number.isNaN(value)) throw new Error("temperature 必须是数字");
          patch.temperature = value;
        }
      }
      return patch;
    }

    function buildProviderBusinessPatchFromForm() {
      const patch = {};
      const caps = currentProviderCapabilities();
      if (caps.reasoning?.effective !== false) {
        const reasoning = $("providerReasoningField").value.trim();
        if (reasoning) patch.reasoning = reasoning;
      }
      const thinkingType = $("providerThinkingTypeField").value.trim();
      if (caps.thinking?.effective !== false) patch.thinking = { type: thinkingType || "disabled" };
      if (caps.temperature?.effective !== false) {
        const raw = $("providerTemperatureField").value.trim();
        if (raw) {
          const value = Number(raw);
          if (Number.isNaN(value)) throw new Error("temperature 必须是数字");
          patch.temperature = value;
        }
      }
      return patch;
    }

    function providerBusinessPatchFromEditorAndForm() {
      const raw = $("providerFieldEditor").value.trim();
      let advanced = {};
      if (raw) {
        advanced = JSON.parse(raw);
        if (!advanced || typeof advanced !== "object" || Array.isArray(advanced)) {
          throw new Error("高级 JSON 必须是对象");
        }
      }
      const patch = { ...advanced, ...buildProviderBusinessPatchFromForm() };
      return providerBusinessPatchAllowedByCapabilities(patch, currentProviderCapabilities());
    }

    function providerPatchFromEditorAndForm() {
      const raw = $("providerFieldEditor").value.trim();
      let advanced = {};
      if (raw) {
        advanced = JSON.parse(raw);
        if (!advanced || typeof advanced !== "object" || Array.isArray(advanced)) {
          throw new Error("高级 JSON 必须是对象");
        }
      }
      const patch = { ...advanced, ...buildProviderPatchFromForm() };
      validateProviderJsonResponseFormat(patch);
      return patch;
    }

    function syncProviderFormToAdvancedJson() {
      try {
        const raw = $("providerFieldEditor").value.trim();
        const advanced = raw ? JSON.parse(raw) : {};
        if (!advanced || typeof advanced !== "object" || Array.isArray(advanced)) return;
        const inheritsDefault = $("providerAgentSelect").value !== "default" && $("providerInheritDefaultField").checked;
        const patch = inheritsDefault
          ? { ...advanced, ...buildProviderBusinessPatchFromForm() }
          : { ...advanced, ...buildProviderPatchFromForm() };
        const allowed = inheritsDefault
          ? providerBusinessPatchAllowedByCapabilities(patch, currentProviderCapabilities())
          : providerPatchAllowedByCapabilities(patch, currentProviderCapabilities());
        $("providerFieldEditor").value = JSON.stringify(allowed, null, 2);
      } catch {
        // Keep the user's JSON untouched until save surfaces the parse error.
      }
    }

    function refreshProviderCapabilityState() {
      const inheritsDefault = $("providerAgentSelect").value !== "default" && $("providerInheritDefaultField").checked;
      applyProviderCapabilityState(inheritsDefault);
      syncProviderFormToAdvancedJson();
      renderProviderEffectivePanel();
    }

    async function saveProviderConfig() {
      try {
        const agentName = $("providerAgentSelect").value;
        const payload = {
          path: projectPath(),
          agents: {},
        };
        if (agentName === "default") {
          payload.default = providerPatchFromEditorAndForm();
        } else if ($("providerInheritDefaultField").checked) {
          const patch = providerBusinessPatchFromEditorAndForm();
          patch.inherit_default = true;
          payload.agents = { [agentName]: patch };
        } else {
          const patch = providerPatchFromEditorAndForm();
          patch.inherit_default = false;
          payload.agents = { [agentName]: patch };
        }
        const data = await apiPost("/api/provider-config", payload);
        $("providerConfigPanel").textContent = JSON.stringify(data, null, 2);
        await loadProviderConfig();
        setMessage("Agent 模型配置已保存并备份");
      } catch (error) {
        setMessage(error.message, true);
      }
    }

    function renderProviderEffectivePanel() {
      const name = $("providerAgentSelect").value;
      const effective = providerEffectiveCache?.[name] || {};
      const uiInheritsDefault = name !== "default" && $("providerInheritDefaultField").checked;
      const config = uiInheritsDefault
        ? providerAgentDisplayConfig(name, true)
        : effective.config || providerAgentDisplayConfig(name, false) || {};
      const source = effective.source_label || effective.source || (uiInheritsDefault ? "default + agent behavior" : "unresolved");
      const overrideFields = effective.override_fields || [];
      const caps = currentProviderCapabilities();
      let inheritance = "未解析";
      if (name === "default" && source === "default") {
        inheritance = "默认配置";
      } else if (uiInheritsDefault || effective.inherit_default || effective.inherits_default) {
        inheritance = "继承 default 调用参数";
      } else if (effective.has_override) {
        inheritance = source === "default + agent override" ? "default + agent override" : "Agent 覆盖配置";
      } else if (source === "default") {
        inheritance = "继承 default 调用参数";
      }
      const rows = [
        ["来源", source],
        ["继承状态", inheritance],
        ["覆盖字段", overrideFields.length ? overrideFields.join(", ") : "无"],
        ["provider", config.provider],
        ["model", config.model],
        ["base_url_env", config.base_url_env],
        ["api_key_env", config.api_key_env],
        ["thinking", config.thinking?.type],
        ["reasoning", config.reasoning],
        ["temperature", config.temperature],
        ["max_tokens", config.max_tokens],
        ["max_context_tokens", config.max_context_tokens],
        ["timeout_seconds", config.timeout_seconds],
        ["max_retries", config.max_retries],
        ["json_response_format", config.json_response_format],
      ];
      const body = rows.map(([label, value]) => {
        const field = label === "thinking" ? "thinking" : label;
        const capability = caps[field];
        const display = capability?.effective === false
          ? `NA（${capability.reason || "当前 provider 不发送此参数"}）`
          : value ?? "未设置";
        return `
          <div class="provider-effective-row">
            <b>${escapeHtml(label)}</b>
            <span>${escapeHtml(display)}</span>
          </div>
        `;
      }).join("");
      const errors = [
        ...(providerConfigBackendMismatch ? [providerConfigBackendMismatch] : []),
        ...(effective.error ? [effective.error] : []),
      ];
      const error = errors.map((item) => `<div class="message error">${escapeHtml(item)}</div>`).join("");
      $("providerEffectivePanel").innerHTML = `${error}<div class="provider-effective-list">${body}</div>`;
    }

    async function loadStateTimeline() {
      try {
        const data = await apiGet("/api/state-timeline", { path: projectPath() });
        const visual = data.visual || {};
        const events = visual.timeline_events || [];
        const eventRows = events.map((event) => `
          <tr><td>${escapeHtml(event.id || "")}</td><td>${escapeHtml(event.chapter_label || event.chapter || "")}</td><td>${escapeHtml(event.summary || "")}</td><td>${escapeHtml(event.location_name || event.location_id || "")}</td><td>${escapeHtml((event.participant_names || []).join(", "))}</td></tr>
        `).join("");
        const itemRows = (visual.items || []).map((item) => `
          <tr><td>${escapeHtml(item.name || item.id || "")}</td><td>${escapeHtml(item.holder_name || item.holder_id || "")}</td><td>${escapeHtml(item.location_name || item.location_id || "")}</td><td>${escapeHtml(item.condition || "")}</td></tr>
        `).join("");
        const characterRows = (visual.characters || []).map((character) => `
          <tr><td>${escapeHtml(character.name || character.id || "")}</td><td>${escapeHtml(character.location_name || character.location_id || "")}</td><td>${escapeHtml(character.health || "")}</td><td>${escapeHtml((character.possessions || []).join(", "))}</td></tr>
        `).join("");
        const chapterCards = Object.entries(visual.timeline_by_chapter || {}).map(([chapter, chapterEvents]) => {
          const label = chapterEvents[0]?.chapter_label || (chapter === "background" ? "背景（未揭示）" : `第 ${chapter} 章`);
          return `
          <div class="timeline-card"><b>${escapeHtml(label)}</b>${chapterEvents.map((event) => `<div>${escapeHtml(event.id || "")}: ${escapeHtml(event.summary || "")}</div>`).join("")}</div>
        `;
        }).join("");
        const conflicts = (visual.conflicts || []).map((item) => `<li>${escapeHtml(item)}</li>`).join("");
        $("stateTimelinePanel").innerHTML = `
          <pre>${escapeHtml(JSON.stringify(data.summary || {}, null, 2))}</pre>
          <h3 style="margin-top: 16px;">Timeline by chapter</h3>
          <div class="timeline-lane">${chapterCards || "<span>无</span>"}</div>
          <h3 style="margin-top: 16px;">Characters</h3>
          <table><thead><tr><th>角色</th><th>地点</th><th>状态</th><th>持有物</th></tr></thead><tbody>${characterRows || "<tr><td colspan='4'>无</td></tr>"}</tbody></table>
          <h3 style="margin-top: 16px;">Items</h3>
          <table><thead><tr><th>物品</th><th>持有人</th><th>地点</th><th>状态</th></tr></thead><tbody>${itemRows || "<tr><td colspan='4'>无</td></tr>"}</tbody></table>
          <h3 style="margin-top: 16px;">Timeline</h3>
          <table><thead><tr><th>id</th><th>chapter</th><th>summary</th><th>location</th><th>participants</th></tr></thead><tbody>${eventRows || "<tr><td colspan='5'>无</td></tr>"}</tbody></table>
          <h3 style="margin-top: 16px;">Conflicts</h3>
          <ul>${conflicts || "<li>无</li>"}</ul>
        `;
      } catch (error) {
        setMessage(error.message, true);
      }
    }

    async function loadDiff() {
      try {
        const data = await apiGet("/api/diff", {
          path: projectPath(),
          left: $("diffLeft").value.trim(),
          right: $("diffRight").value.trim(),
        });
        $("diffViewer").textContent = data.diff || "两个文件没有差异。";
      } catch (error) {
        setMessage(error.message, true);
      }
    }

    function showTab(tabId) {
      document.querySelectorAll(".tabPanel").forEach((panel) => panel.classList.add("hidden"));
      document.querySelectorAll(".tab").forEach((button) => button.classList.remove("active"));
      const panel = $(tabId);
      const button = document.querySelector(`[data-tab="${tabId}"]`);
      if (!panel || !button) return;
      panel.classList.remove("hidden");
      button.classList.add("active");
      if (tabId === "runLogs") loadRuns();
      if (tabId === "usageStats") loadUsage();
      if (tabId === "chapterEditor" && !$("chapterEditorText").value) loadEditorFile();
      if (tabId === "auditLocate") loadAuditAnnotations();
    }

    function escapeHtml(value) {
      return String(value).replace(/[&<>"']/g, (char) => ({
        "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
      }[char]));
    }

    function escapeAttr(value) {
      return escapeHtml(value).replace(/`/g, "&#96;");
    }

    function formatDateTime(value) {
      if (!value) return "";
      const date = new Date(value);
      if (Number.isNaN(date.getTime())) return String(value);
      return date.toLocaleString();
    }

    function safeHighlightedExcerpt(value) {
      return escapeHtml(value)
        .replace(/&lt;mark&gt;/g, "<mark>")
        .replace(/&lt;\/mark&gt;/g, "</mark>");
    }

    function formatElapsed(milliseconds) {
      const totalSeconds = Math.max(0, Math.floor(milliseconds / 1000));
      const minutes = Math.floor(totalSeconds / 60);
      const seconds = totalSeconds % 60;
      return minutes ? `${minutes}分${String(seconds).padStart(2, "0")}秒` : `${seconds}秒`;
    }

    function formatElapsedBetween(startedAt, endedAt) {
      if (!startedAt) return "未开始";
      const start = new Date(startedAt).getTime();
      const end = endedAt ? new Date(endedAt).getTime() : Date.now();
      if (!Number.isFinite(start) || !Number.isFinite(end)) return "未知";
      return formatElapsed(end - start);
    }

    function setBusyMessage() {
      if (!currentBusyStartedAt || !currentBusyLabel) return;
      setBusyBanner(`${currentBusyLabel}执行中，已用时 ${formatElapsed(Date.now() - currentBusyStartedAt)}`);
    }

    function canUseButtonDuringBusy(button) {
      const allowedIds = new Set([
        "cancelSessionTask",
        "openProject",
        "refreshProject",
        "refreshProjectFiles",
        "loadStateTimeline",
        "loadProviderConfig",
        "loadRuns",
        "loadUsage",
        "searchProjectContent",
        "refreshFtsIndex",
        "refreshEmbeddingIndex",
      ]);
      return Boolean(button.id && allowedIds.has(button.id));
    }

    function addRecentOperation(label, status, detail = "") {
      recentOperations = [
        {
          label,
          status,
          detail,
          ended_at: new Date().toISOString(),
        },
        ...recentOperations,
      ].slice(0, 6);
      renderRecentOperations();
    }

    function renderRecentOperations() {
      const panel = $("recentOperationsPanel");
      if (!panel) return;
      if (!recentOperations.length) {
        panel.textContent = "最近操作：暂无";
        return;
      }
      panel.innerHTML = `
        <b>最近操作</b>
        ${recentOperations.map((item) => `
          <div style="margin-top: 6px;">
            <span>${escapeHtml(item.label)}：${escapeHtml(item.status)}</span>
            ${item.detail ? `<div>${escapeHtml(summarizedMessage(item.detail, "已省略").text)}</div>` : ""}
          </div>
        `).join("")}
      `;
    }

    async function withBusy(label, fn) {
      const buttons = Array.from(document.querySelectorAll("button"));
      const previousStates = buttons.map((button) => [button, button.disabled]);
      buttons.forEach((button) => {
        if (!canUseButtonDuringBusy(button)) button.disabled = true;
      });
      currentBusyStartedAt = Date.now();
      currentBusyLabel = label;
      setBusyMessage();
      sessionProgressTimer = window.setInterval(setBusyMessage, 1000);
      try {
        const result = await fn();
        addRecentOperation(label, "完成");
        return result;
      } catch (error) {
        addRecentOperation(label, "失败", error.message);
        setMessage(error.message, true, error.detailText || "");
      } finally {
        if (sessionProgressTimer) window.clearInterval(sessionProgressTimer);
        sessionProgressTimer = null;
        currentBusyStartedAt = null;
        currentBusyLabel = "";
        setBusyBanner("");
        previousStates.forEach(([button, disabled]) => {
          if (button.id !== "messageDetails") button.disabled = disabled;
        });
        syncMessageDetailsButton();
      }
    }

    function actionMessage(label, data) {
      const parts = [`${label}完成`];
      if (data.message) parts.push(data.message);
      if (data.session) {
        parts.push(`session=${data.session.session_id}`);
        parts.push(`status=${data.session.status}/${data.session.content_status}`);
      }
      if (Array.isArray(data.audit_summary)) {
        const blocking = data.audit_summary.reduce((count, item) => count + (item.blocking_issue_count || 0), 0);
        if (blocking) parts.push(`blocking issues=${blocking}`);
      }
      if (Array.isArray(data.rewrite_events) && data.rewrite_events.length) {
        parts.push(`auto rewrites=${data.rewrite_events.length}`);
      }
      if (Array.isArray(data.management_events) && data.management_events.length) {
        parts.push(`management events=${data.management_events.length}`);
      }
      const usageText = apiCallUsageText(data.api_call_usage);
      if (usageText) parts.push(usageText);
      if (data.proposal?.repair_id) parts.push(`repair=${data.proposal.repair_id}`);
      if (data.overall_status) parts.push(`audit=${data.overall_status}`);
      if (Array.isArray(data.warnings) && data.warnings.length) {
        parts.push(`warnings=${data.warnings.length}`);
      }
      return parts.join("；");
    }

    function apiCallUsageText(usage) {
      if (!usage || !usage.call_count) return "";
      const parts = [`messages chars=${usage.messages_char_count ?? 0}`];
      const prompt = usage.prompt_tokens ?? 0;
      const completion = usage.completion_tokens ?? 0;
      const total = usage.total_tokens ?? 0;
      const unknown = usage.unknown_token_call_count ?? 0;
      if (prompt || completion || total || unknown < usage.call_count) {
        parts.push(`tokens prompt=${prompt} completion=${completion} total=${total}`);
      }
      if (unknown) parts.push(`unknown token calls=${unknown}`);
      return parts.join("；");
    }

    function renderSessionSummary(data) {
      const session = data.session || {};
      if (!session.session_id) {
        $("sessionPanel").textContent = "未加载 Session";
        renderRewriteEvents([]);
        return;
      }
      $("sessionPanel").innerHTML = `
        <span>Session</span>
        <b>${escapeHtml(session.session_id)}</b>
        <div>status: ${escapeHtml(session.status || "")}</div>
        <div>outline: ${escapeHtml(session.outline_status || "")}</div>
        <div>content: ${escapeHtml(session.content_status || "")}</div>
        <div>chapters: ${escapeHtml((session.chapter_range || []).join(", "))}</div>
        <div>${escapeHtml(data.message || "")}</div>
        ${renderRevisionRouteSummary(data.revision_route || (session.revision_route_history || []).slice(-1)[0])}
        ${renderSessionAuditSummary(data.audit_summary || [])}
      `;
      if (data.progress) renderSessionProgress(data.progress);
      renderRewriteEvents(data.rewrite_events || []);
      renderManagementEvents(data.management_events || []);
    }

    async function loadSessionProgress(options = {}) {
      const sessionId = $("sessionId").value.trim();
      if (!sessionId) {
        renderSessionProgress({ status: "idle" });
        return null;
      }
      try {
        const data = await apiGet("/api/session/progress", { path: projectPath(), session_id: sessionId });
        renderSessionProgress(data.progress || { status: "idle" });
        return data.progress || null;
      } catch (error) {
        if (!options.quiet) setMessage(error.message, true);
        return null;
      }
    }

    function startSessionProgressPolling() {
      stopSessionProgressPolling();
      const poll = async () => {
        const progress = await loadSessionProgress({ quiet: true });
        if (progress && ["cancelled", "completed", "failed"].includes(progress.status)) {
          stopSessionProgressPolling();
        }
      };
      poll();
      sessionProgressPoller = window.setInterval(poll, 1500);
      return sessionProgressPoller;
    }

    function stopSessionProgressPolling() {
      if (sessionProgressPoller) window.clearInterval(sessionProgressPoller);
      sessionProgressPoller = null;
    }

    async function cancelSessionTask() {
      const sessionId = $("sessionId").value.trim();
      if (!sessionId) {
        setMessage("请先选择或创建 Session。", true);
        return;
      }
      try {
        const data = await apiPost("/api/session/cancel", { path: projectPath(), session_id: sessionId });
        renderSessionProgress(data.progress || { status: "cancel_requested" });
        addRecentOperation("取消当前 Session 任务", "已请求", data.message || "");
        setMessage("取消已请求，会在当前章节或修复轮结束后生效。");
      } catch (error) {
        addRecentOperation("取消当前 Session 任务", "失败", error.message);
        setMessage(error.message, true);
      }
    }

    function renderSessionProgress(progress = {}) {
      const panel = $("sessionProgressPanel");
      const cancelButton = $("cancelSessionTask");
      if (!panel || !cancelButton) return;
      const status = progress.status || "idle";
      if (status === "idle" && !(progress.events || []).length) {
        panel.className = "metric";
        panel.textContent = "当前任务进度：暂无";
        cancelButton.classList.add("hidden");
        cancelButton.disabled = false;
        cancelButton.textContent = "取消当前 Session 任务";
        return;
      }
      const statusLabels = {
        idle: "空闲",
        running: "运行中",
        cancel_requested: "取消已请求",
        cancelled: "已取消",
        completed: "已完成",
        failed: "失败",
      };
      const stateClass = status === "failed" ? "status-bad" : (
        status === "cancel_requested" || status === "cancelled" ? "status-warn" : (
          status === "completed" ? "status-ok" : ""
        )
      );
      const events = (progress.events || []).slice(-5).reverse();
      const elapsed = formatElapsedBetween(progress.started_at, progress.completed_at || (status === "running" || status === "cancel_requested" ? null : progress.updated_at));
      panel.className = `metric ${stateClass}`;
      panel.innerHTML = `
        <b>当前任务进度：${escapeHtml(statusLabels[status] || status)}</b>
        <div>阶段：${escapeHtml(progress.current_stage || "未开始")}</div>
        <div>说明：${escapeHtml(progress.current_message || "暂无")}</div>
        <div>章节：${escapeHtml(progress.current_chapter || "未设置")}；轮次：${escapeHtml(progress.current_round ?? "未设置")}；已用时：${escapeHtml(elapsed)}</div>
        ${progress.error ? `<div class="status-bad">错误：${escapeHtml(progress.error)}</div>` : ""}
        ${events.length ? `
          <div style="margin-top: 8px;"><b style="font-size: 13px;">最近事件</b></div>
          ${events.map((event) => `
            <div style="margin-top: 4px;">
              ${escapeHtml(event.stage || "")}：${escapeHtml(event.message || "")}
            </div>
          `).join("")}
        ` : ""}
      `;
      const canCancel = status === "running";
      cancelButton.classList.toggle("hidden", !["running", "cancel_requested"].includes(status));
      cancelButton.disabled = status === "cancel_requested";
      cancelButton.textContent = status === "cancel_requested"
        ? "取消已请求，等待安全边界"
        : "取消当前 Session 任务";
      cancelButton.title = "取消会在当前章节或修复轮结束后生效，不会强行中断正在进行的 LLM HTTP 调用。";
      if (!canCancel && status !== "cancel_requested") cancelButton.disabled = false;
    }

    function renderRevisionRouteSummary(record) {
      if (!record) return "";
      const decision = record.decision || record;
      if (!decision.route) return "";
      const labelMap = {
        plot_replan: "重写大纲",
        writer_rewrite: "重写正文",
        revision_patch: "局部修订",
      };
      return `
        <div class="metric status-warn" style="margin-top: 8px;">
          <b>本次修改路由：${escapeHtml(labelMap[decision.route] || decision.route)}</b>
          <div>原因：${escapeHtml(decision.reason || "")}</div>
          <div>风险：${escapeHtml(decision.risk_level || "")}</div>
        </div>
      `;
    }

    function startRewriteEventPolling() {
      const sessionId = $("sessionId").value.trim();
      if (!sessionId) return null;
      const poll = async () => {
        try {
          const data = await apiGet("/api/session/rewrite-events", { path: projectPath(), session_id: sessionId });
          renderRewriteEvents(data.rewrite_events || []);
        } catch (error) {
          // Keep long-running session actions quiet; the final request will surface blocking errors.
        }
      };
      poll();
      return window.setInterval(poll, 3000);
    }

    function renderNextStep(data = {}) {
      const session = data.session || {};
      const validation = data.validation || null;
      const status = data.status || {};
      let text = "下一步：打开或初始化一个小说项目。";
      if (validation) {
        if ((validation.error_count || 0) > 0) {
          text = `下一步：项目检查发现 ${validation.error_count} 个错误，请先查看“运行日志 / 项目文件”的详情并修复。`;
        } else if ((validation.warning_count || 0) > 0) {
          text = `下一步：项目检查通过，但有 ${validation.warning_count} 个警告。长篇创作前建议查看并处理。`;
        } else {
          text = "下一步：项目检查通过。可以继续创建 Session、写作或导出。";
        }
      } else if (session.session_id) {
        if (session.status === "outline_proposed" || session.outline_status === "proposed") {
          text = "下一步：查看大纲。不满意就在聊天 / 指令框写修改意见并点击“修改大纲”；满意后点击“批准大纲”。";
        } else if (session.status === "outline_approved" || session.outline_status === "approved" && session.content_status === "not_started") {
          text = "下一步：点击“开始写作”，系统会自动完成写作、润色、审核和状态更新 proposal。";
        } else if (session.content_status === "needs_revision" || session.status === "needs_revision") {
          text = "下一步：查看 Audit 摘要。硬伤优先点击“按 Audit 修订内容”；主观意见写入聊天 / 指令框后点击“按用户意见修订内容”。";
        } else if (session.content_status === "needs_user_review" || session.status === "needs_user_review") {
          text = "下一步：查看 polished/audit。满意后点击“认可本次创作”，再点击“归档”。";
        } else if (session.status === "accepted") {
          text = "下一步：点击“归档”冻结本次创作；之后可以导出 Markdown。";
        } else if (session.status === "archived") {
          text = "下一步：本次创作已归档。可以继续新建 Session 或导出 Markdown。";
        }
      } else if (status.title) {
        if (!status.inspiration_exists) {
          text = "下一步：在聊天 / 指令框输入故事灵感，点击“生成灵感”。";
        } else if ((status.character_count || 0) === 0 && (status.location_count || 0) === 0) {
          text = "下一步：点击“Canon 建议”，确认 proposal 后点击“应用 Canon proposal”。";
        } else {
          text = "下一步：填写章节范围和创作意图，点击“创建大纲”开始一次 Session。";
        }
      }
      syncProjectPrepDetails(data);
      $("nextStepPanel").textContent = text;
      $("workbenchNextStepPanel").textContent = text;
    }

    function renderValidationStatus(validation) {
      const errorCount = validation.error_count || 0;
      const warningCount = validation.warning_count || 0;
      const panel = $("validationStatusPanel");
      const stateClass = errorCount > 0 ? "status-bad" : (warningCount > 0 ? "status-warn" : "status-ok");
      const details = [...(validation.errors || []), ...(validation.warnings || [])].slice(0, 5);
      const detailHtml = details.length
        ? details.map((item) => `
          <div style="margin-top: 4px;">
            <b>${escapeHtml(item.level || "")}</b>
            ${escapeHtml(item.path || "")}: ${escapeHtml(item.message || "")}
          </div>
        `).join("")
        : "<div>未发现错误或警告。</div>";
      panel.className = `metric ${stateClass}`;
      panel.innerHTML = `
        <b>项目检查结果：${errorCount} 个错误，${warningCount} 个警告</b>
        ${detailHtml}
        ${details.length < ((validation.errors || []).length + (validation.warnings || []).length) ? "<div>更多详情见“运行日志 / 项目文件”的“章节文件查看”。</div>" : ""}
      `;
      $("currentValidationSummary").textContent = `项目检查：${errorCount} 个错误，${warningCount} 个警告`;
    }

    function renderSessionAuditSummary(auditSummary) {
      if (!auditSummary.length) return "<div style=\"margin-top: 8px;\">audit: 未生成</div>";
      return auditSummary.map((item) => {
        const issues = item.issues || [];
        const issueRows = issues.length
          ? issues.map((issue) => `
              <div style="margin-top: 4px;">
                <b>${escapeHtml(issue.id || "")}</b>
                [${escapeHtml(issue.severity || "")}/${escapeHtml(issue.type || "")}]
                ${escapeHtml(issue.description || "")}
                ${issue.suggested_fix ? `<div>fix: ${escapeHtml(issue.suggested_fix)}</div>` : ""}
              </div>
            `).join("")
          : "<div>无 issue</div>";
        return `
          <div style="margin-top: 8px;">
            <b>第 ${escapeHtml(item.chapter_number)} 章 audit</b>:
            ${escapeHtml(item.overall_status || (item.exists ? "读取失败" : "未生成"))}
            ，blocking=${escapeHtml(item.blocking_issue_count || 0)}
            ${item.error ? `<div class="message error">${escapeHtml(item.error)}</div>` : ""}
            <div>${issueRows}</div>
          </div>
        `;
      }).join("");
    }

    function renderRewriteEvents(events) {
      const panel = $("rewriteEventsPanel");
      if (!events.length) {
        panel.innerHTML = "自动打回重写记录：暂无";
        return;
      }
      panel.innerHTML = `
        <b>自动打回重写记录</b>
        ${events.map((event) => {
          const actionText = event.action === "plot_replan" ? "重写大纲" : "修正文";
          const issueRows = (event.blocking_issues || []).map((issue) => `
            <div style="margin-top: 4px;">
              <b>${escapeHtml(issue.id || "")}</b>
              [${escapeHtml(issue.severity || "")}/${escapeHtml(issue.type || "")}]
              ${escapeHtml(issue.description || "")}
              ${issue.suggested_fix ? `<div>fix: ${escapeHtml(issue.suggested_fix)}</div>` : ""}
            </div>
          `).join("") || "<div>无阻断 issue</div>";
          const snapshotButton = event.rejected_text_snapshot_path
            ? `<button class="view-rejected-text" data-path="${escapeAttr(event.rejected_text_snapshot_path)}">查看被打回原文</button>`
            : "";
          const selectButton = `<button class="select-rewrite-event" data-event-id="${escapeAttr(event.event_id)}">选择该打回记录</button>`;
          const auditRevisions = (event.audit_revision_history || []).length
            ? `<div>audit 复审次数：${escapeHtml((event.audit_revision_history || []).length)}</div>`
            : "";
          return `
            <div style="margin-top: 8px;">
              <div>第 ${escapeHtml(event.chapter_number)} 章第 ${escapeHtml(event.round_number)} 轮被 Audit 打回：${escapeHtml(actionText)}，status=${escapeHtml(event.status || "")}</div>
              <div>event: ${escapeHtml(event.event_id || "")}</div>
              <div>undo: ${escapeHtml(event.undo_status || "not_requested")}</div>
              <div>audit: ${escapeHtml(event.trigger_audit_path || "")}</div>
              ${selectButton} ${snapshotButton}
              ${auditRevisions}
              <div>${issueRows}</div>
            </div>
          `;
        }).join("")}
      `;
      document.querySelectorAll(".select-rewrite-event").forEach((button) => {
        button.addEventListener("click", () => {
          $("rewriteEventId").value = button.dataset.eventId || "";
          setMessage(`已选择打回记录：${button.dataset.eventId || ""}`);
        });
      });
      document.querySelectorAll(".view-rejected-text").forEach((button) => {
        button.addEventListener("click", () => loadRejectedText(button.dataset.path));
      });
    }

    async function loadRejectedText(relPath) {
      if (!relPath) return;
      try {
        const data = await apiGet("/api/read-file", { path: projectPath(), file: relPath });
        $("rejectedTextViewer").value = data.content || "";
        setMessage(`已读取被打回原文：${data.path}`);
      } catch (error) {
        setMessage(error.message, true);
      }
    }

    $("openProject").addEventListener("click", openProject);
    $("messageDetails").addEventListener("click", openLatestMessageDetails);
    $("refreshProject").addEventListener("click", refreshAll);
    $("refreshProjectFiles").addEventListener("click", refreshAll);
    $("validateProject").addEventListener("click", validateProject);
    $("toggleProjectInit").addEventListener("click", toggleProjectInit);
    $("initProject").addEventListener("click", initProject);
    $("setupDefaultProvider").addEventListener("click", setupDefaultProvider);
    $("setupSkipProvider").addEventListener("click", setupSkipProvider);
    $("setupEmbedding").addEventListener("click", setupEmbedding);
    $("setupRecommendPort").addEventListener("click", recommendSetupPort);
    $("setupSavePort").addEventListener("click", setupSavePort);
    $("planChapter").addEventListener("click", () => runAction("/api/plan-chapter", "生成计划"));
    $("writeChapter").addEventListener("click", () => runAction("/api/write-chapter", "写章节"));
    $("polishChapter").addEventListener("click", () => runAction("/api/polish-chapter", "润色"));
    $("auditChapter").addEventListener("click", () => runAction("/api/audit-chapter", "审核"));
    $("inspireProject").addEventListener("click", inspireProject);
    $("regenerateInspiration").addEventListener("click", regenerateInspiration);
    $("openInspirationFile").addEventListener("click", () => readWorkspaceFile(inspirationPreviewPath));
    $("canonSuggest").addEventListener("click", canonSuggest);
    $("canonApply").addEventListener("click", canonApply);
    $("viewLatestCanonProposal").addEventListener("click", () => {
      if (!latestCanonProposalSnapshotPath) {
        setMessage("暂无可查看的 Canon proposal 快照。", true);
        return;
      }
      readWorkspaceFile(latestCanonProposalSnapshotPath);
    });
    $("refreshFtsIndex").addEventListener("click", () => refreshIndex(false));
    $("refreshEmbeddingIndex").addEventListener("click", () => refreshIndex(true));
    $("saveEmbeddingConfig").addEventListener("click", saveEmbeddingConfig);
    $("setupEmbeddingProvider").addEventListener("change", () => applyEmbeddingProviderDefaults("setup", true));
    $("configEmbeddingProvider").addEventListener("change", () => applyEmbeddingProviderDefaults("config", true));
    $("memoryRepairSuggest").addEventListener("click", memoryRepairSuggest);
    $("memoryRepairClarificationSubmit").addEventListener("click", memoryRepairAnswer);
    $("memoryRepairReset").addEventListener("click", () => resetSettingChangeState("memoryRepairInstruction"));
    $("openMemoryRepairProposal").addEventListener("click", () => openSettingChangeProposalFile("memory"));
    $("memoryRepairApply").addEventListener("click", memoryRepairApply);
    $("settingChangeWorkbenchSuggest").addEventListener("click", () => settingChangeSuggest(currentSettingChangeStage()));
    $("settingChangeClarificationSubmit").addEventListener("click", () => settingChangeAnswer());
    $("settingChangeWorkbenchReset").addEventListener("click", () => resetSettingChangeState("instruction"));
    $("openWorkbenchSettingChangeProposal").addEventListener("click", () => openSettingChangeProposalFile("workbench"));
    $("settingChangeWorkbenchApply").addEventListener("click", () => settingChangeApply({ syncSession: $("settingChangeSyncSession").checked }));
    $("rebuildChapterMemory").addEventListener("click", rebuildChapterMemory);
    $("exportMarkdown").addEventListener("click", () => runExport("/api/export/markdown", "导出 Markdown"));
    $("exportDocx").addEventListener("click", () => runExport("/api/export/docx", "导出 DOCX"));
    $("sessionStart").addEventListener("click", () => {
      $("sessionId").value = "";
      runSessionAction("/api/session/start", sessionPayload({ includeSessionId: false }), "创建 Session 大纲");
    });
    $("sessionReviseOutline").addEventListener("click", () => runSessionAction("/api/session/revise-outline", sessionPayload(), "修改 Session 大纲"));
    $("sessionApprove").addEventListener("click", () => runSessionAction("/api/session/approve-outline", sessionPayload(), "批准 Session 大纲"));
    $("sessionRun").addEventListener("click", () => runSessionAction("/api/session/run", sessionPayload(), "Session 写作"));
    $("sessionAccept").addEventListener("click", () => runSessionAction("/api/session/accept", sessionPayload(), "认可 Session"));
    $("sessionArchive").addEventListener("click", () => runSessionAction("/api/session/archive", sessionPayload(), "归档 Session"));
    $("sessionReviseAuditContent").addEventListener("click", () => runSessionAction("/api/session/revise-content", sessionPayload({ fromAudit: true }), "按 Audit 修订内容"));
    $("sessionReviseInstruction").addEventListener("click", () => runSessionAction("/api/session/revise-content", sessionPayload(), "按用户意见修订内容"));
    $("sessionReviseAudit").addEventListener("click", () => runSessionAction("/api/session/revise-audit", rewriteControlPayload(), "纠正 Audit 理解并重新审核"));
    $("sessionRetryRewrite").addEventListener("click", () => runSessionAction("/api/session/retry-rewrite", rewriteControlPayload(), "根据新审核重新打回"));
    $("sessionUndoRewrite").addEventListener("click", () => runSessionAction("/api/session/undo-rewrite", rewriteControlPayload(), "撤回本次打回"));
    $("cancelSessionTask").addEventListener("click", cancelSessionTask);
    $("viewFile").addEventListener("click", viewFile);
    $("loadCompare").addEventListener("click", loadCompare);
    $("loadEditorFile").addEventListener("click", loadEditorFile);
    $("saveEditorVersion").addEventListener("click", saveEditorVersion);
    $("chapterEditorText").addEventListener("input", () => {
      $("editorDirty").textContent = $("chapterEditorText").value === editorLoadedContent ? "未修改" : "有未保存修改";
    });
    window.addEventListener("beforeunload", (event) => {
      if (editorSourceFile && $("chapterEditorText").value !== editorLoadedContent) {
        event.preventDefault();
        event.returnValue = "";
      }
    });
    document.addEventListener("keydown", (event) => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "s" && !$("chapterEditor").classList.contains("hidden")) {
        event.preventDefault();
        saveEditorVersion();
      }
    });
    $("loadAuditAnnotations").addEventListener("click", loadAuditAnnotations);
    $("auditIssueAsSettingChange").addEventListener("click", auditIssueAsSettingChange);
    $("loadRuns").addEventListener("click", loadRuns);
    $("searchProjectContent").addEventListener("click", searchProjectContent);
    $("searchQuery").addEventListener("keydown", (event) => {
      if (event.key === "Enter") searchProjectContent();
    });
    $("loadUsage").addEventListener("click", loadUsage);
    $("loadProviderConfig").addEventListener("click", loadProviderConfig);
    $("providerAgentSelect").addEventListener("change", renderProviderAgentFields);
    $("providerInheritDefaultField").addEventListener("change", toggleProviderDefaultInheritance);
    providerFormFieldIds.forEach((id) => {
      const handler = id === "providerProviderField" || id === "providerThinkingTypeField"
        ? refreshProviderCapabilityState
        : syncProviderFormToAdvancedJson;
      $(id).addEventListener("input", handler);
      $(id).addEventListener("change", handler);
    });
    $("saveProviderConfig").addEventListener("click", saveProviderConfig);
    $("loadStateTimeline").addEventListener("click", loadStateTimeline);
    $("loadDiff").addEventListener("click", loadDiff);
    document.querySelectorAll(".nav-button").forEach((button) => {
      button.addEventListener("click", () => showMainPage(button.dataset.page));
    });
    $("goWorkbench").addEventListener("click", () => showMainPage("workbenchPage"));
    $("homeGoWorkbench").addEventListener("click", () => showMainPage("workbenchPage"));
    $("goMemoryPage").addEventListener("click", () => showMainPage("memoryPage"));
    $("homeGoConfig").addEventListener("click", () => showMainPage("configPage"));
    document.querySelectorAll(".tab").forEach((button) => {
      button.addEventListener("click", () => showTab(button.dataset.tab));
    });
    $("projectPath").value = localStorage.getItem("writeryang.projectPath") || "";
    $("projectPath").addEventListener("change", () => localStorage.setItem("writeryang.projectPath", $("projectPath").value));
    const savedProjectParentPath = localStorage.getItem("writeryang.projectParentPath");
    $("projectParentPath").dataset.usesRuntimeDefault = savedProjectParentPath ? "0" : "1";
    $("projectParentPath").value = savedProjectParentPath || defaultProjectParentPath;
    $("projectParentPath").addEventListener("input", () => {
      $("projectParentPath").dataset.usesRuntimeDefault = "0";
      localStorage.setItem("writeryang.projectParentPath", $("projectParentPath").value);
      updateProjectInitPathPreview();
    });
    $("projectParentPath").addEventListener("change", () => {
      $("projectParentPath").dataset.usesRuntimeDefault = "0";
      localStorage.setItem("writeryang.projectParentPath", $("projectParentPath").value);
      updateProjectInitPathPreview();
    });
    $("projectTitle").addEventListener("input", updateProjectInitPathPreview);
    $("projectTitle").addEventListener("change", updateProjectInitPathPreview);
    window.addEventListener("resize", syncWorkbenchStickyOffset);
    applyEmbeddingProviderDefaults("setup", false);
    applyEmbeddingProviderDefaults("config", false);
    updateProjectInitPathPreview();
    syncWorkbenchStickyOffset();
    loadRuntime();
