from __future__ import annotations

import copy
import json
import re
from typing import Any
from uuid import uuid4

from sqlalchemy import inspect, select
from sqlalchemy.orm import Session

from app.agents.campaign_editor_graph import campaign_editor_graph
from app.db.models import (
    Campaign, CampaignEvent, CampaignSetting, CampaignSettingDraft, CampaignSettingHistory, Character,
)
from app.llm import chat_completion
from app.rag.embedder import embed_text


def uid(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:12]}"


def serialize(obj) -> dict:
    return {prop.columns[0].name: getattr(obj, prop.key) for prop in inspect(obj).mapper.column_attrs}


def merge_dict(base: dict, patch: dict) -> dict:
    result = copy.deepcopy(base)
    for key, value in patch.items():
        result[key] = merge_dict(result[key], value) if isinstance(value, dict) and isinstance(result.get(key), dict) else value
    return result


def json_safe(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False, default=str))


SETTING_CATEGORIES = (
    "campaign_pitch", "world_rule", "location", "faction", "npc", "monster", "history",
    "deity", "quest", "encounter", "custom",
)
TEMPLATES = {
    "classic_fantasy": [
        {"category": "campaign_pitch", "name": "Campaign Premise", "summary": "Heroes confront a rising threat."},
        {"category": "location", "name": "Starting Settlement", "summary": "The party's first safe haven."},
        {"category": "faction", "name": "Primary Antagonist", "summary": "A faction pursuing the central threat."},
    ],
    "mystery": [
        {"category": "campaign_pitch", "name": "Central Mystery", "summary": "A secret that drives the campaign."},
        {"category": "location", "name": "Investigation Hub", "summary": "The central location for clues."},
        {"category": "npc", "name": "Key Witness", "summary": "A witness with incomplete information."},
    ],
}


def setting_text(setting: CampaignSetting) -> str:
    return f"{setting.category} {setting.name} {setting.summary} {json.dumps(setting.content, ensure_ascii=False)}"


def list_settings(db: Session, campaign_id: str, include_archived: bool = False) -> list[CampaignSetting]:
    query = select(CampaignSetting).where(CampaignSetting.campaign_id == campaign_id)
    if not include_archived:
        query = query.where(CampaignSetting.status == "published")
    return db.scalars(query.order_by(CampaignSetting.category, CampaignSetting.name)).all()


def search_settings(db: Session, campaign_id: str, query: str, limit: int = 6) -> list[CampaignSetting]:
    settings = list_settings(db, campaign_id)
    vector = embed_text(query)
    terms = set(re.findall(r"[\w\u4e00-\u9fff]+", query.casefold()))
    ranked = []
    for setting in settings:
        text = setting_text(setting).casefold()
        lexical = sum(text.count(term) for term in terms)
        semantic = 0.0
        if vector and setting.embedding and len(vector) == len(setting.embedding):
            semantic = sum(a * b for a, b in zip(vector, setting.embedding, strict=True))
        ranked.append((semantic + min(lexical, 8) * 0.3, setting))
    ranked.sort(key=lambda item: (item[0], item[1].updated_at), reverse=True)
    return [item for _, item in ranked[:limit]]


def create_draft(
    db: Session, campaign_id: str, operation: str, proposal: dict, session_id: str | None = None,
    actor_id: str | None = None, target_setting_id: str | None = None, reason: str = "",
) -> CampaignSettingDraft:
    draft = CampaignSettingDraft(
        id=uid("draft"), campaign_id=campaign_id, session_id=session_id, operation=operation,
        target_setting_id=target_setting_id, category=proposal.get("category", "custom"),
        name=proposal.get("name", ""), proposal=proposal, reason=reason, created_by=actor_id,
    )
    db.add(draft)
    db.commit()
    return draft


