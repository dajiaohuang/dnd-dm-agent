from contextlib import asynccontextmanager
import copy
import logging
import shutil
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.db.database import Base, SessionLocal, engine, get_db
from app.db.models import (Campaign, CampaignCheckpoint, CampaignEntity, CampaignEvent, CampaignMemory,
                           CampaignSetting, CampaignSettingComment, CampaignSettingDraft, CampaignSettingHistory, CampaignSummary,
                           CampaignThread, Character, CharacterChange, CompendiumEntry, NapCatCharacterBinding,
                           TaskSession)
from app.character_build_flow import _parse_fields
from app.schemas import (CampaignCreate, CampaignPatch, CharacterCreate, CharacterPatch,
                         CharacterBuildRequest, ChatRequest, DiceRequest, EventCreate,
                         NapCatBindingUpsert, SettingDraftCreate, CampaignPackageImport, SettingCommentCreate,
                         ActorRoleplayPatch, ActorPresencePatch, CharacterQQBindingsPatch,
                         TaskSessionCreate, TaskSessionPatch)
from app.services import (append_event, create_summary, ingest_compendium, ingest_rules,
                          search_rules, serialize, uid)
from app.tools.dice import roll_dice
from app.config import settings
from app.integrations.napcat import (NapCatAdapter, NapCatClient, callback_token_valid, download_attachments,
                                     is_allowed, is_group_at_event, is_supported_message,
                                     message_text, parse_event_text, replied_message_id)
from app.message_router import process_message
from app.platform_adapter import handle_platform_message
from app.commands import route_command
from app.parsing.api import router as parsing_router
from app.parsing.router import parse_files
from app.tools.character_builder import build_character_data, export_character_sheet
from app.tools.character_rules import (
    ABILITY_KEYS, CLASS_HIT_DICE, CLASS_SAVING_THROWS, POINT_BUY_COSTS, SKILL_ABILITIES,
)
from app.tools.spell_catalog import load_spell_catalog, search_spells
from app.tools.item_schema import item_schema_catalog, normalize_character_inventory
from app.tools.effect_engine import ActiveEffect, resolve_effective_character
from app.campaign_memory import backfill_campaign_memory, search_campaign_memory
from app.campaign_editor import (
    TEMPLATES, apply_template, conflict_suggestions, create_draft, export_campaign_package,
    discard_drafts, import_campaign_package, list_settings, publish_drafts, search_settings, setting_graph,
    setting_timeline, setting_to_npc_character, undo_latest_draft, validate_settings,
)
from app.actor_manager import list_actors, roleplay_brief, set_presence, update_roleplay
from app.qq_bindings import (
    active_napcat_campaign, active_napcat_campaign_id, backfill_character_binding_mirrors,
    bind_qq, delete_character_and_bindings, find_binding, find_bindings,
    migrate_binding_schema_for_multiple_characters,
    set_active_napcat_campaign, sync_campaign_actor_bindings, sync_character_bindings, unbind_qq,
)
from app.task_sessions import ACTIVE_STATUSES


@asynccontextmanager
async def lifespan(_: FastAPI):
    Base.metadata.create_all(bind=engine)
    migrate_binding_schema_for_multiple_characters()
    with SessionLocal() as db:
        backfill_character_binding_mirrors(db)
        for campaign in db.scalars(select(Campaign)).all():
            config = campaign.config or {}
            if config.get("dice_dm_qq_user_id"):
                sync_campaign_actor_bindings(db, campaign, str(config["dice_dm_qq_user_id"]))
    if engine.dialect.name == "postgresql":
        with engine.begin() as connection:
            connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
            dimension = settings.embedding_dim
            connection.execute(text(f"""
                DO $$
                BEGIN
                  IF EXISTS (
                    SELECT 1 FROM pg_attribute
                    WHERE attrelid = 'rule_chunks'::regclass
                      AND attname = 'embedding_vector'
                      AND NOT attisdropped
                      AND format_type(atttypid, atttypmod) <> 'vector({dimension})'
                  ) THEN
                    ALTER TABLE rule_chunks DROP COLUMN embedding_vector CASCADE;
                  END IF;
                END $$;
            """))
            connection.execute(text(
                f"ALTER TABLE rule_chunks ADD COLUMN IF NOT EXISTS embedding_vector vector({dimension})"
            ))
            connection.execute(text("""CREATE INDEX IF NOT EXISTS idx_rule_chunks_embedding_vector
                                    ON rule_chunks USING ivfflat (embedding_vector vector_cosine_ops)
                                    WITH (lists = 100)"""))
    yield


