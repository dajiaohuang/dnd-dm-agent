"""Campaign lifecycle operations for the D&D database."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select

from nanobot.dnd.db.database import Database
from nanobot.dnd.db.models import Campaign, Party, PlotSummary, WorldState


class CampaignError(RuntimeError):
    """Base error for campaign lifecycle operations."""


class CampaignAlreadyExistsError(CampaignError):
    """A campaign already uses the requested ID."""


class CampaignNotFoundError(CampaignError):
    """The requested campaign does not exist."""


@dataclass(frozen=True)
class CampaignInfo:
    id: str
    name: str
    status: str
    module_name: str | None
    system_version: str
    description: str | None


class CampaignService:
    """Create, inspect, list, and change campaign lifecycle state."""

    def __init__(self, database: Database) -> None:
        self.database = database

    def create(
        self,
        name: str,
        *,
        campaign_id: str | None = None,
        module_name: str | None = None,
        description: str | None = None,
    ) -> CampaignInfo:
        campaign_id = campaign_id or f"campaign_{uuid.uuid4().hex[:16]}"
        with self.database.transaction() as session:
            if session.get(Campaign, campaign_id) is not None:
                raise CampaignAlreadyExistsError(
                    f"campaign already exists: {campaign_id}"
                )
            campaign = Campaign(
                id=campaign_id,
                name=name,
                module_name=module_name,
                description=description,
                config={"user_md_player_roles": ""},
            )
            session.add(campaign)
            session.flush()
            session.add(
                WorldState(
                    id=f"world_{uuid.uuid4().hex}",
                    campaign_id=campaign_id,
                    state_json={
                        "faction_relations": {},
                        "discovered_locations": [],
                        "quest_progress": {"完成": [], "进行中": [], "待触发": []},
                        "key_npc_status": {},
                        "current_chapter": 0,
                        "current_scene": "",
                        "day_in_game": 1,
                    },
                )
            )
            session.add(
                Party(
                    id=f"party_{uuid.uuid4().hex}",
                    campaign_id=campaign_id,
                    state_json={},
                )
            )
            session.add(
                PlotSummary(
                    id=f"summary_{uuid.uuid4().hex}",
                    campaign_id=campaign_id,
                    scope="campaign",
                    summary="",
                )
            )
            return self._info(campaign)

    def list(self, *, status: str | None = None) -> list[CampaignInfo]:
        with self.database.transaction() as session:
            statement = select(Campaign).order_by(Campaign.created_at, Campaign.id)
            if status is not None:
                statement = statement.where(Campaign.status == status)
            return [self._info(campaign) for campaign in session.scalars(statement)]

    def get(self, campaign_id: str) -> CampaignInfo:
        with self.database.transaction() as session:
            campaign = session.get(Campaign, campaign_id)
            if campaign is None:
                raise CampaignNotFoundError(f"campaign not found: {campaign_id}")
            return self._info(campaign)

    def set_status(self, campaign_id: str, status: str) -> CampaignInfo:
        if status not in {"active", "archived"}:
            raise ValueError("status must be active or archived")
        with self.database.transaction() as session:
            campaign = session.get(Campaign, campaign_id)
            if campaign is None:
                raise CampaignNotFoundError(f"campaign not found: {campaign_id}")
            campaign.status = status
            session.flush()
            return self._info(campaign)

    @staticmethod
    def _info(campaign: Campaign) -> CampaignInfo:
        return CampaignInfo(
            id=campaign.id,
            name=campaign.name,
            status=campaign.status,
            module_name=campaign.module_name,
            system_version=campaign.system_version,
            description=campaign.description,
        )
