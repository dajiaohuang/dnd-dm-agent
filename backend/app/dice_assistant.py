from __future__ import annotations

import copy
import json
import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Campaign, CampaignEvent, CampaignSummary, Character, TaskSession
from app.services import search_rules, update_character
from app.llm import chat_completion
from app.tools.command_tools import TOOL_HANDLERS, tools_for_scope
from app.tools.hot_character import hot_character_for_llm
from app.campaign_memory import build_memory_package
from app.campaign_editor import search_settings
from app.config import settings
from app.tools.dice import roll_dice, roll_with_advantage
from app.tools.spell_catalog import search_spells
from app.actor_manager import is_present, roleplay_brief
from app.campaign_turns import current_turn, format_turn_state, start_combat, turn_notification
from app.qq_bindings import character_is_hosted, sync_campaign_actor_bindings
from app.combat_preferences import combat_preference
from app.tools.effect_engine import resolve_effective_character, roll_effects_for, consume_roll_effects
from app.effect_actions import resolve_concentration_damage
from app.combat_reactions import (
    format_reaction_prompt, open_reaction_window, reaction_notifications, resolve_ready_reaction_window,
)
from app.task_sessions import active_task, create_task, owner_mentions, task_scope

GLOBAL_EXIT_WORDS = {"退出", "取消", "结束", "停止", "算了"}

ABILITY_ALIASES = {
    "力量": "str", "strength": "str", "str": "str",
    "敏捷": "dex", "dexterity": "dex", "dex": "dex",
    "体质": "con", "體質": "con", "constitution": "con", "con": "con",
    "智力": "int", "intelligence": "int", "int": "int",
    "感知": "wis", "wisdom": "wis", "wis": "wis",
    "魅力": "cha", "charisma": "cha", "cha": "cha",
}
ABILITY_LABELS = {
    "str": "力量", "dex": "敏捷", "con": "体质",
    "int": "智力", "wis": "感知", "cha": "魅力",
}
SKILL_ALIASES = {
    "运动": "athletics", "運動": "athletics", "athletics": "athletics",
    "杂技": "acrobatics", "雜技": "acrobatics", "acrobatics": "acrobatics",
    "隐匿": "stealth", "隱匿": "stealth", "stealth": "stealth",
    "调查": "investigation", "調查": "investigation", "investigation": "investigation",
    "察觉": "perception", "察覺": "perception", "perception": "perception",
    "洞悉": "insight", "insight": "insight",
    "说服": "persuasion", "說服": "persuasion", "persuasion": "persuasion",
    "欺瞒": "deception", "欺瞞": "deception", "deception": "deception",
    "威吓": "intimidation", "威嚇": "intimidation", "intimidation": "intimidation",
    "奥秘": "arcana", "奧秘": "arcana", "arcana": "arcana",
}
ADVICE_OUTPUT_TERMS = (
    "建议", "推荐", "不妨", "可以尝试", "你可以选择", "下一步可以", "最好",
)
ROLEPLAY_OUTPUT_TERMS = (
    "你看到", "你听到", "周围", "映入眼帘", "空气中", "说道", "低语", "笑着",
    "扮演", "剧情继续", "故事继续",
)
ROLL_REQUEST_RE = re.compile(
    r"(?i)(?:请|需要|进行|作出|make|please|must|need to|roll).{0,24}"
    r"(?:掷骰|投掷|检定|豁免|攻击检定|roll|check|save|saving throw|attack roll)"
)
ROLL_FORMULA_RE = re.compile(r"(?i)(?<!\w)(\d*d\d+(?:\s*[+-]\s*\d+)?)(?!\w)")


def _result(narration: str, rolls: list[dict] | None = None, changes: list[dict] | None = None) -> dict:
    return {
        "ok": True, "kind": "dice_assistant", "command": "dice_assistant", "narration": narration,
        "data": {}, "rolls": rolls or [], "state_changes": changes or [], "events": [],
    }


def _non_turn_result(result: dict) -> dict:
    result.setdefault("data", {})["turn_consuming"] = False
    return result


