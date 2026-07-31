from django.db import migrations, models
import django.db.models.deletion
from django.utils import timezone


def backfill_latest_attempt_snapshot(apps, schema_editor):
    EmailDelivery = apps.get_model("mailing", "EmailDelivery")
    EmailDeliveryAttempt = apps.get_model(
        "mailing",
        "EmailDeliveryAttempt",
    )
    snapshots = []
    deliveries = (
        EmailDelivery.objects
        .filter(attempt_count__gt=0, last_attempt_at__isnull=False)
        .iterator(chunk_size=1000)
    )
    for delivery in deliveries:
        if delivery.status == "SENT":
            status = "SENT"
        elif delivery.status in {"FAILED", "RETRY_WAITING"}:
            status = "FAILED"
        else:
            status = "STARTED"
        local_attempt = timezone.localtime(delivery.last_attempt_at)
        snapshots.append(
            EmailDeliveryAttempt(
                delivery_id=delivery.pk,
                sequence=max(1, delivery.attempt_count),
                attempt_date=local_attempt.date(),
                daily_sequence=min(max(1, delivery.attempt_count), 3),
                status=status,
                message_id=delivery.message_id,
                error_code=delivery.error_code,
                error_message=delivery.error_message,
                started_at=delivery.last_attempt_at,
                finished_at=(
                    delivery.sent_at
                    or delivery.updated_at
                    or delivery.last_attempt_at
                ),
                is_legacy_snapshot=True,
            )
        )
        if len(snapshots) >= 1000:
            EmailDeliveryAttempt.objects.bulk_create(
                snapshots,
                batch_size=1000,
                ignore_conflicts=True,
            )
            snapshots.clear()
    if snapshots:
        EmailDeliveryAttempt.objects.bulk_create(
            snapshots,
            batch_size=1000,
            ignore_conflicts=True,
        )


class Migration(migrations.Migration):

    dependencies = [
        ("mailing", "0006_backfill_contact_source_batches"),
    ]

    operations = [
        migrations.AddField(
            model_name="emaildelivery",
            name="last_failure_at",
            field=models.DateTimeField(
                blank=True,
                db_index=True,
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="emaildelivery",
            name="next_retry_at",
            field=models.DateTimeField(
                blank=True,
                db_index=True,
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="emaildelivery",
            name="retryable",
            field=models.BooleanField(db_index=True, default=True),
        ),
        migrations.AlterField(
            model_name="emaildelivery",
            name="status",
            field=models.CharField(
                choices=[
                    ("PENDING", "待发送"),
                    ("SENDING", "发送中"),
                    ("SENT", "发送成功"),
                    ("FAILED", "发送失败"),
                    ("RETRY_WAITING", "等待明日重试"),
                    ("SKIPPED", "已跳过"),
                ],
                db_index=True,
                default="PENDING",
                max_length=16,
            ),
        ),
        migrations.CreateModel(
            name="EmailDeliveryAttempt",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("sequence", models.PositiveIntegerField()),
                ("attempt_date", models.DateField(db_index=True)),
                ("daily_sequence", models.PositiveSmallIntegerField()),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("STARTED", "发送中"),
                            ("SENT", "发送成功"),
                            ("FAILED", "发送失败"),
                        ],
                        db_index=True,
                        default="STARTED",
                        max_length=16,
                    ),
                ),
                ("message_id", models.CharField(blank=True, max_length=255)),
                ("error_code", models.CharField(blank=True, max_length=80)),
                ("error_message", models.TextField(blank=True)),
                ("started_at", models.DateTimeField()),
                (
                    "finished_at",
                    models.DateTimeField(blank=True, null=True),
                ),
                ("is_legacy_snapshot", models.BooleanField(default=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "delivery",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="attempts",
                        to="mailing.emaildelivery",
                    ),
                ),
            ],
            options={
                "ordering": ["-started_at", "-id"],
                "indexes": [
                    models.Index(
                        fields=["attempt_date", "status"],
                        name="mail_attempt_day_status_idx",
                    ),
                    models.Index(
                        fields=["delivery", "attempt_date"],
                        name="mail_attempt_delivery_day_idx",
                    ),
                ],
                "constraints": [
                    models.UniqueConstraint(
                        fields=("delivery", "sequence"),
                        name="unique_mail_delivery_attempt_seq",
                    ),
                ],
            },
        ),
        migrations.RunPython(
            backfill_latest_attempt_snapshot,
            migrations.RunPython.noop,
        ),
    ]
