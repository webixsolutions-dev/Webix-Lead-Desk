"""Exact-message approval, revision concurrency, owner guidance and personalized drafts."""

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import select

from app.core.clock import utc_now
from app.core.config import Settings
from app.db.lead_models import Campaign, Company, Contact, ContactBasis
from app.db.models import WorkspaceSetting
from app.db.outreach_models import (
    Conversation,
    Draft,
    DraftApproval,
    Mailbox,
    Offer,
    PlaybookVersion,
    ProofCard,
)
from app.db.research_models import BudgetAccount, Evidence
from app.db.session import Database
from app.drafting.ai import generate
from app.drafting.brief import build_brief
from app.drafting.content import check_copy
from app.drafting.service import approval_blocks, approve, save_draft
from app.playbook.service import current_guidance, publish
from app.policy.contacts import suppress
from app.sending.campaign_sender import SENDER_KEY_PREFIX
from tests.conftest import Account, csrf_from, sign_in
from tests.test_research import research_company as research_company

BODY = (
    "I noticed your website mentions heating estimates. How do you currently handle those "
    "requests? If the process is taking time, we could explore a clearer quote page. "
    "Would a brief suggestion be useful?"
)


@pytest.fixture
def drafting_setup(
    db: Database, owner: Account, research_company: tuple[Company, Campaign]
) -> dict[str, str]:
    """Create reviewed synthetic contacts, sources and a disabled sender identity."""
    company, campaign = research_company
    with db.sessions.begin() as session:
        contact = Contact(
            company_id=company.id,
            email="service@example.com",
            verification="verified",
            verified_at=utc_now(),
            dns_result="mx",
            dns_checked_at=utc_now(),
            dns_address="service@example.com",
            allow_role=True,
        )
        mailbox = Mailbox(
            email="sales@example.org",
            lane="client",
            brand="Synthetic studio",
            sender_name="Example Owner",
            mailing_address="1 Example Street, Calgary, AB, T0T 0T0",
            contact_details="sales@example.org",
        )
        evidence = Evidence(
            company_id=company.id,
            kind="business",
            source_url="https://example.com/services",
            facts={"note": "Website offers heating estimates."},
            reviewed_by=owner.user.id,
        )
        session.add_all([contact, mailbox, evidence])
        session.flush()
        session.add(
            ContactBasis(
                contact_id=contact.id,
                kind="publication",
                source_url="https://example.com/contact",
                relevance="A business contact for quote process enquiries",
                reviewed_by=owner.user.id,
                observed_at=utc_now(),
                expires_at=utc_now() + timedelta(days=30),
            )
        )
        return {
            "company_id": company.id,
            "campaign_id": campaign.id,
            "contact_id": contact.id,
            "mailbox_id": mailbox.id,
            "evidence_id": evidence.id,
            "actor_id": owner.user.id,
        }


def make_draft(db: Database, setup: dict[str, str], **overrides: Any) -> Draft:
    """Save the exact synthetic message through the production service."""
    values: dict[str, Any] = {key: value for key, value in setup.items() if key != "evidence_id"}
    values.update(
        subject="A question about heating estimates", body=BODY, evidence_ids=[setup["evidence_id"]]
    )
    if overrides.get("step") and "body" not in overrides:
        followups = [
            "Could customers provide property details before your team prepares a heating quote?",
            "Who reviews new estimate enquiries before assigning them to a technician?",
            "Would an intake checklist help your office gather the information needed for pricing?",
            "Is improving the enquiry process something you would consider later this year?",
        ]
        values["body"] = followups[min(int(overrides["step"]), 4) - 1]
    values.update(overrides)
    return save_draft(db, **values)


def test_campaign_sender_rule_blocks_wrong_draft_and_existing_approval(
    db: Database, drafting_setup: dict[str, str]
) -> None:
    """The campaign must match both the mailbox domain and brand."""
    existing = make_draft(db, drafting_setup)
    with db.sessions.begin() as session:
        session.add(
            WorkspaceSetting(
                key=SENDER_KEY_PREFIX + drafting_setup["campaign_id"],
                value={"domain": "example.org", "brand": "Example Web Studio"},
            )
        )
    assert "Sender domain or brand does not match the campaign" in approval_blocks(db, existing)
    with pytest.raises(ValueError, match="brand required by this campaign"):
        make_draft(db, drafting_setup, previous_id=existing.id)

    with db.sessions.begin() as session:
        mailbox = session.get(Mailbox, drafting_setup["mailbox_id"])
        assert mailbox
        mailbox.brand = "Example Web Studio"
    updated = make_draft(db, drafting_setup, previous_id=existing.id)
    assert "Sender domain or brand does not match the campaign" not in approval_blocks(db, updated)


