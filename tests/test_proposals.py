"""Real offline meeting/proposal lifecycle and permissions; no message providers are called."""

from datetime import timedelta
from io import BytesIO
from zipfile import ZipFile

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.bookings.service import decide, save
from app.core.clock import utc_now
from app.db.booking_models import Booking
from app.db.delivery_models import InboxMessage, SendIntent
from app.db.models import Job
from app.db.outreach_models import Draft, Mailbox
from app.db.proposal_models import MeetingPack, Proposal, ProposalFollowup
from app.db.session import Database
from app.proposals.actions import record_sent, review_followup
from app.proposals.notes import confirm_notes
from app.proposals.service import amount, approve, create
from app.reporting.proposals import proposal_work
from tests.conftest import Account, csrf_from, sign_in
from tests.test_drafting import drafting_setup as drafting_setup
from tests.test_drafting import make_draft
from tests.test_research import research_company as research_company
from tests.test_review_workflow import booking_fields


def completed(db: Database, setup: dict[str, str]) -> Booking:
    """Simulate elapsed time in a synthetic database and use the actual booking decisions."""
    with db.sessions() as session:
        existing = session.scalar(select(Draft))
    draft = existing or make_draft(db, setup)
    with db.sessions.begin() as session:
        saved = session.get(Draft, draft.id)
        assert saved
        saved.state = "sent"
    item = save(db, setup["actor_id"], booking_fields(setup))
    decide(db, setup["actor_id"], item.id, item.version, "confirmed")
    with pytest.raises(ValueError, match="past"):
        decide(db, setup["actor_id"], item.id, 2, "completed")
    with db.sessions.begin() as session:
        booking = session.get(Booking, item.id)
        assert booking
        booking.starts_at = utc_now() - timedelta(hours=2)
        booking.ends_at = utc_now() - timedelta(hours=1)
    decide(db, setup["actor_id"], item.id, 2, "completed")
    with db.sessions() as session:
        result = session.get(Booking, item.id)
        assert result
        return result


def fields(db: Database, setup: dict[str, str], booking: Booking) -> dict[str, str]:
    """Only the synthetic owner enters commercial scope and prices."""
    note = confirm_notes(
        db,
        setup["actor_id"],
        booking.id,
        booking.version,
        0,
        "Confirmed five pages, existing brand assets and a request form. No CRM integration.",
    )
    return {
        "note_id": note.id,
        "mailbox_id": setup["mailbox_id"],
        "version": "0",
        "title": "Service website proposal",
        "summary": note.text,
        "scope": "Build five service pages and a reviewed request form.",
        "exclusions": "Hosting, domain renewal and paid licences are separately scoped.",
        "timeline": "Agree milestones after content, access and scope are confirmed.",
        "support": "Recorded handover and thirty days of agreed defect support.",
        "terms": "Payment stages and applicable taxes require written acceptance.",
        "currency": "CAD",
        "setup": "1800.50",
        "recurring": "",
        "period": "monthly",
    }


def test_pack_is_automatic_bounded_and_exact_contact(
    db: Database, drafting_setup: dict[str, str]
) -> None:
    """A name or different email never imports another person's opening or reply."""
    setup = drafting_setup
    booking = completed(db, setup)
    with db.sessions() as session:
        pack = session.scalar(select(MeetingPack).where(MeetingPack.booking_id == booking.id))
        assert pack and pack.snapshot["opening"] and len(pack.snapshot["questions"]) <= 3
        assert "No guaranteed results" in pack.snapshot["cautions"][-1]
    values = booking_fields(setup)
    values["contact"] = "someone-else@example.com"
    other = save(db, setup["actor_id"], values)
    with db.sessions() as session:
        pack = session.scalar(select(MeetingPack).where(MeetingPack.booking_id == other.id))
        assert pack and not pack.snapshot["opening"] and not pack.snapshot["reply"]
        assert not pack.snapshot["conversation_id"]


