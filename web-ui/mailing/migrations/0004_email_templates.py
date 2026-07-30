import django.db.models.deletion
import mailing.models
from urllib.parse import urlparse

from django.conf import settings
from django.db import migrations, models


DEFAULT_SUBJECT = (
    "{{ creator_name }}, Vaelos Would Love to Collaborate With You 💕"
)

DEFAULT_BLOCKS = [
    {"type": "text", "style": "paragraph", "text": "Hi {{ creator_name }}💕✨"},
    {
        "type": "text",
        "style": "paragraph",
        "text": "Hope you are doing well and having an amazing day☀️!",
    },
    {
        "type": "text",
        "style": "paragraph",
        "text": (
            "I’m Jackson from Vaelos🧘‍♀️, a trendy activewear & yoga brand "
            "thriving on TikTok POP Shop🛍️."
        ),
    },
    {
        "type": "text",
        "style": "paragraph",
        "text": (
            "We are absolutely obsessed with your unique content style😍 and "
            "would love to sincerely invite you to join us for a fun product "
            "collaboration🤝!"
        ),
    },
    {
        "type": "text",
        "style": "paragraph",
        "text": (
            "✅ 100% FREE product sample sent straight to you🎁, no hidden "
            "fees at all\n✅ Lucrative commission payout for every single "
            "order💰"
        ),
    },
    {
        "type": "text",
        "style": "paragraph",
        "text": (
            "Below is our detailed product image highlighting the key selling "
            "points📝."
        ),
    },
    {
        "type": "text",
        "style": "paragraph",
        "text": (
            "You’re totally free to create videos in your own signature "
            "creative style🎬 — no rigid mandatory script at all!"
        ),
    },
    {
        "type": "text",
        "style": "heading",
        "text": "Quick & easy video guideline📋",
    },
    {
        "type": "text",
        "style": "paragraph",
        "text": (
            "⏱️ Duration: 15–30 seconds\n"
            "💡 Content: Share your genuine real-life wearing experience\n"
            "🔗 Final step: Link our product in your post"
        ),
    },
    {
        "type": "text",
        "style": "paragraph",
        "text": (
            "If this collaboration opportunity interests you🥰, simply reply "
            "to this email and we’ll arrange and ship your complimentary "
            "sample out to you right away🚚💨!"
        ),
    },
    {
        "type": "text",
        "style": "paragraph",
        "text": "Best regards,\nJackson\nVaelos Brand Founder👔",
    },
    {
        "type": "text",
        "style": "heading",
        "text": "TikTok Shop Link:",
    },
    {
        "type": "text",
        "style": "paragraph",
        "text": "Sports jacket link",
    },
    {
        "type": "text",
        "style": "paragraph",
        "text": "Women's shorts link",
    },
    {
        "type": "text",
        "style": "heading",
        "text": (
            "The selling points of the athletic yoga jacket are shown in the "
            "image."
        ),
    },
    {
        "type": "image",
        "legacy_asset": True,
        "alt": "Vaelos athletic yoga jacket and women's shorts",
    },
    {
        "type": "text",
        "style": "heading",
        "text": "Key Selling Points for Women's Shorts:",
    },
    {
        "type": "text",
        "style": "paragraph",
        "text": (
            "• Seamless Comfort Fit\n"
            "• High Waist Tummy Control\n"
            "• Scrunch Butt Lifting Design\n"
            "• 16 Vibrant Color Options"
        ),
    },
]


def seed_default_template(apps, schema_editor):
    EmailTemplate = apps.get_model("mailing", "EmailTemplate")
    EmailTemplateVersion = apps.get_model(
        "mailing",
        "EmailTemplateVersion",
    )
    EmailDelivery = apps.get_model("mailing", "EmailDelivery")
    template, _ = EmailTemplate.objects.get_or_create(
        id=1,
        defaults={"name": "达人合作邮件"},
    )
    blocks = [dict(block) for block in DEFAULT_BLOCKS]
    link_positions = (
        (12, "Sports jacket link 👈🏻Click", settings.SPORTS_JACKET_URL),
        (13, "Women's shorts link 👈🏻Click", settings.WOMENS_SHORTS_URL),
    )
    for index, label, url in link_positions:
        parsed = urlparse(url)
        if parsed.scheme in {"http", "https"} and parsed.netloc:
            blocks[index] = {"type": "link", "text": label, "url": url}
    version, _ = EmailTemplateVersion.objects.get_or_create(
        template=template,
        version=1,
        defaults={
            "subject_template": DEFAULT_SUBJECT,
            "content_blocks": blocks,
        },
    )
    template.active_version_id = version.pk
    template.save(update_fields=["active_version", "updated_at"])
    EmailDelivery.objects.filter(template_version__isnull=True).update(
        template_version=version
    )


class Migration(migrations.Migration):
    dependencies = [
        ("mailing", "0003_creator_reference"),
    ]

    operations = [
        migrations.CreateModel(
            name="EmailTemplate",
            fields=[
                (
                    "id",
                    models.PositiveSmallIntegerField(
                        default=1,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                (
                    "name",
                    models.CharField(
                        default="达人合作邮件",
                        max_length=120,
                    ),
                ),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
        ),
        migrations.CreateModel(
            name="EmailTemplateAsset",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "file",
                    models.FileField(
                        upload_to=mailing.models.email_template_asset_upload_to
                    ),
                ),
                ("original_name", models.CharField(max_length=255)),
                ("content_type", models.CharField(max_length=80)),
                ("byte_size", models.PositiveIntegerField()),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={"ordering": ["created_at", "id"]},
        ),
        migrations.CreateModel(
            name="EmailTemplateVersion",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("version", models.PositiveIntegerField()),
                ("subject_template", models.CharField(max_length=255)),
                ("content_blocks", models.JSONField(default=list)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "assets",
                    models.ManyToManyField(
                        blank=True,
                        related_name="template_versions",
                        to="mailing.emailtemplateasset",
                    ),
                ),
                (
                    "template",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="versions",
                        to="mailing.emailtemplate",
                    ),
                ),
            ],
            options={
                "ordering": ["-version"],
                "constraints": [
                    models.UniqueConstraint(
                        fields=("template", "version"),
                        name="unique_email_template_version",
                    )
                ],
            },
        ),
        migrations.AddField(
            model_name="emailtemplate",
            name="active_version",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="+",
                to="mailing.emailtemplateversion",
            ),
        ),
        migrations.AddField(
            model_name="emaildelivery",
            name="template_version",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="deliveries",
                to="mailing.emailtemplateversion",
            ),
        ),
        migrations.RunPython(
            seed_default_template,
            migrations.RunPython.noop,
        ),
    ]
