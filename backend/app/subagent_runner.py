from __future__ import annotations

import copy
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select

from app.campaign_editor import serialize as editor_serialize
from app.db.database import SessionLocal
from app.db.models import Campaign, CampaignSettingDraft, Character, TaskSession
from app.llm import chat_completion


EXECUTOR = ThreadPoolExecutor(max_workers=2, thread_name_prefix="dm-subagent")


def enqueue_subagent_task(task_id: str) -> None:
    EXECUTOR.submit(run_subagent_task, task_id)


def run_subagent_task(task_id: str) -> None:
    with SessionLocal() as db:
        task = db.get(TaskSession, task_id)
        if not task or task.task_type != "subagent_proposal":
            return
        if task.status not in {"queued", "ready_to_review", "ready_to_commit"}:
            return
        task.status = "running"
        db.commit()
        try:
            campaign = db.get(Campaign, task.campaign_id)
            if not campaign:
                raise ValueError("campaign not found")
            proposal_data = copy.deepcopy(task.proposal_data or {})
            role = str(proposal_data.get("agent_role") or "")
            if role == "campaign_setting_reviewer":
                result = review_campaign_setting_drafts(db, campaign, proposal_data)
            elif role == "character_sheet_reviewer":
                result = review_character_sheet(db, campaign, proposal_data)
            elif role == "character_completer":
                result = complete_character_sheet(db, campaign, proposal_data)
            elif role == "bulk_character_from_setting":
                result = bulk_character_from_settings(db, campaign, proposal_data)
            elif role == "npc_set_generator":
                result = generate_npc_batch(db, campaign, proposal_data)
            elif role == "campaign_compressor":
                result = compress_campaign_events(db, campaign, proposal_data)
            else:
                result = generic_review(campaign, proposal_data)
            proposal_data["result"] = result
            proposal_data["completed_at"] = datetime.now(UTC).isoformat()
            parent = db.get(TaskSession, task.parent_task_id) if task.parent_task_id else None
            current_version = ((parent.draft_data or {}).get("_meta") or {}).get("version", 0) if parent else 0
            source_version = proposal_data.get("source_parent_version", 0)
            proposal_data["current_parent_version"] = current_version
            proposal_data["stale"] = bool(parent and current_version != source_version)
            task.proposal_data = proposal_data
            task.status = "ready_to_review"
            task.next_prompt = "子任务已完成，请审核结果。"
            db.commit()
        except Exception as exc:
            task = db.get(TaskSession, task_id)
            if task:
                data = copy.deepcopy(task.proposal_data or {})
                data["error"] = str(exc)
                data["completed_at"] = datetime.now(UTC).isoformat()
                task.proposal_data = data
                task.status = "failed"
                task.next_prompt = "子任务执行失败，请查看 error。"
                db.commit()


def review_campaign_setting_drafts(db, campaign: Campaign, proposal_data: dict[str, Any]) -> dict[str, Any]:
    proposal = proposal_data.get("proposal") or {}
    draft_ids = [str(item) for item in proposal.get("draft_ids") or []]
    drafts = [
        item for item in db.scalars(select(CampaignSettingDraft).where(
            CampaignSettingDraft.campaign_id == campaign.id,
            CampaignSettingDraft.id.in_(draft_ids),
        )).all()
    ]
    draft_payload = [editor_serialize(item) for item in drafts]
    llm_review = chat_completion([
        {
            "role": "system",
            "content": (
                "You are a background campaign setting reviewer. "
                "Review drafts for consistency, missing details, useful NPC hooks, and publication risks. "
                "Return concise Chinese notes."
            ),
        },
        {"role": "user", "content": f"Campaign: {campaign.name}\nDrafts: {draft_payload}"},
    ], temperature=0.2)
    fallback_notes = [
        f"草稿 {item.name or item.target_setting_id or item.id}：检查名称、可见性、关系引用与摘要是否足够发布。"
        for item in drafts
    ]
    return {
        "kind": "campaign_setting_review",
        "draft_ids": draft_ids,
        "summary": llm_review or "已完成设定草稿后台审核。",
        "recommendations": fallback_notes,
        "blocking_issues": [],
    }


