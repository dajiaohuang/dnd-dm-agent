from __future__ import annotations

import copy
import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Campaign, Character, NapCatCharacterBinding
from app.qq_bindings import character_is_hosted, primary_controller_binding
from app.tools.dice import roll_dice


REACTION_TERMS = ("反应", "反應", "reaction", "护盾术", "護盾術", "shield", "反击", "反擊", "借机攻击", "機會攻擊")
DECLINE_TERMS = ("不反应", "不反應", "不用反应", "不用反應", "放弃反应", "放棄反應", "不使用", "继续", "繼續", "no reaction", "pass")


def reaction_window(campaign: Campaign) -> dict:
    return copy.deepcopy((campaign.config or {}).get("reaction_window") or {})


def _save(db: Session, campaign: Campaign, value: dict | None) -> None:
    config = copy.deepcopy(campaign.config or {})
    if value:
        config["reaction_window"] = value
    else:
        config.pop("reaction_window", None)
    campaign.config = config
    db.commit()


def _reaction_options(character: Character) -> list[str]:
    options = []
    for section in ("features", "spells", "inventory"):
        for item in character.data.get(section) or []:
            text = str(item if isinstance(item, str) else {
                key: item.get(key) for key in ("name", "description", "activation", "trigger", "effects") if item.get(key)
            }).casefold()
            if any(term.casefold() in text for term in REACTION_TERMS):
                options.append(str(item if isinstance(item, str) else item.get("name") or item.get("item_id") or "未命名反应"))
    return list(dict.fromkeys(options))


def open_reaction_window(
    db: Session,
    campaign: Campaign,
    action_text: str,
    formula: str,
    acting_character_id: str | None,
) -> dict | None:
    participants = ((campaign.config or {}).get("turn_state") or {}).get("participants") or []
    participant_ids = [item.get("character_id") for item in participants if item.get("character_id")]
    characters = {
        item.id: item for item in db.scalars(select(Character).where(Character.id.in_(participant_ids))).all()
    } if participant_ids else {}
    reactors = []
    for participant in participants:
        character_id = participant.get("character_id")
        if character_id == acting_character_id or not participant.get("reaction_available", True):
            continue
        character = characters.get(character_id)
        if not character:
            continue
        options = _reaction_options(character)
        if not options:
            continue
        binding = primary_controller_binding(db, campaign.id, character_id)
        dice_mode = (campaign.config or {}).get("play_style") == "dice_assistant"
        hosted = character_is_hosted(db, campaign, character)
        automated = hosted if dice_mode else participant.get("actor_type") in {"npc", "monster"} or not binding
        reactors.append({
            "character_id": character_id,
            "name": participant.get("name"),
            "actor_type": participant.get("actor_type"),
            "qq_user_id": binding.qq_user_id if binding else None,
            "automated": automated,
            "options": options,
            "decision": None,
        })
    if not reactors:
        return None
    for reactor in reactors:
        if reactor["automated"]:
            shield = next((item for item in reactor["options"] if any(term in item.casefold() for term in ("护盾", "護盾", "shield"))), None)
            use = bool(shield and any(term in action_text.casefold() for term in ("攻击", "攻擊", "attack", "命中")))
            reactor["decision"] = {
                "use": use,
                "option": shield if use else None,
                "reason": f"自动控制角色决定使用{shield}。" if use else "自动控制角色评估后不使用反应。",
            }
    window = {
        "action_text": action_text,
        "formula": formula,
        "acting_character_id": acting_character_id,
        "reactors": reactors,
    }
    _save(db, campaign, window)
    return window


def format_reaction_prompt(window: dict) -> str:
    pending = [item for item in window.get("reactors") or [] if item.get("decision") is None]
    automated = [item for item in window.get("reactors") or [] if item.get("automated")]
    lines = [f"行动已声明，尚未投掷：{window.get('action_text')}"]
    if automated:
        lines.append("自动控制角色反应决策：" + "；".join(
            f"{item['name']}：{(item.get('decision') or {}).get('reason')}" for item in automated
        ))
    if pending:
        lines.append("以下玩家角色存在可用反应，请决定是否反应：")
        lines.extend(f"- {item['name']}：{'、'.join(item['options'])}" for item in pending)
    return "\n".join(lines)


def reaction_notifications(window: dict) -> list[dict]:
    return [
        {"qq_user_id": item["qq_user_id"], "name": item["name"], "options": item["options"]}
        for item in window.get("reactors") or []
        if item.get("decision") is None and item.get("qq_user_id")
    ]


def resolve_ready_reaction_window(db: Session, campaign: Campaign, window: dict) -> dict | None:
    if any(item.get("decision") is None for item in window.get("reactors") or []):
        return None
    roll = roll_dice(window["formula"])
    used = [item for item in window["reactors"] if (item.get("decision") or {}).get("use")]
    if used:
        config = copy.deepcopy(campaign.config or {})
        state = copy.deepcopy(config.get("turn_state") or {})
        used_ids = {item["character_id"] for item in used}
        for participant in state.get("participants") or []:
            if participant.get("character_id") in used_ids:
                participant["reaction_available"] = False
        config["turn_state"] = state
        campaign.config = config
        db.commit()
    _save(db, campaign, None)
    reaction_text = "；".join(
        f"{item['name']}使用{item['decision'].get('option')}" for item in used
    ) or "无人使用反应"
    return {
        "ok": True, "kind": "reaction_resolved", "command": "reaction_resolved",
        "narration": (
            f"{window['action_text']}\n反应决定：{reaction_text}。\n"
            f"现在执行投掷 {roll['formula']}：{roll['total']}（{roll['rolls']}，修正 {roll['modifier']:+d}）。"
        ),
        "data": {"turn_consuming": True, "reaction_decisions": window["reactors"]},
        "rolls": [roll], "state_changes": [], "events": [],
    }


def handle_reaction_response(
    db: Session,
    campaign: Campaign,
    character_id: str | None,
    message: str,
) -> dict | None:
    window = reaction_window(campaign)
    if not window:
        return None
    reactor = next(
        (item for item in window.get("reactors") or [] if item.get("character_id") == character_id and item.get("decision") is None),
        None,
    )
    if not reactor:
        return None
    lowered = message.casefold()
    decline = any(term in lowered for term in DECLINE_TERMS)
    selected = next((option for option in reactor["options"] if option.casefold() in lowered), None)
    if not decline and not selected and not any(term in lowered for term in REACTION_TERMS):
        return {
            "ok": False, "kind": "reaction_pending", "command": "reaction_pending",
            "narration": f"请明确回复是否使用反应。可用反应：{'、'.join(reactor['options'])}",
            "data": {"turn_consuming": False, "reaction_notifications": reaction_notifications(window)},
            "rolls": [], "state_changes": [], "events": [],
        }
    reactor["decision"] = {"use": not decline, "option": selected or ("未指定反应" if not decline else None)}
    pending = [item for item in window["reactors"] if item.get("decision") is None]
    if pending:
        _save(db, campaign, window)
        return {
            "ok": True, "kind": "reaction_pending", "command": "reaction_pending",
            "narration": format_reaction_prompt(window),
            "data": {"turn_consuming": False, "reaction_notifications": reaction_notifications(window)},
            "rolls": [], "state_changes": [], "events": [],
        }
    return resolve_ready_reaction_window(db, campaign, window)
