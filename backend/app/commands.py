from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Command:
    name: str


EXPLICIT_COMMANDS = {
    "/帮助": "help",
    "/help": "help",
    "/状态": "status",
    "/status": "status",
    "/保存": "save",
    "/save": "save",
    "/暂停": "pause",
    "/pause": "pause",
    "/继续": "resume",
    "/resume": "resume",
    "/回合模式": "enter_turn_mode",
    "/turns": "enter_turn_mode",
    "/退出回合模式": "exit_turn_mode",
    "/free": "exit_turn_mode",
    "/进入战斗": "start_combat",
    "/combat": "start_combat",
    "/结束战斗": "end_combat",
    "/endcombat": "end_combat",
    "/下一回合": "next_turn",
    "/next": "next_turn",
    "/编辑战役": "enter_campaign_edit",
    "/editcampaign": "enter_campaign_edit",
    "/退出编辑": "exit_campaign_edit",
    "/exitedit": "exit_campaign_edit",
    "/发布设定": "publish_settings",
    "/publishsettings": "publish_settings",
    "/放弃编辑": "discard_settings",
    "/discardsettings": "discard_settings",
    "/查看草稿": "list_setting_drafts",
    "/drafts": "list_setting_drafts",
    "/撤销修改": "undo_setting_draft",
    "/undodraft": "undo_setting_draft",
    "/检查设定": "validate_settings",
    "/骰娘": "enter_dice_assistant",
    "/diceassistant": "enter_dice_assistant",
    "/退出骰娘": "exit_dice_assistant",
    "/exitdice": "exit_dice_assistant",
    "/combatroleplayon": "enable_combat_roleplay",
    "/combatroleplayoff": "disable_combat_roleplay",
    "/combatadviceon": "enable_combat_advice",
    "/combatadviceoff": "disable_combat_advice",
    "/车卡": "start_character_build",
    "/buildcharacter": "start_character_build",
    "/取消车卡": "cancel_character_build",
    "/退出车卡": "cancel_character_build",
    "/退出车卡模式": "cancel_character_build",
    "/cancelcharacterbuild": "cancel_character_build",
    "/查看车卡": "show_character_build",
    "/characterdraft": "show_character_build",
    "/提交车卡": "submit_character_build",
    "/submitcharacter": "submit_character_build",
}

NATURAL_COMMANDS = {
    "帮助": "help",
    "查看帮助": "help",
    "战役状态": "status",
    "查看战役状态": "status",
    "当前是哪个战役": "status",
    "现在是哪个战役": "status",
    "现在在哪个战役中": "status",
    "当前在哪个战役中": "status",
    "保存战役": "save",
    "保存这个战役": "save",
    "保存当前战役": "save",
    "保存当前进度": "save",
    "保存一下": "save",
    "暂停战役": "pause",
    "先暂停一下": "pause",
    "继续战役": "resume",
    "恢复战役": "resume",
    "切换回合模式": "enter_turn_mode",
    "进入回合模式": "enter_turn_mode",
    "切换为回合制模式": "enter_turn_mode",
    "退出回合模式": "exit_turn_mode",
    "退出回合制模式": "exit_turn_mode",
    "进入战斗": "start_combat",
    "开始战斗": "start_combat",
    "结束战斗": "end_combat",
    "退出战斗": "end_combat",
    "取消战斗": "end_combat",
    "下一回合": "next_turn",
    "查看战役": "status",
    "当前战役": "status",
    "查看当前战役": "status",
    "进入战役编辑模式": "enter_campaign_edit",
    "编辑战役": "enter_campaign_edit",
    "保存现在的设定": "publish_settings",
    "退出战役编辑模式": "exit_campaign_edit",
    "退出编辑": "exit_campaign_edit",
    "发布设定": "publish_settings",
    "确认修改": "publish_settings",
    "放弃编辑": "discard_settings",
    "查看草稿": "list_setting_drafts",
    "撤销修改": "undo_setting_draft",
    "检查设定": "validate_settings",
    "进入骰娘模式": "enter_dice_assistant",
    "切换骰娘模式": "enter_dice_assistant",
    "骰娘模式": "enter_dice_assistant",
    "退出骰娘模式": "exit_dice_assistant",
    "开启战斗扮演文字": "enable_combat_roleplay",
    "关闭战斗扮演文字": "disable_combat_roleplay",
    "开启战斗建议": "enable_combat_advice",
    "关闭战斗建议": "disable_combat_advice",
    "车卡": "start_character_build",
    "开始车卡": "start_character_build",
    "我要车卡": "start_character_build",
    "创建角色": "start_character_build",
    "取消车卡": "cancel_character_build",
    "退出车卡": "cancel_character_build",
    "退出车卡模式": "cancel_character_build",
    "结束车卡": "cancel_character_build",
    "查看车卡": "show_character_build",
    "查看车卡草稿": "show_character_build",
    "提交车卡": "submit_character_build",
    "确认车卡": "submit_character_build",
    "创建新战役": "create_campaign_from_prompt",
    "新建战役": "create_campaign_from_prompt",
    "删除当前战役": "delete_active_campaign",
    "删除现在的战役": "delete_active_campaign",
    "删除这个战役": "delete_active_campaign",
    "给这些npc按照设定创建角色卡": "create_npc_cards_from_settings",
    "按设定创建npc角色卡": "create_npc_cards_from_settings",
    "创建这些npc角色卡": "create_npc_cards_from_settings",
}


def route_command(message: str) -> Command | None:
    text = " ".join(message.strip().split())
    lowered = text.lower()
    if lowered in EXPLICIT_COMMANDS:
        return Command(EXPLICIT_COMMANDS[lowered])
    if text in NATURAL_COMMANDS:
        return Command(NATURAL_COMMANDS[text])
    if (
        "设定" in text
        and any(term in lowered for term in ("npc", "怪物"))
        and any(term in text for term in ("创建角色卡", "建立角色卡", "建角色卡", "建卡"))
    ):
        return Command("create_npc_cards_from_settings")
    return None
