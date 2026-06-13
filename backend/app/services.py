import copy
import hashlib
import json
import re
from pathlib import Path
from uuid import uuid4

from sqlalchemy import delete, inspect, select, text
from sqlalchemy.orm import Session

from app.config import settings
from app.agents.dm_graph import dm_graph
from app.db.models import (Campaign, CampaignEvent, CampaignSummary, Character,
                           CharacterChange, CompendiumEntry, RuleChunk)
from app.campaign_memory import build_memory_package, index_event_memory
from app.rag.chunker import chunk_markdown
from app.rag.embedder import embed_text, embed_texts
from app.llm import chat_completion
from app.tools.dice import roll_dice, roll_with_advantage
from app.tools.spell_catalog import search_spells
from app.tools.item_schema import CurrencyWallet, normalize_inventory
from app.campaign_editor import search_settings, suggest_setting_updates_for_event
from app.actor_manager import is_dm_actor, is_present, roleplay_brief


def uid(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:12]}"


def serialize(obj):
    return {
        prop.columns[0].name: getattr(obj, prop.key)
        for prop in inspect(obj).mapper.column_attrs
    }


def merge_dict(base: dict, patch: dict) -> dict:
    result = copy.deepcopy(base)
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = merge_dict(result[key], value)
        else:
            result[key] = value
    return result


def update_character(db: Session, character: Character, patch: dict, reason: str,
                     change_type: str = "character_update", rule_refs: list[str] | None = None) -> CharacterChange:
    before = copy.deepcopy(character.data)
    patch = copy.deepcopy(patch)
    if "inventory" in patch:
        patch["inventory"] = normalize_inventory(patch["inventory"])
    if "currency" in patch:
        patch["currency"] = CurrencyWallet.model_validate(patch["currency"] or {}).model_dump(mode="json")
    after = merge_dict(before, patch)
    character.data = after
    character.version += 1
    change = CharacterChange(
        id=uid("chg"), campaign_id=character.campaign_id, character_id=character.id,
        change_type=change_type, before_data=before, after_data=after, reason=reason,
        rule_refs=rule_refs or [],
    )
    db.add(change)
    db.commit()
    return change


def append_event(db: Session, campaign_id: str, session_id: str | None, event_type: str,
                 content: str, actors: list[str], metadata: dict, visibility: str = "party",
                 memory_plan: dict | None = None) -> CampaignEvent:
    event = CampaignEvent(id=uid("evt"), campaign_id=campaign_id, session_id=session_id,
                          event_type=event_type, content=content, actors=actors,
                          visibility=visibility, event_metadata=metadata)
    db.add(event)
    db.commit()
    index_event_memory(db, event, memory_plan)
    suggest_setting_updates_for_event(db, event)
    return event


def ingest_compendium(db: Session, data_dir: Path | None = None) -> int:
    count = 0
    root = (data_dir or settings.data_dir) / "compendium"
    for path in root.glob("*.json"):
        for raw in json.loads(path.read_text(encoding="utf-8")):
            entry = db.get(CompendiumEntry, raw["id"]) or CompendiumEntry(id=raw["id"])
            entry.entry_type, entry.name, entry.data = raw["entry_type"], raw["name"], raw["data"]
            entry.source, entry.system_version = raw.get("source"), raw.get("system_version")
            db.add(entry)
            count += 1
    db.commit()
    return count


def ingest_rules(db: Session, data_dir: Path | None = None) -> int:
    count = 0
    root = (data_dir or settings.data_dir) / "rules"
    for path in root.glob("*.md"):
        count += ingest_rule_content(db, path.read_text(encoding="utf-8"), path.stem, replace=True)
    return count


