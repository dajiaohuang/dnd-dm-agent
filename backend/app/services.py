import copy
import hashlib
import json
import re
from pathlib import Path
from uuid import uuid4

from sqlalchemy import delete, inspect, select, text
from sqlalchemy.orm import Session

from app.config import settings
from app.db.models import (Campaign, CampaignEvent, CampaignSummary, Character,
                           CharacterChange, CompendiumEntry, RuleChunk)
from app.campaign_memory import build_memory_package, index_event_memory
from app.rag.chunker import chunk_markdown
from app.rag.embedder import embed_text, embed_texts
from app.llm import chat_completion
from app.tools.dice import roll_dice, roll_with_advantage
from app.tools.hot_character import hot_character_for_llm
from app.tools.spell_catalog import search_spells
from app.tools.item_schema import CurrencyWallet, normalize_inventory
from app.tools.effect_engine import advance_effect_durations, normalize_effects, resolve_effective_character
from app.campaign_editor import search_settings, suggest_setting_updates_for_event
from app.actor_manager import is_dm_actor, is_present, roleplay_brief
from app.combat_preferences import combat_preference
from app.combat_reactions import (
    format_reaction_prompt, open_reaction_window, reaction_notifications, resolve_ready_reaction_window,
)


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
    if "active_effects" in patch:
        patch["active_effects"] = normalize_effects(patch["active_effects"])
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


ROLL_REQUEST_RE = re.compile(
    r"(?i)(?:请|需要|进行|作出|make|please|must|need to|roll).{0,24}"
    r"(?:掷骰|投掷|检定|豁免|攻击检定|roll|check|save|saving throw|attack roll)"
)
ROLL_FORMULA_RE = re.compile(r"(?i)(?<!\w)(\d*d\d+(?:\s*[+-]\s*\d+)?)(?!\w)")


def _requested_roll(narration: str) -> str | None:
    if not ROLL_REQUEST_RE.search(narration):
        return None
    match = ROLL_FORMULA_RE.search(narration)
    return re.sub(r"\s+", "", match.group(1)) if match else "1d20"


def _combat_output_instructions(campaign: Campaign) -> str:
    if not bool(((campaign.config or {}).get("turn_state") or {}).get("combat")):
        return ""
    roleplay = combat_preference(campaign, "roleplay")
    advice = combat_preference(campaign, "advice")
    instructions = [
        "Never ask a player to roll dice. Resolve every required roll immediately and continue the action.",
    ]
    if not roleplay:
        instructions.append(
            "Combat roleplay prose is disabled. Use a concise mechanical resolution without dialogue, "
            "cinematic narration, atmosphere, or decorative action prose."
        )
    if not advice:
        instructions.append(
            "Combat advice is disabled. Do not suggest tactics, actions, targets, or next steps."
        )
    return " ".join(instructions)


def _finish_requested_roll(
    context_prompt: str,
    message: str,
    narration: str,
    formula: str,
    output_instructions: str = "",
) -> tuple[str, dict]:
    roll = roll_dice(formula)
    continued = chat_completion([
        {
            "role": "system",
            "content": (
                "Continue and finish the pending DND action using the supplied automatic roll result. "
                "Do not ask for another player roll. Do not invent unrecorded mechanical state changes. "
                + output_instructions
            ),
        },
        {"role": "system", "content": context_prompt},
        {
            "role": "user",
            "content": (
                f"Original action: {message}\n"
                f"Pending resolution: {narration}\n"
                f"Automatic roll: {json.dumps(roll, ensure_ascii=False)}\n"
                "Finish resolving this action now."
            ),
        },
    ])
    fallback = (
        f"{narration}\n系统已自动投掷 {roll['formula']}：{roll['total']}"
        f"（{roll['rolls']}，修正 {roll['modifier']:+d}）。"
    )
    return continued or fallback, roll


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
        "character": (
            {**serialize(character), "data": resolve_effective_character(
                character.data, bool(((campaign.config or {}).get("turn_state") or {}).get("combat")),
            )}
            if character else None
        ),
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


