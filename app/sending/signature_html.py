"""Sanitize owner-authored email signatures and render safe multipart content."""

import re
from email.message import EmailMessage, Message
from html import escape
from html.parser import HTMLParser
from typing import Any

import nh3
import tinycss2

from app.sources.validation import email_address, website_url

STYLE_PROPERTIES = {
    "background",
    "background-color",
    "border",
    "border-collapse",
    "border-color",
    "border-radius",
    "border-top",
    "color",
    "display",
    "font-family",
    "font-size",
    "font-style",
    "font-weight",
    "height",
    "letter-spacing",
    "line-height",
    "margin",
    "margin-bottom",
    "margin-top",
    "max-width",
    "overflow",
    "padding",
    "padding-top",
    "text-align",
    "text-decoration",
    "vertical-align",
    "white-space",
    "width",
}


def safe_css_tokens(tokens: list[Any]) -> bool:
    """Allow presentation values and color gradients, never URLs or dynamic CSS."""
    for token in tokens:
        if token.type == "function":
            if token.lower_name not in {"rgb", "rgba", "linear-gradient"} or not safe_css_tokens(
                token.arguments
            ):
                return False
        elif token.type not in {
            "ident",
            "number",
            "percentage",
            "dimension",
            "hash",
            "literal",
            "whitespace",
            "string",
        }:
            return False
    return True


def clean_style(value: str) -> str:
    """Parse CSS declarations and preserve nested RGB gradients without active content."""
    declarations = tinycss2.parse_blocks_contents(value, skip_comments=True, skip_whitespace=True)
    safe = []
    for declaration in declarations:
        if (
            declaration.type == "declaration"
            and declaration.lower_name in STYLE_PROPERTIES
            and safe_css_tokens(declaration.value)
        ):
            declaration.important = False
            safe.append(declaration)
    return str(tinycss2.serialize(safe))


def filter_attribute(tag: str, attr: str, value: str) -> str | None:
    """Keep public image/link targets and bounded presentation attributes only."""
    # No CSS fetches, escaped identifiers, variables or active legacy CSS.
    if attr == "style" and (
        "\\" in value or re.search(r"url\s*\(|expression|javascript|@|/\*|var\s*\(", value, re.I)
    ):
        return None
    if attr == "style":
        return clean_style(value)
    if attr in {"href", "src"}:
        try:
            if tag == "a" and value.startswith("mailto:"):
                return "mailto:" + email_address(value[7:]) if value[7:] else None
            if tag == "a" and re.fullmatch(r"tel:\+?[0-9 ()-]{5,30}", value):
                return value
            if not value.startswith(("https://", "http://")):
                return None
            return website_url(value.replace("http://", "https://", 1))
        except ValueError:
            return None
    if attr in {
        "width",
        "height",
        "cellpadding",
        "cellspacing",
        "border",
        "colspan",
    } and not re.fullmatch(r"[0-9]{1,3}%?", value):
        return None
    return value


def clean_signature(value: str) -> str:
    """Return a deterministic, bounded HTML fragment; never fetch remote assets."""
    if len(value.encode()) > 10000:
        raise ValueError("Keep the signature HTML under 10 KB")
    return nh3.clean(
        value.strip(),
        tags={
            "table",
            "tbody",
            "tr",
            "td",
            "div",
            "p",
            "span",
            "strong",
            "b",
            "em",
            "i",
            "br",
            "a",
            "img",
        },
        clean_content_tags={"script", "style", "iframe", "object", "svg", "math", "form"},
        attributes={
            "*": {"style", "align", "valign"},
            "table": {"width", "cellpadding", "cellspacing", "border"},
            "td": {"width", "height", "colspan", "bgcolor"},
            "a": {"href", "title"},
            "img": {"src", "width", "height", "alt"},
        },
        attribute_filter=filter_attribute,
        url_schemes={"https", "mailto", "tel"},
        url_relative="deny",
        set_tag_attribute_values={"table": {"role": "presentation"}},
    )


class PlainSignature(HTMLParser):
    """Extract a readable fallback from already-sanitized owner content."""

    def __init__(self) -> None:
        """Initialize text and current link buffers."""
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.link = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        """Preserve line breaks, image labels and actual link destinations."""
        values = dict(attrs)
        if tag in {"div", "p", "tr", "br"}:
            self.parts.append("\n")
        if tag == "a":
            self.link = values.get("href") or ""
        if tag == "img" and values.get("alt"):
            self.parts.append(values["alt"] or "")

    def handle_endtag(self, tag: str) -> None:
        """Separate layout rows and retain URLs for readers without HTML."""
        if tag == "a" and self.link:
            if self.link.startswith("https://"):
                self.parts.append(" (" + self.link + ")")
            self.link = ""
        if tag in {"div", "p", "tr", "table"}:
            self.parts.append("\n")
        elif tag == "td":
            self.parts.append(" ")

    def handle_data(self, data: str) -> None:
        """Collect visible text with zero-width editor artifacts removed."""
        self.parts.append(data.replace("\u200b", ""))


def plain_signature(html: str) -> str:
    """Derive a text alternative from the saved HTML, without separate drifting copy."""
    parser = PlainSignature()
    parser.feed(html)
    return "\n".join(
        line for raw in "".join(parser.parts).splitlines() if (line := " ".join(raw.split()))
    )


def signature_parts(snapshot: dict[str, Any]) -> tuple[str, str]:
    """Reject altered signature snapshots rather than silently changing approved content."""
    signature = snapshot.get("signature") or {}
    if not isinstance(signature, dict):
        raise ValueError("Invalid signature content")
    html, text = signature.get("html", ""), signature.get("text", "")
    if not isinstance(html, str) or not isinstance(text, str):
        raise ValueError("Invalid signature content")
    if clean_signature(html) != html or plain_signature(html) != text:
        raise ValueError("Signature content changed; review a new revision")
    return html, text


def html_message(snapshot: dict[str, Any]) -> str:
    """Render precisely the approved body, sanitized signature and mandatory footer."""
    signature, _ = signature_parts(snapshot)
    body = escape(snapshot["body"]).replace("\n", "<br>")
    footer = escape(snapshot.get("footer", "")).replace("\n", "<br>")
    return (
        '<!doctype html><html lang="en" dir="ltr"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>{escape(snapshot['subject'])}</title></head>"
        '<body style="margin:0;padding:16px;background:#fff;color:#222">'
        '<div lang="en" dir="ltr" '
        'style="font-family:Arial,sans-serif;font-size:14px;line-height:1.5">'
        f"<div>{body}</div><br>{signature}"
        f'<div style="margin-top:16px;font-size:12px;color:#555">{footer}</div>'
        "</div></body></html>"
    )


def set_message_content(message: EmailMessage, snapshot: dict[str, Any]) -> None:
    """Keep legacy plain messages; add an HTML alternative when a signature is approved."""
    html, text = signature_parts(snapshot)
    message.set_content(
        "\n\n".join(part for part in (snapshot["body"], text, snapshot.get("footer", "")) if part)
    )
    if html:
        message.add_alternative(html_message(snapshot), subtype="html")


def html_alternatives(message: Message) -> list[str]:
    """Extract all HTML alternatives for exact Sent-folder reconciliation."""
    result: list[str] = []
    for part in message.walk():
        if part.get_content_type() == "text/html":
            payload = part.get_payload(decode=True)
            if isinstance(payload, bytes):
                result.append(
                    payload.decode(part.get_content_charset() or "utf-8", errors="replace")
                    .replace("\r\n", "\n")
                    .strip()
                )
    return result
