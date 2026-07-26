"""Persistent configuration and execution records for contacting creators."""

from __future__ import annotations

import hashlib
import uuid

from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import Q

from tasks.models import Product, RelatedCreator


def normalize_creator_handle(value: object) -> str:
    """Return a stable, case-insensitive creator handle without ``@``."""
    return str(value or "").strip().lstrip("@").strip().casefold()


def message_sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class GreetingTemplate(models.Model):
    name = models.CharField(max_length=120, unique=True)
    content = models.TextField(max_length=2000)
    content_sha256 = models.CharField(max_length=64, editable=False)
    is_default = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-is_default", "-updated_at", "name"]
        constraints = [
            models.UniqueConstraint(
                fields=["is_default"],
                condition=Q(is_default=True),
                name="unique_default_creator_greeting",
            )
        ]

    def save(self, *args, **kwargs) -> None:
        self.content = str(self.content or "").strip()
        self.content_sha256 = message_sha256(self.content)
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return self.name


class DirectedCollaborationOption(models.Model):
    """Cached ongoing collaboration.

    ``external_invitation_id`` is TikTok's ``invitationGroupId`` shown on the
    collaboration list and right-side card; it is not the per-creator
    ``invitationId`` created later during contact execution.
    """

    class Status(models.TextChoices):
        ONGOING = "ONGOING", "进行中"
        EXPIRING = "EXPIRING", "即将到期"
        CANCELLED = "CANCELLED", "取消中"
        COMPLETED = "COMPLETED", "已完成"
        UNKNOWN = "UNKNOWN", "未知"

    store_id = models.CharField(max_length=80, db_index=True)
    external_invitation_id = models.CharField(
        max_length=128,
        help_text="TikTok invitationGroupId",
    )
    name = models.CharField(max_length=255)
    status = models.CharField(
        max_length=32,
        choices=Status.choices,
        default=Status.ONGOING,
        db_index=True,
    )
    product_count = models.PositiveIntegerField(default=0)
    invited_creator_count = models.PositiveIntegerField(default=0)
    accepted_creator_count = models.PositiveIntegerField(default=0)
    promoted_creator_count = models.PositiveIntegerField(default=0)
    raw_data = models.JSONField(default=dict, blank=True)
    is_active = models.BooleanField(default=True, db_index=True)
    last_synced_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name", "external_invitation_id"]
        constraints = [
            models.UniqueConstraint(
                fields=["store_id", "external_invitation_id"],
                name="unique_store_directed_invitation",
            )
        ]

    def __str__(self) -> str:
        return f"{self.name} · {self.store_id}"


