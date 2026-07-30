from django.db import migrations, models


def backfill_created_at(apps, schema_editor):
    RelatedCreator = apps.get_model("tasks", "RelatedCreator")
    for creator in RelatedCreator.objects.only("pk", "collected_at").iterator():
        RelatedCreator.objects.filter(pk=creator.pk).update(
            created_at=creator.collected_at,
        )


class Migration(migrations.Migration):
    dependencies = [
        ("tasks", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="relatedcreator",
            name="created_at",
            field=models.DateTimeField(null=True),
        ),
        migrations.RunPython(
            backfill_created_at,
            migrations.RunPython.noop,
        ),
        migrations.AlterField(
            model_name="relatedcreator",
            name="created_at",
            field=models.DateTimeField(auto_now_add=True),
        ),
    ]