def strict_tool_output(
    text: str | None,
    campaign: Campaign,
    combat_active: bool = False,
    force_roleplay: bool = False,
) -> str | None:
    if not text:
        return None
    forbidden = []
    if not combat_active or not combat_preference(campaign, "advice"):
        forbidden.extend(ADVICE_OUTPUT_TERMS)
    if not force_roleplay and (not combat_active or not combat_preference(campaign, "roleplay")):
        forbidden.extend(ROLEPLAY_OUTPUT_TERMS)
    return None if any(term in text for term in forbidden) else text.strip()


def _dice_output_instructions(
    campaign: Campaign,
    narrative_mode: bool = False,
    combat_active: bool = False,
    force_roleplay: bool = False,
) -> str:
    if not combat_active:
        return (
            "当前不在战斗中。只作为机械与记录工具工作：可以执行检定和投骰、查询规则/法术/物品/角色卡、"
            "计算数值，并按用户明确指令更新物品、HP、效果和记忆。"
            "不得描述环境、扮演 NPC、续写或推进剧情、裁定行动在世界中的后果、替 DM 决定 DC 或结果，"
            "禁止任何扮演文字。禁止给出行动建议、策略建议或下一步建议。"
            "战斗扮演与战斗建议开关在战斗外无效。"
        )
    roleplay = combat_preference(campaign, "roleplay")
    advice = combat_preference(campaign, "advice")
    instructions = []
    if force_roleplay:
        instructions.append(
            "当前行动者是托管角色。无论战斗扮演开关是否开启，都必须依据 hosted_actor_profile "
            "为该角色的行动附带简短扮演文字；只表现该角色的动作、语气或战斗反应，不得借此推进剧情。"
        )
    elif roleplay:
        instructions.append(
            "战斗中可以依据已提供资料描写环境、扮演 NPC 并表现已建立剧情。"
            if narrative_mode else
            "战斗中可以附带简短的战斗扮演文字，但不得扮演 NPC 或推进剧情。"
        )
    else:
        instructions.append("禁止任何扮演文字、气氛文字和环境描写。")
    if advice:
        instructions.append("战斗中可以回答用户明确询问的行动或策略建议。")
    else:
        instructions.append("禁止给出行动建议、策略建议或下一步建议。")
    return "".join(instructions)


def _is_hosted_actor(db: Session, campaign: Campaign, character: Character | None, combat_active: bool) -> bool:
    if not combat_active or not character:
        return False
    active = current_turn(campaign)
    if not active or active.get("character_id") != character.id:
        return False
    return character_is_hosted(db, campaign, character)


def _automatic_tool_roll(text: str | None) -> tuple[str | None, dict | None]:
    if not text or not ROLL_REQUEST_RE.search(text):
        return text, None
    formula = ROLL_FORMULA_RE.search(text)
    roll = roll_dice(re.sub(r"\s+", "", formula.group(1)) if formula else "1d20")
    resolved = (
        f"{text}\n骰娘已自动投掷 {roll['formula']}：{roll['total']}"
        f"（{roll['rolls']}，修正 {roll['modifier']:+d}）。"
    )
    return resolved, roll


def _state(campaign: Campaign) -> dict:
    return copy.deepcopy((campaign.config or {}).get("dice_assistant_state") or {})


def _save_state(db: Session, campaign: Campaign, state: dict) -> None:
    config = copy.deepcopy(campaign.config or {})
    config["dice_assistant_state"] = state
    campaign.config = config
    db.commit()


def clear_dice_pending_state(db: Session, campaign: Campaign) -> None:
    state = _state(campaign)
    if state:
        _save_state(db, campaign, {})
    for item in db.scalars(select(TaskSession).where(
        TaskSession.campaign_id == campaign.id,
        TaskSession.task_type.in_(("dice_memory_update", "dice_combat_setup")),
        TaskSession.status.in_(("active", "waiting_user", "ready_to_commit")),
    )).all():
        item.status = "cancelled"
    db.commit()


def _scoped_task(
    db: Session,
    campaign: Campaign,
    context: dict,
    task_type: str,
) -> tuple[TaskSession | None, str, str | None, str, str | None]:
    platform, chat_id, user_id, session_id = task_scope(
        context,
        str(context.get("actor_id") or context.get("sender_id") or "") or None,
        context.get("session_id"),
    )
    return active_task(db, campaign, task_type, platform, user_id, session_id), platform, chat_id, user_id, session_id