app = FastAPI(title="Local DND DM Agent", version="1.0.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
app.include_router(parsing_router)

_log = logging.getLogger(__name__)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/integrations/status")
def integrations_status():
    return {
        "napcat_configured": bool(settings.napcat_base_url),
        "napcat_campaign_id": settings.napcat_campaign_id,
        "napcat_access": {
            "allow_all_users": not bool(settings.napcat_allowed_user_ids.strip()),
            "require_group_at": settings.napcat_require_group_at,
            "dm_control_configured": bool(settings.napcat_dm_user_ids.strip()),
        },
        "parsing": {
            "base_formats": ["text", "json", "csv", "html", "docx", "pptx", "pdf", "zip"],
            "optional_backends": ["paddleocr", "pdf_ocr", "whisper", "markitdown"],
        },
    }


@app.post("/napcat/callback")
async def napcat_callback(
    request: Request,
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
):
    if not callback_token_valid(authorization):
        raise HTTPException(401, "Invalid NapCat callback token")
    client = NapCatClient.from_settings()
    if not client:
        raise HTTPException(503, "NapCat is not configured")
    adapter = NapCatAdapter(client)
    payload = await request.json()
    if not is_supported_message(payload):
        return {"ok": True, "ignored": "unsupported_event"}
    if not is_allowed(payload):
        return {"ok": True, "ignored": "user_not_allowed"}

    # ── 骰子被动监听：纯骰子公式无需 @ 也能触发 ──
    dice_passive = _handle_dice_passive(payload, client)
    if dice_passive:
        return dice_passive

    if (payload.get("message_type") == "group" and settings.napcat_require_group_at
            and not is_group_at_event(payload, client.self_id)):
        return {"ok": True, "ignored": "group_message_without_at"}

    text = parse_event_text(payload, client.self_id)
    reply_text = ""
    reply_id = replied_message_id(payload)
    if reply_id:
        try:
            reply_text = message_text(client.get_message(reply_id), client.self_id)
        except Exception:
            reply_text = ""
    temp_root, paths, attachment_errors = download_attachments(client, payload)
    try:
        try:
            parsed = parse_files(paths, per_file_max_chars=4000, total_max_chars=12000) if paths else None
        except Exception as exc:
            parsed = None
            attachment_errors.append(f"parse_error: {exc}")
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)

    if not text and not parsed:
        return {"ok": True, "ignored": "empty_message", "attachment_errors": attachment_errors}

    campaign = adapter.default_campaign(db)
    if not campaign:
        # Direct to lobby mode — no campaign needed
        from app.services import resolve_chat
        result = resolve_chat(db, None, None, None, text, mode="lobby")
        return {"reply": result.get("narration", "大厅模式已就绪。"), "auto_escape": False, "at_sender": False}

    # ── Send pending export files (from plan runner or background tasks) ──
    _pending = (campaign.config or {}).get("pending_exports") or []
    for _exp in _pending[:]:
        _exp_path = _exp.get("file_path", "")
        _exp_name = _exp.get("name", "character.xlsx")
        if _exp_path and Path(_exp_path).exists():
            try:
                if payload.get("message_type") == "group":
                    client.upload_group_file(group_id, _exp_path, _exp_name)
                    client.send_group_at(group_id, user_id, f"角色卡 {_exp_name} 已导出。")
                else:
                    client.upload_private_file(user_id, _exp_path, _exp_name)
            except Exception:
                pass
    # Clear pending exports
    if _pending:
        _cfg = copy.deepcopy(campaign.config or {})
        _cfg.pop("pending_exports", None)
        campaign.config = _cfg; db.commit()

    # ── 持久化最近附件 + 注入上下文 ──
    attachment_context = (parsed or {}).get("content", "")
    if attachment_context:
        text = f"{text}\n\n玩家同时发送了以下附件内容：\n{attachment_context}".strip()
    elif parsed and not text:
        # File-only message: store and prompt
        _stored = {"content": ((parsed or {}).get("content") or "")[:8000],
                    "meta": (parsed or {}).get("meta") or {},
                    "parser": (parsed or {}).get("parser") or ""}
        cfg = copy.deepcopy(campaign.config or {})
        recent = list(cfg.get("last_attachments") or [])
        recent.insert(0, _stored)
        cfg["last_attachments"] = recent[:5]
        campaign.config = cfg; db.commit()
        return {"reply": "文件已收到。请告诉我你想用它做什么？例如：用这个文件的人物卡开卡。", "auto_escape": False, "at_sender": False}
    if parsed:
        _stored = {"content": attachment_context[:8000],
                    "meta": (parsed or {}).get("meta") or {},
                    "parser": (parsed or {}).get("parser") or ""}
        cfg = copy.deepcopy(campaign.config or {})
        recent = list(cfg.get("last_attachments") or [])
        recent.insert(0, _stored)
        cfg["last_attachments"] = recent[:5]
        campaign.config = cfg; db.commit()

    group_id = str(payload.get("group_id", "")).strip()
    group_history: list[dict] = []
    group_history_error = ""
    confirmation_words = {"是", "要", "好", "好的", "可以", "yes", "y", "读取", "讀取"}
    if (group_id and (campaign.config or {}).get("play_style") == "dice_assistant"
            and any(word in confirmation_words for word in text.casefold().split())):
        try:
            group_history = [
                {
                    "message_id": item.get("message_id"),
                    "sender_id": str(item.get("user_id") or (item.get("sender") or {}).get("user_id") or ""),
                    "text": message_text(item, client.self_id),
                    "time": item.get("time"),
                }
                for item in client.get_group_history(group_id, 20)
                if str(item.get("message_id") or "") != str(payload.get("message_id") or "")
            ]
        except Exception as exc:
            group_history_error = str(exc)

    command = route_command(text)

    # ═══════════════════════════════════════════
    # ── /创建角色 名称:xxx 职业:xxx 等级:x ──
    # ═══════════════════════════════════════════
    if command and command.name == "create_character_quick":
        user_id = str(payload.get("user_id", "")).strip()
        parsed_fields = _parse_fields(text)
        char_name = parsed_fields.get("character_name", "").strip()
        class_name = parsed_fields.get("class_name", "").strip()
        if not char_name or not class_name:
            response = {
                "reply": "格式：/创建角色 名称:xxx 职业:xxx [等级:x] [种族:xxx] [背景:xxx]\n示例：/创建角色 名称:卡利恩 职业:法师 等级:3 种族:人类",
                "auto_escape": False, "at_sender": False,
            }
            response["parsed_attachments"] = parsed
            response["attachment_errors"] = attachment_errors
            return response
        raw: dict[str, Any] = {
            "character_name": char_name, "class_name": class_name, "player_name": user_id,
            "actor_type": "player",
            "level": parsed_fields.get("level", 1),
            "ancestry": parsed_fields.get("ancestry", "人类"),
            "background": parsed_fields.get("background", ""),
            "alignment": parsed_fields.get("alignment", ""),
            "abilities": parsed_fields.get("abilities", {}),
        }
        char_data = build_character_data(raw)
        character = Character(
            id=uid("char"), campaign_id=campaign.id,
            player_name=user_id, character_name=char_name, data=char_data,
        )
        db.add(character)
        if user_id.isdigit():
            bind_qq(db, campaign.id, user_id, character, char_name)
        db.commit()
        response = {
            "reply": f"角色卡已创建：{char_name}（{character.id}）\n职业：{class_name} 等级：{raw['level']}\n已自动绑定到你的QQ。发送 /导出角色卡 下载。",
            "auto_escape": False, "at_sender": False,
        }
        response["parsed_attachments"] = parsed
        response["attachment_errors"] = attachment_errors
        return response

    # ═══════════════════════════════════════════
    # ── /创建NPC 名称:xxx 职业:xxx ──
    # ═══════════════════════════════════════════
    if command and command.name == "create_npc_quick":
        parsed_fields = _parse_fields(text)
        npc_name = parsed_fields.get("character_name", "").strip()
        class_name = parsed_fields.get("class_name", "").strip()
        if not npc_name or not class_name:
            response = {
                "reply": "格式：/创建NPC 名称:xxx 职业:xxx [等级:x] [种族:xxx] [属性:str=10,dex=12]\n示例：/创建NPC 名称:卫兵队长 职业:战士 等级:5 种族:人类",
                "auto_escape": False, "at_sender": False,
            }
            response["parsed_attachments"] = parsed
            response["attachment_errors"] = attachment_errors
            return response
        raw: dict[str, Any] = {
            "character_name": npc_name, "class_name": class_name, "player_name": "DM",
            "actor_type": "npc",
            "level": parsed_fields.get("level", 1),
            "ancestry": parsed_fields.get("ancestry", "人类"),
            "alignment": parsed_fields.get("alignment", ""),
            "abilities": parsed_fields.get("abilities", {}),
        }
        char_data = build_character_data(raw)
        npc = Character(
            id=uid("char"), campaign_id=campaign.id,
            player_name="DM", character_name=npc_name, data=char_data,
        )
        db.add(npc)
        db.commit()
        response = {
            "reply": f"NPC 角色卡已创建：{npc_name}（{npc.id}）\n职业：{class_name} 等级：{raw['level']}\n发送 /导出角色卡 可下载 xlsx。",
            "auto_escape": False, "at_sender": False,
        }
        response["parsed_attachments"] = parsed
        response["attachment_errors"] = attachment_errors
        return response

    # ═══════════════════════════════════════════
    # ── /保存设定 NPC/地点/组织 名称 描述 ──
    # ═══════════════════════════════════════════
    if command and command.name == "save_campaign_setting":
        parts = text.split(maxsplit=3)
        if len(parts) < 3:
            response = {
                "reply": "格式：/保存设定 类型 名称 描述\n类型：NPC / 地点 / 组织 / 物品 / 事件\n示例：/保存设定 NPC 玛莎·灰烬 古雾镇的面包师，善良阵营",
                "auto_escape": False, "at_sender": False,
            }
            response["parsed_attachments"] = parsed
            response["attachment_errors"] = attachment_errors
            return response
        cat_map = {"npc": "npc", "地点": "location", "组织": "faction", "物品": "item", "事件": "event"}
        cat_input = parts[1].strip()
        category = cat_map.get(cat_input, cat_input)
        setting_name = parts[2].strip()
        description = parts[3].strip() if len(parts) > 3 else ""
        setting = CampaignSetting(
            id=uid("setting"), campaign_id=campaign.id,
            category=category, name=setting_name,
            summary=description[:200],
            content={"description": description},
            status="published", version=1,
        )
        db.add(setting)
        db.commit()
        response = {
            "reply": f"设定已保存：[{category}] {setting_name}（{setting.id}）\n当前战役共有 {db.query(CampaignSetting).filter(CampaignSetting.campaign_id == campaign.id).count()} 条设定。",
            "auto_escape": False, "at_sender": False,
        }
        response["parsed_attachments"] = parsed
        response["attachment_errors"] = attachment_errors
        return response

    # ── 角色绑定（QQ 绑定到已有角色） ──
    if command and command.name == "bind_character":
        user_id = str(payload.get("user_id", "")).strip()
        print(f"[bind] user_id={user_id} campaign={campaign.id}", flush=True)
        # 从 integrations.qq_user_ids 查找该用户已关联的角色
        all_campaign_chars = db.scalars(
            select(Character).where(Character.campaign_id == campaign.id)
        ).all()
        matched: list[Character] = []
        for ch in all_campaign_chars:
            qq_ids = [str(q).strip() for q in ((ch.data.get("integrations") or {}).get("qq_user_ids") or [])]
            if user_id in qq_ids:
                matched.append(ch)
        if not matched:
            response = {
                "reply": (
                    "在当前战役中没有找到你的角色卡。\n"
                    "请先使用 /车卡 创建角色，创建完成后会自动绑定。\n"
                    "如果你已经在 Web 端创建了角色，请在角色卡的 QQ 绑定设置中添加你的 QQ 号。"
                ),
                "auto_escape": False, "at_sender": False,
            }
            response["parsed_attachments"] = parsed
            response["attachment_errors"] = attachment_errors
            return response
        # 创建正式绑定
        bound_names: list[str] = []
        for ch in matched:
            existing = find_binding(db, campaign.id, user_id, ch.id)
            if existing:
                bound_names.append(ch.character_name or ch.id)
                continue
            binding = NapCatCharacterBinding(
                id=uid("ncb"), campaign_id=campaign.id,
                qq_user_id=user_id, character_id=ch.id,
                display_name=ch.character_name or ch.id,
            )
            db.add(binding)
            bound_names.append(ch.character_name or ch.id)
        db.commit()
        response = {
            "reply": f"已绑定角色卡：{'、'.join(bound_names)}。现在可以使用 /导出角色卡 下载了。",
            "auto_escape": False, "at_sender": False,
        }
        response["parsed_attachments"] = parsed
        response["attachment_errors"] = attachment_errors
        return response

    # ── 查看绑定（返回真实数据库绑定信息） ──
    if command and command.name == "show_bindings":
        user_id = str(payload.get("user_id", "")).strip()
        bindings = find_bindings(db, campaign.id, user_id)
        # 同时检查 integrations.qq_user_ids
        all_campaign_chars = db.scalars(
            select(Character).where(Character.campaign_id == campaign.id)
        ).all()
        unbound: list[str] = []
        for ch in all_campaign_chars:
            qq_ids = [str(q).strip() for q in ((ch.data.get("integrations") or {}).get("qq_user_ids") or [])]
            if user_id in qq_ids and not any(b.character_id == ch.id for b in bindings):
                unbound.append(ch.character_name or ch.id)
        if not bindings and not unbound:
            response = {
                "reply": (
                    "你当前没有任何角色绑定。\n"
                    "绑定方式：\n"
                    "1. QQ 中发送 /车卡 创建角色，完成后自动绑定\n"
                    "2. Web 端角色卡设置中添加 QQ 号后，发送 /绑定角色"
                ),
                "auto_escape": False, "at_sender": False,
            }
        else:
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
                    lines.append(f"  - {name}  → 发送 /绑定角色 完成绑定")
            lines.append("发送 /导出角色卡 下载角色卡")
            response = {
                "reply": "\n".join(lines),
                "auto_escape": False, "at_sender": False,
            }
        response["parsed_attachments"] = parsed
        response["attachment_errors"] = attachment_errors
        return response

    # ── 角色卡导出（NapCat QQ 文件上传） ──
    _log.info("napcat_callback text=%r command=%s", text, command.name if command else None)
    if command and command.name == "export_character_sheet":
        user_id = str(payload.get("user_id", "")).strip()
        print(f"[export] user_id={user_id} campaign={campaign.id}", flush=True)
        bindings = find_bindings(db, campaign.id, user_id)
        print(f"[export] bindings count={len(bindings)}", flush=True)
        # 无绑定时，尝试从 integrations.qq_user_ids 查找角色
        if not bindings:
            all_campaign_chars = db.scalars(
                select(Character).where(Character.campaign_id == campaign.id)
            ).all()
            for ch in all_campaign_chars:
                qq_ids = (ch.data.get("integrations") or {}).get("qq_user_ids") or []
                if user_id in [str(q).strip() for q in qq_ids]:
                    bindings.append(NapCatCharacterBinding(
                        id=uid("ncb"), campaign_id=campaign.id,
                        qq_user_id=user_id, character_id=ch.id,
                        display_name=ch.character_name or ch.id,
                    ))
        if not bindings:
            response = {
                "reply": "你还没有绑定角色卡。请先使用 /车卡 创建角色，或让 DM 为你绑定角色。",
                "auto_escape": False, "at_sender": False,
            }
            response["parsed_attachments"] = parsed
            response["attachment_errors"] = attachment_errors
            return response
        target_dir = settings.data_dir / "generated" / "characters"
        target_dir.mkdir(parents=True, exist_ok=True)
        exported_names: list[str] = []
        upload_errors: list[str] = []
        for binding in bindings:
            character = db.get(Character, binding.character_id)
            if not character:
                upload_errors.append(f"角色 {binding.character_id} 不存在")
                continue
            template = next((settings.data_dir / "raw").glob("*人物卡模板.xlsx"), None)
            if not template:
                upload_errors.append("人物卡模板文件未找到")
                continue
            target = target_dir / f"{character.id}.xlsx"
            try:
                export_character_sheet(character.data, character.player_name or "", Path(template), target)
                name = f"{character.character_name or character.id}.xlsx"
                print(f"[export] generated {target}, uploading as {name}", flush=True)
                if payload.get("message_type") == "group":
                    result = client.upload_group_file(group_id, str(target), name)
                    try:
                        client.send_group_at(group_id, user_id, f"角色卡 {name} 已导出。")
                    except Exception:
                        pass
                else:
                    result = client.upload_private_file(user_id, str(target), name)
                print(f"[export] upload result: {result}", flush=True)
                exported_names.append(character.character_name or binding.character_id)
            except Exception as exc:
                print(f"[export] ERROR: {exc}", flush=True)
                upload_errors.append(f"{character.character_name}: {exc}")
        reply_parts: list[str] = []
        if exported_names:
            reply_parts.append(f"已导出角色卡：{'、'.join(exported_names)}")
        if upload_errors:
            reply_parts.append(f"导出失败：{'；'.join(upload_errors)}")
        response = {
            "reply": "\n".join(reply_parts) or "导出角色卡失败",
            "auto_escape": False,
            "at_sender": False,
        }
        response["parsed_attachments"] = parsed
        response["attachment_errors"] = attachment_errors
        return response

    response = handle_platform_message(
        db,
        adapter,
        adapter.incoming_from_payload(
            payload,
            text,
            reply_text=reply_text,
            group_history=group_history,
            group_history_error=group_history_error,
        ),
        process_fn=process_message,
    )
    # 附件错误反馈到 QQ 用户
    if attachment_errors and response.get("reply"):
        error_note = "\n\n[附件处理问题] " + "；".join(attachment_errors)
        if isinstance(response["reply"], str):
            response["reply"] += error_note
        elif isinstance(response["reply"], list):
            response["reply"].append({"type": "text", "data": {"text": error_note}})
    response["parsed_attachments"] = parsed
    response["attachment_errors"] = attachment_errors
    return response


