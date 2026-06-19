"""Native imported-module retrieval tool tests."""

from __future__ import annotations

from pathlib import Path

from nanobot.agent.tools.dnd_module import DndModuleTool
from nanobot.dnd.db import (
    CampaignService,
    Database,
    ModuleImportService,
    ModuleProgressService,
    sqlite_database_url,
)
from nanobot.dnd.modules.search import ModuleSearchService


class FakeEmbedder:
    model_name = "test/module-tool"
    dimensions = 3

    def encode(self, texts):
        return [[1.0, 0.0, 0.0] for _ in texts]


async def test_dnd_module_tool_search_expand_and_status(tmp_path: Path) -> None:
    chapter = tmp_path / "Module - Ch.1.md"
    chapter.write_text(
        "# Chapter\n\n## Gate\nThe iron gate is guarded.\n",
        encoding="utf-8",
    )
    database = Database(sqlite_database_url(tmp_path / "tool.db"))
    database.upgrade_schema()
    CampaignService(database).create("Campaign", campaign_id="one")
    tool = DndModuleTool(database, migrate=False)
    tool.import_service = ModuleImportService(database, embedder=FakeEmbedder())
    tool.progress_service = ModuleProgressService(database)
    tool.search_service = ModuleSearchService(database, embedder=FakeEmbedder())
    try:
        imported = await tool.execute(
            action="import",
            campaign_id="one",
            source_path=str(chapter),
            module_name="Module",
        )
        assert imported["embeddings"] == 1

        index = await tool.execute(action="index", campaign_id="one")
        scene_id = index["modules"][0]["chapters"][0]["scenes"][0]["id"]

        status = await tool.execute(action="status")
        assert status["chunks"] == 1
        assert status["embedded_chunks"] == 1

        result = await tool.execute(
            action="search", campaign_id="one", query="iron gate", dense=True
        )
        assert result["hits"][0]["scene_title"] == "Gate"

        expanded = await tool.execute(
            action="expand",
            campaign_id="one",
            chunk_id=result["hits"][0]["chunk_id"],
        )
        assert "iron gate is guarded" in expanded["text"]

        progress = await tool.execute(
            action="set_scene",
            campaign_id="one",
            scene_id=scene_id,
            current_room="Gate",
            explored_percent=25,
            state_json={"clues": ["guard"]},
        )
        assert progress["scene_title"] == "Gate"
        current = await tool.execute(action="current", campaign_id="one")
        assert current["current"]["explored_percent"] == 25
    finally:
        database.dispose()