def _has_active_task(db: Session, campaign: Campaign, task_type: str) -> bool:
    return db.scalar(select(TaskSession.id).where(
        TaskSession.campaign_id == campaign.id,
        TaskSession.task_type == task_type,
        TaskSession.status.in_(("active", "waiting_user", "ready_to_commit")),
    ).limit(1)) is not None


def _yes(text: str) -> bool:
    words = {"是", "要", "好", "好的", "可以", "yes", "y", "读取", "讀取"}
    return any(token in words for token in text.casefold().split())


def _no(text: str) -> bool:
    words = {"否", "不", "不要", "不用", "no", "n"}
    return any(token in words for token in text.casefold().split())


def dice_context_action(
    db: Session,
    campaign: Campaign,
    message: str,
    message_context: dict | None = None,
) -> dict | None:
    text = " ".join(message.strip().split())
    lowered = text.casefold()
    context = message_context or {}
    state = _state(campaign)
    memory_task, platform, chat_id, user_id, scoped_session = _scoped_task(
        db, campaign, context, "dice_memory_update",
    )
    combat_task, _platform, _chat_id, _user_id, _scoped_session = _scoped_task(
        db, campaign, context, "dice_combat_setup",
    )
    legacy_memory_pending = bool(state.get("pending_memory_history")) and not _has_active_task(
        db, campaign, "dice_memory_update",
    )
    legacy_combat_pending = bool(state.get("pending_combat_setup")) and not _has_active_task(
        db, campaign, "dice_combat_setup",
    )

    if (campaign.config or {}).get("dice_dm_confirmation_pending"):
        if text in GLOBAL_EXIT_WORDS or any(term in lowered for term in ("退出骰娘", "取消确认", "先算了")):
            config = copy.deepcopy(campaign.config or {})
            config.pop("dice_dm_confirmation_pending", None)
            campaign.config = config
            db.commit()
            return _result("已取消本次 DM 确认。")
        mentioned = [str(item).strip() for item in context.get("mentioned_user_ids") or [] if str(item).strip()]
        match = re.search(r"(?:dm\s*(?:是|为|=)?\s*)?(\d{5,})", lowered)
        dm_qq_user_id = mentioned[0] if len(mentioned) == 1 else match.group(1) if match else ""
        if not dm_qq_user_id:
            return None
        config = copy.deepcopy(campaign.config or {})
        config["dice_dm_qq_user_id"] = dm_qq_user_id
        config.pop("dice_dm_confirmation_pending", None)
        campaign.config = config
        db.commit()
        bound = sync_campaign_actor_bindings(db, campaign, dm_qq_user_id)
        result = _result(f"DM QQ：{dm_qq_user_id}；已关联 NPC/怪物：{len(bound)}。")
        result["data"] = {
            "audit_type": "dice_dm_confirmed",
            "audit_content": dm_qq_user_id,
            "dm_qq_user_id": dm_qq_user_id,
            "dm_actor_ids": [item.id for item in bound],
        }
        return result

    # Memory update + combat setup → now handled by LLM tools (create_memory, start_combat_setup)
    # Only DM confirmation remains as fast-path above
    return None


def _modifier(character: Character | None, text: str, combat: bool = False) -> tuple[str, int]:
    lowered = text.casefold()
    if character:
        effective = resolve_effective_character(character.data, combat).get("effective") or {}
        for alias, skill in SKILL_ALIASES.items():
            if alias in lowered:
                value = (effective.get("skills") or {}).get(skill, 0)
                return skill, _bonus_value(value)
        for alias, ability in ABILITY_ALIASES.items():
            if alias in lowered:
                return ability, int((effective.get("ability_modifiers") or {}).get(ability, 0))
    match = re.search(r"([+-]\d+)", lowered)
    return "检定", int(match.group(1)) if match else 0


