from django.db import migrations, models


def mark_existing_tasks_completed(apps, schema_editor):
    CreatorContactTask = apps.get_model(
        "creator_contact",
        "CreatorContactTask",
    )
    CreatorContactTask.objects.update(email_dispatch_status="COMPLETED")


class Migration(migrations.Migration):
    dependencies = [
        ("creator_contact", "0009_outreach_query_indexes"),
    ]

    operations = [
        migrations.AddField(
            model_name="creatorcontacttask",
            name="email_dispatch_status",
            field=models.CharField(
                choices=[
                    ("PENDING", "等待邮件入队"),
                    ("COMPLETED", "邮件入队完成"),
                    ("FAILED", "邮件入队失败"),
                ],
                db_index=True,
                default="PENDING",
                max_length=16,
            ),
        ),
        migrations.AddField(
            model_name="creatorcontacttask",
            name="email_dispatch_summary",
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddField(
            model_name="creatorcontacttask",
            name="email_dispatched_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.RunPython(
            mark_existing_tasks_completed,
            migrations.RunPython.noop,
        ),
    ]