@app.get("/napcat/bindings")
def list_napcat_bindings(campaign_id: str | None = None, db: Session = Depends(get_db)):
    query = select(NapCatCharacterBinding).order_by(NapCatCharacterBinding.updated_at.desc())
    if campaign_id:
        query = query.where(NapCatCharacterBinding.campaign_id == campaign_id)
    return [serialize_binding(binding, db) for binding in db.scalars(query).all()]


@app.get("/napcat/active-campaign")
def get_active_napcat_campaign(db: Session = Depends(get_db)):
    campaign = active_napcat_campaign(db) or db.get(Campaign, settings.napcat_campaign_id)
    if not campaign:
        raise HTTPException(404, "NapCat active campaign not found")
    return serialize(campaign)


@app.put("/napcat/active-campaign/{campaign_id}")
def switch_active_napcat_campaign(campaign_id: str, db: Session = Depends(get_db)):
    campaign = db.get(Campaign, campaign_id)
    if not campaign:
        raise HTTPException(404, "Campaign not found")
    switched = set_active_napcat_campaign(db, campaign)
    if (switched.config or {}).get("dice_dm_qq_user_id"):
        sync_campaign_actor_bindings(db, switched)
    return serialize(switched)


@app.get("/napcat/bindings/{qq_user_id}")
def get_napcat_binding(
    qq_user_id: str,
    campaign_id: str | None = Query(default=None),
    character_id: str | None = Query(default=None),
    db: Session = Depends(get_db),
):
    campaign_id = campaign_id or active_napcat_campaign_id(db, settings.napcat_campaign_id)
    binding = find_binding(db, campaign_id, qq_user_id, character_id)
    if not binding:
        raise HTTPException(404, "QQ user is not bound to a character in this campaign")
    return serialize_binding(binding, db)


