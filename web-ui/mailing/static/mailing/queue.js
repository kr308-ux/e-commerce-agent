(() => {
  const form = document.querySelector("[data-mail-queue-form]");
  if (!form) return;

  const ruleInputs = Array.from(
    form.querySelectorAll("input[name='business_rule']"),
  );
  const importField = form.querySelector("[data-manual-import-field]");
  const importSelect = form.querySelector("select[name='import_task']");
  const limitField = form.querySelector("[data-queue-limit-field]");
  const limitInput = form.querySelector("input[name='limit']");
  const picker = form.querySelector("[data-candidate-picker]");
  const candidateBody = form.querySelector("[data-candidate-body]");
  const candidateCount = form.querySelector("[data-candidate-count]");
  const candidateNote = form.querySelector("[data-candidate-note]");
  const selectAllButton = form.querySelector("[data-select-all]");
  const dailyLimit = Number(form.dataset.dailyLimit || "100");
  let requestController = null;

  const currentRule = () =>
    ruleInputs.find((input) => input.checked)?.value || "CARD_SENT";

  const selectedCheckboxes = () =>
    Array.from(
      candidateBody.querySelectorAll(
        "input[name='selected_creator_ids']:checked",
      ),
    );

  const allCheckboxes = () =>
    Array.from(
      candidateBody.querySelectorAll("input[name='selected_creator_ids']"),
    );

  const syncSelection = () => {
    const checkboxes = allCheckboxes();
    const selectedCount = selectedCheckboxes().length;
    if (selectAllButton) {
      selectAllButton.textContent =
        checkboxes.length > 0 && selectedCount === checkboxes.length
          ? "取消全选"
          : "全选";
    }
    if (currentRule() === "MANUAL" && limitInput) {
      limitInput.value = String(Math.max(1, selectedCount));
    }
  };

  const addTextCell = (row, text, className = "") => {
    const cell = document.createElement("td");
    if (className) cell.className = className;
    cell.textContent = text || "—";
    row.append(cell);
    return cell;
  };

  const renderCandidates = ({ candidates = [], total = 0, truncated = false }) => {
    const manual = currentRule() === "MANUAL";
    candidateBody.replaceChildren();
    candidateCount.textContent = String(total);
    candidateNote.hidden = !truncated;

    if (!candidates.length) {
      const row = document.createElement("tr");
      const cell = addTextCell(
        row,
        manual
          ? "当前批次没有包含邮箱的达人。"
          : "暂无合作卡片发送成功且包含邮箱的达人。",
        "empty-cell",
      );
      cell.colSpan = 4;
      candidateBody.append(row);
      syncSelection();
      return;
    }

    candidates.forEach((candidate) => {
      const row = document.createElement("tr");
      const selectCell = document.createElement("td");
      selectCell.className = "mail-select-column";
      if (manual) {
        const checkbox = document.createElement("input");
        checkbox.type = "checkbox";
        checkbox.name = "selected_creator_ids";
        checkbox.value = candidate.id;
        checkbox.setAttribute(
          "aria-label",
          `选择 ${candidate.name || candidate.creatorId}`,
        );
        checkbox.addEventListener("change", syncSelection);
        selectCell.append(checkbox);
      } else {
        const eligible = document.createElement("span");
        eligible.className = "delivery-success";
        eligible.textContent = "✓";
        eligible.setAttribute("aria-label", "符合规则");
        selectCell.append(eligible);
      }
      row.append(selectCell);
      const nameCell = addTextCell(row, candidate.name);
      const strong = document.createElement("strong");
      strong.textContent = nameCell.textContent;
      nameCell.replaceChildren(strong);
      addTextCell(row, candidate.creatorId);
      addTextCell(row, candidate.email);
      candidateBody.append(row);
    });
    syncSelection();
  };

  const renderLoading = () => {
    const row = document.createElement("tr");
    const cell = addTextCell(row, "正在加载候选达人…", "empty-cell");
    cell.colSpan = 4;
    candidateBody.replaceChildren(row);
  };

  const loadCandidates = async () => {
    const rule = currentRule();
    if (!importSelect?.value) {
      renderCandidates({ candidates: [], total: 0, truncated: false });
      return;
    }
    requestController?.abort();
    requestController = new AbortController();
    const params = new URLSearchParams({ business_rule: rule });
    params.set("import_task", importSelect.value);
    renderLoading();
    try {
      const response = await fetch(
        `${form.dataset.candidatesUrl}?${params.toString()}`,
        {
          headers: { Accept: "application/json" },
          signal: requestController.signal,
        },
      );
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error || "候选达人加载失败。");
      renderCandidates(payload);
    } catch (error) {
      if (error.name === "AbortError") return;
      renderCandidates({ candidates: [], total: 0, truncated: false });
      window.alert(error.message || "候选达人加载失败。");
    }
  };

  const setRule = ({ refresh = true } = {}) => {
    const manual = currentRule() === "MANUAL";
    importField.hidden = false;
    limitField.hidden = manual;
    selectAllButton.hidden = !manual;
    form.querySelectorAll(".mail-business-rule").forEach((label) => {
      label.classList.toggle(
        "is-active",
        label.querySelector("input")?.checked === true,
      );
    });
    if (refresh) loadCandidates();
    else syncSelection();
  };

  ruleInputs.forEach((input) => {
    input.addEventListener("change", () => setRule());
  });
  importSelect?.addEventListener("change", loadCandidates);
  candidateBody.addEventListener("change", syncSelection);
  selectAllButton?.addEventListener("click", () => {
    const checkboxes = allCheckboxes();
    const shouldSelect = !(
      checkboxes.length > 0 &&
      selectedCheckboxes().length === checkboxes.length
    );
    checkboxes.slice(0, dailyLimit).forEach((checkbox) => {
      checkbox.checked = shouldSelect;
    });
    syncSelection();
  });
  form.addEventListener("submit", (event) => {
    if (!importSelect?.value) {
      event.preventDefault();
      window.alert("请先选择导入批次。");
      importSelect?.focus();
      return;
    }
    if (currentRule() !== "MANUAL") return;
    const selectedCount = selectedCheckboxes().length;
    if (!selectedCount) {
      event.preventDefault();
      window.alert("请至少选择一位达人。");
      picker?.scrollIntoView({ behavior: "smooth", block: "center" });
      return;
    }
    limitInput.value = String(selectedCount);
  });

  setRule({ refresh: false });
})();
