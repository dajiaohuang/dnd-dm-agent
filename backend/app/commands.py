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
    "/进入DM": "enter_campaign_mode",
    "/进入战役": "enter_campaign_mode",
    "/entercampaign": "enter_campaign_mode",
    "/返回大厅": "exit_to_lobby",
    "/退出": "exit_to_lobby",
    "/lobby": "exit_to_lobby",
    "/切换战役": "switch_campaign",
    "/switchcampaign": "switch_campaign",
    "/退出骰娘": "exit_dice_assistant",
    "/exitdice": "exit_dice_assistant",
    "/combatroleplayon": "enable_combat_roleplay",
    "/combatroleplayoff": "disable_combat_roleplay",
    "/combatadviceon": "enable_combat_advice",
    "/combatadviceoff": "disable_combat_advice",
    "/绑定角色": "bind_character",
    "/绑定角色卡": "bind_character",
    "/bind": "bind_character",
    "/查看绑定": "show_bindings",
    "/我的绑定": "show_bindings",
    "/bindings": "show_bindings",
    "/创建角色": "create_character_quick",
    "/快速创建角色": "create_character_quick",
    "/createcharacter": "create_character_quick",
    "/创建NPC": "create_npc_quick",
    "/快速创建NPC": "create_npc_quick",
    "/createnpc": "create_npc_quick",
    "/保存设定": "save_campaign_setting",
    "/savesetting": "save_campaign_setting",
    "/新建战役": "create_campaign_from_prompt",
    "/newcampaign": "create_campaign_from_prompt",
    "/删除战役": "delete_active_campaign",
    "/deletecampaign": "delete_active_campaign",
    "/从设定建NPC": "create_npc_cards_from_settings",
    "/createnpcs": "create_npc_cards_from_settings",
    "/导出角色卡": "export_character_sheet",
    "/导出人物卡": "export_character_sheet",
    "/sheet": "export_character_sheet",
    "/exportcharacter": "export_character_sheet",
}

def route_command(message: str) -> Command | None:
    """Return a Command only for exact /slash matches (fast-path bypass of LLM)."""
    text = " ".join(message.strip().split())
    lowered = text.lower()
    if lowered in EXPLICIT_COMMANDS:
        return Command(EXPLICIT_COMMANDS[lowered])
    return None
