"""PDF structure recovery and module-specific chunking tests."""

from __future__ import annotations

from pathlib import Path

from nanobot.dnd.db.module_content import _split_pdf_chapters
from nanobot.dnd.modules.chunking import parse_module_markdown
from nanobot.dnd.modules.pdf_parser import PdfBookmark, build_structured_markdown


def test_pdf_structure_uses_bookmarks_and_ignores_toc_chapter_lines() -> None:
    pages = [
        "Book Header\n目录 Contents\n第一章：双城记\n埃尔托瑞尔的陨落\n1",
        "Book Header\n第一章 双城记\nChapter 1: A Tale of Two Cities\n"
        "运作本章\n这是第一行\n这是接续内容。\nE1. 酒吧间\n房间说明。\n2",
    ]
    bookmarks = [PdfBookmark(title="运作本章", page=2, depth=0)]

    result = build_structured_markdown(pages, bookmarks)

    assert result.matched_bookmarks == 1
    assert "# 第一章 双城记" in result.content
    assert "# 第一章：双城记" not in result.content
    assert "## 运作本章" in result.content
    assert "#### E1. 酒吧间" in result.content
    assert "这是第一行这是接续内容。" in result.content
    assert "Book Header" not in result.content


def test_module_chunking_respects_headings_pages_and_overlap() -> None:
    content = (
        "<!-- page: 10 -->\n\n# 第一章\n\n## 场景甲\n\n"
        + "甲" * 700
        + "。\n\n"
        + "乙" * 700
        + "。\n\n<!-- page: 11 -->\n\n## 场景乙\n\n"
        + "丙" * 300
        + "。\n"
    )

    chunks = parse_module_markdown(content, max_chunk_chars=800, overlap_chars=80)

    assert len(chunks) == 3
    assert chunks[0].heading == "场景甲"
    assert chunks[0].page_start == 10
    assert chunks[1].overlap_chars > 0
    assert chunks[1].heading == "场景甲"
    assert chunks[2].heading == "场景乙"
    assert chunks[2].overlap_chars == 0
    assert chunks[2].page_start == 11
    assert all("<!-- page:" not in chunk.text for chunk in chunks)


def test_pdf_chapter_split_prefers_full_chapters_over_toc_duplicates() -> None:
    content = (
        "<!-- page: 2 -->\n\n# 第一章：目录项\n短。\n\n# 第二章：目录项\n短。\n\n"
        "<!-- page: 11 -->\n\n# 第一章 正文\n\n## 场景\n" + "甲" * 500 + "。\n\n"
        "<!-- page: 20 -->\n\n# 第二章 正文\n\n## 场景\n" + "乙" * 500 + "。\n"
    )

    chapters = _split_pdf_chapters(Path("module.pdf"), content, {})

    assert [chapter.chapter_key for chapter in chapters] == [
        "frontmatter",
        "ch.1",
        "ch.2",
    ]
    assert chapters[1].title == "第一章 正文"
    assert chapters[1].metadata["page_start"] == 11
