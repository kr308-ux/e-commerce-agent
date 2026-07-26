from unittest.mock import patch

from django.http import HttpResponse
from django.test import override_settings
from django.urls import reverse

from creator_contact.models import (
    CollaborationSyncJob,
    CreatorContactTask,
    GreetingTemplate,
)
from creator_contact.services.candidate_selector import freeze_task_targets

from .base import CreatorContactTestCase


@override_settings(ZINIAO_CONTACT_STORE_ID="store-test-1")
class CreatorContactViewTests(CreatorContactTestCase):
    def test_dashboard_exposes_required_context_contract(self):
        with patch("creator_contact.views.render") as mocked_render:
            mocked_render.return_value = HttpResponse("ok")
            response = self.client.get(reverse("creator_contact:dashboard"))

        self.assertEqual(response.status_code, 200)
        context = mocked_render.call_args.args[2]
        for key in (
            "form",
            "greeting_templates",
            "collaboration_options",
            "source_tasks",
            "products",
            "candidate_preview",
            "excluded_count",
            "latest_contact_tasks",
            "store_id",
            "sync_status",
        ):
            self.assertIn(key, context)

    def test_dashboard_and_task_detail_templates_render(self):
        dashboard = self.client.get(reverse("creator_contact:dashboard"))
        task = self.create_contact_task(top_n=1)
        freeze_task_targets(task)
        detail = self.client.get(
            reverse(
                "creator_contact:task_detail",
                kwargs={"task_id": task.pk},
            )
        )

        self.assertEqual(dashboard.status_code, 200)
        self.assertContains(dashboard, "达人联系")
        self.assertEqual(detail.status_code, 200)
        self.assertContains(detail, "目标达人执行状态")

    def test_candidates_returns_ranked_json(self):
        response = self.client.get(
            reverse("creator_contact:candidates"),
            {
                "product_id": self.product.pk,
                "top_n": 2,
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [row["creatorHandle"] for row in response.json()["creators"]],
            ["highest", "middle"],
        )

    def test_create_task_snapshots_configuration_and_targets(self):
        response = self.client.post(
            reverse("creator_contact:dashboard"),
            {
                "action": "create_task",
                "store_id": self.store_id,
                "source_product": self.product.pk,
                "top_n": 3,
                "greeting_template": self.greeting.pk,
                "collaboration_option": self.invitation.pk,
                "confirm_send_greeting": "on",
                "confirm_send_invitation": "on",
                "confirm_send_card": "on",
            },
        )

        task = CreatorContactTask.objects.get()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response.url,
            reverse(
                "creator_contact:task_detail",
                kwargs={"task_id": task.pk},
            ),
        )
        self.assertEqual(task.greeting_snapshot, "Hello creator")
        self.assertEqual(task.invitation_name_snapshot, "金色拉链+短裤13")
        self.assertEqual(task.targets.count(), 3)

    def test_save_greeting_and_enqueue_collaboration_sync(self):
        greeting_response = self.client.post(
            reverse("creator_contact:dashboard"),
            {
                "action": "save_greeting",
                "name": "Second greeting",
                "content": "A saved message",
            },
        )
        sync_response = self.client.post(
            reverse("creator_contact:sync_collaborations"),
            HTTP_ACCEPT="application/json",
        )

        self.assertEqual(greeting_response.status_code, 302)
        self.assertTrue(
            GreetingTemplate.objects.filter(name="Second greeting").exists()
        )
        self.assertEqual(sync_response.status_code, 202)
        self.assertEqual(CollaborationSyncJob.objects.count(), 1)

    def test_saving_an_existing_greeting_name_updates_it(self):
        response = self.client.post(
            reverse("creator_contact:dashboard"),
            {
                "action": "save_greeting",
                "name": self.greeting.name,
                "content": "Updated saved message",
                "is_default": "on",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.greeting.refresh_from_db()
        self.assertEqual(self.greeting.content, "Updated saved message")
        self.assertEqual(
            GreetingTemplate.objects.filter(
                name=self.greeting.name
            ).count(),
            1,
        )

    def test_saving_a_new_default_replaces_the_previous_default(self):
        response = self.client.post(
            reverse("creator_contact:dashboard"),
            {
                "action": "save_greeting",
                "name": "New default",
                "content": "New default message",
                "is_default": "on",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.greeting.refresh_from_db()
        self.assertFalse(self.greeting.is_default)
        self.assertTrue(
            GreetingTemplate.objects.get(name="New default").is_default
        )

    def test_detail_context_and_status_json(self):
        task = self.create_contact_task(top_n=1)
        freeze_task_targets(task)
        with patch("creator_contact.views.render") as mocked_render:
            mocked_render.return_value = HttpResponse("ok")
            detail = self.client.get(
                reverse(
                    "creator_contact:task_detail",
                    kwargs={"task_id": task.pk},
                )
            )
        status = self.client.get(
            reverse(
                "creator_contact:task_status",
                kwargs={"task_id": task.pk},
            )
        )

        self.assertEqual(detail.status_code, 200)
        context = mocked_render.call_args.args[2]
        self.assertEqual(context["contact_task"], task)
        self.assertIn("targets", context)
        self.assertIn("steps", context)
        self.assertEqual(status.status_code, 200)
        self.assertEqual(status.json()["targetCount"], 1)
