"""Persistent creator imports and normalized creator business data."""

from __future__ import annotations

import uuid

from django.db import models


class ImportTask(models.Model):
    class Status(models.TextChoices):
        QUEUED = "QUEUED", "等待导入"
        IMPORTING = "IMPORTING", "导入中"
        SUCCESS = "SUCCESS", "已完成"
        PARTIAL_SUCCESS = "PARTIAL_SUCCESS", "部分完成"
        FAILED = "FAILED", "失败"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    file_name = models.CharField(max_length=255)
    file_sha256 = models.CharField(max_length=64, db_index=True)
    sheet_name = models.CharField(max_length=255, blank=True)
    status = models.CharField(
        max_length=32,
        choices=Status.choices,
        default=Status.QUEUED,
        db_index=True,
    )
    total_rows = models.PositiveIntegerField(default=0)
    processed_rows = models.PositiveIntegerField(default=0)
    success_rows = models.PositiveIntegerField(default=0)
    partial_success_rows = models.PositiveIntegerField(default=0)
    failed_rows = models.PositiveIntegerField(default=0)
    new_creator_count = models.PositiveIntegerField(default=0)
    updated_creator_count = models.PositiveIntegerField(default=0)
    sales_metric_count = models.PositiveIntegerField(default=0)
    confirmed_rule_version = models.PositiveIntegerField(default=1)
    current_step = models.CharField(max_length=160, blank=True)
    error_message = models.TextField(blank=True)
    snapshot_date = models.DateField()
    confirmed_at = models.DateTimeField()
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.file_name} · {self.get_status_display()}"


class ImportRuleVersion(models.Model):
    class Source(models.TextChoices):
        SYSTEM = "SYSTEM", "系统识别"
        AI = "AI", "大模型修正"

    import_task = models.ForeignKey(
        ImportTask,
        related_name="rule_versions",
        on_delete=models.CASCADE,
    )
    version = models.PositiveIntegerField()
    rule_json = models.JSONField(default=dict)
    source = models.CharField(max_length=16, choices=Source.choices)
    user_instruction = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["version"]
        constraints = [
            models.UniqueConstraint(
                fields=["import_task", "version"],
                name="unique_import_rule_version",
            )
        ]


class Creator(models.Model):
    creator_id = models.CharField(max_length=160, unique=True)
    nickname = models.CharField(max_length=255, blank=True)
    email = models.EmailField(max_length=320, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["creator_id"]

    def __str__(self) -> str:
        return self.nickname or self.creator_id


class ImportTaskCreator(models.Model):
    class RowStatus(models.TextChoices):
        SUCCESS = "SUCCESS", "成功"
        PARTIAL_SUCCESS = "PARTIAL_SUCCESS", "部分成功"

    import_task = models.ForeignKey(
        ImportTask,
        related_name="imported_creators",
        on_delete=models.CASCADE,
    )
    creator = models.ForeignKey(
        Creator,
        related_name="import_memberships",
        on_delete=models.CASCADE,
    )
    first_row_number = models.PositiveIntegerField()
    row_status = models.CharField(
        max_length=24,
        choices=RowStatus.choices,
        default=RowStatus.SUCCESS,
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["first_row_number", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["import_task", "creator"],
                name="unique_import_task_creator",
            )
        ]


class CreatorSalesMetric(models.Model):
    creator = models.ForeignKey(
        Creator,
        related_name="sales_metrics",
        on_delete=models.CASCADE,
    )
    import_task = models.ForeignKey(
        ImportTask,
        related_name="sales_metrics",
        on_delete=models.CASCADE,
    )
    window_days = models.PositiveIntegerField()
    sales_amount = models.DecimalField(max_digits=24, decimal_places=4)
    snapshot_date = models.DateField()
    source_row_number = models.PositiveIntegerField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = [
            "import_task_id",
            "source_row_number",
            "window_days",
        ]
        constraints = [
            models.UniqueConstraint(
                fields=[
                    "import_task",
                    "creator",
                    "window_days",
                    "source_row_number",
                ],
                name="unique_import_creator_sales_row",
            )
        ]


class ImportRowError(models.Model):
    import_task = models.ForeignKey(
        ImportTask,
        related_name="row_errors",
        on_delete=models.CASCADE,
    )
    row_number = models.PositiveIntegerField()
    creator_id = models.CharField(max_length=160, blank=True)
    error_code = models.CharField(max_length=80)
    error_field = models.CharField(max_length=80, blank=True)
    error_message = models.TextField()
    is_fatal = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["row_number", "id"]


class ImportLog(models.Model):
    class Status(models.TextChoices):
        INFO = "INFO", "信息"
        SUCCESS = "SUCCESS", "成功"
        WARNING = "WARNING", "警告"
        FAILED = "FAILED", "失败"

    import_task = models.ForeignKey(
        ImportTask,
        related_name="logs",
        on_delete=models.CASCADE,
    )
    step = models.CharField(max_length=80)
    status = models.CharField(max_length=16, choices=Status.choices)
    message = models.TextField()
    details_json = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "id"]
