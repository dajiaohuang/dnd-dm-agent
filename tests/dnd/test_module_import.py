"""Module document import and static-content snapshot boundaries."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import select

from nanobot.dnd.db import (
    CampaignService,
    CampaignSnapshotService,
    Database,
    ModuleImportService,
    sqlite_database_url,
)
from nanobot.dnd.db.models import ModuleChapter, ModuleSource, SceneIndex, SceneState
from nanobot.dnd.modules.search import ModuleSearchService


class FakeEmbedder:
    model_name = "test/module-fake"
    dimensions = 3

    def encode(self, texts):
        return [
            [1.0, 0.0, 0.0]
            if "infernal contract" in text.casefold() or "devil bargain" in text.casefold()
            else [0.0, 1.0, 0.0]
            for text in texts
        ]


def test_module_import_builds_chapters_and_scene_indexes(tmp_path: Path) -> None:
    module_dir = tmp_path / "module"
    module_dir.mkdir()
    (module_dir / "Avernus - Ch.1 Arrival.md").write_text(
        "# Arrival\n\n## Gate\nDescription.\n\n### Guard\nDetails.\n\n## Tavern\nTalk.\n",
        encoding="utf-8",
    )
    (module_dir / "Avernus - Ch.2 Road.md").write_text(
        "# Road\n\n## Journey\nTravel.\n",
        encoding="utf-8",
    )
    database = Database(sqlite_database_url(tmp_path / "module.db"))
    database.upgrade_schema()
    try:
        CampaignService(database).create("Campaign", campaign_id="one")
        imported = ModuleImportService(database).import_path(
            "one", module_dir, name="Avernus", embed=False
        )
        assert imported.chapter_count == 2
        assert imported.scene_count == 3
        assert imported.is_active is True
        with database.transaction() as session:
            chapters = list(
                session.scalars(
                    select(ModuleChapter).order_by(ModuleChapter.order_index)
                )
            )
            assert [chapter.chapter_key for chapter in chapters] == ["ch.1", "ch.2"]
            assert [chapter.status for chapter in chapters] == ["current", "locked"]
            scenes = list(session.scalars(select(SceneIndex).order_by(SceneIndex.title)))
            assert [scene.title for scene in scenes] == ["Gate", "Journey", "Tavern"]
            gate_id = scenes[0].id
            assert chapters[0].content.startswith("# Arrival")
        gate = ModuleImportService(database).read_scene("one", gate_id)
        assert gate.scene_title == "Gate"
        assert gate.content.startswith("## Gate")
        assert "### Guard" in gate.content
    finally:
        database.dispose()


def test_snapshot_keeps_scene_progress_but_not_static_module_content(
    tmp_path: Path,
) -> None:
    chapter_file = tmp_path / "Avernus - Ch.1 Arrival.md"
    chapter_file.write_text("# Arrival\n\n## Gate\nDescription.\n", encoding="utf-8")
    database = Database(sqlite_database_url(tmp_path / "snapshot.db"))
    database.upgrade_schema()
    try:
        CampaignService(database).create("Campaign", campaign_id="one")
        ModuleImportService(database).import_path(
            "one", chapter_file, name="Avernus", embed=False
        )
        with database.transaction() as session:
            scene = session.scalar(select(SceneIndex))
            session.add(
                SceneState(
                    id="scene_state_one",
                    campaign_id="one",
                    scene_id=scene.id,
                    current_room="Gate",
                    explored_percent=25,
                    state_json={"clues": ["tracks"]},
                )
            )

        snapshots = CampaignSnapshotService(database)
        saved = snapshots.create("one", label="At the gate")
        payload = snapshots.get("one", saved.slot)
        assert payload["schema_version"] == 3
        assert "module_sources" not in payload["state"]
        assert "module_chapters" not in payload["state"]
        assert "scene_indexes" not in payload["state"]
        assert payload["state"]["scene_states"][0]["explored_percent"] == 25

        with database.transaction() as session:
            chapter = session.scalar(select(ModuleChapter))
            chapter.title = "Static title changed outside the save"
            state = session.get(SceneState, "scene_state_one")
            state.explored_percent = 90
            state.state_json = {"clues": ["tracks", "key"]}

        snapshots.restore("one", saved.slot)
        with database.transaction() as session:
            assert session.scalar(select(ModuleSource)) is not None
            assert session.scalar(select(ModuleChapter)).title == (
                "Static title changed outside the save"
            )
            restored = session.get(SceneState, "scene_state_one")
            assert restored.explored_percent == 25
            assert restored.state_json == {"clues": ["tracks"]}
    finally:
        database.dispose()


def test_module_import_builds_dense_chunks_and_searches_semantically(
    tmp_path: Path,
) -> None:
    chapter_file = tmp_path / "Avernus - Ch.1.md"
    chapter_file.write_text(
        "# Avernus\n\n## Contract\nAn infernal contract binds the signer.\n\n"
        "## Market\nMerchants sell ordinary supplies.\n",
        encoding="utf-8",
    )
    database = Database(sqlite_database_url(tmp_path / "dense.db"))
    database.upgrade_schema()
    try:
        CampaignService(database).create("Campaign", campaign_id="one")
        imported = ModuleImportService(
            database, embedder=FakeEmbedder()
        ).import_path("one", chapter_file, name="Avernus")
        assert imported.chunk_count == 2
        assert imported.embeddings == 2
        search = ModuleSearchService(database, embedder=FakeEmbedder())
        hits = search.search("devil bargain", campaign_id="one", dense=True)
        assert hits[0].scene_title == "Contract"
        assert hits[0].channels == ("dense",)
        assert "infernal contract" in search.expand(hits[0].chunk_id)["text"]
    finally:
        database.dispose()


def test_pdf_attachment_is_converted_before_module_import(tmp_path: Path) -> None:
    pdf = tmp_path / "Avernus - Ch.1.pdf"
    pdf.write_bytes(b"%PDF-test")
    converted_paths: list[Path] = []

    def convert(path: Path) -> str:
        converted_paths.append(path)
        return "# Converted Chapter\n\n## Arrival\nConverted PDF content.\n"

    database = Database(sqlite_database_url(tmp_path / "pdf.db"))
    database.upgrade_schema()
    try:
        CampaignService(database).create("Campaign", campaign_id="one")
        imported = ModuleImportService(database, converter=convert).import_path(
            "one", pdf, name="Avernus", embed=False
        )
        assert imported.chapter_count == 1
        assert converted_paths == [pdf]
        with database.transaction() as session:
            assert "Converted PDF content" in session.scalar(select(ModuleChapter)).content
    finally:
        database.dispose()
