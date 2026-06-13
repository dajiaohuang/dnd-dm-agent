from __future__ import annotations

import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import CampaignEntity, CampaignEvent, CampaignMemory, CampaignThread
from app.rag.embedder import embed_text


THREAD_TERMS = (
    "任务", "委托", "承诺", "约定", "寻找", "调查", "追踪", "复仇", "失踪",
    "quest", "mission", "promise", "investigate", "find", "search", "missing",
)
RESOLUTION_TERMS = ("完成任务", "任务完成", "兑现承诺", "已经解决", "quest complete", "resolved")


def _uid(prefix: str, event_id: str) -> str:
    return f"{prefix}_{event_id.removeprefix('evt_')}"


def extract_event_memory(event: CampaignEvent) -> dict:
    response = str((event.event_metadata or {}).get("dm_response", "")).strip()
    combined = "\n".join(part for part in [event.content.strip(), response] if part)
    lowered = combined.casefold()
    memory_type = "decision" if event.event_type == "player_action" else "event"
    if any(term in lowered for term in THREAD_TERMS):
        memory_type = "thread"
    importance = max(event.importance, 4 if memory_type == "thread" else 3)
    tags = sorted({
        term for term in THREAD_TERMS
        if term in lowered
    })
    return {
        "memory_type": memory_type,
        "subject": event.actors[0] if event.actors else None,
        "content": combined[:4000],
        "importance": importance,
        "tags": tags,
        "opens_thread": memory_type == "thread",
        "resolves_thread": any(term in lowered for term in RESOLUTION_TERMS),
    }


def index_event_memory(db: Session, event: CampaignEvent, plan: dict | None = None) -> CampaignMemory | None:
    extracted = extract_event_memory(event)
    if plan and plan.get("skip"):
        return None
    existing = db.scalar(select(CampaignMemory).where(
        CampaignMemory.campaign_id == event.campaign_id,
        CampaignMemory.source_event_id == event.id,
        CampaignMemory.memory_type == extracted["memory_type"],
    ))
    if existing:
        return existing
    memory = CampaignMemory(
        id=_uid("mem", event.id),
        campaign_id=event.campaign_id,
        session_id=event.session_id,
        source_event_id=event.id,
        memory_type=extracted["memory_type"],
        subject=extracted["subject"],
        content=extracted["content"],
        importance=extracted["importance"],
        visibility=event.visibility,
        structured_data={
            "tags": extracted["tags"],
            "event_type": event.event_type,
            "actors": event.actors,
            "graph_plan": plan or {},
        },
        embedding=embed_text(extracted["content"]),
    )
    db.add(memory)
    for actor in event.actors:
        entity = db.scalar(select(CampaignEntity).where(
            CampaignEntity.campaign_id == event.campaign_id,
            CampaignEntity.entity_type == "character",
            CampaignEntity.canonical_name == actor,
        ))
        if not entity:
            entity = CampaignEntity(
                id=f"entity_{event.campaign_id}_{re.sub(r'[^a-zA-Z0-9_-]', '_', actor)[:80]}",
                campaign_id=event.campaign_id,
                entity_type="character",
                canonical_name=actor,
            )
            db.add(entity)
        entity.last_event_id = event.id
        entity.current_state = {"last_memory": extracted["content"][:500]}
    if extracted["opens_thread"]:
        thread = db.scalar(select(CampaignThread).where(CampaignThread.source_event_id == event.id))
        if not thread:
            db.add(CampaignThread(
                id=_uid("thread", event.id),
                campaign_id=event.campaign_id,
                session_id=event.session_id,
                title=event.content.strip()[:120] or "未命名剧情线",
                description=extracted["content"],
                priority=extracted["importance"],
                source_event_id=event.id,
            ))
    db.commit()
    return memory


def backfill_campaign_memory(db: Session, campaign_id: str) -> dict:
    events = db.scalars(
        select(CampaignEvent).where(CampaignEvent.campaign_id == campaign_id).order_by(CampaignEvent.created_at)
    ).all()
    before = db.query(CampaignMemory).filter(CampaignMemory.campaign_id == campaign_id).count()
    for event in events:
        index_event_memory(db, event)
    after = db.query(CampaignMemory).filter(CampaignMemory.campaign_id == campaign_id).count()
    return {"events_scanned": len(events), "memories_created": after - before, "memory_count": after}


def search_campaign_memory(
    db: Session, campaign_id: str, query: str, session_id: str | None = None, limit: int = 8
) -> list[CampaignMemory]:
    statement = select(CampaignMemory).where(
        CampaignMemory.campaign_id == campaign_id,
        CampaignMemory.status == "active",
    )
    memories = db.scalars(statement).all()
    query_embedding = embed_text(query)
    terms = set(re.findall(r"[\w\u4e00-\u9fff]+", query.casefold()))
    ranked = []
    for memory in memories:
        lexical = sum(memory.content.casefold().count(term) for term in terms)
        semantic = 0.0
        if query_embedding and memory.embedding and len(query_embedding) == len(memory.embedding):
            semantic = sum(a * b for a, b in zip(query_embedding, memory.embedding, strict=True))
        session_bonus = 0.2 if session_id and memory.session_id == session_id else 0
        score = semantic + min(lexical, 5) * 0.25 + memory.importance * 0.03 + session_bonus
        ranked.append((score, memory))
    ranked.sort(key=lambda item: (item[0], item[1].updated_at), reverse=True)
    return [memory for _, memory in ranked[:limit]]


def build_memory_package(
    db: Session, campaign_id: str, query: str, session_id: str | None = None, limit: int = 8
) -> dict:
    memories = search_campaign_memory(db, campaign_id, query, session_id, limit)
    entities = db.scalars(
        select(CampaignEntity).where(CampaignEntity.campaign_id == campaign_id)
        .order_by(CampaignEntity.updated_at.desc()).limit(8)
    ).all()
    threads = db.scalars(
        select(CampaignThread).where(
            CampaignThread.campaign_id == campaign_id, CampaignThread.status == "open"
        ).order_by(CampaignThread.priority.desc(), CampaignThread.updated_at.desc()).limit(8)
    ).all()
    return {
        "memories": [
            {"id": item.id, "type": item.memory_type, "subject": item.subject, "content": item.content,
             "importance": item.importance, "visibility": item.visibility,
             "tags": (item.structured_data or {}).get("tags", [])}
            for item in memories
        ],
        "entities": [
            {"id": item.id, "type": item.entity_type, "name": item.canonical_name, "state": item.current_state}
            for item in entities
        ],
        "threads": [
            {"id": item.id, "title": item.title, "description": item.description, "priority": item.priority}
            for item in threads
        ],
    }
