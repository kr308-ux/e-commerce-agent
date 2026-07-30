from __future__ import annotations

import io
import json
import tempfile
import zipfile
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from openpyxl import Workbook

from .models import (
    Creator,
    CreatorSalesMetric,
    ImportRowError,
    ImportTask,
)
from .services.ai_rule_advisor import ImportRuleAdvisor
from .services.import_rules import (
    build_initial_rule,
    extract_window_days,
    is_sales_header,
)
from .services.import_runner import run_import_task, task_source_path
from .services.import_transformer import (
    extract_email,
    parse_money,
    transform_rows,
)
from .services.preview_service import preview_store
from .services.spreadsheet_reader import read_sheet, validate_upload


def xlsx_bytes(*rows, sheets: dict[str, list[list[object]]] | None = None) -> bytes:
    workbook = Workbook()
    if sheets is None:
        worksheet = workbook.active
        worksheet.title = "Creators"
        for row in rows:
            worksheet.append(list(row))
    else:
        workbook.remove(workbook.active)
        for sheet_name, sheet_rows in sheets.items():
            worksheet = workbook.create_sheet(sheet_name)
            for row in sheet_rows:
                worksheet.append(row)
    stream = io.BytesIO()
    workbook.save(stream)
    return stream.getvalue()


def upload(name: str, content: bytes) -> SimpleUploadedFile:
    mime = {
        ".csv": "text/csv",
        ".xls": "application/vnd.ms-excel",
        ".xlsx": (
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        ),
    }[Path(name).suffix.casefold()]
    return SimpleUploadedFile(name, content, content_type=mime)


def xlsx_with_invalid_empty_fill(
    content: bytes,
    *,
    fill_markup: bytes = b"<fill></fill>",
) -> bytes:
    source_stream = io.BytesIO(content)
    output_stream = io.BytesIO()
    with zipfile.ZipFile(source_stream) as source:
        with zipfile.ZipFile(output_stream, "w") as output:
            for item in source.infolist():
                item_content = source.read(item.filename)
                if item.filename == "xl/styles.xml":
                    item_content = item_content.replace(
                        b'<fills count="2">',
                        b'<fills count="3">' + fill_markup,
                    )
                elif item.filename == "xl/worksheets/sheet1.xml":
                    item_content = item_content.replace(
                        b'<dimension ref="A1:B2"/>',
                        b'<dimension ref="A1"/>',
                    )
                output.writestr(item, item_content)
    return output_stream.getvalue()


