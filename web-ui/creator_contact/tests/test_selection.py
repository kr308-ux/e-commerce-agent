from django.core.exceptions import ValidationError

from creator_contact.models import ContactedCreator
from creator_contact.services.candidate_selector import (
    freeze_task_targets,
    select_candidates,
)
from creator_contact.services.contact_runner import (
    record_successful_contact,
)

from .base import CreatorContactTestCase


class CandidateSelectionTests(CreatorContactTestCase):
    def test_orders_by_30_day_revenue_nulls_last_and_excludes_history(self):
        ContactedCreator.objects.create(
            store_id=self.store_id,
            normalized_handle="highest",
            creator_handle="Highest",
            greeting_sha256="a" * 64,
            invitation_id="old",
            invitation_name="Old invitation",
        )

        selection = select_candidates(
            product=self.product,
            store_id=self.store_id,
            top_n=3,
        )

        self.assertEqual(selection.excluded_count, 1)
        self.assertEqual(
            [creator.creator_handle for creator in selection.creators],
            ["@Middle", "low", "null_revenue"],
        )

    def test_freezes_rank_handle_and_revenue_snapshots(self):
        task = self.create_contact_task(top_n=2)
        freeze_task_targets(task)

        self.high.creator_handle = "changed_after_creation"
        self.high.recent_30_day_revenue = 1
        self.high.save()

        targets = list(task.targets.order_by("rank"))
        self.assertEqual([target.rank for target in targets], [1, 2])
        self.assertEqual(targets[0].normalized_handle, "highest")
        self.assertEqual(
            str(targets[0].recent_30_day_revenue_snapshot),
            "500.00",
        )
        self.assertEqual(
            str(targets[0].recent_7_day_revenue_snapshot),
            "10.00",
        )

    def test_contact_history_requires_terminal_send_verification(self):
        task = self.create_contact_task(top_n=1)
        freeze_task_targets(task)
        target = task.targets.get()
        target.message_sent = True
        target.card_sent = True
        target.target_plan_message_verified = True
        target.actual_invitation_id = "7666768491349280526"
        target.invitation_group_id = task.invitation_id_snapshot
        target.final_send_verified = False
        target.result = {
            "success": True,
            "invitationId": "7666768491349280526",
            "invitationGroupId": task.invitation_id_snapshot,
            "planCardServerIds": ["server-message-1"],
            "targetPlanFlightStatus": 3,
            "creatorTabsClosed": True,
            "searchTabKept": True,
            "returnedToFindCreators": True,
        }
        target.save()

        with self.assertRaises(ValidationError):
            record_successful_contact(target)
        self.assertFalse(ContactedCreator.objects.exists())

        target.final_send_verified = True
        target.chat_creator_id = "7493994012378827459"
        target.save()
        record = record_successful_contact(target)

        self.assertEqual(record.normalized_handle, "highest")
        self.assertEqual(
            record.chat_creator_id,
            "7493994012378827459",
        )
        self.assertEqual(record.invitation_id, "7666768491349280526")
        self.assertEqual(
            record.evidence["invitationGroupId"],
            task.invitation_id_snapshot,
        )

    def test_contact_history_rejects_invalid_terminal_server_evidence(self):
        cases = (
            {"invitationGroupId": "7664550207413847999"},
            {"invitationId": "not-numeric"},
            {"planCardServerIds": []},
            {"targetPlanFlightStatus": 1},
        )
        for replacement in cases:
            with self.subTest(replacement=replacement):
                task = self.create_contact_task(top_n=1)
                freeze_task_targets(task)
                target = task.targets.get()
                result = {
                    "success": True,
                    "invitationId": "7666768491349280526",
                    "invitationGroupId": task.invitation_id_snapshot,
                    "planCardServerIds": ["server-message-1"],
                    "targetPlanFlightStatus": 4,
                    "creatorTabsClosed": True,
                    "searchTabKept": True,
                    "returnedToFindCreators": True,
                }
                result.update(replacement)
                target.message_sent = True
                target.card_sent = True
                target.target_plan_message_verified = True
                target.final_send_verified = True
                target.actual_invitation_id = str(result["invitationId"])
                target.invitation_group_id = str(
                    result["invitationGroupId"]
                )
                target.result = result
                target.save()

                with self.assertRaises(ValidationError):
                    record_successful_contact(target)
                self.assertFalse(
                    ContactedCreator.objects.filter(
                        contact_task=task,
                    ).exists()
                )
