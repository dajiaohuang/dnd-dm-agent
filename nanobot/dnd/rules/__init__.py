"""Hierarchical rule ingestion and hybrid retrieval."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from nanobot.dnd.rules.embedding import BgeM3Embedder
    from nanobot.dnd.rules.ingest import RuleIngestService
    from nanobot.dnd.rules.search import RuleSearchService

__all__ = ["BgeM3Embedder", "RuleIngestService", "RuleSearchService"]


def __getattr__(name: str):
    if name == "BgeM3Embedder":
        from nanobot.dnd.rules.embedding import BgeM3Embedder

        return BgeM3Embedder
    if name == "RuleIngestService":
        from nanobot.dnd.rules.ingest import RuleIngestService

        return RuleIngestService
    if name == "RuleSearchService":
        from nanobot.dnd.rules.search import RuleSearchService

        return RuleSearchService
    raise AttributeError(name)