class SpreadsheetRuleTests(TestCase):
    def test_header_detection_email_and_sales_conversion(self):
        rows = (
            ("2026 Creator Report",),
            ("Exported", "2026-07-29"),
            ("达人编号", "达人名称", "Contact", "7D GMV", "30天销量"),
            ("00123", "Alice", "WhatsApp 1 alice@example.com", "1.2K", "50"),
        )
        rule, warnings = build_initial_rule(rows)
        self.assertEqual(rule["header_row"], 3)
        self.assertEqual(rule["creator_id_column"], 0)
        self.assertEqual(
            [(item["window_days"], item["source_column"]) for item in rule["sales_columns"]],
            [(7, 3)],
        )
        self.assertEqual(warnings, [])

        converted = list(transform_rows(rows, rule))
        self.assertEqual(converted[0].creator_id, "00123")
        self.assertEqual(converted[0].email, "alice@example.com")
        self.assertEqual(converted[0].sales, {7: Decimal("1200.0")})
        self.assertEqual(converted[0].status, "SUCCESS")

    def test_amount_and_sales_header_safety(self):
        self.assertEqual(parse_money("¥1,200"), Decimal("1200"))
        self.assertEqual(parse_money("$1.2万"), Decimal("12000.0"))
        self.assertEqual(parse_money("1.5M"), Decimal("1500000.0"))
        self.assertTrue(is_sales_header("30D Revenue"))
        self.assertFalse(is_sales_header("30D Order Count"))
        self.assertFalse(is_sales_header("7天销量"))
        self.assertEqual(extract_window_days("近90日成交额"), 90)

    def test_total_sales_wins_over_video_and_live_breakdowns(self):
        rows = (
            (
                "达人 ID",
                "近 7 天销售额",
                "近 7 天视频销售额",
                "近 7 天直播销售额",
                "近 30 天销售额",
                "近 30 天视频销售额",
            ),
            ("alice", "$100", "$60", "$40", "$500", "$300"),
        )
        rule, warnings = build_initial_rule(rows)
        self.assertEqual(
            [
                (item["window_days"], item["source_column"])
                for item in rule["sales_columns"]
            ],
            [(7, 1), (30, 4)],
        )
        self.assertEqual(warnings, [])

    def test_lifetime_total_sales_is_imported_as_zero_window(self):
        rows = (
            (
                "达人 ID",
                "近 7 天销售额",
                "近 30 天销售额",
                "总销售额",
            ),
            ("alice", "$100", "$500", "$8,000"),
        )

        rule, warnings = build_initial_rule(rows)
        converted = list(transform_rows(rows, rule))

        self.assertEqual(
            [
                (item["window_days"], item["source_column"])
                for item in rule["sales_columns"]
            ],
            [(7, 1), (30, 2), (0, 3)],
        )
        self.assertEqual(warnings, [])
        self.assertEqual(converted[0].sales[0], Decimal("8000"))

    def test_email_extraction_is_optional(self):
        self.assertEqual(
            extract_email("WhatsApp: 123, Email: Alice@Example.com"),
            "alice@example.com",
        )
        self.assertEqual(extract_email("WhatsApp: 123"), "")

    def test_xlsx_display_format_preserves_leading_zeroes(self):
        workbook = Workbook()
        sheet = workbook.active
        sheet.append(["达人ID"])
        sheet.append([123])
        sheet["A2"].number_format = "000000"
        stream = io.BytesIO()
        workbook.save(stream)
        parsed = read_sheet(
            file_name="creators.xlsx",
            file_bytes=stream.getvalue(),
        )
        self.assertEqual(parsed.rows[1][0], "000123")

    def test_xlsx_with_third_party_empty_fill_is_repaired(self):
        content = xlsx_with_invalid_empty_fill(
            xlsx_bytes(("达人ID", "达人昵称"), ("00123", "Alice"))
        )
        parsed = read_sheet(
            file_name="third-party-export.xlsx",
            file_bytes=content,
        )
        self.assertEqual(parsed.rows[1], ("00123", "Alice"))

    def test_xlsx_with_attributed_empty_fill_is_repaired(self):
        content = xlsx_with_invalid_empty_fill(
            xlsx_bytes(("达人ID", "达人昵称"), ("00123", "Alice")),
            fill_markup=b'<fill source="third-party-exporter"/>',
        )
        parsed = read_sheet(
            file_name="third-party-export.xlsx",
            file_bytes=content,
        )
        self.assertEqual(parsed.rows[1], ("00123", "Alice"))

    def test_csv_gb18030_is_supported(self):
        content = "达人ID,达人昵称\n00123,张三\n".encode("gb18030")
        parsed = read_sheet(
            file_name="creators.csv",
            file_bytes=content,
        )
        self.assertEqual(parsed.selected_sheet, "CSV")
        self.assertEqual(parsed.rows[1], ("00123", "张三"))

    def test_upload_rejects_macro_workbook(self):
        content = xlsx_bytes(("达人ID",), ("10001",))
        # A regular workbook is accepted and parsed read-only.
        validate_upload(
            file_name="creators.xlsx",
            content_type=(
                "application/vnd.openxmlformats-officedocument."
                "spreadsheetml.sheet"
            ),
            file_bytes=content,
            max_size=1024 * 1024,
        )


