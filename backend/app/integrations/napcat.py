"""NapCat OneBot v11 adapter with attachment extraction."""
from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
from sqlalchemy.orm import Session

from app.config import settings
from app.db.models import Campaign, Character
from app.platform_adapter import IncomingPlatformMessage, PlatformAdapter, PlatformReply
from app.qq_bindings import active_napcat_campaign


def allowed_user_ids() -> set[str]:
    return {item.strip() for item in settings.napcat_allowed_user_ids.split(",") if item.strip()}


def dm_user_ids() -> set[str]:
    return {item.strip() for item in settings.napcat_dm_user_ids.split(",") if item.strip()}


def is_dm_user(user_id: str | int) -> bool:
    return str(user_id).strip() in dm_user_ids()


class NapCatClient:
    def __init__(self, base_url: str, token: str = "", self_id: str = ""):
        self.base_url = base_url.rstrip("/")
        self.token = token.strip()
        self.self_id = self_id.strip()

    @classmethod
    def from_settings(cls) -> "NapCatClient | None":
        if not settings.napcat_base_url:
            return None
        return cls(settings.napcat_base_url, settings.napcat_token, settings.napcat_self_id)

    def headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def post_action(self, action: str, payload: dict[str, Any]) -> dict[str, Any]:
        response = httpx.post(
            f"{self.base_url}/{action}", headers=self.headers(), json=payload, timeout=20
        )
        response.raise_for_status()
        data = response.json()
        if data.get("status") not in {None, "ok"}:
            raise RuntimeError(f"NapCat action failed: {data}")
        return data

    def send_private_msg(self, user_id: str | int, message: str) -> dict[str, Any]:
        return self.post_action("send_private_msg", {"user_id": int(user_id), "message": message})

    def send_group_msg(self, group_id: str | int, message: str) -> dict[str, Any]:
        return self.post_action("send_group_msg", {"group_id": int(group_id), "message": message})

    def send_group_at(self, group_id: str | int, user_id: str | int, message: str) -> dict[str, Any]:
        return self.post_action("send_group_msg", {
            "group_id": int(group_id),
            "message": [
                {"type": "at", "data": {"qq": str(user_id)}},
                {"type": "text", "data": {"text": f" {message}"}},
            ],
        })

    def resolve_file_url(self, file_id: str) -> str:
        result = self.post_action("get_file", {"file_id": file_id})
        data = result.get("data") or {}
        return str(data.get("url") or data.get("file") or "")

    def get_message(self, message_id: str | int) -> dict[str, Any]:
        return (self.post_action("get_msg", {"message_id": int(message_id)}).get("data") or {})

    def upload_private_file(self, user_id: str | int, file_path: str, name: str) -> dict[str, Any]:
        return self._post_action_long("upload_private_file", {
            "user_id": int(user_id),
            "file": str(file_path),
            "name": name,
        })

    def upload_group_file(self, group_id: str | int, file_path: str, name: str) -> dict[str, Any]:
        return self._post_action_long("upload_group_file", {
            "group_id": int(group_id),
            "file": str(file_path),
            "name": name,
        })

    def _post_action_long(self, action: str, payload: dict[str, Any], timeout: int = 60) -> dict[str, Any]:
        response = httpx.post(
            f"{self.base_url}/{action}", headers=self.headers(), json=payload, timeout=timeout
        )
        response.raise_for_status()
        data = response.json()
        if data.get("status") not in {None, "ok"}:
            raise RuntimeError(f"NapCat action failed: {data}")
        return data

    def get_group_history(self, group_id: str | int, count: int = 20) -> list[dict[str, Any]]:
        data = self.post_action("get_group_msg_history", {"group_id": int(group_id), "count": count}).get("data") or {}
        return list(data.get("messages") or [])


def parse_event_text(payload: dict[str, Any], self_id: str = "") -> str:
    message = payload.get("message")
    if not isinstance(message, list):
        return " ".join(str(payload.get("raw_message") or message or "").split()).strip()
    parts: list[str] = []
    for segment in message:
        kind, data = segment.get("type"), segment.get("data") or {}
        if kind == "text":
            parts.append(str(data.get("text", "")))
        elif kind == "at" and str(data.get("qq", "")).strip() != self_id:
            parts.append(f"@{data.get('qq')} ")
    return " ".join("".join(parts).split()).strip()


def mentioned_user_ids(payload: dict[str, Any], self_id: str = "") -> list[str]:
    message = payload.get("message")
    if not isinstance(message, list):
        return []
    return [
        str((segment.get("data") or {}).get("qq", "")).strip()
        for segment in message
        if segment.get("type") == "at"
        and str((segment.get("data") or {}).get("qq", "")).strip()
        and str((segment.get("data") or {}).get("qq", "")).strip() != self_id
    ]


def replied_message_id(payload: dict[str, Any]) -> str | None:
    message = payload.get("message")
    if not isinstance(message, list):
        return None
    reply = next((segment for segment in message if segment.get("type") == "reply"), None)
    value = str(((reply or {}).get("data") or {}).get("id", "")).strip()
    return value or None