def resolve_chat(db: Session, campaign_id: str | None, session_id: str | None, character_id: str | None,
                 message: str, *, mode: str = "dm", message_context: dict | None = None) -> dict:
    """Unified DM narrative / dice assistant / lobby LLM path.

    mode="lobby" → game-external: campaign management, character creation
    mode="dm"    → full DM: narration, NPC roleplay, combat description
    mode="dice"  → dice assistant: pure mechanics, no roleplay, no advice
    """
    campaign = db.get(Campaign, campaign_id) if campaign_id else None
    character = db.get(Character, character_id) if character_id else None
    context_prompt, context_refs = build_dm_context(db, campaign_id, session_id, character, message)
    text = message.lower()
    rolls, changes = [], []
    result_data = {}
    actors = [character_id] if character_id else []

    # ── Unified system prompt (Lobby / DM / Dice) ──
    _combat = bool(((campaign.config or {}).get("turn_state") or {}).get("combat")) if campaign else False
    _temp_map = {"lobby": 0.3, "dm": 0.7, "dice": 0.2}
    temperature = _temp_map.get(mode, 0.7)

    if mode == "lobby":
        _campaign_info = (
            f"当前选中战役: {campaign.name}（{campaign.id}）\n"
            f"简介: {campaign.description or '无'}\n"
            if campaign else
            "当前没有选中战役。用户需要先创建或选择战役后才能车卡和改设定。\n"
        )
        _campaign_ops = (
            "- 角色卡: 创建/修改/查看角色卡(默认当前战役)\n"
            "- 设定: 添加/修改/查看战役设定(当前战役)\n"
            "- 绑定导出: 绑定QQ/查看绑定/导出角色卡\n"
            if campaign else
            "（选战役后才可车卡和改设定）\n"
        )
        _attachments = (campaign.config or {}).get("last_attachments") or [] if campaign else []
        _att_info = ""
        if _attachments:
            _att_info = f"\n━━━ 最近附件 ━━━\n收到 {len(_attachments)} 个文件。"
            for i, a in enumerate(_attachments):
                meta = a.get("meta", {})
                ctype = "人物卡" if (isinstance(meta, dict) and "character_data" in meta) else "文档"
                _att_info += f"\n  [{i+1}] {ctype} ({a.get('parser','?')})"
            _att_info += "\n用户说「用刚才发的文件开卡」时，调用 read_attachment 读取。\n"
        _sys = (
            "你是 D&D 5E 跑团管理助手。当前处于「游戏外模式」——不在游戏中。\n"
            "你的职责：管理战役、创建角色卡、编辑设定。\n"
            "用户可以说「进入DM」或「进入骰娘」开始游戏。\n"
            f"{_att_info}"
            "\n━━━ 当前战役 ━━━\n"
            f"{_campaign_info}\n"
            "━━━ 可用操作 ━━━\n"
            "- 战役管理: 创建/切换/删除/查看战役\n"
            f"{_campaign_ops}"
            "- 查询: 法术搜索\n"
            "\n"
            "用户说「进入DM」「进入战役」「进入骰娘」时，如果没有当前战役则提示先选。\n"
            "用户说「退出」「返回大厅」时，已在 lobby，无需切换。\n"
            "只输出管理相关信息，禁止编造剧情、扮演、检定、给建议。"
        )
    elif mode == "dice":
        _sys = (
            "你是桌面跑团的工具型骰娘。你不在系统回合制模式下——真人 DM 在管理战斗。\n"
            "你的核心职责：读取角色热数据 → 投骰 → 结算 → 写回数据库。\n"
            "\n"
            "━━━ 主流流程：非管理战斗 ━━━\n"
            "通常从有人 @你「帮扔先攻」开始，然后玩家轮流 @你进行行动：\n"
            "1. @骰娘 帮Aric、Goblin、Mira扔先攻\n"
            "   → 手动投每个人的先攻: hot_character → initiative + checked_roll('1d20')\n"
            "   → 输出结果列表，但不写入战役配置（不进入系统回合制）\n"
            "2. 玩家A @骰娘 我用长剑攻击地精\n"
            "   → LLM理解 → combat_attack/ability_check → checked_roll → 返回结果\n"
            "   → 如果造成伤害 → apply_damage 写入 character.data.combat\n"
            "3. 玩家B @骰娘 我喝治疗药水\n"
            "   → apply_healing 写入 HP\n"
            "4. 玩家C @骰娘 等等上次伤害记错了，回退\n"
            "   → undo_damage → 从 character_change_log 反推 → 恢复HP\n"
            "\n"
            "━━━ 非管理战斗的回合感知 ━━━\n"
            "从最近的 campaign events / memories 中提取 @你的战斗行动，推断回合状态：\n"
            "- 看到「Aric攻击」「Mira施法」→ 提醒: 「Aric和Mira都行动了。Goblin还没动。」\n"
            "- 看到单人多次行动 → 问: 「Aric动了两轮了，是新一轮了吗？」\n"
            "- 不确定回合顺序 → 问: 「刚才Aric行动了，现在是轮到谁？」\n"
            "- 看起来跳过了某人 → 提醒: 「⚠️ B还没行动就被跳过了」\n"
            "- 以上全都是建议性的，不强制。真人的决定优先。\n"
            "\n"
            "━━━ 进入系统回合制 ━━━\n"
            "只有 DM 明确 @你说「进入战斗」「开始战斗」「管理系统战斗」时，才回复：\n"
            "「准备进入系统回合制战斗。需要确认：\n"
            "  1. 哪些角色参战？（请列出名字）\n"
            "  2. 有没有角色先攻有优势或劣势？\n"
            "  确认后我投全体先攻并开始管理回合。」\n"
            "不要在自己猜参战者或自己决定优势劣势。必须等 DM 回复确认。\n"
            "在 DM 确认之前，继续以非管理模式处理战斗行动。\n"
            "\n"
            "━━━ 通用规则 ━━━\n"
            "只输出事实、数据、规则引用、计算结果、状态变更。\n"
            "禁止 NPC 台词、剧情续写、战术建议。禁止替真人 DM 做决定。\n"
            "当行动可选多技能时调用 ask_clarification。\n"
            "需要检定时调用 ability_check/saving_throw，禁止编造投骰结果。\n"
            "用户说记错了/撤销/回退 → undo_damage 或 undo_healing。\n"
            "用户问最近变更 → recent_changes。"
        )
    else:
        combat_instr = ""
        if campaign:
            combat_instr = (
                "战斗中允许描写行动结果和 NPC 反应，但禁止虚构机械数值。"
                if _combat else ""
            )
        _sys = (
            "You are a concise DND Dungeon Master. Continue from established campaign memory. "
            "Use the current character sheet and relevant rules. Do not invent mechanical state changes. "
            "Never stop to ask a player to roll dice; state the required roll clearly so the system can "
            f"roll it immediately and continue. {combat_instr}"
            "When the user asks to drink a potion, take a rest, make a skill check, create a character, "
            "save a setting, check bindings, or export a sheet, use a function call rather than narrating it. "
            "When you need to make an ability check or saving throw, call ability_check or saving_throw tools "
            "with the character's real modifiers — do NOT invent dice results. "
            "When an action can use multiple skills (e.g. climbing: Athletics or Acrobatics), "
            "call ask_clarification to let the player choose which skill to use."
        )
    if character:
        _hot = hot_character_for_llm(db, character.id)
        if _hot:
            import json as _hot_json
            _sys += f"\n\n[当前角色热数据]\n{_hot_json.dumps(_hot, ensure_ascii=False)}"
    _msgs = [
        {"role": "system", "content": _sys},
        {"role": "system", "content": context_prompt},
        {"role": "user", "content": message},
    ]
    _fallback_text = (
        "行动已记录，未产生可确认的机械状态变化。"
        if _combat
        else "骰娘：请提供更具体的问题或检定指令。" if mode == "dice"
        else "你的行动让局势继续向前推进。四周的目光落在你身上，等待你作出下一步选择。"
    )

    # Use unified tool loop (same as turn-based combat)
    from app.llm_loop import execute_llm_with_tools
    _tool_result = execute_llm_with_tools(
        db, campaign, session_id, character_id, None, False, "",
        message_context, messages=_msgs, skip_user_message=True,
    )
    if _tool_result.get("kind") == "llm_unavailable":
        narration = chat_completion(_msgs, temperature=temperature) or _fallback_text
    else:
        narration = _tool_result.get("narration") or _fallback_text
    # ── Dice mode: strip roleplay/advice output ──
    if mode == "dice" and narration:
        from app.dice_assistant import strict_tool_output
        narration = strict_tool_output(narration, campaign, _combat, False) or narration
    requested_formula = _requested_roll(narration)
    automatic_roll_count = 0
    if requested_formula and campaign and bool(((campaign.config or {}).get("turn_state") or {}).get("combat")):
        window = open_reaction_window(db, campaign, narration, requested_formula, character.id if character else None)
        if window:
            resolved = resolve_ready_reaction_window(db, campaign, window)
            if resolved:
                narration = resolved["narration"]
                rolls.extend(resolved["rolls"])
                result_data.update(resolved["data"])
                requested_formula = None
            else:
                narration = format_reaction_prompt(window)
                requested_formula = None
                result_data.update({
                    "turn_consuming": False,
                    "reaction_notifications": reaction_notifications(window),
                })
    while requested_formula and automatic_roll_count < 4:
        narration, automatic_roll = _finish_requested_roll(
            context_prompt, message, narration, requested_formula, "",
        )
        rolls.append(automatic_roll)
        automatic_roll_count += 1
        requested_formula = _requested_roll(narration)

    metadata = {"raw_player_input": message, "dm_response": narration, "rolls": rolls, "state_changes": changes,
                "context_refs": context_refs}
    event = append_event(
        db, campaign_id, session_id, "player_action", message, actors, metadata,
        memory_plan={"extract_after_event": True, "intent_type": "dm_narrative", "skip": False},
    )
    return {"campaign_id": campaign_id, "message": message, "narration": narration,
            "data": result_data, "rolls": rolls, "state_changes": changes, "events": [serialize(event)]}


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
