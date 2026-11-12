"""Bounded evidence across audit areas, with missing coverage explicit."""

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.research_models import Evidence

AREAS = {
    "website": {"page", "website", "mobile"},
    "maps_reviews": {"maps", "reviews", "google_profile"},
    "social": {"social", "social_post"},
    "ads": {"ads", "ad_library"},
    "booking": {"booking", "workflow"},
    "operations": {"operations", "tools"},
}


def evidence_bundle(session: Session, company_id: str) -> list[Evidence]:
    """Keep recent observations per kind before filling remaining context slots."""
    rows = list(
        session.scalars(
            select(Evidence)
            .where(Evidence.company_id == company_id)
            .order_by(Evidence.observed_at.desc())
            .limit(120)
        )
    )
    selected: list[Evidence] = []
    seen: set[str] = set()
    for row in rows:
        if row.kind not in seen:
            selected.append(row)
            seen.add(row.kind)
    for row in rows:
        if len(selected) >= 24:
            break
        if row not in selected:
            selected.append(row)
    return selected[:24]


def coverage(rows: list[Evidence]) -> list[dict[str, Any]]:
    """A stored page does not claim a visual, social, paid-ad or operational audit."""
    coverage_rows = [
        {
            "area": area.replace("_", " "),
            "status": "Evidence recorded; review scope"
            if any(row.kind in kinds for row in rows)
            else "Not checked / needs evidence",
        }
        for area, kinds in AREAS.items()
    ]
    coverage_rows.extend(
        {"area": label, "status": "Evidence recorded; review scope" if checked else "Not checked"}
        for label, checked in (
            ("technical SEO", any(row.kind == "page" and row.facts.get("seo") for row in rows)),
            (
                "mobile lab performance",
                any(
                    row.kind == "performance" and row.facts.get("status") == "measured"
                    for row in rows
                ),
            ),
            ("visual design", any(row.kind == "visual" and row.reviewed_by for row in rows)),
        )
    )
    return coverage_rows
