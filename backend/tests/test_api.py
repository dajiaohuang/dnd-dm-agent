import time

from fastapi.testclient import TestClient
from app.main import app
from app.db.database import SessionLocal
from app.db.models import Campaign, TaskSession
from app.subagent_runner import run_subagent_task
from app.task_sessions import bump_draft_version, create_subagent_proposal


def test_mvp_closed_loop():
    with TestClient(app) as client:
        assert client.get("/health").json() == {"status": "ok"}
        assert client.post("/ingest/compendium").json()["imported"] >= 3
        assert client.post("/ingest/rules").json()["imported"] >= 1
        demo = client.post("/demo/bootstrap").json()
        character_id = demo["character"]["id"]
        result = client.post("/chat/campaign_001", json={
            "session_id": "test_session", "character_id": character_id, "message": "我喝一瓶治疗药水。"
        }).json()
        assert result["rolls"][0]["formula"] == "2d4+2"
        assert len(result["state_changes"]) == 2
        character = client.get(f"/characters/{character_id}").json()
        assert character["data"]["combat"]["current_hp"] > 9
        assert character["data"]["inventory"][1]["quantity"] == 1
        assert len(client.get(f"/characters/{character_id}/changes").json()) >= 1
        assert len(client.get("/campaigns/campaign_001/events").json()) >= 1
        assert client.post("/campaigns/campaign_001/summaries?session_id=test_session").status_code == 201


def test_dice_validation():
    with TestClient(app) as client:
        assert client.post("/dice/roll", json={"formula": "1d20+5"}).status_code == 200
        assert client.post("/dice/roll", json={"formula": "rm -rf"}).status_code == 400


def test_combat_output_options_are_mode_specific():
    with TestClient(app) as client:
        campaign = client.post("/campaigns", json={"name": "Combat Options"}).json()
        status = client.get(f"/campaigns/{campaign['id']}/status").json()
        assert status["combat_preference_style"] == "campaign"
        assert status["combat_roleplay_enabled"] is True
        assert status["combat_advice_enabled"] is True

        roleplay = client.post(f"/chat/{campaign['id']}", json={
            "session_id": "options", "message": "/combatroleplayoff",
        }).json()
        advice = client.post(f"/chat/{campaign['id']}", json={
            "session_id": "options", "message": "/combatadviceoff",
        }).json()
        assert roleplay["data"]["combat_roleplay_enabled"] is False
        assert advice["data"]["combat_advice_enabled"] is False

        client.post(f"/chat/{campaign['id']}", json={
            "session_id": "options", "message": "/diceassistant",
        })
        status = client.get(f"/campaigns/{campaign['id']}/status").json()
        assert status["combat_preference_style"] == "dice_assistant"
        assert status["combat_roleplay_enabled"] is False
        assert status["combat_advice_enabled"] is False

        client.post(f"/chat/{campaign['id']}", json={
            "session_id": "options", "message": "/combatroleplayon",
        })
        client.post(f"/chat/{campaign['id']}", json={
            "session_id": "options", "message": "/combatadviceon",
        })
        status = client.get(f"/campaigns/{campaign['id']}/status").json()
        assert status["combat_roleplay_enabled"] is True
        assert status["combat_advice_enabled"] is True

        client.post(f"/chat/{campaign['id']}", json={
            "session_id": "options", "message": "/exitdice",
        })
        status = client.get(f"/campaigns/{campaign['id']}/status").json()
        assert status["combat_preference_style"] == "campaign"
        assert status["combat_roleplay_enabled"] is False
        assert status["combat_advice_enabled"] is False


