"""Persistent, globally deduplicated creator email deliveries."""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from django.db import models

from tasks.models import Creator


def normalize_creator_id(value: object) -> str:
    return str(value or "").strip().lstrip("@").strip().casefold()


def normalize_email(value: object) -> str:
    return str(value or "").strip().casefold()


def recipient_key_for(*, creator_id: object, email: object) -> str:
    normalized_id = normalize_creator_id(creator_id)
    if normalized_id:
        return f"creator:{normalized_id}"
    address = normalize_email(email)
    if not address:
        raise ValueError("达人 ID 和邮箱不能同时为空。")
    return f"email:{address}"


def email_template_asset_upload_to(
    instance: "EmailTemplateAsset",
    filename: str,
) -> str:
    suffix = Path(filename).suffix.lower()
    return f"mailing/template-assets/{uuid4().hex}{suffix}"


class EmailTemplate(models.Model):
    """Singleton pointer to the currently active immutable template version."""

    id = models.PositiveSmallIntegerField(primary_key=True, default=1)
    name = models.CharField(max_length=120, default="达人合作邮件")
    active_version = models.ForeignKey(
        "EmailTemplateVersion",
        related_name="+",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
    )
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return self.name


class EmailTemplateAsset(models.Model):
    """An uploaded image retained for every template version that uses it."""

    file = models.FileField(upload_to=email_template_asset_upload_to)
    original_name = models.CharField(max_length=255)
    content_type = models.CharField(max_length=80)
    byte_size = models.PositiveIntegerField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "id"]

    def __str__(self) -> str:
        return self.original_name


class EmailTemplateVersion(models.Model):
    """Immutable content used for previewing and delivering one email."""

    template = models.ForeignKey(
        EmailTemplate,
        related_name="versions",
        on_delete=models.CASCADE,
    )
    version = models.PositiveIntegerField()
    subject_template = models.CharField(max_length=255)
    content_blocks = models.JSONField(default=list)
    assets = models.ManyToManyField(
        EmailTemplateAsset,
        related_name="template_versions",
        blank=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-version"]
        constraints = [
            models.UniqueConstraint(
                fields=["template", "version"],
                name="unique_email_template_version",
            )
        ]

    def __str__(self) -> str:
        return f"{self.template.name} · v{self.version}"


class EmailDelivery(models.Model):
    class Status(models.TextChoices):
        PENDING = "PENDING", "待发送"
        SENDING = "SENDING", "发送中"
        SENT = "SENT", "发送成功"
        FAILED = "FAILED", "发送失败"
        SKIPPED = "SKIPPED", "已跳过"

    creator = models.ForeignKey(
        Creator,
        related_name="email_deliveries",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    recipient_key = models.CharField(max_length=400, unique=True)
    creator_id_snapshot = models.CharField(max_length=160, blank=True)
    creator_name_snapshot = models.CharField(max_length=255)
    recipient_email = models.EmailField(max_length=320)
    template_version = models.ForeignKey(
        EmailTemplateVersion,
        related_name="deliveries",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
    )
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True,
    )
    attempt_count = models.PositiveSmallIntegerField(default=0)
    message_id = models.CharField(max_length=255, blank=True)
    error_code = models.CharField(max_length=80, blank=True)
    error_message = models.TextField(blank=True)
    last_attempt_at = models.DateTimeField(null=True, blank=True)
    sent_at = models.DateTimeField(null=True, blank=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["created_at", "id"]

    def save(self, *args, **kwargs) -> None:
        self.creator_id_snapshot = normalize_creator_id(
            self.creator_id_snapshot
        )
        self.recipient_email = normalize_email(self.recipient_email)
        self.creator_name_snapshot = (
            str(self.creator_name_snapshot or "").strip()
            or self.creator_id_snapshot
            or "Creator"
        )
        self.recipient_key = recipient_key_for(
            creator_id=self.creator_id_snapshot,
            email=self.recipient_email,
        )
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return (
            f"{self.creator_name_snapshot} <{self.recipient_email}> · "
            f"{self.get_status_display()}"
        )


class EmailSendingService(models.Model):
    """Singleton control record for the serial email sender process."""

    class Status(models.TextChoices):
        STOPPED = "STOPPED", "已停止"
        RUNNING = "RUNNING", "运行中"
        STOPPING = "STOPPING", "正在停止"

    id = models.PositiveSmallIntegerField(primary_key=True, default=1)
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.STOPPED,
        db_index=True,
    )
    stop_requested = models.BooleanField(default=False)
    run_id = models.UUIDField(null=True, blank=True, editable=False)
    process_id = models.PositiveIntegerField(null=True, blank=True)
    current_delivery = models.ForeignKey(
        EmailDelivery,
        related_name="+",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    sent_count = models.PositiveIntegerField(default=0)
    failed_count = models.PositiveIntegerField(default=0)
    started_at = models.DateTimeField(null=True, blank=True)
    heartbeat_at = models.DateTimeField(null=True, blank=True)
    stopped_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)
