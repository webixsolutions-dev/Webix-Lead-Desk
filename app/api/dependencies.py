"""Authentication, role and CSRF checks enforced on the server."""

import hmac
import re
from dataclasses import dataclass
from typing import Annotated, cast

from fastapi import Depends, HTTPException, Request

from app.core.clock import utc_now
from app.core.context import Services
from app.core.security import SESSION_COOKIE, token_hash
from app.db.models import User, UserSession


@dataclass(frozen=True)
class Principal:
    """The active authenticated user and session CSRF token."""

    user: User
    csrf_token: str


def services(request: Request) -> Services:
    """Retrieve typed application services from FastAPI state."""
    return cast(Services, request.app.state.services)


def require_user(request: Request) -> Principal:
    """Reject absent, expired, revoked or inactive sessions."""
    app = services(request)
    token = request.cookies.get(SESSION_COOKIE, "")
    if token and len(token) <= 128:
        with app.db.sessions() as session:
            stored = session.get(UserSession, token_hash(token))
            if stored is not None and stored.expires_at > utc_now():
                user = session.get(User, stored.user_id)
                if user is not None and user.active:
                    if user.role != "owner" and not re.fullmatch(
                        r"/|/logout|/status/worker|/calls(?:/[^/]+/(?:outcome|notes))?"
                        r"|/linkedin/(?:plan|queue)|/linkedin/tasks/[^/]+(?:/(?:start|complete))?"
                        r"|/people/[^/]+/(?:reply|event)"
                        r"|/linkedin/results(?:/(?:profiles|posts)/[^/]+)?"
                        r"|/linkedin/posts(?:/[^/]+(?:/native)?)?"
                        r"|/linkedin/oauth/(?:finish|[^/]+/(?:start|decision))"
                        r"|/linkedin/media/(?:new|[^/]+/(?:[0-9]+|finish|remove|chunk/[0-9]+))",
                        request.url.path,
                    ):
                        raise HTTPException(403, "This page is available to the owner")
                    return Principal(user=user, csrf_token=stored.csrf_token)
    if request.headers.get("HX-Request") == "true":
        raise HTTPException(status_code=401, headers={"HX-Redirect": "/login"})
    raise HTTPException(status_code=303, headers={"Location": "/login"})


CurrentUser = Annotated[Principal, Depends(require_user)]


def require_owner(principal: CurrentUser) -> Principal:
    """Restrict approval and configuration actions to an owner."""
    if principal.user.role != "owner":
        raise HTTPException(status_code=403, detail="Only an owner can perform this action")
    return principal


Owner = Annotated[Principal, Depends(require_owner)]


def check_csrf(principal: Principal, supplied: str) -> None:
    """Require the unpredictable token belonging to the active session."""
    if not supplied or not hmac.compare_digest(principal.csrf_token, supplied):
        raise HTTPException(status_code=403, detail="Session check failed. Reload the page.")


async def read_form(
    request: Request,
    *,
    maximum: int | None = None,
    repeatable: frozenset[str] = frozenset(),
    max_fields: int = 12,
) -> dict[str, str]:
    """Read bounded URL-encoded forms only; Phase 1 accepts no uploaded files."""
    content_type = request.headers.get("content-type", "").split(";")[0]
    if content_type != "application/x-www-form-urlencoded":
        raise HTTPException(status_code=415, detail="Only form fields are accepted")
    maximum = maximum or services(request).settings.max_form_bytes
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > maximum:
            raise HTTPException(status_code=413, detail="Form is too large")
    from urllib.parse import parse_qsl

    try:
        pairs = parse_qsl(
            body.decode("utf-8"),
            keep_blank_values=True,
            max_num_fields=32 if repeatable else max_fields,
        )
    except (UnicodeDecodeError, ValueError) as error:
        raise HTTPException(status_code=400, detail="Invalid form") from error
    fields: dict[str, str] = {}
    for key, value in pairs:
        if key in fields:
            if key not in repeatable:
                raise HTTPException(
                    status_code=400, detail="Duplicate form fields are not accepted"
                )
            fields[key] += "," + value
        else:
            fields[key] = value
    return fields
