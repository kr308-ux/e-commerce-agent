from __future__ import annotations

from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.core import mail
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from tasks.models import Creator, ImportTask, ImportTaskCreator

from mailing.models import (
    EmailDelivery,
    EmailSendingService,
    EmailTemplateAsset,
    recipient_key_for,
)
from mailing.services.email_sender import build_creator_email
from mailing.services.runtime import get_service_state
from mailing.services.rich_text import sanitize_rich_html
from mailing.services.template_content import (
    get_active_template_version,
    normalize_content_blocks,
    save_rich_template_version,
    save_template_version,
)


class MailingTestCase(TestCase):
    def setUp(self) -> None:
        self.import_task = ImportTask.objects.create(
            file_name="email-creators.xlsx",
            file_sha256="a" * 64,
            sheet_name="Creators",
            status=ImportTask.Status.SUCCESS,
            snapshot_date=timezone.localdate(),
            confirmed_at=timezone.now(),
            finished_at=timezone.now(),
        )

    def create_creator(
        self,
        *,
        handle: str = "@CreatorOne",
        nickname: str = "Creator One",
        email: str = "Creator@Example.com",
        import_task: ImportTask | None = None,
    ) -> Creator:
        creator, _ = Creator.objects.get_or_create(
            creator_id=handle,
            defaults={"nickname": nickname, "email": email},
        )
        ImportTaskCreator.objects.get_or_create(
            import_task=import_task or self.import_task,
            creator=creator,
            defaults={"first_row_number": creator.pk + 1},
        )
        return creator

    def create_delivery(
        self,
        *,
        creator: Creator | None = None,
        status: str = EmailDelivery.Status.PENDING,
    ) -> EmailDelivery:
        creator = creator or self.create_creator()
        return EmailDelivery.objects.create(
            creator=creator,
            recipient_key="recalculated-on-save",
            creator_id_snapshot=creator.creator_id,
            creator_name_snapshot=creator.nickname,
            recipient_email=creator.email,
            status=status,
        )


class EmailDeliveryModelTests(MailingTestCase):
    def test_recipient_key_prefers_normalized_creator_handle(self) -> None:
        self.assertEqual(
            recipient_key_for(
                creator_id=" @CreatorOne ",
                email="OTHER@example.com",
            ),
            "creator:creatorone",
        )
        self.assertEqual(
            recipient_key_for(
                creator_id="",
                email=" Creator@Example.COM ",
            ),
            "email:creator@example.com",
        )

    def test_delivery_normalizes_snapshots_on_save(self) -> None:
        delivery = self.create_delivery()

        self.assertEqual(delivery.recipient_key, "creator:creatorone")
        self.assertEqual(delivery.creator_id_snapshot, "creatorone")
        self.assertEqual(
            delivery.recipient_email,
            "creator@example.com",
        )


class QueueCreatorEmailsCommandTests(MailingTestCase):
    def test_dry_run_does_not_write_queue(self) -> None:
        self.create_creator()

        call_command(
            "queue_creator_emails",
            import_task_id=self.import_task.pk,
            limit=100,
            dry_run=True,
            stdout=StringIO(),
        )

        self.assertFalse(EmailDelivery.objects.exists())

    def test_queue_reuses_global_handle_and_skips_sent_creator(self) -> None:
        creator = self.create_creator()
        call_command(
            "queue_creator_emails",
            import_task_id=self.import_task.pk,
            limit=100,
            stdout=StringIO(),
        )
        delivery = EmailDelivery.objects.get()
        delivery.status = EmailDelivery.Status.SENT
        delivery.sent_at = timezone.now()
        delivery.save(update_fields=["status", "sent_at", "updated_at"])

        second_import = ImportTask.objects.create(
            file_name="second-import.xlsx",
            file_sha256="b" * 64,
            sheet_name="Creators",
            status=ImportTask.Status.SUCCESS,
            snapshot_date=timezone.localdate(),
            confirmed_at=timezone.now(),
            finished_at=timezone.now(),
        )
        self.create_creator(
            handle=creator.creator_id.lower(),
            email="new-address@example.com",
            import_task=second_import,
        )
        call_command(
            "queue_creator_emails",
            import_task_id=second_import.pk,
            limit=100,
            stdout=StringIO(),
        )

        self.assertEqual(EmailDelivery.objects.count(), 1)
        delivery.refresh_from_db()
        self.assertEqual(delivery.status, EmailDelivery.Status.SENT)
        self.assertEqual(
            delivery.recipient_email,
            "creator@example.com",
        )

    def test_failed_delivery_requires_explicit_retry(self) -> None:
        delivery = self.create_delivery()
        delivery.status = EmailDelivery.Status.FAILED
        delivery.attempt_count = 1
        delivery.save(
            update_fields=["status", "attempt_count", "updated_at"]
        )

        call_command(
            "queue_creator_emails",
            import_task_id=self.import_task.pk,
            limit=100,
            stdout=StringIO(),
        )
        delivery.refresh_from_db()
        self.assertEqual(delivery.status, EmailDelivery.Status.FAILED)

        call_command(
            "queue_creator_emails",
            import_task_id=self.import_task.pk,
            limit=100,
            retry_failed=True,
            stdout=StringIO(),
        )
        delivery.refresh_from_db()
        self.assertEqual(delivery.status, EmailDelivery.Status.PENDING)


