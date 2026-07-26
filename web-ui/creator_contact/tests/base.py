from decimal import Decimal

from django.test import TestCase

from tasks.models import CreatorAcquisitionTask, Product, RelatedCreator

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
        self.acquisition_task = CreatorAcquisitionTask.objects.create(
            product_limit=10,
            status=CreatorAcquisitionTask.Status.SUCCESS,
        )
        self.product = Product.objects.create(
            task=self.acquisition_task,
            external_product_id="1731795774080652206",
            name="Third Product",
            product_url=(
                "https://www.chuhaijiang.com/app/discover/tiktok/products/"
                "1731795774080652206?country=US"
            ),
        )
        self.high = RelatedCreator.objects.create(
            product=self.product,
            creator_handle="Highest",
            nickname="Highest Creator",
            recent_30_day_revenue=Decimal("500.00"),
            recent_7_day_revenue=Decimal("10.00"),
        )
        self.middle = RelatedCreator.objects.create(
            product=self.product,
            creator_handle="@Middle",
            nickname="Middle Creator",
            recent_30_day_revenue=Decimal("300.00"),
            recent_7_day_revenue=Decimal("80.00"),
        )
        self.low = RelatedCreator.objects.create(
            product=self.product,
            creator_handle="low",
            nickname="Low Creator",
            recent_30_day_revenue=Decimal("100.00"),
            recent_7_day_revenue=Decimal("90.00"),
        )
        self.null_revenue = RelatedCreator.objects.create(
            product=self.product,
            creator_handle="null_revenue",
            nickname="Null Revenue",
            recent_30_day_revenue=None,
            recent_7_day_revenue=Decimal("1000.00"),
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

    def create_contact_task(
        self,
        *,
        top_n: int = 2,
    ) -> CreatorContactTask:
        return CreatorContactTask.objects.create(
            store_id=self.store_id,
            source_product=self.product,
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
