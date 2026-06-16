from __future__ import annotations

import argparse

from sqlalchemy import select

from app.config import settings
from app.db.database import Base, SessionLocal, engine
from app.db.models import Character, NapCatCharacterBinding
from app.qq_bindings import bind_qq, migrate_binding_schema_for_multiple_characters, unbind_qq


def parser() -> argparse.ArgumentParser:
    command = argparse.ArgumentParser(description="Maintain QQ user to character bindings.")
    command.add_argument("--campaign", default=settings.napcat_campaign_id)
    subcommands = command.add_subparsers(dest="action", required=True)
    subcommands.add_parser("characters", help="List characters in the campaign.")
    subcommands.add_parser("list", help="List QQ bindings in the campaign.")

    bind = subcommands.add_parser("bind", help="Create or update a QQ binding.")
    bind.add_argument("qq_user_id")
    bind.add_argument("character_id")
    bind.add_argument("--name", default="")
    bind.add_argument("--note", default="")

    unbind = subcommands.add_parser("unbind", help="Delete a QQ binding.")
    unbind.add_argument("qq_user_id")
    unbind.add_argument("--character-id", default=None)
    return command


def main() -> None:
    args = parser().parse_args()
    Base.metadata.create_all(bind=engine)
    migrate_binding_schema_for_multiple_characters()
    with SessionLocal() as db:
        if args.action == "characters":
            characters = db.scalars(
                select(Character)
                .where(Character.campaign_id == args.campaign)
                .order_by(Character.character_name)
            ).all()
            for character in characters:
                print(f"{character.id}\t{character.character_name}\t{character.player_name or ''}")
            return

        query = select(NapCatCharacterBinding).where(
            NapCatCharacterBinding.campaign_id == args.campaign
        )
        if args.action == "list":
            for binding in db.scalars(query.order_by(NapCatCharacterBinding.qq_user_id)).all():
                character = db.get(Character, binding.character_id)
                print(
                    f"{binding.qq_user_id}\t{binding.character_id}\t"
                    f"{character.character_name if character else '-'}\t{binding.display_name or ''}"
                )
            return

        if args.action == "unbind":
            bindings = db.scalars(query.where(NapCatCharacterBinding.qq_user_id == args.qq_user_id)).all()
            if args.character_id:
                bindings = [item for item in bindings if item.character_id == args.character_id]
            if not bindings:
                raise SystemExit("Binding not found.")
            unbind_qq(db, args.campaign, args.qq_user_id, args.character_id)
            print(f"Removed {len(bindings)} binding(s) for QQ {args.qq_user_id} from campaign {args.campaign}.")
            return

        if not args.qq_user_id.isdigit():
            raise SystemExit("QQ user ID must contain digits only.")
        character = db.get(Character, args.character_id)
        if not character or character.campaign_id != args.campaign:
            raise SystemExit("Character was not found in the requested campaign.")
        bind_qq(db, args.campaign, args.qq_user_id, character, args.name, args.note)
        print(f"Bound QQ {args.qq_user_id} to {character.character_name} ({character.id}).")


if __name__ == "__main__":
    main()