def ingest_rule_content(
    db: Session,
    content: str,
    source: str,
    system_version: str = "DND_5E_2014",
    metadata: dict | None = None,
    replace: bool = True,
) -> int:
    source = source.strip() or "uploaded_rulebook"
    if replace:
        db.execute(delete(RuleChunk).where(RuleChunk.source == source))
    count = 0
    raws = chunk_markdown(content, source)
    try:
        embeddings: list[list[float] | None] = list(embed_texts([raw["chunk_text"] for raw in raws]))
    except Exception:
        embeddings = [None] * len(raws)
    for index, raw in enumerate(raws):
        digest = hashlib.sha1(raw["chunk_text"].encode("utf-8")).hexdigest()[:12]
        chunk_id = f"rule_{hashlib.sha1(source.encode('utf-8')).hexdigest()[:10]}_{index:05d}_{digest}"
        embedding = embeddings[index]
        chunk = RuleChunk(
            id=chunk_id, source=source, chapter=raw["chapter"], section=raw["section"],
            system_version=system_version, chunk_text=raw["chunk_text"],
            embedding=embedding, chunk_metadata=metadata or {},
        )
        db.add(chunk)
        db.flush()
        if embedding and db.bind and db.bind.dialect.name == "postgresql":
            vector = "[" + ",".join(str(value) for value in embedding) + "]"
            db.execute(text("UPDATE rule_chunks SET embedding_vector = CAST(:vector AS vector) WHERE id = :id"),
                       {"vector": vector, "id": chunk_id})
        count += 1
    db.commit()
    return count


def search_rules(db: Session, query: str, limit: int = 8) -> list[dict]:
    query_embedding = embed_text(query)
    if query_embedding and db.bind and db.bind.dialect.name == "postgresql":
        vector = "[" + ",".join(str(value) for value in query_embedding) + "]"
        rows = db.execute(text("""
            SELECT id, source, chapter, section, chunk_text,
                   1 - (embedding_vector <=> CAST(:vector AS vector)) AS score
            FROM rule_chunks WHERE embedding_vector IS NOT NULL
            ORDER BY embedding_vector <=> CAST(:vector AS vector) LIMIT :limit
        """), {"vector": vector, "limit": limit}).mappings()
        return [dict(row) for row in rows]
    if query_embedding:
        chunks = db.scalars(select(RuleChunk)).all()
        semantic = []
        for chunk in chunks:
            if not chunk.embedding or len(chunk.embedding) != len(query_embedding):
                continue
            score = sum(a * b for a, b in zip(query_embedding, chunk.embedding, strict=True))
            semantic.append((score, chunk))
        if semantic:
            semantic.sort(key=lambda item: item[0], reverse=True)
            return [{**serialize(chunk), "score": score} for score, chunk in semantic[:limit]]
    terms = set(re.findall(r"[\w\u4e00-\u9fff]+", query.lower()))
    chunks = db.scalars(select(RuleChunk)).all()
    ranked = []
    for chunk in chunks:
        haystack = f"{chunk.chapter} {chunk.section} {chunk.chunk_text}".lower()
        score = sum(haystack.count(term) for term in terms)
        if score:
            ranked.append((score, chunk))
    ranked.sort(key=lambda item: item[0], reverse=True)
    return [{**serialize(chunk), "score": score} for score, chunk in ranked[:limit]]


def ability_modifier(score: int) -> int:
    return (score - 10) // 2


