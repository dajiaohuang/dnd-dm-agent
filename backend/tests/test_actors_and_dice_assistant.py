from fastapi.testclient import TestClient

from app.commands import route_command
from app.config import settings
from app.main import app


def test_dice_assistant_natural_command_alias():
    # natural-language phrases removed: LLM handles them now via function-calling tools.
    # Only exact /slash commands remain as fast-path.
    assert route_command("/骰娘").name == "enter_dice_assistant"


def test_dice_assistant_automatically_executes_requested_roll(monkeypatch):
    monkeypatch.setattr(
        "app.dice_assistant.chat_completion",
        lambda *args, **kwargs: "Please roll 1d20+4 to make the check.",
    )
    with TestClient(app) as client:
        campaign = client.post("/campaigns", json={
            "name": "Dice Automatic Roll",
            "config": {"play_style": "dice_assistant"},
        }).json()
        result = client.post(f"/chat/{campaign['id']}", json={
            "session_id": "dice_auto", "message": "Resolve this unusual contest.",
        }).json()
        assert result["rolls"][0]["formula"] == "1d20+4"
        assert "骰娘已自动投掷" in result["narration"]


def test_dice_assistant_combat_preferences_do_not_enable_noncombat_narration(monkeypatch):
    prompts = []
    responses = iter([
        "建议使用掩体。",
        "火光映入眼帘，攻击命中。",
    ])

    def fake_chat(messages, **kwargs):
        prompts.append(messages[0]["content"])
        return next(responses)

    monkeypatch.setattr("app.dice_assistant.chat_completion", fake_chat)
    with TestClient(app) as client:
        campaign = client.post("/campaigns", json={
            "name": "Dice Optional Output",
            "config": {"play_style": "dice_assistant"},
        }).json()
        client.post(f"/chat/{campaign['id']}", json={
            "session_id": "dice_optional", "message": "/combatadviceon",
        })
        client.post(f"/chat/{campaign['id']}", json={
            "session_id": "dice_optional", "message": "/combatroleplayon",
        })

        advice = client.post(f"/chat/{campaign['id']}", json={
            "session_id": "dice_optional", "message": "How should this be handled?",
        }).json()
        roleplay = client.post(f"/chat/{campaign['id']}", json={
            "session_id": "dice_optional", "message": "Describe the attack?",
        }).json()
        assert "建议使用掩体" not in advice["narration"]
        assert "火光映入眼帘" not in roleplay["narration"]
        assert all("战斗扮演与战斗建议开关在战斗外无效" in prompt for prompt in prompts)
        assert all("不得描述环境" in prompt and "裁定行动在世界中的后果" in prompt for prompt in prompts)


