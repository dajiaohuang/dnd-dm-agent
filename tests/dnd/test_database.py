"""Tests for the migrated D&D domain database."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import inspect, select
from sqlalchemy.exc import IntegrityError

from nanobot.dnd.db import Database, sqlite_database_url
from nanobot.dnd.db.models import Campaign, Character, NapCatCharacterBinding, ToolCallAudit


@pytest.fixture
def database(tmp_path: Path) -> Database:
    db = Database(sqlite_database_url(tmp_path / "dnd.db"))
    db.create_schema()
    try:
        yield db
    finally:
        db.dispose()


def test_create_schema_migrates_domain_tables_without_mode_state(database: Database) -> None:
    tables = set(inspect(database.engine).get_table_names())

    assert {
        "campaigns",
        "characters",
        "napcat_character_bindings",
        "character_change_log",
        "campaign_events",
        "campaign_memories",
        "campaign_settings",
        "dice_audit_log",
        "tool_call_audit",
        "rule_chunks",
        "compendium_entries",
    } <= tables
    assert "lobby_session_states" not in tables


def test_campaign_character_binding_and_audit_round_trip(database: Database) -> None:
    with database.transaction() as session:
        session.add(Campaign(id="campaign_1", name="Avernus", config={"chapter": 1}))
        session.add(
            Character(
                id="character_1",
                campaign_id="campaign_1",
                player_name="player",
                character_name="Kalen",
                data={"hp": 18},
            )
        )
        session.add(
            NapCatCharacterBinding(
                id="binding_1",
                campaign_id="campaign_1",
                qq_user_id="123456",
                character_id="character_1",
            )
        )
        session.add(
            ToolCallAudit(
                id="audit_1",
                campaign_id="campaign_1",
                user_id="123456",
                tool_name="dnd_campaign",
                arguments={"action": "status"},
                result={"ok": True},
            )
        )

    with database.transaction() as session:
        character = session.get(Character, "character_1")
        binding = session.scalar(
            select(NapCatCharacterBinding).where(
                NapCatCharacterBinding.qq_user_id == "123456"
            )
        )
        audit = session.get(ToolCallAudit, "audit_1")

        assert character is not None and character.data["hp"] == 18
        assert binding is not None and binding.character_id == character.id
        assert audit is not None and audit.result == {"ok": True}


def test_binding_uniqueness_is_enforced(database: Database) -> None:
    with database.transaction() as session:
        session.add(Campaign(id="campaign_1", name="Avernus"))
        session.add(
            Character(
                id="character_1",
                campaign_id="campaign_1",
                character_name="Kalen",
                data={},
            )
        )

    with pytest.raises(IntegrityError), database.transaction() as session:
        for binding_id in ("binding_1", "binding_2"):
            session.add(
                NapCatCharacterBinding(
                    id=binding_id,
                    campaign_id="campaign_1",
                    qq_user_id="123456",
                    character_id="character_1",
                )
            )

    with database.transaction() as session:
        assert session.scalar(select(NapCatCharacterBinding)) is None
