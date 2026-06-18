from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

from sqlalchemy import inspect, text
from sqlalchemy.engine import make_url

_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT / "backend"))

from app.config import PROJECT_ROOT, settings
from app.db.database import Base, engine
import app.db.models  # noqa: F401 - register all tables in Base.metadata


PRESERVED_RAG_TABLES = {"rule_chunks", "compendium_entries"}


def sqlite_database_path() -> Path:
    url = make_url(settings.database_url)
    if not url.drivername.startswith("sqlite"):
        raise RuntimeError(
            "Automatic destructive cleanup only supports SQLite; "
            f"configured database is {url.drivername!r}."
        )
    if not url.database or url.database == ":memory:":
        raise RuntimeError("No persistent SQLite database is configured.")
    path = Path(url.database)
    if not path.is_absolute():
        # SQLAlchemy resolves relative SQLite URLs from the process working
        # directory.  The BAT deliberately runs this helper from backend/.
        path = Path.cwd() / path
    return path.resolve()


def clear_directory(path: Path, *, dry_run: bool, preserve_gitkeep: bool = False) -> None:
    print(f"  dir:  {path}")
    if dry_run or not path.exists():
        return
    for child in path.iterdir():
        if preserve_gitkeep and child.name == ".gitkeep":
            continue
        if child.is_dir():
            shutil.rmtree(child)
        else:
            child.unlink()


def main() -> int:
    parser = argparse.ArgumentParser(description="Clear DM Agent runtime data.")
    parser.add_argument("--yes", action="store_true", help="Required destructive confirmation")
    parser.add_argument("--dry-run", action="store_true", help="Print targets without deleting")
    args = parser.parse_args()
    if not args.yes and not args.dry_run:
        parser.error("--yes is required (the BAT file provides the interactive confirmation)")

    database = sqlite_database_path()
    generated_characters = PROJECT_ROOT / "data" / "generated" / "characters"
    directories = [
        generated_characters,
        PROJECT_ROOT / "data" / "uploads",
        PROJECT_ROOT / "logs",
    ]

    print("Cleanup targets:")
    try:
        existing_tables = set(inspect(engine).get_table_names())
        cleared_tables = [
            table for table in reversed(Base.metadata.sorted_tables)
            if table.name in existing_tables and table.name not in PRESERVED_RAG_TABLES
        ]
        print(f"  database: {database}")
        print("  preserve tables: " + ", ".join(sorted(PRESERVED_RAG_TABLES)))
        for table in cleared_tables:
            print(f"  clear table: {table.name}")
        if not args.dry_run and cleared_tables:
            with engine.begin() as connection:
                connection.execute(text("PRAGMA foreign_keys=OFF"))
                for table in cleared_tables:
                    connection.execute(table.delete())
                connection.execute(text("PRAGMA foreign_keys=ON"))
        for directory in directories:
            clear_directory(directory, dry_run=args.dry_run)
    except PermissionError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        print("Stop the backend/NapCat callback process and try again.", file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"ERROR: cleanup aborted: {exc}", file=sys.stderr)
        return 3

    print("Dry run complete; nothing was deleted." if args.dry_run else "Runtime data cleared successfully.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
