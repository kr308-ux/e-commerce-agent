from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("mailing", "0007_daily_retry_attempt_history"),
    ]

    operations = [
        migrations.AlterField(
            model_name="emaildelivery",
            name="status",
            field=models.CharField(
                choices=[
                    ("PENDING", "待发送"),
                    ("SENDING", "发送中"),
                    ("SENT", "发送成功"),
                    ("FAILED", "发送失败"),
                    ("RETRY_WAITING", "等待明日重试"),
                    ("UNCERTAIN", "送达状态待确认"),
                    ("SKIPPED", "已跳过"),
                ],
                db_index=True,
                default="PENDING",
                max_length=16,
            ),
        ),
        migrations.AlterField(
            model_name="emaildeliveryattempt",
            name="status",
            field=models.CharField(
                choices=[
                    ("STARTED", "发送中"),
                    ("SENT", "发送成功"),
                    ("FAILED", "发送失败"),
                    ("UNCERTAIN", "送达状态待确认"),
                ],
                db_index=True,
                default="STARTED",
                max_length=16,
            ),
        ),
    ]
