from django.core.exceptions import ValidationError
from django.utils import timezone

from tasks.models import ImportTask

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
            import_task=self.import_task,
            store_id=self.store_id,
            top_n=3,
        )

        self.assertEqual(selection.excluded_count, 1)
        self.assertEqual(
            [creator.creator_id for creator in selection.creators],
            ["@Middle", "low", "null_revenue"],
        )

    def test_sales_ranking_supports_7_day_and_total_revenue(self):
        for creator, amount in (
            (self.high, 20),
            (self.middle, 900),
            (self.low, 100),
        ):
            self.create_creator(
                creator.creator_id,
                creator.nickname,
                revenue_30=None,
                revenue_7=None,
                revenue_total=amount,
                row=creator.import_memberships.get(
                    import_task=self.import_task
                ).first_row_number,
            )

        seven_day = select_candidates(
            import_task=self.import_task,
            store_id=self.store_id,
            top_n=3,
            sales_window_days=7,
        )
        total = select_candidates(
            import_task=self.import_task,
            store_id=self.store_id,
            top_n=3,
            sales_window_days=0,
        )

        self.assertEqual(
            [creator.creator_id for creator in seven_day.creators],
            ["null_revenue", "low", "@Middle"],
        )
        self.assertEqual(
            [creator.creator_id for creator in total.creators],
            ["@Middle", "low", "Highest"],
        )

    def test_creator_id_selects_batch_in_import_order_and_manual_preserves_order(self):
        by_id = select_candidates(
            import_task=self.import_task,
            store_id=self.store_id,
            selection_method="CREATOR_ID",
        )
        manual = select_candidates(
            import_task=self.import_task,
            store_id=self.store_id,
            selection_method="MANUAL",
            selected_creator_pks=[
                str(self.middle.pk),
                str(self.high.pk),
            ],
        )

        self.assertEqual(
            [creator.creator_id for creator in by_id.creators],
            ["Highest", "@Middle", "low", "null_revenue"],
        )
        self.assertEqual(by_id.unmatched_identifiers, ())
        self.assertEqual(
            [creator.creator_id for creator in manual.creators],
            ["@Middle", "Highest"],
        )

    def test_creator_id_rule_selects_at_most_50_candidates(self):
        for index in range(51):
            self.create_creator(
                f"extra-{index:02d}",
                f"Extra {index:02d}",
                revenue_30=None,
                revenue_7=None,
                row=10 + index,
            )

        selection = select_candidates(
            import_task=self.import_task,
            store_id=self.store_id,
            selection_method="CREATOR_ID",
        )

        self.assertEqual(selection.available_count, 55)
        self.assertEqual(len(selection.creators), 50)

    def test_freezes_rank_handle_and_revenue_snapshots(self):
        task = self.create_contact_task(top_n=2)
        freeze_task_targets(task)

        self.high.creator_id = "changed_after_creation"
        self.high.save(update_fields=["creator_id", "updated_at"])

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

        other_import = ImportTask.objects.create(
            file_name="other-import.xlsx",
            file_sha256="b" * 64,
            sheet_name="Creators",
            status=ImportTask.Status.SUCCESS,
            snapshot_date=timezone.localdate(),
            confirmed_at=timezone.now(),
            finished_at=timezone.now(),
        )
        self.create_creator(
            "@Highest",
            "Same creator on another import",
            revenue_30=999,
            revenue_7=999,
            row=2,
            import_task=other_import,
        )

        selection = select_candidates(
            import_task=other_import,
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
