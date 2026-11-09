"""Durable optional Batch API polling, without resubmitting uncertain creates."""

import hashlib
import json
from datetime import timedelta
from typing import Any

from sqlalchemy import select, update

from app.audit.budget import BudgetBlocked, reserve
from app.core.clock import utc_now
from app.db.research_models import AICall
from app.db.session import Database
from app.integrations.openai import ModelUnavailable, batch_result, submit_batch


class BatchPending(Exception):
    """A persisted provider batch is waiting; retry polling without repeating submission."""


def reserve_call(
    db: Database,
    key: str,
    model: str,
    maximum: int,
    *,
    audit: bool,
    batch: bool,
    category: str = "reserve",
    company_id: str | None = None,
) -> AICall:
    """Resume only an acknowledged, still-reserved batch for the same request key."""
    if batch:
        with db.sessions() as session:
            existing = session.scalar(select(AICall).where(AICall.request_key == key))
            if existing:
                if existing.state == "reserved" and existing.batch and existing.batch.get("id"):
                    return existing
                raise BudgetBlocked("Existing batch needs review; no resubmission allowed")
    return reserve(
        db, key, model, maximum, utc_now(), audit=audit, category=category, company_id=company_id
    )


def response(db: Database, call: AICall, api_key: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Persist submission intent before network I/O and poll a confirmed batch at most 25h."""
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    if call.batch:
        if call.batch.get("digest") != digest:
            raise ModelUnavailable("Batch inputs changed; review the reserved request")
        if utc_now() > call.created_at + timedelta(hours=25):
            raise ModelUnavailable("Batch polling deadline exceeded; review provider status")
        output = batch_result(api_key, call.batch["id"], call.id)
        if output is None:
            raise BatchPending()
        return output
    with db.sessions.begin() as session:
        claimed = session.scalar(
            update(AICall)
            .where(AICall.id == call.id, AICall.state == "reserved", AICall.batch.is_(None))
            .values(batch={"digest": digest, "state": "submitting"})
            .returning(AICall.id)
        )
        if not claimed:
            raise ModelUnavailable("Batch submission already started")
    batch_id = submit_batch(api_key, payload, call.id)
    with db.sessions.begin() as session:
        current = session.get(AICall, call.id)
        assert current
        current.batch = {"digest": digest, "id": batch_id, "state": "submitted"}
    raise BatchPending()
