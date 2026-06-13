from __future__ import annotations

import copy
import re
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator


KNOWN_ITEM_DEFAULTS = {
    "longsword": {
        "item_type": "weapon",
        "weapon": {"damage_dice": "1d8", "damage_type": "slashing", "versatile_damage": "1d10"},
    },
    "potion_healing": {
        "item_type": "consumable",
        "consumable": {"consume_on_use": True, "activation": "action"},
        "effects": [{"effect_type": "healing", "formula": "2d4+2"}],
    },
}


class FlexibleModel(BaseModel):
    """Known fields stay queryable while homebrew fields remain lossless."""

    model_config = ConfigDict(extra="allow")


class MoneyValue(FlexibleModel):
    amount: float = 0
    currency: str = "gp"


class ItemEffect(FlexibleModel):
    effect_type: str = "custom"
    name: str = ""
    description: str = ""
    trigger: str = ""
    formula: str = ""
    uses_resource: str = ""
    data: dict[str, Any] = Field(default_factory=dict)


class WeaponData(FlexibleModel):
    damage_dice: str = ""
    damage_type: str = ""
    attack_ability: str = ""
    range_normal: int | None = None
    range_long: int | None = None
    properties: list[str] = Field(default_factory=list)
    ammunition_type: str = ""
    versatile_damage: str = ""
    magic_bonus: int = 0


class ArmorData(FlexibleModel):
    armor_category: str = ""
    base_ac: int | None = None
    dexterity_cap: int | None = None
    strength_requirement: int | None = None
    stealth_disadvantage: bool = False
    magic_bonus: int = 0


class ConsumableData(FlexibleModel):
    consume_on_use: bool = True
    uses_per_item: int = 1
    activation: str = ""


class ContainerData(FlexibleModel):
    capacity_weight: float | None = None
    capacity_items: int | None = None
    extradimensional: bool = False


class ChargeData(FlexibleModel):
    current: int = 0
    maximum: int = 0
    recharge: str = ""
    recharge_formula: str = ""


class DurabilityData(FlexibleModel):
    current: int | None = None
    maximum: int | None = None
    condition: str = "normal"


class CharacterItem(FlexibleModel):
    schema_version: int = 1
    instance_id: str = ""
    item_id: str = ""
    name: str
    item_type: str = "custom"
    subtype: str = ""
    quantity: int = Field(default=1, ge=0)
    stackable: bool = True
    equipped: bool = False
    equipped_slot: str = ""
    attuned: bool = False
    attunement_required: bool = False
    identified: bool = True
    rarity: str = ""
    description: str = ""
    source: str = ""
    tags: list[str] = Field(default_factory=list)
    weight_each: float = Field(default=0, ge=0)
    value_each: MoneyValue = Field(default_factory=MoneyValue)
    location: str = "carried"
    container_instance_id: str = ""
    owner: str = ""
    charges: ChargeData | None = None
    durability: DurabilityData | None = None
    weapon: WeaponData | None = None
    armor: ArmorData | None = None
    consumable: ConsumableData | None = None
    container: ContainerData | None = None
    effects: list[ItemEffect] = Field(default_factory=list)
    requirements: dict[str, Any] = Field(default_factory=dict)
    custom_data: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def apply_invariants(self):
        if not self.instance_id:
            self.instance_id = f"item_{uuid4().hex[:12]}"
        if not self.item_id:
            slug = re.sub(r"[^a-z0-9]+", "_", self.name.casefold()).strip("_")
            self.item_id = slug or self.instance_id
        if self.equipped and not self.equipped_slot:
            self.equipped_slot = "unspecified"
        if self.quantity == 0:
            self.equipped = False
            self.equipped_slot = ""
        return self


class CurrencyWallet(FlexibleModel):
    cp: int = Field(default=0, ge=0)
    sp: int = Field(default=0, ge=0)
    ep: int = Field(default=0, ge=0)
    gp: int = Field(default=0, ge=0)
    pp: int = Field(default=0, ge=0)
    custom: dict[str, float] = Field(default_factory=dict)


def normalize_item(raw: Any) -> dict[str, Any]:
    if isinstance(raw, str):
        raw = {"name": raw}
    if not isinstance(raw, dict):
        raise ValueError("Inventory items must be strings or objects.")
    item = copy.deepcopy(raw)
    known = KNOWN_ITEM_DEFAULTS.get(str(item.get("item_id", "")).casefold())
    if known and item.get("item_type") in {None, "", "custom"}:
        for key, value in known.items():
            item[key] = copy.deepcopy(value) if key not in item or key == "item_type" else item[key]
    if "type" in item and "item_type" not in item:
        item["item_type"] = item.pop("type")
    if "weight" in item and "weight_each" not in item:
        item["weight_each"] = item.pop("weight")
    if "value" in item and "value_each" not in item:
        value = item.pop("value")
        item["value_each"] = value if isinstance(value, dict) else {"amount": value, "currency": "gp"}
    if "damage" in item and "weapon" not in item:
        item["weapon"] = {"damage_dice": item.pop("damage"), "damage_type": item.pop("damage_type", "")}
    if not item.get("name"):
        item["name"] = item.get("item_id") or "Unnamed custom item"
    return CharacterItem.model_validate(item).model_dump(mode="json")


def normalize_inventory(raw: list[Any] | None) -> list[dict[str, Any]]:
    items = [normalize_item(item) for item in (raw or [])]
    instance_ids = [item["instance_id"] for item in items]
    if len(instance_ids) != len(set(instance_ids)):
        raise ValueError("Inventory item instance_id values must be unique.")
    known_ids = set(instance_ids)
    for item in items:
        container_id = item.get("container_instance_id")
        if container_id and container_id not in known_ids:
            raise ValueError(f"Unknown container_instance_id: {container_id}")
        if container_id == item["instance_id"]:
            raise ValueError("An item cannot contain itself.")
    return items


def normalize_character_inventory(data: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(data)
    if "inventory" in result:
        result["inventory"] = normalize_inventory(result.get("inventory"))
    else:
        result["inventory"] = []
    result["currency"] = CurrencyWallet.model_validate(result.get("currency") or {}).model_dump(mode="json")
    return result


def item_schema_catalog() -> dict[str, Any]:
    return {
        "item_types": [
            "weapon", "armor", "shield", "consumable", "container", "tool", "adventuring_gear",
            "ammunition", "focus", "wondrous_item", "currency", "quest_item", "custom",
        ],
        "equipment_slots": [
            "main_hand", "off_hand", "two_hands", "armor", "shield", "head", "neck", "shoulders",
            "hands", "waist", "feet", "ring_left", "ring_right", "back", "unspecified",
        ],
        "storage_rule": "Every carried or equipped object is stored once in inventory. Equipment uses equipped fields.",
        "custom_rule": "Unknown fields are preserved and arbitrary homebrew data belongs in custom_data.",
        "item_json_schema": CharacterItem.model_json_schema(),
        "currency_json_schema": CurrencyWallet.model_json_schema(),
    }
