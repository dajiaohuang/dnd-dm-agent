from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator


class SettingProposal(BaseModel):
    category: Literal["npc", "location", "faction", "item", "event"]
    name: str = Field(min_length=1, max_length=120)
    summary: str = Field(min_length=1, max_length=500)
    content: dict = Field(default_factory=dict)
    visibility: Literal["dm_only", "party", "public"] = "dm_only"
    tags: list[str] = Field(default_factory=list)
    relationships: list[dict] = Field(default_factory=list)

    @model_validator(mode="after")
    def require_content(self):
        if not self.content:
            self.content = {"description": self.summary}
        return self


class SettingBatchArtifact(BaseModel):
    artifact_type: Literal["campaign_settings"] = "campaign_settings"
    theme: str = ""
    settings: list[SettingProposal] = Field(min_length=1)


class CharacterDraftArtifact(BaseModel):
    artifact_type: Literal["character_draft"] = "character_draft"
    character_name: str
    class_name: str
    ancestry: str
    background: str
    level: int = Field(default=1, ge=1, le=20)
    abilities: dict[str, int]
    roll_audit: list[dict] = Field(default_factory=list)