@app.put("/napcat/bindings/{qq_user_id}")
def upsert_napcat_binding(qq_user_id: str, req: NapCatBindingUpsert, db: Session = Depends(get_db)):
    qq_user_id = qq_user_id.strip()
    if not qq_user_id.isdigit():
        raise HTTPException(400, "QQ user ID must contain digits only")
    character = db.get(Character, req.character_id)
    if not character:
        raise HTTPException(404, "Character not found")
    if character.campaign_id != req.campaign_id:
        raise HTTPException(400, "Character does not belong to the requested campaign")
    try:
        binding = bind_qq(db, req.campaign_id, qq_user_id, character, req.display_name, req.note)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return serialize_binding(binding, db)


@app.delete("/napcat/bindings/{qq_user_id}", status_code=204)
def delete_napcat_binding(
    qq_user_id: str,
    campaign_id: str | None = Query(default=None),
    character_id: str | None = Query(default=None),
    db: Session = Depends(get_db),
):
    campaign_id = campaign_id or active_napcat_campaign_id(db, settings.napcat_campaign_id)
    bindings = find_bindings(db, campaign_id, qq_user_id)
    if character_id:
        bindings = [item for item in bindings if item.character_id == character_id]
    if not bindings:
        raise HTTPException(404, "QQ user is not bound to a character in this campaign")
    unbind_qq(db, campaign_id, qq_user_id, character_id)


def serialize_binding(binding: NapCatCharacterBinding, db: Session) -> dict:
    result = serialize(binding)
    character = db.get(Character, binding.character_id)
    result["character_name"] = character.character_name if character else None
    return result


@app.post("/dice/roll")
def dice_roll(req: DiceRequest):
    try:
        return roll_dice(req.formula)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/campaigns", status_code=201)
def create_campaign(req: CampaignCreate, db: Session = Depends(get_db)):
    campaign = Campaign(id=uid("camp"), **req.model_dump())
    db.add(campaign)
    db.commit()
    return serialize(campaign)


@app.get("/campaigns")
def list_campaigns(db: Session = Depends(get_db)):
    return [serialize(x) for x in db.scalars(select(Campaign).order_by(Campaign.created_at.desc())).all()]


@app.get("/campaigns/{campaign_id}")
def get_campaign(campaign_id: str, db: Session = Depends(get_db)):
    campaign = db.get(Campaign, campaign_id)
    if not campaign:
        raise HTTPException(404, "Campaign not found")
    return serialize(campaign)


@app.get("/campaigns/{campaign_id}/status")
def get_campaign_status(campaign_id: str, db: Session = Depends(get_db)):
    from app.campaign_control import campaign_status
    from app.campaign_turns import current_turn, runtime_mode, turn_state
    from app.combat_preferences import combat_preference, preference_style
    campaign = db.get(Campaign, campaign_id)
    if not campaign:
        raise HTTPException(404, "Campaign not found")
    return {
        "campaign_id": campaign.id,
        "status": campaign_status(campaign),
        "active_session_id": (campaign.config or {}).get("active_session_id"),
        "last_checkpoint_id": (campaign.config or {}).get("last_checkpoint_id"),
        "runtime_mode": runtime_mode(campaign),
        "play_style": (campaign.config or {}).get("play_style", "campaign"),
        "combat_roleplay_enabled": combat_preference(campaign, "roleplay"),
        "combat_advice_enabled": combat_preference(campaign, "advice"),
        "combat_preference_style": preference_style(campaign),
        "turn_state": turn_state(campaign),
        "current_turn": current_turn(campaign),
    }


