"""Business-first owner briefing with all collected evidence and exact message routing."""

from typing import Any

from sqlalchemy import func, select

from app.audit.coverage import coverage
from app.db.lead_models import Campaign, Company, SourceRecord
from app.db.outreach_models import Conversation, Draft
from app.db.research_models import Assessment, Evidence
from app.db.session import Database
from app.drafting.context import email_context


def review_brief(db: Database, company_id: str, campaign_id: str | None = None) -> dict[str, Any]:
    """Read the business's facts without asserting unknown research was performed."""
    with db.sessions() as session:
        company = session.get(Company, company_id)
        if not company:
            return {}
        conversation = session.scalar(
            select(Conversation).where(Conversation.company_id == company_id)
        )
        campaign = session.get(
            Campaign, campaign_id or (conversation.campaign_id if conversation else "")
        )
        evidence = list(
            session.scalars(
                select(Evidence)
                .where(Evidence.company_id == company_id)
                .order_by(Evidence.observed_at.desc())
                .limit(150)
            )
        )
        query = select(Assessment).where(Assessment.company_id == company_id)
        if campaign:
            query = query.where(Assessment.campaign_id == campaign.id)
        assessment = session.scalar(query.order_by(Assessment.created_at.desc()).limit(1))
        return {
            "company": company,
            "campaign": campaign,
            "sources": list(
                session.scalars(
                    select(SourceRecord).where(SourceRecord.company_id == company_id).limit(50)
                )
            ),
            "evidence": evidence,
            "coverage": coverage(evidence),
            "assessment": assessment,
            "conversation": email_context(
                session, company_id, conversation.contact_id, conversation.mailbox_id
            )
            if conversation
            else {},
        }


def briefs_for_drafts(db: Database, drafts: list[Draft]) -> dict[str, dict[str, Any]]:
    """Batch company facts for a review page instead of reconnecting for every card."""
    if not drafts:
        return {}
    ids = {draft.snapshot["company_id"] for draft in drafts}
    with db.sessions() as session:
        companies = {
            item.id: item for item in session.scalars(select(Company).where(Company.id.in_(ids)))
        }
        campaign_ids = {draft.snapshot["campaign_id"] for draft in drafts}
        campaigns = {
            item.id: item
            for item in session.scalars(select(Campaign).where(Campaign.id.in_(campaign_ids)))
        }
        ranked = (
            select(
                Evidence.id,
                func.row_number()
                .over(partition_by=Evidence.company_id, order_by=Evidence.observed_at.desc())
                .label("rank"),
            )
            .where(Evidence.company_id.in_(ids))
            .subquery()
        )
        facts: dict[str, list[Evidence]] = {key: [] for key in ids}
        for fact in session.scalars(
            select(Evidence)
            .join(ranked, Evidence.id == ranked.c.id)
            .where(ranked.c.rank <= 150)
            .order_by(Evidence.observed_at.desc())
        ):
            facts[fact.company_id].append(fact)
        sources: dict[str, list[SourceRecord]] = {key: [] for key in ids}
        for source in session.scalars(
            select(SourceRecord)
            .where(SourceRecord.company_id.in_(ids))
            .order_by(SourceRecord.observed_at.desc())
            .limit(5000)
        ):
            if len(sources[source.company_id]) < 50:
                sources[source.company_id].append(source)
        ranked_assessments = (
            select(
                Assessment.id,
                func.row_number()
                .over(
                    partition_by=[Assessment.company_id, Assessment.campaign_id],
                    order_by=Assessment.created_at.desc(),
                )
                .label("rank"),
            )
            .where(Assessment.company_id.in_(ids))
            .subquery()
        )
        assessments = {
            (item.company_id, item.campaign_id): item
            for item in session.scalars(
                select(Assessment)
                .join(ranked_assessments, Assessment.id == ranked_assessments.c.id)
                .where(ranked_assessments.c.rank == 1)
            )
        }
        return {
            draft.id: {
                "company": companies.get(draft.snapshot["company_id"]),
                "campaign": campaigns.get(draft.snapshot["campaign_id"]),
                "evidence": facts[draft.snapshot["company_id"]],
                "sources": sources[draft.snapshot["company_id"]],
                "assessment": assessments.get(
                    (draft.snapshot["company_id"], draft.snapshot["campaign_id"])
                ),
                "coverage": coverage(facts[draft.snapshot["company_id"]]),
                "conversation": email_context(
                    session,
                    draft.snapshot["company_id"],
                    draft.snapshot["contact_id"],
                    draft.snapshot["mailbox_id"],
                )
                if draft.kind == "reply"
                else {},
            }
            for draft in drafts
        }
