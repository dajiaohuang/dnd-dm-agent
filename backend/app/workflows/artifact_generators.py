from __future__ import annotations

import json
import re
from typing import Any

from app.llm import chat_completion
from app.workflows.artifact_schemas import SettingBatchArtifact, SettingProposal


def _json_value(raw: str | None) -> Any:
    text = (raw or "").strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    for opener, closer in (("[", "]"), ("{", "}")):
        start, end = text.find(opener), text.rfind(closer)
        if start >= 0 and end >= start:
            try:
                return json.loads(text[start:end + 1])
            except Exception:
                pass
    return None


def _setting_from_raw(category: str, raw: dict, index: int, theme: str) -> SettingProposal:
    name = str(raw.get("name") or f"{theme} {category} {index}").strip()
    description = str(raw.get("description") or raw.get("summary") or "").strip()
    content = raw.get("content") if isinstance(raw.get("content"), dict) else {}
    for key in (
        "occupation", "ancestry", "age", "alignment", "personality", "appearance",
        "goal", "secret", "leader", "members", "rivals", "history", "geography",
        "powers", "consequences", "combat", "inventory", "roleplay", "story_role",
    ):
        if key in raw:
            content[key] = raw[key]
    if description:
        content.setdefault("description", description)
    else:
        description = f"围绕“{theme}”生成的 {category} 设定。"
        content["description"] = description
    return SettingProposal(
        category=category, name=name, summary=description[:500], content=content,
        visibility=str(raw.get("visibility") or "dm_only"),
        tags=[str(v) for v in raw.get("tags", []) if str(v).strip()],
        relationships=raw.get("relationships") if isinstance(raw.get("relationships"), list) else [],
    )


def generate_setting_batch(request: dict[str, Any]) -> SettingBatchArtifact:
    category = str(request.get("category") or request.get("type") or "npc")
    if category not in {"npc", "location", "faction", "item", "event"}:
        raise ValueError(f"unsupported setting category: {category}")
    count = max(1, min(int(request.get("count") or 1), 50))
    theme = str(request.get("theme") or "D&D campaign")
    prompt = str(request.get("prompt") or "")
    fields = {
        "npc": "name, description, occupation, ancestry, alignment, personality, appearance, goal, secret",
        "location": "name, description, geography, history, secret",
        "faction": "name, description, leader, goal, members, rivals, secret",
        "item": "name, description, powers, history",
        "event": "name, description, consequences",
    }[category]
    messages = [{
        "role": "system",
        "content": (
            f"Generate exactly {count} distinct {category} entries for theme: {theme}. "
            f"Return ONLY a JSON array. Each object uses fields: {fields}. "
            "Descriptions must be detailed Chinese text; names must be unique."
        ),
    }, {"role": "user", "content": prompt or f"生成 {count} 个 {category}"}]

    raw_items: list[dict] = []
    for _attempt in range(3):
        value = _json_value(chat_completion(messages, temperature=0.8))
        if isinstance(value, list):
            raw_items = [item for item in value if isinstance(item, dict)]
        if len(raw_items) >= count:
            break
        messages.append({
            "role": "user",
            "content": f"上次只有 {len(raw_items)} 个有效对象。请重新返回严格包含 {count} 个对象的 JSON 数组。",
        })
    if not raw_items:
        raise ValueError("LLM did not return a valid JSON setting array")

    proposals = [_setting_from_raw(category, item, i + 1, theme) for i, item in enumerate(raw_items[:count])]
    existing_names = {item.name for item in proposals}
    while len(proposals) < count:
        index = len(proposals) + 1
        name = f"{theme} {category} {index}"
        while name in existing_names:
            index += 1
            name = f"{theme} {category} {index}"
        proposals.append(_setting_from_raw(category, {"name": name}, index, theme))
        existing_names.add(name)
    return SettingBatchArtifact(theme=theme, settings=proposals)