@app.get("/campaigns/{campaign_id}/checkpoints")
def list_campaign_checkpoints(campaign_id: str, db: Session = Depends(get_db)):
    if not db.get(Campaign, campaign_id):
        raise HTTPException(404, "Campaign not found")
    query = (
        select(CampaignCheckpoint)
        .where(CampaignCheckpoint.campaign_id == campaign_id)
        .order_by(CampaignCheckpoint.created_at.desc())
    )
    return [serialize(item) for item in db.scalars(query).all()]


@app.get("/campaigns/{campaign_id}/tasks")
def list_campaign_tasks(
    campaign_id: str,
    task_type: str | None = None,
    status: str | None = Query(default=None),
    owner_user_id: str | None = None,
    session_id: str | None = None,
    db: Session = Depends(get_db),
):
    if not db.get(Campaign, campaign_id):
        raise HTTPException(404, "Campaign not found")
    query = select(TaskSession).where(TaskSession.campaign_id == campaign_id)
    if task_type:
        query = query.where(TaskSession.task_type == task_type)
    if status:
        query = query.where(TaskSession.status == status)
    else:
        query = query.where(TaskSession.status.in_(ACTIVE_STATUSES))
    if owner_user_id:
        query = query.where(TaskSession.owner_user_id == owner_user_id)
    if session_id:
        query = query.where(TaskSession.session_id == session_id)
    return [serialize(item) for item in db.scalars(query.order_by(TaskSession.updated_at.desc())).all()]


@app.post("/campaigns/{campaign_id}/tasks", status_code=201)
def create_campaign_task(campaign_id: str, req: TaskSessionCreate, db: Session = Depends(get_db)):
    if not db.get(Campaign, campaign_id):
        raise HTTPException(404, "Campaign not found")
    item = TaskSession(
        id=uid("task"),
        campaign_id=campaign_id,
        task_type=req.task_type,
        platform=req.platform,
        chat_id=req.chat_id,
        owner_user_id=req.owner_user_id,
        session_id=req.session_id,
        status=req.status,
        priority=req.priority,
        draft_data=req.draft_data,
        proposal_data=req.proposal_data,
        missing_fields=req.missing_fields,
        next_prompt=req.next_prompt,
        mentions=req.mentions,
        source_message_id=req.source_message_id,
        parent_task_id=req.parent_task_id,
    )
    db.add(item)
    db.commit()
    return serialize(item)


@app.get("/campaigns/{campaign_id}/tasks/{task_id}")
def get_campaign_task(campaign_id: str, task_id: str, db: Session = Depends(get_db)):
    item = db.get(TaskSession, task_id)
    if not item or item.campaign_id != campaign_id:
        raise HTTPException(404, "Task session not found")
    return serialize(item)


@app.patch("/campaigns/{campaign_id}/tasks/{task_id}")
def patch_campaign_task(campaign_id: str, task_id: str, req: TaskSessionPatch, db: Session = Depends(get_db)):
    item = db.get(TaskSession, task_id)
    if not item or item.campaign_id != campaign_id:
        raise HTTPException(404, "Task session not found")
    for key, value in req.model_dump(exclude_none=True).items():
        setattr(item, key, value)
    db.commit()
    return serialize(item)


@app.patch("/campaigns/{campaign_id}")
def patch_campaign(campaign_id: str, req: CampaignPatch, db: Session = Depends(get_db)):
    campaign = db.get(Campaign, campaign_id)
    if not campaign:
        raise HTTPException(404, "Campaign not found")
    before_config = dict(campaign.config or {})
    for key, value in req.model_dump(exclude_none=True).items():
        setattr(campaign, key, value)
    db.commit()
    after_config = campaign.config or {}
    if (
        before_config.get("play_style") != after_config.get("play_style")
        or before_config.get("dice_dm_qq_user_id") != after_config.get("dice_dm_qq_user_id")
    ):
        sync_campaign_actor_bindings(db, campaign, str(after_config.get("dice_dm_qq_user_id") or "").strip() or None)
    return serialize(campaign)


@app.get("/campaigns/{campaign_id}/settings")
def get_campaign_settings(campaign_id: str, query: str | None = None, db: Session = Depends(get_db)):
    items = search_settings(db, campaign_id, query, 30) if query else list_settings(db, campaign_id)
    return [serialize(item) for item in items]


@app.get("/campaigns/{campaign_id}/setting/{setting_id}")
def get_campaign_setting(campaign_id: str, setting_id: str, db: Session = Depends(get_db)):
    item = db.get(CampaignSetting, setting_id)
    if not item or item.campaign_id != campaign_id:
        raise HTTPException(404, "Campaign setting not found")
    return serialize(item)


@app.post("/campaigns/{campaign_id}/setting/{setting_id}/npc-character", status_code=201)
def create_npc_from_setting(campaign_id: str, setting_id: str, db: Session = Depends(get_db)):
    item = db.get(CampaignSetting, setting_id)
    if not item or item.campaign_id != campaign_id or item.category not in {"npc", "monster"}:
        raise HTTPException(404, "Published NPC or monster setting not found")
    character = setting_to_npc_character(db, item)
    campaign = db.get(Campaign, campaign_id)
    if campaign and (campaign.config or {}).get("dice_dm_qq_user_id"):
        sync_campaign_actor_bindings(db, campaign)
    return serialize(character)


@app.get("/campaigns/{campaign_id}/setting-drafts")
def get_setting_drafts(campaign_id: str, db: Session = Depends(get_db)):
    query = select(CampaignSettingDraft).where(CampaignSettingDraft.campaign_id == campaign_id)
    return [serialize(item) for item in db.scalars(query.order_by(CampaignSettingDraft.created_at.desc())).all()]


@app.post("/campaigns/{campaign_id}/setting-drafts", status_code=201)
def add_setting_draft(campaign_id: str, req: SettingDraftCreate, db: Session = Depends(get_db)):
    proposal = {"category": req.category, "name": req.name, **req.proposal}
    return serialize(create_draft(
        db, campaign_id, req.operation, proposal, req.session_id, req.actor_id,
        req.target_setting_id, req.reason,
    ))


@app.post("/campaigns/{campaign_id}/setting-drafts/publish")
def publish_setting_drafts(campaign_id: str, actor_id: str | None = None, db: Session = Depends(get_db)):
    return [serialize(item) for item in publish_drafts(db, campaign_id, actor_id)]


@app.delete("/campaigns/{campaign_id}/setting-drafts")
def discard_setting_drafts(campaign_id: str, db: Session = Depends(get_db)):
    return {"discarded": discard_drafts(db, campaign_id)}


@app.post("/campaigns/{campaign_id}/setting-drafts/undo")
def undo_setting_draft(campaign_id: str, db: Session = Depends(get_db)):
    item = undo_latest_draft(db, campaign_id)
    return {"draft": serialize(item) if item else None}


@app.get("/campaigns/{campaign_id}/setting-history")
def get_setting_history(campaign_id: str, db: Session = Depends(get_db)):
    query = select(CampaignSettingHistory).where(CampaignSettingHistory.campaign_id == campaign_id)
    return [serialize(item) for item in db.scalars(query.order_by(CampaignSettingHistory.created_at.desc())).all()]


