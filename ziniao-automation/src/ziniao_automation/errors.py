"""Typed errors for the Ziniao WebDriver integration."""

from __future__ import annotations


class ZiniaoError(RuntimeError):
    """Base error safe to show in local command output."""


class ZiniaoConfigurationError(ZiniaoError):
    """Required local configuration is missing or invalid."""


class ZiniaoConnectionError(ZiniaoError):
    """The local Ziniao WebDriver HTTP service is unavailable."""


class ZiniaoApiError(ZiniaoError):
    """The local Ziniao client returned a failed action result."""

    def __init__(self, action: str, status_code: int | None, message: str):
        self.action = action
        self.status_code = status_code
        super().__init__(
            f"紫鸟操作 {action} 失败（statusCode={status_code}）：{message}"
        )


class ZiniaoStoreSelectionError(ZiniaoError):
    """A unique authorized store could not be selected."""


class ZiniaoDriverError(ZiniaoError):
    """A matching, verified ChromeDriver could not be prepared."""


class ZiniaoWorkflowError(ZiniaoError):
    """A visible browser workflow step could not be completed or verified."""
