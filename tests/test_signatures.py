"""Signature sanitization, exact approval binding, MIME output and preview isolation."""

from email import policy
from email.parser import BytesParser
from typing import Any
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from app.core.preferences import SettingsConflict
from app.db.delivery_models import SendIntent
from app.db.models import WorkerInstance, new_id
from app.db.session import Database
from app.drafting.service import approval_blocks, approve
from app.integrations.smtp import SMTPResult
from app.jobs.queue import acquire_worker, claim_next
from app.main import create_app
from app.sending import diagnostic_worker
from app.sending.diagnostics import request_test
from app.sending.reservations import wire_message
from app.sending.signature_html import (
    clean_signature,
    html_alternatives,
    html_message,
    plain_signature,
)
from app.sending.signatures import get_signature, save_signature
from tests.conftest import Account, csrf_from, sign_in
from tests.delivery_helpers import NOW
from tests.test_drafting import drafting_setup as drafting_setup
from tests.test_drafting import make_draft
from tests.test_mailbox_diagnostics import Setup
from tests.test_mailbox_diagnostics import setup as setup
from tests.test_research import research_company as research_company

SIGNATURE = """<table width="560" cellpadding="0" cellspacing="0"><tbody><tr>
<td bgcolor="#d4860a" style="background-color:#d4860a;
background:linear-gradient(rgb(245,166,35) 0%,rgb(154,94,0) 100%);padding:22px 14px;">
<img src="https://example.org/logo.png" alt="Example Studio" width="90" height="90"></td>
<td><div style="font-weight:bold;font-size:32px">Example Owner</div><div>Sales &amp; Marketing</div>
<a href="mailto:sales@example.org">sales@example.org</a><br>
<a href="https://example.org/contact">Website</a></td></tr></tbody></table>"""


@pytest.mark.parametrize(
    "html,forbidden",
    [
        ("<script>alert(1)</script><b>safe</b>", "alert"),
        ('<img src="https://example.org/x" onerror="alert(1)">', "onerror"),
        ('<svg><a href="javascript:alert(1)">x</a></svg><b>safe</b>', "javascript"),
        ('<a href="jav&#x61;script:alert(1)">bad</a>', "href="),
        ('<img src="data:image/svg+xml,bad">', "src="),
        ('<img src="https://127.0.0.1/x">', "src="),
        ('<img src="https://private.local/x">', "src="),
        ('<a href="/logout">bad</a>', "href="),
        ('<div style="background:url(https://example.org/track)">x</div>', "url("),
        ("<div style=\"background:image-set('https://example.org/track')\">x</div>", "image-set"),
        (r'<div style="background:u\72l(https://example.org/track)">x</div>', "track"),
        ('<div style="position:fixed;inset:0;color:#333">x</div>', "position"),
        ('<iframe src="https://example.org">untrusted</iframe>', "iframe"),
        ('<form action="https://example.org"><input name="password"></form>', "form"),
        ('<span id="isPasted" data-clarity-loaded="x">safe</span>', "data-clarity"),
    ],
)
def test_signature_removes_active_markup_and_internal_targets(html: str, forbidden: str) -> None:
    """Pasted markup cannot introduce executable content or arbitrary CSS network fetches."""
    cleaned = clean_signature(html)
    assert forbidden not in cleaned
    assert clean_signature(cleaned) == cleaned


def test_gradient_round_trip_text_alternative_and_escaped_body() -> None:
    """RGB gradients survive repeated sanitation; message text never becomes HTML."""
    clean = clean_signature(SIGNATURE)
    assert "rgb(245,166,35)" in clean and 'role="presentation"' in clean
    assert clean_signature(clean) == clean
    text = plain_signature(clean)
    assert "Example Owner" in text and "https://example.org/contact" in text
    rendered = html_message(
        {
            "subject": "A <test>",
            "body": "<script>alert(1)</script>",
            "signature": {"html": clean, "text": text},
            "footer": "Reply unsubscribe.",
        }
    )
    assert "<script>" not in rendered and "&lt;script&gt;" in rendered
    assert 'lang="en" dir="ltr"' in rendered and "Reply unsubscribe." in rendered
    with pytest.raises(ValueError, match="10 KB"):
        clean_signature("a" * 10001)
    with pytest.raises(ValueError, match="changed"):
        html_message(
            {
                "subject": "Test",
                "body": "Test body",
                "signature": {"html": clean, "text": "Changed"},
            }
        )


