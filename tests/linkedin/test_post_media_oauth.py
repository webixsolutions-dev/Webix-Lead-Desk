"""Private media integrity, OAuth state binding and official API request contracts."""

import base64
import hashlib
import json
import struct
from unittest.mock import Mock
from urllib.parse import parse_qs, urlsplit

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import select

from app.core.config import Settings
from app.core.vault import read_secret
from app.db.linkedin_post_models import PostingConnection, PostMedia
from app.db.session import Database
from app.integrations import linkedin as provider
from app.linkedin import media, oauth, posts
from app.linkedin.identity import add_profile
from tests.conftest import Account, csrf_from, sign_in
from tests.linkedin.test_posts import BODY, DUE, NOW

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+j6R0AAAAASUVORK5CYII="
)


def test_media_hash_scope_duplicate_and_capacity(
    db: Database, settings: Settings, owner: Account, reviewer: Account
) -> None:
    """Bad/missing chunks and foreign member access fail without exposing bytes."""
    profile = add_profile(db, owner.user.id, "https://linkedin.com/in/media-owner", owner.user.id)
    item = media.begin(
        db, settings, profile.id, owner.user.id, "image", len(PNG), hashlib.sha256(PNG).hexdigest()
    )
    with pytest.raises(ValueError, match="incomplete"):
        media.finish(db, item.id, owner.user.id)
    with pytest.raises(ValueError):
        media.chunk(db, item.id, reviewer.user.id, 0, PNG)
    media.chunk(db, item.id, owner.user.id, 0, PNG)
    media.chunk(db, item.id, owner.user.id, 0, PNG)
    with pytest.raises(ValueError, match="differs"):
        media.chunk(db, item.id, owner.user.id, 0, PNG[:-1] + b"X")
    media.finish(db, item.id, owner.user.id)
    with db.sessions() as session:
        stored = session.get(PostMedia, item.id)
        assert stored and stored.state == "ready" and media.bytes_for(session, stored) == PNG
    tiny = settings.model_copy(update={"linkedin_media_workspace_bytes": len(PNG)})
    with pytest.raises(ValueError, match="full"):
        media.begin(
            db, tiny, profile.id, owner.user.id, "image", len(PNG), hashlib.sha256(PNG).hexdigest()
        )
    media.remove(db, item.id, owner.user.id)


def test_media_containers_reject_html_and_bad_duration() -> None:
    """Signature and bounded duration parsing reject active web content and invalid MP4."""
    with pytest.raises(ValueError):
        media.validate_container(b"<svg onload=alert(1)></svg>", "image")

    def box(name: bytes, body: bytes) -> bytes:
        return struct.pack(">I", len(body) + 8) + name + body

    head = b"\0" * 12 + struct.pack(">II", 1000, 6000) + b"\0" * 12
    mp4 = (
        box(b"ftyp", b"isom\0\0\0\0")
        + box(b"moov", box(b"mvhd", head))
        + box(b"mdat", b"\0" * 75000)
    )
    media.validate_container(mp4, "video")
    with pytest.raises(ValueError):
        media.validate_container(
            mp4.replace(struct.pack(">II", 1000, 6000), struct.pack(">II", 1000, 2000)), "video"
        )


