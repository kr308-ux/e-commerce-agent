"""Run one queued task through OpenCode, DeepSeek, and the Chrome MCP."""

from __future__ import annotations

import json
import os
import re
import subprocess
from datetime import datetime
from typing import Any

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from tasks.models import CreatorAcquisitionTask, Product, TaskStep

from .xlsx_importer import import_creator_export, parse_count, parse_metric, product_id_from_url


TOOL_LABELS = {
    "chrome_connect": "连接 Chrome CDP",
    "chrome_open_url": "打开商品搜索页",
    "chrome_check_login": "检查出海匠登录状态",
    "chrome_open_selection": "打开选品模块",
    "chrome_open_product_module": "展开商品模块",
    "chrome_open_product_search": "打开商品搜索",
    "chrome_open_category": "打开类目筛选",
    "chrome_select_sports_outdoor": "选择运动与户外",
    "chrome_select_sports_apparel": "选择运动服饰",
    "chrome_collect_products_for_creators": "收集商品信息",
    "chrome_open_product_detail": "打开商品详情新标签",
    "chrome_open_related_creators": "定位关联达人模块",
    "chrome_export_related_creators": "导出并导入 100 条达人",
    "chrome_close_product_detail": "关闭商品详情标签",
    "chrome_disconnect": "断开 Chrome CDP",
}
SENSITIVE_PATTERN = re.compile(
    r"(sk-[a-zA-Z0-9_-]{8,}|bearer\s+[a-zA-Z0-9._-]{8,})",
    re.IGNORECASE,
)


def _redact(value: str) -> str:
    return SENSITIVE_PATTERN.sub("[REDACTED]", value)


def build_task_prompt(task: CreatorAcquisitionTask) -> str:
    task_id = str(task.id)
    return f"""
你是“获取达人数据”任务的唯一执行编排器。必须且只能调用 chrome-data MCP 工具，
禁止使用 bash、文件读写、网页搜索或其他工具。父任务 taskId 固定为 {task_id}。

严格按下列顺序执行，每次调用都使用给出的唯一 stepId：
1. chrome_connect，stepId=connect
2. chrome_open_url，stepId=open-url
3. chrome_check_login，stepId=check-login
4. chrome_open_selection，stepId=open-selection
5. chrome_open_product_module，stepId=open-product-module
6. chrome_open_product_search，stepId=open-product-search
7. chrome_open_category，stepId=open-category
8. chrome_select_sports_outdoor，stepId=select-sports-outdoor
9. chrome_select_sports_apparel，stepId=select-sports-apparel
10. chrome_collect_products_for_creators，stepId=collect-products，
    maxRows={task.product_limit}
11. 对 collect-products 返回的每个商品，严格按返回顺序逐个执行：
    a. chrome_open_product_detail，stepId=product-N-open，productUrl 使用原样返回值
    b. chrome_open_related_creators，stepId=product-N-creators，productId 使用 a 的返回值
    c. chrome_export_related_creators，stepId=product-N-export，productId 同上
    d. chrome_close_product_detail，stepId=product-N-close，productId 同上
    N 从 1 开始。上一个商品关闭成功后才能处理下一个。
12. chrome_disconnect，stepId=disconnect。

每个工具返回后必须检查 success 和 status。retryable=true 的瞬时失败最多重试两次；
导出超时且详情页变为错误页时，先关闭当前详情标签，再重新打开同一商品、定位达人模块
并重试导出，重试时沿用原 stepId。不可恢复的失败才停止正常流程并调用
chrome_disconnect。遇到 AUTH_REQUIRED / WAITING_CONFIRMATION 时立即停止并等待用户
登录，不得继续点击。

最终只输出一个 JSON 对象，不要使用 Markdown。字段：
success、status、requestedProducts、collectedProducts、exportedProducts、message。
""".strip()


def _tool_name(raw_name: str) -> str:
    return raw_name.split("_", 1)[1] if raw_name.startswith("chrome-data_") else raw_name


def _event_datetime(timestamp_ms: object) -> datetime:
    if isinstance(timestamp_ms, (int, float)):
        return datetime.fromtimestamp(timestamp_ms / 1000, tz=timezone.get_current_timezone())
    return timezone.now()


