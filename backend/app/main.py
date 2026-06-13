from contextlib import asynccontextmanager
import shutil
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.db.database import Base, engine, get_db
from app.db.models import (Campaign, CampaignCheckpoint, CampaignEntity, CampaignEvent, CampaignMemory,
                           CampaignSetting, CampaignSettingComment, CampaignSettingDraft, CampaignSettingHistory, CampaignSummary,
                           CampaignThread, Character, CharacterChange, CompendiumEntry, NapCatCharacterBinding)
from app.schemas import (CampaignCreate, CampaignPatch, CharacterCreate, CharacterPatch,
                         CharacterBuildRequest, ChatRequest, DiceRequest, EventCreate,
                         NapCatBindingUpsert, SettingDraftCreate, CampaignPackageImport, SettingCommentCreate,
                         ActorRoleplayPatch, ActorPresencePatch)
from app.services import (append_event, create_summary, ingest_compendium, ingest_rules,
                          search_rules, serialize, uid)
from app.tools.dice import roll_dice
from app.config import settings
from app.integrations.napcat import (NapCatClient, callback_token_valid, download_attachments,
                                     is_allowed, is_dm_user, is_group_at_event, is_supported_message,
                                     parse_event_text)
from app.message_router import process_message
from app.parsing.api import router as parsing_router
from app.parsing.router import parse_files
from app.tools.character_builder import build_character_data, export_character_sheet
from app.tools.character_rules import (
    ABILITY_KEYS, CLASS_HIT_DICE, CLASS_SAVING_THROWS, POINT_BUY_COSTS, SKILL_ABILITIES,
)
from app.tools.spell_catalog import load_spell_catalog, search_spells
from app.tools.item_schema import item_schema_catalog, normalize_character_inventory
from app.campaign_memory import backfill_campaign_memory, search_campaign_memory
from app.campaign_editor import (
    TEMPLATES, apply_template, conflict_suggestions, create_draft, export_campaign_package,
    discard_drafts, import_campaign_package, list_settings, publish_drafts, search_settings, setting_graph,
    setting_timeline, setting_to_npc_character, undo_latest_draft, validate_settings,
)
from app.actor_manager import list_actors, roleplay_brief, set_presence, update_roleplay


@asynccontextmanager
async def lifespan(_: FastAPI):
    Base.metadata.create_all(bind=engine)
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
    payload = await request.json()
    if not is_supported_message(payload):
        return {"ok": True, "ignored": "unsupported_event"}
    if not is_allowed(payload):
        return {"ok": True, "ignored": "user_not_allowed"}
    if (payload.get("message_type") == "group" and settings.napcat_require_group_at
            and not is_group_at_event(payload, client.self_id)):
        return {"ok": True, "ignored": "group_message_without_at"}

    text = parse_event_text(payload, client.self_id)
    temp_root, paths, attachment_errors = download_attachments(client, payload)
    try:
        parsed = parse_files(paths, per_file_max_chars=4000, total_max_chars=12000) if paths else None
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)

    attachment_context = (parsed or {}).get("content", "")
    if attachment_context:
        text = f"{text}\n\n玩家同时发送了以下附件内容：\n{attachment_context}".strip()
    if not text:
        return {"ok": True, "ignored": "empty_message", "attachment_errors": attachment_errors}

    campaign = db.get(Campaign, settings.napcat_campaign_id)
    if not campaign:
        raise HTTPException(404, f"NapCat campaign not found: {settings.napcat_campaign_id}")
    character_id = settings.napcat_character_id or None
    if character_id and not db.get(Character, character_id):
        character_id = None
    user_id = str(payload.get("user_id", "")).strip() or "user"
    group_id = str(payload.get("group_id", "")).strip()
    session_id = f"napcat_group_{group_id}_{user_id}" if group_id else f"napcat_private_{user_id}"
    binding = db.scalar(select(NapCatCharacterBinding).where(
        NapCatCharacterBinding.campaign_id == campaign.id,
        NapCatCharacterBinding.qq_user_id == user_id,
    ))
    if binding:
        character_id = binding.character_id
    result = process_message(
        db, campaign, session_id, character_id, text, actor_id=user_id, is_dm=is_dm_user(user_id)
    )
    answer = result["narration"]
    if payload.get("message_type") == "group":
        client.send_group_msg(payload["group_id"], answer)
        notification = result.get("turn_notification") or result.get("data", {}).get("turn_notification")
        if notification and notification.get("qq_user_id"):
            client.send_group_at(
                payload["group_id"],
                notification["qq_user_id"],
                f"轮到你的角色“{notification['name']}”行动了。",
            )
    else:
        client.send_private_msg(payload["user_id"], answer)
    return {"ok": True, "result": result, "parsed_attachments": parsed, "attachment_errors": attachment_errors}


