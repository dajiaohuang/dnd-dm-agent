"""Deterministic dice and tool execution audit records."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from nanobot.dnd.db.database import Base
from nanobot.dnd.db.models.common import utc_now


class DiceRoll(Base):
    __tablename__ = "dice_rolls"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    campaign_id: Mapped[str] = mapped_column(
        ForeignKey("campaigns.id", ondelete="CASCADE"), index=True
    )
    character_id: Mapped[str | None] = mapped_column(
        ForeignKey("characters.id", ondelete="SET NULL"), index=True
    )
    formula: Mapped[str] = mapped_column(String)
    result: Mapped[int] = mapped_column(Integer)
    detail_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    context: Mapped[str] = mapped_column(Text, default="")
    tool_name: Mapped[str] = mapped_column(String, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, index=True)


class ToolAudit(Base):
    __tablename__ = "tool_audits"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    campaign_id: Mapped[str | None] = mapped_column(
        ForeignKey("campaigns.id", ondelete="SET NULL"), index=True
    )
    session_id: Mapped[str | None] = mapped_column(String, index=True)
    actor_id: Mapped[str | None] = mapped_column(String, index=True)
    tool_name: Mapped[str] = mapped_column(String, index=True)
    arguments_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    result_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    state_version: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, index=True)
