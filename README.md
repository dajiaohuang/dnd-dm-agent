**English** | [简体中文](README-cn.md)

# DND DM Agent

A local-first LLM Agent system for long-running D&D 5E campaigns.
Uses **OpenAI Function-Calling tools**: the LLM understands natural language,
calls Python tools for deterministic dice rolls, state computation, and
structured data persistence. Character sheets, NPCs, items, effects,
campaign settings, and combat state are all stored in SQLite/PostgreSQL —
queryable, auditable, and rollback-able.

## Three Modes

| Mode | play_style | Description |
|------|-----------|-------------|
| **Lobby** | `lobby` | Manage campaigns, create characters, edit settings. May have no current campaign |
| **DM Mode** | `campaign` | AI Dungeon Master. Describe scenes, roleplay NPCs, advance plot |
| **Dice Assistant** | `dice_assistant` | Mechanical tool. Pure computation, checks, combat resolution |

Startup defaults to lobby. Send `/进入DM` or `/进入骰娘` to enter a game mode,
`/退出` to return to lobby. All three share the same current campaign.

## LLM Agent Tool Architecture

```
User: "Create a level 3 human wizard named Kalen with STR 16 DEX 14"
  → LLM understands intent → tool_call create_character_quick(name="Kalen", ...)
  → characters table INSERT + auto-bind QQ

User: "I attack the goblin with my longsword"
  → LLM → combat_attack(target="goblin", weapon="longsword")
  → read HotSnapshot → checked_roll("1d20+7") → DiceAuditLog

User: "2d6+3" (no @mention)
  → Passive dice listener → @sender "🎲 2d6+3 = 11"
```

53 LLM tools covering: campaign management, character creation, setting editing,
checks, combat actions, undo, binding, and export.

## Combat System

Full docs: [Combat System](docs/COMBAT_SYSTEM.md)

### Turn Action Quotas

Actions are tracked per turn; turns do not auto-advance:

```
Kalen's turn → quota: main 1, bonus 1, move 30
  attack → main: 1→0 → "Hit for 8. Remaining: bonus 1, move 30"
  Action Surge → use_feature("action_surge") → extra: 0→1
  attack → extra: 1→0 → "Hit for 12. Remaining: bonus 1, move 30"
  end_turn → advance_turn → "Goblin's turn"
```

| Combat Tool | Cost | Description |
|-------------|------|-------------|
| `combat_attack` | main/bonus/extra | Weapon attack, auto d20+bonus+damage |
| `combat_cast_spell` | main/bonus/extra | Cast spell, DC/attack+saves |
| `combat_dash/disengage/dodge` | main | Dash/Disengage/Dodge |
| `combat_ability_check` | main | Shove/Grapple etc |
| `use_feature` | varies | Action Surge/Second Wind/Rage |
| `end_turn` | — | End turn, advance |
| `turn_status` | — | Query remaining quota |

### Hot Data Layer

Every action reads `get_hot_character()` for a live mechanical snapshot —
base sheet + active_effects (buffs/debuffs/equipment).
All rolls go through `checked_roll()`: `random.randint()` + `DiceAuditLog`.
HP changes write to `CharacterChange` log, enabling precise `undo_damage`/`undo_healing`.

### Dice Assistant Free Combat (non-managed)

Human DM manages turns; players freely @bot for actions. The dice assistant
senses turn order from conversation history, flags skipped characters,
and asks whether to roll initiative. System-managed combat requires
explicit DM confirmation.

## Quick Start

### Local Backend

Requires Python 3.12 and [uv](https://docs.astral.sh/uv/).

```powershell
Copy-Item .env.example .env
# Edit .env to set DEEPSEEK_API_KEY
cd backend
uv sync
uv run uvicorn app.main:app --host 127.0.0.1 --port 8011
```

Initialize rules:

```powershell
Invoke-RestMethod -Method Post http://127.0.0.1:8011/ingest/rules
```

### Docker Compose

```powershell
Copy-Item .env.example .env
docker compose up --build -d
```

### Frontend

```powershell
run_webui.bat     # http://127.0.0.1:3001
```

## QQ / NapCat

```text
login_napcat_dnd.bat        # one-click launch
```

NapCat OneBot HTTP Post URL: `http://127.0.0.1:8011/napcat/callback`

Manage QQ bindings:

```powershell
manage_qq_bindings.bat bind 123456789 char_001 --name "Player Name"
manage_qq_bindings.bat list
```

## Character Sheet Import/Export

- **Import**: Send xlsx via QQ → `parse_character_sheet_xlsx()` → structured JSON → LLM creates character
- **HTML/PDF/DOCX**: Upload attachment → `parse_files()` → content injected into LLM context
- **Export**: `/导出角色卡` → `export_character_sheet()` → fills template → xlsx file
- **Attachment persistence**: Last 5 attachments stored in `campaign.config.last_attachments`, retrievable via `read_attachment` tool

## Configuration

```env
DEEPSEEK_API_KEY=sk-...
DEEPSEEK_BASE_URL=https://api.deepseek.com
LLM_MODEL=deepseek-chat

NAPCAT_BASE_URL=http://127.0.0.1:3000
NAPCAT_SELF_ID=1534055688
NAPCAT_DM_USER_IDS=2480933622
NAPCAT_REQUIRE_GROUP_AT=true
```

## Project Structure

```text
backend/app/
  main.py               FastAPI + NapCat callback
  services.py           Unified resolve_chat (lobby/DM/dice)
  message_router.py     Message dispatch
  dice_assistant.py     Dice fast-paths + DM confirmation
  llm.py                DeepSeek API + tools
  llm_loop.py           LLM → tool_call → handler loop
  campaign_turns.py     Turn management + action quotas
  tools/
    hot_character.py    HotSnapshot + checked_roll
    command_tools.py    53 tool schemas + handler registry
    combat_tools.py     Combat actions + use_feature/end_turn
    check_tools.py      Checks/damage/healing/undo/conditions
data/
  raw/                  Character sheet templates + rulebooks
docs/
  COMBAT_SYSTEM.md      Combat system documentation
```

## Tests

```powershell
cd backend
uv run pytest -q
```
