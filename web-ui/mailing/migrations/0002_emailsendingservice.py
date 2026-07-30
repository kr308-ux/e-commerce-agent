import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("mailing", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="EmailSendingService",
            fields=[
                (
                    "id",
                    models.PositiveSmallIntegerField(
                        default=1,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("STOPPED", "已停止"),
                            ("RUNNING", "运行中"),
                            ("STOPPING", "正在停止"),
                        ],
                        db_index=True,
                        default="STOPPED",
                        max_length=16,
                    ),
                ),
                ("stop_requested", models.BooleanField(default=False)),
                (
                    "run_id",
                    models.UUIDField(blank=True, editable=False, null=True),
                ),
                (
                    "process_id",
                    models.PositiveIntegerField(blank=True, null=True),
                ),
                ("sent_count", models.PositiveIntegerField(default=0)),
                ("failed_count", models.PositiveIntegerField(default=0)),
                ("started_at", models.DateTimeField(blank=True, null=True)),
                ("heartbeat_at", models.DateTimeField(blank=True, null=True)),
                ("stopped_at", models.DateTimeField(blank=True, null=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "current_delivery",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="+",
                        to="mailing.emaildelivery",
                    ),
                ),
            ],
        ),
    ]