def test_confirmed_notes_and_revision_approval_are_bound(
    db: Database, drafting_setup: dict[str, str], reviewer: Account
) -> None:
    """Changed notes, stale edits and wrong actors cannot approve an earlier proposal."""
    setup = drafting_setup
    booking = completed(db, setup)
    data = fields(db, setup, booking)
    with pytest.raises(ValueError, match="owner"):
        create(db, reviewer.user.id, booking.id, data)
    p = create(db, setup["actor_id"], booking.id, data)
    with pytest.raises(ValueError, match="changed"):
        create(db, setup["actor_id"], booking.id, data)
    with pytest.raises(ValueError, match="changed"):
        approve(db, setup["actor_id"], p.id, "wrong")
    approve(db, setup["actor_id"], p.id, p.digest)
    revised = create(db, setup["actor_id"], booking.id, {**data, "version": "1", "setup": "1950"})
    with db.sessions() as session:
        old = session.get(Proposal, p.id)
        assert old and old.state == "superseded" and revised.state == "draft"
    with pytest.raises(ValueError, match="changed"):
        approve(db, setup["actor_id"], p.id, p.digest)
    confirm_notes(
        db,
        setup["actor_id"],
        booking.id,
        booking.version,
        1,
        "Updated confirmed scope includes six pages and a revised request form.",
    )
    with pytest.raises(ValueError, match="changed"):
        approve(db, setup["actor_id"], revised.id, revised.digest)
    with pytest.raises(ValueError, match="changed"):
        confirm_notes(
            db,
            setup["actor_id"],
            booking.id,
            booking.version,
            1,
            "This is another attempted notes revision.",
        )


def test_sender_and_incoming_changes_invalidate_approval(
    db: Database, drafting_setup: dict[str, str]
) -> None:
    """Neither sender edits nor a new exact-recipient reply can retain a stale review."""
    setup = drafting_setup
    booking = completed(db, setup)
    data = fields(db, setup, booking)
    p = create(db, setup["actor_id"], booking.id, data)
    with db.sessions.begin() as session:
        sender = session.get(Mailbox, setup["mailbox_id"])
        assert sender
        sender.brand = "Changed brand"
    with pytest.raises(ValueError, match="changed"):
        approve(db, setup["actor_id"], p.id, p.digest)
    p = create(db, setup["actor_id"], booking.id, {**data, "version": "1"})
    approve(db, setup["actor_id"], p.id, p.digest)
    with db.sessions.begin() as session:
        from app.db.outreach_models import Conversation

        thread = session.scalar(select(Conversation))
        assert thread
        session.add(
            InboxMessage(
                mailbox_id=setup["mailbox_id"],
                conversation_id=thread.id,
                provider_key="proposal-reply",
                dedupe_key="proposal-reply",
                sender="service@example.com",
                subject="Scope change",
                body="Please wait; the scope has changed.",
            )
        )
    with pytest.raises(ValueError, match="changed"):
        record_sent(
            db, setup["actor_id"], p.id, p.digest, utc_now(), "Recorded manual delivery reference"
        )


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-1", "0.001", "1000001", "wrong"])
def test_owner_prices_are_exact(value: str) -> None:
    """Reject nonfinite or fractional-cent prices without defaulting them to zero."""
    assert amount("") is None and amount("0") == 0 and amount("1200.25") == 120025
    with pytest.raises(ValueError):
        amount(value)


