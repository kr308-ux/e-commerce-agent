(() => {
  const body = document.body;
  const sidebarToggle = document.querySelector("[data-sidebar-toggle]");
  const sidebarClose = document.querySelector("[data-sidebar-close]");
  const toast = document.querySelector(".toast");
  const toastMessage = document.querySelector(".toast-message");
  let toastTimer;

  const showToast = (message) => {
    if (!toast || !toastMessage) return;
    window.clearTimeout(toastTimer);
    toastMessage.textContent = message;
    toast.classList.add("is-visible");
    toast.setAttribute("aria-hidden", "false");
    toastTimer = window.setTimeout(() => {
      toast.classList.remove("is-visible");
      toast.setAttribute("aria-hidden", "true");
    }, 3200);
  };

  const setSidebar = (isOpen) => {
    body.classList.toggle("sidebar-open", isOpen);
    sidebarToggle?.setAttribute("aria-expanded", String(isOpen));
  };

  sidebarToggle?.addEventListener("click", () => {
    setSidebar(!body.classList.contains("sidebar-open"));
  });
  sidebarClose?.addEventListener("click", () => setSidebar(false));

  document.querySelectorAll("[data-demo-action]").forEach((element) => {
    element.addEventListener("click", (event) => {
      event.preventDefault();
      showToast(element.dataset.demoAction);
    });
  });

  document.querySelectorAll(".task-type-card input").forEach((input) => {
    input.addEventListener("change", () => {
      document
        .querySelectorAll(".task-type-card")
        .forEach((card) => card.classList.remove("is-selected"));
      input.closest(".task-type-card")?.classList.add("is-selected");
    });
  });

  const sampleButton = document.querySelector("[data-fill-sample]");
  const instruction = document.querySelector("#task-instruction");
  sampleButton?.addEventListener("click", () => {
    if (!instruction) return;
    instruction.value =
      "打开当前 Chrome 中的达人广场，采集最近 30 天销售额超过 10 万的美妆达人，并整理昵称、账号、粉丝数和销售额。";
    instruction.focus();
  });

  document.querySelector("[data-demo-form]")?.addEventListener("submit", (event) => {
    event.preventDefault();
    showToast("演示任务已准备好；后续接入 Worker 后才会真正执行。");
  });

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") setSidebar(false);
  });
})();
