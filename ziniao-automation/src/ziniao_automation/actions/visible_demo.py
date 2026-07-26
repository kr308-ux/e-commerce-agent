"""Visible, reversible proof that Selenium controls the store window."""

from __future__ import annotations

import time

from selenium.webdriver.remote.webdriver import WebDriver


BADGE_ID = "__ziniao_selenium_connection_demo__"


def run_visible_connection_demo(
    driver: WebDriver,
    *,
    duration_seconds: int | None = 30,
) -> None:
    """Show a local badge and scroll without changing remote store data."""
    if duration_seconds is not None and not 1 <= duration_seconds <= 300:
        raise ValueError("演示停留时间必须在 1 到 300 秒之间。")
    driver.maximize_window()
    original_scroll_y = driver.execute_script("return window.scrollY || 0;")
    driver.execute_script(
        """
        document.getElementById(arguments[0])?.remove();
        const badge = document.createElement("div");
        badge.id = arguments[0];
        badge.textContent = "Selenium 已连接 · 可见自动化演示";
        Object.assign(badge.style, {
          position: "fixed",
          top: "24px",
          right: "24px",
          zIndex: "2147483647",
          padding: "14px 18px",
          borderRadius: "10px",
          background: "#2563eb",
          color: "#ffffff",
          fontSize: "16px",
          fontWeight: "700",
          boxShadow: "0 8px 24px rgba(0, 0, 0, 0.24)"
        });
        document.body.appendChild(badge);
        """,
        BADGE_ID,
    )
    try:
        driver.execute_script(
            "window.scrollTo({top: Math.min(420, document.body.scrollHeight), "
            "behavior: 'smooth'});"
        )
        time.sleep(1.5)
        driver.execute_script(
            "window.scrollTo({top: arguments[0], behavior: 'smooth'});",
            original_scroll_y,
        )
        if duration_seconds is None:
            while True:
                time.sleep(1)
        else:
            time.sleep(duration_seconds)
    finally:
        # Ctrl-C may arrive after chromedriver or the browser has already
        # closed. Badge cleanup is optional and must not hide the original
        # interruption or prevent the store session from being stopped.
        try:
            driver.execute_script(
                "document.getElementById(arguments[0])?.remove();",
                BADGE_ID,
            )
        except Exception:
            pass
