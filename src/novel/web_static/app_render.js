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
        if (blocking) parts.push(`阻断问题=${blocking}`);
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
      if (data.overall_status) parts.push(`审核结论=${auditStatusLabel(data.overall_status)}`);
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
        syncCompareChapterSelect({});
        renderRewriteEvents([]);
        return;
      }
      rememberSessionId(session.session_id);
      syncCompareChapterSelect(session);
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
      applyAllowedSessionCommands(data.next_allowed_commands);
    }

    function applyAllowedSessionCommands(commands) {
      if (!Array.isArray(commands)) return;
      const allowed = new Set(commands);
      const buttonCommands = {
        sessionReviseOutline: ["session.revise_outline"],
        sessionApprove: ["session.approve_outline"],
        sessionRun: ["session.run"],
        sessionReviseAuditContent: ["session.revise_content"],
        sessionReviseInstruction: ["session.revise_content"],
        sessionReviseAudit: ["session.revise_audit"],
        sessionRetryRewrite: ["session.retry_rewrite"],
        sessionUndoRewrite: ["session.undo_rewrite"],
        sessionAccept: ["session.accept"],
        sessionArchive: ["session.archive"],
      };
      Object.entries(buttonCommands).forEach(([id, commandTypes]) => {
        const button = $(id);
        if (button) button.disabled = !commandTypes.some((command) => allowed.has(command));
      });
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

    const AUDIT_SEVERITY_LABELS = { low: "低", medium: "中", high: "高", critical: "严重" };
    const AUDIT_STATUS_LABELS = { passed: "通过", needs_revision: "需修订", blocked: "已阻断" };
    const AUDIT_TYPE_LABELS = {
      state_conflict: "状态冲突",
      continuity_issue: "连续性问题",
      knowledge_conflict: "知识链冲突",
      premature_reveal: "提前揭示伏笔",
      style_mismatch: "文风不符",
      plot_logic_issue: "情节逻辑问题",
      character_voice_issue: "人物口吻问题",
      timeline_conflict: "时间线冲突",
      canon_conflict: "设定冲突",
      plan_deviation: "偏离大纲",
    };

    function auditSeverityLabel(value) {
      return value ? (AUDIT_SEVERITY_LABELS[value] || value) : "";
    }

    function auditStatusLabel(value) {
      return value ? (AUDIT_STATUS_LABELS[value] || value) : "";
    }

    function auditTypeLabel(value) {
      return value ? (AUDIT_TYPE_LABELS[value] || value) : "";
    }

    function renderAuditIssueRows(issues, emptyText) {
      if (!issues.length) return `<div>${emptyText}</div>`;
      return issues.map((issue) => `
        <div style="margin-top: 4px;">
          <b>${escapeHtml(issue.id || "")}</b>
          [${escapeHtml(auditSeverityLabel(issue.severity))}/${escapeHtml(auditTypeLabel(issue.type))}]
          ${escapeHtml(issue.description || "")}
          ${issue.suggested_fix ? `<div>建议修复：${escapeHtml(issue.suggested_fix)}</div>` : ""}
        </div>
      `).join("");
    }

    function renderSessionAuditSummary(auditSummary) {
      if (!auditSummary.length) return "<div style=\"margin-top: 8px;\">审核：未生成</div>";
      return auditSummary.map((item) => {
        const issueRows = renderAuditIssueRows(item.issues || [], "无问题");
        const statusText = item.overall_status
          ? auditStatusLabel(item.overall_status)
          : (item.exists ? "读取失败" : "未生成");
        return `
          <div style="margin-top: 8px;">
            <b>第 ${escapeHtml(item.chapter_number)} 章审核</b>：
            ${escapeHtml(statusText)}
            ，阻断问题 ${escapeHtml(item.blocking_issue_count || 0)} 个
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
          const issueRows = renderAuditIssueRows(event.blocking_issues || [], "无阻断问题");
          const snapshotButton = event.rejected_text_snapshot_path
            ? `<button class="view-rejected-text" data-path="${escapeAttr(event.rejected_text_snapshot_path)}">查看被打回原文</button>`
            : "";
          const selectButton = `<button class="select-rewrite-event" data-event-id="${escapeAttr(event.event_id)}">选择该打回记录</button>`;
          const auditRevisions = (event.audit_revision_history || []).length
            ? `<div>审核复审次数：${escapeHtml((event.audit_revision_history || []).length)}</div>`
            : "";
          return `
            <div style="margin-top: 8px;">
              <div>第 ${escapeHtml(event.chapter_number)} 章第 ${escapeHtml(event.round_number)} 轮被审核打回：${escapeHtml(actionText)}，状态=${escapeHtml(event.status || "")}</div>
              <div>事件 ID：${escapeHtml(event.event_id || "")}</div>
              <div>撤回状态：${escapeHtml(event.undo_status || "not_requested")}</div>
              <div>审核文件：${escapeHtml(event.trigger_audit_path || "")}</div>
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
    $("loadStyleGuide").addEventListener("click", () => loadStyleGuide());
    $("saveStyleGuide").addEventListener("click", saveStyleGuide);
    $("resetStyleGuideTemplate").addEventListener("click", restoreStyleGuideTemplate);
    $("generateStyleGuideDraft").addEventListener("click", generateStyleGuideDraft);
    $("styleGuideEditor").addEventListener("input", updateStyleGuideDirtyState);
    $("inspireProject").addEventListener("click", inspireProject);
    $("regenerateInspiration").addEventListener("click", regenerateInspiration);
    $("openInspirationFile").addEventListener("click", () => readWorkspaceFile(inspirationPreviewPath));
    $("reloadProseView").addEventListener("click", () => loadProseView());
    $("openProseFile").addEventListener("click", openProseFile);
    $("proseViewSource").addEventListener("change", () => loadProseView());
    $("reloadOutlinePreview").addEventListener("click", loadCurrentSession);
    $("canonSuggest").addEventListener("click", canonSuggest);
    $("canonApply").addEventListener("click", canonApply);
    $("viewLatestCanonProposal").addEventListener("click", () => {
      if (!latestCanonProposalSnapshotPath) {
        setMessage("暂无可查看的 Canon proposal 快照。", true);
        return;
      }
      loadCanonProposalPreview(latestCanonProposalSnapshotPath);
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
    $("previewPackage").addEventListener("click", runPreviewPackage);
    $("sessionStart").addEventListener("click", () => {
      $("sessionId").value = "";
      renderOutlinePreviewPlaceholder("正在创建新的 Session 大纲，完成后会显示在这里。");
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
    $("revisionLoadBlocks").addEventListener("click", loadRevisionBlocks);
    $("revisionStart").addEventListener("click", () => runRevisionAction("/api/revision-session/start", "创建局部修订范围"));
    $("revisionRun").addEventListener("click", () => runRevisionAction("/api/revision-session/run", "生成并审核局部修订"));
    $("revisionAccept").addEventListener("click", () => runRevisionAction("/api/revision-session/accept", "认可局部修订"));
    $("cancelSessionTask").addEventListener("click", cancelSessionTask);
    $("viewFile").addEventListener("click", viewFile);
    $("loadCompare").addEventListener("click", loadCompare);
    $("compareChapterSelect").addEventListener("change", () => {
      const selected = $("compareChapterSelect").value;
      if (selected) $("chapterNumber").value = selected;
      loadCompare();
    });
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
    $("providerProfileSelect").addEventListener("change", renderProviderProfileFields);
    $("providerInheritDefaultField").addEventListener("change", toggleProviderDefaultInheritance);
    providerFormFieldIds.forEach((id) => {
      const handler = id === "providerProviderField" ? refreshProviderCapabilityState : syncProviderFormToAdvancedJson;
      $(id).addEventListener("input", handler);
      $(id).addEventListener("change", handler);
    });
    $("saveProviderConfig").addEventListener("click", saveProviderConfig);
    $("providerTaskSelect").addEventListener("change", renderProviderTaskFields);
    $("saveProviderTaskConfig").addEventListener("click", saveProviderTaskConfig);
    $("clearProviderTaskConfig").addEventListener("click", clearProviderTaskConfig);
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
    $("projectPath").addEventListener("change", () => {
      localStorage.setItem("writeryang.projectPath", $("projectPath").value);
      resetStyleGuideState();
    });
