from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from sqlalchemy.orm import Session

from app.campaign_turns import current_turn
from app.db.models import Campaign, Character
from app.message_router import process_message
from app.qq_bindings import find_bindings


@dataclass
class IncomingPlatformMessage:
    platform: str
    text: str
    user_id: str
    chat_id: str = ""
    message_id: str | int | None = None
    reply_message_id: str | int | None = None
    reply_text: str = ""
    mentioned_user_ids: list[str] = field(default_factory=list)
    group_history: list[dict[str, Any]] = field(default_factory=list)
    group_history_error: str = ""
    is_group: bool = False
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class PlatformMention:
    user_id: str
    text: str


@dataclass
class PlatformReply:
    text: str
    mentions: list[PlatformMention] = field(default_factory=list)


class PlatformAdapter(Protocol):
    platform: str

    def default_campaign(self, db: Session) -> Campaign | None:
        ...

    def default_character_id(self, db: Session, campaign: Campaign) -> str | None:
        ...

    def session_id(self, message: IncomingPlatformMessage) -> str:
        ...

    def is_dm_user(self, user_id: str, campaign: Campaign) -> bool:
        ...

    def format_reply(self, message: IncomingPlatformMessage, reply: PlatformReply) -> Any:
        ...


def select_bound_character_id(
    db: Session,
    campaign: Campaign,
    user_id: str,
    text: str,
    default_character_id: str | None = None,
) -> str | None:
    character_id = default_character_id
    bindings = find_bindings(db, campaign.id, user_id)
    active_actor = current_turn(campaign)
    active_binding = next((
        item for item in bindings
        if active_actor and item.character_id == active_actor.get("character_id")
    ), None)
    named_bindings = [
        item for item in bindings
        if (bound := db.get(Character, item.character_id)) is not None
        and bound.character_name.casefold() in text.casefold()
    ]
    selected_binding = active_binding or (named_bindings[0] if len(named_bindings) == 1 else None)
    if not selected_binding and len(bindings) == 1:
        selected_binding = bindings[0]
    if selected_binding:
        character_id = selected_binding.character_id
    elif len(bindings) > 1:
        character_id = None
    return character_id


def build_message_context(message: IncomingPlatformMessage) -> dict[str, Any]:
    context: dict[str, Any] = {
        "platform": message.platform,
        "current_text": message.text,
        "reply_text": message.reply_text,
        "reply_message_id": message.reply_message_id,
        "mentioned_user_ids": message.mentioned_user_ids,
        "message_id": message.message_id,
        "group_id": message.chat_id if message.is_group else None,
        "sender_id": message.user_id,
    }
    if message.group_history:
        context["group_history"] = message.group_history
    if message.group_history_error:
        context["group_history_error"] = message.group_history_error
    return context


def build_platform_reply(result: dict) -> PlatformReply:
    answer = result["narration"]
    reply = PlatformReply(text=answer)
    for mention in (result.get("data") or {}).get("mentions") or []:
        user_id = str(mention.get("user_id") or mention.get("qq_user_id") or "").strip()
        if user_id:
            reply.mentions.append(PlatformMention(
                user_id=user_id,
                text=str(mention.get("text") or "请查看并回复。"),
            ))
    reaction_notifications = (
        result.get("reaction_notifications") or result.get("data", {}).get("reaction_notifications") or []
    )
    notification = result.get("turn_notification") or result.get("data", {}).get("turn_notification")
    for reaction in reaction_notifications:
        if reaction.get("qq_user_id"):
            reply.mentions.append(PlatformMention(
                user_id=str(reaction["qq_user_id"]),
                text=f"你的角色“{reaction['name']}”可以反应，请回复是否使用。",
            ))
    if notification and notification.get("qq_user_id"):
        reply.mentions.append(PlatformMention(
            user_id=str(notification["qq_user_id"]),
            text=f"轮到你的角色“{notification['name']}”行动了。",
        ))
    return reply


def handle_platform_message(
    db: Session,
    adapter: PlatformAdapter,
    message: IncomingPlatformMessage,
    process_fn=process_message,
) -> dict[str, Any]:
    campaign = adapter.default_campaign(db)
    if not campaign:
        raise LookupError(f"{adapter.platform} campaign not found")
    character_id = adapter.default_character_id(db, campaign)
    character_id = select_bound_character_id(db, campaign, message.user_id, message.text, character_id)
    session_id = adapter.session_id(message)
    campaign_dm_user_id = str((campaign.config or {}).get("dice_dm_qq_user_id") or "").strip()
    result = process_fn(
        db,
        campaign,
        session_id,
        character_id,
        message.text,
        actor_id=message.user_id,
        is_dm=adapter.is_dm_user(message.user_id, campaign) or message.user_id == campaign_dm_user_id,
        message_context=build_message_context(message),
    )
    platform_reply = build_platform_reply(result)
    return {
        "reply": adapter.format_reply(message, platform_reply),
        "auto_escape": False,
        "at_sender": False,
        "result": result,
    }
