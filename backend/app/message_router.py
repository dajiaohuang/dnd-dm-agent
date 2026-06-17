from __future__ import annotations

from sqlalchemy.orm import Session

from app.campaign_control import campaign_status, command_result, execute_command
from app.commands import route_command
from app.config import settings
from app.db.models import Campaign, Character
from sqlalchemy.orm import Session
from app.services import append_event, serialize
from app.campaign_memory import build_memory_package
from app.campaign_turns import (
    advance_turn, current_turn, format_turn_state, runtime_mode, turn_access, turn_notification,
    init_turn_actions, get_actions_remaining, format_actions_remaining,
)
from app.dice_assistant import clear_dice_pending_state, dice_context_action, resolve_dice_assistant
from app.actor_manager import list_actors, is_present
from app.tools.combat_tools import COMBAT_TOOLS
from app.tools.hot_character import hot_character_for_llm
from app.combat_reactions import handle_reaction_response, reaction_window
from app.qq_bindings import set_active_napcat_campaign, sync_campaign_actor_bindings
from app.task_sessions import format_ready_reviews, ready_reviews, task_scope
from app.llm_loop import execute_llm_with_tools


GLOBAL_SAFE_COMMANDS = {
    "help", "status", "save", "pause", "resume",
    "cancel_character_build", "exit_dice_assistant", "end_combat", "exit_turn_mode",
    "enter_campaign_edit", "exit_campaign_edit", "publish_settings", "discard_settings",
    "list_setting_drafts", "undo_setting_draft", "validate_settings",
    "create_campaign_from_prompt", "delete_active_campaign", "create_npc_cards_from_settings",
}


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
    if is_dm and compact.startswith(("切换到战役", "切换战役", "使用战役", "改用战役")):
        target_name = compact.replace("切换到战役", "", 1).replace("切换战役", "", 1).replace("使用战役", "", 1).replace("改用战役", "", 1).strip(" ：:")
        if target_name:
            matches = [
                item for item in db.query(Campaign).all()
                if item.name == target_name or item.id == target_name
            ]
            if not matches:
                matches = [
                    item for item in db.query(Campaign).all()
                    if target_name.casefold() in item.name.casefold() or target_name.casefold() in item.id.casefold()
                ]
            if len(matches) == 1:
                switched = set_active_napcat_campaign(db, matches[0])
                if (switched.config or {}).get("dice_dm_qq_user_id"):
                    sync_campaign_actor_bindings(db, switched)
                return command_result(
                    "switch_active_campaign",
                    f"已切换当前 NapCat 战役到“{switched.name}”。当前玩法：{'骰娘辅助' if (switched.config or {}).get('play_style') == 'dice_assistant' else '战役叙事'}。",
                    data={"campaign": serialize(switched)},
                )
            if len(matches) > 1:
                return command_result("switch_active_campaign", "匹配到多个战役，请说得更具体一点。", ok=False)
            return command_result("switch_active_campaign", f"没有找到名为“{target_name}”的战役。", ok=False)
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
    lobby_mode = (campaign.config or {}).get("play_style") == "lobby"
    command = route_command(message)
    bound_character = db.get(Character, character_id) if character_id else None
    scope_platform, _scope_chat, scope_owner, scope_session = task_scope(
        message_context or {"platform": "web"}, actor_id, session_id,
    )
    # ── Character build, task reviews, effect actions → handled by LLM tools ──
    reaction_interrupt_commands = GLOBAL_SAFE_COMMANDS | {"next_turn"}
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
    # ── Exact /slash commands → fast-path (backward compat) ──
    if command:
        if dice_mode and command.name != "start_combat":
            clear_dice_pending_state(db, campaign)
        return execute_command(db, command, campaign, session_id, actor_id, is_dm, message_context)

    # ── Lobby mode → game-external management (non-command messages) ──
    if lobby_mode:
        from app.services import resolve_chat as _lobby_chat
        return _lobby_chat(db, campaign.id, session_id, character_id, message, mode="lobby", message_context=message_context)

    # ── Dice mode: keep DM-confirmation fast path, rest handled by LLM tools ──
    if dice_mode:
        contextual = dice_context_action(
            db, campaign, message,
            {**(message_context or {}), "session_id": session_id, "actor_id": actor_id},
        )
        if contextual:
            return audit_dice_result(db, campaign, session_id, character_id, actor_id, message, contextual, message_context)

    # ── Campaign edit, spell lookup, effects, reviews → handled by LLM tools ──
    # ── Dice assistant mode ──
    if (campaign.config or {}).get("play_style") == "dice_assistant":
        character = bound_character
        if runtime_mode(campaign) == "turn_based":
            allowed, reason = turn_access(db, campaign, character_id, is_dm, actor_id)
            if not allowed:
                return command_result("not_your_turn", reason, ok=False,
                                      data={"turn_state": format_turn_state(campaign)})
            active_actor = current_turn(campaign)
            if active_actor and active_actor["actor_type"] in {"npc", "monster"} and is_dm:
                character = db.get(Character, active_actor["character_id"])
            # ── LLM agent with combat tools ──
            combat_system = _combat_system_prompt(campaign, character, db=db)
            result = execute_llm_with_tools(
                db, campaign, session_id, character_id, actor_id, is_dm, message,
                message_context, system_prompt=combat_system,
                extra_tools=COMBAT_TOOLS,
            )
            if result.get("kind") == "llm_unavailable":
                result = resolve_dice_assistant(db, campaign, character, message)
            if (result.get("data") or {}).get("turn_consuming", True):
                advance_turn(db, campaign)
                result["turn_notification"] = turn_notification(db, campaign)
            result["narration"] += f"\n\n{format_turn_state(campaign)}"
            return audit_dice_result(db, campaign, session_id, character.id if character else None,
                                     actor_id, message, result, message_context)
        return audit_dice_result(db, campaign, session_id, character_id, actor_id, message,
                                 resolve_dice_assistant(db, campaign, character, message), message_context)

    # ── Paused check ──
    if campaign_status(campaign) == "paused":
        return command_result(
            "paused",
            "战役当前处于暂停状态。DM 可发送 /继续 恢复战役；其他命令可发送 /帮助 查看。",
            ok=False,
        )

    # ── Turn access ──
    allowed, reason = turn_access(db, campaign, character_id, is_dm, actor_id)
    if not allowed:
        return command_result("not_your_turn", reason, ok=False,
                              data={"turn_state": format_turn_state(campaign)})

    action_character_id = character_id
    active_actor = current_turn(campaign)
    if active_actor and active_actor["actor_type"] in {"npc", "monster"} and is_dm:
        action_character_id = active_actor["character_id"]

    # ── Turn-based mode → LLM agent with combat tools + DM narrative ──
    if runtime_mode(campaign) == "turn_based":
        combat_character = db.get(Character, action_character_id) if action_character_id else None
        combat_system = _combat_system_prompt(campaign, combat_character, narrative=True)
        result = execute_llm_with_tools(
            db, campaign, session_id, action_character_id, actor_id, is_dm, message,
            message_context, system_prompt=combat_system,
            extra_tools=COMBAT_TOOLS,
        )
        if result.get("kind") == "llm_unavailable":
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

    # ── Default: DM narrative mode ──
    from app.services import resolve_chat
    result = resolve_chat(db, campaign.id, session_id, action_character_id, message, message_context=message_context)
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


