import django.db.models.deletion
from django.db import migrations, models


def _normalize_creator_id(value):
    return str(value or "").strip().lstrip("@").strip().casefold()


def forwards(apps, schema_editor):
    EmailDelivery = apps.get_model("mailing", "EmailDelivery")
    Creator = apps.get_model("tasks", "Creator")
    for delivery in EmailDelivery.objects.select_related(
        "related_creator"
    ).iterator():
        source = delivery.related_creator
        creator_id = _normalize_creator_id(
            source.creator_handle
            if source is not None
            else delivery.creator_handle_snapshot
        )
        creator = Creator.objects.filter(creator_id=creator_id).first()
        delivery.creator_id_snapshot = creator_id
        if creator is not None:
            delivery.creator_id = creator.pk
            delivery.recipient_key = f"creator:{creator_id}"
        delivery.save(
            update_fields=[
                "creator",
                "creator_id_snapshot",
                "recipient_key",
            ]
        )


class Migration(migrations.Migration):
    dependencies = [
        ("tasks", "0004_migrate_legacy_creator_data"),
        ("mailing", "0002_emailsendingservice"),
    ]

    operations = [
        migrations.AddField(
            model_name="emaildelivery",
            name="creator",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="email_deliveries",
                to="tasks.creator",
            ),
        ),
        migrations.AddField(
            model_name="emaildelivery",
            name="creator_id_snapshot",
            field=models.CharField(blank=True, max_length=160),
        ),
        migrations.RunPython(forwards, migrations.RunPython.noop),
        migrations.RemoveField(
            model_name="emaildelivery",
            name="related_creator",
        ),
        migrations.RemoveField(
            model_name="emaildelivery",
            name="creator_handle_snapshot",
        ),
    ]