def test_signature_edit_invalidates_approval_and_freezes_html(
    db: Database, drafting_setup: dict[str, str]
) -> None:
    """A newly saved signature does not mutate an already approved message or its preview."""
    box, actor = drafting_setup["mailbox_id"], drafting_setup["actor_id"]
    save_signature(db, box, SIGNATURE, 0, actor)
    draft = make_draft(db, drafting_setup)
    assert not approval_blocks(db, draft)
    approve(db, draft.id, draft.digest, actor)
    raw = wire_message(draft, SendIntent(message_id="<synthetic@example.org>", step=0), NOW)
    message = BytesParser(policy=policy.default).parsebytes(raw.encode())
    assert message.get_content_type() == "multipart/alternative"
    assert [part.get_content_type() for part in message.walk()] == [
        "multipart/alternative",
        "text/plain",
        "text/html",
    ]
    assert "reply unsubscribe" in html_alternatives(message)[0].lower()
    assert "Example Owner" in html_alternatives(message)[0]
    with pytest.raises(SettingsConflict):
        save_signature(db, box, "<p>Conflicting</p>", 0, actor)
    save_signature(db, box, "<p>Updated signature</p>", 1, actor)
    assert any("signature changed" in reason for reason in approval_blocks(db, draft))
    assert "Example Owner" in html_message(draft.snapshot)
    assert "Updated signature" not in html_message(draft.snapshot)


def test_signed_test_requires_updated_worker_and_exact_signature(
    db: Database, setup: Setup, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only the capable worker sends the exact approved HTML plus its text fallback."""
    save_signature(db, setup.mailbox, SIGNATURE, 0, setup.actor)
    with db.sessions.begin() as session:
        worker = session.get(WorkerInstance, "primary")
        assert worker
        worker.runtime_config = {"app_mode": "PC", "mailbox_tests": True}
    with pytest.raises(ValueError, match="v0.22"):
        request_test(
            db, setup.config, setup.mailbox, "owner@example.com", new_id(), setup.actor, NOW
        )
    assert acquire_worker(db, setup.config, setup.worker, NOW)
    with pytest.raises(ValueError, match="Signature changed"):
        request_test(
            db,
            setup.config,
            setup.mailbox,
            "owner@example.com",
            new_id(),
            setup.actor,
            NOW,
            signature_version=0,
        )
    request_test(
        db,
        setup.config,
        setup.mailbox,
        "owner@example.com",
        new_id(),
        setup.actor,
        NOW,
        signature_version=1,
    )
    job = claim_next(db, setup.config, setup.worker, NOW)
    assert job
    sender = Mock(return_value=SMTPResult("accepted", "250"))
    monkeypatch.setattr(diagnostic_worker, "transmit", sender)
    monkeypatch.setattr(diagnostic_worker, "check_mail_route", lambda _: "mx")
    assert "Accepted" in diagnostic_worker.send_test(db, setup.config, job, clock=lambda: NOW)
    message = BytesParser(policy=policy.default).parsebytes(sender.call_args.args[4])
    assert "Example Owner" in html_alternatives(message)[0]
    assert message["To"] == "owner@example.com"
    assert not setup.config.live_send_enabled


def test_changed_signature_blocks_pending_test(
    db: Database, setup: Setup, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Editing after approval cannot silently swap the signature at transmission time."""
    save_signature(db, setup.mailbox, SIGNATURE, 0, setup.actor)
    request_test(db, setup.config, setup.mailbox, "owner@example.com", new_id(), setup.actor, NOW)
    job = claim_next(db, setup.config, setup.worker, NOW)
    assert job
    save_signature(db, setup.mailbox, "<p>Changed signature</p>", 1, setup.actor)
    sender = Mock()
    monkeypatch.setattr(diagnostic_worker, "transmit", sender)
    assert "blocked" in diagnostic_worker.send_test(db, setup.config, job, clock=lambda: NOW)
    sender.assert_not_called()


def test_signature_form_and_preview_are_owner_only_and_sandboxed(
    db: Database, setup: Setup, owner: Account, reviewer: Account
) -> None:
    """Unsafe HTML stays escaped in the editor and previews get an isolated browser policy."""
    with TestClient(create_app(setup.config), follow_redirects=False) as client:
        sign_in(client, owner)
        page = client.get("/settings")
        url = f"/settings/mailboxes/{setup.mailbox}/signature"
        data = {"html": SIGNATURE, "version": "0"}
        assert client.post(url, data=data).status_code == 403
        data["csrf_token"] = csrf_from(page.text)
        assert client.post(url, data=data).status_code == 303
        assert client.post(url, data=data).status_code == 409
        preview = client.get(f"{url}-preview?version=1&test=true")
        assert preview.status_code == 200 and "Example Owner" in preview.text
        assert "sandbox" in preview.headers["content-security-policy"]
        assert "script-src 'none'" in preview.headers["content-security-policy"]
        assert preview.headers["x-frame-options"] == "SAMEORIGIN"
        assert preview.headers["referrer-policy"] == "no-referrer"
        assert client.get(f"{url}-preview?version=0").status_code == 409
        assert "script-src 'self'" in client.get("/settings").headers["content-security-policy"]
        client.post("/logout", data={"csrf_token": csrf_from(client.get("/settings").text)})
        sign_in(client, reviewer)
        assert client.post(url, data=data).status_code == 403
        assert client.get(f"{url}-preview?version=1").status_code == 403
    with db.sessions() as session:
        saved: dict[str, Any] = get_signature(session, setup.mailbox)
        assert saved["version"] == 1
