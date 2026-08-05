"""Use DeepSeek V4 Pro to translate user instructions into safe import rules."""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import time
import uuid
from typing import Any

from django.conf import settings

from shared.logger import JsonlAuditLogger, project_log_root

from .import_rules import IMPORT_RULE_SCHEMA, RuleValidationError, validate_rule


SENSITIVE_PATTERN = re.compile(
    r"(sk-[a-zA-Z0-9_-]{8,}|bearer\s+[a-zA-Z0-9._-]{8,})",
    re.IGNORECASE,
)


class RuleAdvisorError(RuntimeError):
    """Raised when the model cannot return a safe executable rule."""


def _redact(value: object) -> str:
    redacted = SENSITIVE_PATTERN.sub("[REDACTED]", str(value or ""))
    configured_key = os.environ.get("DEEPSEEK_API_KEY", "")
    if configured_key:
        redacted = redacted.replace(configured_key, "[REDACTED]")
    return redacted


def _extract_json_object(text: str) -> dict[str, Any]:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?\s*", "", stripped, flags=re.IGNORECASE)
        stripped = re.sub(r"\s*```$", "", stripped)
    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError:
        start = stripped.find("{")
        end = stripped.rfind("}")
        if start < 0 or end <= start:
            raise RuleAdvisorError("大模型没有返回可解析的转换规则。")
        try:
            parsed = json.loads(stripped[start : end + 1])
        except json.JSONDecodeError as error:
            raise RuleAdvisorError("大模型返回的转换规则不是有效 JSON。") from error
    if not isinstance(parsed, dict):
        raise RuleAdvisorError("大模型返回的转换规则必须是对象。")
    if isinstance(parsed.get("rule"), dict):
        return parsed["rule"]
    return parsed


def _opencode_text(stdout: str) -> str:
    parts: list[str] = []
    for raw_line in stdout.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("type") == "text":
            part = event.get("part", {})
            parts.append(str(part.get("text", "")))
    return "".join(parts).strip()


