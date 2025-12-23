from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import BigInteger, String, Integer, DateTime, Boolean, ForeignKey, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    tg_hash: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    fake_id: Mapped[int] = mapped_column(Integer, unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)

    subscriptions: Mapped[list["Subscription"]] = relationship(back_populates="user")
    support_tickets: Mapped[list["SupportTicket"]] = relationship(back_populates="user")


class Subscription(Base):
    __tablename__ = "subscriptions"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    expires_at: Mapped[datetime] = mapped_column(
        default=lambda: datetime.utcnow() + timedelta(days=31)
    )

    xui_client_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    xui_email: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    xui_config: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)

    user: Mapped["User"] = relationship(back_populates="subscriptions")


class SupportTicket(Base):
    __tablename__ = "support_tickets"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))

    is_open: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)
    closed_at: Mapped[Optional[datetime]] = mapped_column(nullable=True)

    last_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    user: Mapped["User"] = relationship(back_populates="support_tickets")