import json
from pathlib import Path
from tempfile import TemporaryDirectory

from django.test import TestCase, override_settings
from django.urls import reverse
from openpyxl import Workbook

from .forms import CreatorAcquisitionTaskForm
from .models import CreatorAcquisitionTask, Product, RelatedCreator
from .services.opencode_runner import OpenCodeTaskRunner, build_task_prompt
from .services.xlsx_importer import import_creator_export, parse_metric


class TaskFormTests(TestCase):
    def test_product_limit_uses_ten_item_steps(self) -> None:
        valid = CreatorAcquisitionTaskForm({"product_limit": 20})
        invalid = CreatorAcquisitionTaskForm({"product_limit": 15})

        self.assertTrue(valid.is_valid())
        self.assertFalse(invalid.is_valid())


class TaskPageTests(TestCase):
    def test_dashboard_creates_persistent_creator_task(self) -> None:
        response = self.client.post(
            reverse("tasks:dashboard"),
            {"product_limit": 20},
        )

        task = CreatorAcquisitionTask.objects.get()
        self.assertRedirects(
            response,
            reverse("tasks:task-detail", kwargs={"task_id": task.id}),
        )
        self.assertEqual(task.product_limit, 20)
        self.assertEqual(task.status, CreatorAcquisitionTask.Status.PENDING)

    def test_detail_and_status_render_database_values(self) -> None:
        task = CreatorAcquisitionTask.objects.create(product_limit=10, progress=30)
        Product.objects.create(
            task=task,
            external_product_id="1732380858123457363",
            name="Test Product",
            product_url=(
                "https://www.chuhaijiang.com/app/discover/tiktok/products/"
                "1732380858123457363?country=US"
            ),
            total_sales_raw="3.9万",
        )

        detail = self.client.get(
            reverse("tasks:task-detail", kwargs={"task_id": task.id})
        )
        status = self.client.get(
            reverse("tasks:task-status", kwargs={"task_id": task.id})
        )

        self.assertEqual(detail.status_code, 200)
        self.assertContains(detail, "Test Product")
        self.assertEqual(status.json()["progress"], 30)
        self.assertEqual(status.json()["productCount"], 1)

    def test_waiting_task_can_be_requeued_after_login(self) -> None:
        task = CreatorAcquisitionTask.objects.create(
            product_limit=10,
            status=CreatorAcquisitionTask.Status.WAITING_CONFIRMATION,
            error_code="AUTH_REQUIRED",
        )

        response = self.client.post(
            reverse("tasks:task-retry", kwargs={"task_id": task.id})
        )

        task.refresh_from_db()
        self.assertRedirects(
            response,
            reverse("tasks:task-detail", kwargs={"task_id": task.id}),
        )
        self.assertEqual(task.status, CreatorAcquisitionTask.Status.PENDING)
        self.assertEqual(task.error_code, "")


class XlsxImporterTests(TestCase):
    def test_imports_exported_creator_rows(self) -> None:
        task = CreatorAcquisitionTask.objects.create(product_limit=10)
        product = Product.objects.create(
            task=task,
            external_product_id="1732380858123457363",
            name="Test Product",
            product_url=(
                "https://www.chuhaijiang.com/app/discover/tiktok/products/"
                "1732380858123457363?country=US"
            ),
        )

        with TemporaryDirectory() as directory:
            export_path = Path(directory) / "creators.xlsx"
            workbook = Workbook()
            sheet = workbook.active
            sheet.append(["数据导出自出海匠"])
            sheet.append([
                "达人昵称",
                "达人 ID",
                "TikTok Creator Url",
                "达人分类",
                "近 7 天销售额",
                "近 30 天销售额",
                "粉丝数",
                "email",
                "uid",
            ])
            sheet.append([
                "Creator One",
                "creator_one",
                "https://www.tiktok.com/@creator_one",
                "服装配饰",
                "$37428.90",
                "$117871.50",
                "7306.00",
                "creator@example.com",
                "https://www.chuhaijiang.com/tiktok/detail/tiktok_creator/1",
            ])
            workbook.save(export_path)
            workbook.close()

            with override_settings(EXPORTS_ROOT=Path(directory)):
                artifact = import_creator_export(
                    product,
                    {
                        "filePath": str(export_path),
                        "fileName": export_path.name,
                        "requestedRowCount": 100,
                        "exportedRowCount": 1,
                        "fileSizeBytes": export_path.stat().st_size,
                        "downloadedAt": "2026-07-26T00:00:00.000Z",
                    },
                )

        creator = RelatedCreator.objects.get()
        self.assertEqual(artifact.imported_row_count, 1)
        self.assertEqual(creator.creator_handle, "creator_one")
        self.assertEqual(creator.recent_7_day_revenue, parse_metric("$37428.90"))
        self.assertEqual(creator.email, "creator@example.com")


class PromptTests(TestCase):
    def test_prompt_pins_task_id_limit_and_step_contract(self) -> None:
        task = CreatorAcquisitionTask.objects.create(product_limit=30)
        prompt = build_task_prompt(task)

        self.assertIn(str(task.id), prompt)
        self.assertIn("maxRows=30", prompt)
        self.assertIn("chrome_export_related_creators", prompt)

    def test_successful_retry_clears_transient_step_failure(self) -> None:
        task = CreatorAcquisitionTask.objects.create(
            product_limit=10,
            status=CreatorAcquisitionTask.Status.RUNNING,
        )
        runner = OpenCodeTaskRunner(task)

        def event(response):
            return {
                "type": "tool_use",
                "timestamp": 1785000000000,
                "sessionID": "session-test",
                "part": {
                    "tool": "chrome-data_chrome_open_url",
                    "state": {
                        "status": "completed",
                        "input": {"taskId": str(task.id), "stepId": "open-url"},
                        "output": json.dumps(response),
                    },
                },
            }

        runner._handle_event(event({
            "success": False,
            "status": "FAILED",
            "data": None,
            "error": {
                "code": "TEMPORARY_FAILURE",
                "userMessage": "临时失败",
                "retryable": True,
            },
        }))
        runner._handle_event(event({
            "success": True,
            "status": "SUCCESS",
            "data": {"currentUrl": "https://www.chuhaijiang.com/"},
            "error": None,
        }))

        task.refresh_from_db()
        step = task.steps.get(step_id="open-url")
        self.assertEqual(task.status, CreatorAcquisitionTask.Status.RUNNING)
        self.assertEqual(task.error_code, "")
        self.assertEqual(step.status, "SUCCESS")
