"""Bounded private media uploads with integrity and simple container validation."""

import base64
import hashlib
import struct
from datetime import timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.core.clock import utc_now
from app.core.config import Settings
from app.db.linkedin_post_models import PostMedia, PostMediaChunk, PostRevision
from app.db.session import Database
from app.linkedin.post_access import profile_access
from app.linkedin.quotas import lock_registry

CHUNK = 1_000_000


def begin(
    db: Database, settings: Settings, profile_id: str, actor: str, kind: str, size: int, digest: str
) -> PostMedia:
    """Reserve workspace capacity before accepting any chunk, including incomplete uploads."""
    import re

    if (
        kind not in {"image", "video"}
        or not 1 <= size <= settings.linkedin_media_max_bytes
        or (not re.fullmatch(r"[a-f0-9]{64}", digest))
    ):
        raise ValueError("Use an image or MP4 up to the configured 25 MB maximum")
    with db.sessions.begin() as session:
        profile_access(session, profile_id, actor)
        lock_registry(session)
        used = (
            session.scalar(select(func.sum(PostMedia.size)).where(PostMedia.state != "archived"))
            or 0
        )
        if used + size > settings.linkedin_media_workspace_bytes:
            raise ValueError("Media storage is full; remove unused uploads first")
        item = PostMedia(profile_id=profile_id, kind=kind, size=size, digest=digest)
        session.add(item)
        session.flush()
        return item


def accessible(session: Session, media_id: str, actor: str) -> PostMedia:
    """Check media ownership before uploads or downloads."""
    item = session.get(PostMedia, media_id)
    if not item:
        raise ValueError("Media unavailable")
    profile_access(session, item.profile_id, actor)
    return item


def chunk(db: Database, media_id: str, actor: str, number: int, data: bytes) -> None:
    """Write each immutable chunk once; repeated identical bytes are safe."""
    with db.sessions.begin() as session:
        lock_registry(session)
        item = accessible(session, media_id, actor)
        expected = min(CHUNK, item.size - number * CHUNK)
        if item.state != "uploading" or number < 0 or expected < 1 or len(data) != expected:
            raise ValueError("Upload chunk does not match the reserved file")
        encoded = base64.b64encode(data).decode()
        old = session.get(PostMediaChunk, (media_id, number))
        if old and old.data != encoded:
            raise ValueError("Uploaded chunk differs; start a fresh upload")
        if not old:
            session.add(PostMediaChunk(media_id=media_id, number=number, data=encoded))


