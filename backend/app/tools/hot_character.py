"""Hot structured character snapshot.

Every tool that reads character attributes MUST go through
``get_hot_character()``.  LLM system prompts carry a compact JSON
rendering of the hot snapshot so the model never hallucinates
mechanical values.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

from app.db.models import Character
from app.tools.character_rules import ABILITY_KEYS, SKILL_ABILITIES, ability_modifier
from app.tools.effect_engine import resolve_effective_character


# ── Dataclasses ──────────────────────────────────────────────────

@dataclass
class HotAbility:
    score: int
    modifier: int  # (score // 2) - 5


@dataclass
class HotSkill:
    bonus: int
    proficient: bool = False
    expertise: bool = False
    advantage: bool = False
    disadvantage: bool = False


@dataclass
class HotAttack:
    name: str
    bonus: int
    damage: str
    damage_type: str = ""
    properties: list[str] = field(default_factory=list)


@dataclass
class HotSpellSlotInfo:
    current: int
    maximum: int


@dataclass
class HotSnapshot:
    character_id: str
    character_name: str
    actor_type: str               # "player" / "npc" / "monster"
    level: int
    proficiency_bonus: int

    # Abilities
    abilities: dict[str, HotAbility] = field(default_factory=dict)

    # Combat
    armor_class: int = 10
    current_hp: int = 0
    max_hp: int = 1
    temp_hp: int = 0
    speed: int = 30
    initiative: int = 0
    passive_perception: int = 10
    carrying_capacity: int = 0

    # Saving throws
    saving_throws: dict[str, int] = field(default_factory=dict)

    # Skills
    skills: dict[str, HotSkill] = field(default_factory=dict)

    # Attacks (derived from equipped weapons)
    attacks: list[HotAttack] = field(default_factory=list)

    # Spellcasting
    spell_ability: str = ""
    spell_dc: int | None = None
    spell_attack_bonus: int | None = None
    spell_slots: dict[int, HotSpellSlotInfo] = field(default_factory=dict)

    # Conditions (from active_effects that apply status)
    conditions: list[str] = field(default_factory=list)

    # Traceability – which effect contributed to each value
    sources: dict[str, list[str]] = field(default_factory=dict)

    def to_compact_json(self) -> dict[str, Any]:
        """Return a minimal dict suitable for injection into an LLM system prompt."""
        return {
            "character_name": self.character_name,
            "actor_type": self.actor_type,
            "level": self.level,
            "proficiency_bonus": self.proficiency_bonus,
            "abilities": {k: {"score": v.score, "mod": v.modifier}
                          for k, v in self.abilities.items()},
            "armor_class": self.armor_class,
            "current_hp": self.current_hp,
            "max_hp": self.max_hp,
            "temp_hp": self.temp_hp,
            "speed": self.speed,
            "initiative": self.initiative,
            "passive_perception": self.passive_perception,
            "saving_throws": self.saving_throws,
            "skills": {k: {"bonus": v.bonus}
                       for k, v in self.skills.items()},
            "attacks": [{"name": a.name, "bonus": a.bonus, "damage": a.damage,
                         "damage_type": a.damage_type}
                        for a in self.attacks],
            "spell_ability": self.spell_ability,
            "spell_dc": self.spell_dc,
            "spell_attack_bonus": self.spell_attack_bonus,
            "conditions": self.conditions,
        }

    def to_json(self) -> dict[str, Any]:
        """Full snapshot including sources."""
        return {
            **self.to_compact_json(),
            "character_id": self.character_id,
            "skills": {k: {"bonus": v.bonus, "proficient": v.proficient,
                           "expertise": v.expertise, "advantage": v.advantage,
                           "disadvantage": v.disadvantage}
                       for k, v in self.skills.items()},
            "spell_slots": {str(k): {"current": v.current, "maximum": v.maximum}
                            for k, v in self.spell_slots.items()},
            "sources": self.sources,
        }


# ── Builder ───────────────────────────────────────────────────────

def get_hot_character(db: Session | None, character_id_or_obj: str | Character,
                      combat: bool = False) -> HotSnapshot | None:
    """Return a **live** mechanical snapshot of the character.

    Sources of truth (in order):
    1. ``characters.data`` — the base character sheet (JSON)
    2. ``active_effects`` — buffs / debuffs / equipment / temporary mods
    3. ``resolve_effective_character()`` — the existing effect engine

    Every LLM tool that needs a character value MUST call this function.
    The LLM itself never holds raw numbers; those are injected in the
    system prompt via ``to_compact_json()``.
    """
    # Resolve character
    if isinstance(character_id_or_obj, Character):
        char = character_id_or_obj
    elif db is not None:
        char = db.get(Character, character_id_or_obj)
    else:
        return None
    if not char:
        return None

    data = char.data or {}
    basic = data.get("basic") or {}
    classes = basic.get("classes") or [{}]
    primary = classes[0] if classes else {}
    actor_type = basic.get("actor_type", "player")

    # ── Run the effect engine ──
    snapshot = resolve_effective_character(data, combat=combat)
    effective = snapshot.get("effective") or {}
    eff_abilities = effective.get("abilities", {})
    eff_modifiers = effective.get("ability_modifiers", {})
    eff_combat = effective.get("combat", {})
    eff_saves = effective.get("saving_throws", {})
    eff_skills = effective.get("skills", {})
    eff_spellcasting = effective.get("spellcasting", {})
    eff_roll_effects = effective.get("roll_effects", [])
    raw_effects = effective.get("active_effects", [])

    # ── Build HotSnapshot ──

    # Abilities
    abilities: dict[str, HotAbility] = {}
    for key in ABILITY_KEYS:
        score = int(eff_abilities.get(key, 10))
        modifiers = int(eff_modifiers.get(key, ability_modifier(score)))
        abilities[key] = HotAbility(score=score, modifier=modifiers)

    # Combat
    armor_class = int(eff_combat.get("armor_class",
                      data.get("combat", {}).get("armor_class", 10)))
    current_hp = int(eff_combat.get("current_hp",
                      data.get("combat", {}).get("current_hp", 0)))
    max_hp = int(eff_combat.get("max_hp",
                  data.get("combat", {}).get("max_hp", 1)))
    temp_hp = int(eff_combat.get("temp_hp",
                  data.get("combat", {}).get("temp_hp", 0)))
    speed = int(eff_combat.get("speed",
                data.get("combat", {}).get("speed", 30)))
    initiative = int(eff_combat.get("initiative",
                     data.get("combat", {}).get("initiative",
                     abilities.get("dex", HotAbility(10, 0)).modifier)))
    perception_mod = abilities.get("wis", HotAbility(10, 0)).modifier
    # Perception skill bonus (will be refined in skills loop below)
    _perception_eff = eff_skills.get("perception", 0)
    if isinstance(_perception_eff, dict):
        perception_mod = int(_perception_eff.get("bonus", perception_mod))
    else:
        perception_mod = int(_perception_eff) if _perception_eff else perception_mod
    passive_perception = int(eff_combat.get("passive_perception",
                              data.get("combat", {}).get("passive_perception",
                              10 + perception_mod)))

    # Saving throws
    saving_throws: dict[str, int] = {}
    for key in ABILITY_KEYS:
        val = eff_saves.get(key, 0)
        if isinstance(val, dict):
            saving_throws[key] = int(val.get("bonus", abilities[key].modifier))
        else:
            saving_throws[key] = int(val)

    # Skills
    skills: dict[str, HotSkill] = {}
    base_skills = data.get("skills") or {}
    for skill_name, ability_key in SKILL_ABILITIES.items():
        eff_val = eff_skills.get(skill_name, 0)
        base_val = base_skills.get(skill_name, {})
        if isinstance(eff_val, dict):
            bonus = int(eff_val.get("bonus", abilities[ability_key].modifier))
        else:
            bonus = int(eff_val)

        proficient = bool(base_val.get("proficient", False)) if isinstance(base_val, dict) else False
        expertise = bool(base_val.get("expertise", False)) if isinstance(base_val, dict) else False

        # Check roll_effects for advantage/disadvantage on this skill
        adv = False
        dis = False
        for re in eff_roll_effects:
            target = str(re.get("target", ""))
            if f"skills.{skill_name}" in target or f"ability.{ability_key}" in target:
                if re.get("operation") == "advantage":
                    adv = True
                elif re.get("operation") == "disadvantage":
                    dis = True

        skills[skill_name] = HotSkill(
            bonus=bonus, proficient=proficient, expertise=expertise,
            advantage=adv, disadvantage=dis,
        )

    # Attacks
    inventory = data.get("inventory") or []
    attacks: list[HotAttack] = []
    for item in inventory:
        if not isinstance(item, dict):
            continue
        if item.get("item_type") == "weapon" and item.get("equipped"):
            props = item.get("properties") or []
            if "finesse" in [p.lower() for p in props] or "ranged" in [p.lower() for p in props]:
                ability_mod = abilities.get("dex", HotAbility(10, 0)).modifier
            else:
                ability_mod = abilities.get("str", HotAbility(10, 0)).modifier
            prof = proficiency_bonus(eff_combat.get("proficiency_bonus",
                (2 + (int(primary.get("level", 1)) - 1) // 4)))
            attack_bonus = int(item.get("attack_bonus", 0)) or (
                ability_mod + prof
            )
            # Apply effect modifiers to attack
            attack_bonus = _apply_numeric_effects(
                eff_roll_effects, f"attacks.{item.get('name','')}", attack_bonus,
            )
            attacks.append(HotAttack(
                name=item.get("name", "武器"),
                bonus=attack_bonus,
                damage=item.get("damage", "1d6"),
                damage_type=item.get("damage_type", "钝击"),
                properties=props,
            ))

    # Spellcasting
    spellcasting = data.get("spellcasting") or {}
    spell_ability = spellcasting.get("ability", "")
    spell_dc = eff_spellcasting.get("save_dc") or spellcasting.get("save_dc")
    spell_atk = eff_spellcasting.get("attack_bonus") or spellcasting.get("attack_bonus")

    # Spell slots
    spell_slots: dict[int, HotSpellSlotInfo] = {}
    raw_slots = data.get("spell_slots") or {}
    for level in range(0, 10):
        level_key = str(level)
        current = raw_slots.get(level_key, 0)
        maximum = raw_slots.get(f"{level}_max", current)
        if maximum:
            spell_slots[level] = HotSpellSlotInfo(current=int(current), maximum=int(maximum))

    # Conditions
    conditions: list[str] = []
    sources: dict[str, list[str]] = {"ac": [], "abilities": [], "attacks": [], "saves": []}
    for effect in raw_effects:
        eff_name = effect.get("name") or effect.get("definition_id", "unknown")
        if effect.get("status") != "active":
            continue
        for modifier in effect.get("modifiers") or []:
            target = str(modifier.get("target", ""))
            if modifier.get("operation") == "condition":
                cond = str(modifier.get("value", ""))
                if cond and cond not in conditions:
                    conditions.append(cond)
            # Track sources
            if "armor_class" in target:
                sources.setdefault("ac", []).append(eff_name)
            if target.startswith("abilities."):
                sources.setdefault("abilities", []).append(eff_name)
            if "attack" in target:
                sources.setdefault("attacks", []).append(eff_name)
            if target.startswith("saving_throws."):
                sources.setdefault("saves", []).append(eff_name)

    # Existing conditions from character data
    for cond in data.get("conditions") or []:
        if isinstance(cond, str) and cond not in conditions:
            conditions.append(cond)

    return HotSnapshot(
        character_id=char.id,
        character_name=char.character_name or "未命名",
        actor_type=actor_type,
        level=int(primary.get("level", 1)),
        proficiency_bonus=int(eff_combat.get("proficiency_bonus",
            (2 + (int(primary.get("level", 1)) - 1) // 4))),
        abilities=abilities,
        armor_class=armor_class,
        current_hp=current_hp,
        max_hp=max_hp,
        temp_hp=temp_hp,
        speed=speed,
        initiative=initiative,
        passive_perception=passive_perception,
        carrying_capacity=int(eff_combat.get("carrying_capacity", abilities.get("str", HotAbility(10, 0)).score * 15)),
        saving_throws=saving_throws,
        skills=skills,
        attacks=attacks,
        spell_ability=spell_ability,
        spell_dc=int(spell_dc) if spell_dc else None,
        spell_attack_bonus=int(spell_atk) if spell_atk else None,
        spell_slots=spell_slots,
        conditions=conditions,
        sources=sources,
    )


# ── Helpers ───────────────────────────────────────────────────────

def proficiency_bonus(level_or_val: int | None) -> int:
    """D&D 5E proficiency bonus: 2 + floor((level-1)/4)."""
    lv = max(1, level_or_val or 1)
    return 2 + (lv - 1) // 4


def _apply_numeric_effects(roll_effects: list[dict], target_prefix: str,
                           base: int) -> int:
    """Sum numeric modifiers from effects matching the target prefix."""
    result = base
    for re in roll_effects:
        t = str(re.get("target", ""))
        op = str(re.get("operation", "add"))
        if t.startswith(target_prefix) or t == target_prefix:
            val = int(re.get("value", 0))
            if op == "add":
                result += val
            elif op == "subtract":
                result -= val
            elif op == "set":
                result = val
    return result


def hot_character_for_llm(db: Session, character_id: str | None,
                          combat: bool = False) -> dict | None:
    """Convenience: return compact JSON for LLM system prompt, or None."""
    if not character_id:
        return None
    hot = get_hot_character(db, character_id, combat=combat)
    return hot.to_compact_json() if hot else None


def record_character_change(
    db: Session,
    character: Character,
    change_type: str,
    before_data: dict | None = None,
    after_data: dict | None = None,
    reason: str = "",
    rule_refs: list | None = None,
) -> None:
    """Record a character attribute change for audit trail."""
    from app.db.models import CharacterChange
    from app.services import uid
    db.add(CharacterChange(
        id=uid("chg"),
        campaign_id=character.campaign_id,
        character_id=character.id,
        change_type=change_type,
        before_data=before_data or {},
        after_data=after_data or {},
        reason=reason,
        rule_refs=rule_refs or [],
    ))
    db.commit()


# ── Checked Roll ─────────────────────────────────────────────────

def checked_roll(
    db: Session,
    formula: str,
    campaign_id: str,
    character_id: str | None = None,
    context: str = "",
    tool_name: str = "",
) -> dict:
    """Every dice roll MUST go through this function.

    Uses ``random.randint()`` for true randomness.
    Writes a ``DiceAuditLog`` row for full traceability.
    """
    from app.db.models import DiceAuditLog
    from app.services import uid
    from app.tools.dice import roll_dice

    result = roll_dice(formula)
    db.add(DiceAuditLog(
        id=uid("roll"),
        campaign_id=campaign_id,
        character_id=character_id,
        roll_formula=formula,
        roll_result=result["total"],
        roll_detail=result,
        context=context,
        tool_name=tool_name,
    ))
    db.commit()
    return result
