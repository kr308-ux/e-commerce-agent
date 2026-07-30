import django.db.models.deletion
import uuid
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("tasks", "0002_relatedcreator_created_at"),
    ]

    operations = [
        migrations.CreateModel(
            name="Creator",
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
                ("creator_id", models.CharField(max_length=160, unique=True)),
                ("nickname", models.CharField(blank=True, max_length=255)),
                ("email", models.EmailField(blank=True, max_length=320)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={"ordering": ["creator_id"]},
        ),
        migrations.CreateModel(
            name="ImportTask",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("file_name", models.CharField(max_length=255)),
                ("file_sha256", models.CharField(db_index=True, max_length=64)),
                ("sheet_name", models.CharField(blank=True, max_length=255)),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("QUEUED", "等待导入"),
                            ("IMPORTING", "导入中"),
                            ("SUCCESS", "已完成"),
                            ("PARTIAL_SUCCESS", "部分完成"),
                            ("FAILED", "失败"),
                        ],
                        db_index=True,
                        default="QUEUED",
                        max_length=32,
                    ),
                ),
                ("total_rows", models.PositiveIntegerField(default=0)),
                ("processed_rows", models.PositiveIntegerField(default=0)),
                ("success_rows", models.PositiveIntegerField(default=0)),
                ("partial_success_rows", models.PositiveIntegerField(default=0)),
                ("failed_rows", models.PositiveIntegerField(default=0)),
                ("new_creator_count", models.PositiveIntegerField(default=0)),
                ("updated_creator_count", models.PositiveIntegerField(default=0)),
                ("sales_metric_count", models.PositiveIntegerField(default=0)),
                ("confirmed_rule_version", models.PositiveIntegerField(default=1)),
                ("current_step", models.CharField(blank=True, max_length=160)),
                ("error_message", models.TextField(blank=True)),
                ("snapshot_date", models.DateField()),
                ("confirmed_at", models.DateTimeField()),
                ("started_at", models.DateTimeField(blank=True, null=True)),
                ("finished_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={"ordering": ["-created_at"]},
        ),
        migrations.CreateModel(
            name="ImportRuleVersion",
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
                ("version", models.PositiveIntegerField()),
                ("rule_json", models.JSONField(default=dict)),
                (
                    "source",
                    models.CharField(
                        choices=[
                            ("SYSTEM", "系统识别"),
                            ("AI", "大模型修正"),
                        ],
                        max_length=16,
                    ),
                ),
                ("user_instruction", models.TextField(blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "import_task",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="rule_versions",
                        to="tasks.importtask",
                    ),
                ),
            ],
            options={"ordering": ["version"]},
        ),
        migrations.CreateModel(
            name="ImportTaskCreator",
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
                ("first_row_number", models.PositiveIntegerField()),
                (
                    "row_status",
                    models.CharField(
                        choices=[
                            ("SUCCESS", "成功"),
                            ("PARTIAL_SUCCESS", "部分成功"),
                        ],
                        default="SUCCESS",
                        max_length=24,
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "creator",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="import_memberships",
                        to="tasks.creator",
                    ),
                ),
                (
                    "import_task",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="imported_creators",
                        to="tasks.importtask",
                    ),
                ),
            ],
            options={"ordering": ["first_row_number", "id"]},
        ),
        migrations.CreateModel(
            name="CreatorSalesMetric",
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
                ("window_days", models.PositiveIntegerField()),
                (
                    "sales_amount",
                    models.DecimalField(decimal_places=4, max_digits=24),
                ),
                ("snapshot_date", models.DateField()),
                ("source_row_number", models.PositiveIntegerField()),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "creator",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="sales_metrics",
                        to="tasks.creator",
                    ),
                ),
                (
                    "import_task",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="sales_metrics",
                        to="tasks.importtask",
                    ),
                ),
            ],
            options={
                "ordering": [
                    "import_task_id",
                    "source_row_number",
                    "window_days",
                ]
            },
        ),
        migrations.CreateModel(
            name="ImportRowError",
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
                ("row_number", models.PositiveIntegerField()),
                ("creator_id", models.CharField(blank=True, max_length=160)),
                ("error_code", models.CharField(max_length=80)),
                ("error_field", models.CharField(blank=True, max_length=80)),
                ("error_message", models.TextField()),
                ("is_fatal", models.BooleanField(default=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "import_task",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="row_errors",
                        to="tasks.importtask",
                    ),
                ),
            ],
            options={"ordering": ["row_number", "id"]},
        ),
        migrations.CreateModel(
            name="ImportLog",
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
                ("step", models.CharField(max_length=80)),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("INFO", "信息"),
                            ("SUCCESS", "成功"),
                            ("WARNING", "警告"),
                            ("FAILED", "失败"),
                        ],
                        max_length=16,
                    ),
                ),
                ("message", models.TextField()),
                ("details_json", models.JSONField(blank=True, default=dict)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "import_task",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="logs",
                        to="tasks.importtask",
                    ),
                ),
            ],
            options={"ordering": ["created_at", "id"]},
        ),
        migrations.AddConstraint(
            model_name="importruleversion",
            constraint=models.UniqueConstraint(
                fields=("import_task", "version"),
                name="unique_import_rule_version",
            ),
        ),
        migrations.AddConstraint(
            model_name="importtaskcreator",
            constraint=models.UniqueConstraint(
                fields=("import_task", "creator"),
                name="unique_import_task_creator",
            ),
        ),
        migrations.AddConstraint(
            model_name="creatorsalesmetric",
            constraint=models.UniqueConstraint(
                fields=(
                    "import_task",
                    "creator",
                    "window_days",
                    "source_row_number",
                ),
                name="unique_import_creator_sales_row",
            ),
        ),
    ]

