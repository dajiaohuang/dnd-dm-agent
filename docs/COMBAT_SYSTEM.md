[English] | [简体中文](COMBAT_SYSTEM-cn.md)

# Combat System

## Two Combat Modes

| | System-Managed | Dice Free Combat |
|---|---|---|
| Manager | System | Human DM |
| Entry | `/进入战斗` (after DM confirms) | Natural occurrence |
| Initiative | System rolls + sorts | Human DM manages |
| Turn advance | `end_turn` tool | Never auto-advances |
| Action quotas | ✅ Tracked | ♾️ Unlimited |
| Typical use | Automated combat | Human table @bot assists |

### Dice Free Combat (default)

Human DM runs combat; players freely @bot in chat for actions.
The dice assistant only: reads hot data → rolls → resolves → writes back.

```
@bot "Roll initiative for Aric, Goblin, Mira"
  → Manually roll each → output list

@bot "I attack the goblin with my longsword"
  → combat_attack → checked_roll → "Hit for 8"

@bot "Wait, that damage was wrong"
  → undo_damage → CharacterChange reversal → HP restored

@bot "I drink a healing potion"
  → apply_healing → HP + 2d4+2
```

The dice assistant senses turn order from conversation history,
flags inconsistencies, and asks whether to roll initiative.
System-managed combat requires explicit DM confirmation before activating.

### System-Managed Turn-Based Combat

The system manages initiative, turns, and action quotas.

**Startup flow:**

```
DM: /进入战斗 or "enter combat"
Assistant: "Preparing to enter. Confirm:
            1. Which characters join?
            2. Any advantage/disadvantage on initiative?"
DM: "Aric, Goblin, Mira. Aric has advantage."
Assistant: Rolls all initiative → sorts → enters turn_based mode
```

## Turn Action Quotas

Each turn tracks remaining quotas; turns never auto-advance:

```
Kalen (Fighter Lv5) → quota: main 1, bonus 1, reaction 1, move 30

"Attack goblin with longsword"
  → combat_attack → main: 1→0
  → "Hit 8. Remaining: bonus 1, move 30"

"Action Surge"
  → use_feature("action_surge") → extra: 0→1
  → "Action Surge! Remaining: main 1, bonus 1, move 30"

"Attack orc"
  → combat_attack → extra: 1→0
  → "Hit 12. Remaining: bonus 1, move 30"

"Off-hand shortsword on goblin"
  → combat_attack(use_bonus_action=True) → bonus: 1→0
  → "Hit 4. Remaining: move 30"

"End turn"
  → end_turn → advance_turn → "Goblin's turn"
```

### Quota Types

| main_action | Attack, Cast Spell, Dash, Disengage, Dodge, Ability Check |
| bonus_action | Off-hand attack, Cunning Action, Second Wind, Rage |
| extra_actions | Gained via Action Surge |
| reaction | Opportunity Attack, Shield (usable off-turn) |
| movement | Feet remaining |

### Clarification Flow

The LLM never guesses intent — it asks when information is insufficient:

```
"I cast a spell at him"
  → LLM: "Which spell? You have Magic Missile (1st) and Fireball (3rd)"

"Fireball"
  → LLM: "What level? You have 1x 3rd and 2x 4th slots"

"3rd level"
  → LLM: "Which targets?"

"The orc and the goblin"
  → combat_cast_spell(spell="Fireball", level=3, targets=["orc","goblin"], save_type="dex")
  → main: 1→0, spell_slots[3]: 1→0
  → "Fireball DC 15. Damage 8d6 = 32"
```

## Combat Tools

### Action Tools

| Tool | Cost | Auto-resolves |
|------|------|---------------|
| `combat_attack` | main/bonus/extra | d20+bonus → attack roll; damage dice |
| `combat_cast_spell` | main/bonus/extra | Spell DC/attack + saves |
| `combat_ability_check` | main | d20+mod (shove/grapple) |
| `combat_dash` | main | Double speed |
| `combat_disengage` | main | No opportunity attacks |
| `combat_dodge` | main | Attacks at disadvantage, DEX saves advantage |
| `use_feature` | varies | Action Surge/Second Wind/Rage |
| `end_turn` | — | Advance to next |
| `turn_status` | — | Query remaining |

### Check Tools

| Tool | Description |
|------|-------------|
| `ability_check` | Ability/skill check (reads HotSnapshot) |
| `saving_throw` | Saving throw vs DC |
| `apply_damage` | Deal damage → write HP |
| `apply_healing` | Heal → write HP |
| `apply_condition` | Add condition |
| `remove_condition` | Remove condition |
| `undo_damage` | Undo last damage (CharacterChange reversal) |
| `undo_healing` | Undo last heal |
| `recent_changes` | HP change history |

## Hot Data Layer

Every combat action reads `get_hot_character()` for a live HotSnapshot:

```
base character data (characters table)
  + active_effects (buffs/debuffs/equipment/spell effects)
  = HotSnapshot {
      abilities: {str: {score:18, mod:+4}, ...}
      armor_class: 18, current_hp: 28, max_hp: 28
      saving_throws: {str: +7, dex: +2, ...}
      skills: {athletics: {bonus: +7}, ...}
      attacks: [{name:"Longsword", bonus:+7, damage:"1d8+4"}]
      spell_dc: 15, spell_attack_bonus: 7
      conditions: ["poisoned"]
    }
```

All rolls go through `checked_roll()`: `random.randint()` + `DiceAuditLog`.
HP changes write to `CharacterChange` (before/after snapshots), enabling exact undo.

## DM Mode vs Dice Assistant

Both share the same combat pipeline; only output style differs:

| | DM Combat | Dice Combat |
|---|-----------|-------------|
| Output | Mechanical data → LLM narrative | Pure mechanical data |
| Roleplay | ✅ NPC dialogue, scene description | ❌ `strict_tool_output` filters |
| Advice | ✅ Tactical advice (default) | ❌ Prohibited (default) |
| Temperature | 0.7 | 0.2 |
| Code path | `resolve_chat(mode="dm")` | `resolve_chat(mode="dice")` |
