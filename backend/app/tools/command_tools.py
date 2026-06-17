"""OpenAI function-calling tool definitions and handler registry.

Every command that was previously dispatched via keyword-matching in
``execute_command()`` is registered here as a tool that the LLM can call.
"""

from __future__ import annotations

import copy
import json
from typing import Any, Callable

from sqlalchemy.orm import Session

from app.db.models import Campaign, CampaignSetting, CampaignSettingDraft, Character
from app.services import uid
from app.tools.character_builder import build_character_data, export_character_sheet
from app.character_build_flow import (
    _parse_fields, _active_session, _format_draft, _merge_draft, _missing,
    create_task, session_payload, TASK_TYPE as BUILD_TASK_TYPE,
    ABILITY_KEYS,
)
from app.qq_bindings import bind_qq, find_binding, find_bindings
from app.campaign_editor import create_draft, publish_drafts, discard_drafts
from app.tools.check_tools import CHECK_TOOLS, CHECK_HANDLERS
from app.tools.combat_tools import COMBAT_TOOLS, COMBAT_HANDLERS
# Use CampaignSettingDraft query directly for listing drafts

# ── Handler signature ─────────────────────────────────────────────
Handler = Callable[..., dict]


# ═══════════════════════════════════════════════════════════════════
#  TOOL SCHEMAS (OpenAI function-calling format)
# ═══════════════════════════════════════════════════════════════════

def _dm_only(desc: str) -> str:
    return desc + "（仅限 DM 使用）"


