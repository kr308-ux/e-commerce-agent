"""Cross-platform keyboard helpers for Selenium elements."""

from __future__ import annotations

import platform

from selenium.webdriver.common.keys import Keys
from selenium.webdriver.remote.webelement import WebElement


def select_all_modifier(system_name: str | None = None) -> str:
    """Return the native select-all modifier for the requested platform."""
    current_system = system_name or platform.system()
    return Keys.COMMAND if current_system == "Darwin" else Keys.CONTROL


def replace_element_text(
    element: WebElement,
    value: str,
    *,
    system_name: str | None = None,
) -> None:
    """Select all existing text, delete it, then enter the replacement."""
    element.send_keys(select_all_modifier(system_name), "a")
    element.send_keys(Keys.BACKSPACE)
    element.send_keys(value)
