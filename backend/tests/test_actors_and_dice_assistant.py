from fastapi.testclient import TestClient

from app.commands import route_command
from app.main import app


def test_dice_assistant_natural_command_alias():
    assert route_command("骰娘模式").name == "enter_dice_assistant"


def test_dm_actors_roleplay_presence_and_dice_assistant(monkeypatch):
    captured = {}
    dice_captured = {}

    def fake_chat(messages):
        captured["context"] = messages[1]["content"]
        return "The innkeeper answers in a careful whisper."

    def fake_dice_chat(messages, temperature=0.2):
        dice_captured["system"] = messages[0]["content"]
        dice_captured["context"] = messages[1]["content"]
        return "工具回答：Athletics 检定使用力量调整值。"

    monkeypatch.setattr("app.services.chat_completion", fake_chat)
    monkeypatch.setattr("app.dice_assistant.chat_completion", fake_dice_chat)
    with TestClient(app) as client:
        campaign = client.post("/campaigns", json={
            "name": "Actors and Dice", "description": "A campaign at the old inn.",
            "config": {"scene": "Inn"},
        }).json()
        campaign_id = campaign["id"]
        player = client.post("/characters/build", json={
            "campaign_id": campaign_id, "player_name": "Player", "character_name": "Hero",
            "class_name": "Fighter", "actor_type": "player",
            "abilities": {"str": 16, "dex": 12, "con": 14, "int": 10, "wis": 10, "cha": 10},
            "skill_proficiencies": ["athletics"],
            "inventory": [{"item_id": "potion_healing", "name": "Potion of Healing", "quantity": 1}],
            "features": [{"name": "Second Wind"}],
            "spells": ["Fireball"],
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
        assert client.post(f"/chat/{campaign_id}", json={"session_id": "dice", "message": "/save"}).json()["ok"]

        check = client.post(f"/chat/{campaign_id}", json={
            "session_id": "dice", "player_id": "player", "character_id": player["id"], "message": "力量检定 优势",
        }).json()
        assert check["kind"] == "dice_assistant"
        assert check["rolls"][0]["modifier"] == 3
        inventory = client.post(f"/chat/{campaign_id}", json={
            "session_id": "dice", "player_id": "player", "character_id": player["id"], "message": "查看背包",
        }).json()
        assert "Potion of Healing" in inventory["narration"]
        capabilities = client.post(f"/chat/{campaign_id}", json={
            "session_id": "dice", "player_id": "player", "character_id": player["id"], "message": "我有什么技能能放？",
        }).json()
        assert "athletics +5" in capabilities["narration"]
        assert "Second Wind" in capabilities["narration"]
        assert "火球术" in capabilities["narration"]
        client.post(f"/campaigns/{campaign_id}/events", json={
            "session_id": "dice", "event_type": "dm_note", "content": "SECRET_DRAGON_PLAN",
            "actors": [], "metadata": {}, "visibility": "dm_only",
        })
        tool_answer = client.post(f"/chat/{campaign_id}", json={
            "session_id": "dice", "player_id": "player", "character_id": player["id"],
            "message": "运动检定应该加什么？",
        }).json()
        assert "工具回答" in tool_answer["narration"]
        assert not tool_answer["rolls"]
        assert not tool_answer["state_changes"]
        assert "禁止推进或编造剧情" in dice_captured["system"]
        assert "roleplay_instructions" not in dice_captured["context"]
        assert "planned_actions" not in dice_captured["context"]
        assert '"name": "Actors and Dice"' in dice_captured["context"]
        assert '"scene": "Inn"' in dice_captured["context"]
        assert "SECRET_DRAGON_PLAN" not in dice_captured["context"]
        potion_question = client.post(f"/chat/{campaign_id}", json={
            "session_id": "dice", "player_id": "player", "character_id": player["id"],
            "message": "治疗药水能恢复多少？",
        }).json()
        assert not potion_question["state_changes"]
        assert client.get(f"/characters/{player['id']}").json()["data"]["inventory"][0]["quantity"] == 1
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
        status_during_setup = client.post(f"/chat/{campaign_id}", json={
            "session_id": "dice", "message": "查看战役",
        }).json()
        assert status_during_setup["command"] == "status"
        assert "Actors and Dice" in status_during_setup["narration"]
        assert "当前场景：Inn" in status_during_setup["narration"]
        not_started = client.post(f"/chat/{campaign_id}", json={
            "session_id": "dice", "message": "角色：Hero、Mira、Ogre",
        }).json()
        assert not (not_started.get("data") or {}).get("turn_state")

        client.post(f"/chat/{campaign_id}", json={"session_id": "dice", "message": "开始战斗"})
        exited_pending_combat = client.post(f"/chat/{campaign_id}", json={
            "session_id": "dice", "message": "退出战斗",
        }).json()
        assert exited_pending_combat["command"] == "end_combat"
        assert not client.post(f"/chat/{campaign_id}", json={
            "session_id": "dice", "message": "角色：Hero、Mira、Ogre",
        }).json().get("data", {}).get("turn_state")

        client.post(f"/chat/{campaign_id}", json={"session_id": "dice", "message": "开始战斗"})
        question_during_setup = client.post(f"/chat/{campaign_id}", json={
            "session_id": "dice", "message": "我现在在哪个战役？",
        }).json()
        assert "工具回答" in question_during_setup["narration"]
        started = client.post(f"/chat/{campaign_id}", json={
            "session_id": "dice", "message": "角色：Hero、Mira、Ogre；优势：Hero；劣势：Ogre",
        }).json()
        assert started["data"]["turn_state"]["combat"]
        modes = {item["name"]: item["initiative_mode"] for item in started["data"]["turn_state"]["participants"]}
        assert modes == {"Hero": "advantage", "Mira": "normal", "Ogre": "disadvantage"}
        client.post(f"/chat/{campaign_id}", json={"session_id": "dice", "message": "/endcombat"})

        client.post(f"/chat/{campaign_id}", json={"session_id": "dice", "message": "开始战斗"})
        exited_dice = client.post(f"/chat/{campaign_id}", json={
            "session_id": "dice", "message": "退出骰娘模式",
        }).json()
        assert exited_dice["command"] == "exit_dice_assistant"
        assert client.get(f"/campaigns/{campaign_id}/status").json()["play_style"] == "campaign"
