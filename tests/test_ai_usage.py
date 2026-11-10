"""Financial display keeps uncertainty, expiry, roles and reporting boundaries honest."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.audit.usage import allowance_summary, dollars, usage_report
from app.core.config import Settings
from app.core.preferences import get_preferences
from app.db.acquisition_models import SearchBucket, SearchRecord
from app.db.lead_models import Campaign, Company
from app.db.research_models import AICall, BudgetAccount
from app.db.session import Database
from tests.conftest import Account, csrf_from, sign_in

NOW = datetime(2024, 3, 10, 18, tzinfo=UTC)


def test_allowance_keeps_reservations_and_expired_funding(db: Database, owner: Account) -> None:
    """An expiry never releases uncertain funds or pretends historical costs reset."""
    with db.sessions.begin() as session:
        session.add(
            BudgetAccount(
                key="primary",
                allowance=10_000_000,
                committed=8_000_000,
                expires_at=NOW,
                verified_by=owner.user.id,
            )
        )
        for key, state, reserve, charged in [
            ("confirmed", "complete", 5_000_000, 3_000_000),
            ("pending", "reserved", 2_000_000, None),
            ("unknown", "uncertain", 3_000_000, None),
            ("free", "complete", 1_000_000, 0),
        ]:
            session.add(
                AICall(
                    request_key=key,
                    model="test",
                    state=state,
                    reserved=reserve,
                    charged=charged,
                    created_at=NOW - timedelta(days=1),
                )
            )
    before = allowance_summary(db, NOW - timedelta(seconds=1))
    assert (before["confirmed"], before["reserved"], before["uncertain"]) == (
        3_000_000,
        5_000_000,
        3_000_000,
    )
    assert before["available"] == 2_000_000
    assert before["tone"] == "amber"
    expired = allowance_summary(db, NOW)
    assert expired["available"] == 0 and expired["expired"]
    assert expired["committed"] == 8_000_000
    assert expired["unallocated"] == 2_000_000
    with db.sessions() as session:
        assert session.get(BudgetAccount, "primary").committed == 8_000_000  # type: ignore[union-attr]
        assert session.scalar(select(func.count()).select_from(AICall)) == 4


def test_usage_calendars_zero_cost_and_campaign_attribution(db: Database) -> None:
    """DST-aware day totals and explicit attribution do not double-count search costs."""
    with db.sessions.begin() as session:
        campaign = Campaign(name="Synthetic campaign", industry="HVAC", area="Calgary", services=[])
        company = Company(
            identity_key="test", name="Synthetic Business", city="Calgary", industry="HVAC"
        )
        session.add_all([campaign, company])
        session.flush()
        call = AICall(
            request_key="search",
            model="model-a",
            category="enrichment",
            reserved=500_000,
            charged=120_000,
            state="complete",
            created_at=NOW,
            company_id=company.id,
        )
        session.add(call)
        session.flush()
        session.add(
            SearchRecord(
                request_key="search",
                cache_key="search",
                provider="openai_web_search",
                campaign_id=campaign.id,
                ai_call_id=call.id,
                reserved=500_000,
                created_at=NOW,
            )
        )
        session.add_all(
            [
                AICall(
                    request_key="before-day",
                    model="model-b",
                    reserved=400_000,
                    charged=20_000,
                    state="complete",
                    created_at=NOW.replace(hour=6),
                ),
                AICall(
                    request_key="unknown",
                    model="model-b",
                    reserved=300_000,
                    charged=None,
                    state="uncertain",
                    created_at=NOW,
                ),
                AICall(
                    request_key="last-month",
                    model="model-b",
                    reserved=200_000,
                    charged=200_000,
                    state="complete",
                    created_at=NOW.replace(month=2),
                ),
                SearchBucket(key="openai_web_search:day:2024-03-10", calls=1, committed=120_000),
            ]
        )
    report = usage_report(db, NOW)
    assert report["confirmed"] == 140_000
    assert report["today"] == 120_000
    assert report["reserved"] == 300_000
    assert report["searches"] == 1
    assert report["per_lead"] == 140_000 and report["per_draft"] is None
    assert {item["campaign"] for item in report["detail"]} == {"Synthetic campaign", "Unattributed"}
    assert sum(item["confirmed"] for item in report["detail"]) == report["confirmed"]
    assert report["days"][-2]["amount"] == 20_000
    assert report["projection"] == Decimal(140_000) * 31 / 10


def test_usage_page_owner_only_and_text_preference(
    client: TestClient,
    db: Database,
    settings: Settings,
    owner: Account,
    reviewer: Account,
) -> None:
    """A staff account cannot see financial data; owners persist the readable scale."""
    assert client.get("/settings/ai-usage").status_code == 303
    sign_in(client, reviewer)
    assert client.get("/settings/ai-usage").status_code == 403
    assert "ai-chip" not in client.get("/").text
    sign_in(client, owner)
    page = client.get("/settings/ai-usage")
    assert page.status_code == 200
    assert "No AI calls recorded this month" in page.text
    assert "Expiry does not reset credits" in page.text
    response = client.post(
        "/settings",
        data={
            "csrf_token": csrf_from(page.text),
            "version": "0",
            "workspace_name": "Synthetic",
            "display_timezone": "Asia/Karachi",
            "sending_timezone": "America/Edmonton",
            "preview_enabled": "false",
            "text_size": "large",
        },
    )
    assert response.status_code == 303
    assert get_preferences(db, settings)[0].text_size == "large"
    assert 'class="text-large"' in client.get("/").text


def test_money_rounding_and_empty_report(db: Database) -> None:
    """Amounts retain micro precision internally and use consistent cent presentation."""
    assert dollars(5_000) == "$0.01"
    assert dollars(0) == "$0.00"
    assert dollars(None) == "—"
    summary = allowance_summary(db, NOW)
    assert summary["available"] == summary["percent"] == 0
    report = usage_report(db, NOW)
    assert len(report["days"]) == 7
    assert report["per_lead"] is None and report["projection"] == 0