class MailingDashboardTests(MailingTestCase):
    def test_live_preview_allows_same_origin_blob_images(self) -> None:
        response = self.client.get(reverse("mailing:dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'sandbox="allow-same-origin"')

    def test_dashboard_queues_product_creators_and_renders_delivery(self) -> None:
        creator = self.create_creator()

        response = self.client.post(
            reverse("mailing:dashboard"),
            {
                "import_task": self.import_task.pk,
                "limit": 10,
            },
        )

        self.assertEqual(response.status_code, 200)
        delivery = EmailDelivery.objects.get()
        self.assertEqual(delivery.creator, creator)
        self.assertEqual(delivery.status, EmailDelivery.Status.PENDING)
        self.assertEqual(response.context["pending_count"], 1)
        self.assertContains(response, "邮件队列已更新")
        self.assertContains(response, creator.email.casefold())

    def test_running_email_service_can_be_stopped_from_dashboard(self) -> None:
        service = get_service_state()
        service.status = EmailSendingService.Status.RUNNING
        service.stop_requested = False
        service.save()

        response = self.client.post(
            reverse("mailing:stop_service"),
            HTTP_ACCEPT="application/json",
        )

        service.refresh_from_db()
        self.assertEqual(response.status_code, 202)
        self.assertEqual(
            service.status,
            EmailSendingService.Status.STOPPING,
        )
        self.assertTrue(service.stop_requested)

    def test_stopped_email_service_returns_conflict(self) -> None:
        response = self.client.post(
            reverse("mailing:stop_service"),
            HTTP_ACCEPT="application/json",
        )

        self.assertEqual(response.status_code, 409)


class EmailTemplateTests(MailingTestCase):
    def test_rejects_unsafe_link_scheme(self) -> None:
        with self.assertRaisesMessage(
            ValidationError,
            "HTTP/HTTPS",
        ):
            normalize_content_blocks(
                [
                    {
                        "type": "link",
                        "text": "Unsafe",
                        "url": "javascript:alert(1)",
                    }
                ]
            )
        with self.assertRaisesMessage(ValidationError, "HTTP/HTTPS"):
            sanitize_rich_html(
                '<p><a href="javascript:alert(1)">Unsafe</a></p>'
            )

    def test_dashboard_saves_version_with_uploaded_image(self) -> None:
        content_html = (
            "<h2>Hi {{ creator_name }}</h2>"
            '<p><a href="https://example.com/product">Product</a></p>'
            '<p><img data-upload-token="upload-1" '
            'alt="Product image"></p>'
        )
        with TemporaryDirectory() as directory, override_settings(
            MEDIA_ROOT=directory
        ):
            response = self.client.post(
                reverse("mailing:dashboard"),
                {
                    "action": "save_template",
                    "subject_template": "Hello {{ creator_name }}",
                    "content_html": content_html,
                    "asset_upload-1": SimpleUploadedFile(
                        "product.png",
                        b"\x89PNG\r\n\x1a\nemail-template-test",
                        content_type="image/png",
                    ),
                },
            )

            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.context["template_saved"])
            active = get_active_template_version()
            self.assertEqual(active.version, 2)
            self.assertEqual(active.subject_template, "Hello {{ creator_name }}")
            self.assertEqual(EmailTemplateAsset.objects.count(), 1)
            rich_block = active.content_blocks[0]
            self.assertEqual(rich_block["type"], "rich_text")
            self.assertIn("data-asset-id=", rich_block["html"])
            self.assertNotIn("data-upload-token", rich_block["html"])
            self.assertContains(response, "版本 v2 已启用")

    def test_rich_text_strips_scripts_and_unsupported_attributes(self) -> None:
        sanitized, _ = sanitize_rich_html(
            '<p onclick="alert(1)" style="color:red;text-align:center">'
            "Hello <strong>Creator</strong>"
            "<script>alert(1)</script></p>"
        )

        self.assertEqual(
            sanitized,
            '<p data-align="center">Hello <strong>Creator</strong></p>',
        )

    def test_queued_delivery_keeps_original_template_version(self) -> None:
        self.create_creator()
        version_two = save_template_version(
            subject_template="Version two",
            content_blocks=[
                {"type": "text", "style": "paragraph", "text": "Body two"}
            ],
            files={},
        )
        response = self.client.post(
            reverse("mailing:dashboard"),
            {
                "action": "queue",
                "import_task": self.import_task.pk,
                "limit": 1,
            },
        )
        self.assertEqual(response.status_code, 200)
        delivery = EmailDelivery.objects.get()
        self.assertEqual(delivery.template_version, version_two)

        version_three = save_template_version(
            subject_template="Version three",
            content_blocks=[
                {
                    "type": "text",
                    "style": "paragraph",
                    "text": "Body three",
                }
            ],
            files={},
        )
        delivery.refresh_from_db()
        self.assertNotEqual(version_two, version_three)
        self.assertEqual(delivery.template_version, version_two)

    def test_saved_preview_uses_personalization_and_escaped_text(self) -> None:
        save_template_version(
            subject_template="Preview {{ creator_name }}",
            content_blocks=[
                {
                    "type": "text",
                    "style": "paragraph",
                    "text": "Hi {{ creator_name }} <script>alert(1)</script>",
                },
                {
                    "type": "link",
                    "text": "Open",
                    "url": "https://example.com/path",
                },
            ],
            files={},
        )

        response = self.client.get(reverse("mailing:template_preview"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Hi Creator One")
        self.assertContains(
            response,
            "&lt;script&gt;alert(1)&lt;/script&gt;",
        )
        self.assertNotContains(response, "<script>alert(1)</script>")
        self.assertContains(response, 'href="https://example.com/path"')

    def test_custom_version_builds_matching_html_and_plain_text(self) -> None:
        version = save_template_version(
            subject_template="Custom {{ creator_name }}",
            content_blocks=[
                {
                    "type": "text",
                    "style": "paragraph",
                    "text": "Hello {{ creator_name }}",
                },
                {
                    "type": "link",
                    "text": "Catalog",
                    "url": "https://example.com/catalog",
                },
            ],
            files={},
        )
        delivery = self.create_delivery()
        delivery.template_version = version
        delivery.save(update_fields=["template_version", "updated_at"])

        with override_settings(
            EMAIL_HOST_USER="sender@example.com",
            EMAIL_HOST_PASSWORD="app-password",
        ):
            message = build_creator_email(delivery)

        self.assertEqual(message.subject, "Custom Creator One")
        self.assertIn("Hello Creator One", message.body)
        self.assertIn(
            "Catalog: https://example.com/catalog",
            message.body,
        )
        self.assertIn(
            'href="https://example.com/catalog"',
            message.alternatives[0].content,
        )
        self.assertEqual(message.attachments, [])

    def test_rich_version_builds_formatted_email_with_cid_image(self) -> None:
        with TemporaryDirectory() as directory, override_settings(
            MEDIA_ROOT=directory,
            EMAIL_HOST_USER="sender@example.com",
            EMAIL_HOST_PASSWORD="app-password",
        ):
            version = save_rich_template_version(
                subject_template="Rich {{ creator_name }}",
                content_html=(
                    "<h2>Welcome {{ creator_name }}</h2>"
                    "<p><strong>Important</strong> message</p>"
                    '<p><a href="https://example.com">Catalog</a></p>'
                    '<img data-upload-token="rich-image" alt="Catalog image">'
                ),
                files={
                    "asset_rich-image": SimpleUploadedFile(
                        "catalog.png",
                        b"\x89PNG\r\n\x1a\nrich-email-test",
                        content_type="image/png",
                    )
                },
            )
            delivery = self.create_delivery()
            delivery.template_version = version
            delivery.save(update_fields=["template_version", "updated_at"])

            message = build_creator_email(delivery)

        self.assertEqual(message.subject, "Rich Creator One")
        self.assertIn("Welcome Creator One", message.body)
        self.assertIn("Catalog: https://example.com", message.body)
        self.assertIn(
            "<strong>Important</strong>",
            message.alternatives[0].content,
        )
        self.assertIn(
            "cid:vaelos-products-",
            message.alternatives[0].content,
        )
        self.assertEqual(len(message.attachments), 1)


class EmailMessageTests(MailingTestCase):
    def test_builds_personalized_html_text_and_cid_image(self) -> None:
        delivery = self.create_delivery()
        with TemporaryDirectory() as directory:
            image_path = Path(directory) / "FR7A6183.png"
            image_path.write_bytes(b"\x89PNG\r\n\x1a\nemail-test")
            with override_settings(
                EMAIL_HOST_USER="sender@example.com",
                EMAIL_HOST_PASSWORD="app-password",
                DEFAULT_FROM_EMAIL=(
                    "Jackson | Vaelos <sender@example.com>"
                ),
                CREATOR_EMAIL_IMAGE_PATH=image_path,
                SPORTS_JACKET_URL="",
                WOMENS_SHORTS_URL="",
            ):
                message = build_creator_email(delivery)

        self.assertIn("Creator One", message.subject)
        self.assertEqual(message.to, ["creator@example.com"])
        self.assertIn("Hi Creator One", message.body)
        self.assertEqual(len(message.alternatives), 1)
        self.assertIn(
            "cid:vaelos-products-",
            message.alternatives[0].content,
        )
        self.assertNotIn('href=""', message.alternatives[0].content)
        self.assertEqual(message.mixed_subtype, "related")
        self.assertEqual(len(message.attachments), 1)
        self.assertTrue(
            str(message.attachments[0]["Content-ID"]).startswith(
                "<vaelos-products-"
            )
        )


class SendCreatorEmailsCommandTests(MailingTestCase):
    def _email_settings(self, image_path: Path):
        return override_settings(
            EMAIL_BACKEND=(
                "django.core.mail.backends.locmem.EmailBackend"
            ),
            EMAIL_HOST_USER="sender@example.com",
            EMAIL_HOST_PASSWORD="app-password",
            DEFAULT_FROM_EMAIL="Jackson | Vaelos <sender@example.com>",
            CREATOR_EMAIL_IMAGE_PATH=image_path,
            CREATOR_EMAIL_DAILY_LIMIT=100,
            CREATOR_EMAIL_SEND_INTERVAL_SECONDS=0,
            SPORTS_JACKET_URL="",
            WOMENS_SHORTS_URL="",
        )

    def test_dry_run_does_not_send_or_claim(self) -> None:
        delivery = self.create_delivery()

        call_command(
            "send_creator_emails",
            limit=100,
            interval=0,
            dry_run=True,
            stdout=StringIO(),
        )

        delivery.refresh_from_db()
        self.assertEqual(delivery.status, EmailDelivery.Status.PENDING)
        self.assertEqual(delivery.attempt_count, 0)

    def test_sends_pending_delivery_and_marks_success(self) -> None:
        delivery = self.create_delivery()
        with TemporaryDirectory() as directory:
            image_path = Path(directory) / "FR7A6183.png"
            image_path.write_bytes(b"\x89PNG\r\n\x1a\nemail-test")
            with self._email_settings(image_path):
                call_command(
                    "send_creator_emails",
                    limit=100,
                    interval=0,
                    stdout=StringIO(),
                )

        delivery.refresh_from_db()
        self.assertEqual(delivery.status, EmailDelivery.Status.SENT)
        self.assertEqual(delivery.attempt_count, 1)
        self.assertIsNotNone(delivery.sent_at)
        self.assertTrue(delivery.message_id)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["creator@example.com"])

    def test_rolling_daily_limit_leaves_queue_unchanged(self) -> None:
        first = self.create_delivery(status=EmailDelivery.Status.SENT)
        first.sent_at = timezone.now()
        first.last_attempt_at = timezone.now()
        first.save(
            update_fields=[
                "sent_at",
                "last_attempt_at",
                "updated_at",
            ]
        )
        second_creator = self.create_creator(
            handle="CreatorTwo",
            email="two@example.com",
        )
        second = self.create_delivery(creator=second_creator)

        with override_settings(CREATOR_EMAIL_DAILY_LIMIT=1):
            call_command(
                "send_creator_emails",
                limit=100,
                interval=0,
                stdout=StringIO(),
            )

        second.refresh_from_db()
        self.assertEqual(second.status, EmailDelivery.Status.PENDING)

    def test_stop_request_prevents_claiming_the_next_email(self) -> None:
        first = self.create_delivery()
        second_creator = self.create_creator(
            handle="CreatorTwo",
            email="two@example.com",
        )
        second = self.create_delivery(creator=second_creator)

        def send_and_request_stop(delivery, *, connection=None):
            service = get_service_state()
            service.stop_requested = True
            service.status = EmailSendingService.Status.STOPPING
            service.save()
            return delivery.message_id

        with TemporaryDirectory() as directory:
            image_path = Path(directory) / "FR7A6183.png"
            image_path.write_bytes(b"\x89PNG\r\n\x1a\nemail-test")
            with self._email_settings(image_path), patch(
                "mailing.management.commands.send_creator_emails."
                "send_creator_email",
                side_effect=send_and_request_stop,
            ):
                call_command(
                    "send_creator_emails",
                    limit=100,
                    interval=0,
                    stdout=StringIO(),
                )

        first.refresh_from_db()
        second.refresh_from_db()
        service = get_service_state()
        self.assertEqual(first.status, EmailDelivery.Status.SENT)
        self.assertEqual(second.status, EmailDelivery.Status.PENDING)
        self.assertEqual(
            service.status,
            EmailSendingService.Status.STOPPED,
        )
