from __future__ import annotations

import copy

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.db.database import engine
from app.db.models import Campaign, Character, NapCatCharacterBinding
from app.services import uid


def migrate_binding_schema_for_multiple_characters() -> None:
    if engine.dialect.name != "sqlite":
        return
    with engine.begin() as connection:
        table_sql = connection.execute(text(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='napcat_character_bindings'"
        )).scalar()
        if not table_sql or "uq_napcat_binding_campaign_qq_character" in table_sql:
            return
        connection.execute(text("PRAGMA foreign_keys=OFF"))
        connection.execute(text("""
            CREATE TABLE napcat_character_bindings_multi (
                id VARCHAR NOT NULL PRIMARY KEY,
                campaign_id VARCHAR NOT NULL,
                qq_user_id VARCHAR NOT NULL,
                character_id VARCHAR NOT NULL,
                display_name VARCHAR,
                note TEXT,
                created_at DATETIME NOT NULL,
                updated_at DATETIME NOT NULL,
                CONSTRAINT uq_napcat_binding_campaign_qq_character
                    UNIQUE (campaign_id, qq_user_id, character_id),
                FOREIGN KEY(campaign_id) REFERENCES campaigns (id) ON DELETE CASCADE,
                FOREIGN KEY(character_id) REFERENCES characters (id) ON DELETE CASCADE
            )
        """))
        connection.execute(text("""
            INSERT INTO napcat_character_bindings_multi
                (id, campaign_id, qq_user_id, character_id, display_name, note, created_at, updated_at)
            SELECT id, campaign_id, qq_user_id, character_id, display_name, note, created_at, updated_at
            FROM napcat_character_bindings
        """))
        connection.execute(text("DROP TABLE napcat_character_bindings"))
        connection.execute(text("ALTER TABLE napcat_character_bindings_multi RENAME TO napcat_character_bindings"))
        connection.execute(text("CREATE INDEX ix_napcat_character_bindings_campaign_id ON napcat_character_bindings (campaign_id)"))
        connection.execute(text("CREATE INDEX ix_napcat_character_bindings_qq_user_id ON napcat_character_bindings (qq_user_id)"))
        connection.execute(text("CREATE INDEX ix_napcat_character_bindings_character_id ON napcat_character_bindings (character_id)"))
        connection.execute(text("PRAGMA foreign_keys=ON"))


def character_qq_user_ids(character: Character) -> list[str]:
    integrations = (character.data or {}).get("integrations") or {}
    return sorted({str(item).strip() for item in integrations.get("qq_user_ids", []) if str(item).strip()})


def _write_character_qq_user_ids(character: Character, qq_user_ids: list[str]) -> None:
    data = copy.deepcopy(character.data or {})
    integrations = copy.deepcopy(data.get("integrations") or {})
    integrations["qq_user_ids"] = sorted(set(qq_user_ids))
    data["integrations"] = integrations
    if data != character.data:
        character.data = data
        character.version += 1


def set_dice_dm_actor_bindings(db: Session, campaign: Campaign, dm_qq_user_id: str | None) -> list[Character]:
    updated = []
    characters = db.scalars(select(Character).where(Character.campaign_id == campaign.id)).all()
    for character in characters:
        actor_type = ((character.data or {}).get("basic") or {}).get("actor_type", "player")
        if actor_type not in {"npc", "monster"}:
            continue
        data = copy.deepcopy(character.data or {})
        integrations = copy.deepcopy(data.get("integrations") or {})
        if "dice_dm_qq_user_id" in integrations:
            integrations.pop("dice_dm_qq_user_id", None)
            data["integrations"] = integrations
            character.data = data
            character.version += 1
            db.commit()
        stale_managed = db.scalars(select(NapCatCharacterBinding).where(
            NapCatCharacterBinding.campaign_id == campaign.id,
            NapCatCharacterBinding.character_id == character.id,
            NapCatCharacterBinding.note == "dice_assistant_dm_managed",
            *([] if not dm_qq_user_id else [NapCatCharacterBinding.qq_user_id != dm_qq_user_id]),
        )).all()
        for binding in stale_managed:
            db.delete(binding)
        if stale_managed:
            _write_character_qq_user_ids(character, [
                item for item in character_qq_user_ids(character)
                if item not in {binding.qq_user_id for binding in stale_managed}
            ])
            db.commit()
            updated.append(character)
        if dm_qq_user_id:
            existing = find_binding(db, campaign.id, dm_qq_user_id, character.id)
            if not existing:
                bind_qq(
                    db, campaign.id, dm_qq_user_id, character,
                    note="dice_assistant_dm_managed",
                )
                updated.append(character)
        else:
            continue
    return list({item.id: item for item in updated}.values())


def find_bindings(db: Session, campaign_id: str, qq_user_id: str) -> list[NapCatCharacterBinding]:
    return db.scalars(select(NapCatCharacterBinding).where(
        NapCatCharacterBinding.campaign_id == campaign_id,
        NapCatCharacterBinding.qq_user_id == qq_user_id.strip(),
    ).order_by(NapCatCharacterBinding.updated_at.desc())).all()


