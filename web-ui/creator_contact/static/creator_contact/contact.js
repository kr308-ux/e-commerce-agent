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

  const setupProductFiltering = () => {
    if (!taskForm) return null;
    const taskSelect = taskForm.querySelector("[data-source-task-select]");
    const productSelect = taskForm.querySelector("[data-product-select]");
    if (!taskSelect || !productSelect) return null;

    const placeholder = productSelect.options[0]?.cloneNode(true);
    const productCatalog = Array.from(productSelect.options)
      .slice(1)
      .map((option) => option.cloneNode(true));
    const initiallySelectedProduct = productCatalog.find((option) => option.selected);
    if (initiallySelectedProduct?.dataset.sourceTask) {
      taskSelect.value = initiallySelectedProduct.dataset.sourceTask;
    }

    const filterProducts = ({ chooseFirst = true } = {}) => {
      const taskId = taskSelect.value;
      const previousValue = productSelect.value;
      const matching = productCatalog.filter(
        (option) => !taskId || option.dataset.sourceTask === taskId
      );
      const fragment = document.createDocumentFragment();
      if (placeholder) fragment.append(placeholder.cloneNode(true));
      matching.forEach((option, index) => {
        const clone = option.cloneNode(true);
        const label = clone.dataset.productLabel || clone.textContent.trim();
        clone.textContent = `第 ${index + 1} 个 · ${label}`;
        fragment.append(clone);
      });
      productSelect.replaceChildren(fragment);

      const previousStillAvailable = Array.from(productSelect.options).some(
        (option) => option.value === previousValue
      );
      if (previousStillAvailable) {
        productSelect.value = previousValue;
      } else if (chooseFirst && matching.length > 0) {
        productSelect.value = matching[0].value;
      }
      productSelect.disabled = matching.length === 0;
      return previousValue !== productSelect.value;
    };

    const initialSelectionChanged = filterProducts();
    return { taskSelect, productSelect, filterProducts, initialSelectionChanged };
  };

  const setupCandidatePreview = (productFiltering) => {
    if (!taskForm || !productFiltering) return;
    const { taskSelect, productSelect, filterProducts } = productFiltering;
    const topNInput = taskForm.querySelector('[name="top_n"]');
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

    const setState = (message, loading = false) => {
      if (fetchState) fetchState.textContent = message;
      preview.classList.toggle("is-loading", loading);
      preview.setAttribute("aria-busy", String(loading));
    };

    const emptyRow = (message) => {
      const row = document.createElement("tr");
      const cell = textNode("td", message, "empty-cell");
      cell.colSpan = 6;
      row.append(cell);
      body.replaceChildren(row);
      if (summary) summary.textContent = "0 位待联系";
    };

    const renderCandidates = (candidates) => {
      const fragment = document.createDocumentFragment();
      candidates.forEach((candidate, index) => {
        const row = document.createElement("tr");

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
        const videoCell = textNode(
          "td",
          String(valueFrom(candidate, "related_video_count", "relatedVideoCount") ?? "—")
        );
        const stateCell = document.createElement("td");
        const ready = textNode("span", "", "candidate-ready");
        ready.append(textNode("i", ""), document.createTextNode("待联系"));
        stateCell.append(ready);

        row.append(
          rankCell,
          creatorCell,
          revenue7Cell,
          revenue30Cell,
          videoCell,
          stateCell
        );
        fragment.append(row);
      });

      if (candidates.length === 0) {
        emptyRow("没有符合条件的未联系达人，请调整商品或人数。");
      } else {
        body.replaceChildren(fragment);
        if (summary) summary.textContent = `${candidates.length} 位待联系`;
      }
    };

    const fetchCandidates = async () => {
      const productId = productSelect.value;
      if (!productId) {
        emptyRow("请选择包含已导入达人数据的商品。");
        setState("等待选择商品");
        return;
      }

      requestController?.abort();
      requestController = new AbortController();
      const query = new URLSearchParams({
        product_id: productId,
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
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const payload = await response.json();
        const candidates =
          valueFrom(payload, "creators", "candidates", "results", "targets") ?? [];
        renderCandidates(Array.isArray(candidates) ? candidates : []);
        const excluded = valueFrom(payload, "excluded_count", "excludedCount") ?? 0;
        if (excludedCount) excludedCount.textContent = String(excluded);
        setState("预览已更新");
      } catch (error) {
        if (error.name === "AbortError") return;
        setState("预览更新失败，提交时将由服务端再次校验");
      }
    };

    const scheduleFetch = () => {
      window.clearTimeout(debounceTimer);
      debounceTimer = window.setTimeout(fetchCandidates, 180);
    };

    taskSelect.addEventListener("change", () => {
      filterProducts();
      scheduleFetch();
    });
    productSelect.addEventListener("change", scheduleFetch);
    topNInput?.addEventListener("input", scheduleFetch);
    topNInput?.addEventListener("change", scheduleFetch);
    storeInput?.addEventListener("change", scheduleFetch);

    if (productFiltering.initialSelectionChanged) scheduleFetch();
  };

  const setupConfirmationGate = () => {
    if (!taskForm) return;
    const confirmations = Array.from(
      taskForm.querySelectorAll(
        '[name="confirm_send_greeting"], [name="confirm_send_invitation"], [name="confirm_send_card"]'
      )
    );
    const confirmAll = taskForm.querySelector("[data-confirm-all]");
    const createButton = taskForm.querySelector("[data-create-contact-task]");
    const defaultAction = taskForm.querySelector('input[type="hidden"][name="action"]');

    const update = () => {
      if (!createButton) return;
      const allConfirmed =
        confirmations.length > 0 &&
        confirmations.every((confirmation) => confirmation.checked);
      createButton.disabled = !allConfirmed;
      createButton.setAttribute("aria-disabled", String(!allConfirmed));
      if (confirmAll) {
        confirmAll.checked = allConfirmed;
        confirmAll.indeterminate =
          !allConfirmed && confirmations.some((confirmation) => confirmation.checked);
      }
    };

    confirmations.forEach((confirmation) =>
      confirmation.addEventListener("change", update)
    );
    confirmAll?.addEventListener("change", () => {
      confirmations.forEach((confirmation) => {
        confirmation.checked = confirmAll.checked;
      });
      update();
    });
    update();

    taskForm.addEventListener("submit", (event) => {
      const submittedAction = event.submitter?.value || "create_task";
      if (defaultAction) defaultAction.value = submittedAction;
      if (
        submittedAction === "create_task" &&
        confirmations.some((confirmation) => !confirmation.checked)
      ) {
        event.preventDefault();
        confirmations.find((confirmation) => !confirmation.checked)?.focus();
      }
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

  setupGreetingEditor();
  const productFiltering = setupProductFiltering();
  setupCandidatePreview(productFiltering);
  setupConfirmationGate();
  setupTaskMonitor();
})();
