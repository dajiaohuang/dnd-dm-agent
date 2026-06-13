from fastapi.testclient import TestClient

from app.commands import route_command
from app.main import app


def test_dice_assistant_natural_command_alias():
    assert route_command("骰娘模式").name == "enter_dice_assistant"


def test_dm_actors_roleplay_presence_and_dice_assistant(monkeypatch):
    captured = {}

    def fake_chat(messages):
        captured["context"] = messages[1]["content"]
        return "The innkeeper answers in a careful whisper."

    monkeypatch.setattr("app.services.chat_completion", fake_chat)
    with TestClient(app) as client:
        campaign = client.post("/campaigns", json={"name": "Actors and Dice"}).json()
        campaign_id = campaign["id"]
        player = client.post("/characters/build", json={
            "campaign_id": campaign_id, "player_name": "Player", "character_name": "Hero",
            "class_name": "Fighter", "actor_type": "player",
            "abilities": {"str": 16, "dex": 12, "con": 14, "int": 10, "wis": 10, "cha": 10},
            "skill_proficiencies": ["athletics"],
            "inventory": [{"item_id": "potion_healing", "name": "Potion of Healing", "quantity": 1}],
        }).json()
        npc = client.post("/characters/build", json={
            "campaign_id": campaign_id, "player_name": "DM", "character_name": "Mira",
            "class_name": "Rogue", "actor_type": "npc",
            "abilities": {"str": 8, "dex": 16, "con": 10, "int": 12, "wis": 14, "cha": 14},
            "roleplay": {
                "public_persona": "A helpful innkeeper.",
                "voice": "Quiet and deliberate.",
                "secrets": ["She is the crown's spy."],
                "roleplay_instructions": "Never reveal the spy identity directly.",
            },
            "story_role": {
                "purpose": "Point the party toward the ruined abbey.",
                "planned_actions": ["Deliver a coded map after earning trust."],
                "triggers": ["The party asks about missing merchants."],
            },
            "encounter": {"present": True, "scene": "Inn"},
        }).json()
        monster = client.post("/characters/build", json={
            "campaign_id": campaign_id, "player_name": "DM", "character_name": "Ogre",
            "class_name": "Fighter", "actor_type": "monster",
            "abilities": {"str": 19, "dex": 8, "con": 16, "int": 5, "wis": 7, "cha": 7},
            "max_hp": 59, "armor_class": 11,
            "roleplay": {"combat_behavior": "Attack the nearest armored target."},
            "story_role": {"purpose": "Guard the ruined abbey."},
            "encounter": {"present": False, "scene": "Ruined abbey"},
        }).json()

        actors = client.get(f"/campaigns/{campaign_id}/actors").json()
        assert {item["data"]["basic"]["actor_type"] for item in actors} == {"player", "npc", "monster"}
        brief = client.get(f"/characters/{npc['id']}/roleplay").json()
        assert brief["roleplay"]["secrets"] == ["She is the crown's spy."]
        assert brief["story_role"]["planned_actions"]

        client.post(f"/chat/{campaign_id}", json={"session_id": "play", "message": "Mira, what do you know?"})
        assert "present_dm_actors" in captured["context"]
        assert "Never reveal the spy identity directly." in captured["context"]

        combat = client.post(f"/chat/{campaign_id}", json={"session_id": "play", "message": "/combat"}).json()
        ids = {item["character_id"] for item in combat["data"]["turn_state"]["participants"]}
        assert npc["id"] in ids
        assert monster["id"] not in ids
        client.post(f"/chat/{campaign_id}", json={"session_id": "play", "message": "/endcombat"})

        client.patch(f"/characters/{monster['id']}/presence", json={"present": True, "scene": "Inn"})
        assert client.get(f"/characters/{monster['id']}/roleplay").json()["present"] is True

        before_events = len(client.get(f"/campaigns/{campaign_id}/events").json())
        entered = client.post(f"/chat/{campaign_id}", json={"session_id": "dice", "message": "/diceassistant"}).json()
        assert entered["ok"]
        assert client.get(f"/campaigns/{campaign_id}/status").json()["play_style"] == "dice_assistant"
        assert client.post(f"/chat/{campaign_id}", json={"session_id": "dice", "message": "/memory anything"}).json()["ok"]
        assert not client.post(f"/chat/{campaign_id}", json={"session_id": "dice", "message": "/save"}).json()["ok"]

        check = client.post(f"/chat/{campaign_id}", json={
            "session_id": "dice", "player_id": "player", "character_id": player["id"], "message": "力量检定 优势",
        }).json()
        assert check["kind"] == "dice_assistant"
        assert check["rolls"][0]["modifier"] == 3
        inventory = client.post(f"/chat/{campaign_id}", json={
            "session_id": "dice", "player_id": "player", "character_id": player["id"], "message": "查看背包",
        }).json()
        assert "Potion of Healing" in inventory["narration"]
        hp = client.post(f"/chat/{campaign_id}", json={
            "session_id": "dice", "player_id": "player", "character_id": player["id"], "message": "伤害 4",
        }).json()
        assert hp["events"][0]["event_type"] == "dice_assistant_action"
        assert hp["state_changes"][0]["before"] - hp["state_changes"][0]["after"] == 4
        assert len(client.get(f"/campaigns/{campaign_id}/events").json()) > before_events
        assert client.get(f"/campaigns/{campaign_id}/memories", params={"query": "伤害"}).json()

        memory_update = client.post(f"/chat/{campaign_id}", json={
            "session_id": "dice", "player_id": "player", "character_id": player["id"], "message": "更新记忆：Hero拿到了银钥匙",
        }).json()
        assert memory_update["data"]["present_actors"]
        assert "要不要读取前面的聊天记录" in memory_update["narration"]
        declined = client.post(f"/chat/{campaign_id}", json={
            "session_id": "dice", "player_id": "player", "character_id": player["id"], "message": "不要",
        }).json()
        assert "不读取前文" in declined["narration"]

        setup = client.post(f"/chat/{campaign_id}", json={"session_id": "dice", "message": "开始战斗"}).json()
        assert "哪些角色参战" in setup["narration"]
        started = client.post(f"/chat/{campaign_id}", json={
            "session_id": "dice", "message": "角色：Hero、Mira、Ogre；优势：Hero；劣势：Ogre",
        }).json()
        assert started["data"]["turn_state"]["combat"]
        modes = {item["name"]: item["initiative_mode"] for item in started["data"]["turn_state"]["participants"]}
        assert modes == {"Hero": "advantage", "Mira": "normal", "Ogre": "disadvantage"}
        client.post(f"/chat/{campaign_id}", json={"session_id": "dice", "message": "/endcombat"})

        client.post(f"/chat/{campaign_id}", json={"session_id": "dice", "message": "/exitdice"})
        assert client.get(f"/campaigns/{campaign_id}/status").json()["play_style"] == "campaign"