def test_dm_automatically_rolls_and_continues_pending_action(monkeypatch):
    calls = []

    def fake_chat(messages):
        calls.append(messages)
        if len(calls) == 1:
            return "Please roll 1d20+5 to resolve the attack."
        return "Attack total resolved; the action is complete."

    monkeypatch.setattr("app.services.chat_completion", fake_chat)
    with TestClient(app) as client:
        campaign = client.post("/campaigns", json={
            "name": "Automatic Roll",
            "config": {
                "campaign_combat_roleplay_enabled": False,
                "campaign_combat_advice_enabled": False,
                "turn_state": {"combat": True, "round": 1, "turn_index": 0, "participants": []},
            },
        }).json()
        character = client.post("/characters", json={
            "campaign_id": campaign["id"],
            "character_name": "Hero",
            "data": {"basic": {"actor_type": "player"}},
        }).json()
        result = client.post(f"/chat/{campaign['id']}", json={
            "session_id": "automatic_roll",
            "character_id": character["id"],
            "message": "I attack the target.",
        }).json()

        assert result["narration"] == "Attack total resolved; the action is complete."
        assert result["rolls"][0]["formula"] == "1d20+5"
        assert len(calls) == 2
        assert "Combat roleplay prose is disabled" in calls[0][0]["content"]
        assert "Combat advice is disabled" in calls[0][0]["content"]
        assert "Automatic roll" in calls[1][2]["content"]


def test_qq_character_binding_crud():
    with TestClient(app) as client:
        client.post("/demo/bootstrap")
        second_campaign = client.post("/campaigns", json={"name": "Second Campaign"}).json()
        second_character = client.post("/characters", json={
            "campaign_id": second_campaign["id"], "player_name": "Alice",
            "character_name": "Second Hero", "data": {},
        }).json()
        response = client.put("/napcat/bindings/123456789", json={
            "campaign_id": "campaign_001",
            "character_id": "char_001",
            "display_name": "Alice",
            "note": "fighter",
        })
        assert response.status_code == 200
        assert response.json()["character_name"] == "Aric"
        assert client.get("/characters/char_001").json()["data"]["integrations"]["qq_user_ids"] == ["123456789"]
        assert client.get("/napcat/bindings/123456789").json()["character_id"] == "char_001"
        assert len(client.get("/napcat/bindings?campaign_id=campaign_001").json()) >= 1
        second_same_campaign = client.post("/characters", json={
            "campaign_id": "campaign_001", "player_name": "Alice",
            "character_name": "Aric's Familiar", "data": {},
        }).json()
        assert client.put("/napcat/bindings/123456789", json={
            "campaign_id": "campaign_001", "character_id": second_same_campaign["id"],
        }).status_code == 200
        same_qq_bindings = client.get("/napcat/bindings", params={"campaign_id": "campaign_001"}).json()
        assert {
            item["character_id"] for item in same_qq_bindings if item["qq_user_id"] == "123456789"
        } == {"char_001", second_same_campaign["id"]}
        assert client.delete("/napcat/bindings/123456789", params={
            "campaign_id": "campaign_001", "character_id": second_same_campaign["id"],
        }).status_code == 204
        assert client.get("/napcat/bindings/123456789", params={
            "campaign_id": "campaign_001", "character_id": "char_001",
        }).status_code == 200

        switched = client.put(f"/napcat/active-campaign/{second_campaign['id']}").json()
        assert switched["id"] == second_campaign["id"]
        assert client.get("/napcat/active-campaign").json()["id"] == second_campaign["id"]
        client.patch(f"/characters/{second_character['id']}/qq-bindings", json={
            "qq_user_ids": ["123456789", "987654321"],
        })
        updated = client.get(f"/characters/{second_character['id']}").json()
        assert updated["data"]["integrations"]["qq_user_ids"] == ["123456789", "987654321"]
        assert client.get("/napcat/bindings/123456789", params={
            "campaign_id": second_campaign["id"],
        }).json()["character_id"] == second_character["id"]
        assert client.get("/napcat/bindings/987654321").json()["character_id"] == second_character["id"]

        assert client.delete("/napcat/bindings/123456789", params={"campaign_id": "campaign_001"}).status_code == 204
        assert client.get("/characters/char_001").json()["data"]["integrations"]["qq_user_ids"] == []
        assert client.get("/napcat/bindings/123456789", params={"campaign_id": "campaign_001"}).status_code == 404
        assert client.delete(f"/characters/{second_character['id']}").status_code == 204
        assert client.get("/napcat/bindings/987654321", params={
            "campaign_id": second_campaign["id"],
        }).status_code == 404
        client.put("/napcat/active-campaign/campaign_001")


