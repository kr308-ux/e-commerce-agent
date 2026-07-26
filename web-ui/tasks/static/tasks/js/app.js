(() => {
  const body = document.body;
  const sidebarToggle = document.querySelector("[data-sidebar-toggle]");
  const sidebarClose = document.querySelector("[data-sidebar-close]");

  const setSidebar = (isOpen) => {
    body.classList.toggle("sidebar-open", isOpen);
    sidebarToggle?.setAttribute("aria-expanded", String(isOpen));
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

  const monitor = document.querySelector("[data-task-monitor]");
  if (!monitor) return;
  const activeStatuses = new Set(["PENDING", "RUNNING"]);
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
      if (
        task.status !== monitor.dataset.status ||
        task.progress !== progress
      ) {
        window.location.reload();
      }
    } catch {
      // A transient status request must not disrupt the rendered task result.
    }
  };
  window.setInterval(refreshWhenChanged, 4000);
})();
