"""Campaign lifecycle, USER.md projection, and management CLI tests."""

from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy import select

from nanobot.dnd.db import (
    CampaignService,
    CampaignSnapshotService,
    CharacterService,
    Database,
    sqlite_database_url,
)
from nanobot.dnd.db.cli import main
from nanobot.dnd.db.models import ToolAudit
from nanobot.dnd.db.user_context import read_player_roles, write_player_roles


def test_campaign_service_creates_initial_aggregates_and_archives(tmp_path: Path) -> None:
    database = Database(sqlite_database_url(tmp_path / "campaigns.db"))
    database.upgrade_schema()
    try:
        service = CampaignService(database)
        created = service.create("Avernus", campaign_id="avernus", module_name="BGDIA")
        assert created.id == "avernus"
        assert created.status == "active"
        assert service.list(status="active") == [created]
        assert service.set_status("avernus", "archived").status == "archived"
        assert service.list(status="active") == []
    finally:
        database.dispose()


def test_user_md_updates_only_the_campaign_block(tmp_path: Path) -> None:
    user_md = tmp_path / "USER.md"
    user_md.write_text("# USER.md\n\n- Timezone: Asia/Shanghai\n", encoding="utf-8")

    write_player_roles(tmp_path, "one", "## 战役玩家角色\n\n- Alice：Hero")
    write_player_roles(tmp_path, "two", "## 战役玩家角色\n\n- Bob：Wizard")
    write_player_roles(tmp_path, "one", "## 战役玩家角色\n\n- Alice：Paladin")

    text = user_md.read_text(encoding="utf-8")
    assert "Timezone: Asia/Shanghai" in text
    assert "Alice：Paladin" in text
    assert "Alice：Hero" not in text
    assert "Bob：Wizard" in text
    assert read_player_roles(tmp_path, "one").endswith("Alice：Paladin")


def test_character_service_persists_character_and_audit(tmp_path: Path) -> None:
    database = Database(sqlite_database_url(tmp_path / "characters.db"))
    database.upgrade_schema()
    try:
        CampaignService(database).create("Avernus", campaign_id="avernus")
        created = CharacterService(database).create(
            "avernus",
            "Kalen",
            player_name="Alice",
            class_name="Wizard",
            level=3,
            hp=18,
            max_hp=18,
            armor_class=14,
            sheet_json={"stats": {"int": 16}},
        )
        assert created.party_id is not None
        assert CharacterService(database).list("avernus") == [created]
        with database.transaction() as session:
            audit = session.scalar(select(ToolAudit).where(ToolAudit.campaign_id == "avernus"))
            assert audit is not None
            assert audit.tool_name == "dnd_character_create"
            assert audit.result_json == {"character_id": created.id}
    finally:
        database.dispose()


def test_cli_character_is_in_next_snapshot(tmp_path: Path, capsys) -> None:
    url = sqlite_database_url(tmp_path / "character-cli.db")
    common = ["--database-url", url]
    assert main(common + ["campaign", "create", "--id", "one", "--name", "One"]) == 0
    capsys.readouterr()
    sheet = tmp_path / "hero.json"
    sheet.write_text(json.dumps({"class_name": "Rogue", "hp": {"current": 9, "max": 10}, "ac": 14}), encoding="utf-8")
    assert main(common + ["character", "create", "--campaign", "one", "--name", "Hero", "--player", "Alice", "--sheet-file", str(sheet)]) == 0
    character = json.loads(capsys.readouterr().out)
    assert character["class_name"] == "Rogue"
    assert character["hp"] == 9
    assert main(common + ["save", "create", "--campaign", "one", "--label", "ready"]) == 0
    capsys.readouterr()
    database = Database(url)
    try:
        payload = CampaignSnapshotService(database).get("one", 1)
        assert [item["name"] for item in payload["state"]["characters"]] == ["Hero"]
    finally:
        database.dispose()


def test_cli_campaign_save_load_and_user_projection(
    tmp_path: Path, capsys
) -> None:
    url = sqlite_database_url(tmp_path / "cli.db")
    common = ["--database-url", url]
    assert main(common + ["campaign", "create", "--id", "one", "--name", "One"]) == 0
    capsys.readouterr()

    write_player_roles(tmp_path, "one", "## 战役玩家角色\n\n- Alice：Hero")
    assert (
        main(
            common
            + [
                "save",
                "create",
                "--campaign",
                "one",
                "--label",
                "start",
                "--workspace",
                str(tmp_path),
            ]
        )
        == 0
    )
    saved = json.loads(capsys.readouterr().out)
    assert saved["slot"] == 1

    write_player_roles(tmp_path, "one", "## 战役玩家角色\n\n- Alice：Someone Else")
    assert (
        main(
            common
            + [
                "save",
                "load",
                "--campaign",
                "one",
                "--slot",
                "1",
                "--workspace",
                str(tmp_path),
            ]
        )
        == 0
    )
    loaded = json.loads(capsys.readouterr().out)
    assert loaded["campaign_id"] == "one"
    assert read_player_roles(tmp_path, "one").endswith("Alice：Hero")
