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
}

NATURAL_COMMANDS = {
    "帮助": "help",
    "查看帮助": "help",
    "战役状态": "status",
    "查看战役状态": "status",
    "保存战役": "save",
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
    "下一回合": "next_turn",
    "进入战役编辑模式": "enter_campaign_edit",
    "编辑战役": "enter_campaign_edit",
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
}


def route_command(message: str) -> Command | None:
    text = " ".join(message.strip().split())
    lowered = text.lower()
    if lowered in EXPLICIT_COMMANDS:
        return Command(EXPLICIT_COMMANDS[lowered])
    if text in NATURAL_COMMANDS:
        return Command(NATURAL_COMMANDS[text])
    return None
