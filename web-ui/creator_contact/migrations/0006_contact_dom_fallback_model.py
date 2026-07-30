from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("creator_contact", "0005_creator_import_sources"),
    ]

    operations = [
        migrations.AlterField(
            model_name="creatorcontacttask",
            name="model_name",
            field=models.CharField(
                default="deepseek/deepseek-v4-pro",
                max_length=100,
            ),
        ),
    ]