def find_binding(
    db: Session,
    campaign_id: str,
    qq_user_id: str,
    character_id: str | None = None,
) -> NapCatCharacterBinding | None:
    query = select(NapCatCharacterBinding).where(
        NapCatCharacterBinding.campaign_id == campaign_id,
        NapCatCharacterBinding.qq_user_id == qq_user_id.strip(),
    )
    if character_id:
        query = query.where(NapCatCharacterBinding.character_id == character_id)
    return db.scalar(query.order_by(NapCatCharacterBinding.updated_at.desc()))


def bind_qq(
    db: Session,
    campaign_id: str,
    qq_user_id: str,
    character: Character,
    display_name: str = "",
    note: str = "",
) -> NapCatCharacterBinding:
    qq_user_id = qq_user_id.strip()
    if not qq_user_id.isdigit():
        raise ValueError("QQ user ID must contain digits only")
    if character.campaign_id != campaign_id:
        raise ValueError("Character does not belong to the requested campaign")
    binding = find_binding(db, campaign_id, qq_user_id, character.id)
    if not binding:
        binding = NapCatCharacterBinding(
            id=uid("napcat_binding"), campaign_id=campaign_id,
            qq_user_id=qq_user_id, character_id=character.id,
        )
        db.add(binding)
    binding.character_id = character.id
    binding.display_name = display_name.strip() or None
    binding.note = note.strip() or None
    _write_character_qq_user_ids(character, [*character_qq_user_ids(character), qq_user_id])
    db.commit()
    return binding


def unbind_qq(
    db: Session,
    campaign_id: str,
    qq_user_id: str,
    character_id: str | None = None,
) -> list[NapCatCharacterBinding]:
    bindings = find_bindings(db, campaign_id, qq_user_id)
    if character_id:
        bindings = [item for item in bindings if item.character_id == character_id]
    for binding in bindings:
        character = db.get(Character, binding.character_id)
        db.delete(binding)
        db.flush()
        if character and not find_binding(db, campaign_id, qq_user_id, character.id):
            _write_character_qq_user_ids(character, [
                item for item in character_qq_user_ids(character) if item != binding.qq_user_id
            ])
    db.commit()
    return bindings


def sync_character_bindings(db: Session, character: Character, qq_user_ids: list[str]) -> list[NapCatCharacterBinding]:
    desired = sorted({str(item).strip() for item in qq_user_ids if str(item).strip()})
    if any(not item.isdigit() for item in desired):
        raise ValueError("QQ user ID must contain digits only")
    current = db.scalars(select(NapCatCharacterBinding).where(
        NapCatCharacterBinding.campaign_id == character.campaign_id,
        NapCatCharacterBinding.character_id == character.id,
    )).all()
    for binding in current:
        if binding.qq_user_id not in desired:
            db.delete(binding)
    db.commit()
    for qq_user_id in desired:
        bind_qq(db, character.campaign_id, qq_user_id, character)
    _write_character_qq_user_ids(character, desired)
    db.commit()
    return db.scalars(select(NapCatCharacterBinding).where(
        NapCatCharacterBinding.campaign_id == character.campaign_id,
        NapCatCharacterBinding.character_id == character.id,
    )).all()


def delete_character_and_bindings(db: Session, character: Character) -> None:
    bindings = db.scalars(select(NapCatCharacterBinding).where(
        NapCatCharacterBinding.character_id == character.id,
    )).all()
    for binding in bindings:
        db.delete(binding)
    db.delete(character)
    db.commit()


def backfill_character_binding_mirrors(db: Session) -> None:
    characters = db.scalars(select(Character)).all()
    bindings = db.scalars(select(NapCatCharacterBinding)).all()
    by_character: dict[str, list[str]] = {}
    for binding in bindings:
        by_character.setdefault(binding.character_id, []).append(binding.qq_user_id)
    for character in characters:
        _write_character_qq_user_ids(character, by_character.get(character.id, []))
    db.commit()


def active_napcat_campaign(db: Session) -> Campaign | None:
    campaigns = db.scalars(select(Campaign).order_by(Campaign.updated_at.desc())).all()
    return next((item for item in campaigns if (item.config or {}).get("napcat_active")), None)


def active_napcat_campaign_id(db: Session, fallback: str) -> str:
    campaign = active_napcat_campaign(db)
    return campaign.id if campaign else fallback


def set_active_napcat_campaign(db: Session, campaign: Campaign) -> Campaign:
    for item in db.scalars(select(Campaign)).all():
        config = copy.deepcopy(item.config or {})
        active = item.id == campaign.id
        if bool(config.get("napcat_active")) != active:
            if active:
                config["napcat_active"] = True
            else:
                config.pop("napcat_active", None)
            item.config = config
    db.commit()
    return campaign
