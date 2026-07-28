from django.db import migrations


def backfill_store_global_invited_creators(apps, schema_editor):
    ContactedCreator = apps.get_model(
        "creator_contact",
        "ContactedCreator",
    )
    CreatorContactTarget = apps.get_model(
        "creator_contact",
        "CreatorContactTarget",
    )

    targets = (
        CreatorContactTarget.objects
        .filter(message_sent=True)
        .select_related("task", "related_creator")
        .order_by("created_at", "pk")
    )
    for target in targets.iterator():
        task = target.task
        result = target.result if isinstance(target.result, dict) else {}
        expected_group_id = str(task.invitation_id_snapshot or "")
        result_group_id = str(result.get("invitationGroupId") or "")
        target_group_id = str(target.invitation_group_id or "")
        group_matches = (
            bool(expected_group_id)
            and target_group_id == expected_group_id
            and result_group_id == expected_group_id
        )
        clicked = result.get("invitationButtonClicked") is True
        already_sent = result.get("alreadySent") is True
        legacy_created = target.invitation_created is True
        if not (
            group_matches
            and (clicked or already_sent or legacy_created)
        ):
            continue

        receipt_source = (
            "invitation_button_clicked"
            if clicked
            else (
                "already_sent"
                if already_sent
                else "legacy_invitation_created_flag"
            )
        )
        ContactedCreator.objects.get_or_create(
            store_id=task.store_id,
            normalized_handle=target.normalized_handle,
            defaults={
                "creator_handle": target.creator_handle_snapshot,
                "chat_creator_id": target.chat_creator_id,
                "related_creator_id": target.related_creator_id,
                "contact_task_id": task.pk,
                "greeting_sha256": task.greeting_sha256,
                "invitation_id": target.actual_invitation_id,
                "invitation_name": task.invitation_name_snapshot,
                "evidence": {
                    "contactScope": "store_creator",
                    "contactStage": "INVITATION_COMPLETED",
                    "receiptSource": receipt_source,
                    "targetId": target.pk,
                    "messageSent": target.message_sent,
                    "creatorId": target.chat_creator_id,
                    "invitationGroupId": target_group_id,
                    "invitationCompleted": (
                        result.get("invitationCompleted") is True
                    ),
                    "invitationButtonClicked": clicked,
                    "alreadySent": already_sent,
                    "creatorTabsClosed": (
                        result.get("creatorTabsClosed") is True
                    ),
                    "returnedToFindCreators": (
                        result.get("returnedToFindCreators") is True
                    ),
                    "backfilled": True,
                },
            },
        )


class Migration(migrations.Migration):
    dependencies = [
        (
            "creator_contact",
            "0003_creatorcontacttarget_invitation_completed",
        ),
    ]

    operations = [
        migrations.RunPython(
            backfill_store_global_invited_creators,
            migrations.RunPython.noop,
        ),
    ]
