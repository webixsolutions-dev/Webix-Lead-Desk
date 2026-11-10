"""Encrypted optional owner factors and single-use sign-in challenges."""

from datetime import datetime

from sqlalchemy import Boolean, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models import Base, UTCDateTime


class OwnerMFA(Base):
    """Optional owner authenticator; pending enrollment never enables the factor."""

    __tablename__ = "owner_mfa"
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    encrypted_secret: Mapped[str | None] = mapped_column(Text)
    pending_secret: Mapped[str | None] = mapped_column(Text)
    pending_expires: Mapped[datetime | None] = mapped_column(UTCDateTime())
    pending_session_hash: Mapped[str | None] = mapped_column(String(64))
    last_counter: Mapped[int] = mapped_column(Integer, default=-1)
    version: Mapped[int] = mapped_column(Integer, default=0)


class MFAChallenge(Base):
    """Password-verified, short-lived challenge, never an authenticated session."""

    __tablename__ = "mfa_challenges"
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    csrf_token: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime())
    password_fingerprint: Mapped[str] = mapped_column(String(64))
    mfa_version: Mapped[int] = mapped_column(Integer)


class MFARecovery(Base):
    """Only the hash of each high-entropy, single-use recovery code is stored."""

    __tablename__ = "mfa_recovery"
    code_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    used_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
