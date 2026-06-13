from typing import Any
from pydantic import BaseModel, Field
from app.tools.item_schema import CharacterItem, CurrencyWallet


class CampaignCreate(BaseModel):
    name: str
    system_version: str = "DND_5E_2014"
    description: str = ""
    config: dict[str, Any] = Field(default_factory=dict)


class CampaignPatch(BaseModel):
    name: str | None = None
    description: str | None = None
    config: dict[str, Any] | None = None


class CharacterCreate(BaseModel):
    campaign_id: str
    player_name: str = ""
    character_name: str
    data: dict[str, Any]


class CharacterPatch(BaseModel):
    data: dict[str, Any]
    reason: str = "manual update"
    change_type: str = "character_update"
    rule_refs: list[str] = Field(default_factory=list)


class CharacterBuildRequest(BaseModel):
    campaign_id: str
    player_name: str = ""
    character_name: str
    ancestry: str = ""
    subrace: str = ""
    background: str = ""
    alignment: str = ""
    class_name: str
    level: int = Field(default=1, ge=1, le=20)
    hit_die: int | None = Field(default=None)
    abilities: dict[str, int] = Field(default_factory=dict)
    max_hp: int | None = Field(default=None, ge=1)
    armor_class: int | None = Field(default=None, ge=1)
    speed: int = Field(default=30, ge=0)
    gender: str = ""
    age: str = ""
    faith: str = ""
    appearance: str = ""
    traits: str = ""
    ideals: str = ""
    bonds: str = ""
    flaws: str = ""
    backstory: str = ""
    skill_proficiencies: list[str] = Field(default_factory=list)
    skill_expertise: list[str] = Field(default_factory=list)
    saving_throw_proficiencies: list[str] = Field(default_factory=list)
    spellcasting_ability: str = ""
    languages: list[str] = Field(default_factory=list)
    tool_proficiencies: list[str] = Field(default_factory=list)
    weapon_proficiencies: list[str] = Field(default_factory=list)
    armor_proficiencies: list[str] = Field(default_factory=list)
    inventory: list[CharacterItem | dict[str, Any] | str] = Field(default_factory=list)
    currency: CurrencyWallet | dict[str, Any] = Field(default_factory=CurrencyWallet)
    features: list[dict[str, Any]] = Field(default_factory=list)
    spells: list[Any] = Field(default_factory=list)
    notes: dict[str, Any] = Field(default_factory=dict)


class NapCatBindingUpsert(BaseModel):
    campaign_id: str
    character_id: str
    display_name: str = ""
    note: str = ""


class DiceRequest(BaseModel):
    formula: str


class ChatRequest(BaseModel):
    session_id: str | None = None
    player_id: str | None = None
    character_id: str | None = None
    message: str


class EventCreate(BaseModel):
    session_id: str | None = None
    event_type: str
    content: str
    actors: list[str] = Field(default_factory=list)
    visibility: str = "party"
    importance: int = 3
    metadata: dict[str, Any] = Field(default_factory=dict)
