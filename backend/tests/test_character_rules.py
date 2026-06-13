from app.tools.character_rules import (
    armor_class,
    derive_character_rules,
    point_buy_cost,
    proficiency_bonus,
)
from app.tools.item_schema import normalize_inventory


def test_core_excel_formulas_are_available_as_code():
    abilities = {"str": 8, "dex": 14, "con": 13, "int": 15, "wis": 12, "cha": 10}
    assert point_buy_cost(abilities) == 27
    assert proficiency_bonus(1) == 2
    assert proficiency_bonus(17) == 6
    assert armor_class(abilities) == 12


def test_wizard_rules_are_derived_from_template_logic():
    derived = derive_character_rules({
        "class_name": "Wizard",
        "level": 1,
        "abilities": {"str": 8, "dex": 14, "con": 13, "int": 15, "wis": 12, "cha": 10},
        "skill_proficiencies": ["arcana", "history"],
        "spellcasting_ability": "int",
    })
    assert derived["hit_die"] == 6
    assert derived["saving_throw_proficiencies"] == ["int", "wis"]
    assert derived["skills"]["arcana"]["bonus"] == 4
    assert derived["passive_perception"] == 11
    assert derived["spell_save_dc"] == 12
    assert derived["spell_attack_bonus"] == 4
    assert derived["carrying_capacity"] == 120


def test_inventory_normalizes_standard_and_custom_items():
    inventory = normalize_inventory([
        {"instance_id": "bag_1", "name": "Explorer's Pack", "item_type": "container"},
        {
            "instance_id": "blade_1",
            "name": "Moonlit Letter Opener",
            "type": "custom",
            "weight": 0.2,
            "value": 7,
            "equipped": True,
            "equipped_slot": "main_hand",
            "container_instance_id": "bag_1",
            "custom_data": {"moon_phase": "waxing", "homebrew_rule": {"glows": True}},
            "creator_note": "Any unknown field is preserved.",
        },
    ])
    custom = inventory[1]
    assert custom["item_type"] == "custom"
    assert custom["weight_each"] == 0.2
    assert custom["value_each"] == {"amount": 7.0, "currency": "gp"}
    assert custom["custom_data"]["homebrew_rule"]["glows"]
    assert custom["creator_note"] == "Any unknown field is preserved."

    legacy = normalize_inventory([{"item_id": "longsword", "name": "Longsword"}])[0]
    assert legacy["item_type"] == "weapon"
    assert legacy["weapon"]["damage_dice"] == "1d8"
