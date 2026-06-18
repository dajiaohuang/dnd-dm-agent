"""Rules and compendium knowledge indexes."""

from __future__ import annotations

from typing import Any

from sqlalchemy import JSON, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from nanobot.dnd.db.database import Base
from nanobot.dnd.db.models.common import TimestampMixin


class RuleSource(TimestampMixin, Base):
    __tablename__ = "rule_sources"
    __table_args__ = (UniqueConstraint("source_path", name="uq_rule_source_path"),)

    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String)
    source_path: Mapped[str] = mapped_column(String)
    system_version: Mapped[str | None] = mapped_column(String)
    checksum: Mapped[str | None] = mapped_column(String)
    metadata_json: Mapped[dict[str, Any]] = mapped_column("metadata", JSON, default=dict)


class RuleChunk(Base):
    __tablename__ = "rule_chunks"
    __table_args__ = (
        UniqueConstraint("source_id", "chunk_index", name="uq_rule_chunk_index"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    source_id: Mapped[str] = mapped_column(
        ForeignKey("rule_sources.id", ondelete="CASCADE"), index=True
    )
    chunk_index: Mapped[int] = mapped_column(Integer)
    heading: Mapped[str | None] = mapped_column(String, index=True)
    start_line: Mapped[int | None] = mapped_column(Integer)
    end_line: Mapped[int | None] = mapped_column(Integer)
    char_start: Mapped[int | None] = mapped_column(Integer)
    char_end: Mapped[int | None] = mapped_column(Integer)
    chunk_text: Mapped[str] = mapped_column(Text)
    embedding_json: Mapped[list[float] | None] = mapped_column(JSON)
    metadata_json: Mapped[dict[str, Any]] = mapped_column("metadata", JSON, default=dict)


class CompendiumEntry(TimestampMixin, Base):
    __tablename__ = "compendium_entries"
    __table_args__ = (
        UniqueConstraint("entry_type", "name", "system_version", name="uq_compendium_entry"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    entry_type: Mapped[str] = mapped_column(String, index=True)
    name: Mapped[str] = mapped_column(String, index=True)
    data_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    source: Mapped[str | None] = mapped_column(String)
    system_version: Mapped[str | None] = mapped_column(String)
