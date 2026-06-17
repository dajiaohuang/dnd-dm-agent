from typing import Any
from pydantic import BaseModel, Field
from app.tools.item_schema import CharacterItem, CurrencyWallet
from app.tools.effect_engine import ActiveEffect


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
    actor_type: str = "player"
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
    hair: str = ""
    height: str = ""
    skin: str = ""
    weight: str = ""
    eyes: str = ""
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
    roleplay: dict[str, Any] = Field(default_factory=dict)
    story_role: dict[str, Any] = Field(default_factory=dict)
    encounter: dict[str, Any] = Field(default_factory=dict)
    active_effects: list[ActiveEffect | dict[str, Any]] = Field(default_factory=list)


class NapCatBindingUpsert(BaseModel):
    campaign_id: str
    character_id: str
    display_name: str = ""
    note: str = ""


class DiceRequest(BaseModel):
    formula: str


class ActorRoleplayPatch(BaseModel):
    roleplay: dict[str, Any] = Field(default_factory=dict)
    story_role: dict[str, Any] = Field(default_factory=dict)
    encounter: dict[str, Any] = Field(default_factory=dict)


class ActorPresencePatch(BaseModel):
    present: bool
    scene: str = ""


class CharacterQQBindingsPatch(BaseModel):
    qq_user_ids: list[str] = Field(default_factory=list)


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


class SettingDraftCreate(BaseModel):
    operation: str = "create"
    target_setting_id: str | None = None
    category: str = "custom"
    name: str = ""
    proposal: dict[str, Any] = Field(default_factory=dict)
    reason: str = ""
    session_id: str | None = None
    actor_id: str | None = None


class CampaignPackageImport(BaseModel):
    package: dict[str, Any]


class SettingCommentCreate(BaseModel):
    setting_id: str | None = None
    draft_id: str | None = None
    author_id: str | None = None
    content: str


class TaskSessionCreate(BaseModel):
    task_type: str
    platform: str = "web"
    chat_id: str | None = None
    owner_user_id: str | None = None
    session_id: str | None = None
    status: str = "active"
    priority: int = 3
    draft_data: dict[str, Any] = Field(default_factory=dict)
    proposal_data: dict[str, Any] = Field(default_factory=dict)
    missing_fields: list[str] = Field(default_factory=list)
    next_prompt: str = ""
    mentions: list[dict[str, Any]] = Field(default_factory=list)
    source_message_id: str | None = None
    parent_task_id: str | None = None


class TaskSessionPatch(BaseModel):
    status: str | None = None
    priority: int | None = None
    draft_data: dict[str, Any] | None = None
    proposal_data: dict[str, Any] | None = None
    missing_fields: list[str] | None = None
    next_prompt: str | None = None
    mentions: list[dict[str, Any]] | None = None
    created_object_type: str | None = None
    created_object_id: str | None = None
