from __future__ import annotations

from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.db.models import Campaign, TaskSession
from app.services import serialize, uid


ACTIVE_STATUSES = ("active", "waiting_user", "queued", "running", "ready_to_review", "ready_to_commit")


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


def bump_draft_version(task: TaskSession) -> int:
    draft = dict(task.draft_data or {})
    meta = dict(draft.get("_meta") or {})
    version = int(meta.get("version") or 0) + 1
    meta["version"] = version
    draft["_meta"] = meta
    task.draft_data = draft
    return version


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


def create_subagent_proposal(
    db: Session,
    campaign: Campaign,
    parent: TaskSession,
    *,
    agent_role: str,
    goal: str,
    proposal: dict[str, Any] | None = None,
    next_prompt: str = "请审核子任务提案。",
) -> TaskSession:
    item = TaskSession(
        id=uid("task"),
        campaign_id=campaign.id,
        task_type="subagent_proposal",
        platform=parent.platform,
        chat_id=parent.chat_id,
        owner_user_id=parent.owner_user_id,
        session_id=parent.session_id,
        status="queued",
        priority=parent.priority,
        proposal_data={
            "agent_role": agent_role,
            "goal": goal,
            "source_parent_version": ((parent.draft_data or {}).get("_meta") or {}).get("version", 0),
            "proposal": proposal or {},
        },
        next_prompt=next_prompt,
        mentions=owner_mentions(parent.owner_user_id, next_prompt),
        parent_task_id=parent.id,
    )
    db.add(item)
    return item


def ready_reviews(
    db: Session,
    campaign: Campaign,
    platform: str,
    owner_user_id: str,
    session_id: str | None,
) -> list[TaskSession]:
    query = select(TaskSession).where(
        TaskSession.campaign_id == campaign.id,
        TaskSession.task_type == "subagent_proposal",
        TaskSession.platform == platform,
        TaskSession.owner_user_id == owner_user_id,
        TaskSession.status.in_(["ready_to_review", "failed"]),
    )
    if session_id:
        query = query.where(or_(TaskSession.session_id == session_id, TaskSession.session_id.is_(None)))
    return db.scalars(query.order_by(TaskSession.updated_at.desc())).all()


def format_ready_reviews(tasks: list[TaskSession]) -> str:
    lines = ["有后台子任务结果待审核："]
    for index, task in enumerate(tasks, start=1):
        data = task.proposal_data or {}
        result = data.get("result") or {}
        stale = "（基于旧版草稿，仅供参考）" if data.get("stale") else ""
        summary = result.get("summary")
        if not summary and result.get("names"):
            summary = "已生成：" + "、".join(str(name) for name in result["names"][:8])
        if not summary and result.get("content"):
            summary = str(result["content"])[:300]
        lines.append(
            f"{index}. {data.get('agent_role') or task.task_type}{stale}："
            f"{summary or task.next_prompt or '已完成'}"
        )
        error = result.get("error") or data.get("error")
        if error:
            lines.append(f"   执行失败：{error}")
        issues = result.get("blocking_issues") or []
        if issues:
            lines.append("   阻塞问题：" + "；".join(str(item) for item in issues[:3]))
    lines.append("可以回复“采用建议”“继续修改”或“发布/提交”。")
    return "\n".join(lines)
