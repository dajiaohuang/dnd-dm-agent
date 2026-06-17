"""LLM-with-tools execution loop.

Calls the LLM with function-calling tools, executes any returned tool_calls,
feeds results back, and returns the final narration.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy.orm import Session

from app.db.models import Campaign
from app.llm import chat_completion
from app.tools.command_tools import TOOL_HANDLERS, tools_for_scope
from app.tools.combat_tools import COMBAT_HANDLERS

_log = logging.getLogger(__name__)

MAX_TOOL_ROUNDS = 5


def execute_llm_with_tools(
    db: Session,
    campaign: Campaign,
    session_id: str | None = None,
    character_id: str | None = None,
    actor_id: str | None = None,
    is_dm: bool = False,
    message: str = "",
    message_context: dict | None = None,
    *,
    system_prompt: str = "",
    extra_tools: list[dict[str, Any]] | None = None,
    extra_context: dict[str, Any] | None = None,
    messages: list[dict[str, Any]] | None = None,
    skip_user_message: bool = False,
) -> dict[str, Any]:
    """Run the LLM with tool support, handle any tool calls, return final result.

    Can either pass ``message`` + ``system_prompt`` (simple case) OR pre-built ``messages``.
    Returns a dict compatible with ``command_result()`` output.
    """
    tools = tools_for_scope(campaign, is_dm)
    if extra_tools:
        tools = list(tools) + extra_tools

    if messages is None:
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        if message and not skip_user_message:
            messages.append({"role": "user", "content": message})

    try:
        for _round in range(MAX_TOOL_ROUNDS):
            resp = chat_completion(messages, tools=tools)

            # ── LLM not configured or returned plain string → fallback ──
            if resp is None:
                return {
                    "ok": True, "kind": "llm_unavailable",
                    "narration": "LLM 服务未配置，无法处理。请使用 /命令 格式。",
                    "data": extra_context or {},
                    "rolls": [], "state_changes": [], "events": [],
                }
            if isinstance(resp, str):
                return {
                    "ok": True, "kind": "llm_response",
                    "narration": resp or "（操作已完成。）",
                    "data": extra_context or {},
                    "rolls": [], "state_changes": [], "events": [],
                }

            # ── No tool calls → return content as narration ──
            if not resp.tool_calls:
                narration = resp.content or "（操作已完成。）"
                return {
                    "ok": True, "kind": "llm_response",
                    "narration": narration,
                    "data": extra_context or {},
                    "rolls": [], "state_changes": [], "events": [],
                }

            # ── Has tool calls → execute them ──
            assistant_msg: dict[str, Any] = {
                "role": "assistant",
                "content": resp.content,
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments,
                        },
                    }
                    for tc in resp.tool_calls
                ],
            }
            messages.append(assistant_msg)

            for tc in resp.tool_calls:
                tool_name = tc.function.name
                handler = TOOL_HANDLERS.get(tool_name) or COMBAT_HANDLERS.get(tool_name)
                try:
                    args = json.loads(tc.function.arguments or "{}")
                except json.JSONDecodeError:
                    args = {}

                # Inject common parameters
                args.setdefault("db", db)
                args.setdefault("campaign", campaign)
                args.setdefault("session_id", session_id)
                args.setdefault("actor_id", actor_id)
                args.setdefault("is_dm", is_dm)
                args.setdefault("user_id", str(message_context.get("sender_id", "")).strip() if message_context else "")
                args.setdefault("player_name", str(message_context.get("sender_id", "")).strip() if message_context else "")
                args.setdefault("message_context", message_context)
                # Inject character_id → load Character for combat tools
                if character_id:
                    from app.db.models import Character as _Char
                    _ch = db.get(_Char, character_id)
                    if _ch:
                        args.setdefault("character", _ch)

                if handler:
                    try:
                        result = handler(**{
                            k: v for k, v in args.items()
                            if k in {"db", "campaign", "session_id", "actor_id",
                                      "is_dm", "user_id", "player_name",
                                      "message_context", "character", "character_name",
                                      "class_name", "level", "ancestry",
                                      "background", "abilities", "category",
                                      "name", "description", "query",
                                      # Combat tool args
                                      "target", "weapon", "attack_index",
                                      "spell_name", "spell_level", "targets",
                                      "save_type", "use_bonus_action", "use_reaction",
                                      "ability", "reason", "question", "options"}
                        })
                    except Exception as exc:
                        _log.exception("Tool %s failed", tool_name)
                        result = {"ok": False, "narration": f"工具执行失败: {exc}"}
                else:
                    result = {"ok": False, "narration": f"未知工具: {tool_name}"}

                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": json.dumps(result, ensure_ascii=False, default=str),
                })

            # After executing tools, re-filter tools based on potentially changed mode
            tools = tools_for_scope(campaign, is_dm)
            if extra_tools:
                tools = list(tools) + extra_tools

        # ── Loop exhausted ──
        _log.warning("Tool loop exhausted after %d rounds", MAX_TOOL_ROUNDS)
        return {
            "ok": True, "kind": "llm_response",
            "narration": "操作已处理，如需更多信息请继续描述。",
            "data": extra_context or {},
            "rolls": [], "state_changes": [], "events": [],
        }

    except Exception as exc:
        _log.exception("execute_llm_with_tools failed")
        return {
            "ok": False, "kind": "llm_error",
            "narration": f"处理消息时出错：{exc}",
            "data": extra_context or {},
            "rolls": [], "state_changes": [], "events": [],
        }