@override_settings(
    IMPORT_MAX_FILE_SIZE_BYTES=1024 * 1024,
    IMPORT_PREVIEW_ROWS=20,
    IMPORT_PREVIEW_SCAN_ROWS=200,
)
class ImportViewAndWorkerTests(TestCase):
    def setUp(self):
        preview_store.clear()
        self.temp_directory = tempfile.TemporaryDirectory()
        self.settings_override = override_settings(
            IMPORT_TEMP_ROOT=Path(self.temp_directory.name)
        )
        self.settings_override.enable()

    def tearDown(self):
        preview_store.clear()
        self.settings_override.disable()
        self.temp_directory.cleanup()

    def _preview(
        self,
        content: bytes,
        *,
        file_name: str = "creators.xlsx",
        sheet_name: str = "",
    ):
        data = {"file": upload(file_name, content)}
        if sheet_name:
            data["sheet_name"] = sheet_name
        return self.client.post(reverse("tasks:import-preview"), data)

    def _confirm_and_run(
        self,
        *,
        file_name: str,
        content: bytes,
        sheet_name: str = "",
    ) -> ImportTask:
        preview_response = self._preview(
            content,
            file_name=file_name,
            sheet_name=sheet_name,
        )
        self.assertEqual(preview_response.status_code, 200)
        preview = preview_response.json()
        self.assertTrue(preview["success"])
        self.assertFalse(preview.get("requiresSheetSelection", False))

        confirm = self.client.post(
            reverse(
                "tasks:import-confirm",
                kwargs={"preview_id": preview["previewId"]},
            ),
            {"file": upload(file_name, content)},
        )
        self.assertEqual(confirm.status_code, 202)
        task = ImportTask.objects.get(pk=confirm.json()["taskId"])
        return run_import_task(task)

    def test_preview_stays_out_of_database(self):
        content = xlsx_bytes(
            ("达人ID", "达人昵称", "Contact", "7D GMV"),
            ("001", "Alice", "alice@example.com", "1.2K"),
        )
        response = self._preview(content)
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["success"])
        self.assertEqual(payload["convertedSample"][0]["creatorId"], "001")
        self.assertEqual(ImportTask.objects.count(), 0)
        self.assertEqual(Creator.objects.count(), 0)

    def test_multiple_sheets_require_selection(self):
        content = xlsx_bytes(
            sheets={
                "US": [["达人ID"], ["10001"]],
                "UK": [["UID"], ["20001"]],
            }
        )
        response = self._preview(content)
        self.assertTrue(response.json()["requiresSheetSelection"])
        selected = self._preview(content, sheet_name="UK")
        self.assertEqual(selected.json()["sheetName"], "UK")
        self.assertEqual(
            selected.json()["convertedSample"][0]["creatorId"],
            "20001",
        )

    def test_confirm_hash_mismatch_is_rejected(self):
        content = xlsx_bytes(("达人ID",), ("10001",))
        preview = self._preview(content).json()
        changed = xlsx_bytes(("达人ID",), ("20002",))
        response = self.client.post(
            reverse(
                "tasks:import-confirm",
                kwargs={"preview_id": preview["previewId"]},
            ),
            {"file": upload("creators.xlsx", changed)},
        )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["code"], "FILE_CHANGED")
        self.assertEqual(ImportTask.objects.count(), 0)

    def test_confirm_then_worker_persists_only_standard_fields(self):
        content = xlsx_bytes(
            ("达人编号", "达人名称", "Contact", "7D GMV", "粉丝数"),
            ("001", "Alice", "Email alice@example.com", "1.2K", "99999"),
            ("002", "Bob", "WhatsApp only", "bad", "88888"),
            ("", "No ID", "missing@example.com", "100", "1"),
        )
        preview = self._preview(content).json()
        confirm = self.client.post(
            reverse(
                "tasks:import-confirm",
                kwargs={"preview_id": preview["previewId"]},
            ),
            {
                "file": upload("creators.xlsx", content),
                "saved_file_name": "2026年7月美国达人",
            },
        )
        self.assertEqual(confirm.status_code, 202)
        task = ImportTask.objects.get()
        self.assertEqual(task.file_name, "2026年7月美国达人.xlsx")
        self.assertEqual(task.status, ImportTask.Status.QUEUED)
        self.assertTrue(task_source_path(task).is_file())

        result = run_import_task(task)
        self.assertEqual(result.status, ImportTask.Status.PARTIAL_SUCCESS)
        self.assertEqual(result.success_rows, 1)
        self.assertEqual(result.partial_success_rows, 1)
        self.assertEqual(result.failed_rows, 1)
        self.assertEqual(Creator.objects.count(), 2)
        self.assertEqual(
            Creator.objects.get(creator_id="001").email,
            "alice@example.com",
        )
        self.assertEqual(CreatorSalesMetric.objects.count(), 1)
        self.assertEqual(
            CreatorSalesMetric.objects.get().sales_amount,
            Decimal("1200"),
        )
        self.assertFalse(task_source_path(task).exists())
        self.assertGreaterEqual(ImportRowError.objects.count(), 3)

    def test_supported_table_variants_share_one_database_shape(self):
        standard_xlsx = xlsx_bytes(
            ("UID", "Username", "Email Address", "7D GMV", "30D Revenue"),
            ("00123", "Alice", "alice@example.com", "$1.2K", "¥3,456.78"),
        )
        third_party_xlsx = xlsx_with_invalid_empty_fill(
            xlsx_bytes(
                ("达人ID", "达人昵称", "联系邮箱", "近7天销售额", "近30日成交额"),
                ("00123", "Alice", "alice@example.com", "1200", "3456.78"),
            ),
            fill_markup=b'<fill source="third-party-exporter"/>',
        )
        multi_sheet_xlsx = xlsx_bytes(
            sheets={
                "说明": [["此 Sheet 不应导入"]],
                "达人数据": [
                    ["2026 Creator Report"],
                    [
                        "达人 ID",
                        "达人名称",
                        "联系方式",
                        "近 7 日成交额",
                        "近 30 天销售额",
                        "粉丝数",
                    ],
                    [
                        "00123",
                        "Alice",
                        "Email: alice@example.com",
                        "1.2K",
                        "3456.78",
                        "99999",
                    ],
                ],
            }
        )
        utf8_csv = (
            "creator_id,nickname,email,7 day revenue,30 day gmv\n"
            "00123,Alice,alice@example.com,1200,3456.78\n"
        ).encode("utf-8-sig")
        gb18030_csv = (
            "达人编号,达人昵称,商务邮箱,7天GMV,30日销售额\n"
            "00123,Alice,alice@example.com,1200,3456.78\n"
        ).encode("gb18030")

        variants = (
            ("standard.xlsx", standard_xlsx, ""),
            ("third-party.xlsx", third_party_xlsx, ""),
            ("multi-sheet.xlsx", multi_sheet_xlsx, "达人数据"),
            ("utf8.csv", utf8_csv, ""),
            ("gb18030.csv", gb18030_csv, ""),
        )
        for file_name, content, sheet_name in variants:
            with self.subTest(file_name=file_name):
                task = self._confirm_and_run(
                    file_name=file_name,
                    content=content,
                    sheet_name=sheet_name,
                )
                self.assertEqual(task.status, ImportTask.Status.SUCCESS)
                self.assertEqual(task.total_rows, 1)
                self.assertEqual(task.success_rows, 1)
                self.assertEqual(task.sales_metric_count, 2)
                creator = Creator.objects.get(creator_id="00123")
                self.assertEqual(creator.nickname, "Alice")
                self.assertEqual(creator.email, "alice@example.com")
                self.assertEqual(
                    {
                        metric.window_days: metric.sales_amount
                        for metric in task.sales_metrics.all()
                    },
                    {
                        7: Decimal("1200"),
                        30: Decimal("3456.78"),
                    },
                )

        self.assertEqual(Creator.objects.count(), 1)
        self.assertEqual(ImportTask.objects.count(), len(variants))
        self.assertEqual(CreatorSalesMetric.objects.count(), len(variants) * 2)

    def test_empty_optional_values_do_not_overwrite_existing_creator(self):
        Creator.objects.create(
            creator_id="10001",
            nickname="Alice",
            email="alice@example.com",
        )
        content = xlsx_bytes(
            ("达人ID", "达人昵称", "邮箱"),
            ("10001", "", ""),
        )
        preview = self._preview(content).json()
        self.client.post(
            reverse(
                "tasks:import-confirm",
                kwargs={"preview_id": preview["previewId"]},
            ),
            {"file": upload("creators.xlsx", content)},
        )
        run_import_task(ImportTask.objects.get())
        creator = Creator.objects.get(creator_id="10001")
        self.assertEqual(creator.nickname, "Alice")
        self.assertEqual(creator.email, "alice@example.com")

    def test_task_page_and_status_endpoint(self):
        task = ImportTask.objects.create(
            file_name="confirmed.csv",
            file_sha256="a" * 64,
            sheet_name="CSV",
            status=ImportTask.Status.SUCCESS,
            snapshot_date="2026-07-29",
            confirmed_at="2026-07-29T00:00:00Z",
            finished_at="2026-07-29T00:00:01Z",
        )
        page = self.client.get(
            reverse("tasks:import-detail", kwargs={"task_id": task.pk})
        )
        self.assertContains(page, "confirmed.csv")
        status = self.client.get(
            reverse("tasks:import-status", kwargs={"task_id": task.pk})
        )
        self.assertEqual(status.json()["status"], ImportTask.Status.SUCCESS)

    def test_preview_page_puts_save_controls_and_converted_table_first(self):
        page = self.client.get(reverse("tasks:creator-data"))
        html = page.content.decode()
        self.assertIn("data-saved-file-name", html)
        self.assertLess(
            html.index("data-confirm-import"),
            html.index("data-converted-table"),
        )
        self.assertLess(
            html.index("data-converted-table"),
            html.index("data-original-table"),
        )


