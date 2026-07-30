"""Build and send one personalized multipart creator email."""

from __future__ import annotations

from email.mime.image import MIMEImage
from email.utils import make_msgid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.mail import EmailMultiAlternatives
from django.core.mail.backends.base import BaseEmailBackend

from mailing.models import EmailDelivery
from mailing.services.template_content import (
    get_active_template_version,
    render_template_content,
)


class CreatorEmailError(Exception):
    """Base error for creator collaboration email delivery."""


class EmailConfigurationError(CreatorEmailError):
    """The local SMTP or email asset configuration is incomplete."""


def validate_email_configuration() -> None:
    if not settings.EMAIL_HOST_USER:
        raise EmailConfigurationError("缺少 GMAIL_ADDRESS。")
    if not settings.EMAIL_HOST_PASSWORD:
        raise EmailConfigurationError("缺少 GMAIL_APP_PASSWORD。")


def build_creator_email(
    delivery: EmailDelivery,
    *,
    connection: BaseEmailBackend | None = None,
) -> EmailMultiAlternatives:
    validate_email_configuration()
    version = delivery.template_version or get_active_template_version()
    try:
        rendered = render_template_content(
            version,
            creator_name=delivery.creator_name_snapshot or "Creator",
        )
    except (ValidationError, OSError) as error:
        raise EmailConfigurationError(str(error)) from error
    message_id = delivery.message_id or make_msgid(
        domain="vaelos.email"
    )
    message = EmailMultiAlternatives(
        subject=rendered.subject,
        body=rendered.text_body,
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=[delivery.recipient_email],
        reply_to=[settings.EMAIL_HOST_USER],
        headers={"Message-ID": message_id},
        connection=connection,
    )
    message.attach_alternative(rendered.html_body, "text/html")

    for rendered_image in rendered.images:
        subtype = rendered_image.content_type.split("/", 1)[-1]
        inline_image = MIMEImage(
            rendered_image.read_bytes(),
            _subtype=subtype,
        )
        inline_image.add_header(
            "Content-ID",
            f"<{rendered_image.cid}>",
        )
        inline_image.add_header(
            "Content-Disposition",
            "inline",
            filename=rendered_image.filename,
        )
        message.attach(inline_image)
    if rendered.images:
        message.mixed_subtype = "related"
    return message


def send_creator_email(
    delivery: EmailDelivery,
    *,
    connection: BaseEmailBackend | None = None,
) -> str:
    message = build_creator_email(delivery, connection=connection)
    sent_count = message.send(fail_silently=False)
    if sent_count != 1:
        raise CreatorEmailError(
            f"SMTP 返回的发送数量异常：{sent_count}"
        )
    return str(message.extra_headers["Message-ID"])
