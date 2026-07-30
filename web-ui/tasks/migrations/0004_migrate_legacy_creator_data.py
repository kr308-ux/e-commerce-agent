import hashlib

from django.db import migrations


def _normalize_creator_id(value):
    return str(value or "").strip().lstrip("@").strip().casefold()


def forwards(apps, schema_editor):
    CreatorAcquisitionTask = apps.get_model(
        "tasks",
        "CreatorAcquisitionTask",
    )
    RelatedCreator = apps.get_model("tasks", "RelatedCreator")
    Creator = apps.get_model("tasks", "Creator")
    ImportTask = apps.get_model("tasks", "ImportTask")
    ImportRuleVersion = apps.get_model("tasks", "ImportRuleVersion")
    ImportTaskCreator = apps.get_model("tasks", "ImportTaskCreator")
    CreatorSalesMetric = apps.get_model("tasks", "CreatorSalesMetric")

    for legacy_task in CreatorAcquisitionTask.objects.order_by("created_at"):
        timestamp = (
            legacy_task.finished_at
            or legacy_task.started_at
            or legacy_task.created_at
        )
        import_task, _ = ImportTask.objects.get_or_create(
            pk=legacy_task.pk,
            defaults={
                "file_name": (
                    f"历史浏览器采集-{str(legacy_task.pk)[:8]}.xlsx"
                ),
                "file_sha256": hashlib.sha256(
                    str(legacy_task.pk).encode("utf-8")
                ).hexdigest(),
                "sheet_name": "历史数据",
                "status": "SUCCESS",
                "confirmed_rule_version": 1,
                "current_step": "历史数据迁移完成",
                "snapshot_date": timestamp.date(),
                "confirmed_at": timestamp,
                "started_at": legacy_task.started_at,
                "finished_at": legacy_task.finished_at or timestamp,
            },
        )
        ImportRuleVersion.objects.get_or_create(
            import_task=import_task,
            version=1,
            defaults={
                "rule_json": {"legacy_migration": True},
                "source": "SYSTEM",
            },
        )

        legacy_creators = RelatedCreator.objects.filter(
            product__task_id=legacy_task.pk
        ).order_by("created_at", "pk")
        success_rows = 0
        metric_count = 0
        creator_ids = set()
        for row_number, legacy_creator in enumerate(
            legacy_creators.iterator(),
            start=1,
        ):
            creator_id = _normalize_creator_id(
                legacy_creator.creator_handle
            )
            if not creator_id:
                continue
            creator, created = Creator.objects.get_or_create(
                creator_id=creator_id,
                defaults={
                    "nickname": legacy_creator.nickname,
                    "email": legacy_creator.email,
                },
            )
            changed = []
            if (
                not created
                and legacy_creator.nickname
                and legacy_creator.nickname != creator.nickname
            ):
                creator.nickname = legacy_creator.nickname
                changed.append("nickname")
            if (
                not created
                and legacy_creator.email
                and legacy_creator.email != creator.email
            ):
                creator.email = legacy_creator.email
                changed.append("email")
            if changed:
                creator.save(update_fields=changed)

            ImportTaskCreator.objects.get_or_create(
                import_task=import_task,
                creator=creator,
                defaults={
                    "first_row_number": row_number,
                    "row_status": "SUCCESS",
                },
            )
            creator_ids.add(creator.pk)
            success_rows += 1
            for window_days, amount in (
                (7, legacy_creator.recent_7_day_revenue),
                (30, legacy_creator.recent_30_day_revenue),
            ):
                if amount is None:
                    continue
                CreatorSalesMetric.objects.get_or_create(
                    import_task=import_task,
                    creator=creator,
                    window_days=window_days,
                    source_row_number=legacy_creator.pk,
                    defaults={
                        "sales_amount": amount,
                        "snapshot_date": (
                            legacy_creator.collected_at.date()
                            if legacy_creator.collected_at
                            else import_task.snapshot_date
                        ),
                    },
                )
                metric_count += 1

        import_task.total_rows = success_rows
        import_task.processed_rows = success_rows
        import_task.success_rows = success_rows
        import_task.new_creator_count = len(creator_ids)
        import_task.sales_metric_count = metric_count
        import_task.save(
            update_fields=[
                "total_rows",
                "processed_rows",
                "success_rows",
                "new_creator_count",
                "sales_metric_count",
                "updated_at",
            ]
        )


class Migration(migrations.Migration):
    dependencies = [
        ("tasks", "0003_creator_import_models"),
    ]

    operations = [
        migrations.RunPython(forwards, migrations.RunPython.noop),
    ]