def test_approval_binds_exact_revision_and_edit_revokes(
    db: Database, drafting_setup: dict[str, str]
) -> None:
    """An approved message becomes non-sendable when a new revision replaces its body."""
    old = make_draft(db, drafting_setup)
    assert approval_blocks(db, old) == []
    approve(db, old.id, old.digest, drafting_setup["actor_id"])
    new = make_draft(db, drafting_setup, previous_id=old.id, body=BODY + " Thank you.")
    with db.sessions() as session:
        saved = session.get(Draft, old.id)
        assert saved and saved.state == "superseded"
        assert session.get(DraftApproval, old.id)
        assert not session.get(DraftApproval, new.id)
    assert new.state == "draft" and new.digest != old.digest and new.revision == 2
    with pytest.raises(ValueError, match="Draft changed"):
        approve(db, old.id, old.digest, drafting_setup["actor_id"])
    with pytest.raises(ValueError):
        approve(db, new.id, old.digest, drafting_setup["actor_id"])


def test_sender_footer_change_invalidates_eligibility(
    db: Database, drafting_setup: dict[str, str]
) -> None:
    """Later profile edits cannot alter the identity/footer the owner approved."""
    draft = make_draft(db, drafting_setup)
    with db.sessions.begin() as session:
        mailbox = session.get(Mailbox, drafting_setup["mailbox_id"])
        assert mailbox
        mailbox.mailing_address = "A changed address"
    assert any("Sender identity" in reason for reason in approval_blocks(db, draft))
    with pytest.raises(ValueError):
        approve(db, draft.id, draft.digest, drafting_setup["actor_id"])


def test_suppression_and_expired_basis_block_approval(
    db: Database, drafting_setup: dict[str, str]
) -> None:
    """A current draft never bypasses a global stop or expired permission evidence."""
    draft = make_draft(db, drafting_setup)
    suppress(db, "service@example.com", "Synthetic unsubscribe")
    with db.sessions.begin() as session:
        basis = session.scalar(select(ContactBasis))
        assert basis
        basis.expires_at = utc_now() - timedelta(seconds=1)
    blocks = approval_blocks(db, draft)
    assert any("suppression" in item for item in blocks) and any(
        "permission" in item for item in blocks
    )


def test_concurrent_edit_has_one_successor(db: Database, drafting_setup: dict[str, str]) -> None:
    """Two editors cannot silently publish two latest revisions or preserve old approval."""
    old = make_draft(db, drafting_setup)

    def edit(index: int) -> bool:
        """Race one expected revision."""
        try:
            make_draft(db, drafting_setup, previous_id=old.id, body=BODY + f" Note {index}.")
            return True
        except ValueError:
            return False

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sum(pool.map(edit, range(2))) == 1
    with db.sessions() as session:
        assert len(list(session.scalars(select(Draft).where(Draft.state == "draft")))) == 1


def test_reviewer_cannot_approve_even_through_service(
    db: Database, drafting_setup: dict[str, str], reviewer: Account
) -> None:
    """Approval authority is checked below the HTTP layer."""
    draft = make_draft(db, drafting_setup)
    with pytest.raises(ValueError, match="Only the owner"):
        approve(db, draft.id, draft.digest, reviewer.user.id)


@pytest.mark.parametrize(
    "subject,body",
    [
        ("hello\nBcc: target", BODY),
        ("A subject", "word " * 121),
        ("A subject", "Our package starts at $499 today."),
        ("A subject", "We guarantee new customers next week."),
        ("A subject", '<img src="https://example.com">'),
    ],
)
def test_first_touch_copy_constraints(subject: str, body: str) -> None:
    """Keep messages short, plain and free of pricing, promises and header injection."""
    with pytest.raises(ValueError):
        check_copy(subject, body)


