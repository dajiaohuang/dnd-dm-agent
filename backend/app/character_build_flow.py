from __future__ import annotations

import copy
import re
from typing import Any

from sqlalchemy.orm import Session

from app.campaign_control import command_result
from app.db.models import Campaign, Character, TaskSession
from app.qq_bindings import bind_qq
from app.services import serialize, uid
from app.subagent_runner import enqueue_subagent_task
from app.task_sessions import (
    active_task, bump_draft_version, create_subagent_proposal, create_task,
    owner_mentions, session_payload, task_scope,
)
from app.tools.character_builder import build_character_data
from app.tools.character_rules import ABILITY_KEYS


REQUIRED_FIELDS = ("character_name", "class_name")
ABILITY_ALIASES = {
    "力量": "str", "str": "str", "strength": "str",
    "敏捷": "dex", "dex": "dex", "dexterity": "dex",
    "体质": "con", "體質": "con", "con": "con", "constitution": "con",
    "智力": "int", "int": "int", "intelligence": "int",
    "感知": "wis", "wis": "wis", "wisdom": "wis",
    "魅力": "cha", "cha": "cha", "charisma": "cha",
}
FIELD_ALIASES = {
    "姓名": "character_name", "名字": "character_name", "角色名": "character_name",
    "名称": "character_name", "name": "character_name", "character": "character_name",
    "职业": "class_name", "class": "class_name",
    "种族": "ancestry", "race": "ancestry", "ancestry": "ancestry",
    "背景": "background", "background": "background",
    "等级": "level", "level": "level",
    "hp": "max_hp", "生命": "max_hp", "生命值": "max_hp",
    "ac": "armor_class", "护甲": "armor_class", "护甲等级": "armor_class",
}


TASK_TYPE = "character_build"


def _owner(message_context: dict | None, actor_id: str | None, session_id: str | None) -> tuple[str, str | None, str, str | None]:
    return task_scope(message_context, actor_id, session_id)


def _active_session(
    db: Session,
    campaign: Campaign,
    platform: str,
    user_id: str,
    session_id: str | None,
) -> TaskSession | None:
    return active_task(db, campaign, TASK_TYPE, platform, user_id, session_id)


def has_active_character_build(
    db: Session,
    campaign: Campaign,
    session_id: str | None,
    actor_id: str | None,
    message_context: dict | None,
) -> bool:
    platform, _chat_id, user_id, scoped_session = _owner(message_context, actor_id, session_id)
    return _active_session(db, campaign, platform, user_id, scoped_session) is not None


def _missing(draft: dict[str, Any]) -> list[str]:
    return [field for field in REQUIRED_FIELDS if not draft.get(field)]


def _format_draft(session: TaskSession) -> str:
    draft = session.draft_data or {}
    abilities = draft.get("abilities") or {}
    ability_line = "，".join(f"{key.upper()} {abilities.get(key, 10)}" for key in ABILITY_KEYS)
    lines = [
        f"角色名：{draft.get('character_name') or '未填写'}",
        f"职业：{draft.get('class_name') or '未填写'}",
        f"种族：{draft.get('ancestry') or '未填写'}",
        f"背景：{draft.get('background') or '未填写'}",
        f"等级：{draft.get('level', 1)}",
        f"属性：{ability_line}",
    ]
    if session.missing_fields:
        lines.append("缺少字段：" + "、".join(session.missing_fields))
    return "\n".join(lines)


