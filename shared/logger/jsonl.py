"""Append-only JSONL audit logs grouped by local date and category."""

from __future__ import annotations

import json
import os
import re
import threading
import uuid
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


SCHEMA_VERSION = 1
DEFAULT_TIMEZONE = "Asia/Shanghai"
_FILE_NAME_PATTERN = re.compile(r"[^A-Za-z0-9._-]+")
_BEARER_PATTERN = re.compile(
    r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{8,}"
)
_API_KEY_PATTERN = re.compile(r"\bsk-[A-Za-z0-9_-]{8,}\b")
_COOKIE_HEADER_PATTERN = re.compile(
    r"(?im)\b(cookie|set-cookie)\s*:\s*[^\r\n]+"
)
_SENSITIVE_KEYS = {
    "api_key",
    "apikey",
    "authorization",
    "client_secret",
    "cookie",
    "cookies",
    "deepseek_api_key",
    "password",
    "refresh_token",
    "secret",
    "set_cookie",
    "ziniao_password",
}
_THREAD_LOCK = threading.Lock()


def project_log_root(project_root: str | os.PathLike[str] | Path) -> Path:
    configured = str(os.getenv("RUNTIME_LOG_ROOT", "") or "").strip()
    if configured:
        path = Path(configured).expanduser()
        if not path.is_absolute():
            path = Path(project_root) / path
        return path.resolve()
    return (Path(project_root) / "logs").resolve()


def _timezone() -> ZoneInfo:
    name = str(
        os.getenv("RUNTIME_LOG_TIMEZONE", DEFAULT_TIMEZONE)
        or DEFAULT_TIMEZONE
    ).strip()
    try:
        return ZoneInfo(name)
    except Exception:
        return ZoneInfo(DEFAULT_TIMEZONE)


def _secret_values() -> tuple[str, ...]:
    values = []
    for name in (
        "DEEPSEEK_API_KEY",
        "ZINIAO_PASSWORD",
        "DJANGO_SECRET_KEY",
    ):
        value = str(os.getenv(name, "") or "")
        if value:
            values.append(value)
    return tuple(values)


def _redact_text(value: object) -> str:
    text = str(value or "")
    for secret in _secret_values():
        text = text.replace(secret, "[REDACTED]")
    text = _BEARER_PATTERN.sub("Bearer [REDACTED]", text)
    text = _API_KEY_PATTERN.sub("[REDACTED]", text)
    return _COOKIE_HEADER_PATTERN.sub(
        lambda match: f"{match.group(1)}: [REDACTED]",
        text,
    )


def redact(value: object, *, key: str = "") -> Any:
    normalized_key = str(key or "").strip().casefold().replace("-", "_")
    if normalized_key in _SENSITIVE_KEYS:
        return "[REDACTED]"
    if isinstance(value, Mapping):
        return {
            str(item_key): redact(item_value, key=str(item_key))
            for item_key, item_value in value.items()
        }
    if isinstance(value, (list, tuple, set)):
        return [redact(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, bytes):
        return {
            "encoding": "hex",
            "length": len(value),
            "sha256Unavailable": True,
        }
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return _redact_text(value)


def _safe_name(value: object, *, fallback: str) -> str:
    normalized = _FILE_NAME_PATTERN.sub(
        "-",
        str(value or "").strip(),
    ).strip("-._")
    return normalized[:160] or fallback


class JsonlAuditLogger:
    """Write one complete, redacted JSON event per append operation."""

    def __init__(
        self,
        *,
        root: str | os.PathLike[str] | Path,
        category: str,
        component: str,
        task_id: object = "",
        session_id: object = "",
    ) -> None:
        normalized_category = str(category or "").strip().casefold()
        if normalized_category not in {"regular", "model"}:
            raise ValueError("日志 category 只能是 regular 或 model。")
        self.root = Path(root)
        self.category = normalized_category
        self.component = _safe_name(component, fallback="runtime")
        self.task_id = str(task_id or "").strip()
        self.session_id = str(session_id or "").strip()
        self.run_id = uuid.uuid4().hex

    def bind(
        self,
        *,
        task_id: object | None = None,
        session_id: object | None = None,
    ) -> "JsonlAuditLogger":
        if task_id is not None:
            self.task_id = str(task_id or "").strip()
        if session_id is not None:
            self.session_id = str(session_id or "").strip()
        return self

    def _path(self, occurred_at: datetime) -> Path:
        day = occurred_at.strftime("%Y-%m-%d")
        correlation = _safe_name(
            self.task_id or self.session_id or self.run_id,
            fallback=self.run_id,
        )
        return (
            self.root
            / day
            / self.category
            / f"{self.component}-{correlation}.jsonl"
        )

    def write(
        self,
        event: str,
        *,
        status: str = "",
        task_id: object | None = None,
        session_id: object | None = None,
        target_id: object = "",
        operation: object = "",
        input_content: object = None,
        output_content: object = None,
        error: object = None,
        duration_ms: float | int | None = None,
        metadata: object = None,
    ) -> Path | None:
        occurred_at = datetime.now(_timezone())
        effective_task_id = (
            self.task_id if task_id is None else str(task_id or "").strip()
        )
        effective_session_id = (
            self.session_id
            if session_id is None
            else str(session_id or "").strip()
        )
        record: dict[str, Any] = {
            "schemaVersion": SCHEMA_VERSION,
            "timestamp": occurred_at.isoformat(),
            "timezone": str(occurred_at.tzinfo),
            "category": self.category,
            "component": self.component,
            "event": str(event or ""),
            "status": str(status or ""),
            "taskId": effective_task_id,
            "targetId": str(target_id or ""),
            "sessionId": effective_session_id,
            "operation": str(operation or ""),
            "runId": self.run_id,
        }
        if duration_ms is not None:
            record["durationMs"] = round(float(duration_ms), 3)
        if input_content is not None:
            record["input"] = redact(input_content)
        if output_content is not None:
            record["output"] = redact(output_content)
        if error is not None:
            record["error"] = redact(error)
        if metadata is not None:
            record["metadata"] = redact(metadata)
        line = (
            json.dumps(
                record,
                ensure_ascii=False,
                separators=(",", ":"),
                default=lambda item: _redact_text(item),
            )
            + "\n"
        ).encode("utf-8")
        path = self._path(occurred_at)
        try:
            with _THREAD_LOCK:
                path.parent.mkdir(parents=True, exist_ok=True)
                descriptor = os.open(
                    path,
                    os.O_APPEND | os.O_CREAT | os.O_WRONLY,
                    0o600,
                )
                try:
                    os.write(descriptor, line)
                finally:
                    os.close(descriptor)
            return path
        except OSError:
            return None
