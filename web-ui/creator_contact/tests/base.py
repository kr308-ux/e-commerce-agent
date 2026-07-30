from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from tasks.models import (
    Creator,
    CreatorSalesMetric,
    ImportTask,
    ImportTaskCreator,
)

from creator_contact.models import (
    CreatorContactTask,
    DirectedCollaborationOption,
    GreetingTemplate,
)


class CreatorContactTestCase(TestCase):
    store_id = "store-test-1"

    def setUp(self) -> None:
        GreetingTemplate.objects.filter(is_default=True).update(
            is_default=False
        )
        now = timezone.now()
        self.import_task = ImportTask.objects.create(
            file_name="creator-batch.xlsx",
            file_sha256="a" * 64,
            sheet_name="Creators",
            status=ImportTask.Status.SUCCESS,
            snapshot_date=timezone.localdate(),
            confirmed_at=now,
            finished_at=now,
        )
        self.high = self.create_creator(
            "Highest",
            "Highest Creator",
            revenue_30=Decimal("500.00"),
            revenue_7=Decimal("10.00"),
            row=2,
        )
        self.middle = self.create_creator(
            "@Middle",
            "Middle Creator",
            revenue_30=Decimal("300.00"),
            revenue_7=Decimal("80.00"),
            row=3,
        )
        self.low = self.create_creator(
            "low",
            "Low Creator",
            revenue_30=Decimal("100.00"),
            revenue_7=Decimal("90.00"),
            row=4,
        )
        self.null_revenue = self.create_creator(
            "null_revenue",
            "Null Revenue",
            revenue_30=None,
            revenue_7=Decimal("1000.00"),
            row=5,
        )
        self.greeting = GreetingTemplate.objects.create(
            name="Default Greeting",
            content="Hello creator",
            is_default=True,
        )
        self.invitation = DirectedCollaborationOption.objects.create(
            store_id=self.store_id,
            external_invitation_id="7664550207413847821",
            name="金色拉链+短裤13",
            status=DirectedCollaborationOption.Status.ONGOING,
        )

    def create_creator(
        self,
        creator_id: str,
        nickname: str,
        *,
        revenue_30: Decimal | None,
        revenue_7: Decimal | None,
        row: int,
        revenue_total: Decimal | None = None,
        import_task: ImportTask | None = None,
    ) -> Creator:
        batch = import_task or self.import_task
        creator, _ = Creator.objects.get_or_create(
            creator_id=creator_id,
            defaults={"nickname": nickname},
        )
        ImportTaskCreator.objects.get_or_create(
            import_task=batch,
            creator=creator,
            defaults={"first_row_number": row},
        )
        for window, amount in (
            (30, revenue_30),
            (7, revenue_7),
            (0, revenue_total),
        ):
            if amount is not None:
                CreatorSalesMetric.objects.create(
                    creator=creator,
                    import_task=batch,
                    window_days=window,
                    sales_amount=amount,
                    snapshot_date=batch.snapshot_date,
                    source_row_number=row,
                )
        return creator

    def create_contact_task(
        self,
        *,
        top_n: int = 2,
        import_task: ImportTask | None = None,
    ) -> CreatorContactTask:
        return CreatorContactTask.objects.create(
            store_id=self.store_id,
            source_import_task=import_task or self.import_task,
            top_n=top_n,
            greeting_template=self.greeting,
            greeting_snapshot=self.greeting.content,
            collaboration_option=self.invitation,
            invitation_name_snapshot=self.invitation.name,
            invitation_id_snapshot=self.invitation.external_invitation_id,
            confirm_send_greeting=True,
            confirm_send_invitation=True,
            confirm_send_card=True,
        )
