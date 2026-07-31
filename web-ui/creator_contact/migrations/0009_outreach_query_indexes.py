from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("creator_contact", "0008_creatorcontacttarget_review_required"),
    ]

    operations = [
        migrations.AlterField(
            model_name="contactedcreator",
            name="contacted_at",
            field=models.DateTimeField(auto_now=True, db_index=True),
        ),
        migrations.AlterField(
            model_name="contactedcreator",
            name="normalized_handle",
            field=models.CharField(db_index=True, max_length=160),
        ),
        migrations.AlterField(
            model_name="creatorcontacttarget",
            name="finished_at",
            field=models.DateTimeField(
                blank=True,
                db_index=True,
                null=True,
            ),
        ),
        migrations.AddIndex(
            model_name="contactedcreator",
            index=models.Index(
                fields=["store_id", "-contacted_at"],
                name="contact_store_date_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="creatorcontacttarget",
            index=models.Index(
                fields=["status", "-finished_at"],
                name="contact_status_date_idx",
            ),
        ),
    ]