def build_dm_context(
    db: Session,
    campaign_id: str,
    session_id: str | None,
    character: Character | None,
    message: str,
) -> tuple[str, dict]:
    campaign = db.get(Campaign, campaign_id)
    summary_query = select(CampaignSummary).where(CampaignSummary.campaign_id == campaign_id)
    if session_id:
        summary_query = summary_query.where(
            (CampaignSummary.scope_id == session_id) | (CampaignSummary.scope_id.is_(None))
        )
    summaries = db.scalars(summary_query.order_by(CampaignSummary.updated_at.desc()).limit(2)).all()

    campaign_events = db.scalars(
        select(CampaignEvent)
        .where(CampaignEvent.campaign_id == campaign_id)
        .order_by(CampaignEvent.created_at.desc())
        .limit(12)
    ).all()
    session_events = []
    if session_id:
        session_events = db.scalars(
            select(CampaignEvent)
            .where(CampaignEvent.campaign_id == campaign_id, CampaignEvent.session_id == session_id)
            .order_by(CampaignEvent.created_at.desc())
            .limit(12)
        ).all()
    by_id = {item.id: item for item in [*campaign_events, *session_events]}
    events = sorted(by_id.values(), key=lambda item: item.created_at)[-16:]
    rules = search_rules(db, message, 3)
    spells = search_spells(message, settings.data_dir, 3)
    memory_package = build_memory_package(db, campaign_id, message, session_id)
    campaign_settings = search_settings(db, campaign_id, message, 5)
    dm_actors = [
        roleplay_brief(item)
        for item in db.scalars(select(Character).where(Character.campaign_id == campaign_id)).all()
        if is_dm_actor(item) and is_present(item)
    ]
    context = {
        "campaign": serialize(campaign) if campaign else None,
        "character": serialize(character) if character else None,
        "summaries": [
            {"scope": item.scope, "scope_id": item.scope_id, "summary": item.summary, "open_threads": item.open_threads}
            for item in summaries
        ],
        "recent_events": [
            {
                "type": item.event_type,
                "content": item.content,
                "actors": item.actors,
                "dm_response": item.event_metadata.get("dm_response", ""),
            }
            for item in events
        ],
        "structured_memory": memory_package,
        "relevant_campaign_settings": [
            {
                "id": item.id, "category": item.category, "name": item.name, "summary": item.summary,
                "content": item.content, "visibility": item.visibility, "relationships": item.relationships,
            }
            for item in campaign_settings
        ],
        "present_dm_actors": dm_actors,
        "relevant_rules": [
            {
                "source": item.get("source"),
                "chapter": item.get("chapter"),
                "section": item.get("section"),
                "text": str(item.get("chunk_text", ""))[:1200],
                "score": item.get("score"),
            }
            for item in rules
        ],
        "relevant_spells": [
            {
                "id": item.get("id"),
                "name": item.get("name"),
                "english_name": item.get("english_name"),
                "level": item.get("level"),
                "school": item.get("school"),
                "casting_time": item.get("casting_time"),
                "range": item.get("range"),
                "duration": item.get("duration"),
                "classes": item.get("classes"),
                "description": str(item.get("description", ""))[:1600],
            }
            for item in spells
        ],
    }
    prompt = (
        "Use this canonical campaign context when resolving the player's action. "
        "Respect established events and character state. Rule excerpts are references. "
        "Do not invent mechanical state changes.\n\n"
        + json.dumps(context, ensure_ascii=False, default=str)
    )
    refs = {
        "summary_ids": [item.id for item in summaries],
        "event_ids": [item.id for item in events],
        "memory_ids": [item["id"] for item in memory_package["memories"]],
        "entity_ids": [item["id"] for item in memory_package["entities"]],
        "thread_ids": [item["id"] for item in memory_package["threads"]],
        "rule_chunk_ids": [item.get("id") for item in rules if item.get("id")],
        "spell_ids": [item.get("id") for item in spells if item.get("id")],
        "campaign_setting_ids": [item.id for item in campaign_settings],
        "present_dm_actor_ids": [item["id"] for item in dm_actors],
        "character_id": character.id if character else None,
        "character_version": character.version if character else None,
    }
    return prompt, refs


