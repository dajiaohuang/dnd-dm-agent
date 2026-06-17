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
from app.services import uid


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
            elif role == "character_sheet_completer":
                result = complete_character_sheet(db, campaign, proposal_data)
            elif role == "content_writer":
                result = generate_content(db, campaign, proposal_data)
            elif role == "plan_runner":
                result = run_plan(db, campaign, proposal_data)
            elif role == "campaign_compressor":
                result = compress_campaign_events(db, campaign, proposal_data)
            else:
                result = {"kind": "unknown_role", "error": f"unknown agent_role: {role}"}
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
    """Background subagent: fill/refine character sheet — equipment, backstory, or appearance."""
    char_id = str((proposal_data.get("proposal") or {}).get("character_id") or "")
    focus = str((proposal_data.get("proposal") or {}).get("focus") or "all")
    character = db.get(Character, char_id) if char_id else None
    if not character or character.campaign_id != campaign.id:
        raise ValueError("character not found")
    char_data = character.data or {}
    basic = char_data.get("basic", {})
    import json as _j
    result = {"kind": "character_sheet_completed", "character_id": character.id}
    data = dict(char_data)

    # ── Equipment ──
    if focus in ("equipment", "all"):
        context = {
            "name": character.character_name,
            "class": basic.get("classes", [{}])[0].get("name", "?"),
            "level": basic.get("classes", [{}])[0].get("level", 1),
            "ancestry": basic.get("ancestry", ""),
            "background": basic.get("background", ""),
            "abilities": char_data.get("abilities", {}),
            "current_inventory": [i.get("name") for i in (char_data.get("inventory") or [])[:10]],
            "current_skills": [k for k, v in (char_data.get("skills") or {}).items()
                               if isinstance(v, dict) and v.get("proficient")],
        }
        llm_result = chat_completion([{
            "role": "system", "content": (
                "You are a D&D 5E character equipment designer. "
                "Suggest weapons, armor, gear. Return JSON: {inventory:[{name,item_type,equipped,damage?,damage_type?,weight?}], skills:[], notes:\"\"}"
            ),
        }, {"role": "user", "content": f"Character:\n{_j.dumps(context, ensure_ascii=False)}"}], temperature=0.5)
        try:
            sug = _j.loads(llm_result or "{}")
        except Exception:
            sug = {}
        existing_inv = list(data.get("inventory") or [])
        for item in sug.get("inventory") or []:
            item.setdefault("item_type", "gear"); item.setdefault("equipped", True); item.setdefault("quantity", 1)
            existing_inv.append(item)
        data["inventory"] = existing_inv
        skills = dict(data.get("skills") or {})
        for sk in sug.get("skills") or []:
            if sk not in skills: skills[sk] = {"proficient": True, "expertise": False, "bonus": 0}
        data["skills"] = skills
        result["inventory_added"] = len(sug.get("inventory") or [])
        result["skills_added"] = len(sug.get("skills") or [])

    # ── Backstory ──
    if focus in ("backstory", "all"):
        context = {
            "name": character.character_name,
            "class": basic.get("classes", [{}])[0].get("name", "?"),
            "ancestry": basic.get("ancestry", ""),
            "background": basic.get("background", ""),
            "alignment": basic.get("alignment", ""),
            "traits": (char_data.get("personality") or {}).get("traits", ""),
            "campaign": campaign.name if campaign else "",
        }
        story = chat_completion([{
            "role": "system", "content": "Write a 200-word character backstory in Chinese. Return ONLY the story text, no JSON."
        }, {"role": "user", "content": _j.dumps(context, ensure_ascii=False)}], temperature=0.8)
        personality = dict(data.get("personality") or {})
        personality["backstory"] = (story or "").strip()
        data["personality"] = personality
        result["backstory_length"] = len(personality["backstory"])

    # ── Appearance ──
    if focus in ("appearance", "all"):
        context = {
            "name": character.character_name,
            "ancestry": basic.get("ancestry", ""),
            "class": basic.get("classes", [{}])[0].get("name", "?"),
            "gender": basic.get("gender", ""),
            "age": basic.get("age", ""),
            "existing": basic.get("appearance", ""),
        }
        appearance = chat_completion([{
            "role": "system", "content": "Describe this character's appearance in Chinese: hair, eyes, height, build, clothing style. 2-3 sentences. Return ONLY the description."
        }, {"role": "user", "content": _j.dumps(context, ensure_ascii=False)}], temperature=0.8)
        data["basic"] = dict(data.get("basic") or {})
        data["basic"]["appearance"] = (appearance or "").strip()
        result["appearance_length"] = len(data["basic"]["appearance"])

    character.data = data
    db.commit()
    result["focus"] = focus
    return result
