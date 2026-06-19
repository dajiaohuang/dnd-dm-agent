"""Import immutable module documents and their chapter/scene indexes."""

from __future__ import annotations

import hashlib
import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from sqlalchemy import select

from nanobot.dnd.db.campaigns import CampaignNotFoundError
from nanobot.dnd.db.database import Database
from nanobot.dnd.db.models import (
    Campaign,
    EmbeddingModel,
    ModuleChapter,
    ModuleChunk,
    ModuleSource,
    SceneIndex,
    ToolAudit,
)
from nanobot.dnd.rules.embedding import BgeM3Embedder, Embedder
from nanobot.dnd.rules.parser import parse_markdown

DEFAULT_EMBEDDING_MODEL_ID = "embedding-bge-m3"
SUPPORTED_MODULE_EXTENSIONS = {
    ".md",
    ".markdown",
    ".pdf",
    ".docx",
    ".pptx",
    ".xlsx",
    ".xls",
    ".html",
    ".htm",
    ".txt",
    ".csv",
    ".json",
    ".xml",
}


class ModuleImportError(RuntimeError):
    """Base error for module document imports."""


class ModuleAlreadyExistsError(ModuleImportError):
    """The campaign already has a module source with this name."""


@dataclass(frozen=True)
class ModuleInfo:
    id: str
    campaign_id: str
    name: str
    source_path: str
    checksum: str | None
    is_active: bool
    chapter_count: int
    scene_count: int
    chunk_count: int
    embeddings: int


@dataclass(frozen=True)
class SceneContentInfo:
    scene_id: str
    module_name: str
    chapter_key: str
    chapter_title: str
    scene_title: str
    start_line: int
    end_line: int
    keywords: list[str]
    content: str


_CHAPTER_PATTERNS = (
    re.compile(r"(?:^|\W)ch(?:apter)?\.?\s*(\d+)", re.IGNORECASE),
    re.compile(r"第\s*(\d+)\s*章"),
)


def _chapter_number(path: Path, fallback: int) -> int:
    for pattern in _CHAPTER_PATTERNS:
        match = pattern.search(path.stem)
        if match:
            return int(match.group(1))
    return fallback


def _title(path: Path, lines: list[str]) -> str:
    for line in lines:
        if line.startswith("# "):
            return line[2:].strip()
    return path.stem


def _tags(title: str) -> list[str]:
    lowered = title.lower()
    groups = (
        ("combat", ("combat", "battle", "fight", "encounter", "战斗", "遭遇", "伏击")),
        ("social", ("tavern", "inn", "npc", "酒馆", "旅馆", "交涉")),
        ("transition", ("travel", "journey", "depart", "旅行", "前往", "离开")),
        ("exploration", ("dungeon", "temple", "crypt", "地城", "神殿", "墓穴")),
    )
    found = [tag for tag, words in groups if any(word in lowered for word in words)]
    return found or ["exploration"]


def _scenes(lines: list[str]) -> list[dict[str, object]]:
    starts = [index for index, line in enumerate(lines) if line.startswith("## ")]
    if not starts and lines:
        return [
            {
                "title": "Chapter Content",
                "start_line": 1,
                "end_line": len(lines),
                "headings": [],
                "keywords": ["exploration"],
            }
        ]
    scenes: list[dict[str, object]] = []
    for position, start in enumerate(starts):
        end = starts[position + 1] if position + 1 < len(starts) else len(lines)
        title = lines[start][3:].strip()
        headings = [
            line.lstrip("# ").strip()
            for line in lines[start + 1 : end]
            if line.startswith("### ")
        ]
        scenes.append(
            {
                "title": title,
                "start_line": start + 1,
                "end_line": end,
                "headings": headings,
                "keywords": _tags(title),
            }
        )
    return scenes


