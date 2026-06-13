from __future__ import annotations

import copy
from pathlib import Path

from openpyxl import load_workbook

from app.config import settings
from app.tools.character_rules import (
    ABILITY_KEYS,
    derive_character_rules,
    point_buy_cost,
    validate_character_rules,
)
from app.tools.spell_catalog import enrich_character_spells
from app.tools.item_schema import normalize_inventory, CurrencyWallet

ABILITY_CELLS = {"str": "E8", "dex": "E10", "con": "E12", "int": "E14", "wis": "E16", "cha": "E18"}


def build_character_data(raw: dict) -> dict:
    abilities = {key: int(raw.get("abilities", {}).get(key, 10)) for key in ABILITY_KEYS}
    level = int(raw.get("level", 1))
    derived = derive_character_rules(raw)
    hit_die = derived["hit_die"]
    max_hp = int(raw.get("max_hp") or hit_die + derived["ability_modifiers"]["con"])
    armor_class = int(raw.get("armor_class") or 10 + derived["ability_modifiers"]["dex"])
    return {
        "basic": {
            "name": raw["character_name"],
            "actor_type": raw.get("actor_type") if raw.get("actor_type") in {"npc", "monster"} else "player",
            "ancestry": raw.get("ancestry", ""),
            "subrace": raw.get("subrace", ""),
            "background": raw.get("background", ""),
            "alignment": raw.get("alignment", ""),
            "classes": [{"name": raw.get("class_name", ""), "level": level, "hit_die": hit_die}],
            "gender": raw.get("gender", ""),
            "age": raw.get("age", ""),
            "faith": raw.get("faith", ""),
            "appearance": raw.get("appearance", ""),
        },
        "abilities": abilities,
        "combat": {
            "armor_class": armor_class,
            "max_hp": max_hp,
            "current_hp": max_hp,
            "temp_hp": 0,
            "proficiency_bonus": derived["proficiency_bonus"],
            "speed": int(raw.get("speed", 30)),
            "hit_dice": {"die": f"d{hit_die}", "maximum": level, "current": level},
            "initiative": derived["initiative"],
            "passive_perception": derived["passive_perception"],
            "carrying_capacity": derived["carrying_capacity"],
        },
        "saving_throw_proficiencies": derived["saving_throw_proficiencies"],
        "saving_throws": derived["saving_throws"],
        "skills": derived["skills"],
        "proficiencies": {
            "languages": list(raw.get("languages", [])),
            "tools": list(raw.get("tool_proficiencies", [])),
            "weapons": list(raw.get("weapon_proficiencies", [])),
            "armor": list(raw.get("armor_proficiencies", [])),
        },
        "inventory": normalize_inventory(raw.get("inventory", [])),
        "currency": CurrencyWallet.model_validate(raw.get("currency") or {}).model_dump(mode="json"),
        "features": copy.deepcopy(raw.get("features", [])),
        "spells": enrich_character_spells(copy.deepcopy(raw.get("spells", [])), settings.data_dir),
        "spellcasting": {
            "ability": raw.get("spellcasting_ability", ""),
            "save_dc": derived["spell_save_dc"],
            "attack_bonus": derived["spell_attack_bonus"],
        },
        "derived": derived,
        "validation_errors": validate_character_rules(raw, derived),
        "personality": {
            "traits": raw.get("traits", ""),
            "ideals": raw.get("ideals", ""),
            "bonds": raw.get("bonds", ""),
            "flaws": raw.get("flaws", ""),
            "backstory": raw.get("backstory", ""),
        },
        "roleplay": copy.deepcopy(raw.get("roleplay", {})),
        "story_role": copy.deepcopy(raw.get("story_role", {})),
        "encounter": {"present": True, **copy.deepcopy(raw.get("encounter", {}))},
        "conditions": [],
        "notes": copy.deepcopy(raw.get("notes", {})),
    }


def export_character_sheet(data: dict, player_name: str, template: Path, target: Path) -> Path:
    workbook = load_workbook(template)
    identity = workbook.worksheets[0]
    main = workbook.worksheets[1]
    basic = data.get("basic", {})
    classes = basic.get("classes") or [{}]
    primary_class = classes[0]

    values = {
        "E3": basic.get("name", ""),
        "E4": player_name,
        "E6": basic.get("ancestry", ""),
        "M6": basic.get("alignment", ""),
        "E7": basic.get("gender", ""),
        "M7": basic.get("age", ""),
        "E8": basic.get("subrace", ""),
        "M8": basic.get("faith", ""),
        "E10": basic.get("background", ""),
        "B15": basic.get("appearance", ""),
        "W15": data.get("personality", {}).get("traits", ""),
        "W16": data.get("personality", {}).get("ideals", ""),
        "W17": data.get("personality", {}).get("bonds", ""),
        "W18": data.get("personality", {}).get("flaws", ""),
        "W19": data.get("personality", {}).get("backstory", ""),
    }
    for cell, value in values.items():
        set_sheet_value(identity, cell, value)

    set_sheet_value(main, "S3", primary_class.get("name", ""))
    set_sheet_value(main, "W3", int(primary_class.get("level", 1)))
    for key, cell in ABILITY_CELLS.items():
        set_sheet_value(main, cell, int(data.get("abilities", {}).get(key, 10)))
    set_sheet_value(main, "Y8", int(data.get("combat", {}).get("current_hp", 0)))
    set_sheet_value(main, "Y14", int(data.get("combat", {}).get("temp_hp", 0)))
    set_sheet_value(main, "AD17", int(data.get("combat", {}).get("speed", 30)))

    workbook.save(target)
    return target


def set_sheet_value(worksheet, coordinate: str, value) -> None:
    cell = worksheet[coordinate]
    if cell.__class__.__name__ != "MergedCell":
        cell.value = value
        return
    for merged in worksheet.merged_cells.ranges:
        if coordinate in merged:
            worksheet.cell(merged.min_row, merged.min_col).value = value
            return
    raise ValueError(f"Unable to resolve merged cell {worksheet.title}!{coordinate}")
