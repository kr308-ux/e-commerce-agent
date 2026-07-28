from __future__ import annotations

import hashlib

from creator_contact.models import GreetingTemplate

from .base import CreatorContactTestCase


class CreatorContactModelTests(CreatorContactTestCase):
    def test_greeting_and_task_snapshot_share_canonical_line_endings(
        self,
    ) -> None:
        raw = "We’re ready\r\nUnicode 🧘‍♀️\rFinal line"
        canonical = "We’re ready\nUnicode 🧘‍♀️\nFinal line"
        expected_sha256 = hashlib.sha256(
            canonical.encode("utf-8")
        ).hexdigest()
        greeting = GreetingTemplate.objects.create(
            name="CRLF greeting",
            content=raw,
        )
        task = self.create_contact_task(top_n=1)
        task.greeting_snapshot = raw
        task.save()

        self.assertEqual(greeting.content, canonical)
        self.assertEqual(greeting.content_sha256, expected_sha256)
        self.assertEqual(task.greeting_snapshot, canonical)
        self.assertEqual(task.greeting_sha256, expected_sha256)
