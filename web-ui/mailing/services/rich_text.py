"""Safe rich-text handling for the email editor and MIME renderer."""

from __future__ import annotations

from dataclasses import dataclass
from html import escape
from html.parser import HTMLParser
from urllib.parse import urlparse

from django.core.exceptions import ValidationError

MAX_RICH_HTML_LENGTH = 100_000
MAX_RICH_IMAGES = 10
MAX_IMAGE_ALT_LENGTH = 500
VOID_TAGS = {"br", "img"}
BLOCK_TAGS = {"p", "h2", "h3", "ul", "ol", "li", "blockquote"}
INLINE_TAGS = {"strong", "em", "u", "a"}
ALLOWED_TAGS = BLOCK_TAGS | INLINE_TAGS | VOID_TAGS
SKIPPED_TAGS = {"script", "style", "iframe", "object", "embed", "svg"}
TAG_ALIASES = {"b": "strong", "i": "em", "div": "p"}
ALLOWED_ALIGNMENTS = {"left", "center", "right"}


def rich_text_error(message: str) -> ValidationError:
    return ValidationError(message, code="invalid_email_rich_text")


def _http_url(value: object) -> str:
    url = str(value or "").strip()
    parsed = urlparse(url)
    if (
        not url
        or len(url) > 2_000
        or parsed.scheme not in {"http", "https"}
        or not parsed.netloc
    ):
        raise rich_text_error("正文中的超链接必须是完整的 HTTP/HTTPS 地址。")
    return url


def _alignment(attributes: dict[str, str]) -> str:
    direct = attributes.get("data-align", "").strip().lower()
    if direct in ALLOWED_ALIGNMENTS:
        return direct
    style = attributes.get("style", "").lower().replace(" ", "")
    for alignment in ALLOWED_ALIGNMENTS:
        if f"text-align:{alignment}" in style:
            return alignment
    return ""


def _image_reference(
    attributes: dict[str, str],
    *,
    allow_upload_tokens: bool,
) -> dict[str, object]:
    alt = attributes.get("alt", "").strip() or "邮件图片"
    if len(alt) > MAX_IMAGE_ALT_LENGTH:
        raise rich_text_error(
            f"图片替代文字不能超过 {MAX_IMAGE_ALT_LENGTH} 个字符。"
        )
    reference: dict[str, object] = {"alt": alt}
    if attributes.get("data-legacy-asset") == "true":
        reference["legacy_asset"] = True
        return reference
    asset_id = attributes.get("data-asset-id", "")
    if asset_id:
        try:
            parsed_id = int(asset_id)
        except ValueError as error:
            raise rich_text_error("正文引用的图片无效。") from error
        if parsed_id < 1:
            raise rich_text_error("正文引用的图片无效。")
        reference["asset_id"] = parsed_id
        return reference
    token = attributes.get("data-upload-token", "")
    if allow_upload_tokens and token:
        if (
            len(token) > 80
            or not token.replace("-", "").replace("_", "").isalnum()
        ):
            raise rich_text_error("图片上传标识无效。")
        reference["upload_token"] = token
        return reference
    raise rich_text_error("正文中有图片尚未选择文件。")


