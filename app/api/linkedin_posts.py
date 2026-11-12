"""Scoped public-post calendar, owner review and native fallback forms."""

from datetime import date, datetime, timedelta

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import select
from starlette.responses import HTMLResponse, RedirectResponse

from app.api.dependencies import CurrentUser, Owner, check_csrf, read_form, services
from app.api.views import dashboard_context, render
from app.core.clock import utc_now
from app.core.security import SESSION_COOKIE
from app.db.linkedin_models import LinkedInProfile
from app.db.linkedin_post_models import LinkedInPost, PostingConnection, PostMedia
from app.db.models import WorkspaceSetting
from app.integrations.linkedin import LinkedInError
from app.linkedin import oauth, posts
from app.linkedin.post_access import profile_access
from app.linkedin.timing import daytime, local_day
from app.sending.window import CALGARY

router = APIRouter()


@router.get("/linkedin/posts", response_class=HTMLResponse)
def calendar_page(request: Request, principal: CurrentUser) -> HTMLResponse:
    """Show each member only their own calendar and OAuth status; owner sees all profiles."""
    context = dashboard_context(
        request, principal, "outbox" if principal.user.role == "owner" else "linkedin"
    )
    with services(request).db.sessions() as session:
        draft_bank = (
            session.get(WorkspaceSetting, "linkedin_post_copy_drafts")
            if principal.user.role == "owner"
            else None
        )
        query = select(LinkedInProfile).where(LinkedInProfile.status == "active")
        if principal.user.role != "owner":
            query = query.where(LinkedInProfile.user_id == principal.user.id)
        profiles = list(session.scalars(query.order_by(LinkedInProfile.created_at)))
        rows = list(
            session.scalars(
                select(LinkedInPost)
                .where(LinkedInPost.profile_id.in_([p.id for p in profiles]))
                .order_by(LinkedInPost.due_at.desc())
                .limit(150)
            )
        )
        context.update(
            profiles=profiles,
            posts=rows,
            names={p.id: p.display_name for p in profiles},
            connections={p.id: session.get(PostingConnection, p.id) for p in profiles},
            warning_at=utc_now() + timedelta(days=7),
            oauth_enabled=services(request).settings.linkedin_posting_enabled,
            calgary=CALGARY,
            unassigned_posts=draft_bank.value.get("drafts", []) if draft_bank else [],
        )
    return render(request, "linkedin_posts.html", context)


@router.post("/linkedin/posts/calendar")
async def build_calendar(request: Request, principal: Owner) -> RedirectResponse:
    """Create a two-post weekly baseline; repeated requests preserve capacity."""
    fields = await read_form(request)
    check_csrf(principal, fields.get("csrf_token", ""))
    try:
        posts.calendar(
            services(request).db, principal.user.id, date.fromisoformat(fields.get("monday", ""))
        )
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    return RedirectResponse("/linkedin/posts", 303)


@router.post("/linkedin/posts/new")
async def new_post(request: Request, principal: Owner) -> RedirectResponse:
    """Schedule a single exact Calgary slot without publishing it."""
    fields = await read_form(request)
    check_csrf(principal, fields.get("csrf_token", ""))
    try:
        due = datetime.fromisoformat(fields.get("due", "")).replace(tzinfo=CALGARY)
        item = posts.create_slot(
            services(request).db,
            fields.get("profile_id", ""),
            principal.user.id,
            fields.get("topic", ""),
            fields.get("kind", ""),
            due,
        )
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    return RedirectResponse("/linkedin/posts/" + item.id, 303)


@router.get("/linkedin/posts/{post_id}", response_class=HTMLResponse)
def post_page(request: Request, principal: CurrentUser, post_id: str) -> HTMLResponse:
    """Review immutable copy or show a member's explicitly claimed native-publishing card."""
    context = dashboard_context(
        request, principal, "outbox" if principal.user.role == "owner" else "linkedin"
    )
    with services(request).db.sessions() as session:
        post = session.get(LinkedInPost, post_id)
        try:
            if not post:
                raise ValueError("Post not found")
            profile = profile_access(session, post.profile_id, principal.user.id)
        except ValueError as error:
            raise HTTPException(404, "Post not found") from error
        context.update(
            post=post,
            profile=profile,
            revision=posts.revision(session, post),
            exact_valid=bool(posts.valid(session, post)),
            is_member=profile.user_id == principal.user.id,
            can_start=post.state in {"manual", "approved"}
            and bool(posts.valid(session, post))
            and daytime(utc_now())
            and post.due_at <= utc_now()
            and local_day(post.due_at) == local_day(utc_now()),
            prompt=posts.static_prompt(profile, post),
            calgary=CALGARY,
            media=list(
                session.scalars(
                    select(PostMedia).where(
                        PostMedia.profile_id == profile.id, PostMedia.state == "ready"
                    )
                )
            ),
        )
    if post.kind != "video":
        from app.playbook.service import current_guidance

        guidance = current_guidance(services(request).db)
        if guidance:
            context["prompt"] += "\nOwner-approved Playbook guidance:\n" + "\n".join(
                item.guidance for item in guidance
            )
    return render(request, "linkedin_post.html", context)


