from __future__ import annotations

import unittest
import json
import tempfile
from pathlib import Path
from unittest.mock import Mock
from unittest.mock import patch

from ziniao_automation.dom_fallback import (
    DeepSeekDomFallback,
    is_dom_failure_message,
)


class DeepSeekDomFallbackTests(unittest.TestCase):
    def test_model_call_logs_full_input_and_output(self) -> None:
        response = Mock()
        response.status_code = 200
        response.headers = {"x-request-id": "request-1"}
        response.json.return_value = {
            "id": "completion-1",
            "model": "deepseek-v4-pro",
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {
                                "decision": "stop",
                                "reason": "页面仍在加载",
                            },
                            ensure_ascii=False,
                        )
                    }
                }
            ],
            "usage": {"total_tokens": 20},
        }
        response.raise_for_status.return_value = None
        with tempfile.TemporaryDirectory() as directory:
            fallback = DeepSeekDomFallback(
                api_key="test-key",
                task_id="task-log",
                session_id="session-log",
                log_root=directory,
            )
            with patch(
                "ziniao_automation.dom_fallback.requests.post",
                return_value=response,
            ):
                result = fallback._request_json(
                    system_prompt="完整系统提示",
                    payload={"dom": {"elements": [{"text": "查找达人"}]}},
                )

            paths = list(Path(directory).glob("*/model/*.jsonl"))
            self.assertEqual(len(paths), 1)
            record = json.loads(
                paths[0].read_text(encoding="utf-8").strip()
            )
            self.assertEqual(result["decision"], "stop")
            self.assertEqual(
                record["input"]["request"]["messages"][0]["content"],
                "完整系统提示",
            )
            self.assertEqual(
                record["input"]["domPayload"]["dom"]["elements"][0][
                    "text"
                ],
                "查找达人",
            )
            self.assertEqual(
                record["output"]["requestId"],
                "request-1",
            )
            self.assertEqual(
                record["output"]["response"]["usage"]["total_tokens"],
                20,
            )

    def test_only_dom_failures_are_eligible(self) -> None:
        self.assertTrue(is_dom_failure_message("页面未显示搜索框"))
        self.assertFalse(
            is_dom_failure_message("invitationGroupId 与任务不一致")
        )

    def test_locator_requires_one_visible_enabled_match(self) -> None:
        driver = Mock()
        driver.execute_script.return_value = {
            "url": "https://example.test",
            "title": "Find creators",
            "elements": [],
        }
        element = Mock()
        element.id = "creator-search"
        element.is_displayed.return_value = True
        element.is_enabled.return_value = True
        driver.find_elements.return_value = [element]
        fallback = DeepSeekDomFallback(api_key="test-key")
        fallback._request_json = Mock(
            return_value={
                "decision": "use_locator",
                "reason": "输入框 role 已变化",
                "candidates": [
                    {
                        "strategy": "css",
                        "value": "input[role='combobox']",
                        "confidence": 0.94,
                    }
                ],
            }
        )

        result = fallback.locate(
            driver,
            purpose="查找达人搜索框",
            attempted_selectors=(("css selector", "input.old"),),
        )

        self.assertIs(result, element)
        event = fallback.consume_events()[0]
        self.assertEqual(event["status"], "validated")
        self.assertEqual(event["model"], "deepseek-v4-pro")

    def test_locator_rejects_multiple_matches(self) -> None:
        driver = Mock()
        driver.execute_script.return_value = {
            "url": "https://example.test",
            "title": "Find creators",
            "elements": [],
        }
        first = Mock(id="first")
        second = Mock(id="second")
        for element in (first, second):
            element.is_displayed.return_value = True
            element.is_enabled.return_value = True
        driver.find_elements.return_value = [first, second]
        fallback = DeepSeekDomFallback(api_key="test-key")
        fallback._request_json = Mock(
            return_value={
                "decision": "use_locator",
                "candidates": [
                    {
                        "strategy": "css",
                        "value": "button",
                        "confidence": 0.99,
                    }
                ],
            }
        )

        result = fallback.locate(
            driver,
            purpose="唯一按钮",
            attempted_selectors=(),
        )

        self.assertIsNone(result)
        self.assertEqual(
            fallback.consume_events()[0]["status"],
            "no_unique_match",
        )

    def test_locator_honors_model_stop_even_if_candidates_are_present(
        self,
    ) -> None:
        driver = Mock()
        driver.execute_script.return_value = {
            "url": "https://example.test",
            "title": "Find creators",
            "elements": [],
        }
        fallback = DeepSeekDomFallback(api_key="test-key")
        fallback._request_json = Mock(
            return_value={
                "decision": "stop",
                "reason": "无法安全确认",
                "candidates": [
                    {
                        "strategy": "css",
                        "value": "button",
                        "confidence": 0.99,
                    }
                ],
            }
        )

        result = fallback.locate(
            driver,
            purpose="定位按钮",
            attempted_selectors=(),
        )

        self.assertIsNone(result)
        driver.find_elements.assert_not_called()
        self.assertEqual(
            fallback.consume_events()[0]["status"],
            "model_stopped",
        )


if __name__ == "__main__":
    unittest.main()