def test_natural_campaign_admin_commands_switch_and_delete_active_campaign():
    with TestClient(app) as client:
        client.post("/demo/bootstrap")
        created = client.post("/chat/campaign_001", json={
            "session_id": "admin", "message": "/新建战役",
        }).json()
        assert created["command"] == "create_campaign_from_prompt"
        new_campaign = created["data"]["campaign"]
        assert client.get("/napcat/active-campaign").json()["id"] == new_campaign["id"]

        deleted = client.post(f"/chat/{new_campaign['id']}", json={
            "session_id": "admin", "message": "/删除战役",
        }).json()
        assert deleted["command"] == "delete_active_campaign"
        assert "已删除当前战役" in deleted["narration"]
        assert client.get("/napcat/active-campaign").json()["id"] == "campaign_001"


def test_create_campaign_from_prompt_inherits_dice_context():
    with TestClient(app) as client:
        client.post("/demo/bootstrap")
        client.patch("/campaigns/campaign_001", json={"config": {
            "scene": "Old Dock",
            "play_style": "dice_assistant",
            "dice_dm_qq_user_id": "456",
            "dice_combat_roleplay_enabled": True,
            "dice_combat_advice_enabled": False,
            "pending_generated_campaign_name": "灰烬潮汐",
        }})
        created = client.post("/chat/campaign_001", json={
            "session_id": "admin", "message": "/新建战役",
        }).json()
        new_campaign = created["data"]["campaign"]
        status = client.get(f"/campaigns/{new_campaign['id']}/status").json()
        campaign = client.get(f"/campaigns/{new_campaign['id']}").json()
        assert created["command"] == "create_campaign_from_prompt"
        assert client.get("/napcat/active-campaign").json()["id"] == new_campaign["id"]
        assert status["play_style"] == "dice_assistant"
        assert campaign["config"]["dice_dm_qq_user_id"] == "456"
        assert campaign["config"]["dice_combat_roleplay_enabled"] is True
        assert campaign["config"]["dice_combat_advice_enabled"] is False


def test_character_builder_and_template_export():
    with TestClient(app) as client:
        client.post("/demo/bootstrap")
        catalog = client.get("/characters/rules/catalog").json()
        assert catalog["point_buy"]["budget"] == 27
        assert catalog["classes"]["wizard"]["hit_die"] == 6
        result = client.post("/characters/build", json={
            "campaign_id": "campaign_001",
            "player_name": "Alice",
            "character_name": "Luna",
            "ancestry": "Elf",
            "background": "Sage",
            "class_name": "Wizard",
            "level": 1,
            "hit_die": 6,
            "abilities": {"str": 8, "dex": 14, "con": 13, "int": 15, "wis": 12, "cha": 10},
            "skill_proficiencies": ["arcana", "history"],
            "spells": ["Fireball"],
            "backstory": "Searching for a lost observatory.",
        })
        assert result.status_code == 201
        character = result.json()
        assert character["point_buy_cost"] == 27
        assert character["data"]["combat"]["proficiency_bonus"] == 2
        assert character["data"]["skills"]["arcana"]["proficient"]
        assert character["data"]["saving_throw_proficiencies"] == ["int", "wis"]
        assert character["data"]["spells"][0]["name"] == "火球术"
        sheet = client.get(f"/characters/{character['id']}/sheet")
        assert sheet.status_code == 200
        assert sheet.headers["content-type"].startswith(
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )


def test_parallel_character_build_sessions_do_not_cross_talk(monkeypatch):
    monkeypatch.setattr("app.subagent_runner.chat_completion", lambda *args, **kwargs: "角色卡审核完成。")
    with TestClient(app) as client:
        campaign = client.post("/campaigns", json={"name": "Parallel Build"}).json()
        first = client.post(f"/chat/{campaign['id']}", json={
            "session_id": "group_a", "player_id": "alice",
            "message": "开始车卡 名字: Luna 职业: Wizard 力量8 敏捷14 智力16",
        }).json()
        second = client.post(f"/chat/{campaign['id']}", json={
            "session_id": "group_b", "player_id": "bob",
            "message": "开始车卡 名字: Brak 职业: Fighter 力量16 体质14",
        }).json()
        assert first["data"]["character_build_session"]["user_id"] == "alice"
        assert second["data"]["character_build_session"]["user_id"] == "bob"

        client.post(f"/chat/{campaign['id']}", json={
            "session_id": "group_a", "player_id": "alice",
            "message": "种族: Elf 背景: Sage 魅力13",
        })
        client.post(f"/chat/{campaign['id']}", json={
            "session_id": "group_b", "player_id": "bob",
            "message": "种族: Dwarf 背景: Soldier 敏捷12",
        })

        alice_draft = client.post(f"/chat/{campaign['id']}", json={
            "session_id": "group_a", "player_id": "alice", "message": "查看车卡",
        }).json()["narration"]
        bob_draft = client.post(f"/chat/{campaign['id']}", json={
            "session_id": "group_b", "player_id": "bob", "message": "/查看车卡",
        }).json()["narration"]
        assert "Luna" in alice_draft and "Brak" not in alice_draft
        assert "Brak" in bob_draft and "Luna" not in bob_draft

        alice_submit = client.post(f"/chat/{campaign['id']}", json={
            "session_id": "group_a", "player_id": "alice", "message": "提交车卡",
        }).json()
        bob_submit = client.post(f"/chat/{campaign['id']}", json={
            "session_id": "group_b", "player_id": "bob", "message": "提交车卡",
        }).json()
        assert alice_submit["data"]["character"]["character_name"] == "Luna"
        assert bob_submit["data"]["character"]["character_name"] == "Brak"
        assert alice_submit["data"]["character"]["data"]["abilities"]["int"] == 16
        assert bob_submit["data"]["character"]["data"]["abilities"]["str"] == 16

        tasks = client.get(f"/campaigns/{campaign['id']}/tasks", params={"status": "committed"}).json()
        committed_builds = [item for item in tasks if item["task_type"] == "character_build"]
        assert {item["owner_user_id"] for item in committed_builds} >= {"alice", "bob"}
        review_tasks = client.get(f"/campaigns/{campaign['id']}/tasks", params={"task_type": "subagent_proposal"}).json()
        character_reviews = [
            item for item in review_tasks
            if item["proposal_data"]["agent_role"] == "character_sheet_reviewer"
        ]
        assert len(character_reviews) >= 2
        for _ in range(30):
            latest = client.get(
                f"/campaigns/{campaign['id']}/tasks/{character_reviews[0]['id']}"
            ).json()
            if latest["status"] == "ready_to_review":
                break
            time.sleep(0.05)
        assert latest["status"] == "ready_to_review"
        assert latest["proposal_data"]["result"]["kind"] == "character_sheet_review"


def test_character_build_can_be_exited_with_natural_cancel_phrases():
    with TestClient(app) as client:
        campaign = client.post("/campaigns", json={"name": "Build Exit"}).json()
        started = client.post(f"/chat/{campaign['id']}", json={
            "session_id": "group", "player_id": "alice", "message": "开始车卡 名字: Luna",
        }).json()
        assert started["command"] == "character_build"

        exited = client.post(f"/chat/{campaign['id']}", json={
            "session_id": "group", "player_id": "alice", "message": "/退出车卡",
        }).json()
        assert exited["command"] == "character_build"
        assert "已取消你的车卡草稿" in exited["narration"]

        status = client.post(f"/chat/{campaign['id']}", json={
            "session_id": "group", "player_id": "alice", "message": "/status",
        }).json()
        assert status["command"] == "status"


def test_character_build_generic_exit_word_cancels_draft():
    with TestClient(app) as client:
        campaign = client.post("/campaigns", json={"name": "Build Exit Word"}).json()
        client.post(f"/chat/{campaign['id']}", json={
            "session_id": "group", "player_id": "alice", "message": "开始车卡",
        })
        exited = client.post(f"/chat/{campaign['id']}", json={
            "session_id": "group", "player_id": "alice", "message": "退出",
        }).json()
        assert "已取消你的车卡草稿" in exited["narration"]