class ImportRuleAdvisorTests(TestCase):
    @patch.dict("os.environ", {"DEEPSEEK_API_KEY": "test-secret"})
    @patch("tasks.services.ai_rule_advisor.subprocess.run")
    @override_settings(
        IMPORT_RULE_MODEL="deepseek/deepseek-v4-pro",
        OPENCODE_BINARY="opencode",
    )
    def test_v4_pro_returns_validated_rule(self, run):
        rule = {
            "header_row": 1,
            "creator_id_column": 1,
            "nickname_column": 0,
            "email_column": None,
            "sales_columns": [],
            "ignored_columns": [],
            "column_transforms": {"0": "trim", "1": "trim"},
        }
        run.return_value.returncode = 0
        run.return_value.stdout = json.dumps(
            {
                "type": "text",
                "part": {"text": json.dumps(rule, ensure_ascii=False)},
            }
        )
        result = ImportRuleAdvisor().revise_rule(
            instruction="UID 才是达人 ID，用户名是昵称。",
            rows=(("用户名", "UID"), ("Alice", "10001")),
            current_rule={
                **rule,
                "creator_id_column": 0,
                "nickname_column": 1,
            },
        )
        self.assertEqual(result["creator_id_column"], 1)
        command = run.call_args.args[0]
        self.assertIn("deepseek/deepseek-v4-pro", command)