@app.get("/campaigns/{campaign_id}/setting-comments")
def get_setting_comments(campaign_id: str, db: Session = Depends(get_db)):
    query = select(CampaignSettingComment).where(CampaignSettingComment.campaign_id == campaign_id)
    return [serialize(item) for item in db.scalars(query.order_by(CampaignSettingComment.created_at.desc())).all()]


@app.post("/campaigns/{campaign_id}/setting-comments", status_code=201)
def add_setting_comment(campaign_id: str, req: SettingCommentCreate, db: Session = Depends(get_db)):
    comment = CampaignSettingComment(id=uid("comment"), campaign_id=campaign_id, **req.model_dump())
    db.add(comment)
    db.commit()
    return serialize(comment)


@app.post("/campaigns/{campaign_id}/setting-comments/{comment_id}/resolve")
def resolve_setting_comment(campaign_id: str, comment_id: str, db: Session = Depends(get_db)):
    comment = db.get(CampaignSettingComment, comment_id)
    if not comment or comment.campaign_id != campaign_id:
        raise HTTPException(404, "Campaign setting comment not found")
    comment.resolved = True
    db.commit()
    return serialize(comment)


@app.get("/campaigns/{campaign_id}/settings/validate")
def validate_campaign_settings(campaign_id: str, db: Session = Depends(get_db)):
    return validate_settings(db, campaign_id)


@app.get("/campaigns/{campaign_id}/settings/conflicts")
def campaign_setting_conflicts(campaign_id: str, db: Session = Depends(get_db)):
    return conflict_suggestions(db, campaign_id)


@app.get("/campaigns/{campaign_id}/setting-graph")
def campaign_setting_graph(campaign_id: str, db: Session = Depends(get_db)):
    return setting_graph(db, campaign_id)


@app.get("/campaigns/{campaign_id}/timeline")
def campaign_timeline(campaign_id: str, db: Session = Depends(get_db)):
    return setting_timeline(db, campaign_id)


@app.get("/campaign-setting-templates")
def campaign_setting_templates():
    return TEMPLATES


@app.post("/campaigns/{campaign_id}/templates/{template}")
def apply_campaign_template(campaign_id: str, template: str, actor_id: str | None = None, db: Session = Depends(get_db)):
    try:
        return {"drafts_created": apply_template(db, campaign_id, template, actor_id)}
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc


@app.get("/campaigns/{campaign_id}/package")
def export_campaign(campaign_id: str, db: Session = Depends(get_db)):
    if not db.get(Campaign, campaign_id):
        raise HTTPException(404, "Campaign not found")
    return export_campaign_package(db, campaign_id)


@app.post("/campaigns/{campaign_id}/package")
def import_campaign(campaign_id: str, req: CampaignPackageImport, db: Session = Depends(get_db)):
    try:
        return import_campaign_package(db, campaign_id, req.package)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/characters", status_code=201)
def create_character(req: CharacterCreate, db: Session = Depends(get_db)):
    campaign = db.get(Campaign, req.campaign_id)
    if not campaign:
        raise HTTPException(404, "Campaign not found")
    payload = req.model_dump()
    payload["data"] = normalize_character_inventory(payload["data"])
    payload["data"].setdefault("integrations", {}).setdefault("qq_user_ids", [])
    character = Character(id=uid("char"), **payload)
    db.add(character)
    db.commit()
    qq_user_ids = ((character.data.get("integrations") or {}).get("qq_user_ids")
                   if isinstance(character.data.get("integrations"), dict) else None)
    if qq_user_ids is not None:
        try:
            sync_character_bindings(db, character, qq_user_ids)
        except ValueError as exc:
            db.delete(character)
            db.commit()
            raise HTTPException(400, str(exc)) from exc
    if (campaign.config or {}).get("dice_dm_qq_user_id"):
        sync_campaign_actor_bindings(db, campaign)
    return serialize(character)


@app.post("/characters/build", status_code=201)
def build_character(req: CharacterBuildRequest, db: Session = Depends(get_db)):
    campaign = db.get(Campaign, req.campaign_id)
    if not campaign:
        raise HTTPException(404, "Campaign not found")
    data = build_character_data(req.model_dump())
    character = Character(
        id=uid("char"),
        campaign_id=req.campaign_id,
        player_name=req.player_name,
        character_name=req.character_name,
        data=data,
    )
    db.add(character)
    db.commit()
    if (campaign.config or {}).get("dice_dm_qq_user_id"):
        sync_campaign_actor_bindings(db, campaign)
    return {
        **serialize(character),
        "point_buy_cost": data["derived"]["point_buy_cost"],
        "validation_errors": data["validation_errors"],
    }


@app.get("/characters/rules/catalog")
def character_rule_catalog():
    classes = sorted({
        name: {
            "hit_die": CLASS_HIT_DICE[name],
            "saving_throw_proficiencies": list(CLASS_SAVING_THROWS.get(name, [])),
        }
        for name in CLASS_HIT_DICE
        if name.isascii()
    }.items())
    return {
        "source": "data/raw/DND 5E人物卡模板.xlsx",
        "formula_catalog": "data/generated/character_rules/formula_catalog.json",
        "formula_report": "data/generated/character_rules/formula_report.md",
        "abilities": list(ABILITY_KEYS),
        "skills": SKILL_ABILITIES,
        "classes": dict(classes),
        "point_buy": {"budget": 27, "costs": POINT_BUY_COSTS},
    }


@app.get("/characters/items/schema")
def character_item_schema():
    return item_schema_catalog()


@app.get("/characters/effects/schema")
def character_effect_schema():
    return {
        "storage": "character.data.active_effects",
        "computed": "character.data.effective",
        "effect_json_schema": ActiveEffect.model_json_schema(),
    }


@app.get("/spells")
def list_spells(
    query: str | None = None,
    class_name: str | None = None,
    limit: int = Query(20, ge=1, le=100),
):
    if query:
        return search_spells(query, settings.data_dir, limit, class_name)
    spells = load_spell_catalog(str(settings.data_dir.resolve()))
    if class_name:
        spells = [spell for spell in spells if class_name.casefold() in spell.get("classes", [])]
    return spells[:limit]


@app.get("/spells/{spell_id}")
def get_spell(spell_id: str):
    spell = next((item for item in load_spell_catalog(str(settings.data_dir.resolve())) if item["id"] == spell_id), None)
    if not spell:
        raise HTTPException(404, "Spell not found")
    return spell


@app.get("/campaigns/{campaign_id}/characters")
def list_characters(campaign_id: str, db: Session = Depends(get_db)):
    return [serialize(x) for x in db.scalars(select(Character).where(Character.campaign_id == campaign_id)).all()]


@app.get("/campaigns/{campaign_id}/actors")
def campaign_actors(campaign_id: str, actor_type: str | None = None, present: bool | None = None,
                    db: Session = Depends(get_db)):
    return [serialize(item) for item in list_actors(db, campaign_id, actor_type, present)]


