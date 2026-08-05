from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("creator_contact", "0010_contact_email_dispatch"),
    ]

    operations = [
        migrations.AddField(
            model_name="creatorcontacttask",
            name="active_process_id",
            field=models.PositiveIntegerField(blank=True, null=True),
        ),
    ]