class _Sanitizer(HTMLParser):
    def __init__(self, *, allow_upload_tokens: bool) -> None:
        super().__init__(convert_charrefs=True)
        self.allow_upload_tokens = allow_upload_tokens
        self.output: list[str] = []
        self.stack: list[str] = []
        self.images: list[dict[str, object]] = []
        self.skipped_depth = 0
        self.has_content = False

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        original_tag = tag.lower()
        if self.skipped_depth:
            if original_tag in SKIPPED_TAGS:
                self.skipped_depth += 1
            return
        if original_tag in SKIPPED_TAGS:
            self.skipped_depth = 1
            return
        tag = TAG_ALIASES.get(original_tag, original_tag)
        if tag not in ALLOWED_TAGS:
            return
        attributes = {
            name.lower(): str(value or "")
            for name, value in attrs
        }
        if tag == "a":
            url = _http_url(attributes.get("href"))
            self.output.append(f'<a href="{escape(url, quote=True)}">')
        elif tag == "img":
            reference = _image_reference(
                attributes,
                allow_upload_tokens=self.allow_upload_tokens,
            )
            self.images.append(reference)
            if len(self.images) > MAX_RICH_IMAGES:
                raise rich_text_error(
                    f"邮件正文最多支持 {MAX_RICH_IMAGES} 张图片。"
                )
            rendered_attrs = []
            if reference.get("legacy_asset"):
                rendered_attrs.append('data-legacy-asset="true"')
            elif reference.get("asset_id"):
                rendered_attrs.append(
                    f'data-asset-id="{reference["asset_id"]}"'
                )
            else:
                rendered_attrs.append(
                    "data-upload-token="
                    f'"{escape(str(reference["upload_token"]), quote=True)}"'
                )
            rendered_attrs.append(
                f'alt="{escape(str(reference["alt"]), quote=True)}"'
            )
            self.output.append(f'<img {" ".join(rendered_attrs)}>')
            self.has_content = True
        elif tag == "br":
            self.output.append("<br>")
        else:
            align = _alignment(attributes) if tag in BLOCK_TAGS else ""
            align_attr = f' data-align="{align}"' if align else ""
            self.output.append(f"<{tag}{align_attr}>")
        if tag not in VOID_TAGS:
            self.stack.append(tag)

    def handle_startendtag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag: str) -> None:
        original_tag = tag.lower()
        if self.skipped_depth:
            if original_tag in SKIPPED_TAGS:
                self.skipped_depth -= 1
            return
        tag = TAG_ALIASES.get(original_tag, original_tag)
        if tag not in self.stack:
            return
        while self.stack:
            opened = self.stack.pop()
            self.output.append(f"</{opened}>")
            if opened == tag:
                break

    def handle_data(self, data: str) -> None:
        if self.skipped_depth or not data:
            return
        self.output.append(escape(data))
        if data.strip():
            self.has_content = True

    def finish(self) -> str:
        while self.stack:
            self.output.append(f"</{self.stack.pop()}>")
        if not self.has_content:
            raise rich_text_error("邮件正文不能为空。")
        return "".join(self.output)


def sanitize_rich_html(
    value: object,
    *,
    allow_upload_tokens: bool = False,
) -> tuple[str, list[dict[str, object]]]:
    source = str(value or "").strip()
    if not source:
        raise rich_text_error("邮件正文不能为空。")
    if len(source) > MAX_RICH_HTML_LENGTH:
        raise rich_text_error(
            f"邮件正文不能超过 {MAX_RICH_HTML_LENGTH} 个字符。"
        )
    parser = _Sanitizer(allow_upload_tokens=allow_upload_tokens)
    try:
        parser.feed(source)
        parser.close()
        sanitized = parser.finish()
    except ValidationError:
        raise
    except (ValueError, TypeError) as error:
        raise rich_text_error("邮件正文格式无效。") from error
    return sanitized, parser.images


def blocks_to_rich_html(blocks: list[dict[str, object]]) -> str:
    parts: list[str] = []
    for block in blocks:
        block_type = block.get("type")
        if block_type == "rich_text":
            sanitized, _ = sanitize_rich_html(block.get("html"))
            return sanitized
        if block_type == "text":
            tag = "h3" if block.get("style") == "heading" else "p"
            text = escape(str(block.get("text") or "")).replace("\n", "<br>")
            parts.append(f"<{tag}>{text}</{tag}>")
        elif block_type == "link":
            url = _http_url(block.get("url"))
            text = escape(str(block.get("text") or ""))
            parts.append(
                f'<p><a href="{escape(url, quote=True)}">{text}</a></p>'
            )
        elif block_type == "image":
            alt = escape(str(block.get("alt") or "邮件图片"), quote=True)
            if block.get("legacy_asset"):
                reference = 'data-legacy-asset="true"'
            else:
                reference = f'data-asset-id="{int(block["asset_id"])}"'
            parts.append(f"<p><img {reference} alt=\"{alt}\"></p>")
    sanitized, _ = sanitize_rich_html("".join(parts))
    return sanitized


class _ImageHydrator(HTMLParser):
    def __init__(self, image_source_for) -> None:
        super().__init__(convert_charrefs=True)
        self.image_source_for = image_source_for
        self.output: list[str] = []

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        attributes = {
            name: str(value or "")
            for name, value in attrs
        }
        rendered = [
            f'{name}="{escape(value, quote=True)}"'
            for name, value in attributes.items()
        ]
        if tag == "img":
            source = self.image_source_for(attributes)
            if source:
                rendered.append(f'src="{escape(source, quote=True)}"')
        suffix = f" {' '.join(rendered)}" if rendered else ""
        self.output.append(f"<{tag}{suffix}>")

    def handle_endtag(self, tag: str) -> None:
        if tag not in VOID_TAGS:
            self.output.append(f"</{tag}>")

    def handle_data(self, data: str) -> None:
        self.output.append(escape(data))


