"""Tests for bounded audit-backed state restoration."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import func, select

from nanobot.dnd.db import Database, sqlite_database_url
from nanobot.dnd.db.models import Campaign, StateRevision, ToolAudit, WorldState
from nanobot.dnd.db.undo import NothingToUndoError, UndoLimitExceededError, UndoManager


@pytest.fixture
def database(tmp_path: Path) -> Database:
    db = Database(sqlite_database_url(tmp_path / "undo.db"))
    db.upgrade_schema()
    try:
        yield db
    finally:
        db.dispose()


def _seed_two_world_changes(database: Database) -> None:
    with database.transaction() as session:
        session.add(Campaign(id="campaign_1", name="Avernus"))
        session.flush()
        session.add(
            WorldState(
                id="world_1",
                campaign_id="campaign_1",
                state_json={"day_in_game": 3},
                state_version=3,
            )
        )
        session.flush()

        session.add(
            ToolAudit(
                id="audit_1",
                request_id="request_1",
                campaign_id="campaign_1",
                tool_name="dnd_world",
                engine_function="code.state.world.advance_day",
                result_json={"day_in_game": 2},
                state_version=2,
            )
        )
        session.flush()
        session.add(
            StateRevision(
                id="revision_1",
                campaign_id="campaign_1",
                tool_audit_id="audit_1",
                aggregate_type="world",
                aggregate_key="world_1",
                engine_function="code.state.world.advance_day",
                state_version=2,
                before_state_json={"day_in_game": 1},
                after_state_json={"day_in_game": 2},
            )
        )
        session.flush()

        session.add(
            ToolAudit(
                id="audit_2",
                request_id="request_2",
                campaign_id="campaign_1",
                tool_name="dnd_world",
                engine_function="code.state.world.advance_day",
                result_json={"day_in_game": 3},
                state_version=3,
            )
        )
        session.flush()
        session.add(
            StateRevision(
                id="revision_2",
                campaign_id="campaign_1",
                tool_audit_id="audit_2",
                aggregate_type="world",
                aggregate_key="world_1",
                engine_function="code.state.world.advance_day",
                state_version=3,
                before_state_json={"day_in_game": 2},
                after_state_json={"day_in_game": 3},
            )
        )


def test_undo_restores_multiple_audits_up_to_limit(database: Database) -> None:
    _seed_two_world_changes(database)

    result = UndoManager(database, max_undo=2).undo(
        "campaign_1", count=2, actor_id="dm_1"
    )

    assert result.count == 2
    assert result.reverted_audit_ids == ("audit_2", "audit_1")
    with database.transaction() as session:
        world = session.get(WorldState, "world_1")
        undo_count = session.scalar(
            select(func.count()).select_from(ToolAudit).where(ToolAudit.tool_name == "dnd_undo")
        )
        assert world.state_json == {"day_in_game": 1}
        assert world.state_version == 5
        assert undo_count == 2


def test_undo_limit_is_enforced_before_writing(database: Database) -> None:
    _seed_two_world_changes(database)

    with pytest.raises(UndoLimitExceededError, match="configured limit is 1"):
        UndoManager(database, max_undo=1).undo("campaign_1", count=2)

    with database.transaction() as session:
        assert session.get(WorldState, "world_1").state_json == {"day_in_game": 3}


def test_same_audit_cannot_be_undone_twice(database: Database) -> None:
    _seed_two_world_changes(database)
    manager = UndoManager(database, max_undo=2)
    manager.undo("campaign_1", count=2)

    with pytest.raises(NothingToUndoError):
        manager.undo("campaign_1", count=1)
