from __future__ import annotations

import sys
from pathlib import Path

from sqlalchemy import delete, func, select

_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT / "backend"))

from app.db.database import Base, SessionLocal, engine
from app.db.models import CompendiumEntry, RuleChunk
from app.parsing.router import parse_file
from app.services import ingest_compendium, ingest_rule_content, ingest_rules


RULEBOOK_SUFFIXES = {".pdf", ".md", ".txt", ".docx"}


def main() -> int:
    data_dir = _REPO_ROOT / "data"
    raw_dir = data_dir / "raw"
    Base.metadata.create_all(bind=engine)

    with SessionLocal() as db:
        db.execute(delete(RuleChunk))
        db.execute(delete(CompendiumEntry))
        db.commit()

        compendium_count = ingest_compendium(db, data_dir)
        markdown_count = ingest_rules(db, data_dir)
        raw_count = 0
        failures: list[str] = []

        for path in sorted(raw_dir.iterdir() if raw_dir.exists() else []):
            if not path.is_file() or path.suffix.lower() not in RULEBOOK_SUFFIXES:
                continue
            parsed = parse_file(str(path), max_chars=2_000_000, strategy="auto")
            content = str(parsed.get("content") or "").strip()
            if not parsed.get("ok") or not content:
                failures.append(f"{path.name}: {parsed.get('error') or 'no text extracted'}")
                continue
            raw_count += ingest_rule_content(
                db, content, path.name, "DND_5E_2014",
                metadata={
                    "original_file": path.name,
                    "parser": parsed.get("parser"),
                    "parse_meta": parsed.get("meta") or {},
                },
                replace=True,
            )

        total_rules = db.scalar(select(func.count()).select_from(RuleChunk)) or 0
        embedded = db.scalar(
            select(func.count()).select_from(RuleChunk).where(RuleChunk.embedding.is_not(None))
        ) or 0

    print(f"Compendium entries: {compendium_count}")
    print(f"Markdown rule chunks: {markdown_count}")
    print(f"Raw rulebook chunks: {raw_count}")
    print(f"Total rule chunks: {total_rules} (embedded: {embedded})")
    if failures:
        print("Failed files:")
        for failure in failures:
            print(f"  - {failure}")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
