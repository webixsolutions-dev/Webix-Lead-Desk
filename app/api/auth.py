"""Login and logout routes with CSRF and server-side session checks."""

from fastapi import APIRouter, HTTPException, Request
from starlette.responses import HTMLResponse, RedirectResponse

from app.api.dependencies import CurrentUser, check_csrf, read_form, services
from app.api.views import render
from app.core.auth import authenticate, consume_login_attempt, revoke_session, start_session
from app.core.clock import utc_now
from app.core.security import (
    LOGIN_CSRF_COOKIE,
    SESSION_COOKIE,
    make_login_csrf,
    valid_login_csrf,
)

router = APIRouter()


def login_page(request: Request, error: str | None = None, status: int = 200) -> HTMLResponse:
    """Render a fresh login token, never reflect a password into HTML."""
    settings = services(request).settings
    csrf = make_login_csrf(settings)
    response = render(request, "login.html", {"csrf": csrf, "error": error}, status)
    response.set_cookie(
        LOGIN_CSRF_COOKIE,
        csrf,
        max_age=settings.login_csrf_seconds,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="strict",
    )
    return response


@router.get("/login", response_class=HTMLResponse)
def login(request: Request) -> HTMLResponse:
    """Show the private sign-in screen without public registration."""
    return login_page(request)


@router.post("/login", response_class=HTMLResponse, response_model=None)
async def submit_login(request: Request) -> HTMLResponse | RedirectResponse:
    """Throttle sign-in attempts and issue a fresh session on successful authentication."""
    app = services(request)
    fields = await read_form(request)
    if not valid_login_csrf(
        app.settings, request.cookies.get(LOGIN_CSRF_COOKIE, ""), fields.get("csrf_token", "")
    ):
        raise HTTPException(status_code=403, detail="Sign-in check expired. Reload the page.")
    email = fields.get("email", "").strip().casefold()
    password = fields.get("password", "")
    if len(email) > 254 or len(password) > 256:
        return login_page(request, "Email or password is incorrect.", 400)
    source = request.client.host if request.client else "unknown"
    now = utc_now()
    if not consume_login_attempt(app.db, app.settings, email, source, now):
        response = login_page(request, "Too many sign-in attempts. Please try again later.", 429)
        response.headers["Retry-After"] = str(app.settings.login_window_seconds)
        return response
    user = authenticate(app.db, email, password)
    if user is None:
        return login_page(request, "Email or password is incorrect.", 401)
    old_token = request.cookies.get(SESSION_COOKIE)
    if old_token:
        revoke_session(app.db, old_token, user.id)
    token = start_session(app.db, app.settings, user, now)
    redirect = RedirectResponse("/", status_code=303)
    redirect.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=app.settings.session_hours * 3600,
        httponly=True,
        secure=app.settings.cookie_secure,
        samesite="strict",
    )
    redirect.delete_cookie(LOGIN_CSRF_COOKIE)
    return redirect


@router.post("/logout")
async def logout(request: Request, principal: CurrentUser) -> RedirectResponse:
    """Revoke the server session after validating a POST CSRF token."""
    fields = await read_form(request)
    check_csrf(principal, fields.get("csrf_token", ""))
    revoke_session(services(request).db, request.cookies.get(SESSION_COOKIE, ""), principal.user.id)
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie(SESSION_COOKIE)
    return response