def review_character_sheet(db, campaign: Campaign, proposal_data: dict[str, Any]) -> dict[str, Any]:
    character_id = str((proposal_data.get("proposal") or {}).get("character_id") or "")
    character = db.get(Character, character_id) if character_id else None
    if not character or character.campaign_id != campaign.id:
        raise ValueError("character not found")
    data = character.data or {}
    llm_review = chat_completion([
        {
            "role": "system",
            "content": (
                "You are a background DND character sheet reviewer. "
                "Check required mechanical fields, inventory structure, class/ability consistency, and missing play notes. "
                "Return concise Chinese notes."
            ),
        },
        {"role": "user", "content": f"Campaign: {campaign.name}\nCharacter: {character.character_name}\nSheet: {data}"},
    ], temperature=0.2)
    combat = data.get("combat") or {}
    abilities = data.get("abilities") or {}
    issues = []
    for field in ("armor_class", "max_hp", "current_hp", "proficiency_bonus"):
        if combat.get(field) is None:
            issues.append(f"combat.{field} 未填写。")
    for ability in ("str", "dex", "con", "int", "wis", "cha"):
        if abilities.get(ability) is None:
            issues.append(f"abilities.{ability} 未填写。")
    return {
        "kind": "character_sheet_review",
        "character_id": character.id,
        "summary": llm_review or f"已完成 {character.character_name} 的角色卡后台审核。",
        "recommendations": [
            "确认 AC、HP、熟练加值、属性调整值、物品结构和法术列表是否符合当前等级。",
            "如用于战斗，请确认 active_effects 与 inventory.effects 可被效果引擎读取。",
        ],
        "blocking_issues": issues,
    }


def compress_campaign_events(db, campaign: Campaign, proposal_data: dict[str, Any]) -> dict[str, Any]:
    """Background subagent: compress old events into a summary and archive memories."""
    from app.memory_compressor import COMPRESS_EVERY, get_uncompressed_events, mark_compressed, archive_old_memories, compress_with_llm

    events = get_uncompressed_events(db, campaign.id, COMPRESS_EVERY)
    if not events:
        return {"kind": "campaign_compression", "summary": "无可压缩事件。", "compressed": 0}
    summary = compress_with_llm(events, campaign.name or "")
    mark_compressed(db, events)
    archived = archive_old_memories(db, campaign.id)
    return {
        "kind": "campaign_compression",
        "summary": summary,
        "compressed_events": len(events),
        "archived_memories": archived,
    }


def complete_character_sheet(db, campaign: Campaign, proposal_data: dict[str, Any]) -> dict[str, Any]:
    """Background subagent: fill missing equipment/skills/spells for a character."""
    char_id = str((proposal_data.get("proposal") or {}).get("character_id") or "")
    character = db.get(Character, char_id) if char_id else None
    if not character or character.campaign_id != campaign.id:
        raise ValueError("character not found")
    char_data = character.data or {}
    basic = char_data.get("basic", {})
    abilities = char_data.get("abilities", {})

    # Build a rich prompt for the LLM
    context = {
        "name": character.character_name,
        "class": basic.get("classes", [{}])[0].get("name", "?"),
        "level": basic.get("classes", [{}])[0].get("level", 1),
        "ancestry": basic.get("ancestry", ""),
        "background": basic.get("background", ""),
        "abilities": abilities,
        "current_inventory": [i.get("name") for i in (char_data.get("inventory") or [])[:10]],
        "current_skills": [k for k, v in (char_data.get("skills") or {}).items()
                           if isinstance(v, dict) and v.get("proficient")],
    }
    import json as _j
    llm_result = chat_completion([{
        "role": "system", "content": (
            "You are a D&D 5E character equipment designer. "
            "Based on the character's class, level, ancestry and background, "
            "suggest appropriate starting equipment (weapons, armor, gear), "
            "and note which skills should be proficient. "
            "Return a JSON with keys: inventory (list of {name,item_type,equipped,damage?,damage_type?,weight?,properties?}), "
            "skills (list of skill names that should be proficient), "
            "and notes (short Chinese description of your choices)."
        ),
    }, {"role": "user", "content": f"Character:\n{_j.dumps(context, ensure_ascii=False)}"}], temperature=0.5)
    try:
        suggestion = _j.loads(llm_result or "{}")
    except Exception:
        suggestion = {"inventory": [], "skills": [], "notes": "生成失败，请手动补全。"}

    # Write back to character data
    data = dict(char_data)
    existing_inv = list(data.get("inventory") or [])
    for item in suggestion.get("inventory") or []:
        item.setdefault("item_type", "gear")
        item.setdefault("equipped", item.get("item_type") == "weapon")
        item.setdefault("quantity", 1)
        existing_inv.append(item)
    data["inventory"] = existing_inv

    skills = dict(data.get("skills") or {})
    for sk in suggestion.get("skills") or []:
        if sk not in skills:
            skills[sk] = {"proficient": True, "expertise": False, "bonus": 0}
    data["skills"] = skills
    character.data = data
    db.commit()
    return {"kind": "character_sheet_completed", "character_id": character.id,
            "inventory_added": len(suggestion.get("inventory") or []),
            "skills_added": len(suggestion.get("skills") or []),
            "notes": suggestion.get("notes", "")}


