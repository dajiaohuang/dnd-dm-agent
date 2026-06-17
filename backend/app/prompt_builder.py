"""Unified dynamic system prompt builder.

All LLM prompt assembly goes through ``build_system_prompt()``.
Each aspect is a separate module — returns "" when not applicable.
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy.orm import Session

from app.db.models import Campaign, CampaignEvent, Character


# ═══════════════════════════════════════════════════════════════════
#  Module 1: BASE ROLE INSTRUCTIONS
# ═══════════════════════════════════════════════════════════════════

def _base_role(
    mode: str,
    campaign: Campaign | None = None,
    narrative_mode: bool = False,
    force_roleplay: bool = False,
    turn_based: bool = False,
) -> str:
    """Core role instructions, different per mode."""

    if turn_based:
        # Turn-based combat — built separately in _turn_based_block
        return ""

    if mode == "lobby":
        active = campaign is not None
        info = (f"当前选中战役: {campaign.name}（{campaign.id}）\n"
                f"简介: {campaign.description or '无'}\n" if active else
                "当前没有选中战役。用户需先创建或选择战役。\n")
        ops = ("- 角色卡: 创建/修改/查看(默认当前战役)\n"
               "- 设定: 添加/修改/查看(当前战役)\n"
               "- 绑定导出: 绑定QQ/查看绑定/导出角色卡\n" if active else
               "（选战役后才可车卡和改设定）\n")
        return (
            "你是 D&D 5E 跑团管理助手。当前处于「游戏外模式」。\n"
            "职责：管理战役、创建角色卡、编辑设定。\n"
            "用户说「进入DM」或「进入骰娘」开始游戏。\n"
            f"\n━━━ 当前战役 ━━━\n{info}\n"
            f"━━━ 操作 ━━━\n- 战役管理: 创建/切换/删除/查看战役\n{ops}"
            "- 查询: 法术搜索\n"
            "进入DM/骰娘时若无当前战役则提示先选。\n"
            "只输出管理信息，禁止剧情、扮演、检定、建议。"
        )

    if mode == "dice":
        return (
            "你是 D&D 5E 工具型骰娘。真人 DM 管理战斗，你只负责机械结算。\n"
            "禁止 NPC 台词、剧情续写、战术建议。需要检定时用 ability_check/saving_throw。\n"
            "信息不足时追问，记错/撤销用 undo_damage/undo_healing。"
        )

    # DM mode
    combat = bool(((campaign.config or {}).get("turn_state") or {}).get("combat")) if campaign else False
    combat_instr = "战斗中允许描写行动结果和 NPC 反应，但禁止虚构机械数值。" if combat else ""
    return (
        "You are a concise DND Dungeon Master. Continue from established campaign memory. "
        "Use the current character sheet and relevant rules. Do not invent mechanical state changes. "
        "Never stop to ask a player to roll dice; state the required roll clearly so the system can "
        f"roll it immediately and continue. {combat_instr}"
        "When the user asks to drink a potion, take a rest, make a skill check, create a character, "
        "save a setting, check bindings, or export a sheet, use a function call. "
        "When you need to make a check or saving throw, call ability_check or saving_throw tools "
        "with the character's real modifiers — do NOT invent dice results. "
        "When an action can use multiple skills, ask the user which skill to use."
    )


# ═══════════════════════════════════════════════════════════════════
#  Module 2: COMBAT AWARENESS (dice mode, event-driven)
# ═══════════════════════════════════════════════════════════════════

_COMBAT_SIGNS = {"先攻", "initiative", "attack", "攻击", "伤害", "damage",
                 "进入战斗", "start combat", "投掷", "检定", "豁免", "save"}
_DM_COMBAT_WORDS = {"进入战斗", "start combat", "管理系统战斗", "系统回合"}


def _combat_awareness(db: Session | None, campaign: Campaign | None, mode: str) -> str:
    """If dice mode and recent events show combat, append awareness guidance."""
    if mode != "dice" or not campaign or not db:
        return ""
    events = db.scalars(
        db.query(CampaignEvent)
        .filter(CampaignEvent.campaign_id == campaign.id)
        .order_by(CampaignEvent.created_at.desc()).limit(8)
    ).all()
    text = " ".join(
        (getattr(e, "content", "") or "") + " " +
        str((getattr(e, "metadata", {}) or {}).get("raw_player_input", ""))
        for e in events
    ).lower()
    if not any(kw.lower() in text for kw in _COMBAT_SIGNS):
        return ""

    awareness = (
        "[战斗感知] 最近事件中出现战斗行动。真人 DM 在管理回合：\n"
        "- 从事件序列推断大致轮到谁，提醒跳过角色或询问轮次\n"
        "- 不确定时问「刚才是X行动了，现在轮到谁？」\n"
        "- 以上建议性，不强制"
    )
    if any(kw.lower() in text for kw in _DM_COMBAT_WORDS):
        awareness += (
            "\n\n[系统回合制待确认] DM 似乎想进入系统管理。回复：\n"
            "「准备进入系统回合制。请确认: 1)哪些角色参战 2)有无先攻优势/劣势？」\n"
            "不要自己决定参战者。继续以非管理模式处理，等 DM 确认后再投先攻。"
        )
    return awareness


# ═══════════════════════════════════════════════════════════════════
#  Module 3: COMBAT OUTPUT INSTRUCTIONS (roleplay/advice toggles)
# ═══════════════════════════════════════════════════════════════════

def _combat_output(campaign: Campaign | None, mode: str,
                   narrative_mode: bool = False,
                   force_roleplay: bool = False) -> str:
    """Combat roleplay and advice output constraints."""
    if not campaign:
        return ""
    from app.dice_assistant import combat_preference
    combat_active = bool(((campaign.config or {}).get("turn_state") or {}).get("combat"))
    rp = combat_preference(campaign, "roleplay")
    adv = combat_preference(campaign, "advice")

    if mode == "dice":
        if not combat_active:
            return "始终禁止 NPC 台词和剧情续写。禁止替真实 DM 决定结果。禁止给玩家战术建议。"
        if force_roleplay:
            return "只允许为当前托管角色的本次行动添加简短扮演文字；禁止扮演其他 NPC、剧情续写或推进剧情，禁止替真实 DM 决定结果。"
        if rp:
            return "允许为当前行动添加简短扮演文字；禁止扮演其他 NPC、剧情续写或推进剧情。"
        return "始终禁止 NPC 台词和剧情续写。禁止替真实 DM 决定结果。"
    return ""


# ═══════════════════════════════════════════════════════════════════
#  Module 4: HOT DATA (character snapshot, on-demand)
# ═══════════════════════════════════════════════════════════════════

_MECH_KEYWORDS = {
    "攻击", "attack", "伤害", "damage", "治疗", "heal", "检定", "check",
    "豁免", "save", "属性", "ability", "技能", "skill", "法术", "spell",
    "施法", "cast", "HP", "AC", "先攻", "initiative", "力量", "敏捷",
    "体质", "智力", "感知", "魅力", "str", "dex", "con", "int", "wis", "cha",
    "熟练", "proficiency", "长剑", "武器", "weapon", "护甲", "armor",
    "状态", "condition", "效果", "effect", "车卡", "角色卡", "sheet",
}


def _hot_data(db: Session | None, character: Character | None, message: str) -> str:
    """HotSnapshot — only when message needs mechanical data."""
    if not character or not message or not db:
        return ""
    if not any(kw.lower() in message.lower() for kw in _MECH_KEYWORDS):
        return ""
    from app.tools.hot_character import hot_character_for_llm
    hot = hot_character_for_llm(db, character.id)
    if not hot:
        return ""
    return f"[当前角色热数据]\n{json.dumps(hot, ensure_ascii=False)}"


# ═══════════════════════════════════════════════════════════════════
#  Module 5: ATTACHMENT INFO
# ═══════════════════════════════════════════════════════════════════

def _attachment_info(campaign: Campaign | None) -> str:
    """Recent attachment summary."""
    if not campaign:
        return ""
    stored = (campaign.config or {}).get("last_attachments") or []
    if not stored:
        return ""
    lines = [f"[最近附件] 收到 {len(stored)} 个文件。用户说「用刚才发的文件开卡」时调用 read_attachment。"]
    for i, a in enumerate(stored):
        meta = a.get("meta", {})
        ctype = "人物卡" if (isinstance(meta, dict) and "character_data" in meta) else "文档"
        lines.append(f"  [{i+1}] {ctype} ({a.get('parser','?')})")
    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════
#  Module 6: TURN-BASED BLOCK (initiative + quotas + rules)
# ═══════════════════════════════════════════════════════════════════

def _turn_based_block(
    campaign: Campaign,
    character: Character | None,
    db: Session | None,
    narrative: bool = False,
) -> str:
    """Full turn-based combat prompt: initiative order, hot data, action quotas, rules."""
    from app.campaign_turns import format_actions_remaining, get_actions_remaining

    state = (campaign.config or {}).get("turn_state") or {}
    initiative_order = state.get("initiative_order") or []
    current_idx = state.get("current_turn_index", 0)
    round_num = state.get("round", 1)

    lines = [
        "你是 D&D 5E 地下城主的战斗规则引擎。" if narrative else "你是 D&D 5E 骰娘，负责战斗结算。",
        f"第 {round_num} 轮。",
    ]

    if initiative_order:
        lines.append("先攻顺序：")
        for i, entry in enumerate(initiative_order):
            marker = " ← 当前" if i == current_idx else ""
            lines.append(f"  {i+1}. {entry.get('name','?')} (先攻 {entry.get('initiative','?')}){marker}")

    # Hot data
    if character and db:
        from app.tools.hot_character import hot_character_for_llm
        hot = hot_character_for_llm(db, character.id)
        if hot:
            lines.append(f"\n[当前行动角色热数据]\n{json.dumps(hot, ensure_ascii=False)}")

    # Action quotas
    if db:
        actions = get_actions_remaining(campaign)
        lines.append(f"\n当前回合剩余: {format_actions_remaining(campaign)}")

    lines.append(
        "\n战斗规则: 每个行动调用对应工具，系统自动扣配额。"
        "主动作: 攻击/施法/疾走/撤退/闪避。附赠动作: 双武器副手/灵巧施法。"
        "动作如潮用 use_feature。配额用完后提醒结束回合。用户说结束回合时调用 end_turn。"
        "信息不足时追问，不猜测。只结算当前行动者请求的动作。"
    )
    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════
#  ENTRY POINT
# ═══════════════════════════════════════════════════════════════════

def build_system_prompt(
    *,
    mode: str,
    campaign: Campaign | None = None,
    character: Character | None = None,
    message: str = "",
    db: Session | None = None,
    narrative_mode: bool = False,
    force_roleplay: bool = False,
    turn_based: bool = False,
) -> list[dict[str, Any]]:
    """Assemble system prompt messages from composable modules.

    Returns a list of {"role": "system", "content": ...} dicts.
    Each module returns "" when not applicable — those are filtered out.
    """
    modules: list[str] = []

    if turn_based:
        modules.append(_turn_based_block(campaign, character, db, narrative_mode))
    else:
        modules.append(_base_role(mode, campaign, narrative_mode, force_roleplay, turn_based=False))
        modules.append(_combat_awareness(db, campaign, mode))
        modules.append(_hot_data(db, character, message))
        modules.append(_attachment_info(campaign))
        modules.append(_combat_output(campaign, mode, narrative_mode, force_roleplay))

    return [{"role": "system", "content": m} for m in modules if m]