def _combat_system_prompt(campaign: Campaign, character: Character | None, narrative: bool = False, db: Session | None = None) -> str:
    """Build the combat system prompt with D&D 5E rules context."""
    turn_state = (campaign.config or {}).get("turn_state") or {}
    initiative_order = turn_state.get("initiative_order") or []
    current_idx = turn_state.get("current_turn_index", 0)
    combat_round = turn_state.get("round", 1)

    lines = [
        "你是 D&D 5E 地下城主的战斗规则引擎。" if narrative else "你是 D&D 5E 骰娘，负责战斗结算。",
        f"当前第 {combat_round} 轮。",
    ]

    if initiative_order:
        lines.append("先攻顺序：")
        for i, entry in enumerate(initiative_order):
            marker = " ← 当前" if i == current_idx else ""
            lines.append(f"  {i+1}. {entry.get('name', '?')} (先攻 {entry.get('initiative', '?')}){marker}")

    if character:
        import json as _json
        hot = hot_character_for_llm(db, character.id) if db else None
        if hot is None and db and hasattr(character, 'data'):
            # Fallback: use raw data
            hot = {
                "character_name": character.character_name,
                "abilities": {k: {"score": v, "mod": (v//2-5)}
                    for k, v in (character.data.get("abilities", {}) or {}).items()},
                "armor_class": (character.data.get("combat", {}) or {}).get("armor_class", 10),
                "current_hp": (character.data.get("combat", {}) or {}).get("current_hp", 0),
                "max_hp": (character.data.get("combat", {}) or {}).get("max_hp", 1),
                "speed": (character.data.get("combat", {}) or {}).get("speed", 30),
                "saving_throws": {k: v for k, v in ((character.data.get("saving_throws") or {}).items())},
            }
        if hot:
            lines.append(f"\n[当前角色热数据]\n{_json.dumps(hot, ensure_ascii=False)}")

    # ── Action quota ──
    if db:
        actions = get_actions_remaining(campaign)
        lines.append(f"\n当前回合剩余动作配额: {format_actions_remaining(campaign)}")
    else:
        actions = {"main_action": 1, "bonus_action": 1, "reaction": 1, "movement": 30, "extra_actions": 0}

    lines.append("""
战斗规则:
- 每个行动调用对应工具，系统自动扣除动作配额
- 主动作: 攻击/施法/疾走/撤退/闪避 (main_action)
- 附赠动作: 双武器副手/灵巧施法 (bonus_action)
- 动作如潮: 使用 use_feature("action_surge") 获得额外动作
- 配额用完后提醒用户结束回合，或建议可用的附赠/移动
- 用户说「结束回合」时调用 end_turn 工具
- 用户会用自然语言描述行动，你需要调用对应工具
- 信息不足时调用 ask_clarification 追问，不要猜测
- 追问示例：用哪个法术？几环？目标是谁？用动作还是附赠动作？
- 攻击检定=d20+熟练+属性调整，伤害=武器骰+属性调整
- 法术DC=8+熟练+施法属性调整
- 只结算当前行动者请求的动作，不要替其他角色行动""")
    return "\n".join(lines)