def message_text(message: dict[str, Any], self_id: str = "") -> str:
    return parse_event_text(message, self_id) or str(message.get("raw_message") or "").strip()


def attachment_segments(payload: dict[str, Any]) -> list[dict[str, str]]:
    message = payload.get("message")
    if not isinstance(message, list):
        return []
    attachments = []
    for segment in message:
        kind, data = segment.get("type"), segment.get("data") or {}
        if kind not in {"image", "file", "record", "video"}:
            continue
        attachments.append({
            "type": kind,
            "name": str(data.get("name") or data.get("file") or f"{kind}.bin"),
            "url": str(data.get("url") or ""),
            "file_id": str(data.get("file_id") or data.get("file") or ""),
        })
    return attachments


def is_group_at_event(payload: dict[str, Any], self_id: str = "") -> bool:
    if payload.get("message_type") != "group":
        return False
    message = payload.get("message")
    if isinstance(message, list):
        return any(
            segment.get("type") == "at"
            and str((segment.get("data") or {}).get("qq", "")).strip() == self_id
            for segment in message
        )
    return bool(self_id and f"[CQ:at,qq={self_id}]" in str(payload.get("raw_message") or ""))


def is_supported_message(payload: dict[str, Any]) -> bool:
    return payload.get("post_type") == "message" and payload.get("message_type") in {"private", "group"}


def is_allowed(payload: dict[str, Any]) -> bool:
    allowed = allowed_user_ids()
    return not allowed or str(payload.get("user_id", "")).strip() in allowed


def callback_token_valid(authorization: str | None) -> bool:
    if not settings.napcat_token:
        return True
    return authorization == f"Bearer {settings.napcat_token}"


def download_attachments(client: NapCatClient, payload: dict[str, Any]) -> tuple[Path, list[str], list[str]]:
    root = Path(tempfile.mkdtemp(prefix="dnd_napcat_"))
    paths, errors = [], []
    for index, item in enumerate(attachment_segments(payload)):
        try:
            source = item["url"] or client.resolve_file_url(item["file_id"])
            parsed = urlparse(source)
            if parsed.scheme not in {"http", "https"}:
                raise ValueError("attachment URL must use http or https")
            suffix = Path(parsed.path).suffix or Path(item["name"]).suffix or ".bin"
            target = root / f"{index:02d}_{item['type']}{suffix}"
            with httpx.stream("GET", source, timeout=30, follow_redirects=True) as response:
                response.raise_for_status()
                total = 0
                with target.open("wb") as output:
                    for chunk in response.iter_bytes():
                        total += len(chunk)
                        if total > settings.attachment_max_bytes:
                            raise ValueError("attachment exceeds size limit")
                        output.write(chunk)
            paths.append(str(target))
        except Exception as exc:
            errors.append(f"{item['name']}: {exc}")
    return root, paths, errors


class NapCatAdapter(PlatformAdapter):
    platform = "napcat"

    def __init__(self, client: NapCatClient):
        self.client = client

    def default_campaign(self, db: Session) -> Campaign | None:
        return active_napcat_campaign(db) or db.get(Campaign, settings.napcat_campaign_id)

    def default_character_id(self, db: Session, campaign: Campaign) -> str | None:
        character_id = settings.napcat_character_id or None
        default_character = db.get(Character, character_id) if character_id else None
        if default_character and default_character.campaign_id == campaign.id:
            return character_id
        return None

    def session_id(self, message: IncomingPlatformMessage) -> str:
        if message.is_group:
            return f"napcat_group_{message.chat_id}_{message.user_id}"
        return f"napcat_private_{message.user_id}"

    def is_dm_user(self, user_id: str, campaign: Campaign) -> bool:
        return is_dm_user(user_id)

    def format_reply(self, message: IncomingPlatformMessage, reply: PlatformReply) -> str | list[dict[str, Any]]:
        if not message.is_group or not reply.mentions:
            return reply.text
        segments: list[dict[str, Any]] = [{"type": "text", "data": {"text": f"{reply.text}\n\n"}}]
        for mention in reply.mentions:
            segments.extend([
                {"type": "at", "data": {"qq": mention.user_id}},
                {"type": "text", "data": {"text": f" {mention.text}\n"}},
            ])
        return segments

    def incoming_from_payload(
        self,
        payload: dict[str, Any],
        text: str,
        reply_text: str = "",
        group_history: list[dict[str, Any]] | None = None,
        group_history_error: str = "",
    ) -> IncomingPlatformMessage:
        user_id = str(payload.get("user_id", "")).strip() or "user"
        group_id = str(payload.get("group_id", "")).strip()
        reply_id = replied_message_id(payload)
        return IncomingPlatformMessage(
            platform=self.platform,
            text=text,
            user_id=user_id,
            chat_id=group_id,
            message_id=payload.get("message_id"),
            reply_message_id=reply_id,
            reply_text=reply_text,
            mentioned_user_ids=mentioned_user_ids(payload, self.client.self_id),
            group_history=group_history or [],
            group_history_error=group_history_error,
            is_group=payload.get("message_type") == "group",
            raw=payload,
        )
