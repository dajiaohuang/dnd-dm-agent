from __future__ import annotations

import copy
import json
import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Campaign, CampaignEvent, CampaignSummary, Character
from app.services import search_rules, update_character
from app.llm import chat_completion
from app.campaign_memory import build_memory_package
from app.campaign_editor import search_settings
from app.config import settings
from app.tools.dice import roll_dice, roll_with_advantage
from app.tools.spell_catalog import search_spells
from app.actor_manager import is_present
from app.campaign_turns import format_turn_state, start_combat, turn_notification

ABILITY_ALIASES = {
    "力量": "str", "strength": "str", "str": "str",
    "敏捷": "dex", "dexterity": "dex", "dex": "dex",
    "体质": "con", "體質": "con", "constitution": "con", "con": "con",
    "智力": "int", "intelligence": "int", "int": "int",
    "感知": "wis", "wisdom": "wis", "wis": "wis",
    "魅力": "cha", "charisma": "cha", "cha": "cha",
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


def _result(narration: str, rolls: list[dict] | None = None, changes: list[dict] | None = None) -> dict:
    return {
        "ok": True, "kind": "dice_assistant", "command": "dice_assistant", "narration": narration,
        "data": {}, "rolls": rolls or [], "state_changes": changes or [], "events": [],
    }


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

    if state.get("pending_memory_history"):
        if _yes(text):
            history = context.get("group_history") or []
            state.pop("pending_memory_history", None)
            _save_state(db, campaign, state)
            combined = "\n".join(str(item.get("text") or "") for item in history if item.get("text"))
            result = _result(f"骰娘：已读取并更新前面 {len(history)} 条聊天记录。")
            result["data"]["audit_content"] = combined or "确认读取前文，但没有取得聊天记录。"
            result["data"]["audit_type"] = "dice_memory_history_update"
            return result
        if _no(text):
            state.pop("pending_memory_history", None)
            _save_state(db, campaign, state)
            return _result("骰娘：好的，仅保留刚才被 @ / 引用的内容，不读取前文。")
        return None

    if any(term in lowered for term in ("更新记忆", "更新記憶", "记住", "記住", "记录一下", "記錄一下")):
        source = str(context.get("reply_text") or context.get("current_text") or text).strip()
        state["pending_memory_history"] = True
        _save_state(db, campaign, state)
        result = _result(f"骰娘：已将当前被 @ / 引用内容加入记忆：\n{source}\n\n要不要读取前面的聊天记录来继续更新？")
        result["data"]["audit_content"] = source
        result["data"]["audit_type"] = "dice_memory_update"
        return result

    if state.get("pending_combat_setup"):
        if any(term in lowered for term in ("取消", "算了", "不打了", "退出战斗", "取消战斗", "cancel")):
            state.pop("pending_combat_setup", None)
            _save_state(db, campaign, state)
            return _result("骰娘：已取消本次战斗准备，仍留在当前战役与骰娘模式中。")
        characters = [
            item for item in db.scalars(select(Character).where(Character.campaign_id == campaign.id)).all()
            if is_present(item)
        ]
        selected = [item for item in characters if item.character_name.casefold() in lowered]
        setup_markers = ("角色", "参战", "參戰", "优势", "優勢", "劣势", "劣勢", "participants")
        if not selected or not any(marker in lowered for marker in setup_markers):
            return None
        advantage_text = lowered.split("优势", 1)[1].split("；", 1)[0] if "优势" in lowered else ""
        disadvantage_text = lowered.split("劣势", 1)[1].split("；", 1)[0] if "劣势" in lowered else ""
        modes = {}
        for item in selected:
            name = item.character_name.casefold()
            modes[item.id] = "advantage" if name in advantage_text else "disadvantage" if name in disadvantage_text else "normal"
        combat = start_combat(db, campaign, [item.id for item in selected], modes)
        state.pop("pending_combat_setup", None)
        _save_state(db, campaign, state)
        order = "\n".join(
            f"{index + 1}. {item['name']}（{item['initiative_mode']}）：{item['initiative']['total']}"
            for index, item in enumerate(combat["participants"])
        )
        result = _result(f"骰娘：战斗开始，已按优势状态投掷先攻：\n{order}\n\n{format_turn_state(campaign)}",
                         [item["initiative"] for item in combat["participants"]])
        result["data"] = {"turn_state": combat, "turn_notification": turn_notification(db, campaign),
                          "audit_type": "dice_combat_started", "audit_content": text}
        return result

    if any(term in lowered for term in ("开始战斗", "開始戰鬥", "进入战斗", "進入戰鬥", "start combat", "/combat")):
        state["pending_combat_setup"] = True
        _save_state(db, campaign, state)
        present = [
            item.character_name for item in db.scalars(select(Character).where(Character.campaign_id == campaign.id)).all()
            if is_present(item)
        ]
        return _result(
            "骰娘：准备开始战斗。请告诉我：\n"
            "1. 哪些角色参战？\n2. 哪些角色先攻有优势或劣势？\n"
            f"当前在场角色：{'、'.join(present) or '无'}\n"
            "示例：角色：Aric、Goblin；优势：Aric；劣势：Goblin"
        )
    return None


def _modifier(character: Character | None, text: str) -> tuple[str, int]:
    lowered = text.casefold()
    if character:
        for alias, skill in SKILL_ALIASES.items():
            if alias in lowered:
                return skill, int(((character.data.get("skills") or {}).get(skill) or {}).get("bonus", 0))
        for alias, ability in ABILITY_ALIASES.items():
            if alias in lowered:
                score = int((character.data.get("abilities") or {}).get(ability, 10))
                return ability, (score - 10) // 2
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


def _character_capabilities(character: Character) -> str:
    data = character.data or {}
    skills = [
        f"{name} {int(details.get('bonus', 0)):+d}"
        for name, details in (data.get("skills") or {}).items()
        if details.get("proficient") or details.get("expertise")
    ]
    features = _named_entries(data.get("features") or [], "无已记录特性")
    spells = _named_entries(data.get("spells") or [], "无已记录法术")
    conditions = _named_entries(data.get("conditions") or [], "无")
    combat = data.get("combat") or {}
    return (
        f"骰娘：{character.character_name} 当前可用能力概览：\n"
        f"- 熟练技能：{'、'.join(skills) if skills else '无已记录熟练技能'}\n"
        f"- 特性/能力：{'、'.join(features)}\n"
        f"- 可用法术：{'、'.join(spells)}\n"
        f"- 状态：HP {combat.get('current_hp', '?')}/{combat.get('max_hp', '?')}，"
        f"AC {combat.get('armor_class', '?')}，当前状态 {'、'.join(conditions)}"
    )


def resolve_dice_tool_question(
    db: Session,
    campaign: Campaign,
    character: Character | None,
    message: str,
) -> dict:
    lowered = message.casefold()
    if character and any(term in lowered for term in (
        "我有什么技能", "我会什么", "能放什么", "可用技能", "可用法术", "有什么法术",
        "有什么特性", "有什么能力", "角色卡", "我的状态", "character sheet",
    )):
        return _result(_character_capabilities(character))

    data = character.data if character else {}
    mechanical_character = {
        key: copy.deepcopy(data.get(key))
        for key in (
            "basic", "abilities", "combat", "saving_throws", "skills", "proficiencies",
            "inventory", "currency", "features", "spells", "spellcasting", "conditions", "notes",
        )
        if key in data
    }
    rules = search_rules(db, message, 4)
    spells = search_spells(message, settings.data_dir, 4)
    memory_package = build_memory_package(db, campaign.id, message, limit=5)
    campaign_settings = search_settings(db, campaign.id, message, 8)
    recent_events = db.scalars(
        select(CampaignEvent).where(
            CampaignEvent.campaign_id == campaign.id,
            CampaignEvent.visibility != "dm_only",
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
            if item.visibility != "dm_only"
        ],
        "bound_character": mechanical_character or None,
        "present_actors": present,
        "relevant_rules": [
            {"source": item.get("source"), "section": item.get("section"), "text": item.get("chunk_text")}
            for item in rules
        ],
        "relevant_spells": spells,
        "relevant_memory": [
            item for item in memory_package["memories"] if item.get("visibility") != "dm_only"
        ],
    }
    answer = chat_completion([
        {
            "role": "system",
            "content": (
                "你是桌面跑团的工具型骰娘。自然、直接地回答规则、角色卡、技能、法术、物品、"
                "检定、战斗计算，以及当前战役的进度、场景、背景、角色和已记录事实问题。"
                "骰娘始终运行在 current_campaign 所指向的当前战役内，切换模式不会切换或清空战役。"
                "只能依据提供的战役上下文、机械数据和记忆回答。"
                "禁止推进或编造剧情，禁止描写周围环境，禁止扮演 NPC，禁止替真实 DM 决定结果。"
                "信息不足时明确指出缺少什么。"
            ),
        },
        {"role": "system", "content": json.dumps(context, ensure_ascii=False, default=str)},
        {"role": "user", "content": message},
    ], temperature=0.2)
    if answer:
        return _result(answer)
    if rules:
        excerpts = "\n\n".join(
            f"[{item.get('source')} / {item.get('section')}]\n{str(item.get('chunk_text') or '')[:800]}"
            for item in rules[:3]
        )
        return _result(f"骰娘：找到以下相关规则资料：\n\n{excerpts}")
    if character:
        return _result(_character_capabilities(character))
    return _result("骰娘：这个问题需要先绑定角色卡，或补充要查询的规则、法术、物品或检定名称。")


def resolve_dice_assistant(db: Session, campaign: Campaign, character: Character | None, message: str) -> dict:
    text = " ".join(message.strip().split())
    lowered = text.casefold()
    if character and any(term in lowered for term in ("背包", "物品", "装备", "裝備", "inventory")):
        inventory = character.data.get("inventory") or []
        lines = [f"- {item.get('name', item.get('item_id', '未命名物品'))} × {item.get('quantity', 1)}" for item in inventory]
        return _result(f"骰娘：{character.character_name} 的物品：\n" + ("\n".join(lines) if lines else "（空）"))

    is_question = any(term in lowered for term in (
        "什么", "多少", "怎么", "如何", "能否", "是否", "应该", "可以吗", "吗", "？", "?",
        "what", "how", "which",
    ))
    has_explicit_dice_formula = bool(re.search(r"(?<!\w)\d*d\d+(?:[+-]\d+)?(?!\w)", lowered))
    if is_question and not has_explicit_dice_formula:
        return resolve_dice_tool_question(db, campaign, character, message)

    if character and any(term in lowered for term in ("治疗药水", "治療藥水", "healing potion", "potion of healing")):
        inventory = character.data.get("inventory") or []
        potion = next((item for item in inventory if item.get("item_id") == "potion_healing" and item.get("quantity", 0) > 0), None)
        if not potion:
            return _result(f"骰娘：{character.character_name} 没有可用的治疗药水。")
        roll = roll_dice("2d4+2")
        combat = character.data.get("combat") or {}
        before = int(combat.get("current_hp", 0))
        maximum = int(combat.get("max_hp", before))
        after = min(maximum, before + roll["total"])
        next_inventory = copy.deepcopy(inventory)
        next(item for item in next_inventory if item.get("item_id") == "potion_healing")["quantity"] -= 1
        update_character(
            db, character, {"combat": {"current_hp": after}, "inventory": next_inventory},
            "dice assistant consumed Potion of Healing", "consume_item", ["potion_healing"],
        )
        changes = [
            {"type": "hp_change", "character_id": character.id, "before": before, "after": after},
            {"type": "inventory_change", "character_id": character.id, "item_id": "potion_healing", "delta": -1},
        ]
        return _result(f"骰娘：治疗药水恢复 {roll['total']} 点，{character.character_name} HP {before}/{maximum} → {after}/{maximum}。", [roll], changes)

    formula = re.search(r"(?<!\w)(\d*d\d+(?:[+-]\d+)?)(?!\w)", lowered)
    if formula:
        roll = roll_dice(formula.group(1))
        return _result(f"骰娘：{roll['formula']} = {roll['total']}（{roll['rolls']}，修正 {roll['modifier']:+d}）", [roll])

    hp_change = re.search(r"(?:伤害|傷害|damage)\s*(\d+)", lowered)
    healing = re.search(r"(?:治疗|治療|heal)\s*(\d+)", lowered)
    if (hp_change or healing) and character:
        combat = character.data.get("combat") or {}
        before = int(combat.get("current_hp", 0))
        maximum = int(combat.get("max_hp", before))
        delta = int((healing or hp_change).group(1)) * (1 if healing else -1)
        after = max(0, min(maximum, before + delta))
        update_character(db, character, {"combat": {"current_hp": after}}, text, "dice_assistant_hp")
        change = {"type": "hp_change", "character_id": character.id, "before": before, "after": after}
        return _result(f"骰娘：{character.character_name} HP {before}/{maximum} → {after}/{maximum}。", changes=[change])

    if any(term in lowered for term in ("检定", "檢定", "豁免", "check", "save", "先攻", "initiative")):
        label, modifier = _modifier(character, lowered)
        if "先攻" in lowered or "initiative" in lowered:
            label = "先攻"
            modifier = int(((character.data.get("combat") or {}).get("initiative", modifier))) if character else modifier
        disadvantage = any(term in lowered for term in ("劣势", "劣勢", "disadvantage"))
        advantage = any(term in lowered for term in ("优势", "優勢", "advantage"))
        roll = roll_with_advantage(modifier, disadvantage) if advantage or disadvantage else roll_dice(f"1d20{modifier:+d}")
        mode = "劣势" if disadvantage else "优势" if advantage else "普通"
        return _result(f"骰娘：{label}检定（{mode}，修正 {modifier:+d}）= {roll['total']}。", [roll])

    return resolve_dice_tool_question(db, campaign, character, message)
