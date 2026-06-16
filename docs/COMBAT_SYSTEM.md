[English] | [简体中文](COMBAT_SYSTEM-cn.md)

# Combat System

This document describes combat behavior in both **DM Mode** and **Dice Assistant Mode**, including turn flow, QQ bindings, hosted characters, reactions, and state updates.

## Shared Combat Core

DM Mode and Dice Assistant Mode use the same deterministic combat pipeline. The mode changes who narrates and who has authority over NPCs, not the underlying mechanics.

Shared rules:

- Combat uses entity character sheets only. AC, HP, modifiers, saves, initiative, spellcasting values, inventory, and active effects must come from structured character data.
- Combat begins by confirming participants. Characters without entity sheets cannot join initiative.
- The system rolls initiative for every participating player character, NPC, and monster.
- Combat enters turn-based mode and records round, turn index, participants, initiative, and reaction availability in campaign state.
- A turn normally advances only after the action is resolved, reactions are complete, and state changes have been audited.
- Ending combat clears combat turn state and returns the campaign to free-play mode.

## Character Cards and Effective Values

Each combat action receives:

- the acting character sheet, if known;
- cropped mechanical cards for all combat participants;
- focused target cards when the message names a target;
- relevant rules, spells, effects, memories, and public campaign facts.

Base character data is not overwritten by temporary combat effects. Equipment, buffs, debuffs, spells, custom items, and other temporary modifiers contribute to an `effective` mechanical snapshot.

The effective snapshot may affect:

- AC and HP-related values;
- ability modifiers;
- saves and skill checks;
- initiative;
- attack and spellcasting values;
- advantage or disadvantage;
- bonus dice;
- concentration checks;
- consumable one-shot effects.

## DM Mode Combat

DM Mode is the full AI Dungeon Master mode. In combat it uses the same mechanical engine as Dice Assistant Mode, but it may add narration, portray NPCs, and present established story context.

DM Mode may:

- describe combat scenes and outcomes;
- portray present NPCs and monsters;
- use NPC roleplay profiles, secrets, motives, combat behavior, and planned actions;
- connect combat to established campaign memory and current scene facts;
- operate NPCs and monsters during their turns;
- resolve mechanical actions with deterministic rolls and structured state changes.

DM Mode must still:

- read target AC, HP, saves, and modifiers from entity cards;
- avoid inventing mechanical values;
- wait for reaction decisions before rolling when a reaction window is opened;
- log rolls, state changes, and combat events.

## Dice Assistant Mode Combat

Dice Assistant Mode supports real players and a real DM. It does not run the story by itself. It keeps the campaign framework, character cards, memory, effects, inventory, and combat tools, but disables proactive plot advancement and NPC portrayal.

Dice Assistant Mode may:

- host initiative and turn order;
- calculate checks, saves, attacks, damage, healing, and effects;
- answer mechanical questions about character sheets, spells, rules, items, and combat state;
- update HP, inventory, effects, and memory when explicitly instructed;
- mention the QQ user who controls the current character;
- ask the DM to decide NPC and monster actions or reactions.

Dice Assistant Mode must not:

- narrate scenes or outcomes as the DM;
- portray NPCs or monsters unless a character is explicitly marked as hosted;
- suggest player tactics by default;
- advance plot or decide story consequences;
- treat NPCs or monsters as automatically controlled merely because they are NPCs or monsters.

## QQ Bindings

QQ bindings are campaign-specific. Switching the active campaign changes the active character bindings.

Binding rules:

- One QQ user may be bound to multiple characters in the same campaign.
- One character may also mirror multiple QQ user IDs in its `integrations.qq_user_ids`.
- In combat, when a QQ user controls multiple characters, the router prefers the character whose turn is currently active.
- Outside combat, if the QQ user controls multiple characters, the message can select a character by naming it. If no clear character is selected, the system avoids guessing.
- Deleting a character removes its QQ bindings.
- Switching out of Dice Assistant Mode removes only Dice Assistant managed DM bindings for NPCs and monsters.

## NPC and Monster Bindings in Dice Assistant Mode

Dice Assistant Mode binds NPCs and monsters to the DM QQ user so the real DM can operate them.

When entering Dice Assistant Mode:

- the system uses `campaign.config.dice_dm_qq_user_id` if present;
- otherwise, if exactly one DM QQ is configured in settings, it uses that;
- otherwise, it asks who the DM is;
- once the DM QQ is known, all NPCs and monsters in the campaign are bound to that QQ as Dice Assistant managed bindings.

When exiting Dice Assistant Mode:

- Dice Assistant managed NPC and monster bindings are removed;
- normal player bindings remain intact.

If the DM QQ changes:

- stale Dice Assistant managed bindings for the old DM are removed;
- NPCs and monsters are rebound to the new DM QQ.

On service startup:

- existing databases are migrated to support one QQ controlling multiple characters;
- active Dice Assistant campaigns resync NPC and monster bindings to the configured DM QQ.

## Hosted Characters

In Dice Assistant Mode, no character is hosted by default. NPCs and monsters are not naturally hosted.

A character is treated as hosted only when its character data explicitly sets:

```json
{
  "control": "hosted"
}
```

or:

```json
{
  "control": "auto"
}
```

Hosted character behavior:

- if a hosted character is the current combat actor, its action may include brief roleplay prose even when combat roleplay is disabled;
- the roleplay must be based on that character's own `roleplay` and `story_role` fields;
- the prose is limited to that action's gesture, voice, combat behavior, or immediate reaction;
- it must not advance plot, portray other NPCs, or decide story consequences.

## Turn Access

Turn access is enforced by mode and current actor.

DM Mode:

- player characters act on their own turns;
- NPCs and monsters are operated by the DM;
- DM combat output may include narration and roleplay.

Dice Assistant Mode:

- player characters act through their bound QQ users;
- NPCs and monsters are operated by the DM QQ through their Dice Assistant bindings;
- if one QQ controls multiple characters, the current turn selects the relevant character;
- non-turn messages such as status checks, combat exit, and mode commands can interrupt pending setup or reaction prompts when allowed.

## Reactions

Potential reaction triggers open a reaction window before dice results are shown.

Reaction flow:

1. The acting character declares an action.
2. The system identifies possible reactors from participant cards and reaction features.
3. The system shows the action declaration and required roll formula, but does not roll yet.
4. Bound QQ users are mentioned and asked whether they react.
5. In Dice Assistant Mode, NPC and monster reactions go to the bound DM QQ unless the character is explicitly hosted.
6. Explicitly hosted characters may decide automatically.
7. After every reaction is decided, the system performs the roll, resolves the action, applies state changes, and advances the turn.

## Roleplay and Advice Switches

Combat roleplay and advice are configurable per play style.

Defaults:

- DM Mode: combat roleplay on, combat advice on.
- Dice Assistant Mode: combat roleplay off, combat advice off.

Commands:

- `/combatroleplayon`
- `/combatroleplayoff`
- `/combatadviceon`
- `/combatadviceoff`

Important boundaries:

- Dice Assistant non-combat output is always tool-only; combat roleplay and advice switches do not affect non-combat behavior.
- Hosted character action prose bypasses the combat roleplay switch only for that hosted character's own action.
- Advice remains controlled separately from roleplay.

## State Updates and Audit

Combat updates are persisted and auditable.

The system records:

- dice rolls and formulas;
- HP changes;
- inventory changes;
- active effect changes;
- concentration checks;
- reaction decisions;
- turn advancement;
- character version changes;
- campaign events and memory extraction candidates.

This lets later questions such as "what happened last round?", "why did AC change?", or "which effect was consumed?" be answered from stored state instead of chat history alone.
