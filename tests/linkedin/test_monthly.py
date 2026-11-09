"""Monthly category and total caps supplement the verified funding ledger."""

from datetime import timedelta

import pytest
from sqlalchemy import select

from app.audit.budget import BudgetBlocked, reserve, settle
from app.audit.monthly import CAPS, TOTAL, bounds, usage
from app.db.research_models import AICall, BudgetAccount
from app.db.session import Database
from tests.conftest import Account
from tests.linkedin.test_planner import NOW


def fund(db: Database, owner: Account) -> None:
    """Create a synthetic verified allowance, never configure a provider."""
    with db.sessions.begin() as session:
        session.add(
            BudgetAccount(
                key="primary",
                allowance=500_000_000,
                committed=0,
                expires_at=NOW + timedelta(days=400),
                verified_by=owner.user.id,
            )
        )


def test_unknown_usage_holds_category_and_known_unused_cost_is_released(
    db: Database, owner: Account
) -> None:
    """An uncertain provider response cannot open another paid slot."""
    fund(db, owner)
    call = reserve(db, "a", "example", CAPS["linkedin"], NOW, category="linkedin")
    with pytest.raises(BudgetBlocked, match="Monthly"):
        reserve(db, "b", "example", 1, NOW, category="linkedin")
    settle(db, call.id, 1_000_000)
    reserve(db, "c", "example", CAPS["linkedin"] - 1_000_000, NOW, category="linkedin")
    with db.sessions() as session:
        assert usage(session, NOW)["linkedin"] == CAPS["linkedin"]
        account = session.get(BudgetAccount, "primary")
        assert account and account.committed == CAPS["linkedin"]
        assert len(list(session.scalars(select(AICall)))) == 2


def test_total_ceiling_and_month_reset_do_not_carry_unspent_allowance(
    db: Database, owner: Account
) -> None:
    """Independent caps never allow more than $170 together in a Calgary month."""
    fund(db, owner)
    spent = 0
    for category, cap in CAPS.items():
        amount = min(cap, TOTAL - spent)
        if amount:
            reserve(db, category, "example", amount, NOW, category=category)
            spent += amount
    with pytest.raises(BudgetBlocked, match="Monthly"):
        reserve(db, "over-total", "example", 1, NOW, category="reserve")
    _, next_month = bounds(NOW)
    reserve(db, "next-month", "example", CAPS["linkedin"], next_month, category="linkedin")
    with pytest.raises(BudgetBlocked):
        reserve(db, "no-carry", "example", 1, next_month, category="linkedin")
