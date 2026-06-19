"""JSON CLI for campaign and complete snapshot management."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any

from nanobot.dnd.db.campaigns import CampaignService
from nanobot.dnd.db.database import Database
from nanobot.dnd.db.models import Campaign
from nanobot.dnd.db.snapshots import CampaignSnapshotService
from nanobot.dnd.db.undo import UndoManager
from nanobot.dnd.db.user_context import read_player_roles, write_player_roles


def _emit(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, default=str))


def _sync_user_to_database(database: Database, campaign_id: str, workspace: str) -> None:
    roles = read_player_roles(workspace, campaign_id)
    with database.transaction() as session:
        campaign = session.get(Campaign, campaign_id)
        if campaign is None:
            raise ValueError(f"campaign not found: {campaign_id}")
        config = dict(campaign.config or {})
        config["user_md_player_roles"] = roles
        campaign.config = config


def _sync_database_to_user(database: Database, campaign_id: str, workspace: str) -> Path:
    with database.transaction() as session:
        campaign = session.get(Campaign, campaign_id)
        if campaign is None:
            raise ValueError(f"campaign not found: {campaign_id}")
        roles = str((campaign.config or {}).get("user_md_player_roles", ""))
    return write_player_roles(workspace, campaign_id, roles)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m nanobot.dnd.db.cli")
    parser.add_argument("--database-url", help="Override DND_DATABASE_URL")
    commands = parser.add_subparsers(dest="area", required=True)

    campaign = commands.add_parser("campaign")
    campaign_commands = campaign.add_subparsers(dest="action", required=True)
    create = campaign_commands.add_parser("create")
    create.add_argument("--name", required=True)
    create.add_argument("--id")
    create.add_argument("--module")
    create.add_argument("--description")
    listing = campaign_commands.add_parser("list")
    listing.add_argument("--status", choices=("active", "archived"))
    show = campaign_commands.add_parser("show")
    show.add_argument("--campaign", required=True)
    status = campaign_commands.add_parser("status")
    status.add_argument("--campaign", required=True)
    status.add_argument("--set", required=True, choices=("active", "archived"))

    save = commands.add_parser("save")
    save_commands = save.add_subparsers(dest="action", required=True)
    save_create = save_commands.add_parser("create")
    save_create.add_argument("--campaign", required=True)
    save_create.add_argument("--label", default="")
    save_create.add_argument("--actor")
    save_create.add_argument("--workspace")
    save_list = save_commands.add_parser("list")
    save_list.add_argument("--campaign", required=True)
    save_verify = save_commands.add_parser("verify")
    save_verify.add_argument("--campaign", required=True)
    save_verify.add_argument("--slot", required=True, type=int)
    save_load = save_commands.add_parser("load")
    save_load.add_argument("--campaign", required=True)
    save_load.add_argument("--slot", required=True, type=int)
    save_load.add_argument("--actor")
    save_load.add_argument("--session")
    save_load.add_argument("--request")
    save_load.add_argument("--workspace")

    undo = commands.add_parser("undo")
    undo.add_argument("--campaign", required=True)
    undo.add_argument("--count", type=int, default=1)
    undo.add_argument("--actor")
    undo.add_argument("--workspace")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    database = Database(args.database_url)
    try:
        database.upgrade_schema()
        campaigns = CampaignService(database)
        snapshots = CampaignSnapshotService(database)
        if args.area == "campaign":
            if args.action == "create":
                result = campaigns.create(
                    args.name,
                    campaign_id=args.id,
                    module_name=args.module,
                    description=args.description,
                )
            elif args.action == "list":
                result = campaigns.list(status=args.status)
            elif args.action == "show":
                result = campaigns.get(args.campaign)
            else:
                result = campaigns.set_status(args.campaign, args.set)
            _emit(asdict(result) if not isinstance(result, list) else [asdict(x) for x in result])
        elif args.area == "save":
            if args.action == "create":
                if args.workspace:
                    _sync_user_to_database(database, args.campaign, args.workspace)
                _emit(
                    asdict(
                        snapshots.create(
                            args.campaign, label=args.label, actor_id=args.actor
                        )
                    )
                )
            elif args.action == "list":
                _emit([asdict(item) for item in snapshots.list(args.campaign)])
            elif args.action == "verify":
                payload = snapshots.get(args.campaign, args.slot)
                _emit(
                    {
                        "valid": True,
                        "campaign_id": payload["campaign_id"],
                        "schema_version": payload["schema_version"],
                        "captured_at": payload["captured_at"],
                    }
                )
            else:
                result = snapshots.restore(
                    args.campaign,
                    args.slot,
                    actor_id=args.actor,
                    session_id=args.session,
                    request_id=args.request,
                )
                user_file = (
                    str(_sync_database_to_user(database, args.campaign, args.workspace))
                    if args.workspace
                    else None
                )
                _emit({**asdict(result), "user_md": user_file})
        else:
            result = UndoManager(database).undo(
                args.campaign, count=args.count, actor_id=args.actor
            )
            user_file = (
                str(_sync_database_to_user(database, args.campaign, args.workspace))
                if args.workspace
                else None
            )
            _emit({**asdict(result), "count": result.count, "user_md": user_file})
        return 0
    except Exception as exc:
        _emit({"error": type(exc).__name__, "message": str(exc)})
        return 1
    finally:
        database.dispose()


if __name__ == "__main__":
    sys.exit(main())
