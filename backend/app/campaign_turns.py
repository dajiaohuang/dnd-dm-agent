from __future__ import annotations

import copy

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Campaign, Character, NapCatCharacterBinding
from app.qq_bindings import primary_controller_binding, user_controls_character
from app.tools.dice import roll_dice, roll_with_advantage
from app.actor_manager import actor_type, is_present
from app.tools.effect_engine import advance_effect_durations, resolve_effective_character


def runtime_mode(campaign: Campaign) -> str:
    return str((campaign.config or {}).get("runtime_mode") or "free")


def turn_state(campaign: Campaign) -> dict:
    return copy.deepcopy((campaign.config or {}).get("turn_state") or {})


def is_combat(campaign: Campaign) -> bool:
    return bool(turn_state(campaign).get("combat"))


def initiative_modifier(character: Character, combat_active: bool = True) -> int:
    effective = resolve_effective_character(character.data, combat_active).get("effective") or {}
    combat = effective.get("combat") or {}
    if combat.get("initiative") is not None:
        return int(combat["initiative"])
    dexterity = int(character.data.get("abilities", {}).get("dex", 10))
    return (dexterity - 10) // 2


def _participant(character: Character, initiative: dict | None = None) -> dict:
    return {
        "character_id": character.id,
        "name": character.character_name,
        "actor_type": actor_type(character),
        "initiative": initiative,
        "reaction_available": True,
    }


def _save(db: Session, campaign: Campaign, mode: str, state: dict) -> dict:
    config = copy.deepcopy(campaign.config or {})
    config["runtime_mode"] = mode
    config["turn_state"] = state
    campaign.config = config
    db.commit()
    return state


def _characters(db: Session, campaign_id: str) -> list[Character]:
    return [
        item for item in db.scalars(select(Character).where(Character.campaign_id == campaign_id)).all()
        if is_present(item)
    ]


def advance_character_effects(db: Session, campaign: Campaign, trigger: str) -> list[dict]:
    changes = []
    from app.services import update_character
    for character in _characters(db, campaign.id):
        next_data, expired = advance_effect_durations(character.data, trigger)
        if not expired:
            continue
        update_character(
            db, character, {"active_effects": next_data["active_effects"]},
            f"effect duration trigger: {trigger}", "effects_expired",
            [item["definition_id"] for item in expired],
        )
        changes.extend({"character_id": character.id, "effect": item} for item in expired)
    return changes


def enter_turn_mode(db: Session, campaign: Campaign) -> dict:
    if runtime_mode(campaign) == "turn_based":
        return turn_state(campaign)
    state = {
        "combat": False,
        "round": 1,
        "turn_index": 0,
        "participants": [_participant(character) for character in _characters(db, campaign.id)],
    }
    return _save(db, campaign, "turn_based", state)


def exit_turn_mode(db: Session, campaign: Campaign) -> bool:
    if is_combat(campaign):
        return False
    _save(db, campaign, "free", {})
    return True


def start_combat(
    db: Session,
    campaign: Campaign,
    participant_ids: list[str] | None = None,
    initiative_modes: dict[str, str] | None = None,
) -> dict:
    participants = []
    for character in _characters(db, campaign.id):
        if participant_ids is not None and character.id not in participant_ids:
            continue
        modifier = initiative_modifier(character, True)
        mode = (initiative_modes or {}).get(character.id, "normal")
        if mode in {"advantage", "disadvantage"}:
            initiative = roll_with_advantage(modifier, disadvantage=mode == "disadvantage")
        else:
            initiative = roll_dice(f"1d20{modifier:+d}")
        participant = _participant(character, initiative)
        participant["initiative_mode"] = mode
        participants.append(participant)
    if not participants:
        return {"combat": False, "round": 1, "turn_index": 0, "participants": []}
    participants.sort(
        key=lambda item: (item["initiative"]["total"], item["initiative"]["modifier"], item["name"]),
        reverse=True,
    )
    return _save(db, campaign, "turn_based", {
        "combat": True, "round": 1, "turn_index": 0, "participants": participants,
    })


def end_combat(db: Session, campaign: Campaign) -> None:
    advance_character_effects(db, campaign, "combat_end")
    config = copy.deepcopy(campaign.config or {})
    config.pop("reaction_window", None)
    campaign.config = config
    db.commit()
    _save(db, campaign, "free", {})


def current_turn(campaign: Campaign) -> dict | None:
    state = turn_state(campaign)
    participants = state.get("participants") or []
    if not participants:
        return None
    return participants[int(state.get("turn_index", 0)) % len(participants)]


