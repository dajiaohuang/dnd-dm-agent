"""Lobby mode tools — simple state management for the game-external mode.

The LLM fully controls the lobby flow. These tools give it direct
read/write access to ``campaign.config.lobby_state``.
"""

from __future__ import annotations

import copy
import json
from typing import Any

from sqlalchemy.orm import Session

from app.db.models import Campaign


def _ok(narration: str, **kw: Any) -> dict:
    return {"ok": True, "kind": "lobby", "narration": narration, "data": kw or {}}


def _err(narration: str) -> dict:
    return {"ok": False, "kind": "lobby", "narration": narration}


def _get_state(campaign: Campaign | None) -> dict:
    if campaign is None:
        return {}
    return (campaign.config or {}).get("lobby_state") or {}


def _set_state(db: Session, campaign: Campaign, state: dict) -> None:
    cfg = copy.deepcopy(campaign.config or {})
    cfg["lobby_state"] = state
    campaign.config = cfg
    db.commit()


# ═══════════════════════════════════════════════════════════════════
#  LLM TOOL SCHEMAS
# ═══════════════════════════════════════════════════════════════════

LOBBY_TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "get_lobby_state",
            "description": "读取大厅当前状态(DM确认/待选选项/生成的设定等)",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_lobby_state",
            "description": "更新大厅状态。LLM自由控制流程: 设置待选选项/保存生成的设定/标记DM已确认等。",
            "parameters": {
                "type": "object",
                "properties": {
                    "state": {
                        "type": "object",
                        "description": (
                            "要合并到大厅状态的键值对。支持的键:\n"
                            "- dm_confirmed: true/false\n"
                            "- pending_options: [{id,action,label}]\n"
                            "- generated_setting: {name,description}\n"
                            "- confirmed_campaign: 战役名(已确认要创建)\n"
                        ),
                    },
                },
                "required": ["state"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "resolve_lobby_option",
            "description": "执行用户选择的待选选项(用户回复数字时调用)",
            "parameters": {
                "type": "object",
                "properties": {
                    "option_number": {"type": "integer", "description": "选项编号(1开始)"},
                },
                "required": ["option_number"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_campaign_now",
            "description": "确认创建战役(用户同意后调用)。从lobby_state.generated_setting读取名称和设定。",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
]


# ═══════════════════════════════════════════════════════════════════
#  HANDLERS
# ═══════════════════════════════════════════════════════════════════

def handle_get_lobby_state(
    db: Session, campaign: Campaign, **_kw: Any,
) -> dict:
    state = _get_state(campaign)
    return _ok(json.dumps(state, ensure_ascii=False, indent=2), state=state)


def handle_set_lobby_state(
    db: Session, campaign: Campaign, state: dict | None = None, **_kw: Any,
) -> dict:
    if not state:
        return _err("state is required")
    current = _get_state(campaign)
    current.update(state)
    _set_state(db, campaign, current)
    return _ok(f"大厅状态已更新。{len(current)} 个键。", state=current)


def handle_resolve_lobby_option(
    db: Session, campaign: Campaign,
    option_number: int = 0, **_kw: Any,
) -> dict:
    state = _get_state(campaign)
    options = state.get("pending_options") or []
    if option_number < 1 or option_number > len(options):
        return _err(f"选项不存在。可选 1-{len(options)}。")
    opt = options[option_number - 1]
    action = opt.get("action", "")

    if action == "create_campaign":
        gs = state.get("generated_setting") or {}
        name = gs.get("name", "新战役")
        desc = gs.get("description", "")
        # Create the campaign
        from app.campaign_control import execute_command
        from app.commands import Command
        # Set DM confirmed
        state["dm_confirmed"] = True
        # Inject name/desc for the handler
        cfg = copy.deepcopy(campaign.config or {})
        cfg["pending_generated_campaign_name"] = name
        cfg["pending_generated_campaign_description"] = desc
        cfg["lobby_state"] = state
        campaign.config = cfg; db.commit()
        return execute_command(
            db, Command("create_campaign_from_prompt"), campaign,
            None, None, True, None,
        )

    elif action == "regenerate":
        state.pop("generated_setting", None)
        state.pop("pending_options", None)
        _set_state(db, campaign, state)
        return _ok("已清除。请描述你想要的新战役。")

    elif action == "enter_dm":
        cfg = copy.deepcopy(campaign.config or {})
        cfg["play_style"] = "campaign"
        cfg["lobby_state"] = state
        campaign.config = cfg; db.commit()
        return _ok(f"已进入 DM 模式。当前战役: {campaign.name}。")

    elif action == "enter_dice":
        cfg = copy.deepcopy(campaign.config or {})
        cfg["play_style"] = "dice_assistant"
        cfg["lobby_state"] = state
        campaign.config = cfg; db.commit()
        return _ok(f"已进入骰娘模式。当前战役: {campaign.name}。")

    return _err(f"未知操作: {action}")


def handle_create_campaign_now(
    db: Session, campaign: Campaign, **_kw: Any,
) -> dict:
    state = _get_state(campaign)
    gs = state.get("generated_setting") or {}
    name = gs.get("name", "新战役")
    desc = gs.get("description", "")
    state["dm_confirmed"] = True
    cfg = copy.deepcopy(campaign.config or {})
    cfg["pending_generated_campaign_name"] = name
    cfg["pending_generated_campaign_description"] = desc
    cfg["lobby_state"] = state
    campaign.config = cfg; db.commit()
    from app.campaign_control import execute_command
    from app.commands import Command
    return execute_command(
        db, Command("create_campaign_from_prompt"), campaign,
        None, None, True, None,
    )


LOBBY_HANDLERS = {
    "get_lobby_state": handle_get_lobby_state,
    "set_lobby_state": handle_set_lobby_state,
    "resolve_lobby_option": handle_resolve_lobby_option,
    "create_campaign_now": handle_create_campaign_now,
}
