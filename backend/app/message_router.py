from __future__ import annotations

from sqlalchemy.orm import Session

from app.campaign_control import campaign_status, command_result, execute_command
from app.commands import route_command
from app.config import settings
from app.db.models import Campaign, Character
from app.services import resolve_chat
from app.tools.spell_catalog import direct_spell_lookup, format_spell
from app.campaign_memory import build_memory_package
from app.campaign_turns import (
    advance_turn, current_turn, format_turn_state, runtime_mode, turn_access, turn_notification,
)
from app.campaign_editor import editor_chat
from app.dice_assistant import resolve_dice_assistant


def process_message(
    db: Session,
    campaign: Campaign,
    session_id: str | None,
    character_id: str | None,
    message: str,
    actor_id: str | None = None,
    is_dm: bool = False,
) -> dict:
    compact = " ".join(message.strip().split())
    lowered = compact.casefold()
    if lowered.startswith(("/记忆", "/memory")):
        if (campaign.config or {}).get("play_style") == "dice_assistant":
            return command_result("memory", "骰娘模式不读取或管理战役记忆。", ok=False)
        query = compact.split(maxsplit=1)[1] if len(compact.split(maxsplit=1)) > 1 else compact
        package = build_memory_package(db, campaign.id, query, session_id)
        lines = [f"- [{item['type']}] {item['content']}" for item in package["memories"]]
        return command_result("memory", "\n".join(lines) or "当前还没有可用的结构化战役记忆。", data=package)
    if lowered in {"/剧情线", "/threads"}:
        if (campaign.config or {}).get("play_style") == "dice_assistant":
            return command_result("threads", "骰娘模式不读取或管理战役剧情线。", ok=False)
        package = build_memory_package(db, campaign.id, compact, session_id)
        lines = [f"- {item['title']}: {item['description']}" for item in package["threads"]]
        return command_result("threads", "\n".join(lines) or "当前没有开放的剧情线。", data=package)
    command = route_command(message)
    if command:
        return execute_command(db, command, campaign, session_id, actor_id, is_dm)
    if runtime_mode(campaign) == "campaign_edit":
        if not is_dm:
            return command_result("campaign_edit", "战役编辑模式仅允许 DM 操作。", ok=False)
        result = editor_chat(db, campaign, session_id, message, actor_id)
        return {
            "ok": True, "kind": "campaign_editor", "command": "campaign_editor",
            "narration": result.pop("narration"), "data": result, "rolls": [], "state_changes": [], "events": [],
        }
    spell_lookup = direct_spell_lookup(message, settings.data_dir, 5)
    if spell_lookup:
        spell_query, spells = spell_lookup
        if not spells:
            return command_result("spell_search", f"没有找到与“{spell_query}”匹配的法术。", ok=False)
        return {
            **command_result("spell_search", "\n\n".join(format_spell(spell) for spell in spells)),
            "data": {"query": spell_query, "spells": spells},
        }
    if (campaign.config or {}).get("play_style") == "dice_assistant":
        character = db.get(Character, character_id) if character_id else None
        if runtime_mode(campaign) == "turn_based":
            allowed, reason = turn_access(campaign, character_id, is_dm)
            if not allowed:
                return command_result("not_your_turn", reason, ok=False, data={"turn_state": format_turn_state(campaign)})
            active_actor = current_turn(campaign)
            if active_actor and active_actor["actor_type"] in {"npc", "monster"} and is_dm:
                character = db.get(Character, active_actor["character_id"])
            result = resolve_dice_assistant(db, campaign, character, message)
            advance_turn(db, campaign)
            result["turn_notification"] = turn_notification(db, campaign)
            result["narration"] += f"\n\n{format_turn_state(campaign)}"
            return result
        return resolve_dice_assistant(db, campaign, character, message)
    if campaign_status(campaign) == "paused":
        return command_result(
            "paused",
            "战役当前处于暂停状态。DM 可发送 /继续 恢复战役；其他命令可发送 /帮助 查看。",
            ok=False,
        )
    allowed, reason = turn_access(campaign, character_id, is_dm)
    if not allowed:
        return command_result("not_your_turn", reason, ok=False, data={"turn_state": format_turn_state(campaign)})
    action_character_id = character_id
    active_actor = current_turn(campaign)
    if active_actor and active_actor["actor_type"] in {"npc", "monster"} and is_dm:
        action_character_id = active_actor["character_id"]
    result = resolve_chat(db, campaign.id, session_id, action_character_id, message)
    if runtime_mode(campaign) == "turn_based":
        advance_turn(db, campaign)
        notification = turn_notification(db, campaign)
        result["turn_notification"] = notification
        result["narration"] += f"\n\n{format_turn_state(campaign)}"
    return result