def test_task_session_api_supports_subtask_lifecycle():
    with TestClient(app) as client:
        campaign = client.post("/campaigns", json={"name": "Task API"}).json()
        parent = client.post(f"/campaigns/{campaign['id']}/tasks", json={
            "task_type": "campaign_edit",
            "owner_user_id": "dm",
            "session_id": "edit",
            "proposal_data": {"goal": "create city"},
        }).json()
        child = client.post(f"/campaigns/{campaign['id']}/tasks", json={
            "task_type": "subagent_proposal",
            "owner_user_id": "dm",
            "session_id": "edit",
            "parent_task_id": parent["id"],
            "proposal_data": {"agent_role": "npc_designer", "goal": "draft harbor NPCs"},
            "next_prompt": "Review NPC proposal.",
        }).json()
        assert child["parent_task_id"] == parent["id"]
        active = client.get(f"/campaigns/{campaign['id']}/tasks").json()
        assert {item["id"] for item in active} >= {parent["id"], child["id"]}
        committed = client.patch(f"/campaigns/{campaign['id']}/tasks/{child['id']}", json={
            "status": "committed",
            "created_object_type": "campaign_setting",
            "created_object_id": "setting_123",
        }).json()
        assert committed["status"] == "committed"
        assert committed["created_object_id"] == "setting_123"
        assert not any(item["id"] == child["id"] for item in client.get(f"/campaigns/{campaign['id']}/tasks").json())


def test_subagent_result_marks_stale_when_parent_changes():
    with TestClient(app) as client:
        campaign = client.post("/campaigns", json={"name": "Stale Subagent"}).json()
    with SessionLocal() as db:
        camp = db.get(Campaign, campaign["id"])
        parent = TaskSession(
            id="task_parent_stale",
            campaign_id=camp.id,
            task_type="character_build",
            owner_user_id="alice",
            session_id="stale",
            draft_data={"_meta": {"version": 1}},
        )
        db.add(parent)
        child = create_subagent_proposal(
            db,
            camp,
            parent,
            agent_role="generic_reviewer",
            goal="review stale behavior",
        )
        bump_draft_version(parent)
        db.commit()
        child_id = child.id
    run_subagent_task(child_id)
    with SessionLocal() as db:
        child = db.get(TaskSession, child_id)
        assert child.status == "ready_to_review"
        assert child.proposal_data["source_parent_version"] == 1
        assert child.proposal_data["current_parent_version"] == 2
        assert child.proposal_data["stale"] is True


def test_character_item_schema_and_custom_inventory():
    with TestClient(app) as client:
        client.post("/demo/bootstrap")
        schema = client.get("/characters/items/schema").json()
        assert "custom" in schema["item_types"]
        assert schema["storage_rule"].startswith("Every carried or equipped object")

        result = client.post("/characters/build", json={
            "campaign_id": "campaign_001",
            "character_name": "Artificer",
            "class_name": "Artificer",
            "abilities": {"str": 8, "dex": 14, "con": 13, "int": 15, "wis": 12, "cha": 10},
            "currency": {"gp": 21, "custom": {"guild_credit": 3}},
            "inventory": [{
                "name": "Clockwork Grappling Teapot",
                "item_type": "custom",
                "quantity": 1,
                "equipped": True,
                "equipped_slot": "off_hand",
                "charges": {"current": 2, "maximum": 3, "recharge": "dawn"},
                "effects": [{"effect_type": "movement", "description": "Pulls the bearer 20 feet."}],
                "custom_data": {"brew_temperature": 92, "experimental": True},
            }],
        })
        assert result.status_code == 201
        data = result.json()["data"]
        item = data["inventory"][0]
        assert item["instance_id"].startswith("item_")
        assert item["equipped_slot"] == "off_hand"
        assert item["charges"]["maximum"] == 3
        assert item["custom_data"]["brew_temperature"] == 92
        assert data["currency"]["custom"]["guild_credit"] == 3

        migration = client.post("/campaigns/campaign_001/characters/inventory/normalize").json()
        assert migration["characters_scanned"] >= 2
        assert "characters_updated" in migration


