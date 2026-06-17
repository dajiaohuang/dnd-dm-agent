from __future__ import annotations

import copy
import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.commands import Command
from app.db.models import Campaign, CampaignCheckpoint, CampaignEvent, Character, TaskSession
from app.services import append_event, create_summary, serialize, uid
from app.campaign_turns import (
    advance_turn, end_combat, enter_turn_mode, exit_turn_mode, format_turn_state,
    start_combat, turn_notification,
)
from app.campaign_editor import discard_drafts, publish_drafts, undo_latest_draft, validate_settings
from app.db.models import CampaignSettingDraft
from app.config import settings
from app.campaign_editor import list_settings, setting_to_npc_character
from app.qq_bindings import active_napcat_campaign, set_active_napcat_campaign, sync_campaign_actor_bindings
from app.combat_preferences import combat_preference, preference_style, set_combat_preference
from app.task_sessions import active_task, create_task, owner_mentions, task_scope


DM_ONLY_COMMANDS = {
    "save", "pause", "resume", "start_combat", "end_combat", "next_turn",
    "enter_campaign_edit", "exit_campaign_edit", "publish_settings", "discard_settings",
    "undo_setting_draft", "enter_dice_assistant", "exit_dice_assistant",
    "enable_combat_roleplay", "disable_combat_roleplay",
    "enable_combat_advice", "disable_combat_advice",
    "create_campaign_from_prompt", "delete_active_campaign", "create_npc_cards_from_settings",
}


def campaign_status(campaign: Campaign) -> str:
    return str((campaign.config or {}).get("status") or "active")


def set_campaign_status(campaign: Campaign, status: str, session_id: str | None = None) -> None:
    config = copy.deepcopy(campaign.config or {})
    config["status"] = status
    if session_id:
        config["active_session_id"] = session_id
    campaign.config = config


def close_campaign_edit_tasks(db: Session, campaign: Campaign, status: str) -> None:
    for item in db.scalars(select(TaskSession).where(
        TaskSession.campaign_id == campaign.id,
        TaskSession.task_type == "campaign_edit",
        TaskSession.status.in_(("active", "waiting_user", "ready_to_commit")),
    )).all():
        item.status = status


def delete_campaign_graph(db: Session, campaign: Campaign) -> None:
    db.delete(campaign)
    db.commit()


def play_style(campaign: Campaign) -> str:
    return str((campaign.config or {}).get("play_style") or "campaign")


def _campaign_creation_context(source: Campaign) -> dict:
    source_config = source.config or {}
    new_config = {"scene": "未记录"}
    style = str(source_config.get("play_style") or "campaign")
    if style == "dice_assistant":
        new_config["play_style"] = "dice_assistant"
        dm_qq_user_id = str(source_config.get("dice_dm_qq_user_id") or "").strip()
        if dm_qq_user_id:
            new_config["dice_dm_qq_user_id"] = dm_qq_user_id
        elif source_config.get("dice_dm_confirmation_pending"):
            new_config["dice_dm_confirmation_pending"] = True
        for key in ("dice_combat_roleplay_enabled", "dice_combat_advice_enabled"):
            if key in source_config:
                new_config[key] = source_config[key]
    else:
        for key in ("campaign_combat_roleplay_enabled", "campaign_combat_advice_enabled"):
            if key in source_config:
                new_config[key] = source_config[key]
    return new_config


def append_play_event(db: Session, campaign: Campaign, *args, **kwargs):
    return append_event(db, campaign.id, *args, **kwargs)


def create_checkpoint(
    db: Session,
    campaign: Campaign,
    session_id: str | None,
    created_by: str | None,
    label: str,
) -> CampaignCheckpoint:
    summary = create_summary(db, campaign.id, session_id)
    characters = db.scalars(select(Character).where(Character.campaign_id == campaign.id)).all()
    latest_event = db.scalar(
        select(CampaignEvent)
        .where(CampaignEvent.campaign_id == campaign.id)
        .order_by(CampaignEvent.created_at.desc())
        .limit(1)
    )
    checkpoint = CampaignCheckpoint(
        id=uid("checkpoint"),
        campaign_id=campaign.id,
        session_id=session_id,
        label=label,
        created_by=created_by,
        campaign_snapshot=json.loads(json.dumps(serialize(campaign), default=str)),
        character_snapshots=json.loads(json.dumps([serialize(character) for character in characters], default=str)),
        latest_event_id=latest_event.id if latest_event else None,
        summary_id=summary.id,
    )
    db.add(checkpoint)
    config = copy.deepcopy(campaign.config or {})
    config["last_checkpoint_id"] = checkpoint.id
    campaign.config = config
    db.commit()
    return checkpoint


