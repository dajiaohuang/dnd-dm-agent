from datetime import UTC, datetime

from sqlalchemy import DateTime, ForeignKey, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base


def now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class Campaign(Base):
    __tablename__ = "campaigns"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    system_version: Mapped[str | None] = mapped_column(String)
    description: Mapped[str | None] = mapped_column(Text)
    config: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now, onupdate=now)


class Character(Base):
    __tablename__ = "characters"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    campaign_id: Mapped[str] = mapped_column(ForeignKey("campaigns.id", ondelete="CASCADE"), index=True)
    player_name: Mapped[str | None] = mapped_column(String)
    character_name: Mapped[str] = mapped_column(String, nullable=False)
    data: Mapped[dict] = mapped_column(JSON, nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now, onupdate=now)


class NapCatCharacterBinding(Base):
    __tablename__ = "napcat_character_bindings"
    __table_args__ = (
        UniqueConstraint("campaign_id", "qq_user_id", "character_id", name="uq_napcat_binding_campaign_qq_character"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    campaign_id: Mapped[str] = mapped_column(ForeignKey("campaigns.id", ondelete="CASCADE"), index=True)
    qq_user_id: Mapped[str] = mapped_column(String, index=True)
    character_id: Mapped[str] = mapped_column(ForeignKey("characters.id", ondelete="CASCADE"), index=True)
    display_name: Mapped[str | None] = mapped_column(String)
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now, onupdate=now)


class CharacterChange(Base):
    __tablename__ = "character_change_log"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    campaign_id: Mapped[str] = mapped_column(String, index=True)
    character_id: Mapped[str] = mapped_column(String, index=True)
    change_type: Mapped[str] = mapped_column(String)
    before_data: Mapped[dict] = mapped_column(JSON)
    after_data: Mapped[dict] = mapped_column(JSON)
    reason: Mapped[str | None] = mapped_column(Text)
    rule_refs: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class CampaignEvent(Base):
    __tablename__ = "campaign_events"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    campaign_id: Mapped[str] = mapped_column(ForeignKey("campaigns.id", ondelete="CASCADE"), index=True)
    session_id: Mapped[str | None] = mapped_column(String, index=True)
    event_type: Mapped[str] = mapped_column(String)
    content: Mapped[str] = mapped_column(Text)
    actors: Mapped[list] = mapped_column(JSON, default=list)
    visibility: Mapped[str] = mapped_column(String, default="party")
    importance: Mapped[int] = mapped_column(Integer, default=3)
    event_metadata: Mapped[dict] = mapped_column("metadata", JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class CampaignSummary(Base):
    __tablename__ = "campaign_summaries"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    campaign_id: Mapped[str] = mapped_column(ForeignKey("campaigns.id", ondelete="CASCADE"), index=True)
    scope: Mapped[str] = mapped_column(String)
    scope_id: Mapped[str | None] = mapped_column(String)
    summary: Mapped[str] = mapped_column(Text)
    open_threads: Mapped[list] = mapped_column(JSON, default=list)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now, onupdate=now)


class CampaignCheckpoint(Base):
    __tablename__ = "campaign_checkpoints"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    campaign_id: Mapped[str] = mapped_column(ForeignKey("campaigns.id", ondelete="CASCADE"), index=True)
    session_id: Mapped[str | None] = mapped_column(String, index=True)
    label: Mapped[str] = mapped_column(String)
    created_by: Mapped[str | None] = mapped_column(String)
    campaign_snapshot: Mapped[dict] = mapped_column(JSON)
    character_snapshots: Mapped[list] = mapped_column(JSON)
    latest_event_id: Mapped[str | None] = mapped_column(String)
    summary_id: Mapped[str | None] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class CampaignMemory(Base):
    __tablename__ = "campaign_memories"
    __table_args__ = (
        UniqueConstraint("campaign_id", "source_event_id", "memory_type", name="uq_campaign_memory_source_type"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    campaign_id: Mapped[str] = mapped_column(ForeignKey("campaigns.id", ondelete="CASCADE"), index=True)
    session_id: Mapped[str | None] = mapped_column(String, index=True)
    source_event_id: Mapped[str | None] = mapped_column(String, index=True)
    memory_type: Mapped[str] = mapped_column(String, index=True)
    subject: Mapped[str | None] = mapped_column(String, index=True)
    content: Mapped[str] = mapped_column(Text)
    importance: Mapped[int] = mapped_column(Integer, default=3)
    status: Mapped[str] = mapped_column(String, default="active", index=True)
    visibility: Mapped[str] = mapped_column(String, default="party")
    structured_data: Mapped[dict] = mapped_column(JSON, default=dict)
    embedding: Mapped[list | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now, onupdate=now)


class CampaignEntity(Base):
    __tablename__ = "campaign_entities"
    __table_args__ = (
        UniqueConstraint("campaign_id", "entity_type", "canonical_name", name="uq_campaign_entity_name_type"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    campaign_id: Mapped[str] = mapped_column(ForeignKey("campaigns.id", ondelete="CASCADE"), index=True)
    entity_type: Mapped[str] = mapped_column(String, index=True)
    canonical_name: Mapped[str] = mapped_column(String, index=True)
    aliases: Mapped[list] = mapped_column(JSON, default=list)
    description: Mapped[str | None] = mapped_column(Text)
    current_state: Mapped[dict] = mapped_column(JSON, default=dict)
    last_event_id: Mapped[str | None] = mapped_column(String)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now, onupdate=now)


class CampaignThread(Base):
    __tablename__ = "campaign_threads"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    campaign_id: Mapped[str] = mapped_column(ForeignKey("campaigns.id", ondelete="CASCADE"), index=True)
    session_id: Mapped[str | None] = mapped_column(String, index=True)
    title: Mapped[str] = mapped_column(String)
    description: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String, default="open", index=True)
    priority: Mapped[int] = mapped_column(Integer, default=3)
    related_entity_ids: Mapped[list] = mapped_column(JSON, default=list)
    source_event_id: Mapped[str | None] = mapped_column(String, unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now, onupdate=now)


class CampaignSetting(Base):
    __tablename__ = "campaign_settings"
    __table_args__ = (
        UniqueConstraint("campaign_id", "category", "name", name="uq_campaign_setting_category_name"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    campaign_id: Mapped[str] = mapped_column(ForeignKey("campaigns.id", ondelete="CASCADE"), index=True)
    category: Mapped[str] = mapped_column(String, index=True)
    name: Mapped[str] = mapped_column(String, index=True)
    summary: Mapped[str] = mapped_column(Text, default="")
    content: Mapped[dict] = mapped_column(JSON, default=dict)
    visibility: Mapped[str] = mapped_column(String, default="dm_only")
    status: Mapped[str] = mapped_column(String, default="published", index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    tags: Mapped[list] = mapped_column(JSON, default=list)
    relationships: Mapped[list] = mapped_column(JSON, default=list)
    embedding: Mapped[list | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now, onupdate=now)


class CampaignSettingDraft(Base):
    __tablename__ = "campaign_setting_drafts"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    campaign_id: Mapped[str] = mapped_column(ForeignKey("campaigns.id", ondelete="CASCADE"), index=True)
    session_id: Mapped[str | None] = mapped_column(String, index=True)
    operation: Mapped[str] = mapped_column(String)
    target_setting_id: Mapped[str | None] = mapped_column(String, index=True)
    category: Mapped[str] = mapped_column(String, default="custom")
    name: Mapped[str] = mapped_column(String, default="")
    proposal: Mapped[dict] = mapped_column(JSON, default=dict)
    reason: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String, default="pending", index=True)
    created_by: Mapped[str | None] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now, onupdate=now)


class CampaignSettingHistory(Base):
    __tablename__ = "campaign_setting_history"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    campaign_id: Mapped[str] = mapped_column(String, index=True)
    setting_id: Mapped[str] = mapped_column(String, index=True)
    operation: Mapped[str] = mapped_column(String)
    version: Mapped[int] = mapped_column(Integer)
    before_data: Mapped[dict] = mapped_column(JSON, default=dict)
    after_data: Mapped[dict] = mapped_column(JSON, default=dict)
    reason: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[str | None] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class CampaignSettingComment(Base):
    __tablename__ = "campaign_setting_comments"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    campaign_id: Mapped[str] = mapped_column(String, index=True)
    setting_id: Mapped[str | None] = mapped_column(String, index=True)
    draft_id: Mapped[str | None] = mapped_column(String, index=True)
    author_id: Mapped[str | None] = mapped_column(String)
    content: Mapped[str] = mapped_column(Text)
    resolved: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class RuleChunk(Base):
    __tablename__ = "rule_chunks"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    system_version: Mapped[str | None] = mapped_column(String)
    source: Mapped[str] = mapped_column(String)
    chapter: Mapped[str | None] = mapped_column(String)
    section: Mapped[str | None] = mapped_column(String)
    chunk_text: Mapped[str] = mapped_column(Text)
    embedding: Mapped[list | None] = mapped_column(JSON)
    chunk_metadata: Mapped[dict] = mapped_column("metadata", JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class CompendiumEntry(Base):
    __tablename__ = "compendium_entries"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    entry_type: Mapped[str] = mapped_column(String, index=True)
    name: Mapped[str] = mapped_column(String, index=True)
    data: Mapped[dict] = mapped_column(JSON)
    source: Mapped[str | None] = mapped_column(String)
    system_version: Mapped[str | None] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now, onupdate=now)
