"""Persistent task, product, creator, and export records."""

from __future__ import annotations

import uuid

from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models


PRODUCT_LIMIT_CHOICES = tuple((value, f"{value} 个商品") for value in range(10, 101, 10))


class CreatorAcquisitionTask(models.Model):
    class Status(models.TextChoices):
        PENDING = "PENDING", "等待中"
        RUNNING = "RUNNING", "执行中"
        WAITING_CONFIRMATION = "WAITING_CONFIRMATION", "等待登录"
        SUCCESS = "SUCCESS", "已完成"
        FAILED = "FAILED", "失败"
        CANCELLED = "CANCELLED", "已取消"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=120, default="获取达人数据")
    product_limit = models.PositiveSmallIntegerField(
        choices=PRODUCT_LIMIT_CHOICES,
        validators=[MinValueValidator(10), MaxValueValidator(100)],
    )
    status = models.CharField(
        max_length=32,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True,
    )
    progress = models.PositiveSmallIntegerField(default=0)
    current_step = models.CharField(max_length=160, blank=True)
    error_code = models.CharField(max_length=80, blank=True)
    error_message = models.TextField(blank=True)
    opencode_session_id = models.CharField(max_length=100, blank=True)
    model_name = models.CharField(max_length=100, default="deepseek/deepseek-v4-flash")
    final_summary = models.TextField(blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.name} · {self.product_limit} · {self.get_status_display()}"


class TaskStep(models.Model):
    class Status(models.TextChoices):
        RUNNING = "RUNNING", "执行中"
        SUCCESS = "SUCCESS", "成功"
        FAILED = "FAILED", "失败"

    task = models.ForeignKey(
        CreatorAcquisitionTask,
        related_name="steps",
        on_delete=models.CASCADE,
    )
    step_id = models.CharField(max_length=160)
    sequence = models.PositiveIntegerField()
    operation = models.CharField(max_length=120)
    label = models.CharField(max_length=160)
    status = models.CharField(max_length=16, choices=Status.choices)
    input_summary = models.JSONField(default=dict, blank=True)
    output_summary = models.JSONField(default=dict, blank=True)
    error_code = models.CharField(max_length=80, blank=True)
    error_message = models.TextField(blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["sequence"]
        constraints = [
            models.UniqueConstraint(
                fields=["task", "step_id"],
                name="unique_task_step_id",
            )
        ]


class Product(models.Model):
    task = models.ForeignKey(
        CreatorAcquisitionTask,
        related_name="products",
        on_delete=models.CASCADE,
    )
    external_product_id = models.CharField(max_length=40)
    name = models.TextField()
    product_url = models.URLField(max_length=500)
    country = models.CharField(max_length=12, blank=True)
    category = models.CharField(max_length=160, blank=True)
    total_sales = models.DecimalField(
        max_digits=22,
        decimal_places=2,
        null=True,
        blank=True,
    )
    total_sales_raw = models.CharField(max_length=80, blank=True)
    recent_7_day_revenue = models.DecimalField(
        max_digits=22,
        decimal_places=2,
        null=True,
        blank=True,
    )
    recent_7_day_revenue_raw = models.CharField(max_length=80, blank=True)
    total_revenue = models.DecimalField(
        max_digits=22,
        decimal_places=2,
        null=True,
        blank=True,
    )
    total_revenue_raw = models.CharField(max_length=80, blank=True)
    related_creator_count = models.PositiveIntegerField(null=True, blank=True)
    related_creator_count_raw = models.CharField(max_length=80, blank=True)
    collected_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["id"]
        constraints = [
            models.UniqueConstraint(
                fields=["task", "external_product_id"],
                name="unique_task_external_product",
            )
        ]

    def __str__(self) -> str:
        return self.name


class CreatorExportArtifact(models.Model):
    product = models.ForeignKey(
        Product,
        related_name="exports",
        on_delete=models.CASCADE,
    )
    file_name = models.CharField(max_length=255)
    file_path = models.CharField(max_length=1000)
    sha256 = models.CharField(max_length=64)
    requested_row_count = models.PositiveIntegerField(default=100)
    exported_row_count = models.PositiveIntegerField(default=0)
    imported_row_count = models.PositiveIntegerField(default=0)
    file_size_bytes = models.PositiveBigIntegerField(default=0)
    downloaded_at = models.DateTimeField(null=True, blank=True)
    imported_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-imported_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["product", "sha256"],
                name="unique_product_export_sha256",
            )
        ]


class RelatedCreator(models.Model):
    product = models.ForeignKey(
        Product,
        related_name="related_creators",
        on_delete=models.CASCADE,
    )
    creator_handle = models.CharField(max_length=160)
    nickname = models.CharField(max_length=255, blank=True)
    tiktok_url = models.URLField(max_length=500, blank=True)
    creator_detail_url = models.URLField(max_length=500, blank=True)
    category = models.CharField(max_length=160, blank=True)
    country_code = models.CharField(max_length=12, blank=True)
    recent_7_day_revenue = models.DecimalField(
        max_digits=22, decimal_places=2, null=True, blank=True
    )
    recent_7_day_video_revenue = models.DecimalField(
        max_digits=22, decimal_places=2, null=True, blank=True
    )
    recent_7_day_live_revenue = models.DecimalField(
        max_digits=22, decimal_places=2, null=True, blank=True
    )
    recent_30_day_revenue = models.DecimalField(
        max_digits=22, decimal_places=2, null=True, blank=True
    )
    recent_30_day_video_revenue = models.DecimalField(
        max_digits=22, decimal_places=2, null=True, blank=True
    )
    recent_30_day_live_revenue = models.DecimalField(
        max_digits=22, decimal_places=2, null=True, blank=True
    )
    related_video_count = models.PositiveIntegerField(null=True, blank=True)
    related_live_count = models.PositiveIntegerField(null=True, blank=True)
    follower_count = models.DecimalField(
        max_digits=22, decimal_places=2, null=True, blank=True
    )
    average_views = models.DecimalField(
        max_digits=22, decimal_places=2, null=True, blank=True
    )
    total_views = models.DecimalField(
        max_digits=22, decimal_places=2, null=True, blank=True
    )
    average_likes = models.DecimalField(
        max_digits=22, decimal_places=2, null=True, blank=True
    )
    total_likes = models.DecimalField(
        max_digits=22, decimal_places=2, null=True, blank=True
    )
    engagement_rate = models.DecimalField(
        max_digits=10, decimal_places=4, null=True, blank=True
    )
    like_follower_ratio = models.DecimalField(
        max_digits=18, decimal_places=6, null=True, blank=True
    )
    email = models.EmailField(max_length=320, blank=True)
    x_url = models.URLField(max_length=500, blank=True)
    instagram_url = models.URLField(max_length=500, blank=True)
    youtube_url = models.URLField(max_length=500, blank=True)
    whatsapp_url = models.URLField(max_length=500, blank=True)
    linkedin_url = models.URLField(max_length=500, blank=True)
    telegram_url = models.URLField(max_length=500, blank=True)
    facebook_url = models.URLField(max_length=500, blank=True)
    raw_data = models.JSONField(default=dict, blank=True)
    collected_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["product_id", "-recent_30_day_revenue", "creator_handle"]
        constraints = [
            models.UniqueConstraint(
                fields=["product", "creator_handle"],
                name="unique_product_creator_handle",
            )
        ]

    def __str__(self) -> str:
        return self.nickname or self.creator_handle
