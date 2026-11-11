"""Private native-agent pairing and durable, replay-safe notification records."""

from datetime import datetime

from sqlalchemy import Boolean, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.clock import utc_now
from app.db.models import Base, UTCDateTime, new_id


class WhatsAppConnection(Base):
    """One explicitly paired owner; the token lives in the encrypted credential vault."""

    __tablename__ = "whatsapp_connections"
    id: Mapped[str] = mapped_column(String(16), primary_key=True, default="primary")
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    generation: Mapped[str] = mapped_column(String(36), default=new_id)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    share_details: Mapped[bool] = mapped_column(Boolean, default=False)
    recipient: Mapped[str] = mapped_column(String(100), default="")
    pair_hash: Mapped[str] = mapped_column(String(64), default="")
    pair_expires: Mapped[datetime | None] = mapped_column(UTCDateTime())
    offset: Mapped[int | None] = mapped_column(Integer)
    connected_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    last_poll: Mapped[datetime | None] = mapped_column(UTCDateTime())
    next_poll: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)
    status: Mapped[str] = mapped_column(String(40), default="waiting_for_link")


class WhatsAppNotice(Base):
    """One notification per source revision; uncertain sends are never blindly retried."""

    __tablename__ = "whatsapp_notices"
    __table_args__ = (UniqueConstraint("generation", "source_key"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    generation: Mapped[str] = mapped_column(String(36), index=True)
    source_key: Mapped[str] = mapped_column(String(160))
    kind: Mapped[str] = mapped_column(String(16))
    draft_id: Mapped[str | None] = mapped_column(ForeignKey("drafts.id"))
    digest: Mapped[str] = mapped_column(String(64), default="")
    code: Mapped[str] = mapped_column(String(24), unique=True)
    body: Mapped[str] = mapped_column(Text)
    state: Mapped[str] = mapped_column(String(24), default="pending")
    message_id: Mapped[str] = mapped_column(String(300), default="")
    action: Mapped[str] = mapped_column(String(24), default="")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime())


class WhatsAppCommand(Base):
    """Consumed message IDs only; raw incoming chat text is not retained."""

    __tablename__ = "whatsapp_commands"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)
