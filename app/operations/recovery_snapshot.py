"""Conservatively fence work in an online backup copy, without changing the live workspace."""

import hashlib
from typing import Any

from app.core.clock import utc_now
from app.operations.portable import canonical


def fence_snapshot(payload: dict[str, Any]) -> None:
    """Require reconciliation after restore, including work performed after this snapshot."""
    tables = payload["tables"]
    now = utc_now().isoformat()
    tables["runtime_control"] = [
        {
            "key": "primary",
            "maintenance": True,
            "generation": 1,
            "note": "Online backup restored: reconcile all outbound work before resuming",
            "updated_at": now,
        }
    ]
    for worker in tables["worker_instances"]:
        worker["lease_expires_at"] = now
    for job in tables["jobs"]:
        if job["state"] in {"queued", "running"}:
            job.update(
                state="failed",
                error_code="backup_restore_review",
                finished_at=now,
                lease_expires_at=None,
                claim_owner=None,
                claim_token=None,
            )
    for attempt in tables["job_attempts"]:
        if attempt["outcome"] == "running":
            attempt.update(outcome="interrupted", finished_at=now)
    for intent in tables["send_intents"]:
        if intent["state"] not in {"accepted", "cancelled", "suppressed"}:
            intent.update(state="uncertain", hold_reason="backup_restore_review")
    for draft in tables["drafts"]:
        if draft["state"] in {"approved", "scheduled", "transmitting"}:
            draft["state"] = "uncertain"
    for conversation in tables["conversations"]:
        if conversation["state"] in {"draft", "active"}:
            conversation["state"] = "held"
    for attempt in tables["send_attempts"]:
        if attempt["outcome"] == "started":
            attempt.update(outcome="uncertain", error_code="backup_restore_review", finished_at=now)
    for post in tables["linkedin_posts"]:
        if post["state"] != "published":
            post.update(
                state="needs_check", reason="Online backup restored; check actual LinkedIn state"
            )
    for task in tables["linkedin_tasks"]:
        if task["state"] != "done":
            task["state"] = "needs_check"
    for sequence in tables["linkedin_sequences"]:
        sequence["state"] = "closed"
    payload["counts"] = {name: len(rows) for name, rows in tables.items()}
    payload["data_sha256"] = hashlib.sha256(canonical(tables)).hexdigest()