def bulk_character_from_settings(db, campaign: Campaign, proposal_data: dict[str, Any]) -> dict[str, Any]:
    """Background subagent: create Character cards for all NPC settings."""
    from app.campaign_editor import list_settings, setting_to_npc_character
    from app.services import serialize

    settings = [s for s in list_settings(db, campaign.id) if s.category in {"npc", "monster"}]
    created = []
    total = len(settings)
    for i, setting in enumerate(settings):
        char = setting_to_npc_character(db, setting)
        created.append({"name": char.character_name, "id": char.id})
        proposal_data["progress"] = f"{i+1}/{total}"
        # Write progress so user can check mid-generation
        db.commit()
    return {
        "kind": "bulk_character_cards",
        "created": len(created),
        "total": total,
        "characters": created,
        "progress": f"{len(created)}/{total}",
    }


def generate_npc_batch(db, campaign: Campaign, proposal_data: dict[str, Any]) -> dict[str, Any]:
    """Background subagent: generate a batch of NPC settings via LLM."""
    proposal = proposal_data.get("proposal") or {}
    batch_start = int(proposal.get("batch_start", 0))
    batch_size = int(proposal.get("batch_size", 6))
    theme = str(proposal.get("theme") or campaign.description or "fantasy")
    total_count = int(proposal.get("total_count", batch_size))

    import json as _j
    llm_result = chat_completion([{
        "role": "system", "content": (
            f"You are a D&D 5E NPC designer. Generate {batch_size} unique NPCs "
            f"for a {theme} setting. Each NPC needs: name, occupation, race, "
            f"age, alignment, personality (1 sentence), appearance (1 sentence), "
            f"secret or motivation (1 sentence). "
            f"Return ONLY a JSON array of NPC objects with keys: "
            f"name, occupation, ancestry, age, alignment, personality, appearance, secret."
        ),
    }, {"role": "user", "content": f"Generate NPCs {batch_start+1}-{batch_start+batch_size} of {total_count}"}],
        temperature=0.8)

    try:
        npcs = _j.loads(llm_result or "[]")
    except Exception:
        npcs = [{"name": f"NPC {batch_start+i}", "occupation": "villager", "ancestry": "human",
                 "age": "adult", "alignment": "neutral", "personality": "ordinary",
                 "appearance": "ordinary", "secret": "none"} for i in range(1, batch_size+1)]

    from app.campaign_editor import create_draft
    from app.services import uid

    created = []
    for npc in npcs:
        draft = create_draft(db, campaign.id, "create", proposal={
            "category": "npc", "name": npc.get("name", "?"),
            "description": (
                f"职业: {npc.get('occupation','?')} | 种族: {npc.get('ancestry','?')} | "
                f"阵营: {npc.get('alignment','?')}\n"
                f"性格: {npc.get('personality','?')}\n外貌: {npc.get('appearance','?')}\n"
                f"秘密: {npc.get('secret','?')}"
            ),
        })
        created.append(draft.name)

    from app.campaign_editor import publish_drafts
    published = publish_drafts(db, campaign.id)
    return {
        "kind": "npc_batch_generated",
        "batch": f"{batch_start+1}-{min(batch_start+batch_size, total_count)}",
        "created": len(published),
        "names": created,
    }


def generic_review(campaign: Campaign, proposal_data: dict[str, Any]) -> dict[str, Any]:
    return {
        "kind": "generic_subagent_review",
        "summary": f"已接收战役“{campaign.name}”的后台子任务。",
        "recommendations": [str(proposal_data.get("goal") or "请审核该子任务输出。")],
        "blocking_issues": [],
    }
