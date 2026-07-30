import django.db.models.deletion
from django.db import migrations, models


def _normalize_creator_id(value):
    return str(value or "").strip().lstrip("@").strip().casefold()


def forwards(apps, schema_editor):
    CreatorContactTask = apps.get_model(
        "creator_contact",
        "CreatorContactTask",
    )
    CreatorContactTarget = apps.get_model(
        "creator_contact",
        "CreatorContactTarget",
    )
    ContactedCreator = apps.get_model(
        "creator_contact",
        "ContactedCreator",
    )
    Creator = apps.get_model("tasks", "Creator")

    for task in CreatorContactTask.objects.select_related(
        "source_product"
    ).iterator():
        task.source_import_task_id = task.source_product.task_id
        task.save(update_fields=["source_import_task"])

    for target in CreatorContactTarget.objects.select_related(
        "related_creator"
    ).iterator():
        source = target.related_creator
        creator_id = _normalize_creator_id(
            source.creator_handle
            if source is not None
            else target.creator_handle_snapshot
        )
        creator = Creator.objects.filter(creator_id=creator_id).first()
        if creator is not None:
            target.creator_id = creator.pk
            target.save(update_fields=["creator"])

    for contacted in ContactedCreator.objects.select_related(
        "related_creator"
    ).iterator():
        source = contacted.related_creator
        creator_id = _normalize_creator_id(
            source.creator_handle
            if source is not None
            else contacted.creator_handle
        )
        creator = Creator.objects.filter(creator_id=creator_id).first()
        if creator is not None:
            contacted.creator_id = creator.pk
            contacted.save(update_fields=["creator"])


class Migration(migrations.Migration):
    dependencies = [
        ("tasks", "0004_migrate_legacy_creator_data"),
        ("creator_contact", "0004_backfill_store_global_invited_creators"),
    ]

    operations = [
        migrations.AddField(
            model_name="creatorcontacttask",
            name="source_import_task",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="creator_contact_tasks",
                to="tasks.importtask",
            ),
        ),
        migrations.AddField(
            model_name="creatorcontacttarget",
            name="creator",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="contact_targets",
                to="tasks.creator",
            ),
        ),
        migrations.AddField(
            model_name="contactedcreator",
            name="creator",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="successful_contacts",
                to="tasks.creator",
            ),
        ),
        migrations.RunPython(forwards, migrations.RunPython.noop),
        migrations.RemoveField(
            model_name="creatorcontacttask",
            name="source_product",
        ),
        migrations.RemoveField(
            model_name="creatorcontacttarget",
            name="related_creator",
        ),
        migrations.RemoveField(
            model_name="contactedcreator",
            name="related_creator",
        ),
        migrations.AlterField(
            model_name="creatorcontacttask",
            name="source_import_task",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="creator_contact_tasks",
                to="tasks.importtask",
            ),
        ),
    ]