@app.get("/napcat/bindings")
def list_napcat_bindings(campaign_id: str | None = None, db: Session = Depends(get_db)):
    query = select(NapCatCharacterBinding).order_by(NapCatCharacterBinding.updated_at.desc())
    if campaign_id:
        query = query.where(NapCatCharacterBinding.campaign_id == campaign_id)
    return [serialize_binding(binding, db) for binding in db.scalars(query).all()]


@app.get("/napcat/bindings/{qq_user_id}")
def get_napcat_binding(
    qq_user_id: str,
    campaign_id: str = Query(default=settings.napcat_campaign_id),
    db: Session = Depends(get_db),
):
    binding = find_napcat_binding(db, campaign_id, qq_user_id)
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
    binding = find_napcat_binding(db, req.campaign_id, qq_user_id)
    if not binding:
        binding = NapCatCharacterBinding(
            id=uid("napcat_binding"),
            campaign_id=req.campaign_id,
            qq_user_id=qq_user_id,
            character_id=req.character_id,
        )
        db.add(binding)
    binding.character_id = req.character_id
    binding.display_name = req.display_name.strip() or None
    binding.note = req.note.strip() or None
    db.commit()
    return serialize_binding(binding, db)


@app.delete("/napcat/bindings/{qq_user_id}", status_code=204)
def delete_napcat_binding(
    qq_user_id: str,
    campaign_id: str = Query(default=settings.napcat_campaign_id),
    db: Session = Depends(get_db),
):
    binding = find_napcat_binding(db, campaign_id, qq_user_id)
    if not binding:
        raise HTTPException(404, "QQ user is not bound to a character in this campaign")
    db.delete(binding)
    db.commit()


def find_napcat_binding(db: Session, campaign_id: str, qq_user_id: str) -> NapCatCharacterBinding | None:
    return db.scalar(select(NapCatCharacterBinding).where(
        NapCatCharacterBinding.campaign_id == campaign_id,
        NapCatCharacterBinding.qq_user_id == qq_user_id.strip(),
    ))


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


@app.patch("/campaigns/{campaign_id}")
def patch_campaign(campaign_id: str, req: CampaignPatch, db: Session = Depends(get_db)):
    campaign = db.get(Campaign, campaign_id)
    if not campaign:
        raise HTTPException(404, "Campaign not found")
    for key, value in req.model_dump(exclude_none=True).items():
        setattr(campaign, key, value)
    db.commit()
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
    return serialize(setting_to_npc_character(db, item))


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
    if not db.get(Campaign, req.campaign_id):
        raise HTTPException(404, "Campaign not found")
    payload = req.model_dump()
    payload["data"] = normalize_character_inventory(payload["data"])
    character = Character(id=uid("char"), **payload)
    db.add(character)
    db.commit()
    return serialize(character)


@app.post("/characters/build", status_code=201)
def build_character(req: CharacterBuildRequest, db: Session = Depends(get_db)):
    if not db.get(Campaign, req.campaign_id):
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
                {"inventory": normalized["inventory"], "currency": normalized["currency"]},
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
    change = update_character(db, character, req.data, req.reason, req.change_type, req.rule_refs)
    return {"character": serialize(character), "change": serialize(change)}


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
    character = db.get(Character, "char_001")
    if not character:
        character = Character(id="char_001", campaign_id="campaign_001", player_name="Player",
                              character_name="Aric", data=demo_character())
        db.add(character)
    db.commit()
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
        "conditions": [], "notes": {},
    })
