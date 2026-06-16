from __future__ import annotations

import copy

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Character


def actor_type(character: Character) -> str:
    value = str((character.data.get("basic") or {}).get("actor_type") or "player")
    return value if value in {"player", "npc", "monster"} else "player"


def is_dm_actor(character: Character) -> bool:
    return actor_type(character) in {"npc", "monster"}


def is_present(character: Character) -> bool:
    encounter = character.data.get("encounter") or {}
    return bool(encounter.get("present", True))


def list_actors(db: Session, campaign_id: str, kind: str | None = None, present: bool | None = None) -> list[Character]:
    items = db.scalars(select(Character).where(Character.campaign_id == campaign_id)).all()
    if kind:
        items = [item for item in items if actor_type(item) == kind]
    if present is not None:
        items = [item for item in items if is_present(item) == present]
    return items


def roleplay_brief(character: Character) -> dict:
    data = character.data or {}
    return {
        "id": character.id,
        "name": character.character_name,
        "actor_type": actor_type(character),
        "present": is_present(character),
        "roleplay": copy.deepcopy(data.get("roleplay") or {}),
        "story_role": copy.deepcopy(data.get("story_role") or {}),
        "combat": copy.deepcopy(data.get("combat") or {}),
        "conditions": copy.deepcopy(data.get("conditions") or []),
    }


def set_presence(db: Session, character: Character, present: bool, scene: str = "") -> None:
    from app.services import update_character

    update_character(
        db, character, {"encounter": {"present": present, "scene": scene}},
        f"{'entered' if present else 'left'} scene {scene}".strip(), "actor_presence",
    )


def update_roleplay(db: Session, character: Character, roleplay: dict, story_role: dict, encounter: dict) -> None:
    from app.services import update_character

    update_character(
        db, character, {"roleplay": roleplay, "story_role": story_role, "encounter": encounter},
        "updated DM actor roleplay profile", "actor_roleplay_update",
    )
