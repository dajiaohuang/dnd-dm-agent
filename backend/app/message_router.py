from __future__ import annotations

from sqlalchemy.orm import Session

from app.campaign_control import campaign_status, command_result, execute_command
from app.commands import route_command
from app.config import settings
from app.db.models import Campaign, Character
from app.services import append_event, resolve_chat, serialize
from app.tools.spell_catalog import direct_spell_lookup, format_spell
from app.campaign_memory import build_memory_package
from app.campaign_turns import (
    advance_turn, current_turn, format_turn_state, runtime_mode, turn_access, turn_notification,
)
from app.campaign_editor import editor_chat
from app.dice_assistant import clear_dice_pending_state, dice_context_action, resolve_dice_assistant
from app.actor_manager import list_actors, is_present
from app.effect_actions import resolve_effect_action
from app.combat_reactions import handle_reaction_response, reaction_window
from app.character_build_flow import (
    cancel_character_build, has_active_character_build, show_character_build, start_character_build,
    submit_character_build, update_character_build,
)
from app.task_sessions import active_task, create_subagent_proposal, task_scope


def process_message(
    db: Session,
    campaign: Campaign,
    session_id: str | None,
    character_id: str | None,
    message: str,
    actor_id: str | None = None,
    is_dm: bool = False,
    message_context: dict | None = None,
) -> dict:
    compact = " ".join(message.strip().split())
    lowered = compact.casefold()
    if lowered.startswith(("/记忆", "/memory")):
        query = compact.split(maxsplit=1)[1] if len(compact.split(maxsplit=1)) > 1 else compact
        package = build_memory_package(db, campaign.id, query, session_id)
        lines = [f"- [{item['type']}] {item['content']}" for item in package["memories"]]
        return command_result("memory", "\n".join(lines) or "当前还没有可用的结构化战役记忆。", data=package)
    if lowered in {"/剧情线", "/threads"}:
        package = build_memory_package(db, campaign.id, compact, session_id)
        lines = [f"- {item['title']}: {item['description']}" for item in package["threads"]]
        return command_result("threads", "\n".join(lines) or "当前没有开放的剧情线。", data=package)
    dice_mode = (campaign.config or {}).get("play_style") == "dice_assistant"
    command = route_command(message)
    bound_character = db.get(Character, character_id) if character_id else None
    if not command and compact.startswith(("车卡", "开始车卡", "我要车卡", "创建角色")):
        return start_character_build(db, campaign, session_id, actor_id, message_context, compact)
    if command and command.name == "start_character_build":
        return start_character_build(db, campaign, session_id, actor_id, message_context, compact)
    if command and command.name == "show_character_build":
        return show_character_build(db, campaign, session_id, actor_id, message_context)
    if command and command.name == "cancel_character_build":
        return cancel_character_build(db, campaign, session_id, actor_id, message_context)
    if command and command.name == "submit_character_build":
        return submit_character_build(db, campaign, session_id, actor_id, message_context)
    if not command and has_active_character_build(db, campaign, session_id, actor_id, message_context):
        build_result = update_character_build(db, campaign, session_id, actor_id, message, message_context)
        if build_result:
            return build_result
    reaction_interrupt_commands = {"end_combat", "exit_dice_assistant", "status", "help", "pause", "save"}
    if reaction_window(campaign) and (not command or command.name not in reaction_interrupt_commands):
        reaction_result = handle_reaction_response(db, campaign, character_id, message)
        if reaction_result:
            if (reaction_result.get("data") or {}).get("turn_consuming"):
                advance_turn(db, campaign)
                reaction_result["turn_notification"] = turn_notification(db, campaign)
            reaction_result["narration"] += f"\n\n{format_turn_state(campaign)}"
            return audit_dice_result(
                db, campaign, session_id, character_id, actor_id, message, reaction_result, message_context,
            )
    if command and not (dice_mode and command.name == "start_combat"):
        if dice_mode:
            clear_dice_pending_state(db, campaign)
        return execute_command(db, command, campaign, session_id, actor_id, is_dm, message_context)
    if dice_mode:
        contextual = dice_context_action(
            db, campaign, message,
            {**(message_context or {}), "session_id": session_id, "actor_id": actor_id},
        )
        if contextual:
            return audit_dice_result(db, campaign, session_id, character_id, actor_id, message, contextual, message_context)
    if command:
        return execute_command(db, command, campaign, session_id, actor_id, is_dm, message_context)
    effect_action = resolve_effect_action(db, campaign, bound_character, message, actor_id, session_id)
    if effect_action:
        return effect_action
    edit_platform, _edit_chat, edit_owner, edit_session = task_scope({"platform": "web"}, actor_id, session_id)
    if active_task(db, campaign, "campaign_edit", edit_platform, edit_owner, edit_session):
        if not is_dm:
            return command_result("campaign_edit", "战役编辑模式仅允许 DM 操作。", ok=False)
        parent_task = active_task(db, campaign, "campaign_edit", edit_platform, edit_owner, edit_session)
        result = editor_chat(db, campaign, session_id, message, actor_id)
        if parent_task and result.get("drafts"):
            create_subagent_proposal(
                db,
                campaign,
                parent_task,
                agent_role="campaign_setting_reviewer",
                goal="Review and enrich generated campaign setting drafts before publication.",
                proposal={"draft_ids": [item["id"] for item in result["drafts"]], "source_message": message},
                next_prompt="已生成设定草稿，可审核后发布。",
            )
            db.commit()
        return {
            "ok": True, "kind": "campaign_editor", "command": "campaign_editor",
            "narration": result.pop("narration"), "data": result, "rolls": [], "state_changes": [], "events": [],
        }
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
        character = bound_character
        if runtime_mode(campaign) == "turn_based":
            allowed, reason = turn_access(campaign, character_id, is_dm)
            if not allowed:
                return command_result("not_your_turn", reason, ok=False, data={"turn_state": format_turn_state(campaign)})
            active_actor = current_turn(campaign)
            if active_actor and active_actor["actor_type"] in {"npc", "monster"} and is_dm:
                character = db.get(Character, active_actor["character_id"])
            result = resolve_dice_assistant(db, campaign, character, message)
            if (result.get("data") or {}).get("turn_consuming", True):
                advance_turn(db, campaign)
                result["turn_notification"] = turn_notification(db, campaign)
            result["narration"] += f"\n\n{format_turn_state(campaign)}"
            return audit_dice_result(db, campaign, session_id, character.id if character else None,
                                     actor_id, message, result, message_context)
        return audit_dice_result(db, campaign, session_id, character_id, actor_id, message,
                                 resolve_dice_assistant(db, campaign, character, message), message_context)
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
    if runtime_mode(campaign) == "turn_based":
        combat_character = db.get(Character, action_character_id) if action_character_id else None
        result = resolve_dice_assistant(db, campaign, combat_character, message, narrative_mode=True)
        result["narration"] = result.get("narration", "").replace("骰娘：", "DM：")
        result.setdefault("data", {})["audit_type"] = "dm_combat_action"
        if (result.get("data") or {}).get("turn_consuming", True):
            advance_turn(db, campaign)
            result["turn_notification"] = turn_notification(db, campaign)
        result["narration"] += f"\n\n{format_turn_state(campaign)}"
        return audit_dice_result(
            db, campaign, session_id, action_character_id, actor_id, message, result, message_context,
        )
    result = resolve_chat(db, campaign.id, session_id, action_character_id, message)
    return result