@router.post("/linkedin/posts/{post_id}/save")
async def save_post(request: Request, principal: Owner, post_id: str) -> RedirectResponse:
    """Edit by creating a new revision requiring a new exact approval."""
    fields = await read_form(request)
    check_csrf(principal, fields.get("csrf_token", ""))
    try:
        posts.save(
            services(request).db,
            post_id,
            principal.user.id,
            int(fields.get("previous", "-1")),
            fields.get("body", ""),
            fields.get("media_id", ""),
        )
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    return RedirectResponse("/linkedin/posts/" + post_id, 303)


@router.post("/linkedin/posts/{post_id}/approve")
async def approve_post(request: Request, principal: Owner, post_id: str) -> RedirectResponse:
    """Approve the exact visible snapshot, never the last draft implicitly."""
    fields = await read_form(request)
    check_csrf(principal, fields.get("csrf_token", ""))
    try:
        posts.approve(services(request).db, post_id, principal.user.id, fields.get("digest", ""))
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    return RedirectResponse("/linkedin/posts/" + post_id, 303)


@router.post("/linkedin/posts/{post_id}/native")
async def native_post(request: Request, principal: CurrentUser, post_id: str) -> RedirectResponse:
    """Only the real member starts native work or confirms a checked publication outcome."""
    fields = await read_form(request)
    check_csrf(principal, fields.get("csrf_token", ""))
    try:
        posts.native_action(
            services(request).db,
            post_id,
            principal.user.id,
            fields.get("decision", ""),
            fields.get("url", ""),
        )
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    return RedirectResponse("/linkedin/posts/" + post_id, 303)


@router.post("/linkedin/oauth/{profile_id}/start")
async def oauth_start(request: Request, principal: CurrentUser, profile_id: str) -> HTMLResponse:
    """Start only the signed-in member's OAuth authorization."""
    fields = await read_form(request)
    check_csrf(principal, fields.get("csrf_token", ""))
    try:
        url = oauth.start(
            services(request).db,
            services(request).settings,
            profile_id,
            principal.user.id,
            request.cookies.get(SESSION_COOKIE, ""),
        )
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    return render(request, "linkedin_oauth_start.html", {"authorize_url": url})


@router.get("/linkedin/oauth/callback", response_class=HTMLResponse)
def oauth_callback(request: Request, state: str = "", code: str = "") -> HTMLResponse:
    """Land without a session cookie; Strict cookies return on the same-origin form submit."""
    if not 1 <= len(state) <= 128 or not 1 <= len(code) <= 4096:
        raise HTTPException(422, "Authorization was declined or incomplete; start again")
    return render(request, "linkedin_oauth_return.html", {"state": state, "code": code})


@router.post("/linkedin/oauth/finish")
async def oauth_finish(request: Request, principal: CurrentUser) -> RedirectResponse:
    """The one-use session-bound OAuth state is this callback's CSRF protection."""
    fields = await read_form(request)
    try:
        oauth.finish(
            services(request).db,
            services(request).settings,
            fields.get("state", ""),
            fields.get("code", ""),
            principal.user.id,
            request.cookies.get(SESSION_COOKIE, ""),
        )
    except (ValueError, LinkedInError):
        return RedirectResponse("/linkedin/posts?connection=failed", 303)
    return RedirectResponse("/linkedin/posts?connection=confirm", 303)


@router.post("/linkedin/oauth/{profile_id}/decision")
async def oauth_decision(
    request: Request, principal: CurrentUser, profile_id: str
) -> RedirectResponse:
    """Confirm the displayed authenticated identity or remove its encrypted token."""
    fields = await read_form(request)
    check_csrf(principal, fields.get("csrf_token", ""))
    try:
        oauth.connection_action(
            services(request).db, profile_id, principal.user.id, fields.get("decision", "")
        )
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    return RedirectResponse("/linkedin/posts", 303)