def _parse_fields(text: str) -> dict[str, Any]:
    result: dict[str, Any] = {}
    abilities: dict[str, int] = {}
    normalized = text.replace("，", ",").replace("；", ";")
    field_labels = sorted(FIELD_ALIASES, key=len, reverse=True)
    label_pattern = "|".join(re.escape(label) for label in field_labels)
    normalized = re.sub(rf"\s+({label_pattern})\s*([:：=])", r";\1\2", normalized, flags=re.IGNORECASE)
    for key, value in re.findall(r"([\w\u4e00-\u9fff]+)\s*[:：=]\s*([^,;，；\n]+)", normalized):
        field = FIELD_ALIASES.get(key.casefold(), FIELD_ALIASES.get(key))
        raw = value.strip()
        ability = ABILITY_ALIASES.get(key.casefold(), ABILITY_ALIASES.get(key))
        if ability:
            abilities[ability] = int(re.search(r"\d+", raw).group(0))
        elif field in {"level", "max_hp", "armor_class"}:
            match = re.search(r"\d+", raw)
            if match:
                result[field] = int(match.group(0))
        elif field:
            result[field] = raw
    for label, ability in ABILITY_ALIASES.items():
        match = re.search(rf"{re.escape(label)}\s*[=：: ]?\s*(\d{{1,2}})", text, re.IGNORECASE)
        if match:
            abilities[ability] = int(match.group(1))
    if abilities:
        result["abilities"] = abilities
    return result


