(() => {
  const body = document.body;
  const sidebarToggle = document.querySelector("[data-sidebar-toggle]");
  const sidebarClose = document.querySelector("[data-sidebar-close]");
  const toast = document.querySelector(".toast");

  const setSidebar = (isOpen) => {
    body.classList.toggle("sidebar-open", isOpen);
    sidebarToggle?.setAttribute("aria-expanded", String(isOpen));
  };

  const showToast = (message) => {
    if (!toast) return;
    const messageNode = toast.querySelector(".toast-message");
    if (messageNode) messageNode.textContent = message;
    toast.setAttribute("aria-hidden", "false");
    window.setTimeout(() => toast.setAttribute("aria-hidden", "true"), 2600);
  };

  sidebarToggle?.addEventListener("click", () => {
    setSidebar(!body.classList.contains("sidebar-open"));
  });
  sidebarClose?.addEventListener("click", () => setSidebar(false));
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") setSidebar(false);
  });

  document.querySelector("[data-refresh-page]")?.addEventListener("click", () => {
    window.location.reload();
  });
  document.querySelectorAll("[data-demo-action]").forEach((element) => {
    element.addEventListener("click", (event) => {
      event.preventDefault();
      showToast(element.dataset.demoAction || "该功能暂未开放。");
    });
  });

  const workbench = document.querySelector("[data-import-workbench]");
  if (workbench) {
    const uploadForm = workbench.querySelector("[data-import-upload-form]");
    const fileInput = workbench.querySelector("[data-import-file]");
    const sheetField = workbench.querySelector("[data-sheet-field]");
    const sheetSelect = workbench.querySelector("[data-sheet-select]");
    const previewPanel = workbench.querySelector("[data-import-preview]");
    const message = workbench.querySelector("[data-import-message]");
    const previewButton = workbench.querySelector("[data-preview-submit]");
    const confirmButton = workbench.querySelector("[data-confirm-import]");
    const savedFileNameInput = workbench.querySelector("[data-saved-file-name]");
    const instructionToggle = workbench.querySelector("[data-toggle-instruction]");
    const instructionForm = workbench.querySelector("[data-instruction-form]");
    const originalTable = workbench.querySelector("[data-original-table]");
    const convertedTable = workbench.querySelector("[data-converted-table]");
    const mappings = workbench.querySelector("[data-field-mappings]");
    const history = workbench.querySelector("[data-rule-history]");
    const warnings = workbench.querySelector("[data-preview-warnings]");
    const previewTitle = workbench.querySelector("[data-preview-title]");
    const previewMeta = workbench.querySelector("[data-preview-meta]");
    const csrfToken = uploadForm?.querySelector(
      "input[name=csrfmiddlewaretoken]"
    )?.value;
    let currentPreview = null;
    let previewRequestController = null;

    const setBusy = (button, busy, busyLabel) => {
      if (!button) return;
      if (busy) {
        button.dataset.originalLabel = button.textContent;
        button.textContent = busyLabel;
        button.disabled = true;
      } else {
        button.textContent = button.dataset.originalLabel || button.textContent;
        button.disabled = false;
      }
    };

    const setMessage = (text, type = "info") => {
      if (!message) return;
      message.textContent = text;
      message.className = `import-message import-message-${type}`;
      message.hidden = !text;
    };

    const endpointFor = (template, previewId) =>
      template.replace("__PREVIEW_ID__", previewId);

    const readJsonResponse = async (response, fallbackMessage) => {
      const contentType = response.headers.get("content-type") || "";
      if (!contentType.toLowerCase().includes("application/json")) {
        throw new Error(`${fallbackMessage}（HTTP ${response.status}）。`);
      }
      try {
        return await response.json();
      } catch {
        throw new Error(`${fallbackMessage}：服务返回内容格式错误。`);
      }
    };

    const textCell = (tag, value) => {
      const cell = document.createElement(tag);
      cell.textContent = value == null || value === "" ? "—" : String(value);
      return cell;
    };

    const renderOriginalTable = (rows) => {
      originalTable.replaceChildren();
      const table = document.createElement("table");
      table.className = "data-table preview-data-table";
      const width = Math.max(0, ...rows.map((row) => row.length));
      const thead = document.createElement("thead");
      const heading = document.createElement("tr");
      heading.append(textCell("th", "原始行"));
      for (let index = 0; index < width; index += 1) {
        heading.append(textCell("th", `列 ${index + 1}`));
      }
      thead.append(heading);
      const tbody = document.createElement("tbody");
      rows.forEach((row, rowIndex) => {
        const tr = document.createElement("tr");
        tr.append(textCell("td", rowIndex + 1));
        for (let index = 0; index < width; index += 1) {
          tr.append(textCell("td", row[index]));
        }
        tbody.append(tr);
      });
      table.append(thead, tbody);
      originalTable.append(table);
    };

    const renderConvertedTable = (rows, salesWindows) => {
      convertedTable.replaceChildren();
      const table = document.createElement("table");
      table.className = "data-table preview-data-table";
      const thead = document.createElement("thead");
      const heading = document.createElement("tr");
      ["原始行", "达人 ID", "达人昵称", "邮箱"].forEach((label) => {
        heading.append(textCell("th", label));
      });
      salesWindows.forEach((days) => {
        heading.append(textCell("th", `${days} 天销售额`));
      });
      heading.append(textCell("th", "预览结果"));
      thead.append(heading);
      const tbody = document.createElement("tbody");
      rows.forEach((row) => {
        const tr = document.createElement("tr");
        tr.append(
          textCell("td", row.rowNumber),
          textCell("td", row.creatorId),
          textCell("td", row.nickname),
          textCell("td", row.email)
        );
        salesWindows.forEach((days) => {
          tr.append(textCell("td", row.sales[String(days)]));
        });
        const status = textCell(
          "td",
          row.status === "SUCCESS"
            ? "成功"
            : row.status === "PARTIAL_SUCCESS"
              ? "部分成功"
              : "失败"
        );
        if (row.warnings?.length) status.title = row.warnings.join("；");
        tr.append(status);
        tbody.append(tr);
      });
      table.append(thead, tbody);
      convertedTable.append(table);
    };

    const renderPreview = (payload) => {
      const previousSourceFile = currentPreview?.fileName;
      currentPreview = payload;
      previewPanel.hidden = false;
      if (
        savedFileNameInput &&
        (!savedFileNameInput.value || previousSourceFile !== payload.fileName)
      ) {
        savedFileNameInput.value = payload.fileName;
      }
      previewTitle.textContent = `${payload.fileName} · 转换预览`;
      previewMeta.textContent =
        `Sheet：${payload.sheetName} · 当前规则 v${payload.ruleVersion} · ` +
        `预览将在约 ${Math.max(1, Math.round(payload.expiresInSeconds / 60))} 分钟后失效`;

      mappings.replaceChildren();
      payload.fieldMappings.forEach((label) => {
        const item = document.createElement("li");
        item.textContent = label;
        mappings.append(item);
      });

      history.replaceChildren();
      payload.ruleHistory.forEach((entry) => {
        const item = document.createElement("li");
        item.textContent = `v${entry.version} · ${entry.sourceLabel}` +
          (entry.userInstruction ? ` · ${entry.userInstruction}` : "");
        history.append(item);
      });

      warnings.replaceChildren();
      warnings.hidden = !payload.warnings.length;
      payload.warnings.forEach((warning) => {
        const item = document.createElement("p");
        item.textContent = warning;
        warnings.append(item);
      });
      renderOriginalTable(payload.originalSample);
      renderConvertedTable(payload.convertedSample, payload.salesWindows);
      previewPanel.scrollIntoView({ behavior: "smooth", block: "start" });
    };

    uploadForm?.addEventListener("submit", async (event) => {
      event.preventDefault();
      if (!fileInput?.files?.length) {
        setMessage("请先选择达人表格。", "error");
        return;
      }
      previewRequestController?.abort();
      const requestController = new AbortController();
      previewRequestController = requestController;
      const data = new FormData();
      data.append("file", fileInput.files[0]);
      if (!sheetField.hidden && sheetSelect.value) {
        data.append("sheet_name", sheetSelect.value);
      }
      setBusy(previewButton, true, "正在解析…");
      setMessage("");
      try {
        const response = await fetch(workbench.dataset.previewUrl, {
          method: "POST",
          body: data,
          headers: { "X-CSRFToken": csrfToken },
          signal: requestController.signal,
        });
        const payload = await readJsonResponse(response, "生成预览失败");
        if (!response.ok || !payload.success) {
          throw new Error(payload.error || "生成预览失败。");
        }
        if (payload.requiresSheetSelection) {
          sheetSelect.replaceChildren();
          const placeholder = document.createElement("option");
          placeholder.value = "";
          placeholder.textContent = "请选择 Sheet";
          placeholder.selected = true;
          placeholder.disabled = true;
          sheetSelect.append(placeholder);
          payload.sheetNames.forEach((sheetName) => {
            const option = document.createElement("option");
            option.value = sheetName;
            option.textContent = sheetName;
            sheetSelect.append(option);
          });
          sheetField.hidden = false;
          setMessage("检测到多个 Sheet，选择后将自动生成预览。", "info");
          return;
        }
        renderPreview(payload);
        setMessage("预览已生成，确认前不会写入数据库。", "success");
      } catch (error) {
        if (error.name === "AbortError") return;
        setMessage(error.message || "生成预览失败。", "error");
      } finally {
        if (previewRequestController === requestController) {
          previewRequestController = null;
          setBusy(previewButton, false);
        }
      }
    });

    fileInput?.addEventListener("change", () => {
      previewRequestController?.abort();
      currentPreview = null;
      previewPanel.hidden = true;
      sheetField.hidden = true;
      sheetSelect.replaceChildren();
      instructionForm.hidden = true;
      if (savedFileNameInput) savedFileNameInput.value = "";
      if (fileInput.files?.length) {
        setMessage("正在读取文件并生成预览…", "info");
        uploadForm.requestSubmit();
      } else {
        setMessage("");
      }
    });

    sheetSelect?.addEventListener("change", () => {
      if (sheetSelect.value && fileInput?.files?.length) {
        setMessage("正在读取所选 Sheet 并生成预览…", "info");
        uploadForm.requestSubmit();
      }
    });

    instructionToggle?.addEventListener("click", () => {
      instructionForm.hidden = !instructionForm.hidden;
      if (!instructionForm.hidden) {
        instructionForm.querySelector("textarea")?.focus();
      }
    });

    instructionForm?.addEventListener("submit", async (event) => {
      event.preventDefault();
      if (!currentPreview) return;
      const submit = instructionForm.querySelector("button[type=submit]");
      const instruction = instructionForm.querySelector("textarea")?.value?.trim();
      if (!instruction) {
        setMessage("请先填写转换修改要求。", "error");
        return;
      }
      const data = new FormData();
      data.append("instruction", instruction);
      setBusy(submit, true, "V4 Pro 正在理解…");
      setMessage("");
      try {
        const url = endpointFor(
          workbench.dataset.instructionUrlTemplate,
          currentPreview.previewId
        );
        const response = await fetch(url, {
          method: "POST",
          body: data,
          headers: { "X-CSRFToken": csrfToken },
        });
        const payload = await readJsonResponse(response, "重新生成预览失败");
        if (!response.ok || !payload.success) {
          throw new Error(payload.error || "重新生成预览失败。");
        }
        renderPreview(payload);
        instructionForm.querySelector("textarea").value = "";
        instructionForm.hidden = true;
        setMessage("已使用 V4 Pro 生成新规则，请再次审阅。", "success");
      } catch (error) {
        setMessage(error.message || "重新生成预览失败。", "error");
      } finally {
        setBusy(submit, false);
      }
    });

    confirmButton?.addEventListener("click", async () => {
      if (!currentPreview || !fileInput?.files?.length) {
        setMessage("预览文件已不可用，请重新选择文件。", "error");
        return;
      }
      const savedFileName = savedFileNameInput?.value?.trim();
      if (!savedFileName) {
        setMessage("请填写数据库记录名称。", "error");
        savedFileNameInput?.focus();
        return;
      }
      const data = new FormData();
      data.append("file", fileInput.files[0]);
      data.append("saved_file_name", savedFileName);
      setBusy(confirmButton, true, "正在保存…");
      setMessage("");
      try {
        const url = endpointFor(
          workbench.dataset.confirmUrlTemplate,
          currentPreview.previewId
        );
        const response = await fetch(url, {
          method: "POST",
          body: data,
          headers: { "X-CSRFToken": csrfToken },
        });
        const payload = await readJsonResponse(response, "确认导入失败");
        if (!response.ok || !payload.success) {
          throw new Error(payload.error || "确认导入失败。");
        }
        window.location.assign(payload.detailUrl);
      } catch (error) {
        setMessage(error.message || "确认导入失败。", "error");
        setBusy(confirmButton, false);
      }
    });
  }

  let outreachRequestController = null;

  const loadOutreachRecords = async (
    targetUrl,
    { updateHistory = true } = {}
  ) => {
    const panel = document.querySelector("[data-outreach-records]");
    if (!panel) return;
    outreachRequestController?.abort();
    const requestController = new AbortController();
    outreachRequestController = requestController;
    const destination = new URL(targetUrl, window.location.href);
    const fragmentUrl = new URL(panel.dataset.fragmentUrl, window.location.href);
    fragmentUrl.search = destination.search;
    panel.classList.add("is-loading");
    panel.setAttribute("aria-busy", "true");
    try {
      const response = await fetch(fragmentUrl, {
        headers: { Accept: "text/html" },
        cache: "no-store",
        signal: requestController.signal,
      });
      if (!response.ok) {
        throw new Error(`加载触达记录失败（HTTP ${response.status}）。`);
      }
      const html = await response.text();
      const parsed = new DOMParser().parseFromString(html, "text/html");
      const replacement = parsed.querySelector("[data-outreach-records]");
      if (!replacement) {
        throw new Error("触达记录响应格式错误。");
      }
      panel.replaceWith(replacement);
      if (updateHistory) {
        const historyUrl = new URL(destination, window.location.href);
        historyUrl.pathname = document.querySelector(
          "[data-record-filter-form]"
        )?.action
          ? new URL(
            document.querySelector("[data-record-filter-form]").action,
            window.location.href
          ).pathname
          : window.location.pathname;
        historyUrl.hash = "outreach-records";
        window.history.pushState({}, "", historyUrl);
      }
      replacement.scrollIntoView({ behavior: "smooth", block: "start" });
    } finally {
      if (outreachRequestController === requestController) {
        outreachRequestController = null;
      }
      document.querySelector("[data-outreach-records]")?.classList.remove(
        "is-loading"
      );
      document.querySelector("[data-outreach-records]")?.removeAttribute(
        "aria-busy"
      );
    }
  };

  document.addEventListener("click", (event) => {
    const link = event.target.closest(
      "[data-outreach-records] [data-record-navigation]"
    );
    if (
      !link
      || event.defaultPrevented
      || event.button !== 0
      || event.metaKey
      || event.ctrlKey
      || event.shiftKey
      || event.altKey
    ) {
      return;
    }
    event.preventDefault();
    loadOutreachRecords(link.href).catch((error) => {
      if (error.name !== "AbortError") window.location.assign(link.href);
    });
  });

  document.addEventListener("submit", (event) => {
    const form = event.target.closest(
      "[data-outreach-records] [data-record-filter-form]"
    );
    if (!form) return;
    event.preventDefault();
    const destination = new URL(form.action, window.location.href);
    destination.search = new URLSearchParams(new FormData(form)).toString();
    destination.hash = "outreach-records";
    loadOutreachRecords(destination).catch((error) => {
      if (error.name !== "AbortError") window.location.assign(destination);
    });
  });

  window.addEventListener("popstate", () => {
    if (
      window.location.hash === "#outreach-records"
      && document.querySelector("[data-outreach-records]")
    ) {
      loadOutreachRecords(window.location.href, {
        updateHistory: false,
      }).catch(() => window.location.reload());
    }
  });

  const monitor = document.querySelector("[data-task-monitor]");
  if (!monitor) return;
  const activeStatuses = new Set(["QUEUED", "IMPORTING", "PENDING", "RUNNING"]);
  if (!activeStatuses.has(monitor.dataset.status)) return;

  const refreshWhenChanged = async () => {
    try {
      const response = await fetch(monitor.dataset.statusUrl, {
        headers: { Accept: "application/json" },
        cache: "no-store",
      });
      if (!response.ok) return;
      const task = await response.json();
      const progress = Number(
        document.querySelector(".progress-ring strong")?.textContent?.replace("%", "")
      );
      if (task.status !== monitor.dataset.status || task.progress !== progress) {
        window.location.reload();
      }
    } catch {
      // Transient polling failures must not disrupt the rendered task page.
    }
  };
  window.setInterval(refreshWhenChanged, 4000);
})();