def _named_entries(entries: list, fallback: str) -> list[str]:
    result = []
    for item in entries:
        if isinstance(item, str):
            result.append(item)
        elif isinstance(item, dict):
            name = item.get("name") or item.get("english_name") or item.get("id")
            if name:
                result.append(str(name))
    return result or [fallback]


def _bonus_value(value, default: int = 0) -> int:
    if isinstance(value, dict):
        value = value.get("bonus", default)
    return int(default if value is None else value)


def _actor_matches_message(character: Character, message: str) -> bool:
    lowered = message.casefold()
    name = character.character_name.casefold()
    aliases = [str(item).casefold() for item in ((character.data.get("basic") or {}).get("aliases") or [])]
    if name in lowered or any(alias and alias in lowered for alias in aliases):
        return True
    chinese_name = "".join(re.findall(r"[\u4e00-\u9fff]", name))
    return any(chinese_name[-length:] in lowered for length in range(2, min(5, len(chinese_name) + 1)))


def _mechanical_character_snapshot(character: Character, combat: bool = False) -> dict:
    data = resolve_effective_character(character.data or {}, combat)
    snapshot = {
        key: copy.deepcopy(data.get(key))
        for key in (
            "basic", "abilities", "combat", "saving_throw_proficiencies", "saving_throws",
            "skills", "proficiencies", "inventory", "currency", "features", "spells",
            "spellcasting", "conditions", "active_effects", "notes", "derived", "effective",
        )
        if key in data
    }
    abilities = data.get("abilities") or {}
    derived = copy.deepcopy(data.get("derived") or {})
    ability_modifiers = copy.deepcopy(derived.get("ability_modifiers") or {})
    for ability in ABILITY_LABELS:
        if ability not in ability_modifiers and abilities.get(ability) is not None:
            ability_modifiers[ability] = (int(abilities[ability]) - 10) // 2
    derived["ability_modifiers"] = ability_modifiers
    combat = data.get("combat") or {}
    for field in ("proficiency_bonus", "initiative", "passive_perception", "carrying_capacity"):
        if derived.get(field) is None and combat.get(field) is not None:
            derived[field] = combat[field]
    spellcasting = data.get("spellcasting") or {}
    if derived.get("spell_save_dc") is None and spellcasting.get("save_dc") is not None:
        derived["spell_save_dc"] = spellcasting["save_dc"]
    if derived.get("spell_attack_bonus") is None and spellcasting.get("attack_bonus") is not None:
        derived["spell_attack_bonus"] = spellcasting["attack_bonus"]
    snapshot["derived"] = derived
    return snapshot


def _character_capabilities(character: Character, combat: bool = False) -> str:
    data = _mechanical_character_snapshot(character, combat)
    effective = data.get("effective") or {}
    effective_skills = effective.get("skills") or data.get("skills") or {}
    skills = [
        f"{name} {int(details.get('bonus', 0)):+d}"
        for name, details in effective_skills.items()
        if details.get("proficient") or details.get("expertise")
    ]
    features = _named_entries(data.get("features") or [], "无已记录特性")
    spells = _named_entries(data.get("spells") or [], "无已记录法术")
    conditions = _named_entries(data.get("conditions") or [], "无")
    combat_data = effective.get("combat") or data.get("combat") or {}
    derived = data.get("derived") or {}
    modifiers = effective.get("ability_modifiers") or derived.get("ability_modifiers") or {}
    abilities = effective.get("abilities") or data.get("abilities") or {}
    ability_line = "、".join(
        f"{label} {abilities.get(key, '?')}（{int(modifiers[key]):+d}）"
        if modifiers.get(key) is not None else f"{label} {abilities.get(key, '?')}"
        for key, label in ABILITY_LABELS.items()
    )
    saves = "、".join(
        f"{name} {_bonus_value(details):+d}"
        for name, details in (effective.get("saving_throws") or data.get("saving_throws") or {}).items()
    ) or "无已记录豁免"
    spellcasting = data.get("spellcasting") or {}
    return (
        f"骰娘：{character.character_name} 当前可用能力概览：\n"
        f"- 属性与调整值：{ability_line}\n"
        f"- 战斗数值：AC {combat_data.get('armor_class', '?')}，HP {combat_data.get('current_hp', '?')}/{combat_data.get('max_hp', '?')}，"
        f"临时 HP {combat_data.get('temp_hp', 0)}，先攻 {_bonus_value(combat_data.get('initiative', derived.get('initiative'))):+d}，"
        f"熟练加值 {_bonus_value(combat_data.get('proficiency_bonus', derived.get('proficiency_bonus'))):+d}\n"
        f"- 豁免：{saves}\n"
        f"- 熟练技能：{'、'.join(skills) if skills else '无已记录熟练技能'}\n"
        f"- 特性/能力：{'、'.join(features)}\n"
        f"- 可用法术：{'、'.join(spells)}\n"
        f"- 施法数值：豁免 DC {spellcasting.get('save_dc', derived.get('spell_save_dc', '?'))}，"
        f"法术攻击 {spellcasting.get('attack_bonus', derived.get('spell_attack_bonus', '?'))}\n"
        f"- 当前状态：{'、'.join(conditions)}"
    )