def hydrate_rich_html_images(canonical_html: str, image_source_for) -> str:
    parser = _ImageHydrator(image_source_for)
    parser.feed(canonical_html)
    parser.close()
    return "".join(parser.output)


def personalize(value: object, creator_name: str) -> str:
    return str(value or "").replace(
        "{{ creator_name }}",
        creator_name or "Creator",
    )


class _EmailHtmlRenderer(HTMLParser):
    def __init__(self, *, creator_name: str, image_source_for) -> None:
        super().__init__(convert_charrefs=True)
        self.creator_name = creator_name
        self.image_source_for = image_source_for
        self.output: list[str] = []

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        attributes = {
            name: str(value or "")
            for name, value in attrs
        }
        align = attributes.get("data-align", "left")
        align_style = (
            f"text-align:{align};"
            if align in ALLOWED_ALIGNMENTS and align != "left"
            else ""
        )
        if tag == "p":
            self.output.append(
                f'<p style="margin:0 0 16px;{align_style}">'
            )
        elif tag == "h2":
            self.output.append(
                '<h2 style="margin:24px 0 10px;font-size:22px;'
                f'line-height:1.35;{align_style}">'
            )
        elif tag == "h3":
            self.output.append(
                '<h3 style="margin:22px 0 8px;font-size:17px;'
                f'line-height:1.45;{align_style}">'
            )
        elif tag in {"ul", "ol"}:
            self.output.append(
                f'<{tag} style="margin:0 0 16px;padding-left:24px;">'
            )
        elif tag == "li":
            self.output.append('<li style="margin:5px 0;">')
        elif tag == "blockquote":
            self.output.append(
                '<blockquote style="margin:16px 0;padding:10px 14px;'
                'border-left:3px solid #9eb7a7;color:#4d5a52;">'
            )
        elif tag == "a":
            url = _http_url(attributes.get("href"))
            self.output.append(
                f'<a href="{escape(url, quote=True)}" '
                'style="color:#604713;font-weight:bold;">'
            )
        elif tag == "img":
            source = self.image_source_for(attributes)
            alt = personalize(attributes.get("alt"), self.creator_name)
            self.output.append(
                f'<img src="{escape(source, quote=True)}" '
                f'alt="{escape(alt, quote=True)}" width="680" '
                'style="display:block;width:100%;max-width:680px;'
                'height:auto;border:0;border-radius:6px;'
                'margin:12px 0 20px;">'
            )
        elif tag == "br":
            self.output.append("<br>")
        elif tag in INLINE_TAGS:
            self.output.append(f"<{tag}>")

    def handle_endtag(self, tag: str) -> None:
        if tag not in VOID_TAGS:
            self.output.append(f"</{tag}>")

    def handle_data(self, data: str) -> None:
        self.output.append(
            escape(personalize(data, self.creator_name))
        )


def render_rich_html(
    canonical_html: str,
    *,
    creator_name: str,
    image_source_for,
) -> str:
    parser = _EmailHtmlRenderer(
        creator_name=creator_name,
        image_source_for=image_source_for,
    )
    parser.feed(canonical_html)
    parser.close()
    return "".join(parser.output)


class _PlainTextRenderer(HTMLParser):
    def __init__(self, creator_name: str) -> None:
        super().__init__(convert_charrefs=True)
        self.creator_name = creator_name
        self.output: list[str] = []
        self.link_stack: list[str] = []

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        attributes = {
            name: str(value or "")
            for name, value in attrs
        }
        if tag == "br":
            self.output.append("\n")
        elif tag == "li":
            self.output.append("• ")
        elif tag == "a":
            self.link_stack.append(attributes.get("href", ""))
        elif tag == "img":
            alt = personalize(attributes.get("alt"), self.creator_name)
            self.output.append(f"[图片：{alt}]")

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self.link_stack:
            self.output.append(f": {self.link_stack.pop()}")
        if tag in BLOCK_TAGS:
            self.output.append("\n\n")

    def handle_data(self, data: str) -> None:
        self.output.append(personalize(data, self.creator_name))


def rich_html_to_plain_text(
    canonical_html: str,
    *,
    creator_name: str,
) -> str:
    parser = _PlainTextRenderer(creator_name)
    parser.feed(canonical_html)
    parser.close()
    lines = [line.rstrip() for line in "".join(parser.output).splitlines()]
    output: list[str] = []
    previous_blank = False
    for line in lines:
        blank = not line.strip()
        if blank and previous_blank:
            continue
        output.append(line)
        previous_blank = blank
    return "\n".join(output).strip()
