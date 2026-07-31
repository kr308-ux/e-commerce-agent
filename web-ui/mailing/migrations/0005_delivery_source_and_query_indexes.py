from django.db import migrations, models
from django.db.models import Count
import django.db.models.deletion


def backfill_unambiguous_source_batches(apps, schema_editor):
    EmailDelivery = apps.get_model("mailing", "EmailDelivery")
    ImportTaskCreator = apps.get_model("tasks", "ImportTaskCreator")

    unique_creator_ids = set(
        ImportTaskCreator.objects
        .values("creator_id")
        .annotate(batch_count=Count("import_task_id", distinct=True))
        .filter(batch_count=1)
        .values_list("creator_id", flat=True)
    )
    if not unique_creator_ids:
        return

    source_by_creator = {
        creator_id: import_task_id
        for creator_id, import_task_id in (
            ImportTaskCreator.objects
            .values_list("creator_id", "import_task_id")
            .iterator(chunk_size=2000)
        )
        if creator_id in unique_creator_ids
    }
    pending_updates = []
    deliveries = (
        EmailDelivery.objects
        .filter(
            source_import_task_id__isnull=True,
            creator_id__isnull=False,
        )
        .only("id", "creator_id", "source_import_task_id")
        .iterator(chunk_size=1000)
    )
    for delivery in deliveries:
        source_id = source_by_creator.get(delivery.creator_id)
        if source_id is None:
            continue
        delivery.source_import_task_id = source_id
        pending_updates.append(delivery)
        if len(pending_updates) >= 1000:
            EmailDelivery.objects.bulk_update(
                pending_updates,
                ["source_import_task"],
                batch_size=1000,
            )
            pending_updates.clear()
    if pending_updates:
        EmailDelivery.objects.bulk_update(
            pending_updates,
            ["source_import_task"],
            batch_size=1000,
        )


class Migration(migrations.Migration):

    dependencies = [
        ("mailing", "0004_email_templates"),
        ("tasks", "0005_remove_browser_acquisition_models"),
    ]

    operations = [
        migrations.AddField(
            model_name="emaildelivery",
            name="source_import_task",
            field=models.ForeignKey(
                blank=True,
                help_text="创建或重新入队该邮件时选择的导入批次。",
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="email_deliveries",
                to="tasks.importtask",
            ),
        ),
        migrations.AlterField(
            model_name="emaildelivery",
            name="creator_id_snapshot",
            field=models.CharField(
                blank=True,
                db_index=True,
                max_length=160,
            ),
        ),
        migrations.AlterField(
            model_name="emaildelivery",
            name="last_attempt_at",
            field=models.DateTimeField(
                blank=True,
                db_index=True,
                null=True,
            ),
        ),
        migrations.AddIndex(
            model_name="emaildelivery",
            index=models.Index(
                fields=["status", "-updated_at"],
                name="mail_status_update_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="emaildelivery",
            index=models.Index(
                fields=["source_import_task", "-updated_at"],
                name="mail_batch_update_idx",
            ),
        ),
        migrations.RunPython(
            backfill_unambiguous_source_batches,
            migrations.RunPython.noop,
        ),
    ]
