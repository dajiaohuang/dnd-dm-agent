"""Combat action tools for D&D 5E turn-based combat.

These tools are called by the LLM during combat turns. They handle
the mechanical resolution of player/NPC combat actions.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.db.models import Campaign, Character
from app.tools.dice import roll_dice


def _ok(narration: str, **kw: Any) -> dict:
    return {"ok": True, "kind": "combat_action", "narration": narration, "data": kw or {}}


def _err(narration: str, **kw: Any) -> dict:
    return {"ok": False, "kind": "combat_action", "narration": narration, "data": kw or {}}


# ═══════════════════════════════════════════════════════════════════
#  COMBAT TOOL SCHEMAS
# ═══════════════════════════════════════════════════════════════════

COMBAT_TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "combat_attack",
            "description": (
                "进行一次近战或远程武器攻击。自动投攻击骰(d20+加值)和伤害骰。"
                "如果角色有额外攻击特性，需要指定攻击次数。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "target": {"type": "string", "description": "攻击目标（角色名）"},
                    "weapon": {"type": "string", "description": "使用的武器名，如 长剑/长弓/徒手"},
                    "attack_index": {"type": "integer", "description": "第几次攻击（1=第一次），默认1", "default": 1},
                },
                "required": ["target"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "combat_cast_spell",
            "description": (
                "施放一个法术。自动计算法术DC、投伤害骰。"
                "需要指定法术名、环阶和目标。如果法术需要攻击检定，自动投。"
                "如果法术需要豁免，说明豁免类型（敏捷/体质等）。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "spell_name": {"type": "string", "description": "法术名称"},
                    "spell_level": {"type": "integer", "description": "施法环阶（戏法=0）"},
                    "targets": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "目标角色名列表",
                    },
                    "save_type": {"type": "string", "description": "豁免类型：dex/con/wis/int/str/cha"},
                    "use_bonus_action": {"type": "boolean", "description": "是否用附赠动作施法"},
                    "use_reaction": {"type": "boolean", "description": "是否用反应施法"},
                },
                "required": ["spell_name", "spell_level"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "combat_ability_check",
            "description": (
                "进行一次属性检定（力量/敏捷/体质/智力/感知/魅力），或技能检定。"
                "用于推撞、 grappling、躲藏等需要检定的战斗行动。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "ability": {"type": "string", "description": "属性或技能名：str/dex/con/int/wis/cha 或 athletics/acrobatics 等"},
                    "target": {"type": "string", "description": "对抗目标（可选，用于 contested check）"},
                    "reason": {"type": "string", "description": "检定原因，如 推撞/擒抱/躲藏"},
                },
                "required": ["ability"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "combat_dash",
            "description": "使用动作疾走，本回合速度翻倍。",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "combat_disengage",
            "description": "使用动作撤退脱离，本回合移动不触发借机攻击。",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "combat_dodge",
            "description": "使用动作闪避，攻击你的敌人有劣势，你敏捷豁免有优势。",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "ask_clarification",
            "description": (
                "当用户描述的行动信息不足时，用此工具向用户追问。"
                "例如：用哪个法术？几环？目标是谁？用动作还是附赠动作？"
                "不要猜测用户意图，必须追问后才能继续。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "question": {"type": "string", "description": "向用户追问的问题"},
                    "options": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "可选项列表（可选）",
                    },
                },
                "required": ["question"],
            },
        },
    },
]


# ═══════════════════════════════════════════════════════════════════
#  COMBAT TOOL HANDLERS
# ═══════════════════════════════════════════════════════════════════

def handle_combat_attack(
    db: Session, campaign: Campaign,
    character: Character | None = None,
    target: str = "", weapon: str = "",
    attack_index: int = 1,
    **_kw: Any,
) -> dict:
    """Resolve a weapon attack roll."""
    if not character:
        return _err("未找到当前行动角色。")
    char_data = character.data
    abilities = char_data.get("abilities", {})
    prof_bonus = (char_data.get("combat", {}) or {}).get("proficiency_bonus", 2)

    # Find weapon stats
    inventory = char_data.get("inventory") or []
    weapon_item = None
    if weapon:
        weapon_item = next((i for i in inventory if i.get("name", "").lower() == weapon.lower()), None)
    if not weapon_item:
        weapon_item = next((i for i in inventory if i.get("item_type") == "weapon" and i.get("equipped")), None)
    if not weapon_item and not weapon:
        weapon_item = next((i for i in inventory if i.get("item_type") == "weapon"), None)

    if weapon_item:
        weapon_name = weapon_item.get("name", weapon or "武器")
        damage = weapon_item.get("damage", "1d6")
        attack_bonus = int(weapon_item.get("attack_bonus", 0))
        damage_type = weapon_item.get("damage_type", "钝击")
        # Determine if finesse/ranged → use DEX
        props = [p.lower() for p in weapon_item.get("properties", [])]
        if "finesse" in props or "ranged" in props:
            ability_mod = abilities.get("dex", 10) // 2 - 5
            ability_name = "敏捷"
        else:
            ability_mod = abilities.get("str", 10) // 2 - 5
            ability_name = "力量"
        if not attack_bonus:
            attack_bonus = ability_mod + prof_bonus
    else:
        weapon_name = weapon or "徒手"
        damage = "1d1"
        attack_bonus = abilities.get("str", 10) // 2 - 5 + prof_bonus
        damage_type = "钝击"
        ability_name = "力量"

    attack_roll = roll_dice(f"1d20+{attack_bonus}")
    dmg_roll = roll_dice(damage)
    total_damage = dmg_roll["total"] + max(0, ability_mod := (abilities.get("str", 10) // 2 - 5))

    narration = (
        f"⚔️ {character.character_name} 用{weapon_name}攻击 {target}：\n"
        f"   攻击检定 d20+{attack_bonus} = {attack_roll['total']}\n"
        f"   伤害 {damage} = {dmg_roll['total']}（{damage_type}）"
    )
    return _ok(
        narration,
        attack_roll=attack_roll["total"],
        attack_bonus=attack_bonus,
        damage=total_damage,
        damage_type=damage_type,
        weapon=weapon_name,
        target=target,
        rolls=[attack_roll, dmg_roll],
        turn_consuming=True,
    )


def handle_combat_cast_spell(
    db: Session, campaign: Campaign,
    character: Character | None = None,
    spell_name: str = "", spell_level: int = 0,
    targets: list[str] | None = None,
    save_type: str = "", use_bonus_action: bool = False,
    use_reaction: bool = False,
    **_kw: Any,
) -> dict:
    """Resolve a spell cast."""
    if not character:
        return _err("未找到当前行动角色。")
    char_data = character.data
    abilities = char_data.get("abilities", {})
    prof_bonus = (char_data.get("combat", {}) or {}).get("proficiency_bonus", 2)
    spellcasting = char_data.get("spellcasting", {}) or {}
    save_dc = spellcasting.get("save_dc") or (8 + prof_bonus + max(
        abilities.get(spellcasting.get("ability", "int"), 10) // 2 - 5, 0
    ))
    attack_bonus = spellcasting.get("attack_bonus") or prof_bonus

    # Find spell in character's spells
    spells = char_data.get("spells") or []
    spell_data = next((s for s in spells if s.get("name", "").lower() == spell_name.lower()), None)

    action_type = "附赠动作" if use_bonus_action else "反应" if use_reaction else "动作"
    targets_str = "、".join(targets) if targets else "（无目标）"

    narration = (
        f"✨ {character.character_name} 用{action_type}施放 {spell_name}（{spell_level}环）：\n"
        f"   目标：{targets_str}\n"
        f"   法术DC：{save_dc}"
    )

    if spell_data and spell_data.get("requires_attack_roll"):
        spell_roll = roll_dice(f"1d20+{attack_bonus}")
        narration += f"\n   法术攻击 d20+{attack_bonus} = {spell_roll['total']}"
        return _ok(
            narration,
            spell_name=spell_name, spell_level=spell_level,
            save_dc=save_dc, attack_roll=spell_roll["total"],
            targets=targets, action_type=action_type,
            rolls=[spell_roll], turn_consuming=True,
        )

    if save_type:
        narration += f"\n   目标需进行 {save_type.upper()} 豁免（DC {save_dc}）"
    narration += "\n   请 DM 或系统根据目标属性投豁免并结算伤害。"

    return _ok(
        narration,
        spell_name=spell_name, spell_level=spell_level,
        save_dc=save_dc, save_type=save_type,
        targets=targets, action_type=action_type,
        turn_consuming=True,
    )


def handle_combat_ability_check(
    db: Session, campaign: Campaign,
    character: Character | None = None,
    ability: str = "", target: str = "", reason: str = "",
    **_kw: Any,
) -> dict:
    """Resolve an ability/skill check in combat."""
    if not character:
        return _err("未找到当前行动角色。")
    char_data = character.data
    abilities = char_data.get("abilities", {})
    prof_bonus = (char_data.get("combat", {}) or {}).get("proficiency_bonus", 2)

    # Map ability or skill to modifier
    skill_abilities = {
        "athletics": "str", "acrobatics": "dex", "sleight_of_hand": "dex",
        "stealth": "dex", "arcana": "int", "history": "int", "investigation": "int",
        "nature": "int", "religion": "int", "animal_handling": "wis", "insight": "wis",
        "medicine": "wis", "perception": "wis", "survival": "wis",
        "deception": "cha", "intimidation": "cha", "performance": "cha", "persuasion": "cha",
    }
    abv_to_full = {"str": "力量", "dex": "敏捷", "con": "体质", "int": "智力", "wis": "感知", "cha": "魅力"}

    if ability in skill_abilities:
        skill_name = ability
        ability_key = skill_abilities[ability]
        skills = char_data.get("skills", {})
        skill_info = skills.get(ability, {})
        if isinstance(skill_info, dict):
            bonus = skill_info.get("bonus", abilities.get(ability_key, 10) // 2 - 5)
        else:
            bonus = abilities.get(ability_key, 10) // 2 - 5 + (prof_bonus if skill_info else 0)
    elif ability in {"str", "dex", "con", "int", "wis", "cha"}:
        skill_name = abv_to_full.get(ability, ability)
        ability_key = ability
        bonus = abilities.get(ability_key, 10) // 2 - 5
    else:
        return _err(f"未知属性或技能：{ability}")

    roll = roll_dice(f"1d20+{bonus}")
    reason_text = f"（{reason}）" if reason else ""

    narration = (
        f"🎯 {character.character_name} {skill_name}检定{reason_text}：\n"
        f"   d20+{bonus} = {roll['total']}"
    )
    if target:
        narration += f"\n   对抗目标：{target}"
    return _ok(
        narration,
        ability=ability, bonus=bonus, roll=roll["total"],
        target=target, reason=reason, rolls=[roll],
        turn_consuming=True,
    )


def handle_dash(**_kw: Any) -> dict:
    character = _kw.get("character")
    name = character.character_name if character else "角色"
    return _ok(f"🏃 {name} 使用动作疾走，本回合速度翻倍。", turn_consuming=True)


def handle_disengage(**_kw: Any) -> dict:
    character = _kw.get("character")
    name = character.character_name if character else "角色"
    return _ok(f"🛡️ {name} 使用动作撤退脱离，移动不触发借机攻击。", turn_consuming=True)


def handle_dodge(**_kw: Any) -> dict:
    character = _kw.get("character")
    name = character.character_name if character else "角色"
    return _ok(f"👁️ {name} 使用动作闪避。攻击有劣势，敏捷豁免有优势。", turn_consuming=True)


def handle_ask_clarification(
    character: Character | None = None,
    question: str = "", options: list[str] | None = None,
    **_kw: Any,
) -> dict:
    """Ask the user for clarification — does NOT consume the turn."""
    narration = f"❓ {question}"
    if options:
        narration += "\n可选项：" + " / ".join(options)
    return _ok(
        narration,
        question=question, options=options,
        turn_consuming=False,  # Don't advance turn for clarifications!
    )


# ═══════════════════════════════════════════════════════════════════
#  HANDLER REGISTRY
# ═══════════════════════════════════════════════════════════════════

COMBAT_HANDLERS: dict[str, Any] = {
    "combat_attack": handle_combat_attack,
    "combat_cast_spell": handle_combat_cast_spell,
    "combat_ability_check": handle_combat_ability_check,
    "combat_dash": handle_dash,
    "combat_disengage": handle_disengage,
    "combat_dodge": handle_dodge,
    "ask_clarification": handle_ask_clarification,
}