class ModuleImportService:
    """Register module Markdown files as immutable campaign content."""

    def __init__(
        self,
        database: Database,
        *,
        embedder: Embedder | None = None,
        converter: Callable[[Path], str] | None = None,
    ) -> None:
        self.database = database
        self.embedder = embedder
        self.converter = converter

    def _read_document(self, path: Path) -> str:
        if path.suffix.lower() in {".md", ".markdown", ".txt"}:
            return path.read_text(encoding="utf-8-sig")
        if self.converter is not None:
            return self.converter(path)
        from markitdown import MarkItDown

        result = MarkItDown(enable_plugins=False).convert(str(path))
        content = getattr(result, "text_content", None)
        if not isinstance(content, str) or not content.strip():
            raise ModuleImportError(f"MarkItDown produced no text: {path}")
        return content

    def import_path(
        self,
        campaign_id: str,
        source_path: str | Path,
        *,
        name: str | None = None,
        activate: bool = True,
        embed: bool = True,
        actor_id: str | None = None,
    ) -> ModuleInfo:
        source = Path(source_path).expanduser().resolve()
        files = [source] if source.is_file() else sorted(source.rglob("*"))
        files = [
            path
            for path in files
            if path.is_file() and path.suffix.lower() in SUPPORTED_MODULE_EXTENSIONS
        ]
        if not files:
            raise ModuleImportError(f"no supported module documents found: {source}")
        module_name = (name or source.stem if source.is_file() else name or source.name).strip()
        if not module_name:
            raise ModuleImportError("module name must not be empty")
        embedder = self.embedder or (BgeM3Embedder(show_progress=True) if embed else None)

        digest = hashlib.sha256()
        parsed: list[tuple[Path, list[str], int, str]] = []
        for fallback, path in enumerate(files, start=1):
            raw = path.read_bytes()
            digest.update(path.relative_to(source.parent if source.is_file() else source).as_posix().encode())
            digest.update(b"\0")
            digest.update(raw)
            content = self._read_document(path)
            lines = content.splitlines()
            parsed.append((path, lines, _chapter_number(path, fallback), content))
        parsed.sort(key=lambda item: (item[2], item[0].name.lower()))

        with self.database.transaction() as session:
            campaign = session.get(Campaign, campaign_id)
            if campaign is None:
                raise CampaignNotFoundError(f"campaign not found: {campaign_id}")
            existing = session.scalar(
                select(ModuleSource).where(
                    ModuleSource.campaign_id == campaign_id,
                    ModuleSource.name == module_name,
                )
            )
            if existing is not None:
                raise ModuleAlreadyExistsError(
                    f"module already exists in campaign {campaign_id}: {module_name}"
                )
            if activate:
                for item in session.scalars(
                    select(ModuleSource).where(ModuleSource.campaign_id == campaign_id)
                ):
                    item.is_active = False

            model_row = None
            if embedder is not None:
                model_row = session.get(EmbeddingModel, DEFAULT_EMBEDDING_MODEL_ID)
                if model_row is None:
                    model_row = EmbeddingModel(
                        id=DEFAULT_EMBEDDING_MODEL_ID,
                        provider="sentence-transformers",
                        model_name=embedder.model_name,
                        dimensions=embedder.dimensions,
                    )
                    session.add(model_row)
                elif (
                    model_row.model_name != embedder.model_name
                    or model_row.dimensions != embedder.dimensions
                ):
                    raise ValueError(
                        "active embedding model metadata does not match the embedder"
                    )
                session.flush()

            module = ModuleSource(
                id=f"module_{uuid.uuid4().hex[:16]}",
                campaign_id=campaign_id,
                name=module_name,
                source_path=str(source),
                checksum=digest.hexdigest(),
                is_active=activate,
                metadata_json={
                    "format": "converted-markdown",
                    "chapter_count": len(parsed),
                    "source_extensions": sorted({path.suffix.lower() for path in files}),
                },
            )
            session.add(module)
            session.flush()
            scene_count = chunk_count = embedding_count = 0
            for order, (path, lines, chapter_number, content) in enumerate(parsed):
                chapter = ModuleChapter(
                    id=f"chapter_{uuid.uuid4().hex[:16]}",
                    module_id=module.id,
                    chapter_key=f"ch.{chapter_number}",
                    title=_title(path, lines),
                    source_path=str(path),
                    content=content,
                    order_index=order,
                    status="current" if activate and order == 0 else "locked",
                    metadata_json={
                        "checksum": hashlib.sha256(path.read_bytes()).hexdigest(),
                        "line_count": len(lines),
                    },
                )
                session.add(chapter)
                session.flush()
                scene_rows: list[SceneIndex] = []
                for scene_order, scene in enumerate(_scenes(lines), start=1):
                    scene_row = SceneIndex(
                            id=f"scene_{uuid.uuid4().hex[:16]}",
                            chapter_id=chapter.id,
                            scene_key=f"scene_{scene_order:03d}",
                            title=str(scene["title"]),
                            start_line=int(scene["start_line"]),
                            end_line=int(scene["end_line"]),
                            headings=list(scene["headings"]),
                            keywords=list(scene["keywords"]),
                        )
                    session.add(scene_row)
                    scene_rows.append(scene_row)
                    scene_count += 1
                session.flush()

                _, parsed_chunks = parse_markdown(content)
                embedding_texts = [
                    f"{module_name} | {chapter.chapter_key} | "
                    f"{' → '.join(chunk.heading_path)}\n{chunk.text}"
                    for chunk in parsed_chunks
                ]
                vectors = (
                    embedder.encode(embedding_texts)
                    if embedder is not None
                    else [None] * len(embedding_texts)
                )
                for chunk, embedding_text, vector in zip(
                    parsed_chunks, embedding_texts, vectors, strict=True
                ):
                    scene_row = next(
                        (
                            scene
                            for scene in scene_rows
                            if scene.start_line <= chunk.start_line <= scene.end_line
                        ),
                        None,
                    )
                    breadcrumb = " → ".join(chunk.heading_path)
                    session.add(
                        ModuleChunk(
                            id=f"module_chunk_{uuid.uuid4().hex[:16]}",
                            module_id=module.id,
                            chapter_id=chapter.id,
                            scene_id=scene_row.id if scene_row else None,
                            embedding_model_id=model_row.id if model_row else None,
                            chunk_index=chunk.chunk_index,
                            heading=chunk.heading,
                            breadcrumb=breadcrumb,
                            start_line=chunk.start_line,
                            end_line=chunk.end_line,
                            char_start=chunk.char_start,
                            char_end=chunk.char_end,
                            token_count=max(1, len(chunk.text) // 4),
                            content_hash=hashlib.sha256(
                                chunk.text.encode("utf-8")
                            ).hexdigest(),
                            chunk_text=chunk.text,
                            search_text=f"{module_name}\n{chapter.title}\n{breadcrumb}\n{chunk.text}",
                            embedding_json=vector,
                            metadata_json={
                                "embedding_text_hash": hashlib.sha256(
                                    embedding_text.encode("utf-8")
                                ).hexdigest()
                            },
                        )
                    )
                    chunk_count += 1
                    embedding_count += vector is not None
            if activate:
                campaign.module_name = module_name
            session.flush()
            session.add(
                ToolAudit(
                    id=f"audit_module_{uuid.uuid4().hex[:16]}",
                    request_id=f"module-import:{uuid.uuid4().hex}",
                    campaign_id=campaign_id,
                    actor_id=actor_id,
                    tool_name="dnd_module_import",
                    engine_function="database.module.import",
                    arguments_json={"name": module_name, "source_path": str(source)},
                    result_json={
                        "module_id": module.id,
                        "chapter_count": len(parsed),
                        "scene_count": scene_count,
                        "chunk_count": chunk_count,
                        "embeddings": embedding_count,
                    },
                    success=True,
                )
            )
            return ModuleInfo(
                id=module.id,
                campaign_id=campaign_id,
                name=module.name,
                source_path=module.source_path,
                checksum=module.checksum,
                is_active=module.is_active,
                chapter_count=len(parsed),
                scene_count=scene_count,
                chunk_count=chunk_count,
                embeddings=embedding_count,
            )

    def list(self, campaign_id: str) -> list[ModuleInfo]:
        with self.database.transaction() as session:
            if session.get(Campaign, campaign_id) is None:
                raise CampaignNotFoundError(f"campaign not found: {campaign_id}")
            modules = list(
                session.scalars(
                    select(ModuleSource)
                    .where(ModuleSource.campaign_id == campaign_id)
                    .order_by(ModuleSource.created_at, ModuleSource.id)
                )
            )
            result: list[ModuleInfo] = []
            for module in modules:
                chapters = list(
                    session.scalars(
                        select(ModuleChapter).where(ModuleChapter.module_id == module.id)
                    )
                )
                chapter_ids = [chapter.id for chapter in chapters]
                scenes = (
                    list(
                        session.scalars(
                            select(SceneIndex).where(SceneIndex.chapter_id.in_(chapter_ids))
                        )
                    )
                    if chapter_ids
                    else []
                )
                chunks = list(
                    session.scalars(
                        select(ModuleChunk).where(ModuleChunk.module_id == module.id)
                    )
                )
                result.append(
                    ModuleInfo(
                        id=module.id,
                        campaign_id=campaign_id,
                        name=module.name,
                        source_path=module.source_path,
                        checksum=module.checksum,
                        is_active=module.is_active,
                        chapter_count=len(chapters),
                        scene_count=len(scenes),
                        chunk_count=len(chunks),
                        embeddings=sum(chunk.embedding_json is not None for chunk in chunks),
                    )
                )
            return result

    def index(self, campaign_id: str) -> list[dict[str, object]]:
        with self.database.transaction() as session:
            if session.get(Campaign, campaign_id) is None:
                raise CampaignNotFoundError(f"campaign not found: {campaign_id}")
            modules = session.scalars(
                select(ModuleSource)
                .where(ModuleSource.campaign_id == campaign_id)
                .order_by(ModuleSource.name, ModuleSource.id)
            )
            result: list[dict[str, object]] = []
            for module in modules:
                chapters_payload: list[dict[str, object]] = []
                chapters = session.scalars(
                    select(ModuleChapter)
                    .where(ModuleChapter.module_id == module.id)
                    .order_by(ModuleChapter.order_index, ModuleChapter.id)
                )
                for chapter in chapters:
                    scenes = session.scalars(
                        select(SceneIndex)
                        .where(SceneIndex.chapter_id == chapter.id)
                        .order_by(SceneIndex.start_line, SceneIndex.id)
                    )
                    chapters_payload.append(
                        {
                            "id": chapter.id,
                            "chapter_key": chapter.chapter_key,
                            "title": chapter.title,
                            "status": chapter.status,
                            "scenes": [
                                {
                                    "id": scene.id,
                                    "scene_key": scene.scene_key,
                                    "title": scene.title,
                                    "keywords": list(scene.keywords or []),
                                }
                                for scene in scenes
                            ],
                        }
                    )
                result.append(
                    {
                        "id": module.id,
                        "name": module.name,
                        "is_active": module.is_active,
                        "chapters": chapters_payload,
                    }
                )
            return result

    def read_scene(self, campaign_id: str, scene_id: str) -> SceneContentInfo:
        with self.database.transaction() as session:
            row = session.execute(
                select(SceneIndex, ModuleChapter, ModuleSource)
                .join(ModuleChapter, ModuleChapter.id == SceneIndex.chapter_id)
                .join(ModuleSource, ModuleSource.id == ModuleChapter.module_id)
                .where(
                    SceneIndex.id == scene_id,
                    ModuleSource.campaign_id == campaign_id,
                )
            ).first()
            if row is None:
                raise ModuleImportError(
                    f"scene not found in campaign {campaign_id}: {scene_id}"
                )
            scene, chapter, module = row
            lines = chapter.content.splitlines(keepends=True)
            content = "".join(lines[scene.start_line - 1 : scene.end_line])
            return SceneContentInfo(
                scene_id=scene.id,
                module_name=module.name,
                chapter_key=chapter.chapter_key,
                chapter_title=chapter.title,
                scene_title=scene.title,
                start_line=scene.start_line,
                end_line=scene.end_line,
                keywords=list(scene.keywords or []),
                content=content,
            )
