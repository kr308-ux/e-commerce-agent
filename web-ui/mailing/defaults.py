"""Default editable content matching the original collaboration email."""

from __future__ import annotations

from urllib.parse import urlparse

from django.conf import settings


DEFAULT_EMAIL_SUBJECT = (
    "{{ creator_name }}, Vaelos Would Love to Collaborate With You 💕"
)


def default_email_blocks() -> list[dict[str, object]]:
    blocks: list[dict[str, object]] = [
        {
            "type": "text",
            "style": "paragraph",
            "text": "Hi {{ creator_name }}💕✨",
        },
        {
            "type": "text",
            "style": "paragraph",
            "text": "Hope you are doing well and having an amazing day☀️!",
        },
        {
            "type": "text",
            "style": "paragraph",
            "text": (
                "I’m Jackson from Vaelos🧘‍♀️, a trendy activewear & yoga "
                "brand thriving on TikTok POP Shop🛍️."
            ),
        },
        {
            "type": "text",
            "style": "paragraph",
            "text": (
                "We are absolutely obsessed with your unique content style😍 "
                "and would love to sincerely invite you to join us for a fun "
                "product collaboration🤝!"
            ),
        },
        {
            "type": "text",
            "style": "paragraph",
            "text": (
                "✅ 100% FREE product sample sent straight to you🎁, no "
                "hidden fees at all\n"
                "✅ Lucrative commission payout for every single order💰"
            ),
        },
        {
            "type": "text",
            "style": "paragraph",
            "text": (
                "Below is our detailed product image highlighting the key "
                "selling points📝."
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
                "If this collaboration opportunity interests you🥰, simply "
                "reply to this email and we’ll arrange and ship your "
                "complimentary sample out to you right away🚚💨!"
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
    ]
    for label, url in (
        ("Sports jacket link 👈🏻Click", settings.SPORTS_JACKET_URL),
        ("Women's shorts link 👈🏻Click", settings.WOMENS_SHORTS_URL),
    ):
        parsed = urlparse(url)
        if parsed.scheme in {"http", "https"} and parsed.netloc:
            blocks.append({"type": "link", "text": label, "url": url})
        else:
            blocks.append(
                {"type": "text", "style": "paragraph", "text": label}
            )
    blocks.extend(
        [
            {
                "type": "text",
                "style": "heading",
                "text": (
                    "The selling points of the athletic yoga jacket are shown "
                    "in the image."
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
    )
    return blocks