def publish_drafts(db: Session, campaign_id: str, actor_id: str | None = None) -> list[CampaignSetting]:
    drafts = db.scalars(select(CampaignSettingDraft).where(
        CampaignSettingDraft.campaign_id == campaign_id, CampaignSettingDraft.status == "pending",
    ).order_by(CampaignSettingDraft.created_at)).all()
    published = []
    for draft in drafts:
        before = {}
        setting = db.get(CampaignSetting, draft.target_setting_id) if draft.target_setting_id else None
        if draft.operation == "create":
            setting = CampaignSetting(
                id=uid("setting"), campaign_id=campaign_id, category=draft.category, name=draft.name, version=1,
            )
            db.add(setting)
        elif not setting:
            draft.status = "rejected"
            continue
        else:
            before = serialize(setting)
        proposal = copy.deepcopy(draft.proposal)
        if draft.operation == "archive":
            setting.status = "archived"
        else:
            for key in ("category", "name", "summary", "content", "visibility", "tags", "relationships"):
                if key in proposal:
                    value = proposal[key]
                    if key == "content" and draft.operation == "update":
                        value = merge_dict(setting.content or {}, value)
                    setattr(setting, key, value)
            setting.status = "published"
        setting.version = (setting.version or 1) + (1 if before else 0)
        setting.embedding = embed_text(setting_text(setting))
        db.flush()
        after = json_safe(serialize(setting))
        db.add(CampaignSettingHistory(
            id=uid("setting_history"), campaign_id=campaign_id, setting_id=setting.id,
            operation=draft.operation, version=setting.version, before_data=json_safe(before), after_data=after,
            reason=draft.reason, created_by=actor_id or draft.created_by,
        ))
        draft.status = "published"
        published.append(setting)
    db.commit()
    return published


def discard_drafts(db: Session, campaign_id: str) -> int:
    drafts = db.scalars(select(CampaignSettingDraft).where(
        CampaignSettingDraft.campaign_id == campaign_id, CampaignSettingDraft.status == "pending",
    )).all()
    for draft in drafts:
        draft.status = "discarded"
    db.commit()
    return len(drafts)


def undo_latest_draft(db: Session, campaign_id: str) -> CampaignSettingDraft | None:
    draft = db.scalar(select(CampaignSettingDraft).where(
        CampaignSettingDraft.campaign_id == campaign_id, CampaignSettingDraft.status == "pending",
    ).order_by(CampaignSettingDraft.created_at.desc()).limit(1))
    if draft:
        draft.status = "discarded"
        db.commit()
    return draft


def validate_settings(db: Session, campaign_id: str) -> dict:
    settings = list_settings(db, campaign_id)
    names = {item.name: item.id for item in settings}
    issues = []
    for setting in settings:
        for relation in setting.relationships or []:
            target = relation.get("target_id") or names.get(relation.get("target_name"))
            if not target:
                issues.append({"setting_id": setting.id, "type": "dangling_relationship", "relation": relation})
    duplicate_names = sorted({item.name for item in settings if sum(x.name == item.name for x in settings) > 1})
    return {"valid": not issues and not duplicate_names, "issues": issues, "duplicate_names": duplicate_names}


