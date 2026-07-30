(() => {
  const taskForm = document.querySelector("[data-contact-task-form]");

  const valueFrom = (object, ...keys) => {
    for (const key of keys) {
      if (object?.[key] !== undefined && object?.[key] !== null) {
        return object[key];
      }
    }
    return null;
  };

  const textNode = (tagName, text, className = "") => {
    const element = document.createElement(tagName);
    element.textContent = text;
    if (className) element.className = className;
    return element;
  };

  const currency = (value) => {
    if (value === null || value === undefined || value === "") return "—";
    const text = String(value).trim();
    if (text === "—") return text;
    if (text.startsWith("$")) return text;
    const number = Number(text.replaceAll(",", ""));
    if (!Number.isFinite(number)) return text;
    return new Intl.NumberFormat("en-US", {
      style: "currency",
      currency: "USD",
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    }).format(number);
  };

  const setupGreetingEditor = () => {
    if (!taskForm) return;
    const templateSelect = taskForm.querySelector('[name="greeting_template"]');
    const nameInput = taskForm.querySelector('[name="name"]');
    const messageInput = taskForm.querySelector('[name="content"]');
    const defaultInput = taskForm.querySelector('[name="is_default"]');
    const counter = taskForm.querySelector("[data-greeting-count]");
    const records = new Map(
      Array.from(document.querySelectorAll("[data-greeting-template-record]")).map(
        (record) => [
          record.dataset.templateId,
          {
            name: record.dataset.templateName ?? "",
            message: record.content?.textContent ?? record.textContent ?? "",
            isDefault: record.dataset.templateDefault === "true",
          },
        ]
      )
    );

    const updateCount = () => {
      if (!messageInput || !counter) return;
      const length = Array.from(messageInput.value).length;
      const limit = Number(messageInput.maxLength > 0 ? messageInput.maxLength : 2000);
      counter.textContent = String(length);
      counter.parentElement?.classList.toggle("is-over-limit", length > limit);
    };

    if (messageInput && messageInput.maxLength < 0) {
      messageInput.maxLength = 2000;
    }
    messageInput?.addEventListener("input", updateCount);
    updateCount();

    const loadSelectedTemplate = ({ onlyWhenEmpty = false } = {}) => {
      if (!templateSelect) return;
      const record = records.get(templateSelect.value);
      if (!record) return;
      const editorWasEmpty =
        !nameInput?.value.trim() && !messageInput?.value.trim();
      if (nameInput && (!onlyWhenEmpty || !nameInput.value.trim())) {
        nameInput.value = record.name;
      }
      if (messageInput && (!onlyWhenEmpty || !messageInput.value.trim())) {
        messageInput.value = record.message;
        updateCount();
      }
      if (defaultInput && (!onlyWhenEmpty || editorWasEmpty)) {
        defaultInput.checked = record.isDefault;
      }
    };

    templateSelect?.addEventListener("change", () => loadSelectedTemplate());
    loadSelectedTemplate({ onlyWhenEmpty: true });
  };

  const setupCollaborationSearch = () => {
    if (!taskForm) return;
    const searchInput = taskForm.querySelector(
      "[data-collaboration-search]"
    );
    const select = taskForm.querySelector(
      '[name="collaboration_option"]'
    );
    const state = taskForm.querySelector(
      "[data-collaboration-search-state]"
    );
    if (!searchInput || !select) return;

    const choices = Array.from(select.options).filter(
      (option) => option.value
    );
    const normalize = (value) =>
      String(value ?? "")
        .normalize("NFKC")
        .toLocaleLowerCase()
        .replace(/[\s\-_/+·.，,：:（）()[\]]+/g, "");
    const fuzzyMatch = (candidate, query) => {
      if (!query || candidate.includes(query)) return true;
      let queryIndex = 0;
      for (const character of candidate) {
        if (character === query[queryIndex]) queryIndex += 1;
        if (queryIndex === query.length) return true;
      }
      return false;
    };

    const filterChoices = () => {
      const query = normalize(searchInput.value);
      let visibleCount = 0;
      choices.forEach((option) => {
        const matches = fuzzyMatch(normalize(option.textContent), query);
        option.hidden = !matches && !option.selected;
        if (matches) visibleCount += 1;
      });
      if (state) {
        state.textContent = query
          ? `匹配到 ${visibleCount} 个选项`
          : `${choices.length} 个可用选项`;
      }
    };

    searchInput.addEventListener("input", filterChoices);
    searchInput.addEventListener("search", filterChoices);
  };

  const setupImportBatchSelection = () => {
    if (!taskForm) return null;
    const importTaskSelect = taskForm.querySelector("[data-import-task-select]");
    const methodInputs = Array.from(
      taskForm.querySelectorAll('[name="selection_method"]')
    );
    const panels = Array.from(
      taskForm.querySelectorAll("[data-selection-panel]")
    );
    const manualColumns = Array.from(
      taskForm.querySelectorAll("[data-manual-only]")
    );
    const topNInput = taskForm.querySelector('[name="top_n"]');
    const ruleCopy = taskForm.querySelector("[data-candidate-rule-copy]");
    if (!importTaskSelect) return null;

    const activeMethod = () =>
      methodInputs.find((input) => input.checked)?.value || "SALES";

    const updateMethodUI = () => {
      const method = activeMethod();
      panels.forEach((panel) => {
        panel.hidden = panel.dataset.selectionPanel !== method;
      });
      manualColumns.forEach((column) => {
        column.hidden = method !== "MANUAL";
      });
      if (topNInput) topNInput.required = method === "SALES";
      if (ruleCopy) {
        ruleCopy.textContent =
          method === "CREATOR_ID"
            ? "按导入顺序自动全选候选人，最多 50 位"
            : method === "MANUAL"
              ? "按导入顺序展示，请勾选最终联系人"
              : "按所选销售额周期倒序，销售额相同则按导入顺序排列";
      }
    };

    updateMethodUI();
    return {
      importTaskSelect,
      methodInputs,
      activeMethod,
      updateMethodUI,
      topNInput,
    };
  };

  const setupCandidatePreview = (batchSelection) => {
    if (!taskForm || !batchSelection) return;
    const {
      importTaskSelect,
      methodInputs,
      activeMethod,
      updateMethodUI,
      topNInput,
    } = batchSelection;
    const salesWindowSelect = taskForm.querySelector("[data-sales-window]");
    const storeInput = taskForm.querySelector('[name="store_id"]');
    const preview = taskForm.querySelector("[data-candidate-preview]");
    const body = taskForm.querySelector("[data-candidate-body]");
    const fetchState = taskForm.querySelector("[data-candidate-fetch-state]");
    const excludedCount = taskForm.querySelector("[data-excluded-count]");
    const summary = taskForm.querySelector("[data-candidate-summary]");
    const endpoint = taskForm.dataset.candidatesUrl;
    if (!preview || !body || !endpoint) return;

    let requestController = null;
    let debounceTimer = null;
    const selectedIds = new Set(
      Array.from(
        taskForm.querySelectorAll(
          '[name="selected_creator_ids"]:checked'
        )
      ).map((input) => input.value)
    );

    const setState = (message, loading = false) => {
      if (fetchState) fetchState.textContent = message;
      preview.classList.toggle("is-loading", loading);
      preview.setAttribute("aria-busy", String(loading));
    };

    const emptyRow = (message) => {
      const row = document.createElement("tr");
      const cell = textNode("td", message, "empty-cell");
      cell.colSpan = 7;
      row.append(cell);
      body.replaceChildren(row);
      if (summary) summary.textContent = "0 位已选择";
    };

    const updateManualSummary = () => {
      if (activeMethod() !== "MANUAL") return;
      if (summary) summary.textContent = `${selectedIds.size} 位已选择`;
    };

    const renderCandidates = (candidates) => {
      const method = activeMethod();
      const fragment = document.createDocumentFragment();
      candidates.forEach((candidate, index) => {
        const row = document.createElement("tr");

        const selectCell = document.createElement("td");
        selectCell.dataset.manualOnly = "";
        selectCell.hidden = method !== "MANUAL";
        const checkbox = document.createElement("input");
        checkbox.type = "checkbox";
        checkbox.name = "selected_creator_ids";
        checkbox.value = String(valueFrom(candidate, "id") ?? "");
        checkbox.className = "candidate-checkbox";
        checkbox.checked = selectedIds.has(checkbox.value);
        checkbox.setAttribute(
          "aria-label",
          `选择 ${String(valueFrom(candidate, "nickname", "creatorHandle") ?? "达人")}`
        );
        checkbox.addEventListener("change", () => {
          if (checkbox.checked) selectedIds.add(checkbox.value);
          else selectedIds.delete(checkbox.value);
          updateManualSummary();
        });
        selectCell.append(checkbox);

        const rankCell = document.createElement("td");
        rankCell.append(
          textNode(
            "span",
            String(valueFrom(candidate, "rank", "global_rank", "globalRank") ?? index + 1),
            "candidate-rank"
          )
        );

        const creatorCell = document.createElement("td");
        const handle = String(
          valueFrom(
            candidate,
            "creator_handle",
            "creatorHandle",
            "normalized_handle",
            "handle"
          ) ?? ""
        ).replace(/^@/, "");
        const nickname = String(valueFrom(candidate, "nickname", "display_name") ?? handle);
        creatorCell.append(
          textNode("strong", nickname),
          textNode("small", handle ? `@${handle}` : "—", "block-muted")
        );

        const revenue7Cell = textNode(
          "td",
          currency(valueFrom(candidate, "recent_7_day_revenue", "recent7DayRevenue"))
        );
        const revenue30Cell = document.createElement("td");
        revenue30Cell.append(
          textNode(
            "strong",
            currency(valueFrom(candidate, "recent_30_day_revenue", "recent30DayRevenue"))
          )
        );
        const totalRevenueCell = document.createElement("td");
        totalRevenueCell.append(
          textNode(
            "strong",
            currency(valueFrom(candidate, "total_revenue", "totalRevenue"))
          )
        );
        const stateCell = document.createElement("td");
        const ready = textNode("span", "", "candidate-ready");
        ready.append(textNode("i", ""), document.createTextNode("待联系"));
        stateCell.append(ready);

        row.append(
          selectCell,
          rankCell,
          creatorCell,
          revenue7Cell,
          revenue30Cell,
          totalRevenueCell,
          stateCell
        );
        fragment.append(row);
      });

      if (candidates.length === 0) {
        emptyRow("没有符合条件的未联系达人，请调整导入批次或人数。");
      } else {
        body.replaceChildren(fragment);
        if (summary) {
          summary.textContent =
            method === "MANUAL"
              ? `${selectedIds.size} 位已选择`
              : `${candidates.length} 位已选择`;
        }
      }
    };

    const fetchCandidates = async () => {
      const importTaskId = importTaskSelect.value;
      if (!importTaskId) {
        emptyRow("请选择包含已导入达人数据的批次。");
        setState("等待选择导入批次");
        return;
      }
      const method = activeMethod();
      requestController?.abort();
      requestController = new AbortController();
      const query = new URLSearchParams({
        import_task_id: importTaskId,
        selection_method: method,
        sales_window_days: salesWindowSelect?.value || "30",
        top_n: topNInput?.value || "1",
        store_id: storeInput?.value || "",
      });
      setState("正在更新预览…", true);

      try {
        const response = await fetch(`${endpoint}?${query}`, {
          headers: { Accept: "application/json" },
          cache: "no-store",
          signal: requestController.signal,
        });
        if (!response.ok) {
          const errorPayload = await response.json().catch(() => ({}));
          throw new Error(errorPayload.error || `HTTP ${response.status}`);
        }
        const payload = await response.json();
        const candidates =
          valueFrom(payload, "creators", "candidates", "results", "targets") ?? [];
        renderCandidates(Array.isArray(candidates) ? candidates : []);
        const excluded = valueFrom(payload, "excluded_count", "excludedCount") ?? 0;
        if (excludedCount) excludedCount.textContent = String(excluded);
        const unmatched =
          valueFrom(payload, "unmatchedIdentifiers", "unmatched_identifiers") ?? [];
        setState(
          Array.isArray(unmatched) && unmatched.length
            ? `未在批次中找到：${unmatched.join("、")}`
            : "预览已更新"
        );
      } catch (error) {
        if (error.name === "AbortError") return;
        setState(
          error.message || "预览更新失败，提交时将由服务端再次校验"
        );
      }
    };

    const scheduleFetch = () => {
      window.clearTimeout(debounceTimer);
      debounceTimer = window.setTimeout(fetchCandidates, 180);
    };

    importTaskSelect.addEventListener("change", () => {
      selectedIds.clear();
      scheduleFetch();
    });
    methodInputs.forEach((input) =>
      input.addEventListener("change", () => {
        updateMethodUI();
        scheduleFetch();
      })
    );
    topNInput?.addEventListener("input", scheduleFetch);
    topNInput?.addEventListener("change", scheduleFetch);
    salesWindowSelect?.addEventListener("change", scheduleFetch);
    storeInput?.addEventListener("change", scheduleFetch);

    if (importTaskSelect.value) scheduleFetch();
  };

  const setupTaskSubmission = () => {
    if (!taskForm) return;
    const defaultAction = taskForm.querySelector('input[type="hidden"][name="action"]');

    taskForm.addEventListener("submit", (event) => {
      const submittedAction = event.submitter?.value || "create_task";
      if (defaultAction) defaultAction.value = submittedAction;
    });
  };

  const setupTaskMonitor = () => {
    const monitor = document.querySelector("[data-contact-task-monitor]");
    if (!monitor) return;
    const activeStatuses = new Set(["PENDING", "RUNNING"]);
    if (!activeStatuses.has(monitor.dataset.status)) return;

    const currentProgress = () =>
      Number(
        document
          .querySelector(".progress-ring strong")
          ?.textContent?.replace("%", "")
          .trim() ?? 0
      );

    const refreshWhenChanged = async () => {
      try {
        const response = await fetch(monitor.dataset.statusUrl, {
          headers: { Accept: "application/json" },
          cache: "no-store",
        });
        if (!response.ok) return;
        const task = await response.json();
        if (
          task.status !== monitor.dataset.status ||
          Number(task.progress ?? 0) !== currentProgress()
        ) {
          window.location.reload();
        }
      } catch {
        // A transient polling failure must not replace the persisted task page.
      }
    };

    window.setInterval(refreshWhenChanged, 4000);
  };

  const setupBrowserStatus = () => {
    const status = document.querySelector("[data-browser-status]");
    if (!status?.dataset.statusUrl) return;
    const label = status.querySelector("[data-browser-status-label]");
    const detail = status.querySelector("[data-browser-status-detail]");

    const refresh = async () => {
      try {
        const response = await fetch(status.dataset.statusUrl, {
          headers: { Accept: "application/json" },
          cache: "no-store",
        });
        if (!response.ok) return;
        const payload = await response.json();
        const ready = payload.ready === true;
        status.classList.toggle("is-ready", ready);
        status.classList.toggle("is-unavailable", !ready);
        if (label) {
          label.textContent = payload.statusLabel || "浏览器未就绪";
        }
        if (detail) {
          detail.textContent = ready
            ? `${payload.connectionModeLabel || "浏览器可复用"} · 端口 ${payload.debuggingPort}`
            : payload.errorMessage || "店铺首页验收未通过";
        }
      } catch {
        // Keep the most recent server-rendered state on a transient failure.
      }
    };

    window.setInterval(refresh, 5000);
  };

  setupGreetingEditor();
  setupCollaborationSearch();
  const batchSelection = setupImportBatchSelection();
  setupCandidatePreview(batchSelection);
  setupTaskSubmission();
  setupTaskMonitor();
  setupBrowserStatus();
})();
