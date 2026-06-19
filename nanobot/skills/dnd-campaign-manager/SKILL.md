---
name: dnd-campaign-manager
description: Manage NanoBot D&D campaigns stored in the shared database. Use for creating, listing, selecting, archiving, saving, verifying, loading, or undoing campaigns and complete database snapshots, including campaign-scoped player-role notes synchronized with USER.md.
---

# D&D Campaign Manager

Use the deterministic JSON CLI. Do not compose SQL or call the legacy
`dnd_engine.save.io` file-save functions.

```powershell
python -m nanobot.dnd.db.cli <command>
```

Pass the current NanoBot workspace with `--workspace <absolute-path>` when
saving, loading, or undoing. This synchronizes only the current campaign's
managed player-role block in `USER.md`; it never replaces the whole file.

## Resolve the campaign first

1. Reuse the campaign ID already established in the current conversation.
2. Otherwise list active campaigns:

   ```powershell
   python -m nanobot.dnd.db.cli campaign list --status active
   ```

3. If exactly one active campaign exists, select it. If multiple exist, ask the
   user which campaign to use. Never infer from save slot numbers.
4. Treat “switch campaign” as selecting another campaign ID for the current
   conversation. This version deliberately has no channel binding.

## Manage campaigns

Create only after the user asks to start a new campaign:

```powershell
python -m nanobot.dnd.db.cli campaign create --name "战役名称" --module "模组名称"
```

Inspect or archive:

```powershell
python -m nanobot.dnd.db.cli campaign show --campaign <campaign-id>
python -m nanobot.dnd.db.cli campaign status --campaign <campaign-id> --set archived
```

Do not save into or load an archived campaign until it is explicitly reactivated.

## Save a complete snapshot

Use a short label describing the decision point. Every save creates a new slot;
never overwrite an earlier slot.

```powershell
python -m nanobot.dnd.db.cli save create `
  --campaign <campaign-id> `
  --label "进入地城前" `
  --workspace "<workspace>"
```

Report the returned campaign ID, slot, label, chapter, location, and hash prefix.

## List and verify saves

```powershell
python -m nanobot.dnd.db.cli save list --campaign <campaign-id>
python -m nanobot.dnd.db.cli save verify --campaign <campaign-id> --slot <slot>
```

Slots are campaign-local. Campaign A slot 1 and Campaign B slot 1 are unrelated.

## Load a save

Loading replaces the selected campaign's current database state. If the user did
not specify a slot, list saves and ask. If they explicitly named a slot, load it
without an extra confirmation.

```powershell
python -m nanobot.dnd.db.cli save load `
  --campaign <campaign-id> `
  --slot <slot> `
  --workspace "<workspace>"
```

The CLI validates the database record, embedded campaign ID, snapshot format,
schema version, and SHA-256 checksum. Never bypass a validation error or retry
against a different campaign. After success, report the restored slot and audit ID.

## Undo

Undo restores the state that existed before the latest audited mutation,
including a snapshot load. Respect the configured audit limit.

```powershell
python -m nanobot.dnd.db.cli undo `
  --campaign <campaign-id> `
  --count 1 `
  --workspace "<workspace>"
```

Do not interpret undo as deleting a save. Snapshot rows remain immutable history.

## USER.md player roles

Maintain only this campaign-scoped block when the user assigns player names to
characters:

```markdown
<!-- dnd-campaign:<campaign-id>:players:start -->
## 战役玩家角色

- 玩家甲：角色甲
- 玩家乙：角色乙
<!-- dnd-campaign:<campaign-id>:players:end -->
```

The save command imports the block into campaign configuration before capturing
the snapshot. Load and undo project the restored value back into the same block.
Never place channel IDs or authentication claims here.

Read [references/database-contract.md](references/database-contract.md) only when
debugging a validation failure, explaining snapshot coverage, or extending the CLI.