def generate_content(db, campaign: Campaign, proposal_data: dict[str, Any]) -> dict[str, Any]:
    """Unified content writer subagent — dispatches by content type."""
    proposal = proposal_data.get("proposal") or {}
    ctype = str(proposal.get("type", "npc"))
    theme = str(proposal.get("theme") or campaign.name or "")
    count = int(proposal.get("count", 1))
    prompt = str(proposal.get("prompt") or "")

    import json as _j

    # ── NPC (always create settings + character cards) ──
    if ctype == "npc":
        batch_start = int(proposal.get("batch_start", 0))
        batch_size = int(proposal.get("batch_size", 6))
        total_count = int(proposal.get("total_count", batch_size))
        create_cards = proposal.get("create_cards", True)  # default True
        llm_result = chat_completion([{
            "role": "system", "content": (
                f"Generate {batch_size} unique NPCs for a {theme} setting. Each needs: "
                f"name, occupation, ancestry, age, alignment, personality (1 sentence), "
                f"appearance (1 sentence), secret (1 sentence). "
                f"Return ONLY a JSON array with keys: name, occupation, ancestry, age, alignment, personality, appearance, secret."
            ),
        }, {"role": "user", "content": f"NPCs {batch_start+1}-{batch_start+batch_size} of {total_count}"}],
            temperature=0.8)
        try:
            npcs = _j.loads(llm_result or "[]")
        except Exception:
            npcs = []
        from app.campaign_editor import create_draft as _cd, publish_drafts as _pd, setting_to_npc_character as _s2c, list_settings as _ls
        for npc in npcs:
            desc = (
                f"职业: {npc.get('occupation','?')} | 种族: {npc.get('ancestry','?')} | "
                f"阵营: {npc.get('alignment','?')}\n性格: {npc.get('personality','?')}\n"
                f"外貌: {npc.get('appearance','?')}\n秘密: {npc.get('secret','?')}"
            )
            _cd(db, campaign.id, "create", proposal={"category": "npc", "name": npc.get("name", "?"), "description": desc})
        published = _pd(db, campaign.id)
        card_names = []
        if create_cards:
            for s in _ls(db, campaign.id):
                if s.category == "npc":
                    ch = _s2c(db, s)
                    if ch: card_names.append(ch.character_name)
        return {
            "kind": "content_generated", "type": "npc",
            "batch": f"{batch_start+1}-{min(batch_start+batch_size, total_count)}",
            "settings": len(published), "cards": len(card_names),
        }

    # ── Settings (location/faction/item/event) ──
    if ctype in {"location", "faction", "item", "event"}:
        cat_desc = {
            "location": "a location with geography, key NPCs, history, secrets",
            "faction": "a faction with leader, goals, members, rivals, secrets",
            "item": "a magic item with type, rarity, attunement, powers",
            "event": "a campaign event with timeline, key NPCs, consequences",
        }.get(ctype, "a detailed setting")
        llm_result = chat_completion([{
            "role": "system", "content": (
                f"Generate {count} {ctype} setting(s) for a {theme} campaign. "
                f"Each: {cat_desc}. Return ONLY a JSON array with keys: name, description."
            ),
        }, {"role": "user", "content": prompt or f"Generate {count} {ctype} for {theme}"}],
            temperature=0.8)
        try:
            items = _j.loads(llm_result or "[]")
        except Exception:
            items = [{"name": f"{theme} {ctype}", "description": "Generated."}]
        from app.campaign_editor import create_draft as _cd2, publish_drafts as _pd2
        for item in items:
            _cd2(db, campaign.id, "create", proposal={
                "category": ctype, "name": item.get("name", "?"),
                "description": item.get("description", ""),
            })
        published = _pd2(db, campaign.id)
        return {"kind": "content_generated", "type": ctype, "count": len(published),
                "names": [s.name for s in published]}

    # ── Quest/encounter/loot/rumor/recap/prep (pure text output) ──
    type_guides = {
        "quest": "a multi-step quest with objectives, rewards, and NPCs involved",
        "encounter": "a combat encounter with CR, terrain, monsters, tactics, loot",
        "loot": "a treasure hoard with magic items, gold, and descriptions",
        "rumor": "a list of rumors NPCs might share about the area",
        "recap": "a session recap in narrative form (past tense, summary style)",
        "prep": "a DM session prep covering opening scene, key scenes, NPC timing, transitions",
    }
    guide = type_guides.get(ctype, "well-structured content")
    llm_result = chat_completion([{
        "role": "system", "content": (
            f"You are a D&D 5E DM assistant. Generate {count} {ctype}(s) "
            f"for a {theme} campaign. {guide}. "
            f"Return ONLY the generated content in markdown format."
        ),
    }, {"role": "user", "content": prompt or f"Generate {count} {ctype} for {theme}"}],
        temperature=0.8)
    return {
        "kind": "content_generated", "type": ctype,
        "content": llm_result or "生成失败。",
    }


