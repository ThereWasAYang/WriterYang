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

    async function runPreviewPackage() {
      return withBusy("生成 Preview Package", async () => {
        const payload = exportPayload();
        delete payload.output;
        delete payload.force;
        payload.source = $("previewSource").value;
        const data = await apiPost("/api/preview/package", payload);
        $("fileViewer").textContent = JSON.stringify(data, null, 2);
        setMessage(actionMessage("生成 Preview Package", data));
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
      const chapter = compareChapterNumber();
      $("chapterNumber").value = String(chapter);
      await Promise.all(chapterCompareFileTypes.map(async (type) => {
        const target = $(`${type}Viewer`);
        try {
          const data = await apiGet("/api/chapter-file", {
            path: projectPath(),
            chapter,
            file: type,
          });
          target.textContent = data.exists ? data.content : `${data.relative_path} 不存在`;
        } catch (error) {
          target.textContent = error.message;
        }
      }));
      setMessage("章节对照已加载");
      await loadProseView({ silent: true });
    }

    const proseSourceLabels = { polished: "润色稿", draft: "初稿" };

    function splitChapterFrontmatter(text) {
      const raw = String(text || "");
      if (!raw.startsWith("---\n")) return { title: "", body: raw };
      const end = raw.indexOf("\n---", 4);
      if (end === -1) return { title: "", body: raw };
      const meta = raw.slice(4, end);
      const body = raw.slice(end + 4).replace(/^\s*\n/, "");
      const match = meta.match(/^title:\s*(.+)$/m);
      const title = match ? match[1].trim().replace(/^["']|["']$/g, "") : "";
      return { title, body };
    }

    async function loadProseView(options = {}) {
      const select = $("proseViewSource");
      if (!select) return null;
      const requested = options.source || select.value || "polished";
      const chapter = options.chapter || compareChapterNumber();
      const viewer = $("chapterProseViewer");
      const meta = $("proseViewMeta");
      const order = requested === "draft" ? ["draft"] : ["polished", "draft"];
      for (const source of order) {
        let data;
        try {
          data = await apiGet("/api/chapter-file", { path: projectPath(), chapter, file: source });
        } catch (error) {
          viewer.textContent = `无法读取正文：${error.message}`;
          viewer.classList.add("prose-empty");
          meta.textContent = `第 ${chapter} 章正文读取失败`;
          if (!options.silent) setMessage(error.message, true);
          return null;
        }
        const relPath = data.relative_path
          || `memory/chapters/${String(chapter).padStart(3, "0")}/${source}.md`;
        if (data.exists && String(data.content || "").trim()) {
          const { title, body } = splitChapterFrontmatter(data.content);
          viewer.textContent = body.trim() ? body : data.content;
          viewer.classList.remove("prose-empty");
          select.value = source;
          const fallbackNote = (requested === "polished" && source === "draft")
            ? "（暂无润色稿，显示初稿）" : "";
          const titleNote = title ? ` · 《${title}》` : "";
          meta.textContent = `第 ${chapter} 章${titleNote} · ${proseSourceLabels[source]} · ${relPath}${fallbackNote}`;
          if (!options.silent) setMessage(`已加载第 ${chapter} 章正文（${proseSourceLabels[source]}）`);
          return source;
        }
      }
      viewer.textContent = requested === "draft"
        ? `第 ${chapter} 章尚无初稿（draft.md）。`
        : `第 ${chapter} 章尚未生成正文。点击左侧“开始写作”后会自动显示在这里。`;
      viewer.classList.add("prose-empty");
      meta.textContent = `第 ${chapter} 章 · 暂无正文`;
      if (!options.silent) setMessage(`第 ${chapter} 章暂无正文`, true);
      return null;
    }

    function openProseFile() {
      const chapter = compareChapterNumber();
      const source = $("proseViewSource").value || "polished";
      const relPath = `memory/chapters/${String(chapter).padStart(3, "0")}/${source}.md`;
      return readWorkspaceFile(relPath);
    }

    function focusChapterProse() {
      const panel = $("chapterProsePanel");
      if (panel) panel.scrollIntoView({ behavior: "smooth", block: "start" });
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
        $("auditIssueList").textContent = "无问题";
        return;
      }
      $("auditIssueList").innerHTML = issues.map((issue, index) => {
        const first = (issue.matches || [])[0] || {};
        const loc = first.matched ? `第 ${first.line} 行第 ${first.column} 列` : "无法定位";
        return `<button class="issue-button" data-index="${index}"><b>${escapeHtml(issue.id)}</b> <span class="badge">${escapeHtml(auditSeverityLabel(issue.severity))}</span><br>${escapeHtml(issue.description || "")}<br><small>${escapeHtml(loc)}</small></button>`;
      }).join("");
      document.querySelectorAll(".issue-button").forEach((button) => {
        button.addEventListener("click", () => {
          const issue = issues[Number(button.dataset.index)];
          latestSelectedAuditIssue = issue;
          const match = (issue.matches || []).find((item) => item.matched);
          if (!match) {
            setMessage("该问题的依据无法定位到正文", true);
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
          <tr><td>${escapeHtml(run.workflow_run_id || "")}</td><td>${escapeHtml(run.surface || "")}</td><td>${escapeHtml((run.session_ids || []).join(", "))}</td><td>${escapeHtml(run.node_count ?? 0)}</td><td>${escapeHtml(run.decision_count ?? 0)}</td><td>${escapeHtml(run.status || "")}</td><td>${escapeHtml(run.started_at || "")}</td></tr>
        `).join("");
        const callRows = (data.provider_calls || []).map((call) => `
          <tr><td>${escapeHtml(call.provider || "")}</td><td>${escapeHtml(call.model || "")}</td><td>${escapeHtml(call.workflow_run_id || "")}</td><td>${escapeHtml(call.node_id || "")}</td><td>${escapeHtml(call.status || "")}</td><td>${escapeHtml(call.error_type || "")}</td></tr>
        `).join("");
        const ioRows = (data.model_io_logs || []).map((log) => `
          <tr><td>${escapeHtml(log.agent_name || "")}</td><td>${escapeHtml(log.workflow_run_id || "")}</td><td>${escapeHtml(log.session_id || "")}</td><td>${escapeHtml(log.node_id || "")}</td><td>${escapeHtml(log.status || "")}</td><td>${escapeHtml(log.model_io_path || "")}</td></tr>
        `).join("");
        $("runLogPanel").innerHTML = `
          <h3>Workflow runs</h3><table><thead><tr><th>workflow_run_id</th><th>surface</th><th>sessions</th><th>nodes</th><th>decisions</th><th>status</th><th>started_at</th></tr></thead><tbody>${runRows || "<tr><td colspan='7'>无</td></tr>"}</tbody></table>
          <h3 style="margin-top: 16px;">Provider calls</h3><table><thead><tr><th>provider</th><th>model</th><th>workflow</th><th>node</th><th>status</th><th>error</th></tr></thead><tbody>${callRows || "<tr><td colspan='6'>无</td></tr>"}</tbody></table>
          <h3 style="margin-top: 16px;">Model I/O</h3><table><thead><tr><th>agent</th><th>workflow</th><th>session</th><th>node</th><th>status</th><th>path</th></tr></thead><tbody>${ioRows || "<tr><td colspan='6'>无</td></tr>"}</tbody></table>
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
      const byTask = usage.by_task || {};
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
        <h3>按 Task</h3>${tableFromObject(byTask, "task")}
        <h3 style="margin-top: 16px;">按 Provider</h3>${tableFromObject(byProvider, "provider")}
        <h3 style="margin-top: 16px;">按 Model</h3>${tableFromObject(byModel, "model")}
        <details style="margin-top: 12px;"><summary>完整用量 JSON</summary><pre>${escapeHtml(JSON.stringify(usage, null, 2))}</pre></details>
      `;
    }