def execute_command(
    db: Session,
    command: Command,
    campaign: Campaign,
    session_id: str | None,
    actor_id: str | None,
    is_dm: bool,
    message_context: dict | None = None,
) -> dict:
    if command.name in DM_ONLY_COMMANDS and command.name != "enter_dice_assistant" and not is_dm:
        return command_result(command.name, "该命令仅限 DM 使用。", ok=False)
    campaign_only = {
        "enter_campaign_edit", "exit_campaign_edit", "publish_settings", "discard_settings",
        "list_setting_drafts", "undo_setting_draft", "validate_settings",
    }
    if play_style(campaign) == "dice_assistant" and command.name in campaign_only:
        return command_result(
            command.name,
            "骰娘模式不管理预设战役剧情或设定编辑，但仍会写入操作审计与记忆。"
            "请先使用 /退出骰娘 返回战役叙事模式后再执行此命令。",
            ok=False,
        )

    if command.name == "help":
        return command_result("help", (
            "可用命令：\n"
            "/帮助 - 查看命令\n"
            "/状态 - 查看战役状态\n"
            "/保存 - 创建战役检查点（DM）\n"
            "/暂停 - 保存并暂停战役（DM）\n"
            "/继续 - 继续已暂停战役（DM）\n"
            "/回合模式 - 进入非战斗回合制\n"
            "/退出回合模式 - 退出非战斗回合制\n"
            "/进入战斗 - 投掷全体先攻并进入战斗（DM）\n"
            "/结束战斗 - 结束战斗并返回自由模式（DM）\n"
            "/下一回合 - 跳过当前行动者（DM）\n"
            "/编辑战役 - 进入战役设定编辑模式（DM）\n"
            "/发布设定 - 发布全部待处理设定草稿（DM）\n"
            "/放弃编辑 - 放弃草稿并退出编辑模式（DM）\n"
            "/查看草稿 - 查看当前待发布草稿\n"
            "/撤销修改 - 撤销最近草稿（DM）\n"
            "/检查设定 - 检查悬空引用与冲突\n"
            "/骰娘 - 进入纯角色卡、检定与战斗计算辅助模式（DM）\n"
            "/退出骰娘 - 返回战役叙事模式（DM）\n"
            "/combatroleplayon|off - 开关当前玩法的战斗扮演文字（DM 模式默认开，骰娘默认关）\n"
            "/combatadviceon|off - 开关当前玩法的战斗行动建议（DM 模式默认开，骰娘默认关）\n"
            "/导出角色卡 - 导出绑定的角色卡为 XLSX 文件\n"
            "/法术 法术名 - 直接查询合并法术表"
        ))

    if command.name == "status":
        config = campaign.config or {}
        checkpoint = config.get("last_checkpoint_id") or "无"
        return command_result("status", (
            f"战役：{campaign.name}\n"
            f"简介：{campaign.description or '无'}\n"
            f"当前场景：{config.get('scene') or '未记录'}\n"
            f"状态：{campaign_status(campaign)}\n"
            f"当前会话：{config.get('active_session_id') or session_id or '无'}\n"
            f"最近检查点：{checkpoint}\n"
            f"玩法：{'骰娘辅助' if play_style(campaign) == 'dice_assistant' else '战役叙事'}\n"
            f"战斗扮演文字：{'开启' if combat_preference(campaign, 'roleplay') else '关闭'}\n"
            f"战斗建议：{'开启' if combat_preference(campaign, 'advice') else '关闭'}\n"
            f"{format_turn_state(campaign)}"
        ), data={"status": campaign_status(campaign), "play_style": play_style(campaign),
                 "last_checkpoint_id": config.get("last_checkpoint_id"),
                 "combat_roleplay_enabled": combat_preference(campaign, "roleplay"),
                 "combat_advice_enabled": combat_preference(campaign, "advice"),
                 "combat_preference_style": preference_style(campaign)})

    if command.name == "create_campaign_from_prompt":
        if campaign:
            config = copy.deepcopy(campaign.config or {})
            name = str(config.get("pending_generated_campaign_name") or "").strip() or f"{campaign.name}·新章"
            desc = str(config.get("pending_generated_campaign_description") or campaign.description or "").strip()
            new_config = _campaign_creation_context(campaign)
            system_ver = campaign.system_version
        else:
            name = "新战役"
            desc = ""
            new_config = {"scene": "新场景", "play_style": "lobby"}
            system_ver = "DND_5E_2014"
        new_campaign = Campaign(id=uid("camp"), name=name, system_version=system_ver,
                                description=desc, config=new_config)
        db.add(new_campaign)
        db.commit()
        set_active_napcat_campaign(db, new_campaign)
        if str(new_config.get("play_style") or "") == "dice_assistant":
            sync_campaign_actor_bindings(
                db,
                new_campaign,
                str(new_config.get("dice_dm_qq_user_id") or "").strip() or None,
            )
        return command_result(
            "create_campaign_from_prompt",
            f"已创建并切换到新战役“{new_campaign.name}”（{new_campaign.id}）。",
            data={"campaign": serialize(new_campaign)},
        )

    if command.name == "delete_active_campaign":
        target = active_napcat_campaign(db) or campaign
        deleted_name, deleted_id = target.name, target.id
        delete_campaign_graph(db, target)
        fallback = db.scalar(select(Campaign).order_by(Campaign.updated_at.desc()))
        if fallback:
            set_active_napcat_campaign(db, fallback)
            narration = (
                f"已删除当前战役“{deleted_name}”（{deleted_id}）及其所有关联数据。"
                f"当前已切换到战役“{fallback.name}”（{fallback.id}）。"
            )
        else:
            narration = (
                f"已删除当前战役“{deleted_name}”（{deleted_id}）及其所有关联数据。"
                "当前无活跃战役和角色数据。"
            )
        return command_result("delete_active_campaign", narration)

    if command.name == "create_npc_cards_from_settings":
        created = []
        for item in list_settings(db, campaign.id):
            if item.category not in {"npc", "monster"}:
                continue
            character = setting_to_npc_character(db, item)
            created.append(character)
        dm_qq_user_id = str((campaign.config or {}).get("dice_dm_qq_user_id") or "").strip()
        managed = []
        if created and dm_qq_user_id:
            managed = sync_campaign_actor_bindings(db, campaign, dm_qq_user_id)
        if not created:
            return command_result(
                "create_npc_cards_from_settings",
                "当前战役里没有可用于建卡的已发布 NPC/怪物设定。",
                ok=False,
            )
        return command_result(
            "create_npc_cards_from_settings",
            f"已按设定创建/同步 {len(created)} 张 NPC/怪物角色卡。"
            + (f" 已同步 DM 绑定：{len(managed)}。" if managed else ""),
            data={"characters": [serialize(item) for item in created], "dm_actor_ids": [item.id for item in managed]},
        )

    behavior_commands = {
        "enable_combat_roleplay": ("roleplay", True, "战斗扮演文字已开启。"),
        "disable_combat_roleplay": ("roleplay", False, "战斗扮演文字已关闭。"),
        "enable_combat_advice": ("advice", True, "战斗建议已开启。"),
        "disable_combat_advice": ("advice", False, "战斗建议已关闭。"),
    }
    if command.name in behavior_commands:
        option, enabled, narration = behavior_commands[command.name]
        key = set_combat_preference(db, campaign, option, enabled)
        return command_result(command.name, narration, data={
            "combat_preference_style": preference_style(campaign),
            f"combat_{option}_enabled": enabled,
            "config_key": key,
        })

    if command.name == "enter_dice_assistant":
        config = copy.deepcopy(campaign.config or {})
        config["play_style"] = "dice_assistant"
        if config.get("runtime_mode") == "campaign_edit":
            config["runtime_mode"] = "free"
        configured = [item.strip() for item in settings.napcat_dm_user_ids.split(",") if item.strip()]
        dm_qq_user_id = str(config.get("dice_dm_qq_user_id") or "").strip()
        if not dm_qq_user_id and len(configured) == 1:
            dm_qq_user_id = configured[0]
        if dm_qq_user_id:
            config["dice_dm_qq_user_id"] = dm_qq_user_id
            config.pop("dice_dm_confirmation_pending", None)
        else:
            config["dice_dm_confirmation_pending"] = True
        campaign.config = config
        db.commit()
        bound = sync_campaign_actor_bindings(db, campaign, dm_qq_user_id or None)
        if not dm_qq_user_id:
            return command_result(
                "enter_dice_assistant",
                "已进入骰娘模式。谁是 DM？请回复“DM是 QQ号”或 @ DM。",
                data={"dm_confirmation_pending": True},
            )
        return command_result("enter_dice_assistant", (
            f"骰娘模式已开启。DM QQ：{dm_qq_user_id}；已关联 NPC/怪物：{len(bound)}。"
        ), data={"dm_qq_user_id": dm_qq_user_id, "dm_actor_ids": [item.id for item in bound]})

    if command.name == "exit_dice_assistant":
        config = copy.deepcopy(campaign.config or {})
        config["play_style"] = "campaign"
        config.pop("dice_dm_confirmation_pending", None)
        campaign.config = config
        db.commit()
        synced = sync_campaign_actor_bindings(db, campaign, str(config.get("dice_dm_qq_user_id") or "").strip() or None)
        return command_result(
            "exit_dice_assistant",
            f"骰娘模式已关闭；已同步当前战役的 NPC/怪物控制绑定：{len(synced)}。",
        )

    if command.name == "enter_turn_mode":
        state = enter_turn_mode(db, campaign)
        notification = turn_notification(db, campaign)
        append_play_event(db, campaign, session_id, "turn_mode_entered", "进入回合模式", [], {"turn_state": state})
        return command_result(
            "enter_turn_mode",
            f"已进入回合制模式。\n{format_turn_state(campaign)}",
            data={"turn_state": state, "turn_notification": notification},
        )

    if command.name == "exit_turn_mode":
        if not exit_turn_mode(db, campaign):
            return command_result(
                "exit_turn_mode",
                "战斗进行中，不能退出回合模式。请由 DM 使用 /结束战斗。",
                ok=False,
            )
        append_play_event(db, campaign, session_id, "turn_mode_exited", "退出回合模式", [], {})
        return command_result("exit_turn_mode", "已退出回合制模式，返回自由扮演模式。")

    if command.name == "start_combat":
        state = start_combat(db, campaign)
        if not state["participants"]:
            return command_result("start_combat", "战役中没有可加入战斗的角色。", ok=False)
        notification = turn_notification(db, campaign)
        order = "\n".join(
            f"{index + 1}. {item['name']}（{item['actor_type']}）：{item['initiative']['total']}"
            for index, item in enumerate(state["participants"])
        )
        append_play_event(db, campaign, session_id, "combat_started", "进入战斗", [], {
            "initiative_order": state["participants"],
        })
        return command_result(
            "start_combat",
            f"战斗开始，系统已为所有玩家角色与 NPC 投掷先攻：\n{order}\n\n{format_turn_state(campaign)}",
            data={"turn_state": state, "turn_notification": notification},
        )

    if command.name == "end_combat":
        end_combat(db, campaign)
        append_play_event(db, campaign, session_id, "combat_ended", "结束战斗", [], {})
        return command_result("end_combat", "战斗结束，已自动退出回合制模式并返回自由扮演模式。")

    if command.name == "next_turn":
        next_actor = advance_turn(db, campaign)
        notification = turn_notification(db, campaign)
        append_play_event(db, campaign, session_id, "turn_advanced", "下一回合", [], {
            "next_actor": next_actor,
        })
        return command_result(
            "next_turn",
            f"已推进回合。\n{format_turn_state(campaign)}",
            data={"turn_notification": notification},
        )

    if command.name == "enter_campaign_edit":
        platform, chat_id, owner_user_id, scoped_session = task_scope(
            message_context or {"platform": "web"},
            actor_id,
            session_id,
        )
        existing = active_task(db, campaign, "campaign_edit", platform, owner_user_id, scoped_session)
        if not existing:
            create_task(
                db,
                campaign,
                "campaign_edit",
                platform,
                chat_id,
                owner_user_id,
                scoped_session,
                status="active",
                draft_data={"mode": "campaign_edit"},
                next_prompt="请描述你要创建或修改的战役设定。",
                mentions=owner_mentions(owner_user_id, "已进入战役编辑模式。"),
            )
        config = copy.deepcopy(campaign.config or {})
        config["runtime_mode"] = "campaign_edit"
        config["editor_session_id"] = session_id
        campaign.config = config
        db.commit()
        return command_result("enter_campaign_edit", "已进入战役编辑模式。讨论不会推进剧情，修改会先形成草稿。")

    if command.name == "exit_campaign_edit":
        close_campaign_edit_tasks(db, campaign, "closed")
        config = copy.deepcopy(campaign.config or {})
        config["runtime_mode"] = "free"
        config.pop("editor_session_id", None)
        campaign.config = config
        db.commit()
        return command_result("exit_campaign_edit", "已退出战役编辑模式，未发布草稿仍会保留。")

    if command.name == "publish_settings":
        published = publish_drafts(db, campaign.id, actor_id)
        close_campaign_edit_tasks(db, campaign, "committed")
        db.commit()
        return command_result("publish_settings", f"已发布 {len(published)} 条战役设定。",
                              data={"settings": [serialize(item) for item in published]})

    if command.name == "discard_settings":
        count = discard_drafts(db, campaign.id)
        close_campaign_edit_tasks(db, campaign, "cancelled")
        config = copy.deepcopy(campaign.config or {})
        config["runtime_mode"] = "free"
        config.pop("editor_session_id", None)
        campaign.config = config
        db.commit()
        return command_result("discard_settings", f"已放弃 {count} 条草稿并退出编辑模式。")

    if command.name == "list_setting_drafts":
        drafts = db.scalars(select(CampaignSettingDraft).where(
            CampaignSettingDraft.campaign_id == campaign.id,
            CampaignSettingDraft.status == "pending",
        ).order_by(CampaignSettingDraft.created_at)).all()
        lines = [f"- {item.operation}: {item.name or item.target_setting_id}" for item in drafts]
        return command_result("list_setting_drafts", "\n".join(lines) or "当前没有待发布草稿。",
                              data={"drafts": [serialize(item) for item in drafts]})

    if command.name == "undo_setting_draft":
        draft = undo_latest_draft(db, campaign.id)
        return command_result("undo_setting_draft", "已撤销最近草稿。" if draft else "当前没有可撤销草稿。",
                              ok=bool(draft), data={"draft": serialize(draft) if draft else None})

    if command.name == "validate_settings":
        result = validate_settings(db, campaign.id)
        return command_result("validate_settings", json.dumps(result, ensure_ascii=False, indent=2), data=result)

    if command.name == "save":
        checkpoint = create_checkpoint(db, campaign, session_id, actor_id, "manual_save")
        append_event(db, campaign.id, session_id, "campaign_saved", "保存战役", [], {
            "checkpoint_id": checkpoint.id, "created_by": actor_id,
        })
        return command_result("save", f"战役已保存。检查点：{checkpoint.id}", data=serialize(checkpoint))

    if command.name == "pause":
        if campaign_status(campaign) == "paused":
            return command_result("pause", "战役已经处于暂停状态。")
        checkpoint = create_checkpoint(db, campaign, session_id, actor_id, "pause")
        set_campaign_status(campaign, "paused", session_id)
        db.commit()
        append_event(db, campaign.id, session_id, "campaign_paused", "暂停战役", [], {
            "checkpoint_id": checkpoint.id, "created_by": actor_id,
        })
        return command_result("pause", f"战役已暂停并保存。检查点：{checkpoint.id}", data=serialize(checkpoint))

    if command.name == "resume":
        if campaign_status(campaign) == "active":
            return command_result("resume", "战役已经处于进行状态。")
        set_campaign_status(campaign, "active", session_id)
        db.commit()
        append_event(db, campaign.id, session_id, "campaign_resumed", "继续战役", [], {
            "created_by": actor_id,
        })
        return command_result("resume", f"战役“{campaign.name}”已继续。")

    if command.name == "show_bindings":
        return command_result(
            "show_bindings",
            "请在 QQ 中使用 /查看绑定 命令来查看绑定的角色卡，或在 Web 端 /napcat/bindings 查看。",
            data={"tip": "use_qq_command"},
        )

    if command.name == "bind_character":
        return command_result(
            "bind_character",
            "请在 QQ 中使用 /绑定角色 命令来绑定角色卡，或在 Web 端角色卡设置中配置 QQ 绑定。",
            data={"tip": "use_qq_command"},
        )

    if command.name == "enter_campaign_mode":
        config = copy.deepcopy(campaign.config or {})
        config["play_style"] = "campaign"
        campaign.config = config; db.commit()
        return command_result("enter_campaign_mode",
            f"已进入 DM 模式。当前战役: {campaign.name}。")

    if command.name == "exit_to_lobby":
        config = copy.deepcopy(campaign.config or {})
        config["play_style"] = "lobby"
        campaign.config = config; db.commit()
        return command_result("exit_to_lobby",
            f"已返回大厅。当前战役: {campaign.name}。")

    if command.name == "switch_campaign":
        return command_result("switch_campaign",
            "请说「切换到 战役名」来选择战役。")

    if command.name == "export_character_sheet":
        return command_result(
            "export_character_sheet",
            "请在 QQ 中使用 /导出角色卡 命令来下载角色卡 XLSX 文件，或使用 GET /characters/{character_id}/sheet 接口。",
            data={"tip": "use_qq_command_or_api"},
        )

    return command_result(command.name, "未知命令。", ok=False)


def command_result(name: str, narration: str, ok: bool = True, data: dict | None = None) -> dict:
    return {
        "ok": ok,
        "kind": "command",
        "command": name,
        "narration": narration,
        "data": data or {},
        "rolls": [],
        "state_changes": [],
        "events": [],
    }

