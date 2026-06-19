"""JSON CLI for campaign and complete snapshot management."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any

from sqlalchemy import func, select

from nanobot.dnd.db.campaigns import CampaignService
from nanobot.dnd.db.database import Database
from nanobot.dnd.db.models import (
    Campaign,
    EmbeddingModel,
    RuleChunk,
    RulePublication,
    RuleSection,
    RuleSet,
    RuleSource,
)
from nanobot.dnd.db.snapshots import CampaignSnapshotService
from nanobot.dnd.db.undo import UndoManager
from nanobot.dnd.db.user_context import read_player_roles, write_player_roles
from nanobot.dnd.rules.ingest import RuleIngestService
from nanobot.dnd.rules.search import RuleSearchService


def _emit(value: Any) -> None:
    # ASCII-safe JSON keeps the CLI reliable under the legacy Windows console
    # code pages commonly used to launch NanoBot. JSON consumers recover Unicode.
    print(json.dumps(value, ensure_ascii=True, indent=2, default=str))


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

    rules = commands.add_parser("rules")
    rule_commands = rules.add_subparsers(dest="action", required=True)
    ingest = rule_commands.add_parser("ingest-srd")
    ingest.add_argument("--references-dir")
    ingest.add_argument("--no-embed", action="store_true")
    ingest.add_argument("--force", action="store_true")
    ingest_zh = rule_commands.add_parser("ingest-zh-cn")
    ingest_zh.add_argument("--references-dir", required=True)
    ingest_zh.add_argument("--no-embed", action="store_true")
    ingest_zh.add_argument("--force", action="store_true")
    bind = rule_commands.add_parser("bind")
    bind.add_argument("--campaign", required=True)
    bind.add_argument("--rule-set", default="dnd5e-2024-srd-5.2.1")
    bind.add_argument("--publication", action="append")
    search = rule_commands.add_parser("search")
    search.add_argument("--query", required=True)
    search.add_argument("--campaign")
    search.add_argument("--rule-set")
    search.add_argument("--publication", action="append")
    search.add_argument("--top-k", type=int, default=5)
    search.add_argument("--no-dense", action="store_true")
    search.add_argument(
        "--expand",
        choices=("chunk", "paragraph", "section", "section-with-children"),
    )
    expand = rule_commands.add_parser("expand")
    expand.add_argument("--chunk", required=True)
    expand.add_argument(
        "--mode",
        choices=("chunk", "paragraph", "section", "section-with-children"),
        default="section",
    )
    rule_commands.add_parser("tree")
    rule_commands.add_parser("status")
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
        elif args.area == "undo":
            result = UndoManager(database).undo(
                args.campaign, count=args.count, actor_id=args.actor
            )
            user_file = (
                str(_sync_database_to_user(database, args.campaign, args.workspace))
                if args.workspace
                else None
            )
            _emit({**asdict(result), "count": result.count, "user_md": user_file})
        else:
            ingest_service = RuleIngestService(database)
            search_service = RuleSearchService(database)
            if args.action == "ingest-srd":
                references_dir = args.references_dir or str(
                    Path(__file__).parents[2]
                    / "skills"
                    / "dnd-dm"
                    / "srd"
                    / "references"
                )
                _emit(
                    asdict(
                        ingest_service.ingest_srd(
                            references_dir,
                            embed=not args.no_embed,
                            force=args.force,
                        )
                    )
                )
            elif args.action == "ingest-zh-cn":
                _emit(
                    asdict(
                        ingest_service.ingest_directory_srd(
                            args.references_dir,
                            embed=not args.no_embed,
                            force=args.force,
                        )
                    )
                )
            elif args.action == "bind":
                profile_id = ingest_service.bind_campaign(
                    args.campaign,
                    rule_set_id=args.rule_set,
                    publication_ids=args.publication,
                )
                _emit({"campaign_id": args.campaign, "profile_id": profile_id})
            elif args.action == "search":
                hits = search_service.search(
                    args.query,
                    campaign_id=args.campaign,
                    rule_set_id=args.rule_set,
                    publication_ids=args.publication,
                    top_k=args.top_k,
                    dense=not args.no_dense,
                )
                payload: dict[str, Any] = {"query": args.query, "hits": [asdict(hit) for hit in hits]}
                if args.expand and hits:
                    payload["expanded"] = search_service.expand(
                        hits[0].chunk_id, mode=args.expand
                    )
                _emit(payload)
            elif args.action == "expand":
                _emit(search_service.expand(args.chunk, mode=args.mode))
            elif args.action == "tree":
                with database.transaction() as session:
                    tree = []
                    for rule_set in session.scalars(
                        select(RuleSet).order_by(RuleSet.game_system, RuleSet.edition)
                    ):
                        publications = []
                        for publication in session.scalars(
                            select(RulePublication)
                            .where(RulePublication.rule_set_id == rule_set.id)
                            .order_by(RulePublication.priority.desc(), RulePublication.name)
                        ):
                            section_count = session.scalar(
                                select(func.count())
                                .select_from(RuleSection)
                                .where(RuleSection.publication_id == publication.id)
                            )
                            top_level_sections = list(
                                dict.fromkeys(
                                    session.scalars(
                                        select(RuleSection.title)
                                        .where(
                                            RuleSection.publication_id == publication.id,
                                            RuleSection.depth == 1,
                                            ~RuleSection.title.like("Page %"),
                                        )
                                        .order_by(RuleSection.source_id, RuleSection.order_index)
                                    )
                                )
                            )
                            publications.append(
                                {
                                    "id": publication.id,
                                    "name": publication.name,
                                    "type": publication.publication_type,
                                    "parent_publication_id": publication.parent_publication_id,
                                    "sections": int(section_count or 0),
                                    "top_level_sections": top_level_sections,
                                }
                            )
                        tree.append(
                            {
                                "id": rule_set.id,
                                "game_system": rule_set.game_system,
                                "edition": rule_set.edition,
                                "release": rule_set.release,
                                "locale": rule_set.locale,
                                "publications": publications,
                            }
                        )
                _emit({"rule_sets": tree})
            else:
                with database.transaction() as session:
                    def count(model, *conditions) -> int:
                        statement = select(func.count()).select_from(model)
                        if conditions:
                            statement = statement.where(*conditions)
                        return int(session.scalar(statement) or 0)

                    _emit(
                        {
                            "rule_sets": count(RuleSet),
                            "publications": count(RulePublication),
                            "sources": count(RuleSource),
                            "sections": count(RuleSection),
                            "chunks": count(RuleChunk),
                            "embedded_chunks": count(
                                RuleChunk, RuleChunk.embedding_json.is_not(None)
                            ),
                            "embedding_models": [
                                {
                                    "id": model.id,
                                    "model_name": model.model_name,
                                    "dimensions": model.dimensions,
                                    "active": model.is_active,
                                }
                                for model in session.scalars(select(EmbeddingModel))
                            ],
                        }
                    )
        return 0
    except Exception as exc:
        _emit({"error": type(exc).__name__, "message": str(exc)})
        return 1
    finally:
        database.dispose()


if __name__ == "__main__":
    sys.exit(main())