def test_hosted_actor_action_always_includes_roleplay(monkeypatch):
    captured = {}

    def fake_chat(messages, **kwargs):
        captured["system"] = messages[0]["content"]
        captured["context"] = messages[1]["content"]
        return "亡语鬼婆低语着挥出枯爪。"

    monkeypatch.setattr("app.dice_assistant.chat_completion", fake_chat)
    with TestClient(app) as client:
        campaign = client.post("/campaigns", json={
            "name": "Hosted Actor Roleplay",
            "config": {
                "play_style": "campaign",
                "campaign_combat_roleplay_enabled": False,
            },
        }).json()
        witch = client.post("/characters/build", json={
            "campaign_id": campaign["id"], "player_name": "DM", "character_name": "亡语鬼婆",
            "class_name": "Wizard", "actor_type": "monster",
            "abilities": {"str": 8, "dex": 10, "con": 12, "int": 16, "wis": 14, "cha": 14},
            "roleplay": {"voice": "像枯叶摩擦般低语。", "combat_behavior": "诅咒最虚弱的敌人。"},
        }).json()
        client.patch(f"/campaigns/{campaign['id']}", json={"config": {
            "play_style": "campaign",
            "campaign_combat_roleplay_enabled": False,
            "runtime_mode": "turn_based",
            "turn_state": {
                "combat": True, "round": 1, "turn_index": 0,
                "participants": [
                    {"character_id": witch["id"], "name": "亡语鬼婆", "actor_type": "monster",
                     "initiative": {"total": 18, "modifier": 0}, "reaction_available": True},
                ],
            },
        }})

        result = client.post(f"/chat/{campaign['id']}", json={
            "session_id": "hosted", "message": "亡语鬼婆攻击。",
        }).json()

        assert "低语着挥出枯爪" in result["narration"]
        assert "当前行动者是托管角色" in captured["system"]
        assert "无论战斗扮演开关是否开启" in captured["system"]
        assert '"hosted_actor_profile"' in captured["context"]
        assert "像枯叶摩擦般低语" in captured["context"]


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
    monkeypatch.setattr(settings, "napcat_dm_user_ids", "777777")
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
            "notes": {"private_player_note": "Hero fears deep water."},
            "roleplay": {"voice": "Measured and formal."},
            "story_role": {"personal_goal": "Find the lost banner."},
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
        assert entered["data"]["dm_qq_user_id"] == "777777"
        dm_bindings = client.get("/napcat/bindings", params={"campaign_id": campaign_id}).json()
        assert {(item["qq_user_id"], item["character_id"]) for item in dm_bindings if item["qq_user_id"] == "777777"} == {
            ("777777", npc["id"]), ("777777", monster["id"]),
        }
        assert "777777" not in client.get(f"/characters/{player['id']}").json()["data"]["integrations"]["qq_user_ids"]
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
        assert "禁止给出行动建议" in dice_captured["system"]
        assert "禁止任何扮演文字" in dice_captured["system"]
        assert "roleplay_instructions" not in dice_captured["context"]
        assert "planned_actions" not in dice_captured["context"]
        assert "Hero fears deep water." in dice_captured["context"]
        assert "Measured and formal." not in dice_captured["context"]
        assert "Find the lost banner." not in dice_captured["context"]
        assert '"active_effects"' in dice_captured["context"]
        assert '"derived"' in dice_captured["context"]
        assert '"ability_modifiers"' in dice_captured["context"]
        assert '"name": "Actors and Dice"' in dice_captured["context"]
        assert '"scene": "Inn"' in dice_captured["context"]
        assert "SECRET_DRAGON_PLAN" not in dice_captured["context"]
        mechanical_values = client.post(f"/chat/{campaign_id}", json={
            "session_id": "dice", "player_id": "player", "character_id": player["id"],
            "message": "我的AC和属性调整值是多少？",
        }).json()
        assert "AC 11" in mechanical_values["narration"]
        assert "力量 16（+3）" in mechanical_values["narration"]
        assert "敏捷 12（+1）" in mechanical_values["narration"]
        monkeypatch.setattr("app.dice_assistant.chat_completion", lambda *args, **kwargs: "建议你下一步去调查周围。")
        rejected_advice = client.post(f"/chat/{campaign_id}", json={
            "session_id": "dice", "player_id": "player", "character_id": player["id"],
            "message": "接下来怎么处理？",
        }).json()
        assert "建议" not in rejected_advice["narration"]
        assert "周围" not in rejected_advice["narration"]
        monkeypatch.setattr("app.dice_assistant.chat_completion", fake_dice_chat)
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
        assert "未读取前文" in declined["narration"]

        setup = client.post(f"/chat/{campaign_id}", json={"session_id": "dice", "message": "开始战斗"}).json()
        assert "哪些角色参战" in setup["narration"]
        status_during_setup = client.post(f"/chat/{campaign_id}", json={
            "session_id": "dice", "message": "/status",
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
            "session_id": "dice", "message": "/endcombat",
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
        current = started["data"]["turn_state"]["participants"][0]
        target_lookup = client.post(f"/chat/{campaign_id}", json={
            "session_id": "dice",
            "character_id": current["character_id"] if current["actor_type"] == "player" else None,
            "message": "攻击Ogre时读取目标AC和HP是多少？",
        }).json()
        assert "工具回答" in target_lookup["narration"]
        assert '"combat_participant_cards"' in dice_captured["context"]
        assert '"target_actor_cards"' in dice_captured["context"]
        assert '"name": "Ogre"' in dice_captured["context"]
        assert '"armor_class": 11' in dice_captured["context"]
        assert '"max_hp": 59' in dice_captured["context"]
        client.post(f"/chat/{campaign_id}", json={"session_id": "dice", "message": "/endcombat"})

        client.post(f"/chat/{campaign_id}", json={"session_id": "dice", "message": "开始战斗"})
        exited_dice = client.post(f"/chat/{campaign_id}", json={
            "session_id": "dice", "message": "退出骰娘模式",
        }).json()
        assert exited_dice["command"] == "exit_dice_assistant"
        assert client.get(f"/campaigns/{campaign_id}/status").json()["play_style"] == "campaign"
        dm_bindings = client.get("/napcat/bindings", params={"campaign_id": campaign_id}).json()
        assert {(item["qq_user_id"], item["character_id"], item.get("note")) for item in dm_bindings if item["qq_user_id"] == "777777"} == {
            ("777777", npc["id"], "campaign_dm_hosted"),
            ("777777", monster["id"], "campaign_dm_hosted"),
        }


def test_dice_assistant_asks_for_dm_when_uncertain(monkeypatch):
    monkeypatch.setattr(settings, "napcat_dm_user_ids", "")
    with TestClient(app) as client:
        campaign = client.post("/campaigns", json={"name": "Needs DM"}).json()
        npc = client.post("/characters/build", json={
            "campaign_id": campaign["id"], "player_name": "DM", "character_name": "Guide",
            "class_name": "Rogue", "actor_type": "npc",
            "abilities": {"str": 10, "dex": 10, "con": 10, "int": 10, "wis": 10, "cha": 10},
        }).json()
        entered = client.post(f"/chat/{campaign['id']}", json={
            "session_id": "dice", "player_id": "player", "message": "/diceassistant",
        }).json()
        assert entered["data"]["dm_confirmation_pending"]
        assert "谁是 DM" in entered["narration"]
        assert "DM是 QQ号" in entered["narration"]
        confirmed = client.post(f"/chat/{campaign['id']}", json={
            "session_id": "dice", "player_id": "player", "message": "DM是 888888",
        }).json()
        assert confirmed["data"]["dm_qq_user_id"] == "888888"
        assert client.get("/napcat/bindings/888888", params={
            "campaign_id": campaign["id"], "character_id": npc["id"],
        }).json()["character_id"] == npc["id"]


def test_dice_dm_confirmation_can_be_cancelled_or_bypassed_by_safe_commands(monkeypatch):
    monkeypatch.setattr(settings, "napcat_dm_user_ids", "")
    with TestClient(app) as client:
        campaign = client.post("/campaigns", json={"name": "Needs DM Escape"}).json()
        entered = client.post(f"/chat/{campaign['id']}", json={
            "session_id": "dice", "player_id": "player", "message": "/diceassistant",
        }).json()
        assert entered["data"]["dm_confirmation_pending"]

        status = client.post(f"/chat/{campaign['id']}", json={
            "session_id": "dice", "player_id": "player", "message": "/status",
        }).json()
        assert status["command"] == "status"

        cancelled = client.post(f"/chat/{campaign['id']}", json={
            "session_id": "dice", "player_id": "player", "message": "退出",
        }).json()
        assert "已取消本次 DM 确认" in cancelled["narration"]


def test_dice_assistant_explains_missing_character_binding():
    with TestClient(app) as client:
        campaign = client.post("/campaigns", json={
            "name": "Unbound Dice Campaign",
            "config": {"play_style": "dice_assistant"},
        }).json()
        result = client.post(f"/chat/{campaign['id']}", json={
            "session_id": "unbound", "player_id": "player", "message": "我的AC和调整值是多少？",
        }).json()
        assert "Unbound Dice Campaign" in result["narration"]
        assert "没有绑定角色卡" in result["narration"]
        client.post(f"/chat/{campaign['id']}", json={"session_id": "unbound", "message": "开始战斗"})
        refused = client.post(f"/chat/{campaign['id']}", json={
            "session_id": "unbound", "message": "角色：亡语鬼婆",
        }).json()
        assert "没有找到任何与参战名单匹配的实体角色卡" in refused["narration"]


def test_dice_assistant_blocks_campaign_admin_natural_language_from_rule_fallback():
    with TestClient(app) as client:
        campaign = client.post("/campaigns", json={
            "name": "Dice Admin Guard",
            "config": {"play_style": "dice_assistant"},
        }).json()
        result = client.post(f"/chat/{campaign['id']}", json={
            "session_id": "dice", "message": "/publishsettings",
        }).json()
        assert result["command"] == "publish_settings"
        assert "骰娘模式不管理预设战役剧情或设定编辑" in result["narration"]


def test_natural_effect_actions_apply_and_expire_in_combat():
    with TestClient(app) as client:
        campaign = client.post("/campaigns", json={"name": "Effects Campaign"}).json()
        character = client.post("/characters/build", json={
            "campaign_id": campaign["id"], "player_name": "Player", "character_name": "Hero",
            "class_name": "Fighter",
            "abilities": {"str": 16, "dex": 12, "con": 14, "int": 10, "wis": 10, "cha": 10},
            "inventory": [{
                "name": "Guardian Ring", "equipped": True,
                "effects": [{"name": "Ring Guard", "modifiers": [
                    {"target": "combat.armor_class", "operation": "add", "value": 1},
                ]}],
            }],
        }).json()
        added = client.post(f"/chat/{campaign['id']}", json={
            "session_id": "effects", "character_id": character["id"],
            "message": "给Hero添加效果：AC +2，持续 1 回合，仅战斗",
        }).json()
        assert added["command"] == "add_effect"
        assert added["data"]["effect"]["scope"] == "combat_only"

        client.post(f"/chat/{campaign['id']}", json={"session_id": "effects", "message": "/combat"})
        client.post(f"/chat/{campaign['id']}", json={"session_id": "effects", "message": "/diceassistant"})
        during = client.post(f"/chat/{campaign['id']}", json={
            "session_id": "effects", "character_id": character["id"], "message": "我的AC和调整值是多少？",
        }).json()
        assert "AC 14" in during["narration"]
        listed = client.post(f"/chat/{campaign['id']}", json={
            "session_id": "effects", "character_id": character["id"], "message": "查看效果",
        }).json()
        assert "Guardian Ring" not in listed["narration"]
        assert "AC +2" in listed["narration"]

        client.post(f"/chat/{campaign['id']}", json={"session_id": "effects", "message": "/next"})
        after = client.post(f"/chat/{campaign['id']}", json={
            "session_id": "effects", "character_id": character["id"], "message": "查看效果",
        }).json()
        assert "AC +2" not in after["narration"]
        assert after["data"]["effective"]["combat"]["armor_class"] == 12


def test_damage_resolves_and_breaks_concentration(monkeypatch):
    monkeypatch.setattr(
        "app.effect_actions.roll_dice",
        lambda formula: {"formula": formula, "rolls": [1], "modifier": 2, "total": 3},
    )
    with TestClient(app) as client:
        campaign = client.post("/campaigns", json={"name": "Concentration Campaign"}).json()
        character = client.post("/characters/build", json={
            "campaign_id": campaign["id"], "player_name": "Player", "character_name": "Cleric",
            "class_name": "Cleric",
            "abilities": {"str": 10, "dex": 10, "con": 14, "int": 10, "wis": 16, "cha": 10},
        }).json()
        client.post(f"/chat/{campaign['id']}", json={
            "session_id": "concentration", "character_id": character["id"], "message": "给Cleric添加效果 祝福术",
        })
        client.post(f"/chat/{campaign['id']}", json={"session_id": "concentration", "message": "/diceassistant"})
        damaged = client.post(f"/chat/{campaign['id']}", json={
            "session_id": "concentration", "character_id": character["id"], "message": "伤害 6",
        }).json()
        assert "专注中断" in damaged["narration"]
        assert client.get(f"/characters/{character['id']}").json()["data"]["active_effects"] == []


def test_combat_reaction_window_pauses_roll_and_turn_until_player_decides(monkeypatch):
    monkeypatch.setattr(settings, "napcat_dm_user_ids", "999")
    monkeypatch.setattr(
        "app.campaign_turns.roll_dice",
        lambda formula: {
            "formula": formula, "rolls": [10], "modifier": int(formula.replace("1d20", "") or 0),
            "total": 10 + int(formula.replace("1d20", "") or 0),
        },
    )
    monkeypatch.setattr(
        "app.dice_assistant.chat_completion",
        lambda *args, **kwargs: "Goblin declares a claw attack against Hero. Please roll 1d20+5 for the attack.",
    )
    with TestClient(app) as client:
        campaign = client.post("/campaigns", json={"name": "Reaction Campaign"}).json()
        hero = client.post("/characters/build", json={
            "campaign_id": campaign["id"], "player_name": "Player", "character_name": "Hero",
            "class_name": "Wizard",
            "abilities": {"str": 8, "dex": 12, "con": 12, "int": 16, "wis": 10, "cha": 10},
            "features": [{"name": "Shield reaction", "activation": "reaction"}],
        }).json()
        goblin = client.post("/characters/build", json={
            "campaign_id": campaign["id"], "player_name": "DM", "character_name": "Goblin",
            "class_name": "Fighter", "actor_type": "monster",
            "abilities": {"str": 14, "dex": 20, "con": 10, "int": 8, "wis": 8, "cha": 8},
            "features": [{"name": "Shield reaction", "activation": "reaction"}],
        }).json()
        client.put("/napcat/bindings/456", json={
            "campaign_id": campaign["id"], "character_id": hero["id"],
        })
        client.post(f"/chat/{campaign['id']}", json={"session_id": "reaction", "message": "/diceassistant"})
        client.post(f"/chat/{campaign['id']}", json={"session_id": "reaction", "message": "开始战斗"})
        started = client.post(f"/chat/{campaign['id']}", json={
            "session_id": "reaction", "message": "角色：Hero、Goblin",
        }).json()
        assert started["data"]["turn_state"]["participants"][0]["character_id"] == goblin["id"]

        declared = client.post(f"/chat/{campaign['id']}", json={
            "session_id": "reaction", "message": "Goblin attacks Hero",
        }).json()
        assert not declared["rolls"]
        assert "尚未投掷" in declared["narration"]
        assert declared["data"]["reaction_notifications"][0]["qq_user_id"] == "456"
        assert client.get(f"/campaigns/{campaign['id']}/status").json()["turn_state"]["participants"][0]["character_id"] == goblin["id"]

        resolved = client.post(f"/chat/{campaign['id']}", json={
            "session_id": "reaction", "player_id": "player", "character_id": hero["id"], "message": "不反应",
        }).json()
        assert resolved["rolls"][0]["formula"] == "1d20+5"
        assert "无人使用反应" in resolved["narration"]
        assert resolved["turn_notification"]["character_id"] == hero["id"]

        pending_dm_reaction = client.post(f"/chat/{campaign['id']}", json={
            "session_id": "reaction", "player_id": "player", "character_id": hero["id"],
            "message": "Hero attacks Goblin",
        }).json()
        assert not pending_dm_reaction["rolls"]
        assert "Goblin" in pending_dm_reaction["narration"]
        resolved_dm_reaction = client.post(f"/chat/{campaign['id']}", json={
            "session_id": "reaction", "character_id": goblin["id"], "message": "使用Shield reaction",
        }).json()
        assert resolved_dm_reaction["rolls"][0]["formula"] == "1d20+5"
        assert "Goblin使用Shield reaction" in resolved_dm_reaction["narration"]


def test_dm_combat_uses_dice_mechanics_with_private_roleplay_context(monkeypatch):
    captured = {}

    def fake_dice_chat(messages, temperature=0.2):
        captured["system"] = messages[0]["content"]
        captured["context"] = messages[1]["content"]
        return "The witch hisses from behind the standing stone; no roll is required."

    monkeypatch.setattr("app.dice_assistant.chat_completion", fake_dice_chat)
    monkeypatch.setattr(
        "app.services.chat_completion",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("DM combat must use the shared dice mechanics path")),
    )
    with TestClient(app) as client:
        campaign = client.post("/campaigns", json={"name": "Shared Combat"}).json()
        player = client.post("/characters/build", json={
            "campaign_id": campaign["id"], "player_name": "Player", "character_name": "Hero",
            "class_name": "Fighter",
            "abilities": {"str": 16, "dex": 12, "con": 14, "int": 10, "wis": 10, "cha": 10},
        }).json()
        witch = client.post("/characters/build", json={
            "campaign_id": campaign["id"], "player_name": "DM", "character_name": "Whisper Witch",
            "class_name": "Wizard", "actor_type": "monster",
            "abilities": {"str": 8, "dex": 10, "con": 12, "int": 16, "wis": 14, "cha": 14},
            "roleplay": {"voice": "A cracked whisper.", "secrets": ["She serves the northern gate."]},
            "story_role": {"planned_actions": ["Delay the party until moonrise."]},
        }).json()
        client.patch(f"/campaigns/{campaign['id']}", json={"config": {
            "runtime_mode": "turn_based",
            "turn_state": {
                "combat": True, "round": 1, "turn_index": 0,
                "participants": [
                    {"character_id": witch["id"], "name": "Whisper Witch", "actor_type": "monster",
                     "initiative": {"total": 18, "modifier": 0}, "reaction_available": True},
                    {"character_id": player["id"], "name": "Hero", "actor_type": "player",
                     "initiative": {"total": 12, "modifier": 1}, "reaction_available": True},
                ],
            },
        }})
        result = client.post(f"/chat/{campaign['id']}", json={
            "session_id": "shared", "message": "The witch threatens Hero.",
        }).json()
        assert result["narration"].startswith("The witch hisses")
        assert result["events"][0]["event_type"] == "dm_combat_action"
        assert "地下城主" in captured["system"]
        assert "扮演 NPC" in captured["system"]
        assert "A cracked whisper." in captured["context"]
        assert "Delay the party until moonrise." in captured["context"]
        assert '"combat_participant_cards"' in captured["context"]
