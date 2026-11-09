"""Official member OAuth/media/posts boundary; no browser cookies or scraping."""

import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode, urlsplit

from app.integrations.http import NoRedirect


class LinkedInError(Exception):
    """Redacted provider failure; uncertain writes must never be replayed."""

    def __init__(self, *, uncertain: bool = False, status: int = 0) -> None:
        super().__init__("LinkedIn request needs review")
        self.uncertain, self.status = uncertain, status


@dataclass(frozen=True)
class LinkedInResponse:
    """Bounded response without logging URLs, tokens or provider message bodies."""

    status: int
    headers: dict[str, str]
    data: dict[str, Any]


def request(
    method: str, url: str, headers: dict[str, str], body: bytes | None = None
) -> LinkedInResponse:
    """Restrict HTTPS origins and upload paths; never follow redirects or retry writes."""
    parsed = urlsplit(url)
    allowed = (
        parsed.hostname == "api.linkedin.com"
        and (parsed.path.startswith("/rest/") or parsed.path == "/v2/userinfo")
    ) or (
        parsed.hostname == "www.linkedin.com"
        and (parsed.path == "/oauth/v2/accessToken" or parsed.path.startswith("/dms-uploads/"))
    )
    if (
        not allowed
        or parsed.scheme != "https"
        or parsed.port not in {None, 443}
        or (parsed.username or parsed.password or parsed.fragment)
    ):
        raise LinkedInError()
    req = urllib.request.Request(url, method=method, headers=headers, data=body)
    try:
        with urllib.request.build_opener(NoRedirect()).open(req, timeout=30) as response:
            raw = response.read(1_000_001)
            if len(raw) > 1_000_000:
                raise LinkedInError(uncertain=method != "GET")
            data = json.loads(raw) if raw else {}
            if not isinstance(data, dict):
                raise LinkedInError(uncertain=method != "GET")
            return LinkedInResponse(
                response.status, {k.lower(): v for k, v in response.headers.items()}, data
            )
    except urllib.error.HTTPError as error:
        raise LinkedInError(uncertain=error.code >= 500, status=error.code) from None
    except (OSError, urllib.error.URLError, ValueError) as error:
        raise LinkedInError(uncertain=method != "GET") from error


def exchange(code: str, client_id: str, secret: str, redirect: str) -> dict[str, Any]:
    """Exchange a one-use authorization code without logging provider secrets."""
    return request(
        "POST",
        "https://www.linkedin.com/oauth/v2/accessToken",
        {"Content-Type": "application/x-www-form-urlencoded"},
        urlencode(
            {
                "grant_type": "authorization_code",
                "code": code,
                "client_id": client_id,
                "client_secret": secret,
                "redirect_uri": redirect,
            }
        ).encode(),
    ).data


def userinfo(token: str) -> dict[str, Any]:
    """Resolve the authenticated subject directly from the official OIDC userinfo endpoint."""
    return request(
        "GET", "https://api.linkedin.com/v2/userinfo", {"Authorization": "Bearer " + token}
    ).data


def api(
    method: str, path: str, token: str, version: str, payload: dict[str, Any] | None = None
) -> LinkedInResponse:
    """Use explicit supported version and Rest.li headers for every REST request."""
    if not path.startswith("/rest/"):
        raise LinkedInError()
    return request(
        method,
        "https://api.linkedin.com" + path,
        {
            "Authorization": "Bearer " + token,
            "Linkedin-Version": version,
            "X-Restli-Protocol-Version": "2.0.0",
            "Content-Type": "application/json",
        },
        json.dumps(payload).encode() if payload is not None else None,
    )


def upload(data: bytes, kind: str, author: str, token: str, version: str) -> str:
    """Upload one immutable image or MP4; verify complete ordered multipart ranges."""
    video = kind == "video"
    init: dict[str, Any] = {"owner": author}
    if video:
        init.update(fileSizeBytes=len(data), uploadCaptions=False, uploadThumbnail=False)
    path = "/rest/videos" if video else "/rest/images"
    value = api(
        "POST", path + "?action=initializeUpload", token, version, {"initializeUploadRequest": init}
    ).data.get("value", {})
    urn = value.get("video" if video else "image", "")
    prefix = "video" if video else "image"
    if not isinstance(urn, str) or not re.fullmatch(r"urn:li:" + prefix + r":[\w-]+", urn):
        raise LinkedInError()
    if not video:
        request(
            "PUT",
            str(value.get("uploadUrl", "")),
            {"Content-Type": "application/octet-stream", "Authorization": "Bearer " + token},
            data,
        )
        return urn
    parts, cursor, ids = value.get("uploadInstructions", []), 0, []
    if not isinstance(parts, list) or not 1 <= len(parts) <= 32:
        raise LinkedInError()
    for part in parts:
        start, end = part.get("firstByte"), part.get("lastByte")
        if (
            not isinstance(start, int)
            or not isinstance(end, int)
            or (start != cursor or end < start or end >= len(data))
        ):
            raise LinkedInError()
        result = request(
            "PUT",
            str(part.get("uploadUrl", "")),
            {"Content-Type": "application/octet-stream"},
            data[start : end + 1],
        )
        etag = result.headers.get("etag", "").strip('"')
        if not etag or len(etag) > 2000:
            raise LinkedInError()
        ids.append(etag)
        cursor = end + 1
    if cursor != len(data):
        raise LinkedInError()
    api(
        "POST",
        path + "?action=finalizeUpload",
        token,
        version,
        {
            "finalizeUploadRequest": {
                "video": urn,
                "uploadToken": value.get("uploadToken", ""),
                "uploadedPartIds": ids,
            }
        },
    )
    return urn


def publish(author: str, body: str, media: str, token: str, version: str) -> str:
    """Submit once; require a definitive created post id before recording success."""
    payload: dict[str, Any] = {
        "author": author,
        "commentary": body,
        "visibility": "PUBLIC",
        "distribution": {
            "feedDistribution": "MAIN_FEED",
            "targetEntities": [],
            "thirdPartyDistributionChannels": [],
        },
        "lifecycleState": "PUBLISHED",
        "isReshareDisabledByAuthor": False,
    }
    if media:
        payload["content"] = {"media": {"id": media}}
    result = api("POST", "/rest/posts", token, version, payload)
    urn = result.headers.get("x-restli-id", "")
    if result.status != 201 or not re.fullmatch(r"urn:li:(?:share|ugcPost):[0-9]+", urn):
        raise LinkedInError(uncertain=True)
    return "https://www.linkedin.com/feed/update/" + urn + "/"
