from __future__ import annotations

import copy
import hashlib
import json
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.campaign_editor import setting_text, setting_to_npc_character
from app.db.models import (
    AgentArtifact, AgentJob, CampaignSetting, CampaignSettingHistory,
    NotificationOutbox,
)
from app.rag.embedder import embed_text
from app.services import serialize, uid
from app.workflows.artifact_schemas import SettingBatchArtifact


def create_job(
    db: Session, *, campaign_id: str, job_type: str, request_payload: dict,
    owner_user_id: str | None, platform: str, chat_id: str | None,
    session_id: str | None, idempotency_key: str | None = None,
) -> AgentJob:
    if idempotency_key:
        existing = db.scalar(select(AgentJob).where(AgentJob.idempotency_key == idempotency_key))
        if existing:
            return existing
    job = AgentJob(
        id=uid("job"), campaign_id=campaign_id, job_type=job_type,
        owner_user_id=owner_user_id, platform=platform, chat_id=chat_id,
        session_id=session_id, status="queued", progress={},
        request_payload=copy.deepcopy(request_payload), idempotency_key=idempotency_key,
        retry_count=0,
    )
    db.add(job)
    db.commit()
    return job


def message_idempotency_key(job_type: str, message_context: dict | None) -> str | None:
    message_id = str((message_context or {}).get("message_id") or "").strip()
    if not message_id:
        return None
    platform = str((message_context or {}).get("platform") or "")
    raw = f"{platform}:{message_id}:{job_type}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def list_jobs(db: Session, campaign_id: str, owner_user_id: str | None = None) -> list[AgentJob]:
    query = select(AgentJob).where(AgentJob.campaign_id == campaign_id)
    if owner_user_id:
        query = query.where(AgentJob.owner_user_id == owner_user_id)
    return db.scalars(query.order_by(AgentJob.updated_at.desc()).limit(30)).all()


def latest_artifact(db: Session, job_id: str) -> AgentArtifact | None:
    return db.scalar(select(AgentArtifact).where(
        AgentArtifact.job_id == job_id,
    ).order_by(AgentArtifact.created_at.desc()).limit(1))


def commit_artifact(db: Session, artifact: AgentArtifact, actor_id: str | None = None) -> list[dict]:
    if artifact.status == "committed":
        return list(artifact.committed_objects or [])
    if artifact.artifact_type != "campaign_settings":
        raise ValueError(f"unsupported artifact type: {artifact.artifact_type}")
    batch = SettingBatchArtifact.model_validate(artifact.payload)
    committed: list[dict] = []
    for proposal in batch.settings:
        setting = db.scalar(select(CampaignSetting).where(
            CampaignSetting.campaign_id == artifact.campaign_id,
            CampaignSetting.category == proposal.category,
            CampaignSetting.name == proposal.name,
        ))
        before = serialize(setting) if setting else {}
        if not setting:
            setting = CampaignSetting(
                id=uid("setting"), campaign_id=artifact.campaign_id,
                category=proposal.category, name=proposal.name, version=1,
            )
            db.add(setting)
        else:
            setting.version = (setting.version or 1) + 1
        setting.summary = proposal.summary
        setting.content = copy.deepcopy(proposal.content)
        setting.visibility = proposal.visibility
        setting.tags = list(proposal.tags)
        setting.relationships = list(proposal.relationships)
        setting.status = "published"
        setting.embedding = embed_text(setting_text(setting))
        db.flush()
        db.add(CampaignSettingHistory(
            id=uid("setting_history"), campaign_id=artifact.campaign_id,
            setting_id=setting.id, operation="update" if before else "create",
            version=setting.version, before_data=before, after_data=serialize(setting),
            reason=f"accepted artifact {artifact.id}", created_by=actor_id,
        ))
        object_info = {"type": "campaign_setting", "id": setting.id, "name": setting.name}
        if proposal.category == "npc":
            character = setting_to_npc_character(db, setting)
            object_info["character_id"] = character.id
        committed.append(object_info)
    artifact.status = "committed"
    artifact.committed_objects = committed
    job = db.get(AgentJob, artifact.job_id)
    if job:
        job.status = "committed"
    db.commit()
    return committed


def reject_artifact(db: Session, artifact: AgentArtifact) -> None:
    artifact.status = "rejected"
    job = db.get(AgentJob, artifact.job_id)
    if job:
        job.status = "rejected"
    db.commit()


def pending_notifications(
    db: Session, *, platform: str, owner_user_id: str | None, campaign_id: str | None,
) -> list[NotificationOutbox]:
    query = select(NotificationOutbox).where(
        NotificationOutbox.status == "pending",
        NotificationOutbox.platform == platform,
    )
    if owner_user_id:
        query = query.where(NotificationOutbox.owner_user_id == owner_user_id)
    if campaign_id:
        query = query.where(NotificationOutbox.campaign_id == campaign_id)
    return db.scalars(query.order_by(NotificationOutbox.created_at).limit(10)).all()


def mark_notifications_delivered(db: Session, notices: list[NotificationOutbox]) -> None:
    now = datetime.now(UTC).replace(tzinfo=None)
    for notice in notices:
        notice.status = "delivered"
        notice.delivered_at = now
        notice.attempts = (notice.attempts or 0) + 1
    db.commit()
