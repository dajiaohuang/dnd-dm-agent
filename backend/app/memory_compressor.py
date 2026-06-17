"""Automatic event-to-summary compression for long-running campaigns.

When uncompressed events exceed a threshold, this module calls the LLM
to produce a structured summary, then marks events as compressed and
archives old memories.  Compression runs as a background sub-agent so
it never blocks the main chat flow.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.orm import Session

from app.db.models import CampaignEvent
from app.services import uid

_log = logging.getLogger(__name__)

# ── Thresholds ───────────────────────────────────────────────────
COMPRESS_EVERY = 20          # Compress when this many uncompressed events accumulate
EVENT_RETENTION = 100        # Keep this many recent events hot (older ones get archived)
MEMORY_ARCHIVE_THRESH = 100  # Archive memories when total exceeds this


def count_uncompressed(db: Session, campaign_id: str) -> int:
    """Count events without a compression marker."""
    return db.query(CampaignEvent).filter(
        CampaignEvent.campaign_id == campaign_id,
        CampaignEvent.event_metadata.get("compressed", False) == False,  # noqa: E712
    ).count()


def get_uncompressed_events(db: Session, campaign_id: str, limit: int = COMPRESS_EVERY) -> list[CampaignEvent]:
    """Return the oldest uncompressed events for compression."""
    return db.query(CampaignEvent).filter(
        CampaignEvent.campaign_id == campaign_id,
    ).filter(
        CampaignEvent.event_metadata.get("compressed", False) == False,  # noqa: E712
    ).order_by(CampaignEvent.created_at.asc()).limit(limit).all()


def mark_compressed(db: Session, events: list[CampaignEvent]) -> None:
    """Mark events as compressed in their metadata."""
    for evt in events:
        meta = dict(evt.event_metadata or {})
        meta["compressed"] = True
        evt.event_metadata = meta
    db.commit()


def archive_old_memories(db: Session, campaign_id: str, keep: int = 100) -> int:
    """Set old memories to 'archived' status, keeping the most recent N active."""
    from sqlalchemy import text
    result = db.execute(text(
        "UPDATE campaign_memories SET status = 'archived' "
        "WHERE campaign_id = :cid AND status = 'active' "
        "AND id NOT IN ("
        "  SELECT id FROM campaign_memories "
        "  WHERE campaign_id = :cid2 AND status = 'active' "
        "  ORDER BY updated_at DESC LIMIT :keep"
        ")",
    ), {"cid": campaign_id, "cid2": campaign_id, "keep": keep})
    db.commit()
    return result.rowcount


def compress_with_llm(events: list[CampaignEvent], campaign_name: str = "") -> str:
    """Call the LLM to produce a structured summary from events."""
    from app.llm import chat_completion

    event_texts = []
    for evt in events:
        content = (evt.content or "")[:300]
        meta = evt.event_metadata or {}
        dm_response = str(meta.get("dm_response", ""))[:300]
        intent = meta.get("intent", "").get("intent_type", "") if isinstance(meta.get("intent"), dict) else ""
        event_texts.append(f"- [{evt.event_type}] {content} | DM: {dm_response} | intent: {intent}")

    joined = "\n".join(event_texts)
    prompt = (
        "You are a campaign archivist. Summarize the following D&D session events into a concise paragraph "
        "capturing key NPCs encountered, locations visited, decisions made, items obtained, and plot developments. "
        "Output ONLY the summary paragraph in Chinese, no preamble, no markdown headers.\n\n"
        f"Campaign: {campaign_name or 'Unknown'}\n"
        f"Events ({len(events)}):\n{joined}"
    )

    try:
        result = chat_completion([{"role": "user", "content": prompt}], temperature=0.3)
        return result or "（摘要生成失败）"
    except Exception as exc:
        _log.warning("compress_with_llm failed: %s", exc)
        return "（LLM 不可用，跳过压缩）"


def maybe_compress(db: Session, campaign_id: str, campaign_name: str = "") -> dict[str, Any] | None:
    """If enough uncompressed events, compress them and archive old memories.

    Called after every event append.  Returns compression result dict or None.
    """
    uncompressed = count_uncompressed(db, campaign_id)
    if uncompressed < COMPRESS_EVERY:
        return None

    events = get_uncompressed_events(db, campaign_id, COMPRESS_EVERY)
    if not events:
        return None

    summary = compress_with_llm(events, campaign_name)
    mark_compressed(db, events)

    total_memories = db.query(CampaignEvent).filter(
        CampaignEvent.campaign_id == campaign_id,
    ).count()
    archived = 0
    if total_memories > MEMORY_ARCHIVE_THRESH:
        archived = archive_old_memories(db, campaign_id, EVENT_RETENTION)

    return {
        "compressed_events": len(events),
        "summary": summary[:200] + "..." if len(summary) > 200 else summary,
        "archived_memories": archived,
    }