def test_delivery_creates_only_owner_review_reminders(
    db: Database, drafting_setup: dict[str, str]
) -> None:
    """Day 2/5/9 use actual manual send time, never export time, and cannot send mail."""
    setup = drafting_setup
    booking = completed(db, setup)
    p = create(db, setup["actor_id"], booking.id, fields(db, setup, booking))
    with pytest.raises(ValueError, match="approved"):
        record_sent(
            db,
            setup["actor_id"],
            p.id,
            p.digest,
            utc_now(),
            "Manual delivery reference for synthetic recipient",
        )
    approve(db, setup["actor_id"], p.id, p.digest)
    sent = utc_now()
    record_sent(
        db,
        setup["actor_id"],
        p.id,
        p.digest,
        sent,
        "Manual delivery reference for synthetic recipient",
    )
    with pytest.raises(ValueError, match="approved"):
        record_sent(
            db, setup["actor_id"], p.id, p.digest, sent, "A duplicate recorded delivery reference"
        )
    with db.sessions() as session:
        tasks = list(session.scalars(select(ProposalFollowup).order_by(ProposalFollowup.day)))
        assert [t.day for t in tasks] == [2, 5, 9]
        assert [t.due_at for t in tasks] == [sent + timedelta(days=n) for n in (2, 5, 9)]
        assert not list(session.scalars(select(SendIntent))) and not list(
            session.scalars(select(Job))
        )
        assert len(proposal_work(session, sent + timedelta(days=6))) == 2
    review_followup(
        db,
        setup["actor_id"],
        tasks[0].id,
        "skipped",
        "The prospect replied; review the new request first.",
    )
    with pytest.raises(ValueError, match="already"):
        review_followup(
            db,
            setup["actor_id"],
            tasks[0].id,
            "reviewed",
            "Duplicate review must not resolve it again.",
        )


def test_private_pages_word_export_and_csrf(
    client: TestClient,
    owner: Account,
    reviewer: Account,
    db: Database,
    drafting_setup: dict[str, str],
) -> None:
    """Routes are private, Word text is exact and export cannot approve or mark sent."""
    setup = drafting_setup
    booking = completed(db, setup)
    data = fields(db, setup, booking)
    p = create(db, setup["actor_id"], booking.id, data)
    assert client.get("/proposals/" + p.id).status_code == 303
    sign_in(client, reviewer)
    assert client.get("/proposals/" + p.id).status_code == 403
    sign_in(client, owner)
    for path in [
        "/pipeline/proposals",
        f"/bookings/{booking.id}/pack",
        f"/bookings/{booking.id}/proposal",
        "/proposals/" + p.id,
    ]:
        assert client.get(path).status_code == 200
    page = client.get("/proposals/" + p.id)
    csrf = csrf_from(page.text)
    assert client.post(f"/proposals/{p.id}/approve", data={"digest": p.digest}).status_code == 403
    assert (
        client.post(
            f"/proposals/{p.id}/approve", data={"csrf_token": csrf, "digest": p.digest}
        ).status_code
        == 422
    )
    word = client.get(f"/proposals/{p.id}/word")
    assert word.status_code == 200 and word.headers["cache-control"] == "no-store"
    with ZipFile(BytesIO(word.content)) as z:
        content = z.read("word/document.xml").decode()
        assert (
            "Draft for review" in content and "CAD 1,800.50" in content and "Not quoted" in content
        )
        assert data["scope"] in content
        assert not any("vba" in name.casefold() for name in z.namelist())
    with db.sessions() as session:
        item = session.get(Proposal, p.id)
        assert item and item.state == "draft" and item.sent_at is None
    assert (
        client.post(
            f"/proposals/{p.id}/approve",
            data={"csrf_token": csrf, "digest": p.digest, "checked": "true"},
        ).status_code
        == 303
    )
    assert (
        "Approved"
        in ZipFile(BytesIO(client.get(f"/proposals/{p.id}/word").content))
        .read("word/document.xml")
        .decode()
    )