def resolve_dice_tool_question(
    db: Session,
    campaign: Campaign,
    character: Character | None,
    message: str,
    narrative_mode: bool = False,
) -> dict:
    lowered = message.casefold()
    if character and any(term in lowered for term in (
        "我有什么技能", "我会什么", "能放什么", "可用技能", "可用法术", "有什么法术",
        "有什么特性", "有什么能力", "角色卡", "我的状态", "我的ac", "我的 ac",
        "调整值", "調整值", "属性值", "屬性值", "character sheet",
    )):
        return _result(_character_capabilities(character, bool(((campaign.config or {}).get("turn_state") or {}).get("combat"))))
    if not character and (
        any(term in lowered for term in ("角色卡", "character sheet", "我的ac", "我的 ac", "调整值", "調整值"))
        or ("我的" in lowered and any(term in lowered for term in ("技能", "法术", "法術", "属性", "屬性", "状态", "狀態", "豁免", "先攻")))
    ):
        return _result(
            f"骰娘：当前 QQ 在战役“{campaign.name}”中没有绑定角色卡，因此无法读取 AC、属性调整值或其他角色数值。"
            "请先在该战役中创建或绑定角色卡。"
        )

    mechanical_character = _mechanical_character_snapshot(
        character, bool(((campaign.config or {}).get("turn_state") or {}).get("combat")),
    ) if character else {}
    rules = search_rules(db, message, 4)
    spells = search_spells(message, settings.data_dir, 4)
    memory_package = build_memory_package(db, campaign.id, message, limit=5)
    campaign_settings = search_settings(db, campaign.id, message, 8)
    recent_events = db.scalars(
        select(CampaignEvent).where(
            CampaignEvent.campaign_id == campaign.id,
            *([] if narrative_mode else [CampaignEvent.visibility != "dm_only"]),
        )
        .order_by(CampaignEvent.created_at.desc()).limit(12)
    ).all()
    summaries = db.scalars(
        select(CampaignSummary).where(CampaignSummary.campaign_id == campaign.id)
        .order_by(CampaignSummary.updated_at.desc()).limit(3)
    ).all()
    present = [
        {
            "name": item.character_name,
            "actor_type": (item.data.get("basic") or {}).get("actor_type", "player"),
            "basic": item.data.get("basic") or {},
            "encounter": item.data.get("encounter") or {},
            "hp": (item.data.get("combat") or {}).get("current_hp"),
            "conditions": item.data.get("conditions") or [],
        }
        for item in db.scalars(select(Character).where(Character.campaign_id == campaign.id)).all()
        if is_present(item)
    ]
    combat_active = bool(((campaign.config or {}).get("turn_state") or {}).get("combat"))
    force_roleplay = _is_hosted_actor(db, campaign, character, combat_active)
    participants = ((campaign.config or {}).get("turn_state") or {}).get("participants") or []
    participant_by_id = {
        item.id: item
        for item in db.scalars(select(Character).where(Character.campaign_id == campaign.id)).all()
    }
    combat_participant_cards = [
        {
            "character_id": participant.get("character_id"),
            "name": participant.get("name"),
            "actor_type": participant.get("actor_type"),
            "initiative": participant.get("initiative"),
            "mechanical_card": _mechanical_character_snapshot(actor, combat_active),
        }
        for participant in participants
        if (actor := participant_by_id.get(participant.get("character_id"))) is not None
    ]
    target_actor_cards = [
        item for item in combat_participant_cards
        if (actor := participant_by_id.get(item.get("character_id"))) is not None
        and _actor_matches_message(actor, message)
    ]
    present_dm_actor_profiles = [
        roleplay_brief(item)
        for item in participant_by_id.values()
        if is_present(item) and (item.data.get("basic") or {}).get("actor_type") in {"npc", "monster"}
    ] if narrative_mode else []
    hosted_actor_profile = roleplay_brief(character) if force_roleplay and character else None
    context = {
        "current_campaign": {
            "id": campaign.id,
            "name": campaign.name,
            "description": campaign.description,
            "system_version": campaign.system_version,
            "current_state": campaign.config or {},
        },
        "recent_progress": [
            {"type": item.event_type, "content": item.content, "metadata": item.event_metadata}
            for item in reversed(recent_events)
        ],
        "campaign_summaries": [
            {"scope": item.scope, "summary": item.summary, "open_threads": item.open_threads}
            for item in summaries
        ],
        "relevant_campaign_settings": [
            {
                "category": item.category, "name": item.name, "summary": item.summary,
                "content": item.content, "visibility": item.visibility,
            }
            for item in campaign_settings
            if narrative_mode or item.visibility != "dm_only"
        ],
        "bound_character": mechanical_character or None,
        "combat_participant_cards": combat_participant_cards,
        "target_actor_cards": target_actor_cards,
        "present_actors": present,
        "present_dm_actor_profiles": present_dm_actor_profiles,
        "hosted_actor_profile": hosted_actor_profile,
        "relevant_rules": [
            {"source": item.get("source"), "section": item.get("section"), "text": item.get("chunk_text")}
            for item in rules
        ],
        "relevant_spells": spells,
        "relevant_memory": [
            item for item in memory_package["memories"]
            if narrative_mode or item.get("visibility") != "dm_only"
        ],
        "hot_character_snapshot": hot_character_for_llm(db, character.id) if character else None,
    }
    output_instructions = _dice_output_instructions(campaign, narrative_mode, combat_active, force_roleplay)
    role_instructions = (
        "你是当前战役的地下城主。战斗机械规则与骰娘模式完全一致，必须依据参战者实体卡、有效数值、"
        "持续效果和反应窗口结算。在机械事实之外，可以依据战役记忆和 present_dm_actor_profiles "
        "描写环境、推进已建立的剧情并扮演 NPC，但不得篡改或虚构机械数值。"
        if narrative_mode else
        "你是桌面跑团的工具型骰娘。自然、直接地回答规则、角色卡、技能、法术、物品、"
        "检定、数值计算和明确的状态记录问题。战役上下文只用于找到正确的角色卡、数值和记录，"
        "不得用于讲述场景或继续故事。"
    )
    closing_instructions = (
        "允许依据已提供内容进行 NPC 台词、战斗叙述和剧情表现。"
        if narrative_mode else
        (
            "只允许为当前托管角色的本次行动添加简短扮演文字；禁止扮演其他 NPC、剧情续写或推进剧情，"
            "禁止替真实 DM 决定结果。"
            if force_roleplay else
            "始终禁止 NPC 台词和剧情续写。禁止推进或编造剧情，禁止替真实 DM 决定结果。"
        )
    )
    _sys1 = (
        f"{role_instructions}"
        "系统始终运行在 current_campaign 所指向的当前战役内，切换玩法不会切换或清空战役。"
        "只能依据提供的战役上下文、机械数据和记忆回答。"
        "战斗结算必须从 target_actor_cards 或 combat_participant_cards 读取目标 AC、HP、豁免和其他机械数值；"
        "不得用聊天记录中的假设值替代实体角色卡。"
        "战斗中声明攻击、施法或其他可能触发反应的行动时，先说明行动和所需投掷公式，"
        "不要提前给出投掷结果；系统会先处理所有角色的反应决定。"
        "只输出事实、数据、规则引用、计算结果、状态变更或必要的澄清问题。"
        "当行动可选择多种技能时（如攀爬可用运动或体操），调用 ask_clarification 让玩家选择。"
        f"{output_instructions}"
        f"{closing_instructions}"
        "信息不足时只列出缺少的字段。"
        "如果你需要执行操作（如创建角色、保存设定、查看绑定等），使用函数调用。"
    )
    _sys2 = json.dumps(context, ensure_ascii=False, default=str)
    _msgs = [
        {"role": "system", "content": _sys1},
        {"role": "system", "content": _sys2},
        {"role": "user", "content": message},
    ]

    # Try LLM function-calling tools; fall back to plain chat_completion if no tools triggered
    _tools = tools_for_scope(campaign, is_dm=False)
    _tool_used = False
    try:
        _resp = chat_completion(_msgs, temperature=0.2, tools=_tools)
    except Exception:
        _resp = None
    if _resp is not None and not isinstance(_resp, str) and _resp.tool_calls:
        _tool_used = True
        for tc in _resp.tool_calls:
            handler = TOOL_HANDLERS.get(tc.function.name)
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {}
            args.setdefault("db", db)
            args.setdefault("campaign", campaign)
            if handler:
                try:
                    result = handler(**{k: v for k, v in args.items()
                        if k in {"db", "campaign", "character_name", "class_name",
                                  "level", "ancestry", "background", "abilities",
                                  "category", "name", "description", "query",
                                  "user_id", "player_name"}})
                except Exception as exc:
                    result = {"ok": False, "narration": f"工具执行失败: {exc}"}
            else:
                result = {"ok": False, "narration": f"未知工具: {tc.function.name}"}
            _msgs.append({
                "role": "assistant", "content": _resp.content,
                "tool_calls": [{"id": tc.id, "type": "function",
                    "function": {"name": tc.function.name, "arguments": tc.function.arguments}}],
            })
            _msgs.append({"role": "tool", "tool_call_id": tc.id,
                "content": json.dumps(result, ensure_ascii=False, default=str)})
        try:
            _resp2 = chat_completion(_msgs, temperature=0.2, tools=_tools)
            answer = (_resp2.content if _resp2 is not None and not isinstance(_resp2, str) else (_resp2 if isinstance(_resp2, str) else None))
        except Exception:
            answer = None
        if answer:
            answer = strict_tool_output(answer, campaign, combat_active, force_roleplay)

    if not _tool_used:
        if isinstance(_resp, str):
            answer = strict_tool_output(_resp, campaign, combat_active, force_roleplay)
        elif _resp is not None and _resp.content:
            answer = strict_tool_output(_resp.content, campaign, combat_active, force_roleplay)
        else:
            answer = strict_tool_output(chat_completion(_msgs, temperature=0.2), campaign, combat_active, force_roleplay)
    if answer:
        roll_requested = bool(ROLL_REQUEST_RE.search(answer))
        requested_roll = ROLL_FORMULA_RE.search(answer) if roll_requested else None
        if roll_requested and bool(((campaign.config or {}).get("turn_state") or {}).get("combat")):
            formula = re.sub(r"\s+", "", requested_roll.group(1)) if requested_roll else "1d20"
            window = open_reaction_window(db, campaign, answer, formula, character.id if character else None)
            if window:
                resolved = resolve_ready_reaction_window(db, campaign, window)
                if resolved:
                    return resolved
                result = _result(format_reaction_prompt(window))
                result["data"] = {
                    "turn_consuming": False,
                    "reaction_notifications": reaction_notifications(window),
                }
                return result
        answer, automatic_roll = _automatic_tool_roll(answer)
        return _result(answer, [automatic_roll] if automatic_roll else [])
    if rules:
        excerpts = "\n\n".join(
            f"[{item.get('source')} / {item.get('section')}]\n{str(item.get('chunk_text') or '')[:800]}"
            for item in rules[:3]
        )
        return _result(f"骰娘：找到以下相关规则资料：\n\n{excerpts}")
    if character:
        return _result(_character_capabilities(character, bool(((campaign.config or {}).get("turn_state") or {}).get("combat"))))
    return _result("骰娘：这个问题需要先绑定角色卡，或补充要查询的规则、法术、物品或检定名称。")