def resolve_chat(db: Session, campaign_id: str, session_id: str | None, character_id: str | None,
                 message: str) -> dict:
    character = db.get(Character, character_id) if character_id else None
    context_prompt, context_refs = build_dm_context(db, campaign_id, session_id, character, message)
    memory_package = build_memory_package(db, campaign_id, message, session_id)
    graph_state = dm_graph.invoke({
        "user_message": message,
        "dm_context": context_prompt,
        "memory_context": memory_package,
        "errors": [],
    })
    text = message.lower()
    rolls, changes = [], []
    actors = [character_id] if character_id else []

    is_potion = any(word in text for word in ["potion", "药水", "治療藥水", "治疗药水"])
    is_social = any(word in text for word in ["说服", "說服", "persuade", "persuasion", "交涉"])
    is_rest = any(word in text for word in ["long rest", "长休", "長休", "short rest", "短休"])

    if is_potion and character:
        inventory = character.data.get("inventory", [])
        potion = next((x for x in inventory if x.get("item_id") == "potion_healing" and x.get("quantity", 0) > 0), None)
        if not potion:
            narration = "你翻遍背包，却没有找到可用的治疗药水。"
        else:
            roll = roll_dice("2d4+2")
            before_hp = character.data.get("combat", {}).get("current_hp", 0)
            max_hp = character.data.get("combat", {}).get("max_hp", before_hp)
            after_hp = min(max_hp, before_hp + roll["total"])
            new_inventory = copy.deepcopy(inventory)
            next(x for x in new_inventory if x.get("item_id") == "potion_healing")["quantity"] -= 1
            update_character(db, character, {"combat": {"current_hp": after_hp}, "inventory": new_inventory},
                             "drank a Potion of Healing", "consume_item", ["potion_healing"])
            rolls.append(roll)
            changes = [{"type": "hp_change", "before": before_hp, "after": after_hp},
                       {"type": "inventory_change", "item_id": "potion_healing", "delta": -1}]
            narration = f"你饮下猩红药液，暖意迅速漫过伤口。治疗 {roll['total']} 点，生命值从 {before_hp}/{max_hp} 恢复到 {after_hp}/{max_hp}。"
    elif is_social:
        bonus = 0
        if character:
            charisma = character.data.get("abilities", {}).get("cha", 10)
            proficient = character.data.get("skills", {}).get("persuasion", {}).get("proficient", False)
            bonus = ability_modifier(charisma) + (character.data.get("combat", {}).get("proficiency_bonus", 2) if proficient else 0)
        roll = roll_dice(f"1d20{bonus:+d}")
        rolls.append(roll)
        dc = 13
        success = roll["total"] >= dc
        narration = (f"对方认真听完了你的陈述。Persuasion 检定 {roll['total']}，DC {dc}。"
                     + ("他的态度缓和下来，愿意给你一个机会。" if success else "他仍保持警惕，暂时没有让步。"))
    elif is_rest and character:
        combat = character.data.get("combat", {})
        before_hp, max_hp = combat.get("current_hp", 0), combat.get("max_hp", 0)
        update_character(db, character, {"combat": {"current_hp": max_hp}}, "completed a rest", "rest")
        changes = [{"type": "hp_change", "before": before_hp, "after": max_hp}]
        narration = f"休整结束，你重新振作起来。生命值恢复至 {max_hp}/{max_hp}。"
    else:
        narration = chat_completion([
            {"role": "system", "content": (
                "You are a concise DND Dungeon Master. Continue from established campaign memory. "
                "Use the current character sheet and relevant rules. Do not invent mechanical state changes."
            )},
            {"role": "system", "content": context_prompt},
            {"role": "user", "content": message},
        ]) or "你的行动让局势继续向前推进。四周的目光落在你身上，等待你作出下一步选择。"

    metadata = {"raw_player_input": message, "dm_response": narration, "rolls": rolls, "state_changes": changes,
                "intent": graph_state.get("intent"), "ruling": graph_state.get("ruling"),
                "proposed_actions": graph_state.get("proposed_actions"), "context_refs": context_refs}
    event = append_event(
        db, campaign_id, session_id, "player_action", message, actors, metadata,
        memory_plan=graph_state.get("memory_write_plan"),
    )
    return {"campaign_id": campaign_id, "message": message, "narration": narration,
            "rolls": rolls, "state_changes": changes, "events": [serialize(event)]}


def create_summary(db: Session, campaign_id: str, session_id: str | None) -> CampaignSummary:
    query = select(CampaignEvent).where(CampaignEvent.campaign_id == campaign_id)
    if session_id:
        query = query.where(CampaignEvent.session_id == session_id)
    events = db.scalars(query.order_by(CampaignEvent.created_at)).all()
    lines = [f"- {e.content}: {e.event_metadata.get('dm_response', '')}" for e in events[-20:]]
    summary = CampaignSummary(id=uid("sum"), campaign_id=campaign_id, scope="session" if session_id else "campaign",
                              scope_id=session_id, summary="\n".join(lines) or "本次尚无事件。", open_threads=[])
    db.add(summary)
    db.commit()
    return summary
