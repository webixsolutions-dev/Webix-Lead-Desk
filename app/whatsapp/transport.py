"""Native WhatsApp Agent Platform v1, fixed origin and bounded redacted failures.

Contract: https://www.whatsapp.com/developer/WhatsApp-Agent-Platform-Developer-Manual.pdf
Version 1, August 25 2026. This is not the WhatsApp Business Cloud API.
"""

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from app.integrations.http import NoRedirect

BASE = "https://api.whatsapp.com/agent/v1"


class AgentError(Exception):
    """Safe failure code without response bodies, tokens or prospect contents."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def request(token: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """Send only to the official origin; never forward credentials on a redirect."""
    if not token or any(c in token for c in "\r\n"):
        raise AgentError("invalid_key")
    req = urllib.request.Request(
        BASE + path,
        data=json.dumps(payload).encode() if payload is not None else None,
        headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"},
    )
    try:
        with urllib.request.build_opener(NoRedirect()).open(req, timeout=45) as response:
            raw = response.read(1_000_001)
        if len(raw) > 1_000_000:
            raise AgentError("invalid_response")
        result = json.loads(raw)
        if not isinstance(result, dict):
            raise AgentError("invalid_response")
        return result
    except urllib.error.HTTPError as error:
        raise AgentError(
            "rate_limited"
            if error.code == 429
            else "key_or_request_rejected"
            if 400 <= error.code < 500
            else "delivery_unknown"
        ) from None
    except (OSError, urllib.error.URLError):
        raise AgentError("connection_unknown") from None
    except (ValueError, UnicodeError):
        raise AgentError("invalid_response") from None


def updates(token: str, offset: int | None) -> tuple[int, list[dict[str, Any]]]:
    """Short-poll with a durable offset; no-offset first request deliberately skips history."""
    params = {"limit": 20, "timeout": 0}
    if offset is not None:
        params["offset"] = offset
    data = request(token, "/updates?" + urllib.parse.urlencode(params))
    cursor = data.get("next_offset")
    if type(cursor) is not int or cursor < 0 or (offset is not None and cursor < offset):
        raise AgentError("invalid_response")
    try:
        messages = [
            message
            for entry in data.get("entry", [])
            for change in entry["changes"]
            for message in change["value"].get("messages", [])
        ]
        if len(messages) > 100 or any(not isinstance(m, dict) for m in messages):
            raise ValueError
    except (KeyError, TypeError, ValueError):
        raise AgentError("invalid_response") from None
    return cursor, messages


def send(token: str, recipient: str, body: str) -> str:
    """Return the accepted message ID; uncertain outcomes must not be auto-retried."""
    if not recipient.startswith("user:") or not 1 <= len(body) <= 4096:
        raise AgentError("invalid_message")
    data = request(
        token,
        "/messages",
        {
            "messaging_product": "whatsapp",
            "to": recipient,
            "type": "text",
            "text": {"body": body, "preview_url": False},
        },
    )
    try:
        message_id = data["messages"][0]["id"]
        if not isinstance(message_id, str) or not 1 <= len(message_id) <= 300:
            raise ValueError
        return message_id
    except (KeyError, IndexError, TypeError, ValueError):
        raise AgentError("delivery_unknown") from None
