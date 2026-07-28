from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("creator_contact", "0002_seed_vaelos_greeting"),
    ]

    operations = [
        migrations.AlterField(
            model_name="creatorcontacttarget",
            name="status",
            field=models.CharField(
                choices=[
                    ("PENDING", "等待中"),
                    ("RUNNING", "执行中"),
                    (
                        "INVITATION_COMPLETED",
                        "完成定向合作邀请",
                    ),
                    ("SUCCESS", "已完成"),
                    ("FAILED", "失败"),
                    ("SKIPPED", "已跳过"),
                ],
                db_index=True,
                default="PENDING",
                max_length=24,
            ),
        ),
    ]