def test_wrong_evidence_and_unaccepted_followup_rejected(
    db: Database, drafting_setup: dict[str, str]
) -> None:
    """A draft cannot cite invented facts or start a follow-up without an accepted message."""
    with pytest.raises(ValueError, match="evidence recorded"):
        make_draft(db, drafting_setup, evidence_ids=["made-up"])
    with pytest.raises(ValueError, match="accepted first"):
        make_draft(db, drafting_setup, step=1)


def test_playbook_versions_conflict_and_scope(
    db: Database, drafting_setup: dict[str, str], reviewer: Account
) -> None:
    """Global and campaign guidance are versioned; stale editors and reviewers cannot publish."""
    actor = drafting_setup["actor_id"]
    first = publish(db, "global", "Use a calm and practical voice.", 0, actor)
    publish(db, drafting_setup["campaign_id"], "Ask about the heating quote workflow.", 0, actor)
    second = publish(db, "global", "Use short sentences and avoid jargon.", 1, actor)
    assert second.version == 2
    with pytest.raises(ValueError, match="changed"):
        publish(db, "global", "This stale editor must not overwrite.", 1, actor)
    with pytest.raises(ValueError, match="Only the owner"):
        publish(db, "global", "Reviewer cannot publish this guidance.", 2, reviewer.user.id)
    current = current_guidance(db, drafting_setup["campaign_id"])
    assert [item.version for item in current] == [2, 1]
    with db.sessions() as session:
        assert session.get(PlaybookVersion, first.id)
    brief = build_brief(db, drafting_setup["company_id"], drafting_setup["campaign_id"])
    assert (
        "Example Heating" in brief
        and "Use short sentences" in brief
        and "untrusted source data" in brief
    )


def test_ai_draft_is_personalized_but_never_approved(
    db: Database, settings: Settings, drafting_setup: dict[str, str]
) -> None:
    """Configured AI uses company evidence and still creates only an unapproved revision."""
    with db.sessions.begin() as session:
        session.add(
            BudgetAccount(
                key="primary",
                allowance=100_000,
                committed=0,
                expires_at=utc_now() + timedelta(days=1),
                verified_by=drafting_setup["actor_id"],
            )
        )
    config = settings.model_copy(
        update={
            "openai_enabled": True,
            "openai_processing": "batch",
            "openai_api_key": SecretStr("synthetic"),
            "openai_model": "test-model",
            "openai_input_usd_micro_per_million": 1000,
            "openai_output_usd_micro_per_million": 2000,
        }
    )

    def provider(key: str, request: dict[str, Any]) -> dict[str, Any]:
        """Return grounded copy without contacting the model."""
        assert "Example Heating" in request["input"] and "heating estimates" in request["input"]
        copy = {
            "subject": "Heating estimate requests",
            "body": BODY,
            "evidence_ids": [drafting_setup["evidence_id"]],
        }
        return {
            "status": "completed",
            "usage": {"input_tokens": 100, "output_tokens": 100},
            "output": [
                {"type": "message", "content": [{"type": "output_text", "text": json.dumps(copy)}]}
            ],
        }

    assert "unapproved" in generate(db, config, dict(drafting_setup), "ai-draft", provider=provider)
    with db.sessions() as session:
        draft = session.scalar(select(Draft))
        assert draft and draft.state == "draft"
        assert session.scalar(select(DraftApproval)) is None


