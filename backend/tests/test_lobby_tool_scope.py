from types import SimpleNamespace

from app.db.models import Campaign
from app.db.database import SessionLocal
from app.lobby_sessions import get_lobby_session
from app.main import app
from app.qq_bindings import active_napcat_campaign
from app.services import resolve_chat
from app import llm_loop
from app.tools.command_tools import handle_exit_to_lobby, tools_for_scope
from app.tools.lobby_tools import handle_create_campaign_now, handle_set_lobby_state
from fastapi.testclient import TestClient


def _tool_names(campaign: Campaign | None) -> set[str]:
    return {tool["function"]["name"] for tool in tools_for_scope(campaign, False, "")}


def test_lobby_exposes_preparation_tools_but_not_combat_tools():
    campaign = Campaign(
        id="camp_lobby_test",
        name="Lobby test",
        system_version="DND_5E_2014",
        config={"play_style": "lobby"},
    )

    names = _tool_names(campaign)

    assert {
        "read_attachment",
        "complete_character_sheet",
        "generate_content",
        "execute_plan",
        "check_background_tasks",
        "switch_campaign",
        "set_lobby_state",
        "resolve_lobby_option",
    } <= names
    assert {"combat_attack", "apply_damage", "end_turn"}.isdisjoint(names)


def test_tool_loop_preserves_schema_arguments(monkeypatch):
    captured = {}

    def handler(**kwargs):
        captured.update(kwargs)
        return {"ok": True, "narration": "done"}

    tool_call = SimpleNamespace(
        id="call_1",
        function=SimpleNamespace(
            name="test_lobby_arguments",
            arguments='{"state":{"dm_confirmed":true},"option_number":2,"campaign_name":"新战役"}',
        ),
    )
    responses = iter([
        SimpleNamespace(content=None, tool_calls=[tool_call]),
        SimpleNamespace(content="完成", tool_calls=[]),
    ])

    monkeypatch.setitem(llm_loop.TOOL_HANDLERS, "test_lobby_arguments", handler)
    monkeypatch.setattr(llm_loop, "tools_for_scope", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(llm_loop, "chat_completion", lambda *_args, **_kwargs: next(responses))

    result = llm_loop.execute_llm_with_tools(
        db=object(), campaign=None, messages=[], message_context={"sender_id": "123"},
    )

    assert result["narration"] == "完成"
    assert captured["state"] == {"dm_confirmed": True}
    assert captured["option_number"] == 2
    assert captured["campaign_name"] == "新战役"


def test_campaignless_lobby_persists_state_and_history(monkeypatch):
    session_id = "napcat_group_903107519_2480933622"
    captured_messages = []

    def fake_chat(messages, **_kwargs):
        captured_messages.extend(messages)
        return "已读取上一轮设定，可以继续保存。"

    with TestClient(app), SessionLocal() as db:
        state = {
            "generated_setting": {
                "name": "黯影潮汐",
                "description": "黯影裂隙再次扩张。",
            },
            "pending_options": [
                {"id": 1, "action": "create_campaign", "label": "传统英雄团"},
            ],
        }
        result = handle_set_lobby_state(
            db=db, campaign=None, session_id=session_id, state=state,
            message_context={"platform": "napcat", "group_id": "903107519", "sender_id": "2480933622"},
        )
        assert result["ok"] is True

        monkeypatch.setattr(llm_loop, "chat_completion", fake_chat)
        reply = resolve_chat(
            db, None, session_id, None, "先保存", mode="lobby",
            message_context={"platform": "napcat", "group_id": "903107519", "sender_id": "2480933622"},
        )

        assert reply["narration"] == "已读取上一轮设定，可以继续保存。"
        assert any("黯影潮汐" in str(message.get("content")) for message in captured_messages)
        stored = get_lobby_session(db, session_id)
        assert stored.state["generated_setting"]["name"] == "黯影潮汐"
        assert stored.messages[-2]["content"] == "先保存"

        created = handle_create_campaign_now(db=db, campaign=None, session_id=session_id)
        assert created["ok"] is True
        assert active_napcat_campaign(db).name == "黯影潮汐"


def test_campaign_creation_is_revealed_only_after_exit_to_lobby():
    with TestClient(app), SessionLocal() as db:
        campaign = Campaign(
            id="camp_cross_mode_test",
            name="北境之门",
            system_version="DND_5E_2014",
            config={"play_style": "dice_assistant", "dice_dm_qq_user_id": "2480933622"},
        )
        db.add(campaign)
        db.commit()

        before = _tool_names(campaign)
        assert "exit_to_lobby" in before
        assert "create_campaign_from_prompt" not in before
        assert "create_campaign_now" not in before

        result = handle_exit_to_lobby(db=db, campaign=campaign)
        assert result["ok"] is True

        after = _tool_names(campaign)
        assert "create_campaign_from_prompt" in after
        assert "create_campaign_now" in after
        assert campaign.config["lobby_state"]["dm_confirmed"] is True