@app.get("/characters/{character_id}/roleplay")
def get_actor_roleplay(character_id: str, db: Session = Depends(get_db)):
    character = db.get(Character, character_id)
    if not character:
        raise HTTPException(404, "Character not found")
    return roleplay_brief(character)


@app.patch("/characters/{character_id}/roleplay")
def patch_actor_roleplay(character_id: str, req: ActorRoleplayPatch, db: Session = Depends(get_db)):
    character = db.get(Character, character_id)
    if not character:
        raise HTTPException(404, "Character not found")
    update_roleplay(db, character, req.roleplay, req.story_role, req.encounter)
    return serialize(character)


@app.patch("/characters/{character_id}/presence")
def patch_actor_presence(character_id: str, req: ActorPresencePatch, db: Session = Depends(get_db)):
    character = db.get(Character, character_id)
    if not character:
        raise HTTPException(404, "Character not found")
    set_presence(db, character, req.present, req.scene)
    return serialize(character)


@app.post("/campaigns/{campaign_id}/characters/inventory/normalize")
def normalize_campaign_character_inventories(campaign_id: str, db: Session = Depends(get_db)):
    from app.services import update_character

    if not db.get(Campaign, campaign_id):
        raise HTTPException(404, "Campaign not found")
    characters = db.scalars(select(Character).where(Character.campaign_id == campaign_id)).all()
    updated = []
    for character in characters:
        normalized = normalize_character_inventory(character.data)
        if normalized != character.data:
            update_character(
                db,
                character,
                {
                    "inventory": normalized["inventory"], "currency": normalized["currency"],
                    "active_effects": normalized["active_effects"],
                },
                "normalized inventory to structured item schema",
                "inventory_schema_migration",
            )
            updated.append(character.id)
    return {"characters_scanned": len(characters), "characters_updated": len(updated), "character_ids": updated}


@app.get("/characters/{character_id}")
def get_character(character_id: str, db: Session = Depends(get_db)):
    character = db.get(Character, character_id)
    if not character:
        raise HTTPException(404, "Character not found")
    return serialize(character)


@app.get("/characters/{character_id}/effective")
def get_effective_character(character_id: str, db: Session = Depends(get_db)):
    character = db.get(Character, character_id)
    if not character:
        raise HTTPException(404, "Character not found")
    campaign = db.get(Campaign, character.campaign_id)
    combat = bool((((campaign.config if campaign else {}) or {}).get("turn_state") or {}).get("combat"))
    return resolve_effective_character(character.data, combat)


