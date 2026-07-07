    async function loadProviderConfig() {
      try {
        const data = await apiGet("/api/provider-config", { path: projectPath() });
        const missingFields = [];
        if (!hasResponseField(data, "effective_profiles")) missingFields.push("effective_profiles");
        if (!hasResponseField(data, "effective_tasks")) missingFields.push("effective_tasks");
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
        providerEffectiveProfilesCache = data.effective_profiles || {};
        providerEffectiveTasksCache = data.effective_tasks || {};
        const warnings = [
          ...(providerConfigBackendMismatch ? [providerConfigBackendMismatch] : []),
          ...(data.agents?.warnings || []),
        ];
        $("providerConfigWarnings").innerHTML = warnings.length
          ? `<span style="color:#b91c1c;">${warnings.map(escapeHtml).join("<br>")}</span>`
          : "Profile API 配置正常。";
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

    function providerParameterCapabilities(providerName) {
      const provider = String(providerName || "").toLowerCase();
      const caps = {};
      [
        "provider", "model", "base_url_env", "api_key_env", "max_context_tokens",
        "max_tokens", "timeout_seconds", "max_retries",
      ].forEach((field) => {
        caps[field] = { effective: true, editable: true };
      });
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
      return providerParameterCapabilities(provider);
    }

    function currentProviderJsonValues() {
      return currentProviderCapabilities().json_response_format?.allowed_values || jsonResponseFormatValues;
    }

    function applyProviderCapabilityState(disableAll = false) {
      const inheritLocksCallFields = Boolean(disableAll);
      const caps = currentProviderCapabilities();
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
      if (Object.prototype.hasOwnProperty.call(agent, "json_response_format")) {
        editable.json_response_format = agent.json_response_format;
      }
      return editable;
    }

    function providerBusinessPatchAllowedByCapabilities(agent, capabilities) {
      const editable = {};
      [
        "max_context_tokens", "max_tokens", "timeout_seconds", "max_retries",
      ].forEach((key) => {
        if (Object.prototype.hasOwnProperty.call(agent, key)) editable[key] = agent[key];
      });
      if (Object.prototype.hasOwnProperty.call(agent, "json_response_format")) {
        editable.json_response_format = agent.json_response_format;
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
      const profiles = config?.profiles || {};
      const names = ["default", ...Array.from(new Set([...providerProfileNames, ...Object.keys(profiles)]))];
      const previousName = $("providerProfileSelect").value;
      $("providerProfileSelect").innerHTML = names.map((name) => `<option value="${escapeAttr(name)}">${escapeHtml(name)}</option>`).join("");
      if (names.length) {
        $("providerProfileSelect").value = names.includes(previousName) ? previousName : names[0];
        renderProviderProfileFields();
      } else {
        $("providerFieldEditor").value = "";
      }
      renderProviderTaskEditor();
    }

    function renderProviderProfileFields() {
      const name = $("providerProfileSelect").value;
      const inheritsDefault = providerProfileInheritsDefault(name);
      const profile = providerProfileDisplayConfig(name, inheritsDefault);
      $("providerInheritDefaultRow").classList.toggle("hidden", name === "default");
      $("providerInheritDefaultField").checked = name !== "default" && inheritsDefault;
      $("providerInheritDefaultField").disabled = name === "default";
      $("providerProviderField").value = profile.provider || "";
      $("providerModelField").value = profile.model || "";
      $("providerBaseUrlEnvField").value = profile.base_url_env || "";
      $("providerApiKeyEnvField").value = profile.api_key_env || "";
      $("providerMaxTokensField").value = profile.max_tokens ?? "";
      $("providerMaxContextTokensField").value = profile.max_context_tokens ?? "";
      $("providerTimeoutSecondsField").value = profile.timeout_seconds ?? "";
      $("providerMaxRetriesField").value = profile.max_retries ?? "";
      const caps = applyProviderCapabilityState(name !== "default" && inheritsDefault);
      $("providerFieldEditor").value = JSON.stringify(
        name !== "default" && inheritsDefault
          ? providerInheritedOverridePatch(name, caps)
          : providerEditablePatch(profile, caps),
        null,
        2,
      );
      renderProviderEffectivePanel();
    }

    function providerProfileInheritsDefault(name) {
      if (name === "default") return false;
      const effective = providerEffectiveProfilesCache?.[name] || {};
      if (effective.inherit_default === true || effective.inherits_default === true) return true;
      const raw = providerConfigCache?.profiles?.[name] || {};
      return raw.inherit_default === true;
    }

    function providerDefaultDisplayConfig() {
      return providerEffectiveProfilesCache?.default?.config || providerConfigCache?.default || {};
    }

    function providerProfileDisplayConfig(name, inheritsDefault = providerProfileInheritsDefault(name)) {
      if (name === "default") return providerDefaultDisplayConfig();
      if (inheritsDefault) {
        const current = providerEffectiveProfilesCache?.[name]?.config || providerConfigCache?.profiles?.[name] || {};
        return { ...providerDefaultDisplayConfig(), ...providerConfigFields(current) };
      }
      return providerEffectiveProfilesCache?.[name]?.config || providerConfigCache?.profiles?.[name] || providerDefaultDisplayConfig();
    }

    function providerConfigFields(config) {
      const patch = {};
      Object.entries(config || {}).forEach(([key, value]) => {
        if (key !== "inherit_default" && value !== undefined && value !== null) patch[key] = value;
      });
      return patch;
    }

    function providerInheritedOverridePatch(name, capabilities = currentProviderCapabilities()) {
      const effectiveOverride = providerEffectiveProfilesCache?.[name]?.override;
      const raw = effectiveOverride || providerConfigCache?.profiles?.[name] || {};
      return providerBusinessPatchAllowedByCapabilities(providerConfigFields(raw), capabilities);
    }

    function fillProviderForm(agent) {
      $("providerProviderField").value = agent.provider || "";
      $("providerModelField").value = agent.model || "";
      $("providerBaseUrlEnvField").value = agent.base_url_env || "";
      $("providerApiKeyEnvField").value = agent.api_key_env || "";
      $("providerMaxTokensField").value = agent.max_tokens ?? "";
      $("providerMaxContextTokensField").value = agent.max_context_tokens ?? "";
      $("providerTimeoutSecondsField").value = agent.timeout_seconds ?? "";
      $("providerMaxRetriesField").value = agent.max_retries ?? "";
      const caps = applyProviderCapabilityState(false);
      $("providerFieldEditor").value = JSON.stringify(providerEditablePatch(agent, caps), null, 2);
    }

    function toggleProviderDefaultInheritance() {
      const name = $("providerProfileSelect").value;
      if (name === "default") return;
      const inheritsDefault = $("providerInheritDefaultField").checked;
      fillProviderForm(providerProfileDisplayConfig(name, inheritsDefault));
      const caps = applyProviderCapabilityState(inheritsDefault);
      if (inheritsDefault) {
        $("providerFieldEditor").value = JSON.stringify(providerInheritedOverridePatch(name, caps), null, 2);
      }
      renderProviderEffectivePanel();
    }

    function providerEditablePatch(agent, capabilities = currentProviderCapabilities()) {
      return providerPatchAllowedByCapabilities(agent, capabilities);
    }

    function buildProviderPatchFromForm() {
      const patch = {};
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
        patch[key] = key === "timeout_seconds" ? value : Math.trunc(value);
      });
      return patch;
    }

    function providerInheritedPatchFromEditor() {
      const raw = $("providerFieldEditor").value.trim();
      let advanced = {};
      if (raw) {
        advanced = JSON.parse(raw);
        if (!advanced || typeof advanced !== "object" || Array.isArray(advanced)) {
          throw new Error("高级 JSON 必须是对象");
        }
      }
      const patch = { ...advanced };
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
        const inheritsDefault = $("providerProfileSelect").value !== "default" && $("providerInheritDefaultField").checked;
        const patch = inheritsDefault
          ? { ...advanced }
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
      const inheritsDefault = $("providerProfileSelect").value !== "default" && $("providerInheritDefaultField").checked;
      applyProviderCapabilityState(inheritsDefault);
      syncProviderFormToAdvancedJson();
      renderProviderEffectivePanel();
    }

    async function saveProviderConfig() {
      try {
        const profileName = $("providerProfileSelect").value;
        const payload = {
          path: projectPath(),
          profiles: {},
        };
        if (profileName === "default") {
          payload.default = providerPatchFromEditorAndForm();
        } else if ($("providerInheritDefaultField").checked) {
          const patch = providerInheritedPatchFromEditor();
          patch.inherit_default = true;
          payload.profiles = { [profileName]: patch };
        } else {
          const patch = providerPatchFromEditorAndForm();
          patch.inherit_default = false;
          payload.profiles = { [profileName]: patch };
        }
        const data = await apiPost("/api/provider-config", payload);
        $("providerConfigPanel").textContent = JSON.stringify(data, null, 2);
        await loadProviderConfig();
        setMessage("Profile 模型配置已保存并备份");
      } catch (error) {
        setMessage(error.message, true);
      }
    }

    function renderProviderEffectivePanel() {
      const name = $("providerProfileSelect").value;
      const effective = providerEffectiveProfilesCache?.[name] || {};
      const uiInheritsDefault = name !== "default" && $("providerInheritDefaultField").checked;
      const config = uiInheritsDefault
        ? providerProfileDisplayConfig(name, true)
        : effective.config || providerProfileDisplayConfig(name, false) || {};
      const source = effective.source_label || effective.source || (uiInheritsDefault ? "default + profile" : "unresolved");
      const overrideFields = effective.override_fields || [];
      const caps = currentProviderCapabilities();
      let inheritance = "未解析";
      if (name === "default" && source === "default") {
        inheritance = "默认配置";
      } else if (uiInheritsDefault || effective.inherit_default || effective.inherits_default) {
        inheritance = "继承 default 调用参数";
      } else if (effective.has_override) {
        inheritance = source === "default+profile" ? "default+profile" : "Profile 覆盖配置";
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

    function renderProviderTaskEditor() {
      if (!$("providerTaskSelect")) return;
      const tasks = providerConfigCache?.tasks || {};
      const names = Array.from(new Set([...providerTaskNames, ...Object.keys(tasks)]));
      const previousName = $("providerTaskSelect").value;
      $("providerTaskSelect").innerHTML = names.map((name) => `<option value="${escapeAttr(name)}">${escapeHtml(name)}</option>`).join("");
      if (!names.length) return;
      $("providerTaskSelect").value = names.includes(previousName) ? previousName : names[0];
      renderProviderTaskFields();
    }

    function renderProviderTaskFields() {
      if (!$("providerTaskSelect")) return;
      const taskName = $("providerTaskSelect").value;
      const raw = providerConfigCache?.tasks?.[taskName] || {};
      $("providerTaskThinkingTypeField").value = raw.thinking?.type || "";
      $("providerTaskReasoningField").value = raw.reasoning || "";
      $("providerTaskTemperatureField").value = raw.temperature ?? "";
      $("providerTaskEditor").value = JSON.stringify(providerTaskAdvancedPatch(raw), null, 2);
      renderProviderTaskEffectivePanel();
    }

    function providerTaskAdvancedPatch(raw) {
      const patch = {};
      Object.entries(raw || {}).forEach(([key, value]) => {
        if (!providerTaskFormFields.includes(key) && value !== undefined && value !== null) {
          patch[key] = value;
        }
      });
      return patch;
    }

    function providerTaskFormPatch() {
      const patch = {};
      const thinkingType = $("providerTaskThinkingTypeField").value.trim();
      const reasoning = $("providerTaskReasoningField").value.trim();
      const temperatureRaw = $("providerTaskTemperatureField").value.trim();
      if (thinkingType) {
        patch.thinking = { type: thinkingType };
      }
      if (reasoning) {
        patch.reasoning = reasoning;
      }
      if (temperatureRaw) {
        const temperature = Number(temperatureRaw);
        if (Number.isNaN(temperature)) throw new Error("temperature 必须是数字");
        patch.temperature = temperature;
      }
      return patch;
    }

    function providerTaskPatchFromEditorAndForm() {
      const raw = $("providerTaskEditor").value.trim();
      let advanced = {};
      if (raw) {
        advanced = JSON.parse(raw);
        if (!advanced || typeof advanced !== "object" || Array.isArray(advanced)) {
          throw new Error("Task 高级 JSON 必须是对象");
        }
      }
      providerTaskFormFields.forEach((field) => delete advanced[field]);
      return { ...advanced, ...providerTaskFormPatch() };
    }

    function renderProviderTaskEffectivePanel() {
      if (!$("providerTaskEffectivePanel")) return;
      const taskName = $("providerTaskSelect").value;
      const effective = providerEffectiveTasksCache?.[taskName] || {};
      const config = effective.config || {};
      const rows = [
        ["profile", effective.profile],
        ["source", effective.source_label || effective.source],
        ["override_fields", (effective.override_fields || []).join(", ") || "无"],
        ["provider", config.provider],
        ["model", config.model],
        ["thinking", config.thinking?.type],
        ["reasoning", config.reasoning],
        ["temperature", config.temperature],
        ["max_tokens", config.max_tokens],
        ["max_context_tokens", config.max_context_tokens],
        ["timeout_seconds", config.timeout_seconds],
      ];
      const body = rows.map(([label, value]) => `
        <div class="provider-effective-row">
          <b>${escapeHtml(label)}</b>
          <span>${escapeHtml(value ?? "未设置")}</span>
        </div>
      `).join("");
      const error = effective.error ? `<div class="message error">${escapeHtml(effective.error)}</div>` : "";
      $("providerTaskEffectivePanel").innerHTML = `${error}<div class="provider-effective-list">${body}</div>`;
    }

    async function saveProviderTaskConfig() {
      try {
        const taskName = $("providerTaskSelect").value;
        const patch = providerTaskPatchFromEditorAndForm();
        if (Object.prototype.hasOwnProperty.call(patch, "json_response_format")) {
          const allowed = providerEffectiveTasksCache?.[taskName]?.parameter_capabilities?.json_response_format?.allowed_values || jsonResponseFormatValues;
          if (!allowed.includes(patch.json_response_format)) {
            throw new Error(`当前 task 不支持 json_response_format=${patch.json_response_format}；可用值：${allowed.join(", ")}`);
          }
        }
        const data = await apiPost("/api/provider-config", {
          path: projectPath(),
          tasks: { [taskName]: patch },
        });
        $("providerConfigPanel").textContent = JSON.stringify(data, null, 2);
        await loadProviderConfig();
        setMessage("Task 覆盖配置已保存并备份");
      } catch (error) {
        setMessage(error.message, true);
      }
    }

    async function clearProviderTaskConfig() {
      try {
        const taskName = $("providerTaskSelect").value;
        const data = await apiPost("/api/provider-config", {
          path: projectPath(),
          clear_tasks: [taskName],
        });
        $("providerConfigPanel").textContent = JSON.stringify(data, null, 2);
        await loadProviderConfig();
        setMessage("Task 覆盖配置已清除");
      } catch (error) {
        setMessage(error.message, true);
      }
    }