def test_dm_reasoning_receives_campaign_memory(monkeypatch):
    captured = {}

    def fake_chat(messages):
        captured["messages"] = messages
        return "The guard remembers your earlier promise."

    monkeypatch.setattr("app.services.chat_completion", fake_chat)
    with TestClient(app) as client:
        client.post("/demo/bootstrap")
        client.post("/campaigns/campaign_001/events", json={
            "session_id": "another_player_session",
            "event_type": "story",
            "content": "The player promised to return the silver key.",
            "actors": ["char_001"],
            "metadata": {"dm_response": "The guard accepted the promise."},
        })
        client.post("/campaigns/campaign_001/summaries?session_id=memory_test")
        response = client.post("/chat/campaign_001", json={
            "session_id": "memory_test",
            "character_id": "char_001",
            "message": "I approach the guard again.",
        })
        assert response.json()["narration"] == "The guard remembers your earlier promise."
        context = captured["messages"][1]["content"]
        assert "silver key" in context
        assert "char_001" in context
        event = response.json()["events"][0]
        assert event["metadata"]["context_refs"]["character_id"] == "char_001"


def test_spell_lookup_and_dm_spell_context(monkeypatch):
    captured = {}

    def fake_chat(messages):
        captured["context"] = messages[1]["content"]
        return "A bead of flame streaks forward."

    monkeypatch.setattr("app.services.chat_completion", fake_chat)
    with TestClient(app) as client:
        client.post("/demo/bootstrap")
        api_result = client.get("/spells", params={"query": "Fireball", "limit": 1}).json()
        assert api_result[0]["name"] == "火球术"
        direct = client.post("/chat/campaign_001", json={
            "session_id": "spell_test", "player_id": "player", "message": "/法术 火球术",
        }).json()
        assert direct["command"] == "spell_search"
        assert direct["data"]["spells"][0]["english_name"] == "Fireball"
        action = client.post("/chat/campaign_001", json={
            "session_id": "spell_test", "character_id": "char_001", "message": "I cast Fireball.",
        }).json()
        assert action["narration"] == "A bead of flame streaks forward."
        assert "relevant_spells" in captured["context"]
        assert "Fireball" in captured["context"]


def test_campaign_control_commands_and_permissions():
    with TestClient(app) as client:
        client.post("/demo/bootstrap")
        help_result = client.post("/chat/campaign_001", json={
            "session_id": "control_test", "player_id": "player_1", "message": "/帮助",
        }).json()
        assert help_result["kind"] == "command"
        assert help_result["command"] == "help"

        denied = client.post("/chat/campaign_001", json={
            "session_id": "control_test", "player_id": "player_1", "message": "/暂停",
        }).json()
        assert not denied["ok"]
        assert "仅限 DM" in denied["narration"]

        paused = client.post("/chat/campaign_001", json={
            "session_id": "control_test", "message": "/暂停",
        }).json()
        assert paused["ok"]
        assert paused["command"] == "pause"
        assert client.get("/campaigns/campaign_001/status").json()["status"] == "paused"
        assert len(client.get("/campaigns/campaign_001/checkpoints").json()) >= 1

        blocked = client.post("/chat/campaign_001", json={
            "session_id": "control_test", "player_id": "player_1", "message": "I inspect the gate.",
        }).json()
        assert not blocked["ok"]
        assert blocked["command"] == "paused"

        resumed = client.post("/chat/campaign_001", json={
            "session_id": "control_test", "message": "/继续",
        }).json()
        assert resumed["ok"]
        assert client.get("/campaigns/campaign_001/status").json()["status"] == "active"


def test_campaign_memory_three_stage_pipeline(monkeypatch):
    captured = {}

    def fake_graph(state):
        captured["graph_state"] = state
        return {
            "intent": {"intent_type": "character_action"},
            "ruling": {"requires_roll": False},
            "proposed_actions": [{"tool": "append_campaign_event", "args": {}}],
            "memory_write_plan": {"extract_after_event": True, "intent_type": "character_action", "skip": False},
        }

    monkeypatch.setattr("app.services.dm_graph.invoke", fake_graph)
    monkeypatch.setattr("app.services.chat_completion", lambda messages: "The guard accepts your promise.")
    with TestClient(app) as client:
        client.post("/demo/bootstrap")
        old_event = client.post("/campaigns/campaign_001/events", json={
            "session_id": "memory_pipeline",
            "event_type": "story",
            "content": "The silver key was hidden beneath the gate.",
            "actors": ["char_001"],
            "metadata": {},
        }).json()
        backfill = client.post("/campaigns/campaign_001/memories/backfill").json()
        assert backfill["events_scanned"] >= 1
        memories = client.get("/campaigns/campaign_001/memories", params={"query": "silver key"}).json()
        assert any(item["source_event_id"] == old_event["id"] for item in memories)

        result = client.post("/chat/campaign_001", json={
            "session_id": "memory_pipeline",
            "character_id": "char_001",
            "message": "I promise to investigate the missing guard.",
        }).json()
        assert result["narration"] == "The guard accepts your promise."
        assert "memory_context" in captured["graph_state"]
        assert any("silver key" in item["content"] for item in captured["graph_state"]["memory_context"]["memories"])
        threads = client.get("/campaigns/campaign_001/threads").json()
        assert any("promise" in item["description"].lower() for item in threads)
        command = client.post("/chat/campaign_001", json={
            "session_id": "memory_pipeline", "player_id": "player", "message": "/memory silver key",
        }).json()
        assert command["command"] == "memory"