@app.get("/characters/{character_id}/sheet")
def export_character(character_id: str, db: Session = Depends(get_db)):
    character = db.get(Character, character_id)
    if not character:
        raise HTTPException(404, "Character not found")
    template = next((settings.data_dir / "raw").glob("*人物卡模板.xlsx"), None)
    if not template:
        raise HTTPException(404, "Character sheet template not found")
    target_dir = settings.data_dir / "generated" / "characters"
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"{character.id}.xlsx"
    export_character_sheet(character.data, character.player_name or "", Path(template), target)
    return FileResponse(
        target,
        filename=f"{character.character_name or character.id}.xlsx",
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@app.patch("/characters/{character_id}")
def patch_character(character_id: str, req: CharacterPatch, db: Session = Depends(get_db)):
    from app.services import update_character
    character = db.get(Character, character_id)
    if not character:
        raise HTTPException(404, "Character not found")
    campaign = db.get(Campaign, character.campaign_id)
    qq_user_ids = ((req.data.get("integrations") or {}).get("qq_user_ids")
                   if isinstance(req.data.get("integrations"), dict) else None)
    if qq_user_ids is not None and any(not str(item).strip().isdigit() for item in qq_user_ids):
        raise HTTPException(400, "QQ user ID must contain digits only")
    change = update_character(db, character, req.data, req.reason, req.change_type, req.rule_refs)
    if qq_user_ids is not None:
        try:
            sync_character_bindings(db, character, qq_user_ids)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
    if campaign and (campaign.config or {}).get("dice_dm_qq_user_id"):
        sync_campaign_actor_bindings(db, campaign)
    return {"character": serialize(character), "change": serialize(change)}


@app.patch("/characters/{character_id}/qq-bindings")
def patch_character_qq_bindings(
    character_id: str, req: CharacterQQBindingsPatch, db: Session = Depends(get_db),
):
    character = db.get(Character, character_id)
    if not character:
        raise HTTPException(404, "Character not found")
    try:
        bindings = sync_character_bindings(db, character, req.qq_user_ids)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {
        "character": serialize(character),
        "bindings": [serialize_binding(binding, db) for binding in bindings],
    }


@app.delete("/characters/{character_id}", status_code=204)
def delete_character(character_id: str, db: Session = Depends(get_db)):
    character = db.get(Character, character_id)
    if not character:
        raise HTTPException(404, "Character not found")
    delete_character_and_bindings(db, character)


@app.get("/characters/{character_id}/changes")
def character_changes(character_id: str, db: Session = Depends(get_db)):
    query = select(CharacterChange).where(CharacterChange.character_id == character_id).order_by(CharacterChange.created_at.desc())
    return [serialize(x) for x in db.scalars(query).all()]


@app.post("/campaigns/{campaign_id}/events", status_code=201)
def create_event(campaign_id: str, req: EventCreate, db: Session = Depends(get_db)):
    if not db.get(Campaign, campaign_id):
        raise HTTPException(404, "Campaign not found")
    return serialize(append_event(db, campaign_id, req.session_id, req.event_type, req.content,
                                  req.actors, req.metadata, req.visibility))


@app.get("/campaigns/{campaign_id}/events")
def list_events(campaign_id: str, visibility: str = "party", db: Session = Depends(get_db)):
    query = select(CampaignEvent).where(CampaignEvent.campaign_id == campaign_id)
    if visibility != "dm_only":
        query = query.where(CampaignEvent.visibility != "dm_only")
    return [serialize(x) for x in db.scalars(query.order_by(CampaignEvent.created_at.desc())).all()]


@app.get("/campaigns/{campaign_id}/memories")
def list_campaign_memories(
    campaign_id: str,
    query: str | None = None,
    session_id: str | None = None,
    limit: int = Query(30, ge=1, le=100),
    db: Session = Depends(get_db),
):
    if not db.get(Campaign, campaign_id):
        raise HTTPException(404, "Campaign not found")
    if query:
        return [serialize(item) for item in search_campaign_memory(db, campaign_id, query, session_id, limit)]
    statement = (
        select(CampaignMemory)
        .where(CampaignMemory.campaign_id == campaign_id)
        .order_by(CampaignMemory.updated_at.desc())
        .limit(limit)
    )
    return [serialize(item) for item in db.scalars(statement).all()]


@app.post("/campaigns/{campaign_id}/memories/backfill")
def backfill_memories(campaign_id: str, db: Session = Depends(get_db)):
    if not db.get(Campaign, campaign_id):
        raise HTTPException(404, "Campaign not found")
    return backfill_campaign_memory(db, campaign_id)


@app.get("/campaigns/{campaign_id}/entities")
def list_campaign_entities(campaign_id: str, db: Session = Depends(get_db)):
    statement = (
        select(CampaignEntity)
        .where(CampaignEntity.campaign_id == campaign_id)
        .order_by(CampaignEntity.updated_at.desc())
    )
    return [serialize(item) for item in db.scalars(statement).all()]


@app.get("/campaigns/{campaign_id}/threads")
def list_campaign_threads(campaign_id: str, status: str = "open", db: Session = Depends(get_db)):
    statement = select(CampaignThread).where(CampaignThread.campaign_id == campaign_id)
    if status:
        statement = statement.where(CampaignThread.status == status)
    return [serialize(item) for item in db.scalars(statement.order_by(CampaignThread.priority.desc())).all()]


@app.post("/chat/{campaign_id}")
@app.post("/dm/step/{campaign_id}")
def chat(campaign_id: str, req: ChatRequest, db: Session = Depends(get_db)):
    campaign = db.get(Campaign, campaign_id)
    if not campaign:
        raise HTTPException(404, "Campaign not found")
    if req.character_id:
        character = db.get(Character, req.character_id)
        if not character:
            raise HTTPException(404, "Character not found")
        if character.campaign_id != campaign_id:
            raise HTTPException(400, "Character does not belong to this campaign")
    return process_message(
        db,
        campaign,
        req.session_id,
        req.character_id,
        req.message,
        actor_id=req.player_id,
        is_dm=req.player_id is None,
        message_context={
            "platform": "web",
            "session_id": req.session_id,
            "sender_id": req.player_id,
            "current_text": req.message,
        },
    )


@app.post("/campaigns/{campaign_id}/summaries", status_code=201)
def summarize(campaign_id: str, session_id: str | None = None, db: Session = Depends(get_db)):
    if not db.get(Campaign, campaign_id):
        raise HTTPException(404, "Campaign not found")
    return serialize(create_summary(db, campaign_id, session_id))


@app.get("/campaigns/{campaign_id}/summaries")
def list_summaries(campaign_id: str, db: Session = Depends(get_db)):
    query = select(CampaignSummary).where(CampaignSummary.campaign_id == campaign_id).order_by(CampaignSummary.updated_at.desc())
    return [serialize(x) for x in db.scalars(query).all()]


@app.post("/ingest/rules")
def do_ingest_rules(db: Session = Depends(get_db)):
    return {"imported": ingest_rules(db)}


@app.get("/rules/search")
def rules_search(query: str, limit: int = Query(8, ge=1, le=30), db: Session = Depends(get_db)):
    return search_rules(db, query, limit)


@app.post("/ingest/compendium")
def do_ingest_compendium(db: Session = Depends(get_db)):
    return {"imported": ingest_compendium(db)}


@app.get("/compendium/search")
def compendium_search(type: str | None = None, name: str | None = None, db: Session = Depends(get_db)):
    query = select(CompendiumEntry)
    if type:
        query = query.where(CompendiumEntry.entry_type == type)
    if name:
        query = query.where(CompendiumEntry.name.ilike(f"%{name}%"))
    return [serialize(x) for x in db.scalars(query.limit(30)).all()]


@app.get("/compendium/{entry_id}")
def get_compendium(entry_id: str, db: Session = Depends(get_db)):
    entry = db.get(CompendiumEntry, entry_id)
    if not entry:
        raise HTTPException(404, "Compendium entry not found")
    return serialize(entry)


@app.post("/demo/bootstrap")
def demo_bootstrap(db: Session = Depends(get_db)):
    campaign = db.get(Campaign, "campaign_001")
    if not campaign:
        campaign = Campaign(id="campaign_001", name="北境之门", system_version="DND_5E_2014",
                            description="一支冒险小队抵达戒备森严的北境之门。", config={"scene": "North Gate"})
        db.add(campaign)
    else:
        campaign.name = "北境之门"
        campaign.system_version = "DND_5E_2014"
        campaign.description = "一支冒险小队抵达戒备森严的北境之门。"
        campaign.config = {"scene": "North Gate"}
    character = db.get(Character, "char_001")
    if not character:
        character = Character(id="char_001", campaign_id="campaign_001", player_name="Player",
                              character_name="Aric", data=demo_character())
        db.add(character)
    else:
        character.campaign_id = "campaign_001"
        character.player_name = "Player"
        character.character_name = "Aric"
        character.data = demo_character()
    db.commit()
    set_active_napcat_campaign(db, campaign)
    return {"campaign": serialize(campaign), "character": serialize(character)}


def demo_character() -> dict:
    return normalize_character_inventory({
        "basic": {"name": "Aric", "ancestry": "Human", "classes": [{"name": "Fighter", "level": 3}]},
        "abilities": {"str": 16, "dex": 12, "con": 14, "int": 10, "wis": 11, "cha": 13},
        "combat": {"armor_class": 17, "max_hp": 28, "current_hp": 9, "temp_hp": 0, "proficiency_bonus": 2},
        "skills": {"persuasion": {"proficient": True, "expertise": False}},
        "inventory": [
            {
                "item_id": "longsword", "name": "Longsword", "item_type": "weapon", "quantity": 1,
                "equipped": True, "equipped_slot": "main_hand",
                "weapon": {"damage_dice": "1d8", "damage_type": "slashing", "versatile_damage": "1d10"},
            },
            {
                "item_id": "potion_healing", "name": "Potion of Healing", "item_type": "consumable",
                "quantity": 2, "consumable": {"consume_on_use": True, "activation": "action"},
                "effects": [{"effect_type": "healing", "formula": "2d4+2"}],
            },
        ],
        "conditions": [], "notes": {}, "integrations": {"qq_user_ids": []},
    })


# ═══════════════════════════════════════════════════════════════════
#  Dice passive listener — roll pure dice notation without @mention
# ═══════════════════════════════════════════════════════════════════

import re as _re

_PURE_DICE_RE = _re.compile(r"^\s*(\d*d\d+(?:\s*[+-]\s*\d+)?)\s*$", _re.IGNORECASE)


def _handle_dice_passive(payload: dict, client) -> dict | None:
    """If the message is a pure dice formula, roll it and @ the sender."""
    text = parse_event_text(payload, client.self_id).strip()
    if not text:
        return None
    match = _PURE_DICE_RE.match(text)
    if not match:
        return None
    formula = _re.sub(r"\s+", "", match.group(1))
    from app.tools.dice import roll_dice
    try:
        result = roll_dice(formula)
    except ValueError:
        return None
    user_id = str(payload.get("user_id", "")).strip()
    reply = f"🎲 {formula} = {result['total']} [{', '.join(map(str, result['rolls']))}]"
    if result.get("modifier", 0) != 0:
        reply += f"{result['modifier']:+d}"
    if payload.get("message_type") == "group":
        try:
            client.send_group_at(payload["group_id"], user_id, reply)
        except Exception:
            client.send_group_msg(payload["group_id"], reply)
    else:
        client.send_private_msg(user_id, reply)
    return {"ok": True, "kind": "dice_passive", "formula": formula, "result": result["total"]}
