import hashlib

from django.db import migrations


GREETING_NAME = "VAELOS 官方合作邀约"
GREETING_CONTENT = """Hi We’re obsessed with your content 🥰
Your aesthetic goes so well with VAELOS activewear ✨
We’re a reliable brand selling soft, stretchy yoga & gym wear 🩳🧘‍♀️
We sent you an official collab invite: free samples + high commission 🎁💰
Accept it in your TikTok dashboard to get your products ASAP 🚀
Let’s partner long term and make great content together! 🤩"""


def seed_default_greeting(apps, schema_editor):
    GreetingTemplate = apps.get_model(
        "creator_contact",
        "GreetingTemplate",
    )
    GreetingTemplate.objects.filter(is_default=True).update(is_default=False)
    GreetingTemplate.objects.update_or_create(
        name=GREETING_NAME,
        defaults={
            "content": GREETING_CONTENT,
            "content_sha256": hashlib.sha256(
                GREETING_CONTENT.encode("utf-8")
            ).hexdigest(),
            "is_default": True,
            "is_active": True,
        },
    )


class Migration(migrations.Migration):
    dependencies = [
        ("creator_contact", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(
            seed_default_greeting,
            migrations.RunPython.noop,
        ),
    ]
