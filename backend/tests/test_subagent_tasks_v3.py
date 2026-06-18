from types import SimpleNamespace

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db.database import SessionLocal
from app.db.models import Campaign, CampaignSetting, TaskSession
from app.main import app
from app.subagent_runner import run_subagent_task
from app.tools.command_tools import handle_generate_setting


def test_generate_setting_uses_supported_role_and_persists_result(monkeypatch):
    monkeypatch.setattr("app.subagent_runner.enqueue_subagent_task", lambda _task_id: None)

    with TestClient(app), SessionLocal() as db:
        campaign = Campaign(
            id="camp_subagent_v3",
            name="黯潮群岛",
            system_version="DND_5E_2014",
            config={"play_style": "lobby"},
        )
        db.add(campaign)
        db.commit()

        queued = handle_generate_setting(
            db=db, campaign=campaign, category="location", count=3,
            theme="神秘岛屿", session_id="napcat_group_903107519_2480933622",
            user_id="2480933622",
            message_context={
                "platform": "napcat", "group_id": "903107519",
                "sender_id": "2480933622",
            },
        )
        assert queued["ok"] is True
        task = db.scalar(select(TaskSession).where(TaskSession.campaign_id == campaign.id))
        assert task.proposal_data["agent_role"] == "content_writer"
        assert task.owner_user_id == "2480933622"
        assert task.platform == "napcat"

        monkeypatch.setattr(
            "app.subagent_runner.chat_completion",
            lambda *_args, **_kwargs: '[{"name":"沉钟岛","description":"岛心埋有失落钟塔。"}]',
        )
        run_subagent_task(task.id)
        db.expire_all()

        task = db.get(TaskSession, task.id)
        assert task.status == "ready_to_review"
        assert task.proposal_data["result"]["count"] == 3
        settings = db.scalars(select(CampaignSetting).where(CampaignSetting.campaign_id == campaign.id)).all()
        assert len(settings) == 3
        assert settings[0].name == "沉钟岛"


def test_exhausted_tool_loop_returns_tool_result(monkeypatch):
    from app import llm_loop

    tool_call = SimpleNamespace(
        id="call",
        function=SimpleNamespace(name="status", arguments="{}"),
    )
    monkeypatch.setattr(llm_loop, "tools_for_scope", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(
        llm_loop, "chat_completion",
        lambda *_args, **_kwargs: SimpleNamespace(content=None, tool_calls=[tool_call]),
    )
    monkeypatch.setitem(
        llm_loop.TOOL_HANDLERS, "status",
        lambda **_kwargs: {"ok": True, "narration": "后台任务已成功入队。"},
    )

    result = llm_loop.execute_llm_with_tools(db=object(), campaign=None, messages=[])

    assert result["narration"] == "后台任务已成功入队。"
