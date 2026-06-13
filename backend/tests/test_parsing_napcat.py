import os
import tempfile
from pathlib import Path

os.environ.setdefault("DATABASE_URL", f"sqlite:///{Path(tempfile.gettempdir()).joinpath('dnd_dm_agent_test_api.db').as_posix()}")
os.environ.setdefault("DATA_DIR", str(Path(__file__).parents[2] / "data"))

from fastapi.testclient import TestClient

from app.config import settings
from app.integrations.napcat import attachment_segments, is_allowed, is_group_at_event, parse_event_text
from app.main import app
from app.parsing.router import parse_files
from app.rag.chunker import chunk_markdown


def test_parse_files_merges_multiple_formats(tmp_path: Path):
    text = tmp_path / "notes.md"
    data = tmp_path / "actors.json"
    text.write_text("# Tavern\nThe guard knows Aric.", encoding="utf-8")
    data.write_text('{"npc": "Mira"}', encoding="utf-8")
    result = parse_files([str(text), str(data)])
    assert result["success_count"] == 2
    assert "The guard knows Aric" in result["content"]
    assert '"npc": "Mira"' in result["content"]


def test_parse_upload_endpoint():
    with TestClient(app) as client:
        response = client.post(
            "/parse/files",
            files=[
                ("files", ("scene.md", b"# Gate\nA locked gate.", "text/markdown")),
                ("files", ("npc.json", b'{"name":"Mira"}', "application/json")),
            ],
            data={"total_max_chars": "2000"},
        )
        assert response.status_code == 200
        assert response.json()["success_count"] == 2


def test_rulebook_upload_becomes_searchable(monkeypatch):
    monkeypatch.setattr("app.services.embed_text", lambda text: None)
    with TestClient(app) as client:
        response = client.post(
            "/parse/rulebooks",
            files=[("files", ("homebrew_rules.md", b"# Moon Magic\nMoon bolts deal radiant starlight damage.", "text/markdown"))],
            data={"system_version": "HOMEBREW_1"},
        )
        assert response.status_code == 200
        assert response.json()["imported_chunks"] == 1
        results = client.get("/rules/search", params={"query": "radiant starlight"}).json()
        assert results[0]["source"] == "homebrew_rules.md"
        assert results[0]["system_version"] == "HOMEBREW_1"


def test_pdf_page_markers_become_sections():
    chunks = chunk_markdown("【第 7 页】\nAttack rules.\n\n[Page 8]\nSpell rules.", "book.pdf")
    assert [chunk["section"] for chunk in chunks] == ["Page 7", "Page 8"]


def test_napcat_segment_helpers():
    payload = {
        "post_type": "message",
        "message_type": "group",
        "message": [
            {"type": "at", "data": {"qq": "123"}},
            {"type": "text", "data": {"text": " inspect this "}},
            {"type": "image", "data": {"file": "abc.jpg", "url": "https://example.com/a.jpg"}},
        ],
    }
    assert is_group_at_event(payload, "123")
    assert parse_event_text(payload, "123") == "inspect this"
    assert attachment_segments(payload)[0]["type"] == "image"


def test_empty_allowlist_allows_everyone_and_group_requires_at(monkeypatch):
    monkeypatch.setattr(settings, "napcat_allowed_user_ids", "")
    monkeypatch.setattr(settings, "napcat_require_group_at", True)
    assert is_allowed({"user_id": 10001})
    assert is_allowed({"user_id": 99999})

    without_at = {
        "post_type": "message", "message_type": "group", "group_id": 88, "user_id": 10001,
        "message": [{"type": "text", "data": {"text": "hello"}}],
    }
    with_at = {
        **without_at,
        "message": [{"type": "at", "data": {"qq": "123"}}, {"type": "text", "data": {"text": "hello"}}],
    }
    assert not is_group_at_event(without_at, "123")
    assert is_group_at_event(with_at, "123")


def test_napcat_callback_to_dm(monkeypatch):
    sent = {}
    used = {}

    class FakeClient:
        self_id = "123"

        def send_private_msg(self, user_id, message):
            sent["user_id"], sent["message"] = user_id, message

        def send_group_msg(self, group_id, message):
            sent["group_id"], sent["message"] = group_id, message

    root = Path(tempfile.mkdtemp(prefix="napcat_test_"))
    attachment = root / "note.txt"
    attachment.write_text("The guard carries a silver key.", encoding="utf-8")
    monkeypatch.setattr("app.main.NapCatClient.from_settings", lambda: FakeClient())
    monkeypatch.setattr("app.main.download_attachments", lambda client, payload: (root, [str(attachment)], []))
    monkeypatch.setattr(
        "app.main.process_message",
        lambda db, campaign, session_id, character_id, text, actor_id, is_dm: (
            used.update(character_id=character_id, actor_id=actor_id, is_dm=is_dm) or {"narration": "ok"}
        ),
    )
    monkeypatch.setattr(settings, "napcat_campaign_id", "campaign_001")
    monkeypatch.setattr(settings, "napcat_character_id", "char_001")
    monkeypatch.setattr(settings, "napcat_token", "")

    with TestClient(app) as client:
        client.post("/demo/bootstrap")
        character = client.post("/characters", json={
            "campaign_id": "campaign_001",
            "player_name": "Bound Player",
            "character_name": "Bound Hero",
            "data": {},
        }).json()
        client.put("/napcat/bindings/456", json={
            "campaign_id": "campaign_001",
            "character_id": character["id"],
        })
        response = client.post("/napcat/callback", json={
            "post_type": "message",
            "message_type": "private",
            "user_id": 456,
            "message": [{"type": "text", "data": {"text": "我检查守卫。"}}],
        })
        assert response.status_code == 200
        assert response.json()["parsed_attachments"]["success_count"] == 1
        assert response.json()["reply"] == "ok"
        assert not sent
        assert used["character_id"] == character["id"]
        assert used["actor_id"] == "456"
        assert not used["is_dm"]


def test_napcat_group_turn_notification_ats_bound_player(monkeypatch):
    sent = []

    class FakeClient:
        self_id = "123"

        def send_group_msg(self, group_id, message):
            sent.append(("message", str(group_id), message))

        def send_group_at(self, group_id, user_id, message):
            sent.append(("at", str(user_id), message))

    monkeypatch.setattr("app.main.NapCatClient.from_settings", lambda: FakeClient())
    monkeypatch.setattr("app.main.download_attachments", lambda client, payload: (Path(tempfile.mkdtemp()), [], []))
    monkeypatch.setattr(
        "app.main.process_message",
        lambda *args, **kwargs: {
            "narration": "Next turn.",
            "turn_notification": {"qq_user_id": "456", "name": "Bound Hero"},
        },
    )
    monkeypatch.setattr(settings, "napcat_campaign_id", "campaign_001")
    monkeypatch.setattr(settings, "napcat_token", "")

    with TestClient(app) as client:
        client.post("/demo/bootstrap")
        response = client.post("/napcat/callback", json={
            "post_type": "message",
            "message_type": "group",
            "group_id": 88,
            "user_id": 999,
            "message": [{"type": "at", "data": {"qq": "123"}}, {"type": "text", "data": {"text": "next"}}],
        })
        assert response.status_code == 200
        assert response.json()["reply"][1] == {"type": "at", "data": {"qq": "456"}}
        assert not sent
