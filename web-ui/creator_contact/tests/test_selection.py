from django.core.exceptions import ValidationError

from creator_contact.models import ContactedCreator
from creator_contact.services.candidate_selector import (
    freeze_task_targets,
    select_candidates,
)
from creator_contact.services.contact_runner import (
    record_invited_creator,
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

    def test_invitation_receipt_creates_store_global_contact_history(self):
        task = self.create_contact_task(top_n=1)
        freeze_task_targets(task)
        target = task.targets.get()
        target.message_sent = True
        target.invitation_created = True
        target.invitation_group_id = task.invitation_id_snapshot
        target.result = {
            "success": True,
            "invitationGroupId": task.invitation_id_snapshot,
            "invitationCompleted": True,
            "invitationButtonClicked": True,
            "invitationSubmissionConfirmed": True,
            "creatorTabsClosed": True,
            "creatorDetailTargetGone": True,
            "searchTabKept": True,
            "returnedToFindCreators": True,
            "findCreatorsSearchReady": True,
        }
        target.save()

        invited_record = record_invited_creator(target)
        self.assertEqual(
            invited_record.evidence["contactStage"],
            "INVITATION_COMPLETED",
        )

        with self.assertRaises(ValidationError):
            record_successful_contact(target)
        self.assertEqual(ContactedCreator.objects.count(), 1)

        target.actual_invitation_id = "7666768491349280521"
        target.card_sent = True
        target.target_plan_message_verified = True
        target.final_send_verified = True
        target.result.update(
            {
                "invitationId": target.actual_invitation_id,
                "deliverySource": "accepted_creator_list",
                "acceptedCreatorsPageVisible": True,
                "projectMembershipVerified": True,
                "recipientVerified": True,
                "cardSent": True,
                "targetPlanMessageVerified": True,
                "planCardServerIds": ["server-accepted-1"],
                "targetPlanFlightStatus": 3,
                "finalSendVerified": True,
            }
        )
        target.chat_creator_id = "7493994012378827459"
        target.save()
        record = record_successful_contact(target)

        self.assertEqual(record.normalized_handle, "highest")
        self.assertEqual(
            record.chat_creator_id,
            "7493994012378827459",
        )
        self.assertEqual(record.invitation_id, "7666768491349280521")
        self.assertEqual(
            record.evidence["invitationGroupId"],
            task.invitation_id_snapshot,
        )
        self.assertTrue(record.evidence["projectMembershipVerified"])
        self.assertTrue(record.evidence["finalSendVerified"])
        self.assertEqual(
            record.evidence["contactStage"],
            "CARD_DELIVERED",
        )

    def test_invitation_history_excludes_same_creator_from_other_project(
        self,
    ) -> None:
        task = self.create_contact_task(top_n=1)
        freeze_task_targets(task)
        target = task.targets.get()
        target.message_sent = True
        target.invitation_created = True
        target.invitation_group_id = task.invitation_id_snapshot
        target.result = {
            "success": True,
            "messageSent": True,
            "invitationGroupId": task.invitation_id_snapshot,
            "invitationCompleted": True,
            "invitationButtonClicked": True,
        }
        target.save()
        record_invited_creator(target)

        other_product = self.product.__class__.objects.create(
            task=self.acquisition_task,
            external_product_id="other-product",
            name="Other Product",
            product_url="https://example.test/other-product",
        )
        self.high.__class__.objects.create(
            product=other_product,
            creator_handle="@Highest",
            nickname="Same creator on another project",
            recent_30_day_revenue=999,
            recent_7_day_revenue=999,
        )

        selection = select_candidates(
            product=other_product,
            store_id=self.store_id,
            top_n=3,
        )

        self.assertEqual(selection.excluded_count, 1)
        self.assertEqual(selection.creators, ())

    def test_contact_history_rejects_invalid_card_evidence(self):
        cases = (
            {"invitationGroupId": "7664550207413847999"},
            {"projectMembershipVerified": False},
            {"recipientVerified": False},
            {"planCardServerIds": []},
            {"targetPlanFlightStatus": 2},
            {"finalSendVerified": False},
        )
        for replacement in cases:
            with self.subTest(replacement=replacement):
                task = self.create_contact_task(top_n=1)
                freeze_task_targets(task)
                target = task.targets.get()
                result = {
                    "success": True,
                    "invitationId": "7666768491349280521",
                    "invitationGroupId": task.invitation_id_snapshot,
                    "invitationCompleted": True,
                    "invitationButtonClicked": True,
                    "invitationSubmissionConfirmed": True,
                    "creatorTabsClosed": True,
                    "creatorDetailTargetGone": True,
                    "searchTabKept": True,
                    "returnedToFindCreators": True,
                    "findCreatorsSearchReady": True,
                    "deliverySource": "accepted_creator_list",
                    "acceptedCreatorsPageVisible": True,
                    "projectMembershipVerified": True,
                    "recipientVerified": True,
                    "cardSent": True,
                    "targetPlanMessageVerified": True,
                    "planCardServerIds": ["server-accepted-1"],
                    "targetPlanFlightStatus": 3,
                    "finalSendVerified": True,
                }
                result.update(replacement)
                target.message_sent = True
                target.invitation_created = True
                target.actual_invitation_id = (
                    "7666768491349280521"
                )
                target.invitation_group_id = str(
                    result["invitationGroupId"]
                )
                target.card_sent = True
                target.target_plan_message_verified = True
                target.final_send_verified = (
                    result.get("finalSendVerified") is True
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
