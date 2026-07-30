from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("tasks", "0004_migrate_legacy_creator_data"),
        ("creator_contact", "0005_creator_import_sources"),
        ("mailing", "0003_creator_reference"),
    ]

    operations = [
        migrations.DeleteModel(name="TaskStep"),
        migrations.DeleteModel(name="CreatorExportArtifact"),
        migrations.DeleteModel(name="RelatedCreator"),
        migrations.DeleteModel(name="Product"),
        migrations.DeleteModel(name="CreatorAcquisitionTask"),
    ]

