"""Dice-check tools: ability checks, saving throws, damage, healing, conditions.

Every tool reads attributes from ``get_hot_character()`` (never from LLM text)
and every dice roll goes through ``checked_roll()`` (true randomness + audit).
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.db.models import Campaign, Character
from app.tools.hot_character import get_hot_character, checked_roll


def _ok(narration: str, **kw: Any) -> dict:
    return {"ok": True, "kind": "check_result", "narration": narration, "data": kw or {}}


def _err(narration: str, **kw: Any) -> dict:
    return {"ok": False, "kind": "check_result", "narration": narration, "data": kw or {}}


# ═══════════════════════════════════════════════════════════════════
#  TOOL SCHEMAS
# ═══════════════════════════════════════════════════════════════════

CHECK_TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "ability_check",
            "description": (
                "进行一次属性或技能检定。投 d20 + 调整值。"
                "可指定 DC 来判断成功/失败。可设置优势/劣势。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "ability": {"type": "string", "description": "属性或技能: str/dex/con/int/wis/cha 或 athletics/acrobatics 等"},
                    "dc": {"type": "integer", "description": "难度等级 (DC)"},
                    "advantage": {"type": "boolean", "description": "是否有优势"},
                    "disadvantage": {"type": "boolean", "description": "是否有劣势"},
                    "reason": {"type": "string", "description": "检定原因"},
                },
                "required": ["ability"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "saving_throw",
            "description": "进行一次豁免检定。投 d20 + 豁免加值 vs DC。",
            "parameters": {
                "type": "object",
                "properties": {
                    "ability": {"type": "string", "description": "豁免属性: str/dex/con/int/wis/cha"},
                    "dc": {"type": "integer", "description": "豁免 DC"},
                    "advantage": {"type": "boolean", "description": "是否有优势"},
                    "disadvantage": {"type": "boolean", "description": "是否有劣势"},
                    "reason": {"type": "string", "description": "豁免原因"},
                },
                "required": ["ability", "dc"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "apply_damage",
            "description": "对角色造成伤害，更新 current_hp。自动检查死亡和专注。",
            "parameters": {
                "type": "object",
                "properties": {
                    "character_name": {"type": "string", "description": "受到伤害的角色名"},
                    "amount": {"type": "integer", "description": "伤害值"},
                    "damage_type": {"type": "string", "description": "伤害类型: slashing/piercing/bludgeoning/fire/cold 等"},
                },
                "required": ["character_name", "amount"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "apply_healing",
            "description": "治疗角色，更新 current_hp（不超过 max_hp）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "character_name": {"type": "string", "description": "接受治疗的角色名"},
                    "amount": {"type": "integer", "description": "治疗量"},
                },
                "required": ["character_name", "amount"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "apply_condition",
            "description": "给角色添加状态（如 poisoned, frightened, paralyzed 等）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "character_name": {"type": "string", "description": "角色名"},
                    "condition": {"type": "string", "description": "状态名"},
                },
                "required": ["character_name", "condition"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "remove_condition",
            "description": "移除角色的状态。",
            "parameters": {
                "type": "object",
                "properties": {
                    "character_name": {"type": "string", "description": "角色名"},
                    "condition": {"type": "string", "description": "要移除的状态"},
                },
                "required": ["character_name", "condition"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_character_snapshot",
            "description": "获取角色的完整机械快照（热数据），包含所有 buff/debuff 生效后的实时属性。",
            "parameters": {
                "type": "object",
                "properties": {
                    "character_name": {"type": "string", "description": "角色名（可选，不提供则查当前绑定角色）"},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "undo_damage",
            "description": "撤销最近一次对角色造成的伤害。用户说「撤销伤害」「上次攻击不算」「回退HP」时调用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "character_name": {"type": "string", "description": "要撤销伤害的角色名"},
                },
                "required": ["character_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "undo_healing",
            "description": "撤销最近一次治疗。用户说「撤销治疗」时调用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "character_name": {"type": "string", "description": "角色名"},
                },
                "required": ["character_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "recent_changes",
            "description": "查询最近的 HP 变更历史。用户说「最近改了什么」「HP 变更记录」时调用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "character_name": {"type": "string", "description": "角色名（可选）"},
                },
                "required": [],
            },
        },
    },
]


# ═══════════════════════════════════════════════════════════════════
#  HANDLERS
# ═══════════════════════════════════════════════════════════════════

def handle_ability_check(
    db: Session, campaign: Campaign,
    character: Character | None = None,  # injected by llm_loop
    ability: str = "", dc: int | None = None,
    advantage: bool = False, disadvantage: bool = False,
    reason: str = "",
    **_kw: Any,
) -> dict:
    if not character:
        return _err("未找到当前角色。")

    hot = get_hot_character(db, character, combat=False)
    if not hot:
        return _err("无法获取角色热数据。")

    skill_abilities = {
        "athletics": "str", "acrobatics": "dex", "sleight_of_hand": "dex",
        "stealth": "dex", "arcana": "int", "history": "int", "investigation": "int",
        "nature": "int", "religion": "int", "animal_handling": "wis", "insight": "wis",
        "medicine": "wis", "perception": "wis", "survival": "wis",
        "deception": "cha", "intimidation": "cha", "performance": "cha", "persuasion": "cha",
    }

    if ability in skill_abilities:
        skill = hot.skills.get(ability)
        if skill:
            bonus = skill.bonus
            label = f"{ability}({skill.bonus:+d})"
        else:
            bonus = hot.abilities.get(skill_abilities[ability], hot.abilities.get("str", 0)).modifier if hasattr(hot.abilities.get("str", None), "modifier") else 0
            label = f"{ability}({bonus:+d})"
    elif ability in {"str", "dex", "con", "int", "wis", "cha"}:
        ab = hot.abilities.get(ability)
        if ab:
            bonus = ab.modifier
            label = f"{ability.upper()}({bonus:+d})"
        else:
            return _err(f"未知属性: {ability}")
    else:
        return _err(f"未知属性或技能: {ability}")

    # Roll with advantage/disadvantage
    if advantage and not disadvantage:
        roll_result = checked_roll(db, f"2d20k1h+{bonus}", campaign.id, hot.character_id, reason or "ability_check", "ability_check")
    elif disadvantage and not advantage:
        roll_result = checked_roll(db, f"2d20k1l+{bonus}", campaign.id, hot.character_id, reason or "ability_check", "ability_check")
    else:
        roll_result = checked_roll(db, f"1d20+{bonus}", campaign.id, hot.character_id, reason or "ability_check", "ability_check")

    total = roll_result["total"]
    narration = f"🎯 {hot.character_name} {label}检定"
    if reason:
        narration += f"（{reason}）"
    narration += f"：d20+{bonus} = {total}"
    if dc is not None:
        success = total >= dc
        narration += f" vs DC{dc} → {'成功' if success else '失败'}"

    return _ok(narration, ability=ability, bonus=bonus, roll=total, dc=dc,
               success=(total >= dc) if dc is not None else None,
               character_id=hot.character_id, turn_consuming=True)


def handle_saving_throw(
    db: Session, campaign: Campaign,
    character: Character | None = None,
    ability: str = "", dc: int = 0,
    advantage: bool = False, disadvantage: bool = False,
    reason: str = "",
    **_kw: Any,
) -> dict:
    if not character:
        return _err("未找到当前角色。")
    hot = get_hot_character(db, character, combat=True)
    if not hot:
        return _err("无法获取角色热数据。")

    if ability not in {"str", "dex", "con", "int", "wis", "cha"}:
        return _err(f"未知豁免属性: {ability}")

    save_bonus = hot.saving_throws.get(ability, 0)

    if advantage and not disadvantage:
        roll_result = checked_roll(db, f"2d20k1h+{save_bonus}", campaign.id, hot.character_id, reason or "saving_throw", "saving_throw")
    elif disadvantage and not advantage:
        roll_result = checked_roll(db, f"2d20k1l+{save_bonus}", campaign.id, hot.character_id, reason or "saving_throw", "saving_throw")
    else:
        roll_result = checked_roll(db, f"1d20+{save_bonus}", campaign.id, hot.character_id, reason or "saving_throw", "saving_throw")

    total = roll_result["total"]
    success = total >= dc
    narration = (
        f"🛡️ {hot.character_name} {ability.upper()}豁免"
        + (f"（{reason}）" if reason else "")
        + f"：d20+{save_bonus} = {total} vs DC{dc} → {'通过' if success else '失败'}"
    )
    return _ok(narration, ability=ability, save_bonus=save_bonus, roll=total,
               dc=dc, success=success, character_id=hot.character_id,
               turn_consuming=True)


def handle_apply_damage(
    db: Session, campaign: Campaign,
    character: Character | None = None,
    character_name: str = "", amount: int = 0,
    damage_type: str = "",
    **_kw: Any,
) -> dict:
    # character is injected by llm_loop; fallback: find by name
    if not character and character_name:
        from sqlalchemy import select
        character = db.scalar(
            select(Character).where(
                Character.campaign_id == campaign.id,
                Character.character_name == character_name,
            )
        )
    if not character:
        return _err(f"未找到角色: {character_name}")

    data = character.data or {}
    combat = data.get("combat", {})
    before = int(combat.get("current_hp", 0))
    after = max(0, before - amount)
    combat["current_hp"] = after
    data["combat"] = combat
    character.data = data
    db.commit()
    from app.tools.hot_character import record_character_change
    record_character_change(db, character, "apply_damage",
                            {"current_hp": before}, {"current_hp": after},
                            f"{amount} {damage_type or 'damage'}")

    narration = f"💥 {character.character_name} 受到 {amount} 点{damage_type or '伤害'}：HP {before}→{after}"
    if after <= 0:
        narration += "\n⚠️ 生命值归零！需要进行死亡豁免。"
    return _ok(narration, character_id=character.id, before_hp=before,
               after_hp=after, damage=amount, damage_type=damage_type,
               turn_consuming=True)


def handle_apply_healing(
    db: Session, campaign: Campaign,
    character: Character | None = None,
    character_name: str = "", amount: int = 0,
    **_kw: Any,
) -> dict:
    if not character and character_name:
        from sqlalchemy import select
        character = db.scalar(
            select(Character).where(
                Character.campaign_id == campaign.id,
                Character.character_name == character_name,
            )
        )
    if not character:
        return _err(f"未找到角色: {character_name}")

    data = character.data or {}
    combat = data.get("combat", {})
    before = int(combat.get("current_hp", 0))
    maximum = int(combat.get("max_hp", before))
    after = min(maximum, before + amount)
    combat["current_hp"] = after
    data["combat"] = combat
    character.data = data
    db.commit()
    from app.tools.hot_character import record_character_change
    record_character_change(db, character, "apply_healing",
                            {"current_hp": before}, {"current_hp": after},
                            f"healed {amount}")

    narration = f"💚 {character.character_name} 恢复 {amount} 点生命：HP {before}→{after}"
    return _ok(narration, character_id=character.id, before_hp=before,
               after_hp=after, healing=amount, turn_consuming=True)


def handle_apply_condition(
    db: Session, campaign: Campaign,
    character: Character | None = None,
    character_name: str = "", condition: str = "",
    **_kw: Any,
) -> dict:
    if not character and character_name:
        from sqlalchemy import select
        character = db.scalar(
            select(Character).where(
                Character.campaign_id == campaign.id,
                Character.character_name == character_name,
            )
        )
    if not character:
        return _err(f"未找到角色: {character_name}")

    data = character.data or {}
    conditions = list(data.get("conditions") or [])
    if condition not in conditions:
        conditions.append(condition)
    data["conditions"] = conditions
    character.data = data
    db.commit()

    return _ok(f"🏷️ {character.character_name} 获得状态: {condition}",
               character_id=character.id, condition=condition,
               conditions=conditions, turn_consuming=True)


def handle_remove_condition(
    db: Session, campaign: Campaign,
    character: Character | None = None,
    character_name: str = "", condition: str = "",
    **_kw: Any,
) -> dict:
    if not character and character_name:
        from sqlalchemy import select
        character = db.scalar(
            select(Character).where(
                Character.campaign_id == campaign.id,
                Character.character_name == character_name,
            )
        )
    if not character:
        return _err(f"未找到角色: {character_name}")

    data = character.data or {}
    conditions = list(data.get("conditions") or [])
    if condition in conditions:
        conditions.remove(condition)
    data["conditions"] = conditions
    character.data = data
    db.commit()

    return _ok(f"🏷️ {character.character_name} 移除状态: {condition}",
               character_id=character.id, condition=condition,
               conditions=conditions, turn_consuming=False)


def handle_get_character_snapshot(
    db: Session, campaign: Campaign,
    character: Character | None = None,
    character_name: str = "",
    **_kw: Any,
) -> dict:
    if not character and character_name:
        from sqlalchemy import select
        character = db.scalar(
            select(Character).where(
                Character.campaign_id == campaign.id,
                Character.character_name == character_name,
            )
        )
    if not character:
        return _err(f"未找到角色: {character_name or '当前绑定角色'}")

    hot = get_hot_character(db, character, combat=False)
    if not hot:
        return _err("无法获取角色热数据。")
    return _ok(f"{hot.character_name} 的实时机械快照",
               snapshot=hot.to_json(), turn_consuming=False)


def handle_undo_damage(
    db: Session, campaign: Campaign,
    character: Character | None = None,
    character_name: str = "",
    **_kw: Any,
) -> dict:
    """Undo the most recent apply_damage for this character using CharacterChange records."""
    from app.db.models import CharacterChange
    from sqlalchemy import select as _sel, desc as _desc

    if not character and character_name:
        character = db.scalar(
            _sel(Character).where(
                Character.campaign_id == campaign.id,
                Character.character_name == character_name,
            )
        )
    if not character:
        return _err(f"未找到角色: {character_name}")

    # Find most recent apply_damage from CharacterChange (hot value history)
    change = db.scalar(
        _sel(CharacterChange)
        .where(CharacterChange.character_id == character.id,
               CharacterChange.change_type == "apply_damage")
        .order_by(_desc(CharacterChange.created_at))
        .limit(1)
    )
    if not change:
        return _err(f"{character.character_name} 最近没有伤害记录。")

    before_hp = int((change.before_data or {}).get("current_hp", 0))
    after_hp = int((change.after_data or {}).get("current_hp", 0))
    amount = before_hp - after_hp

    data = character.data or {}
    combat = data.get("combat", {})
    current = int(combat.get("current_hp", 0))
    maximum = int(combat.get("max_hp", current))
    restored = min(maximum, current + max(0, amount))
    combat["current_hp"] = restored
    data["combat"] = combat
    character.data = data
    from app.tools.hot_character import record_character_change
    record_character_change(db, character, "undo_damage",
                            {"current_hp": current}, {"current_hp": restored},
                            f"undid {amount} damage")
    db.commit()

    return _ok(
        f"↩️ {character.character_name} 已撤销 {amount} 点伤害：HP {current}→{restored}",
        character_id=character.id, before_hp=current, after_hp=restored, undone_amount=amount,
        turn_consuming=False,
    )


def handle_undo_healing(
    db: Session, campaign: Campaign,
    character: Character | None = None,
    character_name: str = "",
    **_kw: Any,
) -> dict:
    """Undo the most recent apply_healing using CharacterChange records."""
    from app.db.models import CharacterChange
    from sqlalchemy import select as _sel, desc as _desc

    if not character and character_name:
        character = db.scalar(
            _sel(Character).where(
                Character.campaign_id == campaign.id,
                Character.character_name == character_name,
            )
        )
    if not character:
        return _err(f"未找到角色: {character_name}")

    change = db.scalar(
        _sel(CharacterChange)
        .where(CharacterChange.character_id == character.id,
               CharacterChange.change_type == "apply_healing")
        .order_by(_desc(CharacterChange.created_at))
        .limit(1)
    )
    if not change:
        return _err(f"{character.character_name} 最近没有治疗记录。")

    before_hp = int((change.before_data or {}).get("current_hp", 0))
    after_hp = int((change.after_data or {}).get("current_hp", 0))
    amount = after_hp - before_hp

    data = character.data or {}
    combat = data.get("combat", {})
    current = int(combat.get("current_hp", 0))
    restored = max(0, current - max(0, amount))
    combat["current_hp"] = restored
    data["combat"] = combat
    character.data = data
    from app.tools.hot_character import record_character_change
    record_character_change(db, character, "undo_healing",
                            {"current_hp": current}, {"current_hp": restored},
                            f"undid {amount} healing")
    db.commit()

    return _ok(
        f"↩️ {character.character_name} 已撤销 {amount} 点治疗：HP {current}→{restored}",
        character_id=character.id, before_hp=current, after_hp=restored, undone_amount=amount,
        turn_consuming=False,
    )


def handle_recent_changes(
    db: Session, campaign: Campaign,
    character: Character | None = None,
    character_name: str = "",
    **_kw: Any,
) -> dict:
    """Show recent HP changes for a character."""
    from app.db.models import DiceAuditLog
    from sqlalchemy import select as _sel, desc as _desc

    filter_kwargs = {"campaign_id": campaign.id}
    if character:
        filter_kwargs["character_id"] = character.id
    elif character_name:
        ch = db.scalar(
            _sel(Character).where(
                Character.campaign_id == campaign.id,
                Character.character_name == character_name,
            )
        )
        if ch:
            filter_kwargs["character_id"] = ch.id

    logs = db.scalars(
        _sel(DiceAuditLog)
        .where(
            DiceAuditLog.campaign_id == filter_kwargs["campaign_id"],
            DiceAuditLog.character_id == filter_kwargs.get("character_id", ""),
            DiceAuditLog.tool_name.in_(["apply_damage", "apply_healing", "undo_damage", "undo_healing"]),
        )
        .order_by(_desc(DiceAuditLog.created_at))
        .limit(10)
    ).all()

    if not logs:
        return _ok("没有找到 HP 变更记录。", turn_consuming=False)

    lines = ["=== 最近 HP 变更 ==="]
    for log in logs:
        detail = log.roll_detail or {}
        tool = log.tool_name
        amount = detail.get("damage", detail.get("healing", detail.get("undone_amount", log.roll_result)))
        before = detail.get("before_hp", "?")
        after = detail.get("after_hp", "?")
        lines.append(f"  [{tool}] {before}→{after} ({amount:+d}) | {log.context[:30]}")
    return _ok("\n".join(lines), turn_consuming=False)


# ═══════════════════════════════════════════════════════════════════
#  REGISTRY
# ═══════════════════════════════════════════════════════════════════

CHECK_HANDLERS: dict[str, Any] = {
    "ability_check": handle_ability_check,
    "saving_throw": handle_saving_throw,
    "apply_damage": handle_apply_damage,
    "apply_healing": handle_apply_healing,
    "apply_condition": handle_apply_condition,
    "remove_condition": handle_remove_condition,
    "get_character_snapshot": handle_get_character_snapshot,
    "undo_damage": handle_undo_damage,
    "undo_healing": handle_undo_healing,
    "recent_changes": handle_recent_changes,
}
