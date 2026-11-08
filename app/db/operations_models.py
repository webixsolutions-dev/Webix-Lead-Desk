"""Database-wide maintenance gate shared by the web app and every worker."""

from datetime import datetime

from sqlalchemy import Boolean, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.clock import utc_now
from app.db.models import Base, UTCDateTime


class RuntimeControl(Base):
    """A durable stop that remains enabled on both sides of a database transfer."""

    __tablename__ = "runtime_control"
    key: Mapped[str] = mapped_column(String(32), primary_key=True)
    maintenance: Mapped[bool] = mapped_column(Boolean, default=False)
    generation: Mapped[int] = mapped_column(Integer, default=1)
    note: Mapped[str] = mapped_column(String(500), default="")
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)