def test_pipeline_completion_delivery_and_followup_stop(
    db: Database, drafting_setup: dict[str, str]
) -> None:
    """Held meetings and proposal delivery advance the exact contact; stops hold reminders."""
    from app.db.outreach_models import Conversation
    from app.db.pipeline_models import Opportunity
    from app.proposals.followups import hold_reason

    setup = drafting_setup
    draft = make_draft(db, setup)
    with db.sessions.begin() as session:
        session.add(Opportunity(conversation_id=draft.conversation_id, stage="Call booked"))
    booking = completed(db, setup)
    with db.sessions() as session:
        card = session.scalar(select(Opportunity))
        assert card and card.stage == "Meeting held"
        assert proposal_work(session, utc_now())[0]["key"].startswith("proposal-notes:")
    p = create(db, setup["actor_id"], booking.id, fields(db, setup, booking))
    with db.sessions() as session:
        assert any(x["key"] == "proposal:" + p.id for x in proposal_work(session, utc_now()))
    approve(db, setup["actor_id"], p.id, p.digest)
    record_sent(
        db,
        setup["actor_id"],
        p.id,
        p.digest,
        utc_now(),
        "Manually delivered this approved proposal",
    )
    with db.sessions() as session:
        card = session.scalar(select(Opportunity))
        assert card and card.stage == "Proposal sent"
    with db.sessions.begin() as session:
        thread = session.get(Conversation, draft.conversation_id)
        assert thread
        thread.state = "stopped"
    with db.sessions() as session:
        item = session.get(Proposal, p.id)
        assert item and "do not continue" in hold_reason(session, item)


def test_proposal_routes_notes_revisions_delivery_and_reminders(
    client: TestClient, owner: Account, db: Database, drafting_setup: dict[str, str]
) -> None:
    """Exercise form contracts and explicit confirmation for the complete owner workflow."""
    setup = drafting_setup
    booking = completed(db, setup)
    sign_in(client, owner)
    csrf = csrf_from(client.get("/").text)
    path = f"/bookings/{booking.id}"
    notes = {
        "csrf_token": csrf,
        "booking_version": str(booking.version),
        "version": "0",
        "notes": "Confirmed a service page, request form, brand assets and the intended handover.",
    }
    assert client.post(path + "/notes", data=notes).status_code == 422
    assert client.post(path + "/notes", data={**notes, "checked": "true"}).status_code == 303
    from app.proposals.notes import latest_note

    with db.sessions() as session:
        note = latest_note(session, booking.id)
        assert note
    data = {
        "csrf_token": csrf,
        "note_id": note.id,
        "mailbox_id": setup["mailbox_id"],
        "version": "0",
        "title": "Website scope proposal",
        "currency": "USD",
        "setup": "950",
        "recurring": "0",
        "period": "monthly",
        **{
            key: "Confirmed project details recorded by the owner for this section."
            for key in ("summary", "scope", "exclusions", "timeline", "support", "terms")
        },
    }
    response = client.post(path + "/proposal", data=data)
    assert response.status_code == 303
    url = response.headers["location"]
    with db.sessions() as session:
        p = session.get(Proposal, url.split("/")[-1])
        assert p
    assert (
        client.post(
            url + "/approve", data={"csrf_token": csrf, "digest": p.digest, "checked": "true"}
        ).status_code
        == 303
    )
    assert (
        client.post(
            url + "/sent",
            data={
                "csrf_token": csrf,
                "digest": p.digest,
                "sent_at": utc_now().isoformat(),
                "reference": "Manual approved document sent to the exact recorded contact",
                "checked": "true",
            },
        ).status_code
        == 303
    )
    assert (
        client.get(url).status_code == 200 and client.get("/pipeline/proposals").status_code == 200
    )
    with db.sessions() as session:
        task = session.scalar(select(ProposalFollowup).where(ProposalFollowup.proposal_id == p.id))
        assert task
    assert (
        client.post(
            "/proposal-followups/" + task.id,
            data={
                "csrf_token": csrf,
                "state": "skipped",
                "note": "Owner decided to wait for the prospect response.",
            },
        ).status_code
        == 303
    )
    assert client.post(path + "/pack", data={"csrf_token": csrf}).status_code == 303
    assert client.post(path + "/pack", data={"csrf_token": csrf}).status_code == 303