# ═══════════════════════════════════════════════════════════════════
#  PLAN RUNNER — Coordinated multi-step task execution
# ═══════════════════════════════════════════════════════════════════

import time as _time
import json as _plan_json

_PLAN_POLL_SLEEP = 2  # seconds between dependency checks


def run_plan(db, campaign: Campaign, proposal_data: dict[str, Any]) -> dict[str, Any]:
    """Coordinator subagent: execute plan steps synchronously in sequence.

    Each step runs in the plan runner's own thread — no sub-subagent enqueuing.
    Depends-on ordering: steps with unmet deps are skipped until later passes.
    """
    from app.tools.command_tools import TOOL_HANDLERS
    proposal = proposal_data.get("proposal") or {}
    plan = proposal.get("plan") or {}
    steps = plan.get("steps") or []
    if not steps:
        return {"kind": "plan_completed", "summary": "no steps", "steps": 0}

    step_results: dict[str, dict] = {}
    step_defs: dict[str, dict] = {str(s.get("id","")): s for s in steps if s.get("id")}
    remaining = set(step_defs.keys())
    max_rounds = len(steps) * 3

    for _pass in range(max_rounds):
        if not remaining:
            break
        # Find steps whose deps are all resolved
        ready = []
        for sid in list(remaining):
            deps = step_defs[sid].get("depends_on") or []
            if all(d in step_results for d in deps):
                ready.append(sid)
        if not ready:
            _time.sleep(_PLAN_POLL_SLEEP)
            continue

        for sid in ready:
            s = step_defs[sid]
            # Resolve args from previous step outputs
            args = dict(s.get("args") or {})
            for dep_id in s.get("depends_on") or []:
                dep_out = step_results.get(dep_id, {})
                key = (step_defs.get(dep_id) or {}).get("output_key", "")
                if key and key in dep_out:
                    args[key] = dep_out[key]

            tool_name = str(s.get("tool", ""))
            try:
                if tool_name in TOOL_HANDLERS:
                    result = TOOL_HANDLERS[tool_name](db=db, campaign=campaign, **args)
                elif tool_name == "complete_character_sheet":
                    result = complete_character_sheet(db, campaign, {"proposal": args})
                else:
                    result = {"error": f"unknown tool: {tool_name}"}
            except Exception as exc:
                result = {"error": str(exc)}

            step_results[sid] = result
            remaining.discard(sid)
            proposal_data["progress"] = f"{len(step_results)}/{len(steps)}"
            db.commit()

    return {
        "kind": "plan_completed",
        "steps_total": len(steps),
        "steps_done": len(step_results),
        "step_results": {sid: r for sid, r in step_results.items()},
    }


def generic_review(campaign: Campaign, proposal_data: dict[str, Any]) -> dict[str, Any]:
    return {
        "kind": "generic_subagent_review",
        "summary": f"已接收战役“{campaign.name}”的后台子任务。",
        "recommendations": [str(proposal_data.get("goal") or "请审核该子任务输出。")],
        "blocking_issues": [],
    }