def test_oauth_one_use_session_binding_and_member_confirmation(
    db: Database,
    settings: Settings,
    owner: Account,
    reviewer: Account,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only the initiating logged-in member can exchange and confirm an encrypted connection."""
    profile = add_profile(db, owner.user.id, "https://linkedin.com/in/oauth-owner", owner.user.id)
    config = settings.model_copy(
        update={
            "linkedin_posting_enabled": True,
            "linkedin_client_id": "synthetic-app",
            "linkedin_client_secret": SecretStr("synthetic-secret"),
            "public_base_url": "https://desk.example.test",
            "encryption_key": SecretStr(Fernet.generate_key().decode()),
        }
    )
    with pytest.raises(ValueError):
        oauth.start(db, config, profile.id, reviewer.user.id, "reviewer-session")
    url = oauth.start(db, config, profile.id, owner.user.id, "owner-session")
    query = parse_qs(urlsplit(url).query)
    assert query["scope"] == ["openid profile w_member_social"]
    state = query["state"][0]
    with pytest.raises(ValueError, match="session"):
        oauth.finish(db, config, state, "code", owner.user.id, "other-session")
    monkeypatch.setattr(
        provider,
        "exchange",
        Mock(return_value={"access_token": "test-only-oauth-token", "expires_in": 86400}),
    )
    monkeypatch.setattr(
        provider,
        "userinfo",
        Mock(return_value={"sub": "member-subject", "name": "Synthetic Member"}),
    )
    oauth.finish(db, config, state, "code", owner.user.id, "owner-session")
    with pytest.raises(ValueError):
        oauth.finish(db, config, state, "code", owner.user.id, "owner-session")
    assert read_secret(db, config, "linkedin:" + profile.id) == "test-only-oauth-token"
    with db.sessions() as session:
        row = session.get(PostingConnection, profile.id)
        assert row and row.state == "confirm"
    oauth.connection_action(db, profile.id, owner.user.id, "confirm")
    oauth.connection_action(db, profile.id, owner.user.id, "disconnect")
    with pytest.raises(ValueError, match="missing"):
        read_secret(db, config, "linkedin:" + profile.id)


def test_official_post_contract_and_upload_ranges(monkeypatch: pytest.MonkeyPatch) -> None:
    """POST payload, author, headers and ordered multipart ETags match the official contract."""
    http = Mock(
        return_value=provider.LinkedInResponse(201, {"x-restli-id": "urn:li:share:123"}, {})
    )
    monkeypatch.setattr(provider, "request", http)
    assert provider.publish(
        "urn:li:person:abc", BODY, "urn:li:image:img", "token", "202610"
    ).endswith("share:123/")
    args = http.call_args.args
    assert args[2]["Linkedin-Version"] == "202610"
    assert json.loads(args[3])["content"] == {"media": {"id": "urn:li:image:img"}}
    value = {
        "video": "urn:li:video:abc",
        "uploadToken": "",
        "uploadInstructions": [
            {
                "firstByte": 0,
                "lastByte": 1,
                "uploadUrl": "https://www.linkedin.com/dms-uploads/one",
            },
            {
                "firstByte": 2,
                "lastByte": 3,
                "uploadUrl": "https://www.linkedin.com/dms-uploads/two",
            },
        ],
    }
    api = Mock(
        side_effect=[
            provider.LinkedInResponse(200, {}, {"value": value}),
            provider.LinkedInResponse(200, {}, {}),
        ]
    )
    monkeypatch.setattr(provider, "api", api)
    http.side_effect = [
        provider.LinkedInResponse(200, {"etag": '"first"'}, {}),
        provider.LinkedInResponse(200, {"etag": '"second"'}, {}),
    ]
    assert (
        provider.upload(b"abcd", "video", "urn:li:person:abc", "token", "202610")
        == "urn:li:video:abc"
    )
    assert api.call_args.args[4]["finalizeUploadRequest"]["uploadedPartIds"] == ["first", "second"]
    value["uploadInstructions"][0]["firstByte"] = 1  # type: ignore[index]
    api.side_effect = [provider.LinkedInResponse(200, {}, {"value": value})]
    with pytest.raises(provider.LinkedInError):
        provider.upload(b"abcd", "video", "urn:li:person:abc", "token", "202610")


@pytest.mark.parametrize(
    "url",
    [
        "http://api.linkedin.com/rest/posts",
        "https://evil.test/dms-uploads/a",
        "https://www.linkedin.com/in/member",
        "https://api.linkedin.com@127.0.0.1/rest/posts",
    ],
)
def test_provider_rejects_nonofficial_destinations(url: str) -> None:
    """Credentials and media cannot be forwarded to redirects, local hosts or profile pages."""
    with pytest.raises(provider.LinkedInError):
        provider.request("PUT", url, {"Authorization": "Bearer test-only"}, b"data")


def test_post_media_ui_member_scope_and_csrf(
    db: Database,
    client: TestClient,
    owner: Account,
    reviewer: Account,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exercise actual upload and review routes without making any provider calls."""
    monkeypatch.setattr(posts, "utc_now", lambda: NOW)
    profile = add_profile(db, owner.user.id, "https://linkedin.com/in/ui-post-owner", owner.user.id)
    post = posts.create_slot(db, profile.id, owner.user.id, "A useful lesson", "image", DUE)
    sign_in(client, owner)
    page = client.get("/linkedin/posts/" + post.id)
    assert page.status_code == 200 and "data-media-upload" in page.text
    csrf = csrf_from(page.text)
    response = client.post(
        "/linkedin/media/new",
        data={
            "csrf_token": csrf,
            "profile_id": profile.id,
            "kind": "image",
            "size": str(len(PNG)),
            "digest": hashlib.sha256(PNG).hexdigest(),
        },
    )
    assert response.status_code == 200
    media_id = response.json()["id"]
    assert client.put(f"/linkedin/media/{media_id}/0", content=PNG).status_code == 403
    assert (
        client.put(
            f"/linkedin/media/{media_id}/0", content=PNG, headers={"X-CSRF-Token": csrf}
        ).status_code
        == 200
    )
    assert (
        client.post(f"/linkedin/media/{media_id}/finish", data={"csrf_token": csrf}).status_code
        == 200
    )
    assert client.get(f"/linkedin/media/{media_id}/chunk/0").content == PNG
    posts.save(db, post.id, owner.user.id, 0, BODY, media_id)
    with db.sessions() as session:
        assert session.scalar(select(PostMedia))
    with pytest.raises(ValueError, match="revision"):
        media.remove(db, media_id, owner.user.id)
    sign_in(client, reviewer)
    assert client.get("/linkedin/posts/" + post.id).status_code == 404
    assert client.get(f"/linkedin/media/{media_id}/chunk/0").status_code == 404
    assert "A useful lesson" not in client.get("/linkedin/posts").text
    assert client.post("/linkedin/posts/new", data={}).status_code == 403