COMMAND_TOOLS: list[dict[str, Any]] = [
    # ── 基础命令 ──
    {
        "type": "function",
        "function": {
            "name": "status",
            "description": "查看当前战役状态（名称、场景、玩法模式、回合信息等）",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "help",
            "description": "显示可用命令列表",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    # ── 战役控制 ──
    {
        "type": "function",
        "function": {
            "name": "save",
            "description": _dm_only("保存战役进度，创建检查点"),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "pause",
            "description": _dm_only("暂停战役"),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "resume",
            "description": _dm_only("继续已暂停的战役"),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "enter_turn_mode",
            "description": _dm_only("进入回合制模式"),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "exit_turn_mode",
            "description": _dm_only("退出回合制模式"),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "start_combat",
            "description": _dm_only("投掷全体先攻并进入战斗"),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "end_combat",
            "description": _dm_only("结束战斗并返回自由模式"),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "next_turn",
            "description": _dm_only("切换到下一个行动者"),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    # ── 战役编辑 ──
    {
        "type": "function",
        "function": {
            "name": "enter_campaign_edit",
            "description": _dm_only("进入战役设定编辑模式"),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "exit_campaign_edit",
            "description": _dm_only("退出战役编辑模式"),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "publish_settings",
            "description": _dm_only("发布所有待处理的设定草稿"),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "discard_settings",
            "description": _dm_only("放弃所有待处理的设定草稿"),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_setting_drafts",
            "description": "查看当前待发布的设定草稿",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "undo_setting_draft",
            "description": _dm_only("撤销最近一条设定草稿"),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "validate_settings",
            "description": "检查设定中的悬空引用和冲突",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    # ── 模式切换 ──
    {
        "type": "function",
        "function": {
            "name": "enter_dice_assistant",
            "description": _dm_only("进入骰娘辅助模式"),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "exit_dice_assistant",
            "description": _dm_only("退出骰娘模式，返回战役叙事"),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    # ── 偏好设置 ──
    {
        "type": "function",
        "function": {
            "name": "enable_combat_roleplay",
            "description": "开启战斗扮演文字",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "disable_combat_roleplay",
            "description": "关闭战斗扮演文字",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    # ── 角色卡（快速创建） ──
    {
        "type": "function",
        "function": {
            "name": "create_character_quick",
            "description": "快速创建角色卡并绑定当前用户。需提供角色名和职业。",
            "parameters": {
                "type": "object",
                "properties": {
                    "character_name": {"type": "string", "description": "角色名"},
                    "class_name": {"type": "string", "description": "职业，如 法师/战士/游荡者"},
                    "level": {"type": "integer", "description": "等级，默认1", "default": 1},
                    "ancestry": {"type": "string", "description": "种族，如 人类/精灵/矮人", "default": "人类"},
                    "background": {"type": "string", "description": "背景"},
                    "abilities": {
                        "type": "object",
                        "description": "属性值，如 {\"str\":16,\"dex\":14}",
                    },
                },
                "required": ["character_name", "class_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_npc_quick",
            "description": (
                "快速创建一个 NPC/怪物角色卡。当用户说「创建NPC」「新建NPC」时调用。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "character_name": {"type": "string", "description": "NPC 名称"},
                    "class_name": {"type": "string", "description": "职业/类型"},
                    "level": {"type": "integer", "description": "等级/CR", "default": 1},
                    "ancestry": {"type": "string", "description": "种族", "default": "人类"},
                    "abilities": {
                        "type": "object",
                        "description": "属性值",
                    },
                },
                "required": ["character_name", "class_name"],
            },
        },
    },
    # ── 车卡草稿（分步创建，可编辑的草稿） ──
    {
        "type": "function",
        "function": {
            "name": "create_character_draft",
            "description": "创建可逐步编辑的车卡草稿。可带初始字段或留空。",
            "parameters": {
                "type": "object",
                "properties": {
                    "character_name": {"type": "string", "description": "角色名（可选）"},
                    "class_name": {"type": "string", "description": "职业（可选）"},
                    "level": {"type": "integer", "description": "等级", "default": 1},
                    "ancestry": {"type": "string", "description": "种族"},
                    "background": {"type": "string", "description": "背景"},
                    "abilities": {"type": "object", "description": "属性值"},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_character_draft",
            "description": "更新车卡草稿的字段，一次可更新多个。",
            "parameters": {
                "type": "object",
                "properties": {
                    "character_name": {"type": "string", "description": "新的角色名"},
                    "class_name": {"type": "string", "description": "新的职业"},
                    "level": {"type": "integer", "description": "新的等级"},
                    "ancestry": {"type": "string", "description": "新的种族"},
                    "background": {"type": "string", "description": "新的背景"},
                    "alignment": {"type": "string", "description": "阵营"},
                    "abilities": {"type": "object", "description": "属性值更新"},
                    "max_hp": {"type": "integer", "description": "最大生命值"},
                    "armor_class": {"type": "integer", "description": "护甲等级"},
                    "speed": {"type": "integer", "description": "速度"},
                    "gender": {"type": "string", "description": "性别"},
                    "age": {"type": "string", "description": "年龄"},
                    "faith": {"type": "string", "description": "信仰"},
                    "appearance": {"type": "string", "description": "外貌描述"},
                    "traits": {"type": "string", "description": "性格特点"},
                    "backstory": {"type": "string", "description": "背景故事"},
                    "spellcasting_ability": {"type": "string", "description": "施法关键属性"},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "show_character_draft",
            "description": "查看当前车卡草稿的内容。用户说「查看草稿」「我的车卡怎么样了」时调用。",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "commit_character_draft",
            "description": (
                "提交车卡草稿，保存为正式角色卡并绑定到用户。"
                "用户说「提交」「保存角色」「确认车卡」「完成车卡」时调用。"
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "cancel_character_draft",
            "description": "取消/放弃当前车卡草稿。用户说「取消车卡」「不要了」时调用。",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    # ── 战役设定草稿 ──
    {
        "type": "function",
        "function": {
            "name": "create_setting_draft",
            "description": (
                "创建一条战役设定草稿。用户说「加一个NPC设定」「记录一个地点」"
                "「保存这个组织」时调用。category: npc/location/faction/item/event"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "category": {
                        "type": "string",
                        "description": "设定类型：npc/location/faction/item/event",
                        "enum": ["npc", "location", "faction", "item", "event"],
                    },
                    "name": {"type": "string", "description": "名称"},
                    "description": {"type": "string", "description": "详细描述"},
                },
                "required": ["category", "name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "show_setting_drafts",
            "description": "查看当前所有待发布的设定草稿。用户说「查看草稿」「有哪些待发布的设定」时调用。",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "publish_setting_drafts",
            "description": (
                "发布所有待处理的设定草稿，正式保存到战役设定库。"
                "用户说「保存设定」「发布设定」「确认修改」「存档」时调用。"
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "discard_setting_drafts",
            "description": "丢弃所有待处理的设定草稿。用户说「放弃设定」「不要了」时调用。",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "bind_character",
            "description": "将当前 QQ 用户绑定到已有角色卡。当用户说「绑定角色」「绑定我的角色」时调用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "character_name": {
                        "type": "string",
                        "description": "要绑定的角色名（可选，不提供则绑定所有匹配的角色）",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "show_bindings",
            "description": "查看当前 QQ 用户的角色绑定情况。当用户说「查看绑定」「我的绑定」时调用。",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "export_character_sheet",
            "description": "导出角色卡为 XLSX 文件并发送给用户。当用户说「导出角色卡」「下载角色卡」时调用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "character_name": {
                        "type": "string",
                        "description": "要导出的角色名（可选，不提供则导出所有已绑定角色）",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "save_campaign_setting",
            "description": (
                "保存一条战役设定。当用户说「保存设定」「记录这个NPC」「存档」时调用。"
                "category 可选值：npc（NPC）、location（地点）、faction（组织）、item（物品）、event（事件）"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "category": {
                        "type": "string",
                        "description": "设定类型：npc/location/faction/item/event",
                        "enum": ["npc", "location", "faction", "item", "event"],
                    },
                    "name": {"type": "string", "description": "设定名称"},
                    "description": {"type": "string", "description": "详细描述"},
                },
                "required": ["category", "name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_npc_cards_from_settings",
            "description": _dm_only("从已发布的 NPC 设定批量生成角色卡"),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_campaign_from_prompt",
            "description": "创建新战役。用户说「创建战役」「新建战役」「创建新战役」时调用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "campaign_name": {"type": "string", "description": "新战役名称"},
                    "description": {"type": "string", "description": "战役描述（可选）"},
                },
                "required": ["campaign_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "enter_campaign_mode",
            "description": "进入 DM 战役叙事模式。用户说「进入DM」「进入战役」「开始游戏」时调用。需要先有当前战役。",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "exit_to_lobby",
            "description": "退出当前模式，返回游戏外大厅。用户说「退出」「返回大厅」时调用。",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "switch_campaign",
            "description": "切换到另一个战役。用户说「切换战役」「换个战役」时调用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "campaign_name": {"type": "string", "description": "要切换到的战役名称"},
                },
                "required": ["campaign_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "memory_search",
            "description": "搜索战役记忆",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "搜索关键词"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_attachment",
            "description": "读取用户最近上传的附件内容（支持 xlsx/pdf/docx/md/txt）。用户说「用刚才发的文件」「读那个文件」「打开那个文件」时调用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "index": {"type": "integer", "description": "附件索引: 0=最近, 1=上一个, 默认为0", "default": 0},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "spell_search",
            "description": "搜索 D&D 5E 法术",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "法术名或关键词"},
                },
                "required": ["query"],
            },
        },
    },
]


# ═══════════════════════════════════════════════════════════════════
#  HANDLER IMPLEMENTATIONS
# ═══════════════════════════════════════════════════════════════════

def _ok(narration: str, **kw: Any) -> dict:
    return {"ok": True, "kind": "tool_result", "narration": narration, "data": kw or {}}


def _err(narration: str, **kw: Any) -> dict:
    return {"ok": False, "kind": "tool_result", "narration": narration, "data": kw or {}}


# ── 基础命令 ──

def handle_status(db: Session, campaign: Campaign, **_kw: Any) -> dict:
    from app.campaign_control import campaign_status, play_style, format_turn_state
    config = campaign.config or {}
    return _ok(
        f"战役：{campaign.name}\n"
        f"简介：{campaign.description or '无'}\n"
        f"当前场景：{config.get('scene') or '未记录'}\n"
        f"状态：{campaign_status(campaign)}\n"
        f"玩法：{'骰娘辅助' if play_style(campaign) == 'dice_assistant' else '战役叙事'}\n"
        f"{format_turn_state(campaign)}",
    )


def handle_help(db: Session, campaign: Campaign, **_kw: Any) -> dict:
    return _ok(
        "可用操作：\n"
        "- 查看状态 / 创建角色 / 创建NPC\n"
        "- 保存设定 / 绑定角色 / 查看绑定 / 导出角色卡\n"
        "- 进入骰娘 / 退出骰娘\n"
        "- 进入战斗 / 结束战斗 / 下一回合\n"
        "- 保存战役 / 暂停战役 / 继续战役\n"
        "- 编辑战役 / 发布设定 / 查看草稿\n"
        "- 搜索法术 / 搜索记忆\n"
        "直接告诉我你想做什么即可。",
    )


# ── 角色卡 ──

def handle_create_character_quick(
    db: Session, campaign: Campaign,
    character_name: str = "", class_name: str = "",
    level: int = 1, ancestry: str = "人类", background: str = "",
    abilities: dict[str, int] | None = None,
    player_name: str = "",  # injected by caller
    **_kw: Any,
) -> dict:
    if not character_name or not class_name:
        return _err("需要提供角色名和职业。")
    raw: dict[str, Any] = {
        "character_name": character_name, "class_name": class_name,
        "player_name": player_name or "Player",
        "actor_type": "player",
        "level": level,
        "ancestry": ancestry,
        "background": background,
        "abilities": abilities or {},
    }
    char_data = build_character_data(raw)
    character = Character(
        id=uid("char"), campaign_id=campaign.id,
        player_name=player_name or "Player",
        character_name=character_name, data=char_data,
    )
    db.add(character)
    db.commit()
    # Auto-bind QQ user if user_id is numeric
    if user_id and user_id.isdigit():
        try:
            bind_qq(db, campaign.id, user_id, character, character_name)
        except Exception:
            pass
    return _ok(
        f"角色卡已创建：{character_name}（{character.id}）\n"
        f"职业：{class_name} 等级：{level} 种族：{ancestry}",
        character_id=character.id,
    )


def handle_create_npc_quick(
    db: Session, campaign: Campaign,
    character_name: str = "", class_name: str = "",
    level: int = 1, ancestry: str = "人类",
    abilities: dict[str, int] | None = None,
    **_kw: Any,
) -> dict:
    if not character_name or not class_name:
        return _err("需要提供 NPC 名称和职业/类型。")
    raw: dict[str, Any] = {
        "character_name": character_name, "class_name": class_name,
        "player_name": "DM", "actor_type": "npc",
        "level": level, "ancestry": ancestry,
        "abilities": abilities or {},
    }
    char_data = build_character_data(raw)
    npc = Character(
        id=uid("char"), campaign_id=campaign.id,
        player_name="DM", character_name=character_name, data=char_data,
    )
    db.add(npc)
    db.commit()
    return _ok(
        f"NPC 角色卡已创建：{character_name}（{npc.id}）\n"
        f"类型：{class_name} 等级：{level}",
        character_id=npc.id,
    )


def handle_bind_character(
    db: Session, campaign: Campaign,
    user_id: str = "",  # injected by caller
    character_name: str = "",
    **_kw: Any,
) -> dict:
    from sqlalchemy import select
    all_chars = db.scalars(
        select(Character).where(Character.campaign_id == campaign.id)
    ).all()
    # Find characters where integrations.qq_user_ids contains user_id
    matched: list[Character] = []
    for ch in all_chars:
        qq_ids = [str(q).strip() for q in ((ch.data.get("integrations") or {}).get("qq_user_ids") or [])]
        if user_id in qq_ids:
            if character_name and character_name not in ch.character_name:
                continue
            matched.append(ch)
    if not matched:
        return _err(
            "在当前战役中没有找到你的角色卡。请先创建角色，或确保角色卡的 QQ 设置里包含你的 QQ 号。"
        )
    bound: list[str] = []
    for ch in matched:
        existing = find_binding(db, campaign.id, user_id, ch.id)
        if not existing:
            binding_id = uid("ncb")
            from app.db.models import NapCatCharacterBinding
            db.add(NapCatCharacterBinding(
                id=binding_id, campaign_id=campaign.id,
                qq_user_id=user_id, character_id=ch.id,
                display_name=ch.character_name or ch.id,
            ))
            db.commit()
        bound.append(ch.character_name or ch.id)
    return _ok(
        f"已绑定角色卡：{'、'.join(bound)}。现在可以导出角色卡了。",
        bound_characters=bound,
    )


def handle_show_bindings(
    db: Session, campaign: Campaign,
    user_id: str = "",  # injected by caller
    **_kw: Any,
) -> dict:
    bindings = find_bindings(db, campaign.id, user_id)
    # Also check integrations
    from sqlalchemy import select
    all_chars = db.scalars(
        select(Character).where(Character.campaign_id == campaign.id)
    ).all()
    unbound: list[str] = []
    for ch in all_chars:
        qq_ids = [str(q).strip() for q in ((ch.data.get("integrations") or {}).get("qq_user_ids") or [])]
        if user_id in qq_ids and not any(b.character_id == ch.id for b in bindings):
            unbound.append(ch.character_name or ch.id)
    lines: list[str] = []
    if bindings:
        lines.append(f"=== 已绑定角色（{len(bindings)}）===")
        for b in bindings:
            ch = db.get(Character, b.character_id)
            name = ch.character_name if ch else "(角色已删除)"
            lines.append(f"  - {name} ({b.character_id})")
    if unbound:
        lines.append(f"=== 可绑定的角色（{len(unbound)}）===")
        for name in unbound:
            lines.append(f"  - {name}  → 使用「绑定角色」完成绑定")
    if not lines:
        lines.append("你当前没有任何角色绑定。请先创建角色卡。")
    return _ok("\n".join(lines))


def handle_export_character_sheet(
    db: Session, campaign: Campaign,
    user_id: str = "",  # injected by caller
    character_name: str = "",
    **_kw: Any,
) -> dict:
    """Generate xlsx and return the file path for the caller to upload."""
    from pathlib import Path
    from app.config import settings as _settings

    bindings = find_bindings(db, campaign.id, user_id)
    if character_name:
        bindings = [b for b in bindings if b.display_name == character_name
                     or (db.get(Character, b.character_id) or Character(character_name="")).character_name == character_name]
    if not bindings:
        return _err("没有找到可导出的角色卡。请先创建角色并使用「绑定角色」完成绑定。")

    template = next((_settings.data_dir / "raw").glob("*人物卡模板.xlsx"), None)
    if not template:
        return _err("人物卡模板文件未找到。")

    target_dir = _settings.data_dir / "generated" / "characters"
    target_dir.mkdir(parents=True, exist_ok=True)
    exported: list[str] = []
    paths: list[str] = []
    for b in bindings:
        ch = db.get(Character, b.character_id)
        if not ch:
            continue
        target = target_dir / f"{ch.id}.xlsx"
        try:
            export_character_sheet(ch.data, ch.player_name or "", Path(template), target)
            exported.append(ch.character_name or ch.id)
            paths.append(str(target))
        except Exception as exc:
            return _err(f"导出 {ch.character_name} 失败: {exc}")
    return _ok(
        f"已生成角色卡：{'、'.join(exported)}",
        exported_files=paths,
        character_names=exported,
    )


def handle_save_campaign_setting(
    db: Session, campaign: Campaign,
    category: str = "", name: str = "", description: str = "",
    **_kw: Any,
) -> dict:
    if category not in {"npc", "location", "faction", "item", "event"}:
        return _err(f"无效的分类：{category}。可选值：npc/location/faction/item/event")
    if not name.strip():
        return _err("需要提供设定名称。")
    setting = CampaignSetting(
        id=uid("setting"), campaign_id=campaign.id,
        category=category, name=name.strip(),
        summary=(description or "")[:200],
        content={"description": description or ""},
        status="published", version=1,
    )
    db.add(setting)
    db.commit()
    count = db.query(CampaignSetting).filter(
        CampaignSetting.campaign_id == campaign.id
    ).count()
    return _ok(
        f"设定已保存：[{category}] {name}（{setting.id}）\n当前战役共有 {count} 条设定。",
        setting_id=setting.id,
    )


# ═══════════════════════════════════════════════════════════════════
#  CHARACTER BUILD DRAFT HANDLERS
# ═══════════════════════════════════════════════════════════════════

def handle_create_character_draft(
    db: Session, campaign: Campaign,
    user_id: str = "", player_name: str = "",
    character_name: str = "", class_name: str = "",
    level: int = 1, ancestry: str = "", background: str = "",
    abilities: dict[str, int] | None = None,
    **extra: Any,
) -> dict:
    """Create or return existing character build draft."""
    # Check for existing draft using character_build_flow internals
    platform = "napcat" if user_id else "web"
    chat_id: str | None = None
    scoped_session: str | None = None
    existing = _active_session(db, campaign, platform, user_id, scoped_session)
    if existing:
        return _ok(
            f"你已经有一个进行中的车卡草稿。\n{_format_draft(existing)}",
            character_build_session=session_payload(existing),
            task_session_id=existing.id,
        )
    draft = {
        "campaign_id": campaign.id,
        "player_name": user_id or player_name or "Player",
        "character_name": character_name,
        "class_name": class_name,
        "level": level,
        "abilities": {key: (abilities or {}).get(key, 10) for key in ABILITY_KEYS},
        "_meta": {"version": 1},
    }
    if ancestry:
        draft["ancestry"] = ancestry
    if background:
        draft["background"] = background
    for k, v in extra.items():
        if k not in draft and v:
            draft[k] = v
    missing = _missing(draft)
    item = create_task(
        db, campaign, BUILD_TASK_TYPE, platform, chat_id, user_id, scoped_session,
        status="waiting_user", draft_data=draft, missing_fields=missing,
        next_prompt="请继续补充角色名、职业、种族、背景、等级、属性等车卡信息。",
    )
    db.commit()
    return _ok(
        f"车卡草稿已创建。\n{_format_draft(item)}",
        character_build_session=session_payload(item),
        task_session_id=item.id,
    )


def handle_update_character_draft(
    db: Session, campaign: Campaign,
    user_id: str = "",
    **fields: Any,
) -> dict:
    """Update the current character build draft with new field values."""
    platform = "napcat" if user_id else "web"
    existing = _active_session(db, campaign, platform, user_id, None)
    if not existing:
        return _err("你没有进行中的车卡草稿。请先说「车卡」来开始创建角色。")
    # Filter known fields
    allowed = {
        "character_name", "class_name", "level", "ancestry", "background",
        "alignment", "abilities", "max_hp", "armor_class", "speed",
        "gender", "age", "faith", "appearance", "hair", "height", "skin",
        "weight", "eyes", "traits", "ideals", "bonds", "flaws", "backstory",
        "spellcasting_ability",
    }
    patch = {k: v for k, v in fields.items() if k in allowed and v is not None}
    if "abilities" in patch and isinstance(patch["abilities"], dict):
        patch["abilities"] = {k: int(v) for k, v in patch["abilities"].items()}
    if not patch:
        return _err("没有提供有效的更新字段。")
    draft = copy.deepcopy(existing.draft_data or {})
    draft = _merge_draft(draft, patch)
    existing.draft_data = draft
    existing.missing_fields = _missing(draft)
    if not existing.missing_fields:
        existing.status = "ready_to_commit"
    db.commit()
    updated_fields = "、".join(patch.keys())
    return _ok(
        f"草稿已更新：{updated_fields}。\n{_format_draft(existing)}",
        character_build_session=session_payload(existing),
    )


def handle_show_character_draft(
    db: Session, campaign: Campaign,
    user_id: str = "", **_kw: Any,
) -> dict:
    """Show current character build draft."""
    platform = "napcat" if user_id else "web"
    existing = _active_session(db, campaign, platform, user_id, None)
    if not existing:
        return _ok("你当前没有进行中的车卡草稿。说「车卡」来开始创建角色。")
    return _ok(
        f"当前车卡草稿：\n{_format_draft(existing)}",
        character_build_session=session_payload(existing),
        task_session_id=existing.id,
    )


def handle_commit_character_draft(
    db: Session, campaign: Campaign,
    user_id: str = "", **_kw: Any,
) -> dict:
    """Submit character build draft → create Character record."""
    from app.character_build_flow import submit_character_build as _submit
    platform = "napcat" if user_id else "web"
    existing = _active_session(db, campaign, platform, user_id, None)
    if not existing:
        return _err("你没有进行中的车卡草稿。请先说「车卡」来开始创建角色。")
    raw = existing.draft_data or {}
    if not raw.get("character_name") or not raw.get("class_name"):
        return _err("草稿缺少角色名或职业，请先补充后再提交。")
    char_data = build_character_data(raw)
    character = Character(
        id=uid("char"), campaign_id=campaign.id,
        player_name=user_id or raw.get("player_name", "Player"),
        character_name=raw["character_name"], data=char_data,
    )
    db.add(character)
    existing.status = "committed"
    existing.created_object_type = "character"
    existing.created_object_id = character.id
    if user_id and user_id.isdigit():
        try:
            bind_qq(db, campaign.id, user_id, character, raw["character_name"])
        except Exception:
            pass
    db.commit()
    return _ok(
        f"角色卡已提交：{raw['character_name']}（{character.id}）\n"
        f"职业：{raw['class_name']} 等级：{raw.get('level', 1)}。\n"
        "已自动绑定到你的QQ。可以用「导出角色卡」下载xlsx。",
        character_id=character.id,
    )


def handle_cancel_character_draft(
    db: Session, campaign: Campaign,
    user_id: str = "", **_kw: Any,
) -> dict:
    """Cancel current character build draft."""
    platform = "napcat" if user_id else "web"
    existing = _active_session(db, campaign, platform, user_id, None)
    if not existing:
        return _ok("你没有进行中的车卡草稿。")
    name = (existing.draft_data or {}).get("character_name", "未命名")
    existing.status = "cancelled"
    db.commit()
    return _ok(f"已取消车卡草稿「{name}」。")

# ═══════════════════════════════════════════════════════════════════
#  CAMPAIGN SETTING DRAFT HANDLERS
# ═══════════════════════════════════════════════════════════════════

def handle_create_setting_draft(
    db: Session, campaign: Campaign,
    category: str = "", name: str = "", description: str = "",
    user_id: str = "",  # for ownership tracking
    **_kw: Any,
) -> dict:
    """Create a campaign setting draft."""
    if category not in {"npc", "location", "faction", "item", "event"}:
        return _err(f"无效分类：{category}。可选：npc/location/faction/item/event")
    if not name.strip():
        return _err("需要提供设定名称。")
    draft = create_draft(
        db, campaign.id, "create",
        proposal={"category": category, "name": name.strip(), "description": description or ""},
        actor_id=user_id or None,
    )
    db.commit()
    return _ok(
        f"设定草稿已创建：[{category}] {name}（{draft.id}）。"
        "继续添加更多设定，或说「保存设定」来发布。",
        draft_id=draft.id,
    )


def handle_show_setting_drafts(
    db: Session, campaign: Campaign, **_kw: Any,
) -> dict:
    """Show current setting drafts."""
    from sqlalchemy import select as _select
    drafts = db.scalars(
        _select(CampaignSettingDraft).where(
            CampaignSettingDraft.campaign_id == campaign.id,
            CampaignSettingDraft.status == "pending",
        )
    ).all()
    if not drafts:
        return _ok("当前没有待发布的设定草稿。")
    lines = [f"=== 设定草稿（{len(drafts)}）==="]
    for d in drafts:
        lines.append(f"  [{d.category}] {d.name}: {(d.proposal or {}).get('description', '')[:80]}")
    return _ok("\n".join(lines), draft_count=len(drafts))


def handle_publish_setting_drafts(
    db: Session, campaign: Campaign, **_kw: Any,
) -> dict:
    """Publish all pending setting drafts → campaign_settings."""
    from sqlalchemy import select as _select
    drafts = db.scalars(
        _select(CampaignSettingDraft).where(
            CampaignSettingDraft.campaign_id == campaign.id,
            CampaignSettingDraft.status == "pending",
        )
    ).all()
    if not drafts:
        return _ok("没有待发布的设定草稿。")
    published = publish_drafts(db, campaign.id, actor_id=None)
    count = len(published)
    names = "、".join(s.name for s in published[:10])
    return _ok(
        f"已发布 {count} 条设定：{names}。",
        published_count=count,
        setting_ids=[s.id for s in published],
    )


def handle_discard_setting_drafts(
    db: Session, campaign: Campaign, **_kw: Any,
) -> dict:
    """Discard all pending setting drafts."""
    from sqlalchemy import select as _select
    drafts = db.scalars(
        _select(CampaignSettingDraft).where(
            CampaignSettingDraft.campaign_id == campaign.id,
            CampaignSettingDraft.status == "pending",
        )
    ).all()
    if not drafts:
        return _ok("没有待发布的设定草稿。")
    count = len(drafts)
    discarded = discard_drafts(db, campaign.id)
    return _ok(f"已放弃 {discarded} 条设定草稿。")

# ═══════════════════════════════════════════════════════════════════
#  DELEGATE TO execute_command (22 DM-only / query commands)
# ═══════════════════════════════════════════════════════════════════

def _via_execute_command(command_name: str):
    """Return a handler that dispatches to execute_command()."""
    def handler(db: Session, campaign: Campaign, is_dm: bool = False,
                session_id: str = None, actor_id: str = None,
                message_context: dict = None, **_kw: Any) -> dict:
        from app.commands import Command
        from app.campaign_control import execute_command
        return execute_command(
            db, Command(command_name), campaign,
            session_id, actor_id, is_dm, message_context,
        )
    return handler

def handle_enter_campaign_mode(
    db: Session, campaign: Campaign, **_kw: Any,
) -> dict:
    """Enter DM campaign mode from lobby."""
    config = copy.deepcopy(campaign.config or {})
    config["play_style"] = "campaign"
    config.pop("dice_dm_confirmation_pending", None)
    campaign.config = config
    db.commit()
    return _ok(f"已进入 DM 模式。当前战役: {campaign.name}。\n发送 /退出 返回大厅。")

def handle_exit_to_lobby(
    db: Session, campaign: Campaign, **_kw: Any,
) -> dict:
    """Exit current mode to lobby."""
    config = copy.deepcopy(campaign.config or {})
    config["play_style"] = "lobby"
    config.pop("dice_dm_confirmation_pending", None)
    campaign.config = config
    db.commit()
    return _ok(f"已返回大厅。当前战役: {campaign.name}。\n发送 /进入DM 或 /进入骰娘 开始游戏。")

def handle_read_attachment(
    db: Session, campaign: Campaign,
    index: int = 0, **_kw: Any,
) -> dict:
    """Read a stored attachment from campaign config."""
    stored = (campaign.config or {}).get("last_attachments") or []
    if not stored:
        return _err("最近没有收到附件。请先发送文件。")
    if index >= len(stored):
        return _err(f"只有 {len(stored)} 个最近附件，索引 {index} 超出范围。")
    item = stored[index]
    parser = item.get("parser", "unknown")
    meta = item.get("meta", {})
    content = item.get("content", "")
    summary = f"[附件 #{index+1}] 解析器: {parser}\n"
    if meta:
        if isinstance(meta, dict) and "character_data" in meta:
            import json as _json_r
            summary += "类型: 人物卡\n"
            summary += f"内容:\n{_json_r.dumps(meta['character_data'], ensure_ascii=False, indent=2)}"
        else:
            summary += f"元数据: {meta}\n"
    if content and not (isinstance(meta, dict) and "character_data" in meta):
        summary += f"内容: {content[:2000]}"
    return _ok(summary, attachment_index=index, parser=parser,
               has_character_data=bool(isinstance(meta, dict) and "character_data" in meta),
               turn_consuming=False)


def handle_complete_character_sheet(
    db: Session, campaign: Campaign,
    character_name: str = "", **_kw: Any,
) -> dict:
    """Enqueue background subagent to complete a character sheet."""
    from sqlalchemy import select as _sel
    from app.db.models import TaskSession
    from app.services import uid
    from app.subagent_runner import enqueue_subagent_task

    character = db.scalar(_sel(Character).where(
        Character.campaign_id == campaign.id, Character.character_name == character_name,
    ))
    if not character:
        return _err(f"未找到角色: {character_name}")
    task = TaskSession(
        id=uid("task"), campaign_id=campaign.id, task_type="subagent_proposal",
        platform="system", chat_id=None, owner_user_id=None, session_id=None,
        status="queued", priority=2, draft_data={},
        proposal_data={"agent_role": "character_sheet_completer",
                       "proposal": {"character_id": character.id}},
        missing_fields=[], next_prompt=f"补全{character_name}的角色卡。",
    )
    db.add(task); db.commit()
    enqueue_subagent_task(task.id)
    return _ok(f"后台开始补全 {character_name} 的角色卡。完成后会自动通知你。")


def handle_generate_cards_from_settings(
    db: Session, campaign: Campaign, **_kw: Any,
) -> dict:
    """Synchronously create character cards for all NPC/monster settings."""
    from app.campaign_editor import list_settings, setting_to_npc_character

    settings = [s for s in list_settings(db, campaign.id) if s.category in {"npc", "monster"}]
    if not settings:
        return _err("当前战役没有已发布的 NPC/怪物设定。请先创建。")
    created = []
    for s in settings:
        ch = setting_to_npc_character(db, s)
        if ch:
            created.append(ch.character_name)
    return _ok(f"已为 {len(created)} 个 NPC 创建角色卡: {', '.join(created[:10])}。",
               created_count=len(created))


def handle_check_background_tasks(
    db: Session, campaign: Campaign, **_kw: Any,
) -> dict:
    """Check background subagent task progress."""
    from app.db.models import TaskSession
    from sqlalchemy import select as _sel

    tasks = db.scalars(_sel(TaskSession).where(
        TaskSession.campaign_id == campaign.id,
        TaskSession.task_type == "subagent_proposal",
        TaskSession.status.in_(["queued", "running", "ready_to_review"]),
    ).order_by(TaskSession.updated_at.desc()).limit(10)).all()
    if not tasks:
        return _ok("no bg tasks", turn_consuming=False)
    lines = ["bg tasks:"]
    for t in tasks:
        prog = (t.proposal_data or {}).get("progress", "")
        lines.append(f"  [{t.status}] {t.next_prompt or t.id[:20]} {prog}")
    return _ok("\n".join(lines), task_count=len(tasks), turn_consuming=False)




def handle_generate_npc_set(
    db: Session, campaign: Campaign,
    count: int = 0, theme: str = "", batch_size: int = 6, **_kw: Any,
) -> dict:
    """Split N NPCs into batches and enqueue background subagent tasks."""
    if count < 1:
        return _err("需要指定 NPC 数量。例如: 创建30个NPC")
    batch_size = max(3, min(batch_size, 8))  # clamp 3-8
    from app.db.models import TaskSession
    from app.services import uid
    from app.subagent_runner import enqueue_subagent_task

    batches = (count + batch_size - 1) // batch_size
    task_ids = []
    for b in range(batches):
        start = b * batch_size
        task = TaskSession(
            id=uid("task"), campaign_id=campaign.id, task_type="subagent_proposal",
            platform="system", chat_id=None, owner_user_id=None, session_id=None,
            status="queued", priority=2, draft_data={},
            proposal_data={
                "agent_role": "npc_batch_worker",
                "proposal": {
                    "batch_start": start, "batch_size": batch_size,
                    "total_count": count, "theme": theme or campaign.name,
                },
            },
            missing_fields=[], next_prompt=f"NPC {start+1}-{min(start+batch_size, count)}",
        )
        db.add(task); task_ids.append(task.id)
    db.commit()
    for tid in task_ids:
        enqueue_subagent_task(tid)
    return _ok(f"后台开始生成 {count} 个 NPC（{batches} 批，每批 {batch_size} 个）。完成后自动通知。")


def handle_switch_campaign(
    db: Session, campaign: Campaign,
    campaign_name: str = "", **_kw: Any,
) -> dict:
    """Switch to a different campaign."""
    from sqlalchemy import select as _sel
    from app.qq_bindings import set_active_napcat_campaign
    if not campaign_name.strip():
        return _err("请提供战役名称。")
    target = db.scalar(_sel(Campaign).where(Campaign.name == campaign_name.strip()))
    if not target:
        target = db.scalar(_sel(Campaign).where(Campaign.id == campaign_name.strip()))
    if not target:
        all_campaigns = db.scalars(_sel(Campaign)).all()
        names = "、".join(c.name for c in all_campaigns[:10])
        return _err(f"未找到战役「{campaign_name}」。当前战役: {names}")
    set_active_napcat_campaign(db, target)
    return _ok(f"已切换到战役: {target.name}（{target.id}）")

_DELEGATED = [
    "create_campaign_from_prompt", "delete_active_campaign",
    "save", "pause", "resume",
    "enter_turn_mode", "exit_turn_mode",
    "start_combat", "end_combat", "next_turn",
    "enter_campaign_edit", "exit_campaign_edit",
    "publish_settings", "discard_settings",
    "list_setting_drafts", "undo_setting_draft", "validate_settings",
    "enter_dice_assistant", "exit_dice_assistant",
    "enable_combat_roleplay", "disable_combat_roleplay",
    "create_npc_cards_from_settings",
    "memory_search", "spell_search",
]
_DELEGATED_HANDLERS = {name: _via_execute_command(name) for name in _DELEGATED}


# ═══════════════════════════════════════════════════════════════════
#  HANDLER REGISTRY
# ═══════════════════════════════════════════════════════════════════

TOOL_HANDLERS: dict[str, Handler] = {
    "status": handle_status,
    "help": handle_help,
    "bind_character": handle_bind_character,
    "show_bindings": handle_show_bindings,
    "export_character_sheet": handle_export_character_sheet,
    "create_character_quick": handle_create_character_quick,
    "create_npc_quick": handle_create_npc_quick,
    "save_campaign_setting": handle_save_campaign_setting,
    # Character build drafts
    "create_character_draft": handle_create_character_draft,
    "update_character_draft": handle_update_character_draft,
    "show_character_draft": handle_show_character_draft,
    "commit_character_draft": handle_commit_character_draft,
    "cancel_character_draft": handle_cancel_character_draft,
    # Campaign setting drafts
    "create_setting_draft": handle_create_setting_draft,
    "show_setting_drafts": handle_show_setting_drafts,
    "publish_setting_drafts": handle_publish_setting_drafts,
    "discard_setting_drafts": handle_discard_setting_drafts,
    # Check & dice tools (from check_tools.py)
    "ability_check": CHECK_HANDLERS["ability_check"],
    "saving_throw": CHECK_HANDLERS["saving_throw"],
    "apply_damage": CHECK_HANDLERS["apply_damage"],
    "apply_healing": CHECK_HANDLERS["apply_healing"],
    "apply_condition": CHECK_HANDLERS["apply_condition"],
    "remove_condition": CHECK_HANDLERS["remove_condition"],
    "get_character_snapshot": CHECK_HANDLERS["get_character_snapshot"],
    "undo_damage": CHECK_HANDLERS["undo_damage"],
    "undo_healing": CHECK_HANDLERS["undo_healing"],
    "recent_changes": CHECK_HANDLERS["recent_changes"],
    # Combat tools (from combat_tools.py)
    "combat_attack": COMBAT_HANDLERS["combat_attack"],
    "combat_cast_spell": COMBAT_HANDLERS["combat_cast_spell"],
    "combat_ability_check": COMBAT_HANDLERS["combat_ability_check"],
    "combat_dash": COMBAT_HANDLERS["combat_dash"],
    "combat_disengage": COMBAT_HANDLERS["combat_disengage"],
    "combat_dodge": COMBAT_HANDLERS["combat_dodge"],
    "ask_clarification": COMBAT_HANDLERS["ask_clarification"],
    "use_feature": COMBAT_HANDLERS["use_feature"],
    "end_turn": COMBAT_HANDLERS["end_turn"],
    "turn_status": COMBAT_HANDLERS["turn_status"],
    # Lobby mode tools
    "enter_campaign_mode": handle_enter_campaign_mode,
    "exit_to_lobby": handle_exit_to_lobby,
    "switch_campaign": handle_switch_campaign,
    "read_attachment": handle_read_attachment,
    "complete_character_sheet": handle_complete_character_sheet,
    "generate_cards_from_settings": handle_generate_cards_from_settings,
    "generate_npc_set": handle_generate_npc_set,
    "check_background_tasks": handle_check_background_tasks,
    # Delegated to execute_command
    **_DELEGATED_HANDLERS,
}


def tools_for_scope(campaign: Campaign, is_dm: bool, message: str = "") -> list[dict[str, Any]]:
    """Return tools available given the current campaign mode, user role, and message context."""
    from app.campaign_control import play_style
    from app.campaign_turns import runtime_mode
    mode = runtime_mode(campaign)
    style = play_style(campaign)
    msg_lower = message.lower() if message else ""

    dm_only_commands = {
        "save", "pause", "resume", "enter_turn_mode", "exit_turn_mode",
        "start_combat", "end_combat", "next_turn",
        "enter_campaign_edit", "exit_campaign_edit",
        "publish_settings", "discard_settings", "undo_setting_draft",
        "enter_dice_assistant", "exit_dice_assistant",
        "create_npc_cards_from_settings",
    }

    # ── Slash-only: not surfaced to LLM ──
    slash_only = {"help", "enter_campaign_edit", "exit_campaign_edit",
                  "enter_dice_assistant", "exit_dice_assistant", "ask_clarification"}

    always_available = {
        "status", "show_bindings", "bind_character",
        "export_character_sheet", "create_character_quick",
        "save_campaign_setting", "spell_search", "memory_search",
        "enable_combat_roleplay", "disable_combat_roleplay",
        # Draft tools (all modes)
        "create_character_draft", "update_character_draft",
        "show_character_draft", "commit_character_draft", "cancel_character_draft",
        "create_setting_draft", "show_setting_drafts",
        "publish_setting_drafts", "discard_setting_drafts",
        "list_setting_drafts", "validate_settings",
        "create_npc_quick",
        "generate_npc_set",
    }

    # ── Lobby mode: limited tool set ──
    lobby_tools = {
        "status", "create_campaign_from_prompt", "delete_active_campaign",
        "enter_campaign_mode", "exit_to_lobby", "switch_campaign",
        "create_character_quick", "create_character_draft",
        "update_character_draft", "show_character_draft",
        "commit_character_draft", "cancel_character_draft",
        "create_npc_quick",
        "save_campaign_setting", "create_setting_draft",
        "show_setting_drafts", "publish_setting_drafts", "discard_setting_drafts",
        "list_setting_drafts", "validate_settings",
        "bind_character", "show_bindings",
        "export_character_sheet",
        "spell_search", "memory_search",
    }

    allowed: set[str] = set(always_available)
    if is_dm:
        allowed |= dm_only_commands
    if style == "lobby":
        allowed = lobby_tools  # lobby always has the same set, DM or not
    if style == "dice_assistant":
        allowed -= {"enter_campaign_edit", "exit_campaign_edit",
                     "publish_settings", "discard_settings",
                     "undo_setting_draft", "validate_settings"}
    if mode == "campaign_edit":
        allowed = {"help", "status", "exit_campaign_edit",
                    "publish_settings", "discard_settings",
                    "list_setting_drafts", "undo_setting_draft",
                    "validate_settings", "save_campaign_setting"}
    elif mode == "turn_based":
        allowed |= {"next_turn", "start_combat", "end_combat"}

    result = [t for t in COMMAND_TOOLS if t["function"]["name"] in allowed and t["function"]["name"] not in slash_only]
    # Check and combat tools only in game modes (not lobby), filtered by message relevance
    if style != "lobby":
        _has_mech = any(kw in msg_lower for kw in (
            "攻击", "attack", "伤害", "damage", "治疗", "heal", "检定", "check",
            "豁免", "save", "属性", "ability", "技能", "skill", "法术", "spell",
            "施法", "cast", "hp", "ac", "先攻", "initiative", "力量", "敏捷",
            "体质", "智力", "感知", "魅力", "武器", "weapon", "护甲", "armor",
            "状态", "condition", "效果", "effect", "疾走", "dash", "撤退", "disengage",
            "闪避", "dodge", "结束回合", "end turn", "动作如潮", "action surge",
            "撤销", "undo", "回退", "回滚",
        ))
        if _has_mech or not msg_lower:
            result.extend(CHECK_TOOLS)
            result.extend([t for t in COMBAT_TOOLS if t["function"]["name"] not in slash_only])
    return result