class ImportRuleAdvisor:
    """Small, stateless model adapter; it never receives the source file."""

    def revise_rule(
        self,
        *,
        instruction: str,
        rows: tuple[tuple[str, ...], ...],
        current_rule: dict[str, Any],
        task_id: str = "",
    ) -> dict[str, Any]:
        normalized_instruction = str(instruction or "").strip()
        if not normalized_instruction:
            raise RuleAdvisorError("请先描述需要如何修改转换结果。")
        if len(normalized_instruction) > 2000:
            raise RuleAdvisorError("修改说明不能超过 2000 个字符。")
        if not os.environ.get("DEEPSEEK_API_KEY"):
            raise RuleAdvisorError("本地环境未配置 DEEPSEEK_API_KEY。")

        header_row = int(current_rule["header_row"])
        sample_start = max(0, header_row - 1)
        samples = [
            list(row)
            for row in rows[sample_start : sample_start + settings.IMPORT_PREVIEW_ROWS + 1]
        ]
        prompt = self._build_prompt(
            instruction=normalized_instruction,
            samples=samples,
            current_rule=current_rule,
        )
        session_id = f"opencode_rule_{uuid.uuid4().hex}"
        model_logger = JsonlAuditLogger(
            root=project_log_root(settings.PROJECT_ROOT),
            category="model",
            component="opencode",
            task_id=task_id,
            session_id=session_id,
        )
        environment = os.environ.copy()
        command: list[str] = []
        configured_model = str(settings.IMPORT_RULE_MODEL or "").strip()
        model_name = configured_model.rsplit("/", 1)[-1] or "deepseek-v4-pro"
        model_ref = f"project-deepseek/{model_name}"
        started_at = time.perf_counter()
        try:
            with tempfile.TemporaryDirectory(
                prefix="creator-import-rule-"
            ) as isolated_directory:
                # 写入临时项目配置，使 opencode 使用 DEEPSEEK_API_KEY 环境变量
                # 而非全局 auth.json，实现项目级密钥隔离
                temp_config = {
                    "provider": {
                        "project-deepseek": {
                            "models": {
                                model_name: {"name": model_name}
                            },
                            "npm": "@ai-sdk/openai-compatible",
                            "options": {
                                "apiKey": os.environ["DEEPSEEK_API_KEY"],
                                "baseURL": "https://api.deepseek.com/v1",
                            },
                        }
                    }
                }
                (Path(isolated_directory) / "opencode.json").write_text(
                    json.dumps(temp_config, ensure_ascii=False)
                )

                command = [
                    settings.OPENCODE_BINARY,
                    "run",
                    "--pure",
                    "--dir",
                    isolated_directory,
                    "--model",
                    model_ref,
                    "--format",
                    "json",
                    prompt,
                ]
                result = subprocess.run(
                    command,
                    cwd=settings.PROJECT_ROOT,
                    env=environment,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding="utf-8",
                    timeout=settings.IMPORT_RULE_TIMEOUT_SECONDS,
                    check=False,
                )
        except FileNotFoundError as error:
            model_logger.write(
                "model_call",
                status="ERROR",
                operation="opencode.run",
                input_content={
                    "command": command[:-1],
                    "prompt": prompt,
                    "model": model_ref,
                },
                error={
                    "type": type(error).__name__,
                    "message": str(error),
                },
                duration_ms=(time.perf_counter() - started_at) * 1000,
                metadata={"provider": "opencode+deepseek"},
            )
            raise RuleAdvisorError("未找到 OpenCode 可执行程序。") from error
        except subprocess.TimeoutExpired as error:
            model_logger.write(
                "model_call",
                status="ERROR",
                operation="opencode.run",
                input_content={
                    "command": command[:-1],
                    "prompt": prompt,
                    "model": model_ref,
                },
                output_content={
                    "rawOutput": _redact(error.stdout or ""),
                },
                error={
                    "type": type(error).__name__,
                    "message": str(error),
                },
                duration_ms=(time.perf_counter() - started_at) * 1000,
                metadata={"provider": "opencode+deepseek"},
            )
            raise RuleAdvisorError("大模型解析修改要求超时，请重试。") from error

        model_logger.write(
            "model_call",
            status="SUCCESS" if result.returncode == 0 else "FAILED",
            operation="opencode.run",
                input_content={
                    "command": command[:-1],
                    "prompt": prompt,
                    "model": model_ref,
                },
            output_content={
                "returnCode": result.returncode,
                "rawOutput": result.stdout,
                "parsedText": _opencode_text(result.stdout),
            },
            duration_ms=(time.perf_counter() - started_at) * 1000,
            metadata={"provider": "opencode+deepseek"},
        )
        if result.returncode != 0:
            raise RuleAdvisorError(
                f"大模型解析失败：{_redact(result.stdout)[-500:]}"
            )
        text = _opencode_text(result.stdout)
        if not text:
            raise RuleAdvisorError("大模型没有返回转换规则。")
        proposed = _extract_json_object(text)
        try:
            return validate_rule(proposed, rows)
        except RuleValidationError as error:
            raise RuleAdvisorError(f"大模型规则校验失败：{error}") from error

    @staticmethod
    def _build_prompt(
        *,
        instruction: str,
        samples: list[list[str]],
        current_rule: dict[str, Any],
    ) -> str:
        return (
            "你只负责修改达人表格的结构化转换规则，不得调用任何工具，不得生成代码，"
            "不得猜测样本中不存在的数据。达人 ID 必须原样保留；销量、订单数绝不能映射"
            "为销售额。邮箱列映射后，column_transforms 中对应列必须设为 extract_email。"
            "只输出一个符合 JSON Schema 的 JSON 对象，不要 Markdown，"
            "不要解释。\n\n"
            f"用户修改要求：{instruction}\n\n"
            f"当前规则：{json.dumps(current_rule, ensure_ascii=False)}\n\n"
            f"表头及少量样本：{json.dumps(samples, ensure_ascii=False)}\n\n"
            f"JSON Schema：{json.dumps(IMPORT_RULE_SCHEMA, ensure_ascii=False)}"
        )
