"""Owner security preferences and the unauthenticated second-factor sign-in screen."""

from urllib.parse import quote, urlencode

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import select
from starlette.responses import HTMLResponse, RedirectResponse

from app.api.dependencies import Owner, check_csrf, read_form, services
from app.api.views import dashboard_context, render
from app.core.auth import consume_login_attempt
from app.core.clock import utc_now
from app.core.security import SESSION_COOKIE, token_hash
from app.db.models import UserSession
from app.db.security_models import OwnerMFA
from app.security import management
from app.security.login import CHALLENGE_COOKIE, challenge, finish_login

router = APIRouter()


def security_page(
    request: Request,
    principal: Owner,
    *,
    error: str = "",
    secret: str = "",
    codes: list[str] | None = None,
    notice: str = "",
    status: int = 200,
) -> HTMLResponse:
    """Render only this owner's devices; setup secrets appear only in their POST response."""
    app = services(request)
    now = utc_now()
    with app.db.sessions() as session:
        factor = session.get(OwnerMFA, principal.user.id)
        enabled = factor is not None and factor.enabled
        devices = list(
            session.scalars(
                select(UserSession)
                .where(UserSession.user_id == principal.user.id, UserSession.expires_at > now)
                .order_by(UserSession.created_at.desc())
            )
        )
    uri = ""
    if secret:
        uri = "otpauth://totp/" + quote("Webix Lead Desk:" + principal.user.email, safe="")
        uri += "?" + urlencode(
            {
                "secret": secret,
                "issuer": "Webix Lead Desk",
                "algorithm": "SHA1",
                "digits": 6,
                "period": 30,
            }
        )
    context = dashboard_context(request, principal, "settings")
    context.update(
        enabled=enabled,
        devices=devices,
        error=error,
        notice=notice,
        setup_secret=secret,
        setup_uri=uri,
        recovery_codes=codes or [],
        current_hash=token_hash(request.cookies.get(SESSION_COOKIE, "")),
    )
    return render(request, "account_security.html", context, status)


@router.get("/settings/security", response_class=HTMLResponse)
def security(request: Request, principal: Owner) -> HTMLResponse:
    """Show optional owner-only authenticator and active devices."""
    return security_page(request, principal)


@router.post("/settings/security/{action}", response_class=HTMLResponse)
async def change_security(request: Request, principal: Owner, action: str) -> HTMLResponse:
    """Check role, CSRF, rate, current password and factor before a security mutation."""
    app = services(request)
    fields = await read_form(request)
    check_csrf(principal, fields.get("csrf_token", ""))
    if action not in {"start", "confirm", "disable", "recovery", "signout"}:
        raise HTTPException(404, "Security action not found")
    now = utc_now()
    source = request.client.host if request.client else "unknown"
    if not consume_login_attempt(
        app.db, app.settings, "security:" + principal.user.id, source, now
    ):
        response = security_page(
            request, principal, error="Too many checks. Try again later.", status=429
        )
        response.headers["Retry-After"] = str(app.settings.login_window_seconds)
        return response
    token = request.cookies.get(SESSION_COOKIE, "")
    password, code = fields.get("password", ""), fields.get("code", "").strip()
    secret, codes = "", []
    try:
        with app.db.sessions.begin() as session:
            if action == "start":
                secret = management.start_enrollment(
                    session, app.settings, principal.user.id, token, password, now
                )
            elif action == "confirm":
                codes = management.confirm_enrollment(
                    session, app.settings, principal.user.id, token, password, code, now
                )
            else:
                if fields.get("confirmed") != "true":
                    raise ValueError("Confirm the selected security action first.")
                codes = management.manage(
                    session, app.settings, principal.user.id, token, password, code, action, now
                )
    except ValueError as error:
        return security_page(request, principal, error=str(error), status=400)
    notices = {
        "start": "Add the key to your authenticator, then confirm its code below.",
        "confirm": "Authenticator enabled. Other devices have been signed out.",
        "disable": "Authenticator disabled. Other devices have been signed out.",
        "recovery": "Recovery codes replaced. Other devices have been signed out.",
        "signout": "Other devices and unfinished sign-ins have been signed out.",
    }
    return security_page(request, principal, secret=secret, codes=codes, notice=notices[action])


def verification_page(request: Request, error: str = "", status: int = 200) -> HTMLResponse:
    """Render a five-minute cookie-bound factor form without account information."""
    app = services(request)
    with app.db.sessions() as session:
        try:
            item = challenge(session, request.cookies.get(CHALLENGE_COOKIE, ""), utc_now())
        except ValueError as exc:
            raise HTTPException(303, headers={"Location": "/login"}) from exc
    return render(request, "verify_login.html", {"csrf": item.csrf_token, "error": error}, status)


@router.get("/login/verify", response_class=HTMLResponse)
def verify_page(request: Request) -> HTMLResponse:
    """Prompt only after successful password verification; no full session exists yet."""
    return verification_page(request)


@router.post("/login/verify", response_model=None)
async def verify_login(request: Request) -> HTMLResponse | RedirectResponse:
    """Authenticate once with a code, then rotate to a new full session."""
    app = services(request)
    fields = await read_form(request)
    token = request.cookies.get(CHALLENGE_COOKIE, "")
    now = utc_now()
    with app.db.sessions() as session:
        try:
            item = challenge(session, token, now)
        except ValueError as exc:
            raise HTTPException(303, headers={"Location": "/login"}) from exc
        import hmac

        if not hmac.compare_digest(item.csrf_token, fields.get("csrf_token", "")):
            raise HTTPException(403, "Sign-in check failed. Start again.")
        user_id = item.user_id
    source = request.client.host if request.client else "unknown"
    if not consume_login_attempt(app.db, app.settings, "mfa:" + user_id, source, now):
        response = verification_page(request, "Too many checks. Try again later.", 429)
        response.headers["Retry-After"] = str(app.settings.login_window_seconds)
        return response
    try:
        with app.db.sessions.begin() as session:
            authenticated = finish_login(
                session,
                app.settings,
                token,
                fields.get("csrf_token", ""),
                fields.get("code", "").strip(),
                now,
                request.headers.get("user-agent", ""),
            )
    except ValueError:
        return verification_page(
            request, "Code was not accepted. Use an unused code or start again.", 401
        )
    redirect = RedirectResponse("/", status_code=303)
    redirect.set_cookie(
        SESSION_COOKIE,
        authenticated,
        max_age=app.settings.session_hours * 3600,
        httponly=True,
        secure=app.settings.cookie_secure,
        samesite="strict",
    )
    redirect.delete_cookie(CHALLENGE_COOKIE)
    return redirect