def _merge_draft(draft: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    merged = copy.deepcopy(draft)
    if "abilities" in patch:
        abilities = {key: int((merged.get("abilities") or {}).get(key, 10)) for key in ABILITY_KEYS}
        abilities.update({key: int(value) for key, value in patch.pop("abilities").items()})
        merged["abilities"] = abilities
    merged.update(patch)
    return merged


def start_character_build(
    db: Session,
    campaign: Campaign,
    session_id: str | None,
    actor_id: str | None,
    message_context: dict | None,
    initial_text: str = "",
) -> dict:
    platform, chat_id, user_id, scoped_session = _owner(message_context, actor_id, session_id)
    existing = _active_session(db, campaign, platform, user_id, scoped_session)
    if existing:
        return command_result(
            "character_build",
            "你已经有一个进行中的车卡草稿。\n" + _format_draft(existing),
            data={
                "character_build_session": session_payload(existing),
                "task_session": serialize(existing),
                "mentions": owner_mentions(user_id, "你有一个进行中的车卡草稿。"),
            },
        )
    draft = {
        "campaign_id": campaign.id,
        "player_name": user_id,
        "character_name": "",
        "class_name": "",
        "level": 1,
        "abilities": {key: 10 for key in ABILITY_KEYS},
        "_meta": {"version": 1},
    }
    patch = _parse_fields(initial_text)
    draft = _merge_draft(draft, patch)
    missing = _missing(draft)
    item = create_task(
        db,
        campaign,
        TASK_TYPE,
        platform,
        chat_id,
        user_id,
        scoped_session,
        status="waiting_user",
        draft_data=draft,
        missing_fields=missing,
        next_prompt="请继续补充角色名、职业、种族、背景、等级、属性等车卡信息。",
        mentions=owner_mentions(user_id, "请继续补充你的车卡信息。"),
    )
    db.commit()
    return command_result(
        "character_build",
        "已开始车卡。每个玩家的车卡草稿会独立保存，不会互相覆盖。\n"
        "可以继续发送：名字: Luna 职业: Wizard 种族: Elf 力量8 敏捷14 体质12 智力16 感知10 魅力13。\n"
        + _format_draft(item),
        data={
            "character_build_session": session_payload(item),
            "task_session": serialize(item),
            "mentions": item.mentions,
        },
    )


def update_character_build(
    db: Session,
    campaign: Campaign,
    session_id: str | None,
    actor_id: str | None,
    message: str,
    message_context: dict | None,
) -> dict | None:
    platform, _chat_id, user_id, scoped_session = _owner(message_context, actor_id, session_id)
    item = _active_session(db, campaign, platform, user_id, scoped_session)
    if not item:
        return None
    patch = _parse_fields(message)
    if not patch:
        return command_result(
            "character_build",
            "我正在为你保留车卡草稿。请用“字段: 值”补充，例如：名字: Luna 职业: Wizard。",
            data={
                "character_build_session": session_payload(item),
                "task_session": serialize(item),
                "mentions": owner_mentions(user_id, "请用“字段: 值”补充你的车卡信息。"),
            },
        )
    item.draft_data = _merge_draft(item.draft_data or {}, patch)
    bump_draft_version(item)
    item.missing_fields = _missing(item.draft_data)
    item.status = "ready_to_commit" if not item.missing_fields else "waiting_user"
    item.mentions = owner_mentions(user_id, "你的车卡草稿已更新。")
    db.commit()
    return command_result(
        "character_build",
        "已更新你的车卡草稿。\n" + _format_draft(item),
        data={
            "character_build_session": session_payload(item),
            "task_session": serialize(item),
            "mentions": item.mentions,
        },
    )


def show_character_build(
    db: Session,
    campaign: Campaign,
    session_id: str | None,
    actor_id: str | None,
    message_context: dict | None,
) -> dict:
    platform, _chat_id, user_id, scoped_session = _owner(message_context, actor_id, session_id)
    item = _active_session(db, campaign, platform, user_id, scoped_session)
    if not item:
        return command_result("character_build", "你当前没有进行中的车卡草稿。", ok=False)
    return command_result(
        "character_build",
        _format_draft(item),
        data={
            "character_build_session": session_payload(item),
            "task_session": serialize(item),
            "mentions": owner_mentions(user_id, "这是你当前的车卡草稿。"),
        },
    )


def cancel_character_build(
    db: Session,
    campaign: Campaign,
    session_id: str | None,
    actor_id: str | None,
    message_context: dict | None,
) -> dict:
    platform, _chat_id, user_id, scoped_session = _owner(message_context, actor_id, session_id)
    item = _active_session(db, campaign, platform, user_id, scoped_session)
    if not item:
        return command_result("character_build", "你当前没有进行中的车卡草稿。", ok=False)
    item.status = "cancelled"
    db.commit()
    return command_result("character_build", "已取消你的车卡草稿。")


def submit_character_build(
    db: Session,
    campaign: Campaign,
    session_id: str | None,
    actor_id: str | None,
    message_context: dict | None,
) -> dict:
    platform, _chat_id, user_id, scoped_session = _owner(message_context, actor_id, session_id)
    item = _active_session(db, campaign, platform, user_id, scoped_session)
    if not item:
        return command_result("character_build", "你当前没有进行中的车卡草稿。", ok=False)
    missing = _missing(item.draft_data or {})
    if missing:
        item.missing_fields = missing
        db.commit()
        return command_result(
            "character_build",
            "车卡还不能提交，缺少字段：" + "、".join(missing),
            ok=False,
            data={
                "character_build_session": session_payload(item),
                "task_session": serialize(item),
                "mentions": owner_mentions(user_id, "你的车卡还缺少必填字段。"),
            },
        )
    raw = copy.deepcopy(item.draft_data or {})
    raw["campaign_id"] = campaign.id
    raw.setdefault("player_name", user_id)
    data = build_character_data(raw)
    character = Character(
        id=uid("char"),
        campaign_id=campaign.id,
        player_name=raw.get("player_name", user_id),
        character_name=raw["character_name"],
        data=data,
    )
    db.add(character)
    item.status = "committed"
    item.created_object_type = "character"
    item.created_object_id = character.id
    item.mentions = owner_mentions(user_id, f"车卡已创建：{character.character_name}。")
    subtask = create_subagent_proposal(
        db,
        campaign,
        item,
        agent_role="character_sheet_reviewer",
        goal="Review the submitted character sheet for mechanical completeness and structured data quality.",
        proposal={"character_id": character.id},
        next_prompt="角色卡后台审核完成后可查看结果。",
    )
    db.commit()
    enqueue_subagent_task(subtask.id)
    if platform == "napcat" and user_id.isdigit():
        bind_qq(db, campaign.id, user_id, character)
    return command_result(
        "character_build",
        f"车卡已创建：{character.character_name}（{character.id}）。",
        data={
            "character": serialize(character),
            "character_build_session": session_payload(item),
            "task_session": serialize(item),
            "mentions": item.mentions,
        },
    )
