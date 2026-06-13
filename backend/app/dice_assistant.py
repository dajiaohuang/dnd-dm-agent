from __future__ import annotations

import copy
import re

from sqlalchemy.orm import Session

from app.db.models import Campaign, Character
from app.services import update_character
from app.tools.dice import roll_dice, roll_with_advantage

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


def resolve_dice_assistant(db: Session, campaign: Campaign, character: Character | None, message: str) -> dict:
    text = " ".join(message.strip().split())
    lowered = text.casefold()
    if character and any(term in lowered for term in ("背包", "物品", "装备", "裝備", "inventory")):
        inventory = character.data.get("inventory") or []
        lines = [f"- {item.get('name', item.get('item_id', '未命名物品'))} × {item.get('quantity', 1)}" for item in inventory]
        return _result(f"骰娘：{character.character_name} 的物品：\n" + ("\n".join(lines) if lines else "（空）"))

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

    return _result("骰娘模式不会推进剧情。请发送骰式（如 2d6+3）、属性/技能检定、先攻，或“伤害 5 / 治疗 5”。")
