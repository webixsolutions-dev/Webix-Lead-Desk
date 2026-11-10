"""Focused industry priors; evidence takes precedence and no match is allowed."""

import json
import re
from pathlib import Path
from typing import Any, cast

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.outreach_models import PlaybookVersion


def catalog() -> list[dict[str, Any]]:
    """Load generic industry seeds without account data or credentials."""
    return cast(
        list[dict[str, Any]], json.loads(Path(__file__).with_name("industries.json").read_text())
    )


def matching(industry: str) -> list[dict[str, Any]]:
    """Use specific whole-phrase aliases; ambiguous multi-industry campaigns get no prior."""
    matches: list[tuple[int, int, dict[str, Any]]] = []
    for item in catalog():
        for alias in item["aliases"]:
            for match in re.finditer(r"(?<!\w)" + re.escape(alias) + r"(?!\w)", industry, re.I):
                matches.append((match.start(), match.end(), item))
    # A more specific overlapping phrase wins (vet clinic over clinic); two distinct
    # industries must not silently select whichever alias happens to have more letters.
    specific = {
        item["slug"]: item
        for start, end, item in matches
        if not any(a <= start and end <= b and (a, b) != (start, end) for a, b, _ in matches)
    }
    return list(specific.values()) if len(specific) == 1 else []


def context(session: Session, industry: str) -> list[dict[str, str]]:
    """Include only the matching industry's current owner version, otherwise its seed."""
    result = []
    for item in matching(industry):
        saved = session.scalar(
            select(PlaybookVersion)
            .where(PlaybookVersion.scope == "industry:" + item["slug"])
            .order_by(PlaybookVersion.version.desc())
            .limit(1)
        )
        result.append(
            {
                "title": item["title"],
                "guidance": saved.guidance if saved else item["guidance"],
                "status": "owner-published" if saved else "reference seed",
            }
        )
    return result
