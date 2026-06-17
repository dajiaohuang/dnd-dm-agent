import tempfile
from pathlib import Path

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
        lambda db, campaign, session_id, character_id, text, actor_id, is_dm, message_context=None: (
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


def test_napcat_group_reaction_notification_ats_eligible_players(monkeypatch):
    class FakeClient:
        self_id = "123"

    monkeypatch.setattr("app.main.NapCatClient.from_settings", lambda: FakeClient())
    monkeypatch.setattr("app.main.download_attachments", lambda client, payload: (Path(tempfile.mkdtemp()), [], []))
    monkeypatch.setattr(
        "app.main.process_message",
        lambda *args, **kwargs: {
            "narration": "Action declared; no roll yet.",
            "data": {"reaction_notifications": [
                {"qq_user_id": "456", "name": "Hero", "options": ["Shield"]},
                {"qq_user_id": "789", "name": "Guard", "options": ["Parry"]},
            ]},
        },
    )
    monkeypatch.setattr(settings, "napcat_campaign_id", "campaign_001")
    with TestClient(app) as client:
        client.post("/demo/bootstrap")
        response = client.post("/napcat/callback", json={
            "post_type": "message", "message_type": "group", "group_id": 88, "user_id": 999,
            "message": [{"type": "at", "data": {"qq": "123"}}, {"type": "text", "data": {"text": "attack"}}],
        })
        ats = [item["data"]["qq"] for item in response.json()["reply"] if item["type"] == "at"]
        assert ats == ["456", "789"]


def test_napcat_group_generic_task_mentions(monkeypatch):
    class FakeClient:
        self_id = "123"

    monkeypatch.setattr("app.main.NapCatClient.from_settings", lambda: FakeClient())
    monkeypatch.setattr("app.main.download_attachments", lambda client, payload: (Path(tempfile.mkdtemp()), [], []))
    monkeypatch.setattr(
        "app.main.process_message",
        lambda *args, **kwargs: {
            "narration": "Need more character fields.",
            "data": {"mentions": [{"user_id": "456", "text": "请补充你的车卡。"}]},
        },
    )
    monkeypatch.setattr(settings, "napcat_campaign_id", "campaign_001")
    monkeypatch.setattr(settings, "napcat_token", "")

    with TestClient(app) as client:
        client.post("/demo/bootstrap")
        response = client.post("/napcat/callback", json={
            "post_type": "message", "message_type": "group", "group_id": 88, "user_id": 999,
            "message": [{"type": "at", "data": {"qq": "123"}}, {"type": "text", "data": {"text": "车卡"}}],
        })
        assert response.status_code == 200
        reply = response.json()["reply"]
        assert reply[1] == {"type": "at", "data": {"qq": "456"}}
        assert "请补充你的车卡" in reply[2]["data"]["text"]


def test_napcat_campaign_edit_task_keeps_platform_scope(monkeypatch):
    class FakeClient:
        self_id = "123"

    monkeypatch.setattr("app.main.NapCatClient.from_settings", lambda: FakeClient())
    monkeypatch.setattr("app.main.download_attachments", lambda client, payload: (Path(tempfile.mkdtemp()), [], []))
    monkeypatch.setattr(settings, "napcat_campaign_id", "campaign_001")
    monkeypatch.setattr(settings, "napcat_token", "")
    monkeypatch.setattr(settings, "napcat_dm_user_ids", "456")

    with TestClient(app) as client:
        client.post("/demo/bootstrap")
        response = client.post("/napcat/callback", json={
            "post_type": "message", "message_type": "group", "group_id": 88, "user_id": 456,
            "message": [{"type": "at", "data": {"qq": "123"}}, {"type": "text", "data": {"text": "/editcampaign"}}],
        })
        assert response.status_code == 200
        tasks = client.get("/campaigns/campaign_001/tasks", params={"task_type": "campaign_edit"}).json()
        assert tasks[0]["platform"] == "napcat"
        assert tasks[0]["chat_id"] == "88"
        assert tasks[0]["owner_user_id"] == "456"


def test_napcat_active_campaign_switches_bound_character(monkeypatch):
    used = {}

    class FakeClient:
        self_id = "123"

    monkeypatch.setattr("app.main.NapCatClient.from_settings", lambda: FakeClient())
    monkeypatch.setattr("app.main.download_attachments", lambda client, payload: (Path(tempfile.mkdtemp()), [], []))
    monkeypatch.setattr(
        "app.main.process_message",
        lambda db, campaign, session_id, character_id, text, **kwargs: (
            used.update(campaign_id=campaign.id, character_id=character_id) or {"narration": "ok"}
        ),
    )
    monkeypatch.setattr(settings, "napcat_token", "")

    with TestClient(app) as client:
        client.post("/demo/bootstrap")
        campaign = client.post("/campaigns", json={"name": "Switched Campaign"}).json()
        character = client.post("/characters", json={
            "campaign_id": campaign["id"], "player_name": "Player",
            "character_name": "Switched Hero", "data": {},
        }).json()
        client.put("/napcat/bindings/456", json={
            "campaign_id": campaign["id"], "character_id": character["id"],
        })
        client.put(f"/napcat/active-campaign/{campaign['id']}")
        response = client.post("/napcat/callback", json={
            "post_type": "message", "message_type": "private", "user_id": 456,
            "message": [{"type": "text", "data": {"text": "check"}}],
        })
        assert response.status_code == 200
        assert used == {"campaign_id": campaign["id"], "character_id": character["id"]}
        client.put("/napcat/active-campaign/campaign_001")


def test_natural_switch_active_campaign_command_updates_napcat_active():
    with TestClient(app) as client:
        client.post("/demo/bootstrap")
        created = client.post("/campaigns", json={"name": "灰烬潮汐"}).json()
        switched = client.post("/chat/campaign_001", json={
            "session_id": "switch_campaign",
            "message": "切换到战役 灰烬潮汐",
        }).json()
        assert switched["command"] == "switch_active_campaign"
        assert client.get("/napcat/active-campaign").json()["id"] == created["id"]


def test_napcat_campaign_creation_in_dice_mode_routes_follow_up_to_new_campaign(monkeypatch):
    monkeypatch.setattr(settings, "napcat_dm_user_ids", "456")
    monkeypatch.setattr(settings, "napcat_token", "")

    class FakeClient:
        self_id = "123"

    monkeypatch.setattr("app.main.NapCatClient.from_settings", lambda: FakeClient())
    monkeypatch.setattr("app.main.download_attachments", lambda client, payload: (Path(tempfile.mkdtemp()), [], []))

    with TestClient(app) as client:
        client.post("/demo/bootstrap")
        client.patch("/campaigns/campaign_001", json={"config": {
            "napcat_active": True,
            "play_style": "dice_assistant",
            "dice_dm_qq_user_id": "456",
            "pending_generated_campaign_name": "灰烬潮汐",
        }})
        created = client.post("/napcat/callback", json={
            "post_type": "message",
            "message_type": "private",
            "user_id": 456,
            "message": [{"type": "text", "data": {"text": "创建新战役"}}],
        })
        assert created.status_code == 200
        new_campaign_id = created.json()["result"]["data"]["campaign"]["id"]
        client.post(f"/campaigns/{new_campaign_id}/setting-drafts", json={
            "operation": "create",
            "category": "npc",
            "name": "Harbor Master",
            "proposal": {
                "summary": "Controls entry to the harbor.",
                "content": {"combat": {"armor_class": 12, "max_hp": 9, "current_hp": 9}},
            },
        })
        client.post(f"/campaigns/{new_campaign_id}/setting-drafts/publish")
        client.patch("/campaigns/campaign_001", json={"config": {
            "napcat_active": True,
            "play_style": "dice_assistant",
            "dice_dm_qq_user_id": "456",
        }})
        routed = client.post("/napcat/callback", json={
            "post_type": "message",
            "message_type": "private",
            "user_id": 456,
            "message": [{"type": "text", "data": {"text": "/createnpcs"}}],
        })
        assert routed.status_code == 200
        assert routed.json()["result"]["command"] == "create_npc_cards_from_settings"
        assert "1 张 NPC/怪物角色卡" in routed.json()["result"]["narration"]
        assert client.get("/napcat/active-campaign").json()["id"] == new_campaign_id


def test_campaign_confirmed_dm_has_napcat_dm_permission(monkeypatch):
    used = {}

    class FakeClient:
        self_id = "123"

    monkeypatch.setattr("app.main.NapCatClient.from_settings", lambda: FakeClient())
    monkeypatch.setattr("app.main.download_attachments", lambda client, payload: (Path(tempfile.mkdtemp()), [], []))
    monkeypatch.setattr(
        "app.main.process_message",
        lambda *args, **kwargs: used.update(is_dm=kwargs["is_dm"]) or {"narration": "ok"},
    )
    monkeypatch.setattr(settings, "napcat_dm_user_ids", "")
    monkeypatch.setattr(settings, "napcat_token", "")

    with TestClient(app) as client:
        client.post("/demo/bootstrap")
        client.patch("/campaigns/campaign_001", json={"config": {
            "napcat_active": True, "play_style": "dice_assistant", "dice_dm_qq_user_id": "888888",
        }})
        response = client.post("/napcat/callback", json={
            "post_type": "message", "message_type": "private", "user_id": 888888,
            "message": [{"type": "text", "data": {"text": "status"}}],
        })
        assert response.status_code == 200
        assert used["is_dm"]


def test_napcat_group_context_includes_reply_mentions_and_history(monkeypatch):
    used = {}

    class FakeClient:
        self_id = "123"

        def get_message(self, message_id):
            assert str(message_id) == "77"
            return {"message": [{"type": "text", "data": {"text": "Hero found a silver key."}}]}

        def get_group_history(self, group_id, count=20):
            assert str(group_id) == "88"
            return [
                {
                    "message_id": 70,
                    "user_id": 456,
                    "time": 100,
                    "message": [{"type": "text", "data": {"text": "The door is locked."}}],
                },
            ]

    monkeypatch.setattr("app.main.NapCatClient.from_settings", lambda: FakeClient())
    monkeypatch.setattr("app.main.download_attachments", lambda client, payload: (Path(tempfile.mkdtemp()), [], []))
    monkeypatch.setattr(settings, "napcat_campaign_id", "campaign_001")
    monkeypatch.setattr(settings, "napcat_token", "")

    with TestClient(app) as client:
        client.post("/demo/bootstrap")
        client.post("/chat/campaign_001", json={"session_id": "dice", "message": "/diceassistant"})
        monkeypatch.setattr(
            "app.main.process_message",
            lambda *args, **kwargs: used.update(kwargs.get("message_context") or {}) or {"narration": "ok"},
        )
        response = client.post("/napcat/callback", json={
            "post_type": "message",
            "message_type": "group",
            "message_id": 78,
            "group_id": 88,
            "user_id": 456,
            "message": [
                {"type": "reply", "data": {"id": "77"}},
                {"type": "at", "data": {"qq": "123"}},
                {"type": "at", "data": {"qq": "999"}},
                {"type": "text", "data": {"text": " 要"}},
            ],
        })
        assert response.status_code == 200
        assert used["current_text"] == "@999 要"
        assert used["reply_text"] == "Hero found a silver key."
        assert used["mentioned_user_ids"] == ["999"]
        assert used["group_history"][0]["text"] == "The door is locked."
