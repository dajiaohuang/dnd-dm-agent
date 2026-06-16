from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Campaign, TaskSession
from app.services import serialize, uid


ACTIVE_STATUSES = ("active", "waiting_user", "ready_to_commit")


def task_scope(
    message_context: dict | None,
    actor_id: str | None,
    session_id: str | None,
) -> tuple[str, str | None, str, str | None]:
    context = message_context or {}
    platform = str(context.get("platform") or "web")
    chat_id = context.get("group_id") or context.get("chat_id")
    owner_user_id = str(actor_id or context.get("sender_id") or session_id or "anonymous")
    return platform, str(chat_id) if chat_id is not None else None, owner_user_id, session_id


def active_task(
    db: Session,
    campaign: Campaign,
    task_type: str,
    platform: str,
    owner_user_id: str,
    session_id: str | None,
) -> TaskSession | None:
    query = select(TaskSession).where(
        TaskSession.campaign_id == campaign.id,
        TaskSession.task_type == task_type,
        TaskSession.platform == platform,
        TaskSession.owner_user_id == owner_user_id,
        TaskSession.status.in_(ACTIVE_STATUSES),
    )
    if session_id:
        query = query.where(TaskSession.session_id == session_id)
    return db.scalar(query.order_by(TaskSession.updated_at.desc()))


def owner_mentions(owner_user_id: str | None, text: str) -> list[dict[str, str]]:
    if not owner_user_id or not owner_user_id.isdigit():
        return []
    return [{"user_id": owner_user_id, "text": text}]


def session_payload(task: TaskSession) -> dict[str, Any]:
    data = serialize(task)
    data["user_id"] = data.get("owner_user_id")
    data["created_character_id"] = data.get("created_object_id")
    return data


def create_task(
    db: Session,
    campaign: Campaign,
    task_type: str,
    platform: str,
    chat_id: str | None,
    owner_user_id: str,
    session_id: str | None,
    *,
    status: str = "active",
    draft_data: dict[str, Any] | None = None,
    proposal_data: dict[str, Any] | None = None,
    missing_fields: list[str] | None = None,
    next_prompt: str = "",
    mentions: list[dict[str, str]] | None = None,
) -> TaskSession:
    item = TaskSession(
        id=uid("task"),
        campaign_id=campaign.id,
        task_type=task_type,
        platform=platform,
        chat_id=chat_id,
        owner_user_id=owner_user_id,
        session_id=session_id,
        status=status,
        draft_data=draft_data or {},
        proposal_data=proposal_data or {},
        missing_fields=missing_fields or [],
        next_prompt=next_prompt,
        mentions=mentions or [],
    )
    db.add(item)
    return item