def editor_chat(db: Session, campaign: Campaign, session_id: str | None, message: str, actor_id: str | None) -> dict:
    settings = [serialize(item) for item in list_settings(db, campaign.id)]
    state = campaign_editor_graph.invoke({"user_message": message, "setting_context": settings})
    intent = state["intent"]
    related = state.get("related_settings", [])
    if intent == "brainstorm":
        narration = chat_completion([
            {"role": "system", "content": "You are a collaborative campaign setting editor. Discuss ideas without changing canonical data."},
            {"role": "system", "content": json.dumps(settings[:20], ensure_ascii=False, default=str)},
            {"role": "user", "content": message},
        ]) or "这是一个可继续发展的方向。你可以明确说“创建设定：类别 | 名称 | 摘要”来形成草稿。"
        return {"narration": narration,
                "editor_intent": intent, "related_settings": related, "drafts": []}
    if intent == "inspect_setting":
        return {"narration": json.dumps(related, ensure_ascii=False, indent=2), "editor_intent": intent,
                "related_settings": related, "drafts": []}
    if intent == "validate_setting":
        result = validate_settings(db, campaign.id)
        return {"narration": json.dumps(result, ensure_ascii=False, indent=2), "editor_intent": intent, "drafts": []}
    proposal = parse_editor_proposal(message, intent, related)
    draft = create_draft(
        db, campaign.id, proposal.pop("operation"), proposal, session_id, actor_id,
        proposal.pop("target_setting_id", None), message,
    )
    return {"narration": f"已生成设定草稿：{draft.operation} {draft.name or draft.target_setting_id}。发送 /发布设定 确认。",
            "editor_intent": intent, "drafts": [serialize(draft)], "related_settings": related}


def parse_editor_proposal(message: str, intent: str, related: list[dict]) -> dict:
    body = message.split("：", 1)[-1].split(":", 1)[-1].strip()
    parts = [part.strip() for part in re.split(r"[|｜]", body)]
    operation = intent.replace("_setting", "")
    target = related[0] if related else None
    if operation == "create":
        category = parts[0] if parts and parts[0] in SETTING_CATEGORIES else "custom"
        name = parts[1] if len(parts) > 1 else (parts[0] if parts else "Untitled Setting")
        summary = parts[2] if len(parts) > 2 else message
        return {"operation": "create", "category": category, "name": name, "summary": summary, "content": {}}
    return {
        "operation": operation, "target_setting_id": target.get("id") if target else None,
        "category": target.get("category", "custom") if target else "custom",
        "name": target.get("name", "") if target else "",
        "summary": parts[-1] if parts else message,
    }


def setting_graph(db: Session, campaign_id: str) -> dict:
    settings = list_settings(db, campaign_id)
    return {
        "nodes": [{"id": item.id, "name": item.name, "category": item.category} for item in settings],
        "edges": [{"source": item.id, **relation} for item in settings for relation in (item.relationships or [])],
    }


def setting_timeline(db: Session, campaign_id: str) -> list[dict]:
    settings = list_settings(db, campaign_id)
    events = db.scalars(select(CampaignEvent).where(CampaignEvent.campaign_id == campaign_id)).all()
    timeline = [
        {"kind": "setting", "id": item.id, "name": item.name, "date": (item.content or {}).get("date"),
         "created_at": item.created_at}
        for item in settings if item.category == "history" or (item.content or {}).get("date")
    ]
    timeline += [{"kind": "event", "id": item.id, "name": item.content, "date": None, "created_at": item.created_at}
                 for item in events]
    return sorted(timeline, key=lambda item: item["created_at"])


def export_campaign_package(db: Session, campaign_id: str) -> dict:
    campaign = db.get(Campaign, campaign_id)
    return {
        "format": "dnd-dm-agent-campaign-package-v1",
        "campaign": serialize(campaign),
        "settings": [serialize(item) for item in list_settings(db, campaign_id, include_archived=True)],
        "characters": [serialize(item) for item in db.scalars(select(Character).where(Character.campaign_id == campaign_id)).all()],
    }


def apply_template(db: Session, campaign_id: str, template: str, actor_id: str | None = None) -> int:
    items = TEMPLATES.get(template)
    if not items:
        raise ValueError("Unknown campaign template")
    for item in items:
        create_draft(db, campaign_id, "create", {**item, "content": {}}, actor_id=actor_id)
    return len(items)


