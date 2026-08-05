from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("mailing", "0008_uncertain_delivery_status"),
    ]

    operations = [
        migrations.AlterField(
            model_name="emailsendingservice",
            name="status",
            field=models.CharField(
                choices=[
                    ("STOPPED", "已停止"),
                    ("RUNNING", "运行中"),
                    ("STOPPING", "正在停止"),
                    ("PAUSED", "已暂停"),
                ],
                db_index=True,
                default="STOPPED",
                max_length=16,
            ),
        ),
    ]