class CreatorContactTask(models.Model):
    class Status(models.TextChoices):
        PENDING = "PENDING", "等待中"
        RUNNING = "RUNNING", "执行中"
        SUCCESS = "SUCCESS", "已完成"
        PARTIAL_SUCCESS = "PARTIAL_SUCCESS", "部分完成"
        FAILED = "FAILED", "失败"
        CANCELLED = "CANCELLED", "已取消"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=160, default="联系达人")
    store_id = models.CharField(max_length=80, db_index=True)
    source_product = models.ForeignKey(
        Product,
        related_name="creator_contact_tasks",
        on_delete=models.PROTECT,
    )
    top_n = models.PositiveSmallIntegerField(
        validators=[MinValueValidator(1), MaxValueValidator(100)]
    )
    greeting_template = models.ForeignKey(
        GreetingTemplate,
        related_name="contact_tasks",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    greeting_snapshot = models.TextField(max_length=2000)
    greeting_sha256 = models.CharField(max_length=64, editable=False)
    collaboration_option = models.ForeignKey(
        DirectedCollaborationOption,
        related_name="contact_tasks",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    invitation_name_snapshot = models.CharField(max_length=255)
    invitation_id_snapshot = models.CharField(
        max_length=128,
        help_text="Selected collaboration invitationGroupId snapshot",
    )
    confirm_send_greeting = models.BooleanField(default=False)
    confirm_send_invitation = models.BooleanField(default=False)
    confirm_send_card = models.BooleanField(default=False)
    status = models.CharField(
        max_length=32,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True,
    )
    progress = models.PositiveSmallIntegerField(default=0)
    current_step = models.CharField(max_length=200, blank=True)
    error_code = models.CharField(max_length=100, blank=True)
    error_message = models.TextField(blank=True)
    model_name = models.CharField(
        max_length=100,
        default="deepseek/deepseek-v4-flash",
    )
    opencode_session_id = models.CharField(max_length=120, blank=True)
    final_summary = models.JSONField(default=dict, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def save(self, *args, **kwargs) -> None:
        self.store_id = str(self.store_id or "").strip()
        self.greeting_snapshot = str(self.greeting_snapshot or "").strip()
        self.greeting_sha256 = message_sha256(self.greeting_snapshot)
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f"{self.name} · {self.top_n} 位达人"


class CreatorContactTarget(models.Model):
    class Status(models.TextChoices):
        PENDING = "PENDING", "等待中"
        RUNNING = "RUNNING", "执行中"
        SUCCESS = "SUCCESS", "已完成"
        FAILED = "FAILED", "失败"
        SKIPPED = "SKIPPED", "已跳过"

    task = models.ForeignKey(
        CreatorContactTask,
        related_name="targets",
        on_delete=models.CASCADE,
    )
    related_creator = models.ForeignKey(
        RelatedCreator,
        related_name="contact_targets",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    rank = models.PositiveIntegerField()
    creator_handle_snapshot = models.CharField(max_length=160)
    normalized_handle = models.CharField(max_length=160, db_index=True)
    nickname_snapshot = models.CharField(max_length=255, blank=True)
    recent_30_day_revenue_snapshot = models.DecimalField(
        max_digits=22,
        decimal_places=2,
        null=True,
        blank=True,
    )
    recent_7_day_revenue_snapshot = models.DecimalField(
        max_digits=22,
        decimal_places=2,
        null=True,
        blank=True,
    )
    chat_creator_id = models.CharField(max_length=40, blank=True)
    actual_invitation_id = models.CharField(max_length=128, blank=True)
    invitation_group_id = models.CharField(max_length=128, blank=True)
    status = models.CharField(
        max_length=24,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True,
    )
    current_step = models.CharField(max_length=200, blank=True)
    message_sent = models.BooleanField(default=False)
    invitation_created = models.BooleanField(default=False)
    card_sent = models.BooleanField(default=False)
    target_plan_message_verified = models.BooleanField(default=False)
    final_send_verified = models.BooleanField(default=False)
    opencode_session_id = models.CharField(max_length=120, blank=True)
    result = models.JSONField(default=dict, blank=True)
    error_code = models.CharField(max_length=100, blank=True)
    error_message = models.TextField(blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["rank"]
        constraints = [
            models.UniqueConstraint(
                fields=["task", "normalized_handle"],
                name="unique_contact_task_creator_handle",
            ),
            models.UniqueConstraint(
                fields=["task", "rank"],
                name="unique_contact_task_creator_rank",
            ),
        ]

    def save(self, *args, **kwargs) -> None:
        normalized = normalize_creator_handle(
            self.normalized_handle or self.creator_handle_snapshot
        )
        self.normalized_handle = normalized
        self.creator_handle_snapshot = str(
            self.creator_handle_snapshot or normalized
        ).strip().lstrip("@")
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f"@{self.normalized_handle} · {self.get_status_display()}"


class CreatorContactTaskStep(models.Model):
    class Status(models.TextChoices):
        RUNNING = "RUNNING", "执行中"
        SUCCESS = "SUCCESS", "成功"
        FAILED = "FAILED", "失败"

    task = models.ForeignKey(
        CreatorContactTask,
        related_name="steps",
        on_delete=models.CASCADE,
    )
    target = models.ForeignKey(
        CreatorContactTarget,
        related_name="steps",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
    )
    step_id = models.CharField(max_length=200)
    sequence = models.PositiveIntegerField()
    operation = models.CharField(max_length=160)
    label = models.CharField(max_length=200)
    status = models.CharField(max_length=16, choices=Status.choices)
    input_summary = models.JSONField(default=dict, blank=True)
    output_summary = models.JSONField(default=dict, blank=True)
    error_code = models.CharField(max_length=100, blank=True)
    error_message = models.TextField(blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["sequence"]
        constraints = [
            models.UniqueConstraint(
                fields=["task", "step_id"],
                name="unique_creator_contact_task_step",
            )
        ]


class ContactedCreator(models.Model):
    store_id = models.CharField(max_length=80, db_index=True)
    normalized_handle = models.CharField(max_length=160)
    creator_handle = models.CharField(max_length=160)
    chat_creator_id = models.CharField(max_length=40, blank=True)
    related_creator = models.ForeignKey(
        RelatedCreator,
        related_name="successful_contacts",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    contact_task = models.ForeignKey(
        CreatorContactTask,
        related_name="contacted_creator_records",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    greeting_sha256 = models.CharField(max_length=64)
    invitation_id = models.CharField(max_length=128)
    invitation_name = models.CharField(max_length=255)
    evidence = models.JSONField(default=dict, blank=True)
    contacted_at = models.DateTimeField(auto_now=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-contacted_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["store_id", "normalized_handle"],
                name="unique_contacted_creator_per_store",
            )
        ]

    def save(self, *args, **kwargs) -> None:
        self.store_id = str(self.store_id or "").strip()
        self.normalized_handle = normalize_creator_handle(
            self.normalized_handle or self.creator_handle
        )
        self.creator_handle = str(
            self.creator_handle or self.normalized_handle
        ).strip().lstrip("@")
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f"@{self.normalized_handle} · {self.store_id}"


class CollaborationSyncJob(models.Model):
    class Status(models.TextChoices):
        PENDING = "PENDING", "等待中"
        RUNNING = "RUNNING", "执行中"
        SUCCESS = "SUCCESS", "已完成"
        FAILED = "FAILED", "失败"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    store_id = models.CharField(max_length=80, db_index=True)
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True,
    )
    imported_count = models.PositiveIntegerField(default=0)
    deactivated_count = models.PositiveIntegerField(default=0)
    error_code = models.CharField(max_length=100, blank=True)
    error_message = models.TextField(blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.store_id} · {self.get_status_display()}"