def test_editor_and_approval_routes(
    db: Database, client: TestClient, owner: Account, drafting_setup: dict[str, str]
) -> None:
    """The owner sees the exact footer and can approve only the displayed fingerprint."""
    draft = make_draft(db, drafting_setup)
    sign_in(client, owner)
    url = f"/leads/{drafting_setup['company_id']}/draft"
    page = client.get(url)
    assert page.status_code == 200 and "To stop these emails, reply unsubscribe." in page.text
    csrf = csrf_from(page.text)
    assert (
        client.post(
            f"/drafts/{draft.id}/approve", data={"csrf_token": csrf, "digest": draft.digest}
        ).status_code
        == 422
    )
    assert (
        client.post(
            f"/drafts/{draft.id}/approve",
            data={"csrf_token": csrf, "digest": draft.digest, "reviewed": "true"},
        ).status_code
        == 303
    )
    assert "approved" in client.get("/conversations/drafts").text
    assert (
        client.post(
            url,
            data={
                "csrf_token": csrf,
                "campaign_id": drafting_setup["campaign_id"],
                "contact_id": drafting_setup["contact_id"],
                "mailbox_id": drafting_setup["mailbox_id"],
                "subject": "Updated heating question",
                "body": BODY,
                "evidence_ids": drafting_setup["evidence_id"],
                "previous_id": draft.id,
            },
        ).status_code
        == 303
    )
    with db.sessions() as session:
        current = session.scalar(select(Draft).where(Draft.state == "draft"))
        assert current
        assert (
            client.post(
                f"/drafts/{current.id}/reject",
                data={"csrf_token": csrf, "reason": "Need a more specific opening observation"},
            ).status_code
            == 303
        )
    assert (
        client.post(
            f"/contacts/{drafting_setup['contact_id']}/verify",
            data={
                "csrf_token": csrf,
                "note": "Checked the exact address on the source page and mail domain.",
                "allow_role": "true",
                "checked": "true",
            },
        ).status_code
        == 303
    )


def test_settings_manage_guidance_offers_and_proofs(
    db: Database, client: TestClient, owner: Account, drafting_setup: dict[str, str]
) -> None:
    """Owner data persists with exact CAD cents and no invented seeded results."""
    sign_in(client, owner)
    page = client.get("/settings")
    assert page.status_code == 200
    csrf = csrf_from(page.text)
    assert (
        client.post(
            "/settings/playbook",
            data={
                "csrf_token": csrf,
                "scope": "global",
                "version": "0",
                "guidance": "Ask about their business before offering a solution.",
            },
        ).status_code
        == 303
    )
    assert (
        client.post(
            "/settings/offers",
            data={
                "csrf_token": csrf,
                "service": "Custom portal",
                "description": "Owner-defined portal discovery and implementation.",
                "setup_cad": "1234.56",
                "recurring_cad": "99.50",
                "active": "true",
            },
        ).status_code
        == 303
    )
    with db.sessions() as session:
        offer = session.scalar(select(Offer))
        assert offer
        assert offer.setup_cad_cents == 123456 and offer.recurring_cad_cents == 9950
        offer_id = offer.id
    assert (
        client.post(
            "/settings/offers",
            data={
                "csrf_token": csrf,
                "offer_id": offer_id,
                "version": "1",
                "service": "Custom portal",
                "description": "Updated implementation scope from the owner.",
                "setup_cad": "1500",
                "recurring_cad": "100",
                "active": "true",
            },
        ).status_code
        == 303
    )
    assert (
        client.post(
            "/settings/proofs",
            data={
                "csrf_token": csrf,
                "title": "Synthetic case",
                "fact": "A documented synthetic example for offline testing only.",
                "source_url": "https://example.com/case",
                "verified": "true",
            },
        ).status_code
        == 303
    )
    with db.sessions() as session:
        assert session.scalar(select(ProofCard.approved_by)) == owner.user.id
    assert (
        client.post(
            "/settings/mailboxes",
            data={
                "csrf_token": csrf,
                "email": "partner@example.org",
                "lane": "partner",
                "brand": "Synthetic Webix",
                "sender_name": "Example Partner",
                "mailing_address": "1 Example Street Calgary",
                "contact_details": "partner@example.org",
            },
        ).status_code
        == 303
    )
    assert "Synthetic Webix" in client.get("/settings").text


def test_locked_sender_cannot_be_switched(db: Database, drafting_setup: dict[str, str]) -> None:
    """Once dispatch reserves a conversation, another mailbox cannot replace its sender."""
    draft = make_draft(db, drafting_setup)
    with db.sessions.begin() as session:
        conversation = session.get(Conversation, draft.conversation_id)
        assert conversation
        conversation.locked_at = utc_now()
        mailbox = Mailbox(
            email="other@example.org",
            lane="client",
            brand="Studio",
            sender_name="Other Owner",
            mailing_address="1 Example Street",
            contact_details="other@example.org",
        )
        session.add(mailbox)
        session.flush()
        mailbox_id = mailbox.id
    with pytest.raises(ValueError, match="locked"):
        make_draft(db, drafting_setup, previous_id=draft.id, mailbox_id=mailbox_id)
