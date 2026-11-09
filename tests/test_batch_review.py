"""Offline batch lifecycle, uncertainty and provider-result matching."""

import json
from datetime import datetime, timedelta
from unittest.mock import patch

import pytest
from sqlalchemy import select

from app.audit.batch import BatchPending, reserve_call, response
from app.audit.budget import BudgetBlocked
from app.core.clock import utc_now
from app.core.config import Settings
from app.db.models import Job
from app.db.research_models import AICall, BudgetAccount
from app.db.session import Database
from app.integrations.openai import ModelUnavailable, batch_result, submit_batch
from app.jobs.queue import enqueue
from app.jobs.runner import run_once
from tests.test_research import funded as funded


def test_batch_submitted_once_and_resumed(db: Database, funded: None) -> None:
    """Repeated polls reuse the persisted ID and the original cost reservation."""
    payload = {"model": "synthetic", "input": "Evidence"}
    call = reserve_call(db, "batch-test", "synthetic", 100, audit=True, batch=True)
    with patch("app.audit.batch.submit_batch", return_value="batch_test") as submit:
        with pytest.raises(BatchPending):
            response(db, call, "synthetic", payload)
        resumed = reserve_call(db, "batch-test", "synthetic", 100, audit=True, batch=True)
        with patch("app.audit.batch.batch_result", return_value=None), pytest.raises(BatchPending):
            response(db, resumed, "synthetic", payload)
        with patch("app.audit.batch.batch_result", return_value={"status": "completed"}):
            assert response(db, resumed, "synthetic", payload) == {"status": "completed"}
        assert submit.call_count == 1
        with pytest.raises(ModelUnavailable, match="inputs changed"):
            response(db, resumed, "synthetic", {"input": "Changed"})
    with db.sessions() as session:
        assert len(list(session.scalars(select(AICall)))) == 1
        budget = session.get(BudgetAccount, "primary")
        assert budget and budget.committed == 100


def test_uncertain_submission_cannot_resubmit(db: Database, funded: None) -> None:
    """Losing a create acknowledgement retains submission intent and blocks retries."""
    call = reserve_call(db, "lost-ack", "synthetic", 100, audit=False, batch=True)
    with (
        patch("app.audit.batch.submit_batch", side_effect=ModelUnavailable("Lost ack")),
        pytest.raises(ModelUnavailable),
    ):
        response(db, call, "synthetic", {"input": "Evidence"})
    with pytest.raises(BudgetBlocked):
        reserve_call(db, "lost-ack", "synthetic", 100, audit=False, batch=True)


def test_batch_polling_does_not_exhaust_job_retries(db: Database, settings: Settings) -> None:
    """Expected waiting releases the lease without consuming the transient-error retry budget."""
    now = utc_now()
    job = enqueue(db, "batch-job", "assess", {}, now)

    def pending(item: Job) -> str:
        """Represent a persisted remote request that has not completed yet."""
        raise BatchPending()

    for count in range(5):
        tick = now + timedelta(minutes=6 * count)

        def clock(tick: datetime = tick) -> datetime:
            """Freeze the poll time."""
            return tick

        assert run_once(db, settings, "batch-worker", clock=clock, handler=pending)
    with db.sessions() as session:
        saved = session.get(Job, job.id)
        assert saved and saved.state == "queued" and saved.attempts == 0


def test_batch_adapter_matches_identity_and_rejects_failed_rows() -> None:
    """One JSONL request is uploaded, and only its exact custom ID can supply output."""
    with patch(
        "app.integrations.openai._batch_request",
        side_effect=[
            b'{"id":"file_test"}',
            b'{"id":"batch_test"}',
        ],
    ) as http:
        assert submit_batch("synthetic", {"model": "synthetic"}, "request-1") == "batch_test"
        assert b'"custom_id": "request-1"' in http.call_args_list[0].args[2]
    row = {
        "custom_id": "request-1",
        "response": {"status_code": 200, "body": {"status": "completed"}},
    }
    status = b'{"status":"completed","output_file_id":"file_output"}'
    with patch(
        "app.integrations.openai._batch_request", side_effect=[status, json.dumps(row).encode()]
    ):
        assert batch_result("synthetic", "batch_test", "request-1") == {"status": "completed"}
    with (
        patch(
            "app.integrations.openai._batch_request", side_effect=[status, json.dumps(row).encode()]
        ),
        pytest.raises(ModelUnavailable),
    ):
        batch_result("synthetic", "batch_test", "wrong-id")
    with (
        patch("app.integrations.openai._batch_request", return_value=b'{"status":"expired"}'),
        pytest.raises(ModelUnavailable),
    ):
        batch_result("synthetic", "batch_test", "request-1")