def bytes_for(session: Session, item: PostMedia) -> bytes:
    """Reassemble bounded contiguous chunks and recheck their whole-file hash."""
    chunks = list(
        session.scalars(
            select(PostMediaChunk)
            .where(PostMediaChunk.media_id == item.id)
            .order_by(PostMediaChunk.number)
        )
    )
    if [c.number for c in chunks] != list(range((item.size + CHUNK - 1) // CHUNK)):
        raise ValueError("Upload is incomplete")
    try:
        data = b"".join(base64.b64decode(c.data, validate=True) for c in chunks)
    except ValueError as error:
        raise ValueError("Media integrity check failed") from error
    if len(data) != item.size or hashlib.sha256(data).hexdigest() != item.digest:
        raise ValueError("Media integrity check failed")
    return data


def validate_container(data: bytes, kind: str) -> None:
    """Accept PNG/JPEG or a bounded MP4 container; provider performs codec validation."""
    if kind == "image":
        if data.startswith(b"\x89PNG\r\n\x1a\n") and len(data) >= 33 and data[12:16] == b"IHDR":
            width, height = struct.unpack(">II", data[16:24])
            if 0 < width * height < 36_152_320 and b"IEND" in data[-16:]:
                return
        if data.startswith(b"\xff\xd8") and data.endswith(b"\xff\xd9"):
            offset = 2
            while offset + 9 < len(data):
                if data[offset] != 255:
                    break
                marker = data[offset + 1]
                length = int.from_bytes(data[offset + 2 : offset + 4])
                if length < 2:
                    break
                if marker in {0xC0, 0xC1, 0xC2}:
                    height, width = struct.unpack(">HH", data[offset + 5 : offset + 9])
                    if 0 < width * height < 36_152_320:
                        return
                offset += length + 2
        raise ValueError("Use a valid PNG or JPEG below 36 million pixels")
    if (
        len(data) < 75_000
        or data[4:8] != b"ftyp"
        or data[8:12] not in {b"isom", b"iso2", b"mp41", b"mp42", b"avc1", b"M4V "}
    ):
        raise ValueError("Use an MP4 of at least 75 KB; 3 seconds to 30 minutes")
    # Read only bounded top-level boxes and movie header, never execute a decoder.
    cursor = 0
    while cursor + 8 <= len(data):
        size = int.from_bytes(data[cursor : cursor + 4])
        if size < 8 or cursor + size > len(data):
            break
        if data[cursor + 4 : cursor + 8] == b"moov":
            end, child = cursor + size, cursor + 8
            while child + 8 <= end:
                length = int.from_bytes(data[child : child + 4])
                if length < 8 or child + length > end:
                    break
                if data[child + 4 : child + 8] == b"mvhd":
                    body = data[child + 8 : child + length]
                    if len(body) < 32 or body[0] not in {0, 1}:
                        break
                    at = 20 if body[0] == 1 else 12
                    scale = int.from_bytes(body[at : at + 4])
                    duration = int.from_bytes(body[at + 4 : at + (12 if body[0] == 1 else 8)])
                    if scale and 3 <= duration / scale <= 1800:
                        return
                child += length
        cursor += size
    raise ValueError("MP4 duration/container could not be validated; re-export a standard MP4")


def finish(db: Database, media_id: str, actor: str) -> None:
    """Mark the file ready only after all chunks and content headers validate."""
    with db.sessions.begin() as session:
        lock_registry(session)
        item = accessible(session, media_id, actor)
        data = bytes_for(session, item)
        validate_container(data, item.kind)
        item.state = "ready"


def remove(db: Database, media_id: str, actor: str) -> None:
    """Remove only never-referenced uploads, preserving every approved post's exact asset."""
    with db.sessions.begin() as session:
        lock_registry(session)
        item = accessible(session, media_id, actor)
        for revision in session.scalars(select(PostRevision)):
            if revision.snapshot.get("media_id") == media_id:
                raise ValueError("This asset belongs to a post revision; keep it for audit")
        session.execute(delete(PostMediaChunk).where(PostMediaChunk.media_id == media_id))
        session.delete(item)


def cleanup_incomplete(db: Database) -> None:
    """Release abandoned upload reservations after one day without touching ready assets."""
    with db.sessions.begin() as session:
        lock_registry(session)
        for item in session.scalars(
            select(PostMedia).where(
                PostMedia.state == "uploading", PostMedia.created_at < utc_now() - timedelta(days=1)
            )
        ):
            session.execute(delete(PostMediaChunk).where(PostMediaChunk.media_id == item.id))
            session.delete(item)


def archive(db: Database, media_id: str, actor: str) -> None:
    """Owner may remove old published bytes while preserving hashes and approval metadata."""
    from app.db.linkedin_post_models import LinkedInPost
    from app.linkedin.identity import require_staff

    with db.sessions.begin() as session:
        require_staff(session, actor, owner=True)
        lock_registry(session)
        item = accessible(session, media_id, actor)
        referenced = [
            r.post_id
            for r in session.scalars(select(PostRevision))
            if r.snapshot.get("media_id") == media_id
        ]
        if not referenced:
            raise ValueError("Remove an unused upload instead")
        for post in session.scalars(select(LinkedInPost).where(LinkedInPost.id.in_(referenced))):
            if post.state != "published" or post.due_at > utc_now() - timedelta(days=30):
                raise ValueError("Archive only files from posts published at least 30 days ago")
        session.execute(delete(PostMediaChunk).where(PostMediaChunk.media_id == media_id))
        item.state = "archived"