class OpenCodeTaskRunner:
    def __init__(self, task: CreatorAcquisitionTask):
        self.task = task
        self.completed_tool_calls = 0
        self.failed = False
        self.fatal_error = False
        self.waiting = False
        self.final_text = ""
        self.total_tool_calls = 11 + (task.product_limit * 4)

    def run(self) -> CreatorAcquisitionTask:
        self.task.status = CreatorAcquisitionTask.Status.RUNNING
        self.task.started_at = timezone.now()
        self.task.current_step = "启动 OpenCode / DeepSeek"
        self.task.model_name = settings.DEEPSEEK_MODEL
        self.task.opencode_session_id = ""
        self.task.error_code = ""
        self.task.error_message = ""
        self.task.save()

        environment = os.environ.copy()
        if not environment.get("DEEPSEEK_API_KEY"):
            return self._fail(
                "DEEPSEEK_KEY_MISSING",
                "本地 .env 中未配置 DEEPSEEK_API_KEY。",
            )

        command = [
            settings.OPENCODE_BINARY,
            "run",
            "--model",
            settings.DEEPSEEK_MODEL,
            "--format",
            "json",
            build_task_prompt(self.task),
        ]
        process = subprocess.Popen(
            command,
            cwd=settings.PROJECT_ROOT,
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            bufsize=1,
        )
        assert process.stdout is not None

        try:
            for raw_line in process.stdout:
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                self._handle_event(event)
            return_code = process.wait(timeout=settings.TASK_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            process.terminate()
            return self._fail("TASK_TIMEOUT", "获取达人数据任务执行超时。")

        if return_code != 0 and not self.failed and not self.waiting:
            return self._fail(
                "OPENCODE_FAILED",
                f"OpenCode 进程异常退出（状态码 {return_code}）。",
            )
        if self.waiting:
            self.task.status = CreatorAcquisitionTask.Status.WAITING_CONFIRMATION
            self.task.save()
            return self.task
        if self.failed:
            unresolved_failure = self.task.steps.filter(
                status=TaskStep.Status.FAILED
            ).exists()
            if self.fatal_error or unresolved_failure:
                return self.task
            self.failed = False
            self.task.status = CreatorAcquisitionTask.Status.RUNNING
            self.task.error_code = ""
            self.task.error_message = ""
            self.task.finished_at = None

        product_count = self.task.products.count()
        exported_count = self.task.products.filter(exports__isnull=False).distinct().count()
        if product_count < self.task.product_limit or exported_count < product_count:
            return self._fail(
                "INCOMPLETE_RESULT",
                f"任务未完整导出：已采集 {product_count} 个商品，已导出 {exported_count} 个商品。",
            )

        self.task.status = CreatorAcquisitionTask.Status.SUCCESS
        self.task.progress = 100
        self.task.current_step = "达人数据已写入数据库"
        self.task.final_summary = self.final_text
        self.task.finished_at = timezone.now()
        self.task.save()
        return self.task

    def _handle_event(self, event: dict[str, Any]) -> None:
        session_id = event.get("sessionID")
        if session_id and not self.task.opencode_session_id:
            self.task.opencode_session_id = str(session_id)
            self.task.save(update_fields=["opencode_session_id", "updated_at"])

        event_type = event.get("type")
        if event_type == "tool_use":
            self._handle_tool_event(event)
        elif event_type == "text":
            self.final_text += str(event.get("part", {}).get("text", ""))
        elif event_type == "error":
            error = event.get("error", {})
            data = error.get("data", {}) if isinstance(error, dict) else {}
            self._fail(
                "DEEPSEEK_API_ERROR",
                _redact(str(data.get("message") or error or "DeepSeek 调用失败。")),
            )

    @transaction.atomic
    def _handle_tool_event(self, event: dict[str, Any]) -> None:
        part = event.get("part", {})
        state = part.get("state", {})
        if state.get("status") != "completed":
            return
        operation = _tool_name(str(part.get("tool", "")))
        tool_input = state.get("input", {})
        step_id = str(tool_input.get("stepId") or f"tool-{self.completed_tool_calls + 1}")
        try:
            response = json.loads(state.get("output", "{}"))
        except (TypeError, json.JSONDecodeError):
            response = {
                "success": False,
                "status": "FAILED",
                "data": None,
                "error": {
                    "code": "INVALID_MCP_RESPONSE",
                    "userMessage": "MCP 返回了无法解析的结果。",
                },
            }

        self.completed_tool_calls += 1
        success = response.get("success") is True
        error = response.get("error") or {}
        timestamp = _event_datetime(event.get("timestamp"))
        TaskStep.objects.update_or_create(
            task=self.task,
            step_id=step_id,
            defaults={
                "sequence": self.completed_tool_calls,
                "operation": operation,
                "label": TOOL_LABELS.get(operation, operation),
                "status": TaskStep.Status.SUCCESS if success else TaskStep.Status.FAILED,
                "input_summary": {
                    key: value
                    for key, value in tool_input.items()
                    if key not in {"taskId", "stepId"}
                },
                "output_summary": self._output_summary(operation, response.get("data")),
                "error_code": str(error.get("code", "")),
                "error_message": _redact(str(error.get("userMessage", ""))),
                "started_at": timestamp,
                "finished_at": timestamp,
            },
        )

        self.task.progress = min(
            99,
            int(self.completed_tool_calls / max(self.total_tool_calls, 1) * 100),
        )
        self.task.current_step = TOOL_LABELS.get(operation, operation)

        if success:
            try:
                self._persist_tool_data(operation, response.get("data") or {})
            except Exception as error:
                self.failed = True
                self.task.status = CreatorAcquisitionTask.Status.FAILED
                self.task.error_code = "DATABASE_IMPORT_FAILED"
                self.task.error_message = _redact(
                    f"浏览器步骤成功，但结果写入数据库失败：{error}"
                )
                self.task.finished_at = timezone.now()
                TaskStep.objects.filter(task=self.task, step_id=step_id).update(
                    status=TaskStep.Status.FAILED,
                    error_code=self.task.error_code,
                    error_message=self.task.error_message,
                )
            if (
                not self.fatal_error
                and not self.task.steps.filter(status=TaskStep.Status.FAILED).exists()
            ):
                self.failed = False
                self.task.status = CreatorAcquisitionTask.Status.RUNNING
                self.task.error_code = ""
                self.task.error_message = ""
                self.task.finished_at = None
            self.task.save()
            return

        code = str(error.get("code", "MCP_STEP_FAILED"))
        message = _redact(str(error.get("userMessage") or "浏览器步骤执行失败。"))
        if response.get("status") == "WAITING_CONFIRMATION" or code == "AUTH_REQUIRED":
            self.waiting = True
            self.task.status = CreatorAcquisitionTask.Status.WAITING_CONFIRMATION
        else:
            self.failed = True
            self.task.status = CreatorAcquisitionTask.Status.FAILED
            self.task.finished_at = timezone.now()
        self.task.error_code = code
        self.task.error_message = message
        self.task.save()

    def _persist_tool_data(self, operation: str, data: dict[str, Any]) -> None:
        if operation == "chrome_collect_products_for_creators":
            country = str(data.get("country") or "")
            category = str(data.get("selectedCategory") or "")
            for row in data.get("products", []):
                product_url = str(row["productUrl"])
                total_sales_raw = str(row.get("totalSales") or "")
                recent_revenue_raw = str(row.get("recent7DayRevenue") or "")
                total_revenue_raw = str(row.get("totalRevenue") or "")
                creator_count_raw = str(row.get("relatedCreators") or "")
                Product.objects.update_or_create(
                    task=self.task,
                    external_product_id=product_id_from_url(product_url),
                    defaults={
                        "name": str(row.get("title") or ""),
                        "product_url": product_url,
                        "country": country,
                        "category": category,
                        "total_sales": parse_metric(total_sales_raw),
                        "total_sales_raw": total_sales_raw,
                        "recent_7_day_revenue": parse_metric(recent_revenue_raw),
                        "recent_7_day_revenue_raw": recent_revenue_raw,
                        "total_revenue": parse_metric(total_revenue_raw),
                        "total_revenue_raw": total_revenue_raw,
                        "related_creator_count": parse_count(creator_count_raw),
                        "related_creator_count_raw": creator_count_raw,
                    },
                )
        elif operation == "chrome_export_related_creators":
            product = Product.objects.get(
                task=self.task,
                external_product_id=str(data["productId"]),
            )
            import_creator_export(product, data)

    def _output_summary(self, operation: str, data: object) -> dict[str, Any]:
        if not isinstance(data, dict):
            return {}
        allowed = {
            "browserVersion",
            "pageCount",
            "loggedIn",
            "currentUrl",
            "selectedCategory",
            "returnedRowCount",
            "scannedPageCount",
            "productId",
            "openedInNewTab",
            "creatorCount",
            "tableVisible",
            "requestedRowCount",
            "exportedRowCount",
            "fileName",
            "filePath",
            "fileSizeBytes",
            "tabClosed",
            "disconnected",
        }
        summary = {key: value for key, value in data.items() if key in allowed}
        if operation == "chrome_collect_products_for_creators":
            summary["productCount"] = len(data.get("products", []))
        return summary

    def _fail(self, code: str, message: str) -> CreatorAcquisitionTask:
        self.failed = True
        self.fatal_error = True
        self.task.status = CreatorAcquisitionTask.Status.FAILED
        self.task.error_code = code
        self.task.error_message = _redact(message)
        self.task.finished_at = timezone.now()
        self.task.save()
        return self.task
