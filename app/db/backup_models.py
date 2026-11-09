"""Shared backup receipt and scheduler lease; keys and filesystem paths stay on the PC."""

from datetime import datetime

from sqlalchemy import Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models import Base, UTCDateTime, new_id


class BackupStatus(Base):
    """Track durable successful backup receipts and a bounded reservation between workers."""

    __tablename__ = "backup_status"
    key: Mapped[str] = mapped_column(String(32), primary_key=True)
    namespace: Mapped[str] = mapped_column(String(36), default=new_id)
    last_success_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    last_attempt_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    lease_until: Mapped[datetime | None] = mapped_column(UTCDateTime())
    claim: Mapped[str] = mapped_column(String(36), default="")
    sha256: Mapped[str] = mapped_column(String(64), default="")
    file_bytes: Mapped[int] = mapped_column(Integer, default=0)
    error_code: Mapped[str] = mapped_column(String(64), default="")
