from django.db import migrations
from django.db.models import OuterRef, Subquery


def backfill_source_from_verified_contacts(apps, schema_editor):
    EmailDelivery = apps.get_model("mailing", "EmailDelivery")
    CreatorContactTarget = apps.get_model(
        "creator_contact",
        "CreatorContactTarget",
    )

    latest_verified_contact = (
        CreatorContactTarget.objects
        .filter(
            creator_id=OuterRef("creator_id"),
            status="SUCCESS",
            card_sent=True,
            final_send_verified=True,
            finished_at__isnull=False,
            finished_at__lte=OuterRef("created_at"),
            task__source_import_task_id__isnull=False,
        )
        .order_by("-finished_at", "-id")
        .values("task__source_import_task_id")[:1]
    )
    EmailDelivery.objects.filter(
        source_import_task_id__isnull=True,
        creator_id__isnull=False,
    ).update(
        source_import_task_id=Subquery(latest_verified_contact),
    )


class Migration(migrations.Migration):

    dependencies = [
        ("creator_contact", "0009_outreach_query_indexes"),
        ("mailing", "0005_delivery_source_and_query_indexes"),
    ]

    operations = [
        migrations.RunPython(
            backfill_source_from_verified_contacts,
            migrations.RunPython.noop,
        ),
    ]