def conflict_suggestions(db: Session, campaign_id: str) -> list[dict]:
    settings = list_settings(db, campaign_id)
    events = db.scalars(
        select(CampaignEvent).where(CampaignEvent.campaign_id == campaign_id)
        .order_by(CampaignEvent.created_at.desc()).limit(50)
    ).all()
    suggestions = []
    contradiction_terms = ("不再", "摧毁", "死亡", "倒塌", "betrayed", "destroyed", "died", "no longer")
    for setting in settings:
        for event in events:
            combined = f"{event.content} {(event.event_metadata or {}).get('dm_response', '')}"
            if setting.name.casefold() in combined.casefold() and any(term in combined.casefold() for term in contradiction_terms):
                suggestions.append({
                    "setting_id": setting.id, "setting_name": setting.name, "event_id": event.id,
                    "reason": "Recent event may invalidate or change this published setting.",
                })
    return suggestions


def suggest_setting_updates_for_event(db: Session, event: CampaignEvent) -> list[CampaignSettingDraft]:
    if event.event_type != "player_action":
        return []
    combined = f"{event.content} {(event.event_metadata or {}).get('dm_response', '')}"
    lowered = combined.casefold()
    terms = ("不再", "摧毁", "死亡", "倒塌", "betrayed", "destroyed", "died", "no longer")
    if not any(term in lowered for term in terms):
        return []
    suggestions = []
    for setting in list_settings(db, event.campaign_id):
        if setting.name.casefold() not in lowered:
            continue
        existing = db.scalar(select(CampaignSettingDraft).where(
            CampaignSettingDraft.campaign_id == event.campaign_id,
            CampaignSettingDraft.target_setting_id == setting.id,
            CampaignSettingDraft.status == "pending",
            CampaignSettingDraft.reason == f"event_conflict:{event.id}",
        ))
        if not existing:
            existing = create_draft(
                db, event.campaign_id, "update",
                {"category": setting.category, "name": setting.name, "content": {
                    "update_suggestion": combined[:2000], "source_event_id": event.id,
                }},
                event.session_id, target_setting_id=setting.id, reason=f"event_conflict:{event.id}",
            )
        suggestions.append(existing)
    return suggestions


def import_campaign_package(db: Session, campaign_id: str, package: dict, actor_id: str | None = None) -> dict:
    if package.get("format") != "dnd-dm-agent-campaign-package-v1":
        raise ValueError("Unsupported campaign package format")
    created = 0
    for item in package.get("settings", []):
        proposal = {key: item.get(key) for key in (
            "category", "name", "summary", "content", "visibility", "tags", "relationships"
        )}
        create_draft(db, campaign_id, "create", proposal, actor_id=actor_id, reason="campaign package import")
        created += 1
    return {"drafts_created": created}


def setting_to_npc_character(db: Session, setting: CampaignSetting) -> Character:
    content = setting.content or {}
    existing = db.scalar(select(Character).where(
        Character.campaign_id == setting.campaign_id, Character.character_name == setting.name,
    ))
    if existing:
        return existing
    abilities = content.get("abilities") or {"str": 10, "dex": 10, "con": 10, "int": 10, "wis": 10, "cha": 10}
    character = Character(
        id=uid("char"), campaign_id=setting.campaign_id, player_name="DM", character_name=setting.name,
        data={
            "basic": {"name": setting.name, "actor_type": content.get("actor_type", setting.category)},
            "abilities": abilities,
            "combat": content.get("combat") or {"armor_class": 10, "max_hp": 1, "current_hp": 1, "initiative": 0},
            "inventory": content.get("inventory") or [],
            "roleplay": content.get("roleplay") or {
                "public_persona": setting.summary, "voice": "", "mannerisms": [], "goals": [], "fears": [],
                "secrets": [], "knowledge": [], "attitude": "", "roleplay_instructions": "",
            },
            "story_role": content.get("story_role") or {
                "purpose": "", "planned_actions": [], "triggers": [], "relationships": setting.relationships,
            },
            "encounter": content.get("encounter") or {"present": False, "scene": ""},
            "conditions": [],
            "notes": {"campaign_setting_id": setting.id, "summary": setting.summary},
        },
    )
    db.add(character)
    db.commit()
    return character
