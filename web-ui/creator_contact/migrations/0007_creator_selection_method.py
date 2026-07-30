from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("creator_contact", "0006_contact_dom_fallback_model"),
    ]

    operations = [
        migrations.AddField(
            model_name="creatorcontacttask",
            name="selection_method",
            field=models.CharField(
                choices=[
                    ("CREATOR_ID", "按达人 ID"),
                    ("SALES", "按销售额"),
                    ("MANUAL", "手动勾选"),
                ],
                default="SALES",
                max_length=24,
            ),
        ),
        migrations.AddField(
            model_name="creatorcontacttask",
            name="sales_window_days",
            field=models.PositiveSmallIntegerField(
                choices=[
                    (0, "总销售额"),
                    (7, "近 7 天"),
                    (30, "近 30 天"),
                ],
                default=30,
            ),
        ),
        migrations.AddField(
            model_name="creatorcontacttarget",
            name="total_revenue_snapshot",
            field=models.DecimalField(
                blank=True,
                decimal_places=2,
                max_digits=22,
                null=True,
            ),
        ),
    ]
