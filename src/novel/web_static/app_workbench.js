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
        await loadDebugArtifactPreview(endpoint, { silent: true });
        setMessage(actionMessage(label, data));
      });
    }

    async function runSessionAction(endpoint, payload, label) {
      if (!validateSessionAction(endpoint)) return;
      if (!(await prepareSessionRunAction(endpoint))) return;
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
          if (session.session_id) {
            $("sessionId").value = session.session_id;
            rememberSessionId(session.session_id);
          }
          if (data.progress) renderSessionProgress(data.progress);
          renderSessionSummary(data);
          $("fileViewer").textContent = JSON.stringify(data, null, 2);
          await refreshAll({ silent: true });
          renderSessionSummary(data);
          renderNextStep(data);
          await refreshSessionGeneratedPreview(endpoint, session);
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

    function validateSessionAction(endpoint) {
      if (sessionIdRequiredEndpoints.has(endpoint)) {
        const sessionId = $("sessionId").value.trim();
        if (!sessionId) {
          renderOutlinePreviewPlaceholder("请先创建或填写 Session ID。");
          setMessage("请先创建或填写 Session ID。", true);
          $("sessionId").focus();
          return false;
        }
      }
      if (rewriteEventRequiredEndpoints.has(endpoint)) {
        const eventId = $("rewriteEventId").value.trim();
        if (!eventId) {
          setMessage("请先填写 Rewrite Event ID。", true);
          $("rewriteEventId").focus();
          return false;
        }
      }
      return true;
    }

    async function prepareSessionRunAction(endpoint) {
      if (endpoint !== "/api/session/run") return true;
      const data = await loadCurrentSession({ silent: true });
      const session = data?.session || {};
      if (!session.session_id) return true;
      if (sessionNeedsOutlineApproval(session)) {
        await loadOutlinePreview(session, { silent: true });
        renderNextStep(data);
        setMessage("请先批准大纲，再开始写作。", true);
        return false;
      }
      if (sessionHasGeneratedContent(session)) {
        await showSessionGeneratedContentIfAvailable(session, { silent: true });
        renderNextStep(data);
        setMessage("当前 Session 已生成正文，请先查看草稿、按 Audit/用户意见修订，或认可/归档。", true);
        return false;
      }
      return true;
    }

    async function refreshSessionGeneratedPreview(endpoint, session) {
      if (outlinePreviewEndpoints.has(endpoint)) {
        await loadOutlinePreview(session, { silent: true });
      }
      if (chapterComparePreviewEndpoints.has(endpoint) || sessionHasGeneratedContent(session)) {
        await showSessionGeneratedContentIfAvailable(session, { silent: true, force: true });
      }
    }

    function sessionHasGeneratedContent(session = {}) {
      const generatedStatuses = new Set(["needs_revision", "needs_user_review", "accepted"]);
      return Boolean((session.final_output_paths || []).length)
        || generatedStatuses.has(session.status)
        || generatedStatuses.has(session.content_status);
    }

    function sessionNeedsOutlineApproval(session = {}) {
      return session.outline_status !== "approved"
        || session.status === "drafting_intent"
        || session.status === "outline_proposed";
    }

    async function showSessionGeneratedContentIfAvailable(session = {}, options = {}) {
      if (!options.force && !sessionHasGeneratedContent(session)) return false;
      showTab("chapterCompare");
      await loadCompare();
      focusChapterProse();
      return true;
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

    async function loadRevisionBlocks() {
      return withBusy("加载修订 blocks", async () => {
        const data = await apiGet("/api/revision-session/blocks", {
          path: projectPath(),
          chapter: chapterNumber(),
        });
        const blocks = data.blocks || [];
        $("revisionBlocksPreview").textContent = blocks.length
          ? blocks.map((block) => `${block.index}. [${block.kind}] ${block.preview}`).join("\n")
          : "当前 accepted 章节没有可修订 block。";
        $("fileViewer").textContent = JSON.stringify(data, null, 2);
        setMessage(`已加载第 ${data.chapter_number} 章的 ${blocks.length} 个 Markdown block。`);
        return data;
      });
    }

    function revisionPayload() {
      return {
        path: projectPath(),
        chapter: chapterNumber(),
        start_block: Number($("revisionStartBlock").value),
        end_block: Number($("revisionEndBlock").value),
        instruction: $("instruction").value.trim(),
        revision_session_id: $("revisionSessionId").value.trim(),
        provider: $("provider").value,
        use_search_context: $("useSearchContext").checked,
        vector_context: $("vectorContextMode").value,
        use_vector_context: $("useVectorContext").checked,
      };
    }

    function renderRevisionSession(data = {}) {
      const session = data.revision_session || {};
      if (!session.revision_session_id) return;
      $("revisionSessionId").value = session.revision_session_id;
      $("revisionSessionPanel").innerHTML = `
        <b>${escapeHtml(session.revision_session_id)}</b>
        <div>phase: ${escapeHtml(session.phase || "")}</div>
        <div>chapter: ${escapeHtml(session.chapter_number || "")}</div>
        <div>blocks: ${escapeHtml(`${session.selection?.start_block || ""}-${session.selection?.end_block || ""}`)}</div>
        <div>candidate: ${escapeHtml(session.candidate?.sha256 || "待生成")}</div>
      `;
    }

    async function runRevisionAction(endpoint, label) {
      const payload = revisionPayload();
      if (endpoint !== "/api/revision-session/start" && !payload.revision_session_id) {
        setMessage("请先创建或填写 Revision Session ID。", true);
        $("revisionSessionId").focus();
        return;
      }
      if (endpoint === "/api/revision-session/start" && !payload.instruction) {
        setMessage("请先在聊天 / 指令框填写局部修订要求。", true);
        $("instruction").focus();
        return;
      }
      return withBusy(label, async () => {
        const data = await apiPost(endpoint, payload);
        renderRevisionSession(data);
        $("fileViewer").textContent = JSON.stringify(data, null, 2);
        if (endpoint === "/api/revision-session/run" || endpoint === "/api/revision-session/accept") {
          await loadCompare();
        }
        await refreshAll({ silent: true });
        setMessage(actionMessage(label, data));
      });
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
        setMessage("默认 API 连通性测试通过，已保存为 default API；4 个 profile 默认继承该配置。");
      });
    }

    function setupSkipProvider() {
      setSetupStatus("已暂时跳过默认 API 配置。真实创作前需要配置默认 API，否则真实模型调用会失败。", true);
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

    function renderArtifactPreviewPlaceholder(metaId, preId, text, meta = "当前文件：暂无") {
      $(metaId).textContent = meta;
      $(preId).textContent = text;
    }

    function renderArtifactPreview(metaId, preId, data, relPath) {
      const path = data.path || data.relative_path || relPath;
      $(metaId).textContent = `当前文件：${path || "暂无"}`;
      $(preId).textContent = data.content || (path ? `${path} 为空。` : "文件为空。");
    }

    function renderArtifactPreviewError(metaId, preId, relPath, error, label = "文件") {
      $(metaId).textContent = relPath ? `当前文件：${relPath}` : "当前文件：暂无";
      $(preId).textContent = `无法读取${label}：${error.message}`;
    }

    async function loadWorkspaceArtifactPreview(relPath, target, options = {}) {
      if (!relPath) {
        renderArtifactPreviewPlaceholder(target.metaId, target.preId, target.emptyText || "暂无可预览文件。");
        if (!options.silent) setMessage(target.missingMessage || "暂无可预览文件。", true);
        return null;
      }
      try {
        const data = await apiGet("/api/read-file", { path: projectPath(), file: relPath });
        renderArtifactPreview(target.metaId, target.preId, data, relPath);
        if (!options.silent) setMessage(`已读取${target.label || "文件"}：${data.path || relPath}`);
        return data;
      } catch (error) {
        renderArtifactPreviewError(target.metaId, target.preId, relPath, error, target.label || "文件");
        if (!options.silent) setMessage(error.message, true);
        return null;
      }
    }

    async function loadChapterArtifactPreview(fileType, target, options = {}) {
      const chapter = options.chapter || chapterNumber();
      try {
        const data = await apiGet("/api/chapter-file", {
          path: projectPath(),
          chapter,
          file: fileType,
        });
        const relPath = data.relative_path || `memory/chapters/${String(chapter).padStart(3, "0")}/${fileType}`;
        renderArtifactPreview(target.metaId, target.preId, {
          ...data,
          content: data.exists ? data.content : `${relPath} 不存在`,
        }, relPath);
        if (!options.silent) setMessage(`已读取${target.label || "章节产物"}：${relPath}`);
        return data;
      } catch (error) {
        renderArtifactPreviewError(target.metaId, target.preId, "", error, target.label || "章节产物");
        if (!options.silent) setMessage(error.message, true);
        return null;
      }
    }

    function outlinePreviewFileFor(session = {}) {
      const sessionId = session.session_id || $("sessionId").value.trim();
      if (!sessionId) return "";
      const isApproved = session.outline_status === "approved"
        || session.status === "outline_approved"
        || Boolean(session.approved_outline_path);
      const fileName = isApproved ? "approved_outline.md" : "outline_proposal.md";
      return `memory/sessions/${sessionId}/${fileName}`;
    }

    function renderOutlinePreviewPlaceholder(text = "创建或加载 Session 后，大纲正文会显示在这里。") {
      renderArtifactPreviewPlaceholder("outlinePreviewMeta", "outlinePreview", text);
    }

    async function loadOutlinePreview(session = {}, options = {}) {
      const relPath = outlinePreviewFileFor(session);
      return loadWorkspaceArtifactPreview(relPath, {
        metaId: "outlinePreviewMeta",
        preId: "outlinePreview",
        label: "大纲",
        emptyText: "创建或加载 Session 后，大纲正文会显示在这里。",
        missingMessage: "请先创建或填写 Session ID。",
      }, options);
    }

    async function applyLoadedSessionData(data, options = {}) {
      const session = data?.session || {};
      if (!session.session_id) return null;
      $("sessionId").value = session.session_id;
      rememberSessionId(session.session_id);
      renderSessionSummary(data);
      renderNextStep(data);
      await loadOutlinePreview(session, { silent: true });
      await showSessionGeneratedContentIfAvailable(session, { silent: true });
      if (!options.silent && options.message) setMessage(options.message);
      return data;
    }

    async function loadCurrentSession(options = {}) {
      const sessionId = $("sessionId").value.trim();
      if (!sessionId) {
        renderOutlinePreviewPlaceholder();
        if (!options.silent) setMessage("请先创建或填写 Session ID。", true);
        return null;
      }
      try {
        const data = await apiGet("/api/session", { path: projectPath(), session_id: sessionId });
        return await applyLoadedSessionData(data, {
          ...options,
          message: `已加载 Session：${sessionId}`,
        });
      } catch (error) {
        $("outlinePreviewMeta").textContent = `Session：${sessionId}`;
        $("outlinePreview").textContent = `无法加载 Session：${error.message}`;
        if (!options.silent) setMessage(error.message, true);
        return null;
      }
    }

    async function restoreRecentSessionIfEmpty(options = {}) {
      if ($("sessionId").value.trim()) return null;
      const sessionId = recentSessionId();
      let storedData = null;
      let storedFailed = false;
      if (sessionId) {
        $("sessionId").value = sessionId;
        try {
          storedData = await apiGet("/api/session", { path: projectPath(), session_id: sessionId });
        } catch (error) {
          storedData = null;
        }
      }
      if (sessionId && !storedData) {
        storedFailed = true;
        localStorage.removeItem(recentSessionStorageKey());
        $("sessionId").value = "";
        renderOutlinePreviewPlaceholder();
      }
      let latestData = null;
      try {
        latestData = await apiGet("/api/session/latest", { path: projectPath() });
      } catch (error) {
        latestData = null;
      }
      if (
        latestData
        && (!storedData || (sessionHasGeneratedContent(latestData.session) && !sessionHasGeneratedContent(storedData.session)))
      ) {
        await applyLoadedSessionData(latestData, { silent: true });
        if (!options.silent) setMessage(`已恢复最近 Session：${latestData.session.session_id}`);
        return latestData;
      }
      if (storedData) {
        await applyLoadedSessionData(storedData, { silent: true });
        if (!options.silent) setMessage(`已恢复最近 Session：${storedData.session.session_id}`);
        return storedData;
      }
      if (storedFailed && !options.silent) setMessage("最近 Session 已失效，请重新创建或填写 Session ID。", true);
      return null;
    }

    async function loadInspirationPreview(options = {}) {
      return loadWorkspaceArtifactPreview(inspirationPreviewPath, {
        metaId: "inspirationPreviewMeta",
        preId: "inspirationPreview",
        label: "灵感",
      }, options);
    }

    async function loadCanonProposalPreview(relPath, options = {}) {
      const proposalPath = relPath || $("canonProposalPath").value.trim();
      return loadWorkspaceArtifactPreview(proposalPath, {
        metaId: "canonProposalPreviewMeta",
        preId: "canonProposalPreview",
        label: "Canon proposal",
        emptyText: "生成或应用 Canon proposal 后，内容会显示在这里。",
        missingMessage: "暂无 Canon proposal 可预览。",
      }, options);
    }

    async function loadDebugArtifactPreview(endpoint, options = {}) {
      const fileType = debugActionPreviewFiles[endpoint];
      if (!fileType) return null;
      return loadChapterArtifactPreview(fileType, {
        metaId: "debugArtifactPreviewMeta",
        preId: "debugArtifactPreview",
        label: "调试产物",
      }, options);
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
        const previewData = await loadCanonProposalPreview(data.relative_path, { silent: true });
        setMessage(
          previewData ? actionMessage("Canon 建议", data) : `${actionMessage("Canon 建议", data)}；预览读取失败，请到“运行日志 / 项目文件”查看。`,
          !previewData
        );
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
        const previewData = await loadCanonProposalPreview(data.proposal_snapshot_relative_path || proposalFile, { silent: true });
        setMessage(
          previewData ? actionMessage("应用 Canon proposal", data) : `${actionMessage("应用 Canon proposal", data)}；预览读取失败，请到“运行日志 / 项目文件”查看。`,
          !previewData
        );
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