def audit_dice_result(
    db: Session,
    campaign: Campaign,
    session_id: str | None,
    character_id: str | None,
    actor_id: str | None,
    message: str,
    result: dict,
    message_context: dict | None,
) -> dict:
    present = [item for item in list_actors(db, campaign.id) if is_present(item)]
    actors = [character_id] if character_id else []
    audit_content = str((result.get("data") or {}).get("audit_content") or message)
    event = append_event(
        db, campaign.id, session_id,
        str((result.get("data") or {}).get("audit_type") or "dice_assistant_action"),
        audit_content,
        actors,
        {
            "actor_id": actor_id,
            "raw_input": message,
            "dice_response": result.get("narration"),
            "rolls": result.get("rolls") or [],
            "state_changes": result.get("state_changes") or [],
            "message_context": message_context or {},
            "turn_state": (result.get("data") or {}).get("turn_state"),
            "combat_participant_cards": (result.get("data") or {}).get("combat_participant_cards"),
            "present_actors": [
                {
                    "character_id": item.id,
                    "name": item.character_name,
                    "actor_type": (item.data.get("basic") or {}).get("actor_type", "player"),
                    "hp": (item.data.get("combat") or {}).get("current_hp"),
                    "conditions": item.data.get("conditions") or [],
                }
                for item in present
            ],
        },
        memory_plan={"extract_after_event": True, "intent_type": "dice_assistant", "skip": False},
    )
    result["events"] = [serialize(event)]
    result.setdefault("data", {})["present_actors"] = [
        {"character_id": item.id, "name": item.character_name} for item in present
    ]
    return result