def test_free_turn_based_and_combat_mode_transitions(monkeypatch):
    def fixed_roll(formula):
        modifier = int(formula.replace("1d20", "") or 0)
        return {"formula": formula, "rolls": [10], "modifier": modifier, "total": 10 + modifier}

    monkeypatch.setattr("app.campaign_turns.roll_dice", fixed_roll)
    monkeypatch.setattr("app.services.chat_completion", lambda messages: "Action resolved.")
    with TestClient(app) as client:
        campaign = client.post("/campaigns", json={"name": "Turn Test"}).json()
        campaign_id = campaign["id"]
        player = client.post("/characters", json={
            "campaign_id": campaign_id,
            "character_name": "Hero",
            "data": {
                "basic": {"actor_type": "player"},
                "abilities": {"dex": 12},
                "combat": {"initiative": 1},
            },
        }).json()
        npc = client.post("/characters", json={
            "campaign_id": campaign_id,
            "character_name": "Goblin",
            "data": {
                "basic": {"actor_type": "npc"},
                "abilities": {"dex": 18},
                "combat": {"initiative": 4},
            },
        }).json()

        entered = client.post(f"/chat/{campaign_id}", json={
            "session_id": "turns", "player_id": "player", "message": "/turns",
        }).json()
        assert entered["ok"]
        assert client.get(f"/campaigns/{campaign_id}/status").json()["runtime_mode"] == "turn_based"
        exited = client.post(f"/chat/{campaign_id}", json={
            "session_id": "turns", "player_id": "player", "message": "/free",
        }).json()
        assert exited["ok"]

        combat = client.post(f"/chat/{campaign_id}", json={
            "session_id": "turns", "message": "/combat",
        }).json()
        assert combat["ok"]
        assert combat["data"]["turn_state"]["participants"][0]["character_id"] == npc["id"]

        denied_exit = client.post(f"/chat/{campaign_id}", json={
            "session_id": "turns", "player_id": "player", "message": "/free",
        }).json()
        assert not denied_exit["ok"]
        assert "不能退出回合模式" in denied_exit["narration"]

        player_on_npc_turn = client.post(f"/chat/{campaign_id}", json={
            "session_id": "turns", "player_id": "player", "character_id": player["id"], "message": "I attack.",
        }).json()
        assert not player_on_npc_turn["ok"]
        assert player_on_npc_turn["command"] == "not_your_turn"

        npc_action = client.post(f"/chat/{campaign_id}", json={
            "session_id": "turns", "character_id": player["id"], "message": "The goblin attacks.",
        }).json()
        assert npc_action["events"][0]["actors"] == [npc["id"]]
        assert npc_action["turn_notification"]["character_id"] == player["id"]

        player_action = client.post(f"/chat/{campaign_id}", json={
            "session_id": "turns", "player_id": "player", "character_id": player["id"], "message": "I defend.",
        }).json()
        assert player_action["turn_notification"]["character_id"] == npc["id"]

        ended = client.post(f"/chat/{campaign_id}", json={
            "session_id": "turns", "message": "/endcombat",
        }).json()
        assert ended["ok"]
        assert client.get(f"/campaigns/{campaign_id}/status").json()["runtime_mode"] == "free"
