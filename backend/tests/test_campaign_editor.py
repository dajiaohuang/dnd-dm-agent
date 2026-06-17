import time

from fastapi.testclient import TestClient

from app.main import app


def test_campaign_editor_all_phases(monkeypatch):
    captured = {}

    def fake_chat(messages):
        captured["messages"] = messages
        return "The published setting shapes the scene."

    monkeypatch.setattr("app.services.chat_completion", fake_chat)
    monkeypatch.setattr("app.subagent_runner.chat_completion", lambda *args, **kwargs: "后台审核完成。")
    with TestClient(app) as client:
        campaign = client.post("/campaigns", json={"name": "Editor Test"}).json()
        campaign_id = campaign["id"]

        entered = client.post(f"/chat/{campaign_id}", json={"session_id": "edit", "message": "/editcampaign"}).json()
        assert entered["ok"]
        assert client.get(f"/campaigns/{campaign_id}/status").json()["runtime_mode"] == "campaign_edit"

        proposal = client.post(f"/chat/{campaign_id}", json={
            "session_id": "edit",
            "message": "create setting: location | Gray Harbor | A fog-covered trade city",
        }).json()
        assert proposal["kind"] == "campaign_editor"
        assert proposal["data"]["drafts"][0]["status"] == "pending"
        tasks = client.get(f"/campaigns/{campaign_id}/tasks", params={"task_type": "subagent_proposal"}).json()
        assert tasks[0]["parent_task_id"]
        assert tasks[0]["proposal_data"]["agent_role"] == "campaign_setting_reviewer"
        assert proposal["data"]["drafts"][0]["id"] in tasks[0]["proposal_data"]["proposal"]["draft_ids"]
        assert tasks[0]["status"] in {"queued", "running", "ready_to_review"}
        for _ in range(30):
            reviewed = client.get(f"/campaigns/{campaign_id}/tasks/{tasks[0]['id']}").json()
            if reviewed["status"] == "ready_to_review":
                break
            time.sleep(0.05)
        assert reviewed["status"] == "ready_to_review"
        assert reviewed["proposal_data"]["result"]["kind"] == "campaign_setting_review"
        digest = client.post(f"/chat/{campaign_id}", json={
            "session_id": "edit", "message": "查看建议",
        }).json()
        assert digest["command"] == "task_reviews"
        assert "后台子任务结果待审核" in digest["narration"]

        published = client.post(f"/chat/{campaign_id}", json={"session_id": "edit", "message": "/publishsettings"}).json()
        assert len(published["data"]["settings"]) == 1
        setting_id = published["data"]["settings"][0]["id"]
        assert client.get(f"/campaigns/{campaign_id}/settings", params={"query": "fog trade city"}).json()[0]["id"] == setting_id
        assert len(client.get(f"/campaigns/{campaign_id}/setting-history").json()) == 1

        client.post(f"/campaigns/{campaign_id}/setting-drafts", json={
            "operation": "create",
            "category": "npc",
            "name": "Harbor Master",
            "proposal": {
                "summary": "Controls entry to Gray Harbor.",
                "content": {"combat": {"armor_class": 12, "max_hp": 9, "current_hp": 9}},
                "relationships": [{"target_id": setting_id, "type": "located_in"}],
            },
        })
        npc_setting = client.post(f"/campaigns/{campaign_id}/setting-drafts/publish").json()[0]
        npc = client.post(f"/campaigns/{campaign_id}/setting/{npc_setting['id']}/npc-character").json()
        assert npc["data"]["basic"]["actor_type"] == "npc"

        graph = client.get(f"/campaigns/{campaign_id}/setting-graph").json()
        assert len(graph["nodes"]) == 2
        assert graph["edges"][0]["target_id"] == setting_id
        assert client.get(f"/campaigns/{campaign_id}/settings/validate").json()["valid"]

        comment = client.post(f"/campaigns/{campaign_id}/setting-comments", json={
            "setting_id": setting_id, "author_id": "dm", "content": "Add more dock districts.",
        }).json()
        assert comment["content"].startswith("Add more")
        resolved = client.post(f"/campaigns/{campaign_id}/setting-comments/{comment['id']}/resolve").json()
        assert resolved["resolved"] is True

        conflict_event = client.post(f"/campaigns/{campaign_id}/events", json={
            "session_id": "play",
            "event_type": "player_action",
            "content": "Gray Harbor was destroyed by the storm.",
        }).json()
        conflict_drafts = client.get(f"/campaigns/{campaign_id}/setting-drafts").json()
        assert conflict_drafts[0]["target_setting_id"] == setting_id
        assert conflict_drafts[0]["reason"] == f"event_conflict:{conflict_event['id']}"
        assert client.get(f"/campaigns/{campaign_id}/settings/conflicts").json()[0]["event_id"] == conflict_event["id"]
        assert client.post(f"/campaigns/{campaign_id}/setting-drafts/undo").json()["draft"]["status"] == "discarded"

        package = client.get(f"/campaigns/{campaign_id}/package").json()
        target = client.post("/campaigns", json={"name": "Imported Campaign"}).json()
        imported = client.post(f"/campaigns/{target['id']}/package", json={"package": package}).json()
        assert imported["drafts_created"] == 2
        assert len(client.post(f"/campaigns/{target['id']}/setting-drafts/publish").json()) == 2

        template = client.post(f"/campaigns/{target['id']}/templates/mystery").json()
        assert template["drafts_created"] == 3
        assert "classic_fantasy" in client.get("/campaign-setting-templates").json()

        client.post(f"/chat/{campaign_id}", json={"session_id": "edit", "message": "/exitedit"})
        client.post(f"/chat/{campaign_id}", json={"session_id": "play", "message": "I enter Gray Harbor."})
        context = captured["messages"][1]["content"]
        assert "relevant_campaign_settings" in context
        assert "Gray Harbor" in context


def test_campaign_edit_mode_allows_safe_commands_and_exit():
    with TestClient(app) as client:
        campaign = client.post("/campaigns", json={"name": "Editor Escape"}).json()
        entered = client.post(f"/chat/{campaign['id']}", json={
            "session_id": "edit", "message": "/editcampaign",
        }).json()
        assert entered["ok"]

        status = client.post(f"/chat/{campaign['id']}", json={
            "session_id": "edit", "message": "/status",
        }).json()
        assert status["command"] == "status"

        exited = client.post(f"/chat/{campaign['id']}", json={
            "session_id": "edit", "message": "/exitedit",
        }).json()
        assert exited["command"] == "exit_campaign_edit"
