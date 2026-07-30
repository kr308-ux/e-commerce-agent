"""Validate, version, and render user-editable email content."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse
from uuid import uuid4

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Max
from django.template.loader import render_to_string
from django.urls import reverse

from mailing.defaults import DEFAULT_EMAIL_SUBJECT, default_email_blocks
from mailing.models import (
    EmailTemplate,
    EmailTemplateAsset,
    EmailTemplateVersion,
)
from mailing.services.rich_text import (
    blocks_to_rich_html,
    hydrate_rich_html_images,
    render_rich_html,
    rich_html_to_plain_text,
    sanitize_rich_html,
)

MAX_CONTENT_BLOCKS = 100
MAX_IMAGE_BLOCKS = 10
MAX_TEXT_LENGTH = 20_000
MAX_LINK_LENGTH = 2_000
MAX_ALT_LENGTH = 500
ALLOWED_TEXT_STYLES = {"paragraph", "heading"}
ALLOWED_IMAGE_SIGNATURES = {
    "png": ("image/png", b"\x89PNG\r\n\x1a\n"),
    "jpeg": ("image/jpeg", b"\xff\xd8\xff"),
}


@dataclass(frozen=True)
class InlineImage:
    cid: str
    filename: str
    content_type: str
    read_bytes: Callable[[], bytes]


@dataclass(frozen=True)
class RenderedEmailContent:
    subject: str
    text_body: str
    html_body: str
    images: list[InlineImage]


def _validation_error(message: str) -> ValidationError:
    return ValidationError(message, code="invalid_email_template")


def personalize(value: object, creator_name: str) -> str:
    return str(value or "").replace(
        "{{ creator_name }}",
        creator_name or "Creator",
    )


def validate_subject(value: object) -> str:
    subject = str(value or "").strip()
    if not subject:
        raise _validation_error("邮件主题不能为空。")
    if len(subject) > 255:
        raise _validation_error("邮件主题不能超过 255 个字符。")
    return subject


def validate_http_url(value: object) -> str:
    url = str(value or "").strip()
    if not url or len(url) > MAX_LINK_LENGTH:
        raise _validation_error("超链接地址不能为空且不能超过 2000 个字符。")
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise _validation_error("超链接必须是完整的 HTTP/HTTPS 地址。")
    return url


def normalize_content_blocks(
    value: object,
    *,
    allow_upload_tokens: bool = False,
) -> list[dict[str, object]]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError as error:
            raise _validation_error("邮件正文数据格式无效。") from error
    if not isinstance(value, list):
        raise _validation_error("邮件正文必须是内容块列表。")
    if not value:
        raise _validation_error("邮件正文至少需要一个内容块。")
    if len(value) > MAX_CONTENT_BLOCKS:
        raise _validation_error(
            f"邮件正文最多支持 {MAX_CONTENT_BLOCKS} 个内容块。"
        )

    normalized: list[dict[str, object]] = []
    image_count = 0
    for index, raw_block in enumerate(value, start=1):
        if not isinstance(raw_block, dict):
            raise _validation_error(f"第 {index} 个内容块格式无效。")
        block_type = str(raw_block.get("type") or "").strip()
        if block_type == "text":
            text = str(raw_block.get("text") or "").strip()
            style = str(raw_block.get("style") or "paragraph").strip()
            if not text:
                raise _validation_error(f"第 {index} 个文字块不能为空。")
            if len(text) > MAX_TEXT_LENGTH:
                raise _validation_error(
                    f"第 {index} 个文字块不能超过 {MAX_TEXT_LENGTH} 个字符。"
                )
            if style not in ALLOWED_TEXT_STYLES:
                raise _validation_error(f"第 {index} 个文字块样式无效。")
            normalized.append(
                {"type": "text", "style": style, "text": text}
            )
            continue
        if block_type == "link":
            text = str(raw_block.get("text") or "").strip()
            if not text:
                raise _validation_error(f"第 {index} 个链接文字不能为空。")
            if len(text) > MAX_TEXT_LENGTH:
                raise _validation_error(f"第 {index} 个链接文字过长。")
            normalized.append(
                {
                    "type": "link",
                    "text": text,
                    "url": validate_http_url(raw_block.get("url")),
                }
            )
            continue
        if block_type == "image":
            image_count += 1
            if image_count > MAX_IMAGE_BLOCKS:
                raise _validation_error(
                    f"邮件正文最多支持 {MAX_IMAGE_BLOCKS} 张图片。"
                )
            alt = str(raw_block.get("alt") or "").strip()
            if len(alt) > MAX_ALT_LENGTH:
                raise _validation_error(
                    f"第 {index} 张图片的替代文字不能超过 "
                    f"{MAX_ALT_LENGTH} 个字符。"
                )
            block: dict[str, object] = {
                "type": "image",
                "alt": alt or "邮件图片",
            }
            if raw_block.get("legacy_asset") is True:
                block["legacy_asset"] = True
            elif raw_block.get("asset_id") not in (None, ""):
                try:
                    asset_id = int(raw_block["asset_id"])
                except (TypeError, ValueError) as error:
                    raise _validation_error(
                        f"第 {index} 张图片引用无效。"
                    ) from error
                if asset_id < 1:
                    raise _validation_error(f"第 {index} 张图片引用无效。")
                block["asset_id"] = asset_id
            elif allow_upload_tokens and raw_block.get("upload_token"):
                token = str(raw_block["upload_token"])
                if (
                    len(token) > 80
                    or not token.replace("-", "").replace("_", "").isalnum()
                ):
                    raise _validation_error(
                        f"第 {index} 张图片上传标识无效。"
                    )
                block["upload_token"] = token
            else:
                raise _validation_error(f"第 {index} 张图片尚未选择文件。")
            normalized.append(block)
            continue
        raise _validation_error(f"第 {index} 个内容块类型无效。")
    return normalized


def validate_image_upload(upload) -> tuple[str, str]:
    max_bytes = settings.EMAIL_TEMPLATE_IMAGE_MAX_BYTES
    if upload.size < 1:
        raise _validation_error("上传的图片不能为空。")
    if upload.size > max_bytes:
        max_mb = max_bytes // (1024 * 1024)
        raise _validation_error(f"单张图片不能超过 {max_mb} MB。")
    suffix = Path(upload.name).suffix.lower().lstrip(".")
    if suffix == "jpg":
        suffix = "jpeg"
    expected = ALLOWED_IMAGE_SIGNATURES.get(suffix)
    if expected is None:
        raise _validation_error("图片仅支持 PNG、JPG 或 JPEG 格式。")
    content_type, signature = expected
    header = upload.read(max(len(signature), 12))
    upload.seek(0)
    if not header.startswith(signature):
        raise _validation_error(f"图片 {upload.name} 的文件内容无效。")
    supplied_type = str(getattr(upload, "content_type", "") or "")
    if supplied_type and supplied_type != content_type:
        raise _validation_error(f"图片 {upload.name} 的文件类型不匹配。")
    return content_type, suffix


def get_active_template_version() -> EmailTemplateVersion:
    template = (
        EmailTemplate.objects.select_related("active_version")
        .filter(pk=1)
        .first()
    )
    if template and template.active_version:
        return template.active_version

    with transaction.atomic():
        template, _ = EmailTemplate.objects.select_for_update().get_or_create(
            pk=1,
            defaults={"name": "达人合作邮件"},
        )
        if template.active_version_id:
            return EmailTemplateVersion.objects.get(
                pk=template.active_version_id
            )
        version = EmailTemplateVersion.objects.create(
            template=template,
            version=1,
            subject_template=DEFAULT_EMAIL_SUBJECT,
            content_blocks=default_email_blocks(),
        )
        template.active_version = version
        template.save(update_fields=["active_version", "updated_at"])
        return version


def save_template_version(
    *,
    subject_template: object,
    content_blocks: object,
    files,
) -> EmailTemplateVersion:
    subject = validate_subject(subject_template)
    blocks = normalize_content_blocks(
        content_blocks,
        allow_upload_tokens=True,
    )
    uploads: dict[str, tuple[object, str]] = {}
    total_upload_bytes = 0
    for block in blocks:
        token = block.get("upload_token")
        if not token:
            continue
        upload = files.get(f"asset_{token}")
        if upload is None:
            raise _validation_error("有图片尚未完成上传，请重新选择。")
        content_type, _ = validate_image_upload(upload)
        total_upload_bytes += upload.size
        uploads[str(token)] = (upload, content_type)
    if total_upload_bytes > settings.EMAIL_TEMPLATE_TOTAL_IMAGE_MAX_BYTES:
        max_mb = settings.EMAIL_TEMPLATE_TOTAL_IMAGE_MAX_BYTES // (
            1024 * 1024
        )
        raise _validation_error(f"本次上传的图片总计不能超过 {max_mb} MB。")

    existing_asset_ids = {
        int(block["asset_id"])
        for block in blocks
        if block.get("asset_id")
    }
    existing_assets = {
        asset.pk: asset
        for asset in EmailTemplateAsset.objects.filter(
            pk__in=existing_asset_ids
        )
    }
    if set(existing_assets) != existing_asset_ids:
        raise _validation_error("正文引用的图片不存在，请重新选择。")

    with transaction.atomic():
        template, _ = EmailTemplate.objects.select_for_update().get_or_create(
            pk=1,
            defaults={"name": "达人合作邮件"},
        )
        referenced_assets = list(existing_assets.values())
        persisted_blocks: list[dict[str, object]] = []
        for block in blocks:
            persisted = dict(block)
            token = persisted.pop("upload_token", None)
            if token:
                upload, content_type = uploads[str(token)]
                asset = EmailTemplateAsset.objects.create(
                    file=upload,
                    original_name=Path(upload.name).name[:255],
                    content_type=content_type,
                    byte_size=upload.size,
                )
                persisted["asset_id"] = asset.pk
                referenced_assets.append(asset)
            persisted_blocks.append(persisted)
        latest = (
            EmailTemplateVersion.objects.filter(template=template).aggregate(
                value=Max("version")
            )["value"]
            or 0
        )
        version = EmailTemplateVersion.objects.create(
            template=template,
            version=latest + 1,
            subject_template=subject,
            content_blocks=persisted_blocks,
        )
        version.assets.set(referenced_assets)
        template.active_version = version
        template.save(update_fields=["active_version", "updated_at"])
        return version


def save_rich_template_version(
    *,
    subject_template: object,
    content_html: object,
    files,
) -> EmailTemplateVersion:
    subject = validate_subject(subject_template)
    sanitized_html, image_references = sanitize_rich_html(
        content_html,
        allow_upload_tokens=True,
    )
    uploads: dict[str, tuple[object, str]] = {}
    total_upload_bytes = 0
    for reference in image_references:
        token = reference.get("upload_token")
        if not token:
            continue
        upload = files.get(f"asset_{token}")
        if upload is None:
            raise _validation_error("有图片尚未完成上传，请重新选择。")
        content_type, _ = validate_image_upload(upload)
        total_upload_bytes += upload.size
        uploads[str(token)] = (upload, content_type)
    if total_upload_bytes > settings.EMAIL_TEMPLATE_TOTAL_IMAGE_MAX_BYTES:
        max_mb = settings.EMAIL_TEMPLATE_TOTAL_IMAGE_MAX_BYTES // (
            1024 * 1024
        )
        raise _validation_error(f"本次上传的图片总计不能超过 {max_mb} MB。")

    existing_asset_ids = {
        int(reference["asset_id"])
        for reference in image_references
        if reference.get("asset_id")
    }
    existing_assets = {
        asset.pk: asset
        for asset in EmailTemplateAsset.objects.filter(
            pk__in=existing_asset_ids
        )
    }
    if set(existing_assets) != existing_asset_ids:
        raise _validation_error("正文引用的图片不存在，请重新选择。")

    with transaction.atomic():
        template, _ = EmailTemplate.objects.select_for_update().get_or_create(
            pk=1,
            defaults={"name": "达人合作邮件"},
        )
        referenced_assets = list(existing_assets.values())
        persisted_html = sanitized_html
        for token, (upload, content_type) in uploads.items():
            asset = EmailTemplateAsset.objects.create(
                file=upload,
                original_name=Path(upload.name).name[:255],
                content_type=content_type,
                byte_size=upload.size,
            )
            referenced_assets.append(asset)
            persisted_html = persisted_html.replace(
                f'data-upload-token="{token}"',
                f'data-asset-id="{asset.pk}"',
            )
        persisted_html, _ = sanitize_rich_html(persisted_html)
        latest = (
            EmailTemplateVersion.objects.filter(template=template).aggregate(
                value=Max("version")
            )["value"]
            or 0
        )
        version = EmailTemplateVersion.objects.create(
            template=template,
            version=latest + 1,
            subject_template=subject,
            content_blocks=[
                {"type": "rich_text", "html": persisted_html}
            ],
        )
        version.assets.set(referenced_assets)
        template.active_version = version
        template.save(update_fields=["active_version", "updated_at"])
        return version


def _legacy_image() -> tuple[str, str, Callable[[], bytes]]:
    path = Path(settings.CREATOR_EMAIL_IMAGE_PATH)
    if not path.is_file():
        raise _validation_error(f"默认邮件产品图不存在：{path}")
    suffix = path.suffix.lower()
    if suffix == ".png":
        content_type = "image/png"
    elif suffix in {".jpg", ".jpeg"}:
        content_type = "image/jpeg"
    else:
        raise _validation_error("默认邮件产品图仅支持 PNG、JPG 或 JPEG。")
    return path.name, content_type, path.read_bytes


def render_template_content(
    version: EmailTemplateVersion,
    *,
    creator_name: str,
    image_url_for: Callable[[dict[str, object]], str] | None = None,
) -> RenderedEmailContent:
    rich_block = next(
        (
            block
            for block in version.content_blocks
            if isinstance(block, dict) and block.get("type") == "rich_text"
        ),
        None,
    )
    if rich_block is not None:
        canonical_html, image_references = sanitize_rich_html(
            rich_block.get("html")
        )
        asset_ids = {
            int(reference["asset_id"])
            for reference in image_references
            if reference.get("asset_id")
        }
        assets = {
            asset.pk: asset
            for asset in EmailTemplateAsset.objects.filter(pk__in=asset_ids)
        }
        images: list[InlineImage] = []

        def rich_image_source(attributes: dict[str, str]) -> str:
            block: dict[str, object] = {
                "type": "image",
                "alt": attributes.get("alt", "邮件图片"),
            }
            if attributes.get("data-legacy-asset") == "true":
                block["legacy_asset"] = True
            else:
                block["asset_id"] = int(attributes["data-asset-id"])
            if image_url_for is not None:
                return image_url_for(block)

            cid = f"vaelos-products-{len(images)}-{uuid4().hex}"
            if block.get("legacy_asset"):
                filename, content_type, reader = _legacy_image()
            else:
                asset = assets.get(int(block["asset_id"]))
                if asset is None:
                    raise _validation_error(
                        "邮件模板引用的图片不存在。"
                    )
                filename = asset.original_name
                content_type = asset.content_type

                def reader(asset=asset) -> bytes:
                    with asset.file.open("rb") as file_handle:
                        return file_handle.read()

            images.append(
                InlineImage(
                    cid=cid,
                    filename=filename,
                    content_type=content_type,
                    read_bytes=reader,
                )
            )
            return f"cid:{cid}"

        rendered_html = render_rich_html(
            canonical_html,
            creator_name=creator_name,
            image_source_for=rich_image_source,
        )
        return RenderedEmailContent(
            subject=personalize(version.subject_template, creator_name),
            text_body=rich_html_to_plain_text(
                canonical_html,
                creator_name=creator_name,
            ),
            html_body=render_to_string(
                "mailing/email_content.html",
                {"rich_html": rendered_html},
            ),
            images=images,
        )

    asset_ids = {
        int(block["asset_id"])
        for block in version.content_blocks
        if isinstance(block, dict) and block.get("asset_id")
    }
    assets = {
        asset.pk: asset
        for asset in EmailTemplateAsset.objects.filter(pk__in=asset_ids)
    }
    rendered_blocks: list[dict[str, object]] = []
    plain_parts: list[str] = []
    images: list[InlineImage] = []

    for index, block in enumerate(version.content_blocks):
        block_type = block["type"]
        if block_type == "text":
            text = personalize(block["text"], creator_name)
            rendered_blocks.append(
                {
                    "type": "text",
                    "style": block.get("style", "paragraph"),
                    "text": text,
                }
            )
            plain_parts.append(text)
        elif block_type == "link":
            text = personalize(block["text"], creator_name)
            rendered_blocks.append(
                {"type": "link", "text": text, "url": block["url"]}
            )
            plain_parts.append(f"{text}: {block['url']}")
        elif block_type == "image":
            alt = personalize(block.get("alt", "邮件图片"), creator_name)
            if image_url_for is not None:
                source = image_url_for(block)
            else:
                cid = f"vaelos-products-{index}-{uuid4().hex}"
                source = f"cid:{cid}"
                if block.get("legacy_asset"):
                    filename, content_type, reader = _legacy_image()
                else:
                    asset = assets.get(int(block["asset_id"]))
                    if asset is None:
                        raise _validation_error("邮件模板引用的图片不存在。")
                    filename = asset.original_name
                    content_type = asset.content_type

                    def reader(asset=asset) -> bytes:
                        with asset.file.open("rb") as file_handle:
                            return file_handle.read()

                images.append(
                    InlineImage(
                        cid=cid,
                        filename=filename,
                        content_type=content_type,
                        read_bytes=reader,
                    )
                )
            rendered_blocks.append(
                {
                    "type": "image",
                    "alt": alt,
                    "source": source,
                }
            )
            plain_parts.append(f"[图片：{alt}]")

    return RenderedEmailContent(
        subject=personalize(version.subject_template, creator_name),
        text_body="\n\n".join(plain_parts),
        html_body=render_to_string(
            "mailing/email_content.html",
            {"blocks": rendered_blocks},
        ),
        images=images,
    )


def rich_html_for_editor(version: EmailTemplateVersion) -> str:
    canonical_html = blocks_to_rich_html(version.content_blocks)
    return hydrated_rich_html_for_editor(canonical_html)


def hydrated_rich_html_for_editor(canonical_html: str) -> str:
    canonical_html, _ = sanitize_rich_html(
        canonical_html,
        allow_upload_tokens=True,
    )

    def image_source_for(attributes: dict[str, str]) -> str:
        if attributes.get("data-legacy-asset") == "true":
            return reverse("mailing:legacy_template_asset")
        if attributes.get("data-asset-id"):
            return reverse(
                "mailing:template_asset",
                args=[attributes["data-asset-id"]],
            )
        return ""

    return hydrate_rich_html_images(canonical_html, image_source_for)


def default_rich_html_for_editor() -> str:
    canonical_html = blocks_to_rich_html(default_email_blocks())
    return hydrated_rich_html_for_editor(canonical_html)