def resolve_dice_assistant(
    db: Session,
    campaign: Campaign,
    character: Character | None,
    message: str,
    narrative_mode: bool = False,
) -> dict:
    text = " ".join(message.strip().split())
    lowered = text.casefold()
    # Inventory/potion/HP change → handled by LLM tools now
    is_question = any(term in lowered for term in (
        "什么", "多少", "怎么", "如何", "能否", "是否", "应该", "可以吗", "吗", "？", "?",
        "what", "how", "which",
    ))
    has_explicit_dice_formula = bool(re.search(r"(?<!\w)\d*d\d+(?:[+-]\d+)?(?!\w)", lowered))
    if is_question and not has_explicit_dice_formula:
        from app.services import resolve_chat
        result = resolve_chat(db, campaign.id, None, character.id if character else None, message, mode="dice", message_context=None)
        return _non_turn_result(_result(result.get("narration", ""), result.get("rolls", []), result.get("state_changes", [])))

    # Direct dice formula → fast-path atomic roll (keep)
    formula = re.search(r"(?<!\w)(\d*d\d+(?:[+-]\d+)?)(?!\w)", lowered)
    if formula:
        roll = roll_dice(formula.group(1))
        return _result(f"骰娘：{roll['formula']} = {roll['total']}（{roll['rolls']}，修正 {roll['modifier']:+d}）", [roll])

    # Skill/save/initiative checks → fast-path (keep: core dice mechanic)
    if any(term in lowered for term in ("检定", "檢定", "豁免", "check", "save", "先攻", "initiative")):
        combat_active = bool(((campaign.config or {}).get("turn_state") or {}).get("combat"))
        label, modifier = _modifier(character, lowered, combat_active)
        if "先攻" in lowered or "initiative" in lowered:
            label = "先攻"
            if character:
                effective = resolve_effective_character(character.data, combat_active).get("effective") or {}
                modifier = int((effective.get("combat") or {}).get("initiative", modifier))
        disadvantage = any(term in lowered for term in ("劣势", "劣勢", "disadvantage"))
        advantage = any(term in lowered for term in ("优势", "優勢", "advantage"))
        roll_type = (
            "saving_throw" if any(term in lowered for term in ("豁免", "save"))
            else "attack_roll" if any(term in lowered for term in ("攻击", "攻擊", "attack"))
            else "ability_check"
        )
        roll_context = roll_effects_for(resolve_effective_character(character.data, combat_active), roll_type) if character else {
            "advantage": "normal", "bonus_dice": [], "effect_ids": [],
        }
        if not advantage and not disadvantage:
            advantage = roll_context["advantage"] == "advantage"
            disadvantage = roll_context["advantage"] == "disadvantage"
        roll = roll_with_advantage(modifier, disadvantage) if advantage or disadvantage else roll_dice(f"1d20{modifier:+d}")
        bonus_rolls = [roll_dice(formula) for formula in roll_context["bonus_dice"]]
        if bonus_rolls:
            roll["base_total"] = roll["total"]
            roll["bonus_dice"] = bonus_rolls
            roll["total"] += sum(item["total"] for item in bonus_rolls)
        if character and roll_context["effect_ids"]:
            next_data, consumed = consume_roll_effects(character.data, roll_context["effect_ids"], roll_type)
            if consumed:
                update_character(db, character, {"active_effects": next_data["active_effects"]},
                                 f"consumed effects on {roll_type}", "effect_consumed")
        mode = "劣势" if disadvantage else "优势" if advantage else "普通"
        bonus_text = f"，额外骰 {roll_context['bonus_dice']}" if bonus_rolls else ""
        return _result(f"骰娘：{label}检定（{mode}，修正 {modifier:+d}{bonus_text}）= {roll['total']}。", [roll])

    # Fallthrough to unified LLM path (dice mode)
    from app.services import resolve_chat
    result = resolve_chat(db, campaign.id, None, character.id if character else None, message, mode="dice")
    return _result(result.get("narration", ""), result.get("rolls", []), result.get("state_changes", []))