def advance_turn(db: Session, campaign: Campaign) -> dict | None:
    state = turn_state(campaign)
    participants = state.get("participants") or []
    if not participants:
        return None
    next_index = int(state.get("turn_index", 0)) + 1
    advance_character_effects(db, campaign, "turn_end")
    if next_index >= len(participants):
        next_index = 0
        state["round"] = int(state.get("round", 1)) + 1
        advance_character_effects(db, campaign, "round_end")
    state["turn_index"] = next_index
    if participants:
        participants[next_index]["reaction_available"] = True
    _save(db, campaign, "turn_based", state)
    advance_character_effects(db, campaign, "turn_start")
    init_turn_actions(db, campaign, participants[next_index].get("character_id", ""))
    return participants[next_index]


def turn_access(
    db: Session,
    campaign: Campaign,
    character_id: str | None,
    is_dm: bool,
    controller_user_id: str | None = None,
) -> tuple[bool, str]:
    if runtime_mode(campaign) != "turn_based":
        return True, ""
    current = current_turn(campaign)
    if not current:
        return False, "当前回合制没有参与角色。"
    if current["actor_type"] in {"npc", "monster"}:
        if is_dm or user_controls_character(db, campaign.id, current["character_id"], controller_user_id):
            return True, ""
        return False, f"当前是 NPC“{current['name']}”的回合，由 DM 操作。"
    if character_id == current["character_id"]:
        return True, ""
    return False, f"当前是玩家角色“{current['name']}”的回合。"


def turn_notification(db: Session, campaign: Campaign) -> dict | None:
    current = current_turn(campaign)
    if not current:
        return None
    notification = {
        "character_id": current["character_id"],
        "name": current["name"],
        "actor_type": current["actor_type"],
        "round": turn_state(campaign).get("round", 1),
    }
    binding = primary_controller_binding(db, campaign.id, current["character_id"])
    notification["qq_user_id"] = binding.qq_user_id if binding else None
    return notification


def format_turn_state(campaign: Campaign) -> str:
    mode = runtime_mode(campaign)
    if mode == "campaign_edit":
        return "当前模式：战役编辑模式"
    if mode == "free":
        return "当前模式：自由扮演模式"
    state = turn_state(campaign)
    current = current_turn(campaign)
    current_text = f"{current['name']}（{current['actor_type']}）" if current else "无"
    combat_text = "战斗中" if state.get("combat") else "非战斗"
    return f"当前模式：回合制模式（{combat_text}）\n轮次：{state.get('round', 1)}\n当前行动者：{current_text}"


# ═══════════════════════════════════════════════════════════════════
#  Turn Action Quota Tracking
# ═══════════════════════════════════════════════════════════════════

DEFAULT_ACTIONS = {
    "main_action": 1,
    "bonus_action": 1,
    "reaction": 1,
    "movement": 30,
    "extra_actions": 0,
}


def get_actions_remaining(campaign: Campaign) -> dict:
    """Return current turn's action quota. Only valid in turn_based mode."""
    if runtime_mode(campaign) != "turn_based":
        return dict(DEFAULT_ACTIONS)  # Always return fresh defaults in free-form
    ts = (campaign.config or {}).get("turn_state") or {}
    current = ts.get("current_turn") or {}
    return {**DEFAULT_ACTIONS, **current.get("actions_remaining", {})}


def set_actions_remaining(db: Session, campaign: Campaign, actions: dict) -> None:
    """Update current turn's action quota. No-op in free-form mode."""
    if runtime_mode(campaign) != "turn_based":
        return  # Don't pollute config in free-form
    config = copy.deepcopy(campaign.config or {})
    ts = config.setdefault("turn_state", {})
    current = ts.setdefault("current_turn", {})
    current["actions_remaining"] = actions
    campaign.config = config
    db.commit()


def init_turn_actions(db: Session, campaign: Campaign, character_id: str = "") -> dict:
    """Reset action quota at the start of a turn."""
    actions = dict(DEFAULT_ACTIONS)
    set_actions_remaining(db, campaign, actions)
    return actions


def consume_action(db: Session, campaign: Campaign, action_type: str, amount: int = 1) -> dict | None:
    """Consume one unit of an action type. Returns remaining quota or None if not enough.
    In free-form mode, always returns fresh defaults (no real tracking needed)."""
    if runtime_mode(campaign) != "turn_based":
        return dict(DEFAULT_ACTIONS)  # Free-form: unlimited actions, human DM manages turns
    actions = get_actions_remaining(campaign)
    if actions.get(action_type, 0) < amount:
        return None
    actions[action_type] -= amount
    set_actions_remaining(db, campaign, actions)
    return actions


def format_actions_remaining(campaign: Campaign) -> str:
    """Human-readable remaining actions summary."""
    a = get_actions_remaining(campaign)
    parts = []
    if a.get("main_action", 0) > 0:
        parts.append(f"主动作×{a['main_action']}")
    if a.get("extra_actions", 0) > 0:
        parts.append(f"额外动作×{a['extra_actions']}")
    if a.get("bonus_action", 0) > 0:
        parts.append(f"附赠动作×{a['bonus_action']}")
    parts.append(f"移动{a.get('movement', 0)}尺")
    return " | ".join(parts) if parts else "无可用动作"
