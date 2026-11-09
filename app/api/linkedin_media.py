"""Private chunked uploads and downloads compatible with bounded hosted requests."""

import base64

from fastapi import APIRouter, HTTPException, Request
from starlette.responses import JSONResponse, Response

from app.api.dependencies import CurrentUser, Owner, check_csrf, read_form, services
from app.db.linkedin_post_models import PostMediaChunk
from app.linkedin import media

router = APIRouter()


@router.post("/linkedin/media/new")
async def upload_begin(request: Request, principal: CurrentUser) -> JSONResponse:
    """Reserve a bounded durable upload; ownership is checked in the service."""
    fields = await read_form(request)
    check_csrf(principal, fields.get("csrf_token", ""))
    try:
        item = media.begin(
            services(request).db,
            services(request).settings,
            fields.get("profile_id", ""),
            principal.user.id,
            fields.get("kind", ""),
            int(fields.get("size", "0")),
            fields.get("digest", ""),
        )
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    return JSONResponse({"id": item.id, "chunk_bytes": media.CHUNK})


@router.put("/linkedin/media/{media_id}/{number}")
async def upload_chunk(
    request: Request, principal: CurrentUser, media_id: str, number: int
) -> JSONResponse:
    """Stream a single bounded chunk without trusting Content-Length."""
    check_csrf(principal, request.headers.get("X-CSRF-Token", ""))
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > media.CHUNK:
            raise HTTPException(413, "Chunk too large")
    try:
        media.chunk(services(request).db, media_id, principal.user.id, number, bytes(body))
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    return JSONResponse({"ok": True})


@router.post("/linkedin/media/{media_id}/finish")
async def finish_upload(request: Request, principal: CurrentUser, media_id: str) -> JSONResponse:
    """Verify bytes and container before exposing an asset for approval."""
    fields = await read_form(request)
    check_csrf(principal, fields.get("csrf_token", ""))
    try:
        media.finish(services(request).db, media_id, principal.user.id)
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    return JSONResponse({"ok": True})


@router.post("/linkedin/media/{media_id}/remove")
async def remove_upload(request: Request, principal: CurrentUser, media_id: str) -> JSONResponse:
    """Free unused media with explicit ownership and reference checks."""
    fields = await read_form(request)
    check_csrf(principal, fields.get("csrf_token", ""))
    try:
        media.remove(services(request).db, media_id, principal.user.id)
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    return JSONResponse({"ok": True})


@router.get("/linkedin/media/{media_id}/chunk/{number}")
def download_chunk(
    request: Request, principal: CurrentUser, media_id: str, number: int
) -> Response:
    """Return private attachment bytes in a bounded response; never render uploaded HTML."""
    with services(request).db.sessions() as session:
        try:
            item = media.accessible(session, media_id, principal.user.id)
        except ValueError as error:
            raise HTTPException(404, "Media unavailable") from error
        chunk = session.get(PostMediaChunk, (media_id, number))
        if not chunk or item.state != "ready":
            raise HTTPException(404, "Media unavailable")
        return Response(
            base64.b64decode(chunk.data),
            media_type="application/octet-stream",
            headers={"Content-Disposition": 'attachment; filename="media.part"'},
        )


@router.post("/linkedin/media/{media_id}/archive")
async def archive_upload(request: Request, principal: Owner, media_id: str) -> JSONResponse:
    """Retain the audit fingerprint while explicitly reclaiming old published media storage."""
    fields = await read_form(request)
    check_csrf(principal, fields.get("csrf_token", ""))
    try:
        media.archive(services(request).db, media_id, principal.user.id)
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    return JSONResponse({"ok": True})
