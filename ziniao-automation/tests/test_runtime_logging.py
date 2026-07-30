from __future__ import annotations

import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from shared.logger import JsonlAuditLogger


class JsonlAuditLoggerTests(unittest.TestCase):
    def test_daily_category_split_and_secret_redaction(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.dict(
                "os.environ",
                {
                    "DEEPSEEK_API_KEY": "sk-test-secret-value",
                    "ZINIAO_PASSWORD": "browser-password",
                    "RUNTIME_LOG_TIMEZONE": "Asia/Shanghai",
                },
            ):
                regular = JsonlAuditLogger(
                    root=root,
                    category="regular",
                    component="creator-contact",
                    task_id="task-1",
                    session_id="session-1",
                )
                model = JsonlAuditLogger(
                    root=root,
                    category="model",
                    component="deepseek",
                    task_id="task-1",
                    session_id="session-1",
                )
                regular_path = regular.write(
                    "operation_finished",
                    status="SUCCESS",
                    input_content={
                        "greetingMessage": "完整招呼语",
                        "password": "browser-password",
                    },
                    output_content={"success": True},
                )
                model_path = model.write(
                    "model_call",
                    status="SUCCESS",
                    input_content={
                        "prompt": "完整模型输入",
                        "authorization": (
                            "Bearer sk-test-secret-value"
                        ),
                    },
                    output_content={"content": "完整模型输出"},
                    metadata={
                        "rawHeaders": "Cookie: session=private-value"
                    },
                )

            assert regular_path is not None
            assert model_path is not None
            self.assertEqual(regular_path.parent.name, "regular")
            self.assertEqual(model_path.parent.name, "model")
            self.assertEqual(regular_path.parent.parent.name.count("-"), 2)
            regular_record = json.loads(
                regular_path.read_text(encoding="utf-8").strip()
            )
            model_record = json.loads(
                model_path.read_text(encoding="utf-8").strip()
            )
            self.assertEqual(
                regular_record["input"]["greetingMessage"],
                "完整招呼语",
            )
            self.assertEqual(
                regular_record["input"]["password"],
                "[REDACTED]",
            )
            self.assertEqual(
                model_record["input"]["prompt"],
                "完整模型输入",
            )
            self.assertEqual(
                model_record["input"]["authorization"],
                "[REDACTED]",
            )
            self.assertEqual(
                model_record["output"]["content"],
                "完整模型输出",
            )
            combined = (
                regular_path.read_text(encoding="utf-8")
                + model_path.read_text(encoding="utf-8")
            )
            self.assertNotIn("sk-test-secret-value", combined)
            self.assertNotIn("browser-password", combined)
            self.assertNotIn("private-value", combined)

    def test_concurrent_appends_remain_valid_json_lines(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            logger = JsonlAuditLogger(
                root=Path(directory),
                category="regular",
                component="creator-contact",
                task_id="task-concurrent",
            )
            threads = [
                threading.Thread(
                    target=logger.write,
                    args=("operation_finished",),
                    kwargs={
                        "status": "SUCCESS",
                        "output_content": {"index": index},
                    },
                )
                for index in range(40)
            ]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()

            paths = list(Path(directory).glob("*/*/*.jsonl"))
            self.assertEqual(len(paths), 1)
            records = [
                json.loads(line)
                for line in paths[0].read_text(
                    encoding="utf-8"
                ).splitlines()
            ]
            self.assertEqual(len(records), 40)
            self.assertEqual(
                {record["output"]["index"] for record in records},
                set(range(40)),
            )


if __name__ == "__main__":
    unittest.main()
